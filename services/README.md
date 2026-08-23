# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-valve-mqtt/` | 1 — native MQTT pub/sub | **built and wired into compose.** Smart sample valve assembly on `BR-201`; config page on <http://localhost:8085>. **Ignition ingest verified 2026-08-17**: the Engine custom namespace auto-creates a JSON-shaped tag tree, and a null field creates no tag at all ([spec](../docs/plans/01-native-mqtt.md)) |
| `sim-valve-spb/` | 2 — Sparkplug B edge node | **built and wired into compose**, and it is the **same device** as `sim-valve-mqtt/`, not a different one (see below). Config page on <http://localhost:8086>. **Ignition ingest verified 2026-08-17**: MQTT Engine built all 19 typed tags with their engineering units straight from DBIRTH, with no configuration at all ([spec](../docs/plans/02-sparkplug-b.md)) |
| `opcua-countess/` | Extra — not the talk | **server built, still in compose.** Designed Countess 3 FL address space; MQTT never wired. Docs: [`docs/extra/`](../docs/extra/README.md) |
| `opcua-novaflex/` | 3 — OPC UA → MQTT (and 4, planned HTTPS POST) | **server built and wired into compose.** Simulated Nova BioProfile FLEX2; address space transcribed from the real vendor server per [`docs/reference/novaflex2-opcua-model.md`](../docs/reference/novaflex2-opcua-model.md). Ignition side: connection + `bioanalyzer` UDT + instance **verified bound — 57/57 tags monitored**. **MQTT publish built 2026-08-19, broker-verified 2026-08-20**: `result/sample_time` (vendor `HistoricalSampleResults/SampleTime`) → Event Stream `03_opcua/novaflex-result` → `icc26/site1/qc/analyzers/novaflex-01/result` with `meta.mechanism = "opcua-event"`. Does not use `ICC26Extensions` |
| `lims/` | Extra — not the talk | **built, retired 2026-08-23.** Still in compose until pattern 4 rebuild. Docs: [`docs/extra/lims-webhook-spec.md`](../docs/extra/lims-webhook-spec.md). Live pattern 4: [`04-novaflex-webhook.md`](../docs/plans/04-novaflex-webhook.md) |
| `sim-turbidity/` | 5 and 6 — local DB | **planned.** Writes only to database `turbidity`. Pattern 5 tails it (Debezium); pattern 6 polls it ([spec 05](../docs/plans/05-cdc-turbidity.md), [spec 06](../docs/plans/06-poll-turbidity.md)). Vendor API TBD |
| — | 5 — CDC | Debezium Server in `compose/debezium/`, not under `services/`. Source is `turbidity` |
| `sim-particle-counter/` | — | **not planned.** Pattern 6 is the turbidity database, not Modbus |
| `ams/` | — | **not planned.** Pattern 7 is TBD |
| `opcua-dcs/` | — | **not planned.** Pattern 7 is TBD |
| `sim-vibration/` | — | **retired.** Pattern-1 vibration gateway, superseded 2026-08-17. Not wired in. Pattern 7 will not resurrect it |

## Why patterns 1 and 2 are the same device

`valve.py` and `webui.py` are **byte-for-byte identical** in both build contexts — same badge
roster, same interlock, same state machine, same stroke times, same simulator controls. Fix
one, copy it across, and `diff` the two before committing.

That is the experiment's control. If the two containers differed in anything but the protocol,
every difference you could see on stage would be arguable. They do not, so the differences are
the protocol's:

| | `sim-valve-mqtt` | `sim-valve-spb` |
|---|---|---|
| Topic | a text box on the config page | derived from group/node/device; there is no field |
| QoS / retained | a dropdown and a checkbox | disabled, fixed by the spec, clause shown |
| Payload | JSON we invented | protobuf, self-describing |
| Discovery | none — hand-written Ignition tag config | DBIRTH builds the tag tree by itself |
| Death | retained JSON on a topic we chose, timestamp frozen at connect | NDEATH, spec-mandated, every consumer applies it |
| Loss detection | none | `seq` |
| Dependencies | `paho-mqtt` | `paho-mqtt` — deliberately identical, see that service's README |

Both are publish-only. Nothing on the backbone can open either valve; authorization is decided
at the sample port against a local badge roster.

## Why pattern 3 runs two analyzers

They were originally sketched as alternatives. They are not, and the reason only became clear
once the FLEX2's vendor OPC manual was read:

| | `opcua-countess` | `opcua-novaflex` |
|---|---|---|
| Vendor OPC server | **none** — the instrument writes CSV | **yes**, licensed, shipped on the Bridge PC |
| Whose address space | ours, DI + LADS shaped | **Nova's**, two flat trees of string-id tags |
| Completion signal | counter + events, designed in | **none.** `SampleResults` just changes |
| Actions | a method *and* a command bit | **command bits only.** No methods at all |
| Refusing a bad request | `Bad_InvalidState` from the method | nothing — the write returns `Good` regardless |

The Countess is the model we wish vendors shipped. The FLEX2 is what they ship. One of them
alone is a demo; the pair is an argument — and it settles §6.1 of the Countess model doc, which
claimed command bits are what actually ships because a SCADA tag cannot invoke a method. Here is
a 2024 vendor product with 104 writable bits and zero methods.

The FLEX2's missing trigger is why `opcua-novaflex` publishes an `ICC26Extensions` branch. It is
a separate top-level object with a `README` variable inside it saying it is not vendor, because
a tag export taken from that simulator will outlive anyone's memory of which half was invented.

Pattern 5 (CDC) is Debezium Server under `compose/debezium/`, tailing database `turbidity`.
Pattern 6 is an Ignition JDBC poll of the same database. The writer is `sim-turbidity/`
(planned). Odoo is not the source.

## The LIMS — leftover from 2026-08-20

`lims/` was pattern 4 until 2026-08-23. It still runs. It is not the talk. Pattern 4 rebuilds
as an HTTPS POST from `opcua-novaflex/` into an Event Stream; see
[`../docs/plans/04-novaflex-webhook.md`](../docs/plans/04-novaflex-webhook.md).

Unwire `lims/` in the same pass as that rebuild. `lims.sample_result`, the outbox, and
`mes.batch_event` have no talk consumer after that. Pattern 5's spec retires the CDC
publication that still names those tables.
