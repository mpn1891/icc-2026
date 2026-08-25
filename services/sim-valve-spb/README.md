# sim-valve-spb — pattern 2, Sparkplug B

The same smart sample valve assembly as [`../sim-valve-mqtt/`](../sim-valve-mqtt/), on the
sample port of `BR-202`, with Sparkplug B v3.0.0 firmware instead. Same badge roster, same
stroke times, same stroke faults — `valve.py` and `webui.py` are byte-for-byte identical
between the two directories. Everything else that differs, the specification caused.

Full contract and reasoning: [`docs/plans/02-sparkplug-b.md`](../../docs/plans/02-sparkplug-b.md).

## The configuration page

<http://localhost:8086>. Deliberately the same layout as the plain-MQTT page, and that is
where the argument is:

| | plain MQTT (`:8085`) | Sparkplug B (`:8086`) |
|---|---|---|
| Topic | free text | **derived, read-only** |
| QoS | dropdown, 0/1/2 | **disabled at 0**, with the clause that fixes it |
| Retained | checkbox | **disabled, unchecked** |
| What you *can* set | the namespace itself | three names *inside* a namespace |

The disabled controls are left on the page rather than removed. A device with no QoS setting
looks like a device that forgot one; a device that shows you the setting greyed out, next to
`tck-id-topics-ddata-mqtt`, is telling you something.

## Topics

All six derived from Group ID / Edge Node ID / Device ID. Shipped defaults
`ICC26-Site1-UPSTREAM` / `SAMPLE-VALVE-02` / `SV-202`:

| Message | Topic | QoS | Retained |
|---|---|---|---|
| NBIRTH | `spBv1.0/ICC26-Site1-UPSTREAM/NBIRTH/SAMPLE-VALVE-02` | 0 | no |
| DBIRTH | `spBv1.0/…/DBIRTH/SAMPLE-VALVE-02/SV-202` | 0 | no |
| DDATA | `spBv1.0/…/DDATA/SAMPLE-VALVE-02/SV-202` | 0 | no |
| DDEATH | `spBv1.0/…/DDEATH/SAMPLE-VALVE-02/SV-202` | 0 | no |
| NDEATH | `spBv1.0/…/NDEATH/SAMPLE-VALVE-02` | **1** | no |
| NCMD | `spBv1.0/…/NCMD/SAMPLE-VALVE-02` | 0 | no — **subscribed**, not published |

NDEATH is the outlier because it is registered as the MQTT Will, and the spec fixes will QoS
at 1 (`tck-id-message-flow-edge-node-birth-publish-will-message-qos`).

## The one inbound message

`Node Control/Rebirth` on NCMD, and nothing else. That is not a command to the valve — it is
the host asking the device to re-announce itself. **Ignition's MQTT Engine sends it
unprompted** whenever it sees data for a device it holds no birth certificate for, so an edge
node that ignores it can sit permanently unknown in the tag tree. Answering it is what makes
the integration self-heal, and plain MQTT has no equivalent at all.

A DCMD write to any valve metric is refused and logged. The valve still takes no commands.

## Metrics

Nineteen, all declared in DBIRTH with datatype, alias and engineering unit, including the ones
that have no value yet — those go out as typed nulls, so a consumer learns
`Badge/LastScanId` is a String before anybody has badged in.

```
Valve/State (String)   Valve/IsOpen (Boolean)   Valve/PositionPct (Float, %, db 0.5)
Badge/LastScanId LastScanHolder LastScanRole LastScanResult LastDenyReason (String)
Badge/LastScanTime (DateTime)
Sample/CycleCount (Int64)  Sample/LastSampleId (String)  Sample/LastSampleTime (DateTime)
Sample/LastOpenDurationS (Float, s)   Sample/LastCycleResult (String)
Actuator/AirSupplyBar (Float, bar, db 0.05)   Device/EnclosureTempC (Float, degC, db 0.2)
Device/FirmwareVersion  Device/SerialNumber  Device/Cell (String)
```

DDATA carries only the metrics that moved past their deadband, by alias rather than by name.
Set `USE_ALIASES=false` to put the names back on the wire and watch the payload grow.

`Actuator/AirSupplyBar` is the physical **cause** of `Sample/LastCycleResult`: a pneumatic
actuator starved of air is a valve that will not seat. Sag the supply from the page, watch one
DDATA report it crossing the 0.05 deadband, then badge in — the sample completes
`failed-to-seat` in a later DDATA, minutes after the message that predicted it.

`Valve/State`, `Valve/IsOpen` and `Valve/PositionPct` have **no equivalent in pattern 1**,
which cut its `state` topic on 2026-08-25 and publishes valve position nowhere at all. Same
`valve.py`, same snapshot handed to both sinks; only this one has somewhere to put it.

## Why the protobuf is hand-written

`pysparkplug` and `mqtt-spb-wrapper` both pin `paho-mqtt` to the 1.x line, and every other
service here is on paho 2.x with the VERSION2 callback API. A Sparkplug container that
differed from its plain-MQTT twin in its *MQTT client* would confound the one comparison
these two services exist to make. So `sparkplug.py` encodes
`org.eclipse.tahu.protobuf.Payload` directly — a small closed schema, about eighty lines of
wire format — and the only dependency stays `paho-mqtt`.

That is only acceptable because it is checked:

```bash
python selftest.py
```

Golden byte vectors plus a cross-check against **Eclipse Tahu's own generated protobuf code**,
which is skipped unless the generated module is importable (`selftest.py`'s docstring has the
three commands that produce it). The check also runs at image build time, so a wire-format
regression fails `docker build` rather than becoming a device Ignition silently refuses to
birth.

`sparkplug_b_pb2.py` is deliberately **not** committed: it is generated code carrying a
protobuf-runtime version guard, and the service does not need protobuf at runtime.

## Environment

| Variable | Default | |
|---|---|---|
| `BROKER_HOST` / `BROKER_PORT` | `chariot` / `1883` | |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | `sample-valve-02` | see `compose/chariot/mqtt-users.json` |
| `GROUP_ID` | `ICC26-Site1-UPSTREAM` | factory default; the page overrides it |
| `EDGE_NODE_ID` / `DEVICE_ID` | `SAMPLE-VALVE-02` / `SV-202` | ditto |
| `CELL` | `br-202` | |
| `USE_ALIASES` | `true` | what Cirrus's own Transmission does |
| `BADGE_ROSTER` | same three badges as the twin | |
| `SAMPLE_WINDOW_S` / `VALVE_STROKE_S` | `12` / `1.5` | |
| `TELEMETRY_INTERVAL_S` | `5` | |
| `SCAN_INTERVAL_S` | `90` | **`0` disables** free-running scans for a scripted stage run |
| `UI_PORT` | `8080` | container-side; compose maps it to 8086 |
| `CONFIG_PATH` | `/data/config.json` | commissioned ids, on a named volume |

## Verify

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'spBv1.0/#' -v
```

On connect: NBIRTH (seq 0, carrying `bdSeq`) then DBIRTH with all nineteen metrics. A badge
scan produces one DDATA carrying only what changed.

The checkpoint that matters is not the wire — it is **MQTT Engine building the tag tree from
DBIRTH by itself**. That is the proof the encoder is right, and it is the half of pattern 2
that pattern 1 cannot do at all.

`docker stop icc26-sim-valve-spb` publishes DDEATH and an explicit NDEATH, because a clean
DISCONNECT makes the broker discard the will. `docker kill icc26-sim-valve-spb` is the case
where the will does the work instead.
