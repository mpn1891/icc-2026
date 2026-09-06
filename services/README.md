# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-valve-mqtt/` | 1 — native MQTT pub/sub | **built and wired into compose.** Smart sample valve assembly on `BR-201`; config page on <http://localhost:8085>. **Ignition ingest verified 2026-08-17**: the Engine custom namespace auto-creates a JSON-shaped tag tree, and a null field creates no tag at all ([spec](../docs/plans/01-native-mqtt.md), [talk track](../docs/talk-tracks/01-native-mqtt.md)) |
| `sim-valve-spb/` | 2 — Sparkplug B edge node | **built and wired into compose**, and it is the **same device** as `sim-valve-mqtt/`, not a different one (see below). Config page on <http://localhost:8086>. **Ignition ingest verified 2026-08-17**: MQTT Engine built all 19 typed tags with their engineering units straight from DBIRTH, with no configuration at all ([spec](../docs/plans/02-sparkplug-b.md), [talk track](../docs/talk-tracks/02-sparkplug-b.md)) |
| `opcua-countess/` | 3 — OPC UA example, **out of the demo** | **server built and wired into compose**, and it stays there as the worked example. Simulated Countess 3 FL cell counter; address space per [`docs/reference/countess-3fl-opcua-model.md`](../docs/reference/countess-3fl-opcua-model.md). Ignition side: `opc-connection/cell_counter` and the `cell_counter` UDT type remain; the `countess-01` instance is deleted. **MQTT publish will not be wired** — dropped from the demo 2026-08-25, not deferred |
| `opcua-cell-analyzer/` | 3 — OPC UA → MQTT | **server built and wired into compose**, and since 2026-08-25 it is **pattern 3's only instrument** (see below). Simulated the vendor cell analyzer; address space transcribed from the real vendor server per [`docs/reference/novaflex2-opcua-model.md`](../docs/reference/novaflex2-opcua-model.md). Ignition side: connection + `cell_analyzer` UDT + instance **verified bound — 57/57 tags monitored**. **MQTT publish built 2026-08-19, broker-verified 2026-08-20**: `result/sample_time` (vendor `HistoricalSampleResults/SampleTime`) → Event Stream `03_opcua/cell-analyzer-result` → `icc26/site1/qc/analyzers/cell-analyzer-01/result` with `meta.mechanism = "opcua-event"`. Does not use `ICC26Extensions`. **2026-08-26: gained its own sample-login screen on <http://localhost:8087> and stopped free-running** — the valve's sample id is typed in by a person, which is what joins pattern 1 to pattern 3 ([talk track](../docs/talk-tracks/03-opcua-analyzer.md)) |
| `lims/` | 4 — approval webhook | **built and wired into compose.** Review screen on <http://localhost:8000>. Subscribes as `lims-bridge` (publish grant empty) to **two** topics: the valve's `event/sample-complete`, which **opens the sample entry**, and the analyzer result, which **appends the analytes to it**. Holds the finished record for human review and POSTs through a transactional outbox ([spec](../docs/plans/04-lims-webhook.md) § *Revised 2026-08-26*, [talk track](../docs/talk-tracks/04-lims-webhook.md) — **rewritten 2026-09-06** for this rebuild). **Both review outcomes publish** since 2026-08-30: `disposition` `pass` on approve, `fail` on reject, each through the same outbox row |
| `sim-particle-counter/` | 6 — poll | **built and wired into compose.** Simulated particle counter serving **GraphQL over HTTPS on 8443** with JWT auth and cursor pagination, transcribed verbatim from [`docs/reference/particle_counter_sim.md`](../docs/reference/particle_counter_sim.md) and given nothing it does not document. Operator touchscreen on <http://localhost:8089> — Start/Stop, the sample point, clean/dirty room, live readout. **Nothing pushes**: Ignition's `particle_counter_poll` walks the cursor on a gateway timer, writes `em.reading`, and relays through Event Stream `06_poll/particle-counter-result` to `icc26/site1/qc/analyzers/particle-counter-01/result` with `meta.mechanism = "poll"`. `values.status` ∈ `normal | excursion` is computed **by the poll script** against `config/excursion_threshold` on the UDT — the instrument does not know what a cleanroom limit is. Broker-verified 2026-08-29 ([spec](../docs/plans/06-poll-particle-counter.md), [talk track](../docs/talk-tracks/06-poll.md), [service README](sim-particle-counter/README.md)). ~~**Remaining:** the gateway timer script.~~ **Built the same evening** — `ignition/timer/06-poll/`, 30 s fixed delay — so pattern 6 runs hands-off and the wire shows a burst of three every 30 s rather than one message every 10 s |

`sim-vibration/` — the pattern-1 vibration gateway superseded by the sample valve on
2026-08-17 — was **deleted on 2026-08-23**, along with the `vibsim` script module and both
`vibration-gw-*` event streams. Nobody wanted it back, and one of the event streams was still
enabled and subscribed to a topic no service published. Note that the `icc26-native` Engine
namespace is **not** part of that retired set: it is the sample valve's ingest surface. See
[`docs/00-architecture.md`](../docs/00-architecture.md).

## Why patterns 1 and 2 are the same device

`valve.py` and `webui.py` are **byte-for-byte identical** in both build contexts — same badge
roster, same state machine, same stroke times, same stroke faults, same simulator controls.
Fix one, copy it across, and `diff` the two before committing.

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

## Why pattern 3 built two analyzers, and demos one

They were originally sketched as alternatives. They are not — they are a pair, and the reason
only became clear once the analyzer's vendor OPC manual was read. **Only the analyzer is in the demo
as of 2026-08-25**; the Countess stays in compose as the worked example, and the contrast below
is now something said on stage rather than something shown running:

| | `opcua-countess` | `opcua-cell-analyzer` |
|---|---|---|
| Vendor OPC server | **none** — the instrument writes CSV | **yes**, licensed, shipped on the Bridge PC |
| Whose address space | ours, DI + LADS shaped | **the vendor's**, two flat trees of string-id tags |
| Completion signal | counter + events, designed in | **none.** `SampleResults` just changes |
| Actions | a method *and* a command bit | **command bits only.** No methods at all |
| Refusing a bad request | `Bad_InvalidState` from the method | nothing — the write returns `Good` regardless |

The Countess is the model we wish vendors shipped. The analyzer is what they ship. That argument
is worth making, and it survives the cut — it just gets made in a sentence now. It settles §6.1
of the Countess model doc, which claimed command bits are what actually ships because a SCADA tag
cannot invoke a method. Here is a 2024 vendor product with 104 writable bits and zero methods.

The analyzer's missing trigger is why `opcua-cell-analyzer` publishes an `ICC26Extensions` branch. It is
a separate top-level object with a `README` variable inside it saying it is not vendor, because
a tag export taken from that simulator will outlive anyone's memory of which half was invented.

Pattern 5 (CDC) adds no service of its own here — it is the `quay.io/debezium/server` image
configured from `compose/debezium/`, tailing `bes.batch_event`. Pattern 7 is a gateway script,
not a container.

## The LIMS contract — one surface since 2026-08-19

`lims/` used to serve four patterns, and the contract was the point: four surfaces, one per
consumer, so the implementation could be swapped between [SENAITE](https://www.senaite.com/) and a
FastAPI stub with a compose change.

**Patterns 5, 6 and 7 were re-sourced on 2026-08-19 and again on 2026-08-23** — CDC to our
batch table, polling to a particle counter HTTP API, aggregation to a sample-chain join — so three of
those LIMS surfaces have no consumer. What is left is the one that does:

1. **Receives analyzer results off the backbone**, holds them for a human to review, and **POSTs
   the review to Ignition** with `analyst` + `disposition` → pattern 4

Pattern 7 **subscribes to that MQTT review**. That is not a second LIMS surface.

Retired, and recorded so the reasoning survives: `GET /results?since_id=N` (old pattern 6), a
Debezium-tailed insert into `lims.sample_result` (old pattern 5), and a query against the LIMS
for an aggregation script (old pattern 7).

SENAITE was worth considering when four patterns depended on this. For one webhook, a small FastAPI
service is the right size. See [`../docs/plans/04-lims-webhook.md`](../docs/plans/04-lims-webhook.md).

The Postgres schema (`compose/postgres/initdb/02-schema.sql`) has `status` / `verified_at`
on `lims.sample_result` and the `lims.webhook_delivery` outbox. `bes.batch_event` in the same
file is pattern 5's CDC source, and **the only table in the `icc26_cdc` publication** since
2026-08-26 — `lims.sample_result` came out so an analyst review cannot arrive twice under two
mechanisms.
