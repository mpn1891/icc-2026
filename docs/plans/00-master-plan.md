# Master plan — team-sharing fixes + pattern build specs

> **Status: approved plan, not yet executed** (written 2026-08-09). Part 1 must land before any
> pattern work starts. Part 2's per-pattern spec docs (`docs/plans/01…08-*.md` +
> `00-conventions.md`) get written as Part 2's first deliverable, then handed to agents/teammates
> one file at a time.

## Context

This repo is the demo environment for an ICC 2026 talk on seven data-transaction/event patterns
(Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17, config-as-code via bind-mounted
`ignition/config` + `ignition/projects`). Step 1 (infra) is built; steps 2–9 (patterns) are not.
Two or three teammates need to clone, run, and collaborate via push/pull, starting now
(conference is ~5 weeks out).

**Verdict on the current implementation: it will NOT work for team sharing yet.** Findings:

1. **Nothing is committed** — 0 commits, no branch history, no remote. (~935 committable files
   under `ignition/`, 3.8 MB; `.gitignore`/`.gitattributes` verified correct for runtime noise
   and EOL.)
2. **The fresh-clone path is self-contradictory.** `tasks.py up` (tasks.py:548) gates on
   "`ignition/config` populated" as its proxy for "volume seeded". Once config is committed, a
   teammate's clone has populated config but no volume → `up` proceeds and breaks the gateway;
   `seed` (tasks.py:517) offers to **overwrite the committed config** with the vanilla baseline.
   No path both seeds a teammate's volume and preserves shared config.
3. **Committed Cirrus configs are non-portable.** Broker passwords are stored as `"type":
   "Embedded"` JWE ciphertext encrypted with a gateway-local key that is gitignored — teammates
   would pull config their gateway cannot decrypt → broker auth silently broken. Also
   Engine's broker URL is `tcp://localhost:1883` (should be `tcp://chariot:1883`).
4. **Machine-specific values would churn forever**: `systemName` is a Docker container ID;
   ~17 auto-generated MQTT diagnostic tag files keyed to per-machine edge-node IDs.
5. **Guardrails don't guard**: `verify-modules` result discarded by `seed`/`up`; `main()` always
   exits 0; manifest sha256 fields empty; `compose/ignition/modules/README.md` referenced in 4
   places but the real file is `compose/ignition/MODULES.md`.

Decisions locked in: **GitHub private repo under Matt's account** (created via `gh`); **mixed
Windows + macOS/Linux team**; **FastAPI stub for LIMS**; **demo-grade committed credentials are
acceptable** (portability, not secrecy, is the goal).

`docs/00-architecture.md` is settled truth (baked modules, two-pass seed, manual commissioning,
Chariot trial API) — nothing below changes the architecture.

---

# Part 1 — Infra: make clone-and-run and push/pull actually work

## Phase A — `tasks.py` lifecycle + guardrails (code)

**A1. Exit codes.** `main()` (tasks.py:841) must propagate handler results (None/True→0,
False→1). `task_health` returns its healthy bool; `seed`/`up` failure paths return non-zero.

**A2. `verify-modules` hard-gates `seed` and `up`** (currently discarded at tasks.py:515/545).
Abort with a pointer to `compose/ignition/MODULES.md`. `init` stays warn-only.

**A3. Seed state machine.** Replace the single `is_populated()` check with two axes —
volume exists (`docker volume inspect <project>_ign-data`, project from
`COMPOSE_PROJECT_NAME` env → `.env` → `icc26`) × `ignition/config` populated:

| Volume | config | Behavior |
|---|---|---|
| absent | empty | **Full seed** — current behavior (seed compose, commissioning wait, full export, down) |
| absent | populated | **Clone seed** (new; teammate / post-nuke): seed compose, commissioning wait, export **only gitignored identity pieces**, down. Never `_rmtree` committed config |
| exists | populated | Error: "already seeded — use `nuke` to rebuild" |
| exists | empty | Error: half-initialized — point at `nuke` |

Clone-seed identity export (`docker cp` from `icc26-ignition-seed`; all destinations gitignored):
`data/config/local/.` → `ignition/config/local/`; `data/config/resources/local/.` →
`ignition/config/resources/local/`; `data/config/ignition/tags/valueStore.idb` → same path.
Warn-and-continue if a source path is absent. (Whether 8.3 regenerates these gracefully when
absent is unverified — the export is cheap insurance; E1 partially answers it.)

**A4. `up` gates on volume existence** (same mechanism), with the config-populated check kept as
secondary. Absent volume → "run `python tasks.py seed`", non-zero exit.

**A5. Doc sync** — README quickstart, `ignition/README.md`, `docs/00-architecture.md` seeding
section: describe the teammate flow *clone → drop .modl → init → seed (browser commissioning) →
up*, and remove "directories are empty until seed" claims.

## Phase B — config/compose edits (offline)

- **B1 systemName**: hand-edit `ignition/config/resources/core/ignition/system-properties/config.json`
  → `"ICC26-Ignition"`; add `hostname: icc26-ignition` to the ignition service in **both** compose
  files (entrypoint derives default name from hostname → stable in the volume too).
- **B2 Sparkplug IDs** (root cause of churn + needed by Pattern 2 anyway): in
  `.../com.cirruslink.mqtt.transmission.gateway/transmitter/Example Transmitter/config.json` set
  `groupId: "ICC26-Site1-USP"`, `edgeNodeId: "USP-EDGE-01"`.
- **B3 gitignore** the auto-generated diagnostic tag trees, then (stack up, after B2) delete the
  stale `Edge Node 6bd5cf`/`c43bcf` dirs from disk:
  ```gitignore
  ignition/config/resources/core/ignition/tag-definition/MQTT Engine/Edge Nodes/
  ignition/config/resources/core/ignition/tag-definition/MQTT Transmission/Transmission Info/Transmitters/*/Edge Nodes/
  ```
- **B4 .env.example**: delete inert `POSTGRES_IGNITION_*`/`POSTGRES_DEMO_USER`/`POSTGRES_CDC_*`
  vars (note `01-databases.sql` is source of truth; keep `POSTGRES_DEMO_DB` — health uses it);
  pin `CHARIOT_VERSION` (inspect running container for the tag, likely 3.0.1); default
  `CHARIOT_MQTTS_PORT=18883` (Windows reserved-range safe, harmless elsewhere); add
  `MQTT_ENGINE_PASSWORD`, `MQTT_TRANSMISSION_PASSWORD`, `MQTT_DISTRIBUTOR_PASSWORD`,
  `ICC26_DB_PASSWORD` (feed the env secret provider; must match `mqtt-users.json`/init SQL).
- **B5 compose**: pass those four vars into `ignition.environment` as `${VAR:-default}`
  (docker-compose.yml only; seed pass runs vanilla config). Optional: basic healthchecks for
  chariot (web port) and ignition (StatusPing; caveat RUNNING≠commissioned — verify curl exists
  in the image first).
- **B6 broken refs**: point `README.md:30`, `.gitignore:40`, `modules.manifest.json` note at
  `compose/ignition/MODULES.md`. Don't move the file (Dockerfile COPYs `modules/`).
- **B7 churn convention** in README + `ignition/README.md`: every gateway write touches
  `lastModification*` in `resource.json` — *commit the files you meant to change, `git restore`
  the rest*. Also: changed `.env` secrets need a container restart, not `scan`.

## Phase C — empirical, on the running stack (UI-then-commit; C1 before C2/C3)

- **C1 Secret Provider**: Gateway UI → create an environment-variable-backed Secret Provider named
  `env`. Discover the resulting resource files via `git status`, confirm nothing machine-specific,
  commit. (If 8.3.8 lacks an env-var provider type, fall back to a file-based provider backed by a
  bind-mounted secrets file — record the deviation.) **Never hand-author unknown schemas.**
- **C2 Convert the three Cirrus Embedded secrets to Referenced** (Engine `Chariot SCADA` — also
  fix URL → `tcp://chariot:1883` and try username `ign-engine`; Transmission `chariot_broker` —
  `password` + `rpcPassword`; Distributor admin). After each: confirm connected in gateway UI +
  Chariot client list, `grep -r '"type": "Embedded"'` the Cirrus configs → must be empty, commit.
  If the `ign-engine` ACL breaks Engine, keep `admin` and note it.
- **C3 Postgres JDBC datasource** `ICC26` → `jdbc:postgresql://postgres:5432/icc26`, user
  `icc26`, password as a **Referenced** secret (`ICC26_DB_PASSWORD`). Verify Valid, commit the
  discovered resource files. (Patterns 6/7 need this; README already claims it exists.)
- **C4 Module hashes**: `python tasks.py hash-modules` → paste sha256s into
  `modules.manifest.json` → `verify-modules` green.

## Phase D — first commit + remote

- **D1 audit**: `git add -n .` review — no `.env`/`.modl`/`local/`/`valueStore.idb`/
  `Edge Nodes/` trees/Embedded blobs; ~900+ files expected.
- **D2 commit** on `main`, two commits: (1) tooling/infra, (2) Ignition config+projects baseline.
- **D3** `gh auth status` → `gh repo create icc-2026 --private --source . --push`; add teammates
  as collaborators.

## Phase E — verification (the real exit criteria)

- **E1 author round-trip**: `nuke` → delete untracked `local/`+`valueStore.idb` (simulate fresh
  clone) → `seed` takes the **clone-seed** path and leaves `git status` clean → `up` → `health`
  green → MQTT Engine/Transmission connected (referenced secrets resolving), datasource Valid,
  project visible (proves `.resources/` cache regeneration) → `git status` still clean.
- **E2 fresh-clone acid test**: main stack down → clone from GitHub to a scratch dir → copy
  .modl files in → `init` → set `COMPOSE_PROJECT_NAME=icc26test` in `.env` → `seed` → `up` →
  `health` green → Sparkplug publishes as `ICC26-Site1-USP/USP-EDGE-01` → `git status` clean.
  Cleanup: `down -v`, delete scratch, main stack back up.
- **E3 guardrail negatives**: in the scratch clone without .modl files, `seed` and `up` hard-fail
  non-zero with the MODULES.md pointer.

---

# Part 2 — Pattern build specs for other agents

**Deliverable: `docs/plans/00-conventions.md` + `docs/plans/01…08-*.md`** — self-contained briefs
written so a fresh agent session (or a teammate's agent) can be pointed at one file and execute
it. Each spec carries: objective + talk point, files to create (with sketches), Ignition
resources (exact paths where known, UI-then-commit procedure where schemas are unknown),
MQTT user + topics, empirical checkpoints, copy-pasteable verification, and "update
`docs/0N-*.md` talk-track doc" as the closing step.

## Cross-cutting conventions (docs/plans/00-conventions.md)

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
- **Verification harness**: `docker run --rm -it --network icc26_default eclipse-mosquitto:2
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v` (pull the image once, before
  the conference; observer ACL already covers `icc26/#`, `spBv1.0/#`, `$SYS/#`).
- Commit hygiene: B7 rule; never commit gitignored identity paths.

## The eight specs

**01 — Native MQTT (`services/sim-vibration`)** — no dependencies.
Paho app: 4 sensors (`vib-01…04`) on `pumpskid1`; telemetry (RMS, temp) every 5 s; subscribes
`.../+/cmd/collect`; on command synthesizes ~4096-sample waveform (bearing-defect harmonics +
noise) → publishes to `.../waveform` with the originating `correlation_id`; retained LWT
birth/death on `.../state`. User `vib-gateway` (ACL already fits). Ignition: MQTT Engine
**custom namespace** (UI-then-commit) → Document tags; Perspective page fires
`cmd/collect` (via Engine — cmd publish is in its ACL) and plots the waveform.
Verify: mosquitto_pub a collect cmd → waveform + ack; `docker stop icc26-sim-vibration` →
retained death on `state`.

**02 — Sparkplug B edge node** — no dependencies; no new container.
Programmable Device Simulator device (UI-then-commit; program CSV committed) feeding a
`Bioreactor` UDT (temp, pH, DO, agitation, level, OUR) instance `BR-201`; MQTT Transmission tag
tree + deadbands for report-by-exception (transmitter already fixed to
`ICC26-Site1-USP`/`USP-EDGE-01` in infra B2). Verify: `mosquitto_sub 'spBv1.0/#'` shows
NBIRTH/DBIRTH/DDATA; stopping the gateway produces NDEATH. Talk point: free birth/death vs
pattern 1's hand-rolled `state`.

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
envelope, `mechanism=cdc`, same sample-result topic (batch_event rows → `mes/batch/event`).
Named volume for offsets. Verify: psql `INSERT` on stage → topic message within ~1 s.

**06 — Poll / diff** — needs 04 + infra C3 datasource.
Two deliberate variants, each with its own watermark tag and an enable toggle (only one on at a
time): (a) gateway timer script polling `GET /results?since_id=N`; (b) SQL high-water-mark query
against datasource `ICC26`. Publish `mechanism=poll`, same topic. Verify: trigger a sample with
webhook+CDC disabled → message within one poll interval; show the id-vs-timestamp watermark
argument in the doc.

**07 — Scripted aggregation** — needs 04, 02, C3.
Gateway script (Perspective button + optional timer): joins `plant.batch` (SQL), LIMS
`GET /results/latest`, and live `BR-201` tag values into one composite document →
`transmission.publish` to `icc26/site1/usp/br-201/batch-summary`, `mechanism=aggregate`.
Verify: one message containing all three source sections.

**08 — Presentation + runbook** — last.
Perspective: overview page, one page per pattern (04/05/06 share a "LIMS result" page with
mechanism toggles — the set-piece control panel), and the **firehose** colored by
`meta.mechanism`. Firehose: primary = WebDev-served static HTML with **vendored** mqtt.js over
Chariot WS :8090 as `observer`, embedded in Perspective (offline-safe; riskiest UI piece);
fallback (build first) = Engine-subscribed wildcard → gateway script appends to a dataset tag →
Perspective table. `docs/demo-runbook.md`: T-15 dual-trial checklist, per-pattern trigger +
recovery, set-piece choreography (webhook off → CDC on → only `meta.mechanism` changes).

## Execution order

- **Wave 0**: Part 1 (infra) through E2 — nothing else starts until the fresh-clone test passes.
- **Wave 1** (parallelizable across agents/teammates): 01, 02, 03, and 04's stub service.
- **Wave 2**: 04's WebDev half → 05, 06, 07 (parallel after 04).
- **Wave 3**: 08.
- Each wave ends with: `tasks.py health` green, pattern verification passes, `git status` shows
  only intended files, push.

## Verification (whole effort)

1. Part 1 phases E1–E3 (round-trip, fresh clone, guardrail negatives).
2. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
3. End-to-end: all seven mechanisms firing → firehose shows seven colors; the set-piece
   rehearsed; run once with networking disabled to prove offline viability.
