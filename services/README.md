# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-valve-mqtt/` | 1 — native MQTT pub/sub | **built and wired into compose.** Smart sample valve assembly on `BR-201`; config page on <http://localhost:8085>. **Ignition ingest verified 2026-08-17**: the Engine custom namespace auto-creates a JSON-shaped tag tree, and a null field creates no tag at all ([spec](../docs/plans/01-native-mqtt.md)) |
| `sim-valve-spb/` | 2 — Sparkplug B edge node | **built and wired into compose**, and it is the **same device** as `sim-valve-mqtt/`, not a different one (see below). Config page on <http://localhost:8086>. **Ignition ingest verified 2026-08-17**: MQTT Engine built all 19 typed tags with their engineering units straight from DBIRTH, with no configuration at all ([spec](../docs/plans/02-sparkplug-b.md)) |
| `opcua-countess/` | 3 — OPC UA → MQTT | **server built and wired into compose.** Simulated Countess 3 FL cell counter; address space per [`docs/reference/countess-3fl-opcua-model.md`](../docs/reference/countess-3fl-opcua-model.md). Ignition side: connection + `cell_analyzer` UDT done, tag-change script TODO |
| `opcua-novaflex/` | 3 — OPC UA → MQTT | **server built and wired into compose**, and it runs **alongside** `opcua-countess/`, not instead of it (see below). Simulated Nova BioProfile FLEX2; address space transcribed from the real vendor server per [`docs/reference/novaflex2-opcua-model.md`](../docs/reference/novaflex2-opcua-model.md). Ignition side: connection + `bioanalyzer` UDT + instance done and **verified bound — 57/57 tags monitored**, tag-change script TODO |
| `lims/` | 4, 6, 7 — webhook / poll / aggregation source | **implementation undecided** |
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

## The LIMS contract

`lims/` is deliberately unresolved — it may become [SENAITE](https://www.senaite.com/) or
stay a small FastAPI stub. What matters is that whatever fills it exposes all four surfaces,
because four separate patterns read from it:

1. **Emits a webhook POST** on sample-complete → pattern 4
2. **`GET /results?since_id=N`** with a monotonic id → pattern 6
3. **Writes to `lims.sample_result`** in Postgres, which Debezium tails → pattern 5
4. **Answers a query** the aggregation script can join against → pattern 7

Build against the contract, not the implementation. SENAITE satisfies all four and carries
real weight with a life-sciences audience; a ~100-line stub satisfies them too and starts in
a second. Either way the swap is a `docker-compose.yml` change, not a redesign.

The Postgres schema those patterns depend on already exists — see
`compose/postgres/initdb/02-schema.sql`.
