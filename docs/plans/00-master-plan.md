# Pattern build specs

> What is left to build. Infra (Part 1) is done and the stale-image blocker is cleared — see
> [`00-status.md`](00-status.md). Each spec below gets written up as its own file
> (`docs/plans/01…08-*.md`) and handed to an agent or teammate one at a time.
>
> [`../00-architecture.md`](../00-architecture.md) is settled truth. It is *usually* not changed
> from here — but it was on **2026-08-19** (shared-topic set-piece dropped) and again on
> **2026-08-23**, when patterns 5, 6 and 7 were re-sourced and pattern 4 gained a pass/fail
> disposition. Read those sections before touching 04, 05, 06 or 07.
> Where a per-pattern file disagrees with its summary here, **the per-pattern file is newer.**

Seven data-transaction/event patterns on Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17,
config-as-code via bind-mounted `ignition/config` + `ignition/projects`. Two or three teammates
clone, run, and collaborate via push/pull. Conference is ~4 weeks out.

All seven `meta.mechanism` values still have exactly one user each. Patterns 1–6 still carry
different data; pattern 7 is the join of several of them into one aggregate document. The
namespace still must not leak the mechanism.

Locked in: GitHub private repo under Matt's account; mixed Windows + macOS/Linux team; FastAPI
stub for LIMS; demo-grade committed credentials are acceptable.

**Each spec must carry:** objective + talk point, files to create (with sketches), Ignition
resources (exact paths where known, UI-then-commit where schemas are unknown), MQTT user +
topics, empirical checkpoints, copy-pasteable verification, and "update the `docs/0N-*.md`
talk-track doc" as the closing step.

## Cross-cutting conventions

These apply to every spec below and belong at the top of `00-conventions.md` when that file
is written (planned — not present yet).

- **Envelope + namespace**: reference `docs/00-architecture.md`; `meta.mechanism` ∈
  `native-mqtt | sparkplug | opcua-event | webhook | cdc | poll | aggregate`.
- **Ignition-originated publishes go through Transmission**:
  `system.cirruslink.transmission.publish("chariot_broker", topic, payload, qos, retain)`.
  Discovered constraint: the `ign-engine` ACL only allows publishing commands
  (`.../cmd/#`, NCMD/DCMD) — `ign-transmission` has `icc26/#` publish rights. Engine is for
  ingest + command-out; Transmission for event-out. This is itself a talk point.
- **Authoring Ignition config**: known file formats (tags, project resources: scripts, WebDev,
  Perspective views) → edit files + `python tasks.py scan`; unknown gateway-scoped schemas
  (device connections, OPC UA connections, Engine namespaces) → create via UI once, read the
  files `git status` reveals, commit, then parametrize.
- **Python service skeleton**: paho-mqtt 2.x, env-config (`BROKER_HOST=chariot`, creds), retained
  LWT where specified, reconnect-with-backoff, envelope helper (~20 lines, duplicated per
  service — no shared lib, keeps build contexts self-contained).
- **Compose service pattern**: `build: ./services/<name>`, `container_name: icc26-<name>`,
  `restart: unless-stopped`, broker reconnection handled in-app (no depends_on chariot).
- **Verification harness**: `docker run --rm -it --network icc26 eclipse-mosquitto:2
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v` (pull the image once, before
  the conference; observer ACL already covers `icc26/#`, `spBv1.0/#`, `$SYS/#`).
- Commit hygiene: commit the files you meant to change and `git restore` the timestamp churn;
  never commit gitignored identity paths. See *Working rules* in [`00-status.md`](00-status.md).

## The eight specs

**01 and 02 are one device in two firmwares, and they must be read together.**
**Written up in [`01-native-mqtt.md`](01-native-mqtt.md) and
[`02-sparkplug-b.md`](02-sparkplug-b.md), which supersede this entry entirely.** Both moved a
very long way from the sketches that were here — pattern 1 was a wireless vibration gateway
simulated inside Ignition, pattern 2 was a bioreactor UDT on the Programmable Device Simulator.
Each file carries the reasoning and a deviations table; do not "fix" either back toward an older
summary.

A **smart sample valve assembly**: a sanitary sample valve with an RFID badge reader on a
bioreactor's sample port. Badge in, the valve strokes open for a sampling window, the valve
closes; every scan is published, granted or denied. `services/sim-valve-mqtt` on `BR-201`
speaks plain MQTT; `services/sim-valve-spb` on `BR-202` is the *same device* speaking Sparkplug
B v3.0.0. `valve.py` and `webui.py` are byte-for-byte identical between the two build contexts,
so everything that differs between the containers is a difference the protocol caused.

Both are **publish-only** — no command topic, nothing on the backbone can open either valve,
authorization is decided locally against a badge roster. Each container serves its own **device
commissioning webpage** (8085, 8086), and the difference between those two pages is as much of
the talk as the traffic: a free-text topic, a QoS dropdown and a retained checkbox on one; the
same three controls disabled, with the TCK clause that fixed each, on the other. Talk point:
spec-mandated versus hand-rolled — the topic, the datatypes, the units, the loss detection and
the death certificate, one protocol giving you all five and the other giving you three form
fields and a wiki page.

**03 — OPC UA → MQTT** — no dependencies. **Both servers built, and written up in
[`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md), which supersedes this entry.**

Two analyzers, and they **run together rather than as alternatives** — that decision is
resolved. Both have a server, an Ignition connection and a bound UDT. Nova MQTT publish is
built (vendor `SampleTime` → Event Stream → Transmission). Countess still needs its publish.

| | `services/opcua-countess` | `services/opcua-novaflex` |
|---|---|---|
| Instrument | Thermo Fisher Countess 3 FL cell counter | Nova Biomedical BioProfile FLEX2 |
| Vendor OPC server | **none** — the instrument writes CSV | **yes**, licensed, and we transcribed its real tag list |
| Address space | ours, DI + LADS shaped | **Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands` |
| Completion signal | counter + events, designed in | **none vendor-side** — Ignition publishes off `HistoricalSampleResults/SampleTime`; `ICC26Extensions` remains in the address space but is not the MQTT trigger |
| Actions | a method *and* a command bit | **command bits only, no methods** |
| Ignition UDT | `cell_analyzer`, 44 bound tags | `bioanalyzer`, 57 bound tags |

The second earns its stage time by being what vendors actually ship: the Countess is the model
we would design, the FLEX2 is the one we would have to integrate. It also settles the argument
in §6.1 of the Countess model doc — a 2024 vendor product with 104 writable bits and zero
methods, because a SCADA tag write cannot invoke a method.

Nova is done: `result/sample_time` → Event Stream `03_opcua/novaflex-result` →
`icc26/site1/qc/analyzers/novaflex-01/result`, `mechanism=opcua-event`. Remaining: the same
shape for Countess on `count_completed_counter`. Verify Nova: one message per completed
sample, never per-value, nothing on abort/fail/QC. Talk point: event-on-completion, keyed
off the field the vendor actually ships.

**04 — LIMS approval webhook (`services/lims` + WebDev)** — **built 2026-08-20.**
**Written up in [`04-lims-webhook.md`](04-lims-webhook.md), which supersedes this entry
except for the remaining delta below.** Do not "fix" the built shape back toward an older
summary: subscribe to pattern 3, hold for a human, webhook into Ignition, no LIMS MQTT
publish rights, transactional outbox. Port 8000, approval screen served by the LIMS itself.

**Remaining (2026-08-23):** both review outcomes publish. Approve and reject each write an
outbox row and land on `icc26/site1/qc/lims/sample-result` with `analyst` and
`values.disposition` ∈ `pass | fail`. Reject is no longer silent — pattern 7 listens for the
review, not only for a pass. The rest of the contract is unchanged.

**05 — CDC on our own batch table** — decided 2026-08-23, no spec yet.
A **very simple batch engine inside Ignition**: a gateway timer, started and stopped from
enable/disable tags, auto-cycles `BR-201` through `CIP → SIP → INOC → GROWTH → HARVEST`.
The timer writes the bioreactor UDT (add an operation/phase tag to the existing
`bioreactor` type) and inserts `mes.batch_event` in the same step. The sequencer publishes
**nothing** onto MQTT.

Debezium tails **our** Postgres — `quay.io/debezium/server:3.x`, pgoutput, user `cdc`,
publication on `mes.batch_event` only (drop `lims.sample_result` from `04-cdc.sql`).
HTTP sink → WebDev (`cdc-sink`) → envelope `mechanism=cdc` →
`icc26/site1/upstream/br-201/batch/event`. Named volume for offsets.

Talk point, said honestly: **CDC's textbook case is an application you do not own.** This
engine is one we do own, and it is a stand-in for an MES we would not patch to emit events.
The demonstration is still the observer: the writer does not know MQTT exists, the `cdc`
role is not `icc26`, and disabling Debezium leaves the reactor cycling with a silent topic.
That is the failure demo — not a fake ERP UI.

The JDBC datasource `ICC26` is now on the critical path (the timer writes the table). It is
not in the repo yet; **UI first, then commit.**

Verify: flip the enable tag → phase tags advance on a dwell → a row appears in
`mes.batch_event` → a message on `batch/event` within ~1 s. Stop Debezium, leave the timer
running, show the gap.

**06 — Poll of a MET ONE environmental analyzer** — decided 2026-08-23, no spec yet.
A simulated Hach MET ONE particle counter beside `BR-201`, speaking an **HTTP API** (not
Modbus). New service `services/sim-metone`. The exact routes and record shape are **TBD** —
vendor API docs will be dropped in; until then treat it as "a pull API that returns
completed particle-count analysis events with a timestamp and channel counts," watermarked
on record id or analysis time.

An Ignition gateway timer polls that API. **On a new analysis, the script submits the
payload to an Event Stream** (`06_poll/metone-result`), which publishes through
Transmission to `icc26/site1/upstream/br-201/particle-counter-01/result` with
`mechanism=poll`. Same relay shape as pattern 3; the acquisition is the poll.

Do not invent a Modbus device connection or a rotate-buffer checkbox for this revision.
Those were the 2026-08-19 plan. The talk is now: an environmental instrument that only
offers a pull API, so Ignition has to notice that an analysis happened.

Verify: one HTTP analysis → one Event Stream fire → one MQTT message. Stall the poll and
show a missed (or late) analysis.

**07 — Scripted aggregation: the sample chain** — decided 2026-08-23, no spec yet.
**This is the designated cut** if the schedule bites: it is the join of 01, 03, 04, 05 and
06, and it cannot start until those four sources exist.

A gateway script **listens for the pattern-4 LIMS review** on MQTT (`qc/lims/sample-result`,
pass or fail). On that message it builds one composite document,
`mechanism=aggregate`, on `icc26/site1/upstream/br-201/sample-chain/event`:

| Section | Source | What it answers |
|---|---|---|
| Valve open | pattern 1 event (`sample-valve-01`) | when material left the reactor |
| Analysis complete | pattern 3 Nova result | when the sample was run; **duration outside = open → analysis** |
| Reactor operation | pattern 5 batch/phase at sample time | CIP / SIP / INOC / GROWTH / HARVEST when the valve opened |
| Environment | pattern 6 MET ONE | **the nearest particle-count analysis to the Nova timestamp** |

Always publish. A dirty or missing MET ONE reading is a finding in the document, not a
refusal. `meta.correlation_id` is the `sample_id` already stamped in 3 and 4, so one
sample is four colours on the firehose (opcua-event, webhook, and whatever 5/6 contributed)
plus the aggregate.

This is a real join, not the old "same LIMS row three ways." Pattern 7 no longer uses a
webhook, a waveform, or `cmd`/`response`. `downstream` still has no user; `utilities`
still has none.

Verify: badge the valve, complete a Nova sample, review it in the LIMS → one aggregate
message carrying duration, operation, and the nearest MET ONE record.

**08 — Presentation + runbook** — **start the fallback firehose now, not last.**
Perspective: overview page, one page per pattern, and the **firehose** colored by
`meta.mechanism`. Firehose: primary = WebDev-served static HTML with **vendored** mqtt.js over
Chariot WS :8090 as `observer`, embedded in Perspective (offline-safe; riskiest UI piece);
fallback = Engine-subscribed wildcard → gateway script appends to a dataset tag →
Perspective table. `docs/demo-runbook.md` (planned — write with this spec): T-15 dual-trial
checklist, per-pattern trigger + recovery.

There is **no shared "LIMS result" page with mechanism toggles** — that set-piece is gone.
Each of 04/05/06 gets its own page. Pattern 7's page *is* the join: one sample's valve,
analysis, review, batch phase and env reading on one screen. Runbook choreography is
per-pattern failure (webhook without an outbox, CDC with Debezium down, a poll that
stalls, an aggregate whose MET ONE nearest-neighbour is missing or dirty).

**Build the fallback firehose in the next working session.** It is the only deliverable whose
absence is visible from the audience, it is the one piece with no dependencies on any pattern, and
scheduling it last means it competes with whatever is running late.

Two notes for patterns 1 and 2 specifically. Their Perspective page is mostly **a link to the
two device config pages on 8085 and 8086** — the comparison is between those two screens, and
rebuilding them inside Perspective would only make them less convincing. And **pattern 2 will
not appear on a firehose that colours by `meta.mechanism`**: Sparkplug payloads carry no
envelope, by design. Either colour it by topic prefix as a special case, or say out loud that
the one pattern which needed no agreement is the one that does not fit the field invented to
track agreements.

## Execution order

**Rewritten 2026-08-23.** Patterns 1–3 stay as built. Pattern 4 is built minus pass/fail.
Pattern 7 is the only join, so it is last of the seven.

- **Wave 0** — done. 01, 02, 03 built and broker-verified; 04 built minus disposition.
- **Wave 1**, independent of each other:
  - **04 pass/fail.** Small: both review outcomes publish `disposition` + `analyst`.
    Pattern 7 cannot listen until this lands.
  - **08's fallback firehose.** No dependencies, and the only thing the audience sees.
  - **05** — timer + `mes.batch_event` + Debezium. JDBC datasource first.
  - **06** — MET ONE HTTP simulator + poll script + Event Stream. Longest new service;
    start it once the API notes arrive, or stub the routes.
- **Wave 2**: 07 (the sample-chain aggregate — needs 01, 03, 04, 05, 06), 08's primary firehose.
- **Wave 3**: runbook, dual-trial rehearsal, the offline run.
- Each wave ends with: `tasks.py health` green, pattern verification passes, `git status` shows
  only intended files, push.

No Odoo, so the old "nuke before Odoo init" rule is gone. `nuke` still rebuilds `icc26` and
the CDC publication; land the `mes.batch_event`-only publication change before Debezium
first connects, or drop the slot and reconnect.

## Verification (whole effort)

1. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
2. **Per pattern, the failure too.** A webhook whose delivery is lost without an outbox; a
   batch engine that keeps cycling while Debezium is down; a poll that stalls and misses an
   analysis; an aggregate that still publishes when the nearest MET ONE is missing.
   A mechanism shown only working is a mechanism nobody can judge.
3. End-to-end: all seven mechanisms firing → firehose shows seven colors; one sample's
   valve → Nova → LIMS review → aggregate on the same `correlation_id`. Run once with
   networking disabled to prove offline viability.
4. **A subscriber cannot tell how anything arrived.** Read the topic list to somebody who has not
   seen the build and ask them which patterns use CDC. That, not the old switch-over, is now how
   the namespace claim gets tested.
