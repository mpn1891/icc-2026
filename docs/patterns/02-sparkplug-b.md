# 02 — Sparkplug B: the same valve, the other firmware

> Pattern 2 of 7. Built from [`plans/02-sparkplug-b.md`](../plans/02-sparkplug-b.md);
> architecture decisions are in [`00-architecture.md`](../00-architecture.md).
>
> **Read it with [`01-native-mqtt.md`](01-native-mqtt.md).** This is the second half of one
> argument.

| | |
|---|---|
| **Pattern** | 2 of 7: Sparkplug B v3.0.0 edge node |
| **Container** | `sim-valve-spb`, [`services/sim-valve-spb/`](../../services/sim-valve-spb/) |
| **Config page** | <http://localhost:8086> |
| **Depends on** | nothing |
| **Feeds** | nothing downstream; `lims-bridge` does not subscribe to it |
| **Signal contributed** | **Device liveness and session state**, as an argument rather than a field in pattern 7 |
| **GxP relevance** | You can prove the valve was alive when it said nothing, and the spec-mandated sequence number tells you whether you *missed* messages in between. Silence becomes evidence, and so does a gap |

**The GxP point has two halves, and the second is the stronger one.** Liveness answers *was it
alive?*; `seq` answers *did I get everything it said?* It is a per-edge-node counter the spec
mandates, so a gap is a detected loss rather than an unnoticed one. Pattern 1 has nothing
equivalent. For an audit trail, "I know I am missing scan 41" is a different position from "the
record is complete as far as I know", and only one of the two valves can say it.

## Overview

**This is not a different device. It is the same device.** Same sanitary sample valve, same RFID
reader, same badge roster, same stroke times, on `BR-202` instead of `BR-201` so both can run at
once. `valve.py` and `webui.py` are byte-for-byte identical between the two build contexts.
Everything that differs between the two containers is a difference **the protocol caused**,
which is why the pair is an argument rather than two demos.

Drop this valve's connection mid-sampling-window and the death certificate fires. The record
shows the valve was alive and had nothing to report, which is a claim pattern 1 cannot make.

## Pattern 1 against pattern 2

| | Pattern 1 — plain MQTT | Pattern 2 — Sparkplug B |
|---|---|---|
| **Topic** | a text box | derived from three names; there is no field |
| **QoS** | a dropdown, 0/1/2 | fixed at 0 (`tck-id-topics-ddata-mqtt`) |
| **Retained** | a checkbox | fixed at false, same clause |
| **Who enforces the namespace** | a broker ACL somebody remembered to write | the protocol |
| **Payload** | JSON we invented | protobuf, self-describing |
| **Datatypes** | whatever `json.dumps` produced; the consumer guesses | declared per metric |
| **Engineering units** | agreed out of band, or not at all | a metric property, on the wire |
| **Discovery** | none; someone hand-writes an Ignition tag config and maintains it | DBIRTH builds the tag tree by itself |
| **Consumer subscription** | `icc26/site1/upstream/br-201/sample-valve-01/#`: one device, by name. A wildcard wide enough for two valves also swallows patterns 5 and 7, so a second valve means editing Engine's config | `spBv1.0/#`: every edge node that will ever exist, already covered |
| **Message taxonomy** | invented per device; this valve split its events into `vlv-badge-scanned` and `sample-acq-completed` on topics it chose, and every consumer has to be told | one metric list, declared once in DBIRTH |
| **Loss detection** | none | `seq`, 0–255 rolling, per edge node; a gap is visible |
| **A null value** | no tag is created at all | a typed null: the tag exists, correctly typed, empty |
| **Death, what it says** | retained JSON on a topic we chose, timestamp frozen at connect, meaning agreed nowhere | NDEATH: spec-mandated topic and payload, never retained, and `bdSeq` says *which session* died |
| **Death, how fast** | broker notices at TCP teardown, else at keepalive expiry | **identical**: NDEATH is the same Last Will |
| **Re-announce** | nothing | `Node Control/Rebirth`, and the host asks for it unprompted |
| **Adding a metric** | edit the device, edit Ignition, edit the doc, keep three in step | edit the device |

**The honest half.** Sparkplug does not invent a death mechanism; it standardises the one MQTT
already had. NDEATH *is* a Last Will: same CONNECT-packet registration, same frozen payload, same
one-will-per-session limit. DDEATH is not a will at all; it is an ordinary publish. **And none of
it is any faster.** A will fires when the broker notices the session ended: immediately if the
TCP connection tore down (which is what `docker kill` gives you), otherwise not until the
keepalive expires, 1.5 × the keepalive interval. Sparkplug mandates no keepalive value, so two
valves configured the same die at the same wall-clock moment. Sparkplug does not detect death
sooner.

**One thing here is more than an agreement: `bdSeq`.** The NDEATH will and the NBIRTH that
follows it are registered together at CONNECT carrying the same birth/death sequence number, so a
consumer receiving NDEATH can ask *which session died* and discard it if it does not match the
session it currently believes is alive. That closes a real race: a delayed will from a previous
session, arriving after the device has already reconnected, marks a live device dead. Pattern 1's
will carries nothing to match sessions with, so its consumer simply believes it. Sparkplug also
forbids retain on NDEATH, which is why pattern 2's death certificate cannot do what pattern 1's
retained `status` does: sit in the broker and replay to every new subscriber as current.

Everything else that changed is that the topic, the payload and the rule are agreed in advance by
everybody, instead of being three fields in a config page and a paragraph in a wiki.

## Identity and the wire

Group `ICC26-Site1-UPSTREAM`, edge node `SAMPLE-VALVE-02`, device `SV-202`. The assembly is its
own edge node with one device (itself), which is what a self-contained smart instrument actually
is.

```
spBv1.0/{group_id}/{message_type}/{edge_node_id}[/{device_id}]
```

Every constant is quoted with its TCK identifier in
[`services/sim-valve-spb/sparkplug.py`](../../services/sim-valve-spb/sparkplug.py) and **rendered
on the config page next to the control it disables**, so the greyed-out box tells you which
clause greyed it out.

**Nineteen device metrics**, all declared in DBIRTH with name, alias, datatype and, for the four analogs,
an engineering unit (`%`, `bar`, `degC`, `s`). Metrics with no value yet go out as **typed
nulls**, so a consumer learns `Badge/LastScanId` exists and is a String *before anybody has
badged in*, and `Sample/LastSampleId` too: the same field pattern 1 cannot show you until
somebody's badge is accepted.

**Report by exception.** DDATA carries only metrics that moved past their deadband, and the
deadband lives in the device: it is a property of the measurement. An enclosure temperature that
wanders 0.05 °C is not news, and saying so is the device's job. Pattern 1 publishes its whole
telemetry document every five seconds because it has no way to express the idea.

**And the analogs are wired to the fault.** `Actuator/AirSupplyBar` is the physical cause of
`Sample/LastCycleResult`: a pneumatic actuator starved of air is a valve that will not seat. So
one DDATA reports the supply sagging, and a later DDATA reports the sample that failed because of
it. Two messages, minutes apart, and the first predicted the second. Pattern 1 carries both facts
too; nothing on that side connects them.

**One inbound message: `Node Control/Rebirth`, and nothing else.** This is not a command to the
valve. It is the host asking the device to re-announce itself, and **Ignition's MQTT Engine
sends it unprompted** whenever it sees data for a device it holds no birth certificate for. An
edge node that ignores it sits permanently unknown in the tag tree, so answering it is what makes
the integration self-heal. A DCMD write to any valve metric is refused and logged: **the valve
still takes no commands**, in either pattern.

## The checkpoint that actually matters

**MQTT Engine built every tag from DBIRTH by itself. Zero files changed.**
`subscription: "spBv1.0/#"` was already there, shipped enabled. Nobody configured anything.

```
MQTT Engine/Edge Nodes/ICC26-Site1-UPSTREAM/SAMPLE-VALVE-02/SV-202/
    Valve/{State,IsOpen,PositionPct}
    Badge/{LastScanId,LastScanHolder,LastScanRole,LastScanResult,LastDenyReason,LastScanTime}
    Sample/{CycleCount,LastSampleId,LastSampleTime,LastOpenDurationS,LastCycleResult}
    Actuator/AirSupplyBar                     Device/{EnclosureTempC,FirmwareVersion,SerialNumber,Cell}
```

Right datatypes, right units. Those unit strings exist nowhere except the DBIRTH property sets,
so they are proof Engine parsed the metric *properties*, not just the names. This is both the
proof the hand-written encoder is correct **and** the half of pattern 2 that pattern 1 cannot do
at all.

**Rebirth, unprompted, in 6 milliseconds.** Restart the gateway and Engine holds no birth
certificate. The device is silent because it reports by exception, so nothing happens until it
next has something to say, and then:

```
20:28:47.193  valve   badge B-1042 granted  -> DDATA
20:28:47.196  Engine  Received message from unknown edge node - requesting rebirth
20:28:47.199  valve   rebirth requested -- re-announcing
```

No loop, no operator, no configuration. Pattern 1's consumer, having missed nothing in
particular, simply stays wrong.

## No envelope, by design

Sparkplug payloads carry no `ts`/`values` envelope. Every other pattern is a JSON document under
`icc26/`, and this one is protobuf under `spBv1.0/`, so `mosquitto_sub` needs a second
subscription and prints bytes instead of text. The one pattern that needed no agreement is the
one that does not fit the conventions the others had to invent.

## Try it

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer -t 'spBv1.0/#' -v
```

| Step | Do | What you see |
|---|---|---|
| Birth | Container connects | NBIRTH (`seq` 0, `bdSeq`), then DBIRTH with every metric, the ones with no value yet as typed nulls |
| The tag tree | Nothing | Every metric as a typed tag with its units, built by Engine alone |
| Report by exception | Badge on :8086 | One DDATA carrying **only** what changed, not the whole set. `seq` increments |
| Cause, then effect | Sag the air supply, then badge `B-1042` | One DDATA reports `Actuator/AirSupplyBar` crossing its deadband. A later one reports `Sample/LastCycleResult: failed-to-seat` |
| Death, the will | `docker kill icc26-sim-valve-spb` | NDEATH alone, published by the broker, matching `bdSeq` |
| Death, clean | `docker stop` | DDEATH *and* an explicit NDEATH; a clean DISCONNECT makes the broker discard the will |
| Rebirth | Restart MQTT Engine | It asks by itself; NBIRTH + DBIRTH again, `seq` back to 0. The page counts them |
| Recommission | Change the Device ID on the page | The old identity gets DDEATH + NDEATH before teardown, then births under the new one |
| Aliases | `VALVE_SPB_USE_ALIASES=false`, restart | The DDATA payloads visibly grow |

If the tag tree does not appear, check the gateway log for a Rebirth request loop before
suspecting the encoder.

**`bdSeq` stays 0 across container restarts.** It increments per CONNECT *within a process*, and
a restarted container is a new process. That is correct for a device that rebooted, but do not
read a `bdSeq` of 0 as "never reconnected".
