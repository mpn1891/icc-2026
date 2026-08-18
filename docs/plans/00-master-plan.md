# Pattern build specs

> What is left to build. Infra (Part 1) is done — see [`00-status.md`](00-status.md) for the one
> blocker still outstanding. Each spec below gets written up as its own file
> (`docs/plans/01…08-*.md`) and handed to an agent or teammate one at a time.
>
> [`../00-architecture.md`](../00-architecture.md) is settled truth — nothing here changes it.
> Where a per-pattern file disagrees with its summary here, **the per-pattern file is newer.**

Seven data-transaction/event patterns on Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17,
config-as-code via bind-mounted `ignition/config` + `ignition/projects`. Two or three teammates
clone, run, and collaborate via push/pull. Conference is ~5 weeks out.

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
resolved. Both have a server, an Ignition connection and a bound UDT; neither has its
tag-change publish script yet, which is all that remains of this pattern.

| | `services/opcua-countess` | `services/opcua-novaflex` |
|---|---|---|
| Instrument | Thermo Fisher Countess 3 FL cell counter | Nova Biomedical BioProfile FLEX2 |
| Vendor OPC server | **none** — the instrument writes CSV | **yes**, licensed, and we transcribed its real tag list |
| Address space | ours, DI + LADS shaped | **Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands` |
| Completion signal | counter + events, designed in | **none** — added as a labelled `ICC26Extensions` branch |
| Actions | a method *and* a command bit | **command bits only, no methods** |
| Ignition UDT | `cell_analyzer`, 44 bound tags | `bioanalyzer`, 57 bound tags |

The second earns its stage time by being what vendors actually ship: the Countess is the model
we would design, the FLEX2 is the one we would have to integrate. It also settles the argument
in §6.1 of the Countess model doc — a 2024 vendor product with 104 writable bits and zero
methods, because a SCADA tag write cannot invoke a method.

Remaining for both: a tag-change gateway event script on the counter reads the whole-result JSON
node and publishes once → `transmission.publish` to
`icc26/site1/qc/analyzers/{countess-01,novaflex-01}/result`, `mechanism=opcua-event`. Verify:
one message per counter increment, never per-value. Talk point: event-on-completion, not
value-change polling.

**04 — LIMS stub + webhook (`services/lims` + WebDev)** — blocks 05/06/07.
FastAPI stub satisfying the four-surface contract: (1) generator (interval + `POST /trigger` for
stage demo) inserts into `lims.sample_result` (as `icc26` role) and POSTs to the WebDev endpoint
with shared-secret header + idempotency key + retry/backoff, toggleable via
`POST /webhook/{enable|disable}` (the set-piece switch); (2) `GET /results?since_id=N`;
(3) the PG insert feeds Debezium; (4) `GET /results/latest` for aggregation. Port 8000.
Ignition side: WebDev POST resource in the `icc-2026` project — validate secret, dedupe on
idempotency key, tags + `transmission.publish` → `icc26/site1/qc/lims/sample-result`,
`mechanism=webhook`; correct 200/401/409 semantics. Verify: curl POST → topic message; duplicate
key → 409, no republish.

**05 — CDC (`compose/debezium/`)** — needs 04's stub + WebDev shape.
`quay.io/debezium/server:3.x`, `application.properties`: pgoutput, publication `icc26_cdc`
(already created by `04-cdc.sql`), user `cdc`, tables `lims.sample_result` + `mes.batch_event`,
**HTTP sink** → second WebDev endpoint (`cdc-sink`) that maps Debezium change events to the
envelope, `mechanism=cdc`, same sample-result topic (batch_event rows →
`icc26/site1/upstream/br-201/batch/event`). Named volume for offsets. Verify: psql `INSERT`
on stage → topic message within ~1 s.

**06 — Poll / diff** — needs 04 + the `ICC26` datasource (see `00-status.md`).
Two deliberate variants, each with its own watermark tag and an enable toggle (only one on at a
time): (a) gateway timer script polling `GET /results?since_id=N`; (b) SQL high-water-mark query
against datasource `ICC26`. Publish `mechanism=poll`, same topic. Verify: trigger a sample with
webhook+CDC disabled → message within one poll interval; show the id-vs-timestamp watermark
argument in the doc.

**07 — Scripted aggregation** — needs 04 and the `ICC26` datasource.
Gateway script (Perspective button + optional timer): joins `plant.batch` (SQL), LIMS
`GET /results/latest`, and a third live source into one composite document →
`transmission.publish` to `icc26/site1/upstream/br-201/batch-summary`, `mechanism=aggregate`.
Verify: one message containing all three source sections.

> **Open: the third source is undecided.** It used to be live `BR-201` process values from
> pattern 2's Sparkplug bioreactor, which no longer exists — pattern 2 is now a sample valve.
> Options are the valve's own metrics, a small standalone process simulator, or dropping to two
> sources. See [`02-sparkplug-b.md § Known consequence`](02-sparkplug-b.md).

**08 — Presentation + runbook** — last.
Perspective: overview page, one page per pattern (04/05/06 share a "LIMS result" page with
mechanism toggles — the set-piece control panel), and the **firehose** colored by
`meta.mechanism`. Firehose: primary = WebDev-served static HTML with **vendored** mqtt.js over
Chariot WS :8090 as `observer`, embedded in Perspective (offline-safe; riskiest UI piece);
fallback (build first) = Engine-subscribed wildcard → gateway script appends to a dataset tag →
Perspective table. `docs/demo-runbook.md` (planned — write with this spec): T-15 dual-trial
checklist, per-pattern trigger + recovery, set-piece choreography (webhook off → CDC on →
only `meta.mechanism` changes).

Two notes for patterns 1 and 2 specifically. Their Perspective page is mostly **a link to the
two device config pages on 8085 and 8086** — the comparison is between those two screens, and
rebuilding them inside Perspective would only make them less convincing. And **pattern 2 will
not appear on a firehose that colours by `meta.mechanism`**: Sparkplug payloads carry no
envelope, by design. Either colour it by topic prefix as a special case, or say out loud that
the one pattern which needed no agreement is the one that does not fit the field invented to
track agreements.

## Execution order

- **Wave 0**: the blocker in [`00-status.md`](00-status.md) — nothing else starts until a clone
  can reproduce a working stack.
- **Wave 1** (parallelizable across agents/teammates): 01, 02, 03, and 04's stub service.
- **Wave 2**: 04's WebDev half → 05, 06, 07 (parallel after 04).
- **Wave 3**: 08.
- Each wave ends with: `tasks.py health` green, pattern verification passes, `git status` shows
  only intended files, push.

## Verification (whole effort)

1. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
2. End-to-end: all seven mechanisms firing → firehose shows seven colors; the set-piece
   rehearsed; run once with networking disabled to prove offline viability.
