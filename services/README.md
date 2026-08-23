# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-valve-mqtt/` | 1 — native MQTT pub/sub | **built and wired into compose.** Smart sample valve assembly on `BR-201`; config page on <http://localhost:8085>. **Ignition ingest verified 2026-08-17**: the Engine custom namespace auto-creates a JSON-shaped tag tree, and a null field creates no tag at all ([spec](../docs/plans/01-native-mqtt.md)) |
| `sim-valve-spb/` | 2 — Sparkplug B edge node | **built and wired into compose**, and it is the **same device** as `sim-valve-mqtt/`, not a different one (see below). Config page on <http://localhost:8086>. **Ignition ingest verified 2026-08-17**: MQTT Engine built all 19 typed tags with their engineering units straight from DBIRTH, with no configuration at all ([spec](../docs/plans/02-sparkplug-b.md)) |
| `opcua-countess/` | Extra — not the talk | **server built, still in compose.** Designed Countess 3 FL address space; MQTT never wired. Docs: [`docs/extra/`](../docs/extra/README.md) |
| `opcua-novaflex/` | 3 — OPC UA → MQTT | **server built and wired into compose.** Simulated Nova BioProfile FLEX2; address space transcribed from the real vendor server per [`docs/reference/novaflex2-opcua-model.md`](../docs/reference/novaflex2-opcua-model.md). Ignition side: connection + `bioanalyzer` UDT + instance **verified bound — 57/57 tags monitored**. **MQTT publish built 2026-08-19, broker-verified 2026-08-20**: `result/sample_time` (vendor `HistoricalSampleResults/SampleTime`) → Event Stream `03_opcua/novaflex-result` → `icc26/site1/qc/analyzers/novaflex-01/result` with `meta.mechanism = "opcua-event"`. Does not use `ICC26Extensions`. **Untouched by pattern 4** — the webhook is its own container |
| `webhook-novaflex/` | 4 — HTTPS webhook | **built 2026-08-23, UNVERIFIED against a running stack.** The same FLEX2 with only a callback URL. Device page on <http://localhost:8084>. POSTs a vendor-shaped body to Event Stream `04_webhook/novaflex-result`, which republishes onto **pattern 3's topic** with `meta.mechanism = "webhook"`. The sender was exercised locally against a stub receiver; the receiving Event Stream was **authored blind** and its source type, config keys and mount URL are guesses. No dependencies at all — stdlib only. Read the deviations table and run the runbook in [`docs/04-novaflex-webhook.md`](../docs/04-novaflex-webhook.md) before demoing |
| `lims/` | Extra — not the talk | **built, retired 2026-08-23, unwired.** Commented out of compose, `lims-bridge` dropped from the Chariot ACLs, `lims.*` marked Extra but not dropped. Kept on disk as **pattern 4's proven fallback** — its WebDev `doPost` was hand-authored as files and broker-verified. Docs: [`docs/extra/lims-webhook-spec.md`](../docs/extra/lims-webhook-spec.md) |
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

## Why pattern 4 is a third container and not a flag on the second

`opcua-novaflex/` and `webhook-novaflex/` are the same instrument twice, the way
`sim-valve-mqtt/` and `sim-valve-spb/` are — but for a different reason, and the difference is
worth keeping straight.

The valves are one device in two *firmwares*, so their shared files are byte-identical and
every remaining difference is the protocol's doing. The two FLEX2 containers are one device in
two *product configurations*: a licensed OPC UA server, and an instrument whose whole outward
surface is a callback URL. Nothing is shared byte-for-byte there — `_culture()` and the result
synthesis are **copied** into `webhook-novaflex/app.py` and should be diffed when either
changes, per the house convention.

The build spec sketched pattern 4 as a POST added inside `opcua-novaflex/app.py`. A separate
container was chosen instead:

- **Bought:** pattern 3 is live and broker-verified, and nothing about pattern 4 can disturb
  it. Stop, restart, retoggle or misconfigure the webhook device freely.
- **Paid:** two independent sample lifecycles, so **one physical sample cannot appear on the
  result topic twice.** Both paths stamp `sample_id` into `meta.correlation_id` and the id
  format is identical, but lining a specific id up across both mechanisms is manual
  (`WEBHOOK_NOVAFLEX_SAMPLE_ID_START`). Do not promise a matching pair on stage without
  having set it. See [`../docs/04-novaflex-webhook.md`](../docs/04-novaflex-webhook.md)
  § Deviations.

## The LIMS — unwired 2026-08-23

`lims/` was pattern 4 until 2026-08-23. It no longer runs: the compose service is commented
out, `lims-bridge` is gone from `compose/chariot/mqtt-users.json`, and `lims.*` is marked Extra
in the schema. Nothing was deleted.

It stays because it is **pattern 4's proven fallback.** Its WebDev endpoint at
`ignition/projects/icc-2026/com.inductiveautomation.webdev/resources/lims/sample-result/` was
hand-authored as plain files — no gateway UI — and broker-verified on 2026-08-20, publishing
with `system.cirruslink.transmission.publish`. Pattern 4's Event Stream was authored blind; if
it will not load, this is the one-step recovery, and `webhook_event.handle_webdev()` is
already written for it.

`lims.sample_result`, the outbox, and `mes.batch_event` have no consumer. Drop the tables in
the same volume rebuild that pattern 5 needs, along with the CDC publication that still names
them — deliberately, in one pass, rather than adding a `nuke` to an HTTP change.
