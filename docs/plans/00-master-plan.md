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

**01 — Native MQTT** — no dependencies. **Written up and in progress:
[`01-native-mqtt.md`](01-native-mqtt.md), which supersedes this entry entirely.** The design
moved a long way from the sketch that was here — the gateway is simulated inside Ignition rather
than in a container, the command topic is fleet-addressed, there is no correlation id and no
LWT. That file carries the reasoning and a deviations table; do not "fix" it back toward an
older summary.

**02 — Sparkplug B edge node** — no dependencies; no new container.
Programmable Device Simulator device (UI-then-commit; program CSV committed) feeding a
`Bioreactor` UDT (temp, pH, DO, agitation, level, OUR) instance `BR-201`; MQTT Transmission tag
tree + deadbands for report-by-exception (transmitter already fixed to
`ICC26-Site1-UPSTREAM`/`UPSTREAM-EDGE-01`, already set). Verify: `mosquitto_sub 'spBv1.0/#'` shows
NBIRTH/DBIRTH/DDATA; stopping the gateway produces NDEATH. Talk point: free birth/death
(NDEATH/DDEATH) vs pattern 1, which deliberately claims no lifecycle at all.

**03 — OPC UA → MQTT (`services/opcua-novaflex`)** — no dependencies.
`asyncua` server: glucose, lactate, glutamine, glutamate, ammonia, pH, osmolality +
`SampleCompleteCounter`; sample cycle every ~120 s (env-tunable) updates all analytes then
increments the counter. Ignition OPC UA client connection to `opc.tcp://opcua-novaflex:4840`
(UI-then-commit); tag-change gateway event script on the counter assembles ALL analytes into one
payload → `transmission.publish` to `icc26/site1/qc/analyzers/novaflex-01/result`,
`mechanism=opcua-event`. Verify: one message per counter increment, never per-value. Talk
point: event-on-completion, not value-change polling.

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

**07 — Scripted aggregation** — needs 04, 02, and the `ICC26` datasource.
Gateway script (Perspective button + optional timer): joins `plant.batch` (SQL), LIMS
`GET /results/latest`, and live `BR-201` tag values into one composite document →
`transmission.publish` to `icc26/site1/upstream/br-201/batch-summary`, `mechanism=aggregate`.
Verify: one message containing all three source sections.

**08 — Presentation + runbook** — last.
Perspective: overview page, one page per pattern (04/05/06 share a "LIMS result" page with
mechanism toggles — the set-piece control panel), and the **firehose** colored by
`meta.mechanism`. Firehose: primary = WebDev-served static HTML with **vendored** mqtt.js over
Chariot WS :8090 as `observer`, embedded in Perspective (offline-safe; riskiest UI piece);
fallback (build first) = Engine-subscribed wildcard → gateway script appends to a dataset tag →
Perspective table. `docs/demo-runbook.md` (planned — write with this spec): T-15 dual-trial
checklist, per-pattern trigger + recovery, set-piece choreography (webhook off → CDC on →
only `meta.mechanism` changes).

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
