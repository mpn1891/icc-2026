# 02 — Sparkplug B: the same sample valve, the other firmware

> **Build spec.** What exists, what it publishes, and how it was measured. The talk material —
> the pattern 1 vs 2 comparison table, segment beats, the risk narrative — lives in
> [`../talk-tracks/02-sparkplug-b.md`](../talk-tracks/02-sparkplug-b.md). Progress log at the bottom.
>
> This file supersedes spec 02 in [`00-master-plan.md`](00-master-plan.md).

| | |
|---|---|
| **Pattern** | 2 of 7 — Sparkplug B v3.0.0 edge node |
| **Mechanism tag** | none — Sparkplug payloads carry no envelope, and that is itself the point |
| **Container** | `sim-valve-spb` — [`../../services/sim-valve-spb/`](../../services/sim-valve-spb/) |
| **Config page** | <http://localhost:8086> |
| **Pairs with** | [`01-native-mqtt.md`](01-native-mqtt.md) — same assembly, plain MQTT |
| **Depends on** | nothing (Wave 1) |
| **Blocks** | nothing |
| **Signal contributed** | Device liveness / session state — **narrative only** (see below) |

## The device is the same device

Same sanitary sample valve, same RFID reader, same badge roster, same interlock, same stroke
times, on `BR-202` instead of `BR-201` so both can run at once. `valve.py` and `webui.py` are
byte-for-byte identical between the two build contexts. **Everything that differs between the
two containers is a difference the protocol caused** — that is the whole reason this pattern
exists, and any change that breaks the identity breaks the pattern. Fix one copy, copy it
across, `diff` before committing.

**Scope note on the through line.** Pattern 7 joins on `sample-valve-01` — pattern 1's valve, on
`BR-201`. This valve is `sample-valve-02` on `BR-202`, and its liveness contribution is a stage
argument, not a section of the composite document. **Do not build a pattern-7 dependency on this
container**, and do not promise one on stage. If that ever changes, this valve has to move to
`BR-201` or pattern 7 has to read both, and both are re-scopes.

The valve takes no commands in either firmware: authorization is decided at the sample port
against the local roster. A DCMD write to any valve metric is refused and logged.

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
[`../../services/sim-valve-spb/sparkplug.py`](../../services/sim-valve-spb/sparkplug.py) and
rendered on the config page next to the control it disables.

Sequencing rules the implementation honours: NBIRTH carries `seq = 0`
(`tck-id-topics-nbirth-seq-num`) and a `bdSeq` metric matching the will's; NDEATH carries `bdSeq`
and **only** `bdSeq` (`tck-id-topics-ndeath-payload`) and no `seq` at all
(`tck-id-topics-ndeath-seq`); `bdSeq` starts at 0 and increments on every new CONNECT, which
means re-arming the will on every disconnect before paho reconnects.

---

## Metrics

Twenty, all declared in DBIRTH with name, alias, datatype and — where they have one — an
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
| `Sample/LastCycleResult` | String | | |
| `Actuator/AirSupplyBar` | Float | bar | 0.05 |
| `Device/EnclosureTempC` | Float | degC | 0.2 |
| `Device/FirmwareVersion` `Device/SerialNumber` `Device/Cell` | String | | |

Node-level, on NBIRTH: `bdSeq` (Int64) and `Node Control/Rebirth` (Boolean, writable).

**Three of these changed on 2026-08-23**, all of them driven by `valve.py`, which is shared
byte-for-byte with pattern 1:

- **`Line/PressureBar` → `Actuator/AirSupplyBar`** and **`Line/TemperatureC` →
  `Device/EnclosureTempC`.** A diaphragm valve on a sample port has nothing moving through it
  while it is shut, so a continuous line reading was either measuring a dead leg or restating
  BR-201's own vessel instruments. What the assembly genuinely measures all the time is its own
  condition. Same datatypes, same units, same deadbands — only the thing being measured is now
  real. See [`01-native-mqtt.md § telemetry`](01-native-mqtt.md).
- **`Sample/LastCycleResult` is new** — `normal` | `failed-to-seat` | `stroke-timeout` |
  `aborted-interlock`, mirroring `cycle_result` on pattern 1's `event/sample-complete`.

The rename earns pattern 2 something it did not have: **`Actuator/AirSupplyBar` is the physical
cause of `Sample/LastCycleResult`.** A pneumatic actuator starved of air is a valve that will
not seat, so the deadbanded analog and the fault string are now the same story rather than two
unrelated metrics — and on the wire you can watch the DDATA that reported the sagging supply
arrive minutes before the DDATA that reported the failure.

**Not built yet.** The registry in `app.py:54-73` still declares nineteen metrics with the old
`Line/*` names, and `valve.py` has no fault path. See open item 4.

**Report by exception.** DDATA carries only metrics that moved past their deadband, and the
deadband lives in the device rather than in the broker or the consumer — it is a property of the
measurement. An enclosure temperature that wanders 0.05 °C is not news, and saying so is the
device's job. Compare pattern 1, which publishes the whole telemetry document every five seconds
because it has no way to express the idea.

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
tree, so answering it is not politeness — it is what makes the integration self-heal.

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

## Ingest, as built

Verified 2026-08-17 against a gateway seeded from an empty volume. **Zero files changed.**
`default-namespace/Sparkplug B/config.json` already read `subscription: "spBv1.0/#"` with
`defaultTagsEnabled: true`, and `namespace-server-set/Sparkplug B-Default Set` already bound it
to the Default Set. Nothing was configured, and that is the entire point.

What appeared, on its own:

```
MQTT Engine/Edge Nodes/ICC26-Site1-UPSTREAM/SAMPLE-VALVE-02/
    Node Control/Rebirth            Node Info/   (16 diagnostics)
    SV-202/Valve/{State,IsOpen,PositionPct}
    SV-202/Interlock/Ok
    SV-202/Badge/{LastScanId,LastScanHolder,LastScanRole,LastScanResult,LastDenyReason,LastScanTime}
    SV-202/Sample/{CycleCount,LastSampleId,LastSampleTime,LastOpenDurationS}
    SV-202/Line/{PressureBar,TemperatureC}
    SV-202/Device/{FirmwareVersion,SerialNumber,Cell}
    SV-202/Device Info/             (9 diagnostics)
```

**19 device metric tags**, plus the diagnostics Engine adds itself. Engineering units landed on
all four analogs — `%`, `bar`, `degC`, `s` — and those strings exist nowhere except the DBIRTH
property sets, so they are proof Engine parsed the metric properties, not just the names.

> **Measured against the nineteen-metric set.** This is what a 2026-08-17 gateway built from the
> DBIRTH the code emits *today*. The twenty-metric set specified above renames the two `Line/*`
> tags and adds `Sample/LastCycleResult`, which also takes the typed-null count from nine to
> ten. Unit count is unchanged at four. **Re-measure when it lands** — and note that nothing
> about the *mechanism* is expected to change, which is the point: adding a metric to a
> Sparkplug device is a device-side edit, and the tag tree follows on its own. Pattern 1's
> equivalent change needs its Engine subscription checked by a human.

### What this does not put on disk, and why that matters

`ignition/config/resources/…/tag-definition/MQTT Engine/Edge Nodes/` holds a `tags.json` for
**only the four metrics with engineering units.** Ignition persists a tag definition only where
the configuration is non-default, so the fifteen String, Boolean and DateTime metrics write
nothing at all — their folders exist and are empty.

Two consequences worth remembering:

- **The filesystem is not a reliable way to inspect a MANAGED provider.** Counting tags on disk
  gives 4, not 19. The authoritative read is `GET /data/api/v1/tags/export?provider=MQTT
  Engine&type=json&recursive=true`, which needs an API key.
- The gitignore entry for this tree is still right — it is runtime state keyed to whatever
  edge nodes a machine has seen — but it is ignoring much less than it looks like.

### The encoder, proven on the wire

Captured from Chariot and decoded with the device's own `sparkplug.py`:

```
NBIRTH  seq=0   2 metrics: bdSeq (Int64), Node Control/Rebirth (Boolean)
DBIRTH  seq=1  19 metrics, aliases 1-19, 9 of them typed nulls
NDEATH  seq=None, bdSeq only
```

NBIRTH carries `seq = 0`, NDEATH carries `bdSeq` and **no `seq` at all** — the two sequencing
rules the spec is strictest about, both honoured. Nine metrics went out as **typed nulls**,
including `Badge/LastScanTime` as a DateTime.

### Rebirth, unprompted, in 6 milliseconds

Restarting the gateway leaves Engine holding no birth certificate. The device is silent because
it reports by exception, so nothing happens until it next has something to say — then:

```
20:28:47.193  valve   badge B-1042 granted  -> DDATA
20:28:47.196  Engine  Received message from unknown edge node - requesting rebirth
20:28:47.196  Engine  Requesting Rebirth from ICC26-Site1-UPSTREAM/SAMPLE-VALVE-02
20:28:47.199  valve   rebirth requested -- re-announcing
              page    runtime.rebirths = 1
```

No loop, no operator, no configuration.

### Death, both ways

`docker stop` → **DDEATH then an explicit NDEATH**, because a clean DISCONNECT makes the broker
discard the will. `docker kill` → **NDEATH alone**, published by the broker from the will. Both
carried the matching `bdSeq`, and every restart re-birthed with `seq` back to 0.

**`bdSeq` stays 0 across container restarts.** It increments per CONNECT *within a process*, and
a restarted container is a new process starting from 0. Correct for a device that rebooted, and
harmless here, but worth knowing before anyone reads a bdSeq of 0 as "never reconnected".

---

## Verification

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'spBv1.0/#' -v
```

**0 — The encoder.** `cd services/sim-valve-spb && python selftest.py`. No Docker, no broker, no
network. It also runs inside `docker build`.

**1 — Birth.** On connect: NBIRTH on `…/NBIRTH/SAMPLE-VALVE-02` (seq 0, `bdSeq`), then DBIRTH on
`…/DBIRTH/SAMPLE-VALVE-02/SV-202` with every device metric — nineteen as built, twenty once
`Sample/LastCycleResult` lands.

**2 — The checkpoint that actually matters.** MQTT Engine builds the tag tree from DBIRTH by
itself — every metric, right datatypes, right units, nobody configured anything. That is both
the proof the hand-written encoder is correct **and** the half of pattern 2 that pattern 1 cannot
do at all. If it does not appear, check the gateway log for a Rebirth request loop before
suspecting the encoder.

**3 — Report by exception.** Press a badge on <http://localhost:8086>. One DDATA carrying only
the metrics that changed — not the whole set. Watch `seq` increment, and note that the enclosure
temperature is absent from most DDATA messages because of its deadband.

**3a — Cause and effect on the wire** (once open item 4 lands). Sag the air supply from the
page. `Actuator/AirSupplyBar` crosses its 0.05 deadband and one DDATA reports it. Then badge
`B-1042`: the scan is granted, and the sample ends with `Sample/LastCycleResult` =
`failed-to-seat` in a later DDATA. Two messages, minutes apart, and the first one predicted the
second. Pattern 1 carries the same two facts in a five-second telemetry document and a
`cycle_result` string, with nothing connecting them.

**4 — Death, both ways.** `docker kill icc26-sim-valve-spb` → the broker publishes NDEATH with
the matching `bdSeq`. `docker stop` → DDEATH and an explicit NDEATH.

**5 — Rebirth.** Publish `Node Control/Rebirth = true` on the NCMD topic (or restart MQTT Engine
and watch it ask by itself) → NBIRTH and DBIRTH again, `seq` back to 0. The page counts how many
it has honoured.

**6 — Recommission.** Change the Device ID on the page → the old identity gets a DDEATH and an
NDEATH before the session is torn down, then births under the new one. No consumer is left
holding a device that no longer exists.

**7 — Aliases.** Set `VALVE_SPB_USE_ALIASES=false`, restart, and watch the DDATA payloads grow.

**8 — The identity check.** `diff services/sim-valve-mqtt/valve.py services/sim-valve-spb/valve.py`
and the same for `webui.py`. Both must be empty. This is the pattern's real regression test.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | `bdSeq` wraps at 256 (Tahu's convention); the spec only says "increment by one" | harmless, noted in the code |
| 2 | Whether `STATE` / a primary-host application belongs in the demo at all | out of scope for v1 — no host application is claimed |
| 3 | Pattern 2 does not appear on a firehose coloured by `meta.mechanism` | by design. Master plan §08 decides between a topic-prefix special case and saying it out loud on stage |
| 4 | **The metric set above is ahead of the build.** `app.py:54-73` still declares nineteen with the old `Line/*` names, and `valve.py` has no stroke-fault path. Both changes originate in `valve.py`, which is **shared byte-for-byte with pattern 1** — so this is one edit landing in two containers, and `diff services/sim-valve-mqtt/valve.py services/sim-valve-spb/valve.py` must come back empty afterwards | open — paired with pattern 1's open item 2, land them together |

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-17 | **Pattern re-scoped**: bioreactor UDT on the Programmable Device Simulator → the same smart sample valve assembly as pattern 1, as a standalone Sparkplug edge node container. Document created. |
| 2026-08-17 | `services/sim-valve-spb/` built: hand-written Tahu-verified protobuf encoder, 19 metrics with deadbands and units, NBIRTH/DBIRTH/DDATA/DDEATH/NDEATH, `bdSeq` re-armed per CONNECT, Rebirth honoured, config page on 8086 with the three controls disabled and cited. Compose service, ACL account, seed rows, `.env.example` block. |
| 2026-08-17 | **Ran against a real gateway for the first time.** Engine built all 19 tags with units from DBIRTH with zero configuration; unprompted Rebirth observed and answered in 6 ms; both death paths measured. Encoder proven on the wire against Chariot. Open items 1 and 2 (tag-tree auto-build, 3.0.0 vs 2.2 acceptance by Cirrus 5.0.4) both resolved here. |
| 2026-08-19 | Pattern 7's lost tag source resolved elsewhere — it was re-scoped twice and no longer needs this pattern's metrics. Nothing in this file changed as a result. |
| 2026-08-23 | **Split.** Talk material and the pattern 1 vs 2 comparison table moved to [`../talk-tracks/02-sparkplug-b.md`](../talk-tracks/02-sparkplug-b.md); this file is the build spec. Deviations table and the resolved *Known consequence* section dropped. Recorded that this pattern's through-line contribution is narrative, not a field in pattern 7's document. |
| 2026-08-23 | **Metric set follows pattern 1's payload redesign**, because `valve.py` is shared. `Line/PressureBar` → `Actuator/AirSupplyBar` and `Line/TemperatureC` → `Device/EnclosureTempC`: nothing flows through a shut sample valve, so the old pair measured a dead leg or restated BR-201's own instruments. `Sample/LastCycleResult` added, mirroring `cycle_result` — nineteen metrics become twenty and nine typed nulls become ten. Datatypes, units and deadbands all unchanged. The rename buys a causal pair the old metrics never had: the deadbanded air supply is the physical cause of the fault string, so one DDATA predicts another. Measured sections left at nineteen and marked for re-measurement; open item 4 tracks the build. |
