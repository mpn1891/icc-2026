# 02 — Sparkplug B: the same sample valve, the other firmware

> **Living document.** Updated as the pattern is built — see the [progress log](#progress-log) at
> the bottom. This file supersedes spec 02 in [`00-master-plan.md`](00-master-plan.md), which
> described a bioreactor UDT on the Programmable Device Simulator. That design is gone; see
> [Deviations](#deviations-from-the-earlier-docs).

| | |
|---|---|
| **Pattern** | 2 of 7 — Sparkplug B v3.0.0 edge node |
| **Mechanism tag** | none — Sparkplug payloads carry no envelope, and that is itself the point |
| **New container** | `sim-valve-spb` — [`services/sim-valve-spb/`](../../services/sim-valve-spb/) |
| **Config page** | <http://localhost:8086> |
| **Pairs with** | [`01-native-mqtt.md`](01-native-mqtt.md) — same assembly, plain MQTT |
| **Depends on** | nothing (Wave 1) |
| **Blocks** | nothing |

## Objective and talk point

**This is not a different device. It is the same device.** Same sanitary sample valve, same RFID
reader, same badge roster, same interlock, same stroke times, on `BR-202` instead of `BR-201` so
both can run at once. `valve.py` and `webui.py` are byte-for-byte identical between the two
build contexts. Everything below is a consequence of the protocol and nothing else, which is why
the pair is an argument rather than two demos.

The five minutes: **open both config pages side by side, then look at the wire.**

| | Pattern 1 — plain MQTT | Pattern 2 — Sparkplug B |
|---|---|---|
| **Topic** | a text box | derived from three names; there is no field |
| **QoS** | a dropdown, 0/1/2 | fixed at 0 (`tck-id-topics-ddata-mqtt`) |
| **Retained** | a checkbox | fixed at false, same clause |
| **Who enforces the namespace** | a broker ACL somebody remembered to write | the protocol |
| **Payload** | JSON we invented | protobuf, self-describing |
| **Datatypes** | whatever `json.dumps` produced; the consumer guesses | declared per metric |
| **Engineering units** | agreed out of band, or not at all | a metric property, on the wire |
| **Discovery** | none — someone hand-writes an Ignition tag config and maintains it | DBIRTH builds the tag tree by itself |
| **Loss detection** | none | `seq`, 0–255 rolling; a gap is visible |
| **Death** | retained JSON on a topic we chose, timestamp frozen at connect, meaning agreed nowhere | NDEATH, spec-mandated topic and payload, every consumer already applies it |
| **Re-announce** | nothing | `Node Control/Rebirth`, and the host asks for it unprompted |
| **Adding a metric** | edit the device, edit Ignition, edit the doc, keep three in step | edit the device |

**The honest half of that table:** Sparkplug does not invent a death mechanism, it standardises
the one MQTT already had. NDEATH *is* a Last Will — same CONNECT-packet registration, same
frozen payload, same one-will-per-session limit. DDEATH is not a will at all; it is an ordinary
publish. What changed is that the topic, the payload and the rule are agreed in advance by
everybody, instead of being three fields in a config page and a paragraph in a wiki. Say that
out loud — it is more convincing than pretending the plumbing is different.

---

## Sparkplug identity and topics

Group `ICC26-Site1-UPSTREAM`, edge node `SAMPLE-VALVE-02`, device `SV-202`. The assembly is its
own edge node with one device — itself — which is what a self-contained smart instrument
actually is.

```
spBv1.0/{group_id}/{message_type}/{edge_node_id}[/{device_id}]
```

| Message | Topic | QoS | Retained | Fixed by |
|---|---|---|---|---|
| NBIRTH | `spBv1.0/ICC26-Site1-UPSTREAM/NBIRTH/SAMPLE-VALVE-02` | 0 | no | `tck-id-topics-nbirth-mqtt` |
| DBIRTH | `spBv1.0/…/DBIRTH/SAMPLE-VALVE-02/SV-202` | 0 | no | `tck-id-topics-dbirth-mqtt` |
| DDATA | `spBv1.0/…/DDATA/SAMPLE-VALVE-02/SV-202` | 0 | no | `tck-id-topics-ddata-mqtt` |
| DDEATH | `spBv1.0/…/DDEATH/SAMPLE-VALVE-02/SV-202` | 0 | no | `tck-id-topics-ddeath-mqtt` |
| NDEATH | `spBv1.0/…/NDEATH/SAMPLE-VALVE-02` | **1** | no | `tck-id-message-flow-edge-node-birth-publish-will-message-qos` |
| NCMD | `spBv1.0/…/NCMD/SAMPLE-VALVE-02` | 0 | no | **subscribed**, not published |

NDEATH is the one outlier, and only because it is registered as the MQTT Will rather than
published. Every constant above is quoted with its TCK identifier in
[`services/sim-valve-spb/sparkplug.py`](../../services/sim-valve-spb/sparkplug.py) and rendered
on the config page next to the control it disables.

Sequencing rules the implementation honours: NBIRTH carries `seq = 0`
(`tck-id-topics-nbirth-seq-num`) and a `bdSeq` metric matching the will's; NDEATH carries `bdSeq`
and **only** `bdSeq` (`tck-id-topics-ndeath-payload`) and no `seq` at all
(`tck-id-topics-ndeath-seq`); `bdSeq` starts at 0 and increments on every new CONNECT, which
means re-arming the will on every disconnect before paho reconnects.

---

## Metrics

Nineteen, all declared in DBIRTH with name, alias, datatype and — where they have one — an
engineering unit. Metrics with no value yet go out as **typed nulls**, so a consumer learns
`Badge/LastScanId` exists and is a String before anybody has badged in, rather than watching a
tag appear from nowhere on the first scan.

| Metric | Type | Unit | Deadband |
|---|---|---|---|
| `Valve/State` | String | | |
| `Valve/IsOpen` | Boolean | | |
| `Valve/PositionPct` | Float | % | 0.5 |
| `Interlock/Ok` | Boolean | | |
| `Badge/LastScanId` `LastScanHolder` `LastScanRole` `LastScanResult` `LastDenyReason` | String | | |
| `Badge/LastScanTime` | DateTime | | |
| `Sample/CycleCount` | Int64 | | |
| `Sample/LastSampleId` | String | | |
| `Sample/LastSampleTime` | DateTime | | |
| `Sample/LastOpenDurationS` | Float | s | |
| `Line/PressureBar` | Float | bar | 0.05 |
| `Line/TemperatureC` | Float | degC | 0.2 |
| `Device/FirmwareVersion` `Device/SerialNumber` `Device/Cell` | String | | |

Node-level, on NBIRTH: `bdSeq` (Int64) and `Node Control/Rebirth` (Boolean, writable).

**Report by exception.** DDATA carries only metrics that moved past their deadband, and the
deadband lives in the device rather than in the broker or the consumer — it is a property of the
measurement. A line temperature that wanders 0.05 °C is not news, and saying so is the device's
job. Compare pattern 1, which publishes the whole telemetry document every five seconds because
it has no way to express the idea.

**Aliases** are on by default, which is what Cirrus's own Transmission does: name *and* alias at
birth, alias alone in DDATA. It is a real bandwidth saving and a real dependency — a consumer
that missed the birth certificate cannot read the data at all, which is exactly why Rebirth
exists. `VALVE_SPB_USE_ALIASES=false` puts the names back on the wire.

---

## The one inbound message

`Node Control/Rebirth`, and nothing else.

This is **not** a command to the valve. It is the host asking the device to re-announce itself,
and **Ignition's MQTT Engine sends it unprompted** whenever it sees DATA for a device it holds
no birth certificate for. An edge node that ignores it can sit permanently unknown in the tag
tree, so answering it is not politeness — it is what makes the integration self-heal. Plain MQTT
has no equivalent; pattern 1's consumer, having missed nothing in particular, simply stays wrong.

A DCMD write to any valve metric is refused and logged. **The valve still takes no commands**:
authorization is decided at the sample port against the local roster, in both patterns.

---

## Implementation

`services/sim-valve-spb/`, `paho-mqtt` and nothing else.

| File | |
|---|---|
| `valve.py` | **byte-for-byte identical** to the pattern 1 copy |
| `webui.py` | **byte-for-byte identical** to the pattern 1 copy |
| `sparkplug.py` | Payload encoding, topics, `seq`, and a small decoder for NCMD |
| `app.py` | Metric registry + deadbands, birth/death/data, the page's view of the device |
| `selftest.py` | Proof the encoder is right |
| `page.html` | The page, same layout as pattern 1's with the three controls disabled |

### Why the protobuf is hand-written

Both Sparkplug libraries on PyPI (`pysparkplug`, `mqtt-spb-wrapper`) pin `paho-mqtt` to the 1.x
line, and every other service in this repo is on paho 2.x with the VERSION2 callback API. A
Sparkplug container that differed from its twin **in its MQTT client** would confound the one
comparison these two services exist to make. So `sparkplug.py` encodes
`org.eclipse.tahu.protobuf.Payload` directly — a small closed schema, about eighty lines of wire
format — and the dependency list stays identical to pattern 1's.

That is only defensible because it is checked. `python selftest.py` runs golden byte vectors
plus a cross-check against **Eclipse Tahu's own generated protobuf code**, field by field, for
every datatype this device uses including negative integers, DateTime, typed nulls, metric
properties and alias-only DDATA. The Tahu half is skipped unless the generated module is
importable (three commands in the file's docstring produce it); the golden vectors always run.
**The check also runs at image build time**, so a wire-format regression fails `docker build`
rather than becoming a device Ignition silently refuses to birth.

`sparkplug_b_pb2.py` is deliberately **not** committed: generated code with a protobuf-runtime
version guard, needed by nobody at runtime.

---

## Infrastructure

**MQTT user `sample-valve-02`**: publishes `spBv1.0/ICC26-Site1-UPSTREAM/#`, subscribes only its
own NCMD and DCMD topics. Note how much tighter that is than pattern 1's account, and that
nothing was given up to get it — the protocol already pins the topics, so the ACL can be exact.

Same two caveats as pattern 1: `MQTT_USERS` seeds Chariot on first run only, and
`compose/postgres/initdb/` runs on an empty volume only.

**MQTT Transmission is not involved.** This edge node owns its own session. Transmission remains
the publisher for Ignition-originated events in patterns 3–7.

---

## Deviations from the earlier docs

| Master plan said | Now | Why |
|---|---|---|
| Programmable Device Simulator feeding a `Bioreactor` UDT (temp, pH, DO, agitation, level, OUR), published by MQTT Transmission | A standalone Sparkplug edge node container: the same sample valve as pattern 1 | Pattern 2 has to be the *same device* as pattern 1 for the comparison to mean anything, and it needs its own config page. A device commissioned through the SCADA system it publishes to cannot make that point |
| Edge node `ICC26-Site1-UPSTREAM` / `UPSTREAM-EDGE-01`, device `BR-201` | Group `ICC26-Site1-UPSTREAM`, node `SAMPLE-VALVE-02`, device `SV-202` | The assembly is its own edge node. The group is unchanged |
| Deadbands configured in the Transmission tag tree | Deadbands in the device's metric table | They are a property of the measurement |
| Talk point: free birth/death vs pattern 1, *which claims no lifecycle at all* | Talk point: **spec-mandated vs hand-rolled** birth/death | Pattern 1 now owns its session and has a real will, so the comparison got better rather than weaker |

---

## Known consequence, not solved here

**Pattern 7 (scripted aggregation) has lost its live process values.** It planned to join
`plant.batch` (SQL), the LIMS `GET /results/latest`, and live `BR-201` tag values — and those tag
values came from pattern 2's Sparkplug bioreactor, which no longer exists. Options, in rough
order of cost:

1. Join the valve's own metrics instead (sampling history is a legitimate thing to aggregate).
2. Add a small process simulator publishing bioreactor conditions, unattached to any pattern.
3. Let pattern 7 join only SQL + LIMS and drop the third source.

Recorded here and in [`00-master-plan.md`](00-master-plan.md); decide when spec 07 is written.

---

## Verification

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'spBv1.0/#' -v
```

**0 — The encoder.** `cd services/sim-valve-spb && python selftest.py`. No Docker, no broker, no
network. It also runs inside `docker build`.

**1 — Birth.** On connect: NBIRTH on `…/NBIRTH/SAMPLE-VALVE-02` (seq 0, `bdSeq`), then DBIRTH on
`…/DBIRTH/SAMPLE-VALVE-02/SV-202` with all nineteen metrics.

**2 — The checkpoint that actually matters.** MQTT Engine builds the tag tree from DBIRTH by
itself — nineteen tags, right datatypes, right units, nobody configured anything. That is both
the proof the hand-written encoder is correct **and** the half of pattern 2 that pattern 1 cannot
do at all. If it does not appear, check the gateway log for a Rebirth request loop before
suspecting the encoder.

**3 — Report by exception.** Press a badge on <http://localhost:8086>. One DDATA carrying only
the metrics that changed — not nineteen. Watch `seq` increment, and note that the line
temperature is absent from most DDATA messages because of its deadband.

**4 — Death, both ways.** `docker kill icc26-sim-valve-spb` → the broker publishes NDEATH with
the matching `bdSeq`. `docker stop` → DDEATH and an explicit NDEATH, because a clean DISCONNECT
makes the broker discard the will. Compare with pattern 1's retained JSON on a topic of its own
choosing.

**5 — Rebirth.** Publish `Node Control/Rebirth = true` on the NCMD topic (or restart MQTT Engine
and watch it ask by itself) → NBIRTH and DBIRTH again, `seq` back to 0. The page counts how many
it has honoured.

**6 — Recommission.** Change the Device ID on the page → the old identity gets a DDEATH and an
NDEATH before the session is torn down, then births under the new one. No consumer is left
holding a device that no longer exists.

**7 — Aliases.** Set `VALVE_SPB_USE_ALIASES=false`, restart, and watch the DDATA payloads grow.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | Ignition-side: confirm Engine auto-builds the tag tree, and where in the tag provider it lands | **not built.** Docs + services only, by decision on 2026-08-17 |
| 2 | Sparkplug B 3.0.0 vs the 2.2-era shape Cirrus 5.0.4 also accepts | targeting 3.0.0; verify Engine is happy at checkpoint 2 |
| 3 | Pattern 7's lost tag source | see *Known consequence* above |
| 4 | `bdSeq` wraps at 256 (Tahu's convention); the spec only says "increment by one" | harmless, noted in the code |
| 5 | Whether `STATE` / a primary-host application belongs in the demo at all | out of scope for v1 — no host application is claimed |

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-17 | **Pattern re-scoped**: bioreactor UDT on the Programmable Device Simulator → the same smart sample valve assembly as pattern 1, as a standalone Sparkplug edge node container. Document created. |
| 2026-08-17 | `services/sim-valve-spb/` built: hand-written Tahu-verified protobuf encoder, 19 metrics with deadbands and units, NBIRTH/DBIRTH/DDATA/DDEATH/NDEATH, `bdSeq` re-armed per CONNECT, Rebirth honoured, config page on 8086 with the three controls disabled and cited. Compose service, ACL account, seed rows, `.env.example` block. |
