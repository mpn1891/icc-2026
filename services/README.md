# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-valve-mqtt/` | 1 — native MQTT pub/sub | **built and wired into compose.** Smart sample valve assembly on `BR-201`; config page on <http://localhost:8085>. **Ignition ingest verified 2026-08-17**: the Engine custom namespace auto-creates a JSON-shaped tag tree, and a null field creates no tag at all ([spec](../docs/plans/01-native-mqtt.md)) |
| `sim-valve-spb/` | 2 — Sparkplug B edge node | **built and wired into compose**, and it is the **same device** as `sim-valve-mqtt/`, not a different one (see below). Config page on <http://localhost:8086>. **Ignition ingest verified 2026-08-17**: MQTT Engine built all 19 typed tags with their engineering units straight from DBIRTH, with no configuration at all ([spec](../docs/plans/02-sparkplug-b.md)) |
| `opcua-countess/` | 3 — OPC UA → MQTT | **server built and wired into compose.** Simulated Countess 3 FL cell counter; address space per [`docs/reference/countess-3fl-opcua-model.md`](../docs/reference/countess-3fl-opcua-model.md). Ignition side: connection + `cell_analyzer` UDT done, tag-change script TODO |
| `opcua-novaflex/` | 3 — OPC UA → MQTT | **server built and wired into compose**, and it runs **alongside** `opcua-countess/`, not instead of it (see below). Simulated Nova BioProfile FLEX2; address space transcribed from the real vendor server per [`docs/reference/novaflex2-opcua-model.md`](../docs/reference/novaflex2-opcua-model.md). Ignition side: connection + `bioanalyzer` UDT + instance **verified bound — 57/57 tags monitored**. **MQTT publish built 2026-08-19**: `result/sample_time` (vendor `HistoricalSampleResults/SampleTime`) → Event Stream `03_opcua/novaflex-result` → `icc26/site1/qc/analyzers/novaflex-01/result`. Does not use `ICC26Extensions` |
| `lims/` | 4 — approval webhook | **specified, not built.** Was the source for 4, 6 and 7; since the 2026-08-19 re-sourcing it serves pattern 4 only. Subscribes to the analyzer topic, holds results for human approval, POSTs the released result to Ignition via a transactional outbox ([spec](../docs/plans/04-lims-webhook.md)) |
| `odoo/` | 5 — CDC source | **planned.** Debezium tails Odoo's own Postgres. Community edition only — the Quality app is Enterprise, so the demo keys off `mrp_production` / `stock_move` |
| `sim-particle-counter/` | 6 — poll / diff | **planned.** Simulated Hach MET ONE over Modbus TCP, with the vendor's buffered record block and its *Rotate Buffer* checkbox — two ways to lose data, selected by a tickbox |
| `ams/` | 7 — aggregation trigger | **planned.** Asset management system stub; a copy of `lims/`'s FastAPI skeleton with a different noun |
| `opcua-dcs/` | 7 — steady-state gate | **planned.** Small OPC UA server, cheapest possible new source because that toolchain is already proven |
| `sim-vibration/` | — | **retired.** The pattern-1 vibration gateway, superseded on 2026-08-17 by the sample valve. Not wired in, kept on disk only until it is clear nobody wants it back |

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

Pattern 5 (CDC) adds no service of its own here — it is the `quay.io/debezium/server` image
configured from `compose/debezium/`.

## The LIMS contract — one surface since 2026-08-19

`lims/` used to serve four patterns, and the contract was the point: four surfaces, one per
consumer, so the implementation could be swapped between [SENAITE](https://www.senaite.com/) and a
FastAPI stub with a compose change.

**Patterns 5, 6 and 7 were re-sourced on 2026-08-19** — CDC moved to Odoo, polling to a MET ONE
particle counter, aggregation to condition monitoring — so three of those surfaces have no
consumer. What is left is the one that does:

1. **Receives analyzer results off the backbone**, holds them for a human to approve, and **POSTs
   the approved result to Ignition** → pattern 4

Retired, and recorded so the reasoning survives: `GET /results?since_id=N` (pattern 6), a
Debezium-tailed insert into `lims.sample_result` (pattern 5), and a query for the aggregation script
to join (pattern 7).

SENAITE was worth considering when four patterns depended on this. For one webhook, a small FastAPI
service is the right size. See [`../docs/plans/04-lims-webhook.md`](../docs/plans/04-lims-webhook.md).

The Postgres schema exists (`compose/postgres/initdb/02-schema.sql`) and needs two columns added;
note that `mes.batch_event` in the same file now has **no consumer at all**, since Odoo replaces the
hand-made MES table.
