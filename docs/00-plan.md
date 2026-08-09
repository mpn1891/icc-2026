# ICC 2026 Demo Environment — Data Transaction & Event Patterns

> **Scope.** Execution so far is **Step 1 only — the infrastructure / compose stack**: repo
> scaffolding, git layers, Postgres, Ignition, Chariot, and the seeding + task scripts. None of
> the pattern-specific work (steps 2–9) is built yet.
>
> **This document is the plan as approved, kept as a record of intent.** Several of its step-1
> details were proven wrong while building and are now superseded — read
> [`00-architecture.md`](00-architecture.md) for current truth. Specifically:
>
> | Plan said | Reality |
> |---|---|
> | Try the `/modules` bind mount + `GATEWAY_MODULE_RELINK` first | Both are 8.1-era; the 8.3 entrypoint handles neither. Modules must be **baked into a derived image** at `data/var/ignition/modl` — bind-mounting into the data volume stops Docker seeding it at all |
> | MQTT Distributor in a `fallback` compose profile | Distributor is an Ignition *module*, not a container. There are **no compose profiles** |
> | Separate `scripts/*.ps1` | Folded into one runner; three scripts would have triplicated the .env parsing and auth handling |
> | A PowerShell runner plus a `Makefile` mirroring it | Two implementations of the same knowledge, and the mirror **drifted** — it lost the module version check, the COMMISSIONING detection and the Chariot login wait. Now a single `tasks.py` with two-line forwarders |
> | Gateway is up when `/StatusPing` says `RUNNING` | It also says `RUNNING` while parked in **commissioning**. Any third-party module needs a one-time certificate acceptance in the browser |
> | Chariot just runs | Its **MQTT listener stays closed** until a trial is started via an undocumented API. The web UI answers regardless |
>
> Steps 2–9 below stand as written.

## Context

You are presenting at ICC 2026 on **data transaction and event patterns** — seven distinct
mechanisms by which data gets from a source system into an event-driven architecture. The talk
needs a live, reproducible environment that demonstrates each pattern end-to-end, that the team can
clone and run, and that survives a conference network and a stage.

`C:\Users\matt\repos\icc-2026` is currently **empty** — greenfield, no git repo yet.

Core stack: **Ignition 8.3.8** + **Cirrus Link MQTT modules 5.0.4** + **PostgreSQL 17**,
orchestrated by `docker compose`, everything git-committable.

### The seven patterns

| # | Pattern | Demo subject |
|---|---------|--------------|
| 1 | Native MQTT pub/sub | Vibration sensors behind a simulated local gateway; receive a `collect` command, publish the resulting waveform |
| 2 | Sparkplug B edge node | Bioreactor UDT in Ignition, publishing report-by-exception |
| 3 | OPC UA → MQTT | Nova Flex analyzer OPC UA server; Ignition monitors, publishes on sample-complete event |
| 4 | Webhook / Push API | Event-capable but non-MQTT system POSTing to Ignition |
| 5 | CDC / log tailing | Postgres WAL → Debezium Server → Ignition |
| 6 | Poll / diff on incrementing key | REST API and DB table polled on a high-water mark |
| 7 | Scripted multi-source aggregation | Gateway script joining Postgres + REST + tags into one composite publish |

---

## Decisions made

**Broker: Chariot MQTT Server, with MQTT Distributor installed but disabled as fallback.**
A standalone broker keeps the architecture honest — the talk's thesis is "seven mechanisms, one
event backbone," and if the broker lives inside Ignition then Ignition is simultaneously the
backbone and half the clients. Chariot is also genuinely compose-friendly: multi-arch, fully
env-var configured, `MQTT_USERS` reads a **JSON file path** (so ACLs are a committed artifact), and
port 8090 gives MQTT-over-WebSocket for a browser-based live topic firehose. Cost: a second
independent 2-hour trial timer — hence the Distributor fallback, and worth emailing Cirrus for a
Chariot demo key before the conference.

**No compose profiles, except one.** The stack is ~7 containers; during the talk everything is up
anyway because you move between patterns live. Profiles mainly introduce a failure mode
(forgetting `--profile` and debugging a "missing" service on stage). Dev iteration is served by
`docker compose up -d <service>` / `restart <service>`. Startup ordering is handled properly with
`depends_on` + healthchecks + `restart: unless-stopped`, not by profiles. The single exception is
`fallback`, holding MQTT Distributor, which genuinely should not run by default.

**LIMS is a placeholder with a defined contract, not a defined implementation.** May become
SENAITE, may stay a stub. Either way it must expose the same four surfaces (§ Pattern services),
so swapping it is a compose change rather than a redesign.

---

## Topic namespace

Organized by **ISA-95 physical hierarchy**, never by ingestion mechanism. A subscriber must not
need to know *how* data arrived in order to find it — if the LIMS migrates from polling to CDC,
nothing downstream should break.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

```
icc26/site1/utilities/pumpskid1/vib-01/telemetry     # 1  periodic RMS / temp
icc26/site1/utilities/pumpskid1/vib-01/cmd/collect   # 1  command in
icc26/site1/utilities/pumpskid1/vib-01/waveform      # 1  bulk response
icc26/site1/utilities/pumpskid1/vib-01/state         # 1  retained LWT birth/death
icc26/site1/qc/analyzers/novaflex-01/result          # 3
icc26/site1/qc/lims/sample-result                    # 4 AND 5 AND 6 — same topic
icc26/site1/mes/batch/event                          # 5
icc26/site1/usp/br-201/batch-summary                 # 7

spBv1.0/ICC26-Site1-USP/{NBIRTH|DBIRTH|DDATA}/USP-EDGE-01/BR-201   # 2 — spec-mandated
```

Areas: `usp` (upstream processing), `dsp`, `qc`, `utilities`, `mes`.

**Patterns 4, 5 and 6 deliberately share one topic.** Same logical data — a LIMS sample result —
acquired three completely different ways, landing identically. On stage you disable the webhook,
enable CDC, and the subscriber never notices. That demonstrates the architectural point far better
than three separate branches: *the mechanism is an implementation detail of the edge, not a
property of the data.*

Closed message-type vocabulary: `telemetry`, `event`, `waveform`, `state`, `cmd/<verb>`, `ack`.

**Every non-Sparkplug payload carries a consistent envelope:**

```json
{
  "ts": "2026-08-07T14:03:22.145Z",
  "seq": 1041,
  "source": { "id": "novaflex-01", "type": "analyzer" },
  "meta": { "mechanism": "cdc", "ingest_ts": "…", "correlation_id": "…" },
  "values": { }
}
```

`meta.mechanism` ∈ `native-mqtt | sparkplug | opcua-event | webhook | cdc | poll | aggregate`.
This single field drives the Perspective firehose view — you filter and color by pattern *there*,
keeping demo legibility without corrupting the namespace.

Two deliberate details that become talk content:

- **Every native-MQTT publisher sets a retained LWT on `.../state`**, hand-rolling what Sparkplug
  gives you for free. Direct contrast between patterns 1 and 2.
- **Chariot is MQTT 3.1.1**, so there are no MQTT 5 response-topic / correlation-data properties.
  Pattern 1's request/response is hand-built via `correlation_id` in the payload — a good honest
  moment about what MQTT is and isn't.

---

## Target architecture

```
                     ┌──────────────────────────────┐
   pattern 1  ──────▶│                              │
   pattern 3  ──────▶│   Chariot MQTT Server        │◀────── pattern 2 (Sparkplug B)
   pattern 6  ──────▶│   :1883 / :8090(ws) / :8080  │◀────── pattern 7 (scripted aggregate)
                     └──────────────┬───────────────┘
                                    │
                          MQTT Engine / Transmission
                                    │
   pattern 4 (webhook) ──▶ ┌────────┴─────────┐
   pattern 5 (CDC/HTTP) ─▶ │  Ignition 8.3.8  │──── JDBC ────▶ PostgreSQL 17
                           │  + Cirrus 5.0.4  │                (wal_level=logical)
                           └──────────────────┘                        │
                                                                  Debezium Server
```

| Service | Image / build | Purpose | Ports (host) |
|---|---|---|---|
| `postgres` | `postgres:17-alpine` | Ignition backing DB + demo schemas; `wal_level=logical` | 5432 |
| `ignition` | `inductiveautomation/ignition:8.3.8` | Gateway; Cirrus modules mounted | 8088, 8043 |
| `chariot` | `cirruslink/chariot:latest` | MQTT broker | 1883, 8883, 8090, 8081→8080 |
| `opcua-novaflex` | build: `services/opcua-novaflex` (Python `asyncua`) | Nova Flex analyzer OPC UA server | 4840 |
| `sim-vibration` | build: `services/sim-vibration` (Python `paho-mqtt`) | Simulated local gateway + vibration sensors | — |
| `lims` | **TBD** — stub now, possibly SENAITE | See contract below | 8000 |
| `debezium` | `quay.io/debezium/server:3.x` | Postgres logical decoding → HTTP sink | 8083 |

### LIMS contract (implementation TBD)

Whatever backs it must provide all four surfaces:

1. Emit a webhook POST on sample-complete → pattern 4
2. `GET /results?since_id=N` with a monotonic id → pattern 6
3. Write to a Postgres table Debezium can tail → pattern 5
4. Answer a query the aggregation script can join → pattern 7

Build against this contract. SENAITE (Plone + Postgres + REST) satisfies all four and carries real
credibility with a life-sciences audience; a ~100-line FastAPI stub satisfies them too and starts
in a second. Defer the choice — it does not block steps 1–4.

---

## Repository strategy — one repo, not several

**Single monorepo.** The alternative (separate repos for compose / Ignition config / each
simulator) costs more than it returns here:

- **The deliverable is "clone and run."** Multi-repo means submodules or a manifest tool, and a
  teammate's first experience becomes a `submodule init` failure instead of a working stack.
- **Every meaningful change is cross-cutting.** Changing the topic namespace touches Ignition
  config, all three simulators, Chariot ACLs, Debezium config, and the docs. That's one commit and
  one review in a monorepo; across four repos it's four PRs with an ordering dependency, and any
  revert or bisect becomes painful. With a fixed conference date, this is decisive.
- **Compose builds from the tree.** `build: ./services/sim-vibration` requires the source to be
  present. Splitting it out means publishing images to a registry — adding a CI pipeline and a
  network dependency to a demo whose main virtue is running entirely offline.
- **Lifetime doesn't justify the overhead.** Multi-repo separation pays off across years of
  independent release cadences. This repo's job largely ends when the talk does.

Note there is no "Ignition data" repo to consider — we commit only `data/config` and
`data/projects`. Everything else under `data/` (internal db, logs, `var`, `valueStore.idb`) is
runtime state and is gitignored, which is the actual fix for the "gateway writes noise into my
repo" problem that repo-splitting is usually reaching for.

You still get the separation benefits *within* the tree: `ignition/` is its own top-level
directory, so `git log -- ignition/` is clean, and `CODEOWNERS` can assign it separately from
`services/`. If a simulator later proves genuinely reusable across projects, extract it then and
consume it as a published image — that's a cheap change to make later and an expensive one to make
prematurely.

## Repository layout

```
icc-2026/
├── README.md                       # what this is, prerequisites, 3-command quickstart
├── .gitignore                      # Ignition 8.3 runtime state + secrets + .modl
├── .gitattributes                  # * text=auto eol=lf
├── .env.example                    # committed; .env is gitignored
├── docker-compose.yml              # all services, always-on
├── docker-compose.seed.yml         # step-1 only: named volume, no config bind mounts
├── tasks.py                        # the runner, all platforms (up/down/seed/scan/reset-trial)
├── Makefile                        # 2-line Linux/macOS forwarder to tasks.py
├── compose/
│   ├── ignition/
│   │   ├── modules/                # .modl files — GITIGNORED
│   │   │   └── README.md           # exact versions + where to download
│   │   └── modules.manifest.json   # name/version/sha256 so the team gets identical bits
│   ├── chariot/
│   │   └── mqtt-users.json         # per-pattern MQTT users + ACLs (committed)
│   ├── postgres/
│   │   ├── postgresql.conf.append  # wal_level=logical, replication slots
│   │   └── initdb/{01-databases,02-schema,03-seed}.sql
│   └── debezium/conf/application.properties
├── ignition/                       # ← config-as-code payload, bind-mounted
│   ├── config/                     # → /usr/local/bin/ignition/data/config
│   └── projects/                   # → /usr/local/bin/ignition/data/projects
├── services/
│   ├── sim-vibration/{Dockerfile,requirements.txt,app/}
│   ├── opcua-novaflex/{Dockerfile,requirements.txt,app/}
│   └── lims/                       # TBD
│                                   # (no scripts/ — all of it lives in tasks.py:
│                                   #  seed, scan, reset-trial, health)
└── docs/
    ├── 00-architecture.md          # incl. the namespace rationale above
    ├── 01-native-mqtt.md … 07-scripted-aggregation.md
    └── demo-runbook.md             # stage-day checklist, talk track, recovery steps
```

---

## Step 1 — Docker Compose + git layers

This has one non-obvious ordering constraint, in §1.4.

### 1.1 — Initialize the repo

`git init`; create `README.md`, `.env.example`, `tasks.py`, `tasks.cmd`, `Makefile`, `.gitattributes`
(`* text=auto eol=lf` — the team spans Windows and Linux and Ignition's config files must not
acquire CRLF).

`.gitignore`, derived from Inductive Automation's official 8.3 version-control guide since we
bind-mount live gateway directories into the repo:

```gitignore
# secrets / local
.env
*.gwbk
**/certificates/*
**/keystore/
ignition/config/local
ignition/config/resources/local

# Ignition 8.3 runtime state — must never be committed
**/db/*
**/metricsdb/*
**/autobackup/*
**/db_backup_sqlite.idb
**/valueStore.idb
**/jar-cache/*
**/var
**/logs
*.log
**/request*
**/response*
*.tmp
*.bak
**/.resources/
**/projects/conversion-report.txt
**/migration-log-*.md

# third-party modules (licensed binaries)
compose/ignition/modules/*.modl

# python
__pycache__/
*.pyc
.venv/
```

### 1.2 — Postgres

`postgres:17-alpine`, named volume for `/var/lib/postgresql/data`, `compose/postgres/initdb/`
mounted to `/docker-entrypoint-initdb.d/`. Command override setting `wal_level=logical`,
`max_replication_slots=4`, `max_wal_senders=4` — **do this now**; changing it later means dropping
the volume, and pattern 5 depends on it.

Schemas seeded up front so later steps have somewhere to land:
`lims.sample_result` (monotonic `id`, `created_at`) → patterns 4/5/6; `mes.batch_event` → pattern
5; `plant.equipment`, `plant.batch` → pattern 7.

### 1.3 — Cirrus module acquisition

`.modl` files are licensed binaries — don't commit them. Instead:
`compose/ignition/modules.manifest.json` pins filenames, version **5.0.4**, and sha256 for
Distributor / Engine / Transmission; `compose/ignition/MODULES.md` gives the download
location; `tasks.py verify-modules` checks local files against the manifest and fails with a
useful message, so a teammate who clones gets a clear error rather than a mysteriously broken
gateway. **All three versions must match exactly** — Cirrus documents class-loading instability
otherwise.

### 1.4 — Ignition, and the seeding order that matters

Ignition 8.3 stores **all** gateway configuration as human-readable files under `data/config` (DB
connections, device connections, tag providers, UDTs, alarm pipelines, MQTT module settings) and
`data/projects`. That is what makes this repo possible.

The catch: on a **fresh** data volume, Ignition seeds `data/` from the image at first launch. Bind-
mounting empty host directories over `data/config` and `data/projects` on that first launch blocks
seeding and the gateway comes up broken. So step 1 runs in two passes.

**Pass A — seed (`docker-compose.seed.yml`):**
```yaml
services:
  ignition:
    image: inductiveautomation/ignition:8.3.8
    environment:
      ACCEPT_IGNITION_EULA: "Y"
      IGNITION_EDITION: standard
      GATEWAY_ADMIN_USERNAME: admin
      GATEWAY_ADMIN_PASSWORD: ${IGNITION_ADMIN_PASSWORD}
      GATEWAY_MODULE_RELINK: "true"
      TZ: America/Chicago
    volumes:
      - ign-data:/usr/local/bin/ignition/data      # named volume, NO bind mounts
      - ./compose/ignition/modules:/modules:ro     # official image auto-links /modules
    ports: ["8088:8088", "8043:8043"]
```
Bring it up, confirm the three Cirrus modules load, then run
**`scripts/seed-ignition-config.ps1`** → `docker compose cp` `data/config` and `data/projects` out
to `./ignition/`. Commit that as the baseline.

**Pass B — normal operation (`docker-compose.yml`):** same service, now with
```yaml
    volumes:
      - ign-data:/usr/local/bin/ignition/data
      - ./ignition/config:/usr/local/bin/ignition/data/config
      - ./ignition/projects:/usr/local/bin/ignition/data/projects
      - ./compose/ignition/modules:/modules:ro
```
From here the loop is bidirectional: Gateway UI and Designer edits land as file diffs in the repo,
and `git pull` + `scripts/scan-config.ps1` (POST `/data/api/v1/scan/config` and
`/data/api/v1/scan/projects`) pushes teammates' changes into a running gateway without a restart.

**Module path caveat — resolve empirically here.** Sources conflict on where 8.3 wants third-party
modules: `/modules` (official image bind-mount convention, auto-linked), `data/local/modl` (IA 8.3
docs), `data/var/ignition/modl` (community reports). Try `/modules` + `GATEWAY_MODULE_RELINK=true`
first; if modules don't register, fall back to a thin derived image
(`compose/ignition/Dockerfile`) that `COPY`s the `.modl` files to whichever path works. Pin the
winner in `docs/00-architecture.md` so nobody re-litigates it.

### 1.5 — Chariot broker

`cirruslink/chariot:latest`, `ACCEPT_EULA=true`, `ADMIN_PASSWORD` from `.env`,
`MQTT_USERS=/config/mqtt-users.json` bind-mounted from `compose/chariot/mqtt-users.json`. Give each
pattern its own MQTT user with an ACL scoped to its branch of the namespace — costs nothing now and
makes a good slide.

Add MQTT Distributor to `modules.manifest.json` and to the `fallback` compose profile, disabled by
default.

### 1.6 — Windows host consideration (settle before building on it)

The repo lives at `C:\Users\matt\repos\icc-2026`. Docker Desktop bind-mounts from the Windows
filesystem into Linux containers over a translation layer that is slow and has UID/permission
quirks — and Ignition runs as UID 2003 and must *write* to `data/config`. Pick one:

1. **Clone inside WSL2** (`\\wsl$\Ubuntu\home\matt\icc-2026`) for the demo machine. Fast, correct
   permissions, no surprises. This is what I'd do for a machine you present from.
2. Stay on the Windows path and set `IGNITION_UID`/`IGNITION_GID` to match, accepting slower I/O.

`tasks.py` runs identically either way — and on macOS and native Linux for the rest of the team.

### 1.7 — Trial timer handling

`scripts/reset-trial.ps1` hits the gateway's license-status endpoint. Note the real constraint:
**the reset only succeeds once the trial has actually expired** — POSTing against an active trial
returns 403. So the runbook procedure is *let it expire, then reset*, not *top it up before you go
on*. `scripts/healthcheck.ps1` reports `trialSecondsLeft` so you can see the clock. Chariot has an
independent timer with its own reset; `docs/demo-runbook.md` gets a T-15-minute checklist covering
both.

### 1.8 — Step 1 exit criteria

`git clone` → add `.modl` files → `python tasks.py seed` → `python tasks.py up` yields: gateway on :8088
with all three Cirrus modules loaded; Chariot UI on :8081 with a working MQTT connection on :1883;
Postgres reachable with demo schemas and `wal_level=logical` confirmed; `git status` clean after a
full up/down cycle. Commit.

---

## Steps 2–9 — The patterns

Each pattern gets its services, its Ignition config committed as files, a Perspective view, and a
`docs/0N-*.md` carrying the talk track plus the honest "when would you actually use this, and what
does it cost you" framing.

**Step 2 — Pattern 1, native MQTT pub/sub.** `services/sim-vibration`: one process modeling a local
gateway fronting 4 accelerometers on `pumpskid1`. Publishes low-rate RMS/temperature telemetry
continuously; subscribes to `.../cmd/collect`; on command synthesizes a realistic waveform
(bearing-defect harmonics + noise, ~4096 samples) and publishes it with the originating
`correlation_id`. Retained LWT on `.../state`. Ignition ingests via **MQTT Engine Custom Namespace**
into a Document tag; a Perspective view fires the command and plots the returned waveform. Your best
visual — command out, chart appears.

**Step 3 — Pattern 2, Sparkplug B edge node.** Bioreactor UDT (temp, pH, DO, agitation, level, OUR)
in Ignition. Data source is Ignition's built-in **Programmable Device Simulator**, whose program is
a committed CSV — no extra container, entirely config-as-code. MQTT Transmission publishes
NBIRTH/DBIRTH/DDATA report-by-exception. Show the birth certificate and deadband behavior on the
broker, and contrast the free birth/death against the hand-rolled `state` topic from step 2.

**Step 4 — Pattern 3, OPC UA → MQTT.** `services/opcua-novaflex`: `asyncua` server exposing glucose,
lactate, glutamine, glutamate, ammonia, pH, osmolality, plus a `SampleCompleteCounter`. Ignition
connects as OPC UA client; a tag-change script on the counter assembles the full result set into one
payload and publishes it as a discrete event. Teaching point: **event on completion**, not polling
every value on change.

**Step 5 — Pattern 4, webhook / push API.** Ignition **WebDev** endpoint receives POSTs from the
LIMS service (shared-secret header, idempotency key, correct 2xx/4xx semantics), payload → tags →
`icc26/site1/qc/lims/sample-result`. Cover the honest tradeoffs: no delivery guarantee without
retry, no replay, receiver must be reachable.

**Step 6 — Pattern 5, CDC.** Debezium Server against Postgres logical decoding (`pgoutput`) watching
`mes.batch_event` and `lims.sample_result`. **HTTP sink → a second WebDev endpoint** — Debezium
Server has no first-party MQTT sink, so HTTP is the right bridge, and it conveniently reuses the
pattern-4 receiver *shape* while being a completely different *source* mechanism. Publishes to the
same LIMS topic as pattern 4. Demo: `INSERT` in psql on stage → tag updates within a second, with
nobody having modified the source application.

**Step 7 — Pattern 6, poll / diff.** Two variants, deliberately: a gateway timer script polling the
LIMS `GET /results?since_id=N`, and a SQL high-water-mark query against `lims.sample_result`.
Watermark persisted in a tag. Publishes to the same topic again. This is where you show the failure
modes the other patterns avoid — latency floor, updates missed between polls, clock skew on
timestamp watermarks, and why an id watermark beats a timestamp one.

**Step 8 — Pattern 7, scripted aggregation.** Gateway script joining Postgres (batch record), the
LIMS REST API (latest results), and live tag values into one composite batch-summary document,
published via `system.cirruslink.transmission` custom publish to
`icc26/site1/usp/br-201/batch-summary`. The point: sometimes the event *is* the aggregation, and no
single upstream source knows enough to emit it.

**Step 9 — Presentation layer & runbook.** One Perspective "ICC 2026" project: an architecture
overview page, one page per pattern, and a **live topic firehose** filtered/colored by
`meta.mechanism` (Chariot's WebSocket port makes this straightforward). Then
`docs/demo-runbook.md`: pre-flight checklist, per-pattern talk track, and a recovery step for each
plausible on-stage failure.

**The set-piece to rehearse:** with the firehose on screen, disable the pattern-4 webhook and enable
pattern 5 CDC. Same topic, same payload shape, only `meta.mechanism` changes. Nothing downstream
notices. That's the talk in ten seconds.

---

## Key risks

| Risk | Mitigation |
|---|---|
| Two independent 2-hour trial timers (Ignition + Chariot) | Runbook T-15 checklist; ask Cirrus for a Chariot demo key; Distributor fallback profile |
| Trial reset only works *after* expiry (403 while active) | Healthcheck surfaces `trialSecondsLeft`; runbook plans around it rather than assuming top-up |
| 8.3 third-party module path genuinely ambiguous across sources | Resolve empirically in §1.4, pin the answer in docs, Dockerfile-COPY fallback ready |
| Windows bind-mount permissions/perf vs Ignition UID 2003 | Recommend WSL2 clone for the demo machine (§1.6) |
| LIMS implementation undecided | Build to the four-surface contract; swap is a compose change. Does not block steps 1–4 |
| Config-as-code drift between teammates | `scan-config` script + documented "gateway UI edits are commits" workflow |
| Conference network | Everything local; nothing depends on internet at runtime. Verify by running with networking disabled |

---

## Verification

**After step 1:**
- `docker compose ps` — all services healthy
- `http://localhost:8088` — gateway up, Distributor/Engine/Transmission listed under modules
- `http://localhost:8081` — Chariot UI up
- `docker compose exec postgres psql -c "SHOW wal_level;"` → `logical`
- `git status` clean after a full up/down cycle — proves `.gitignore` covers Ignition runtime state
- **Fresh-clone test in a clean directory** — the real test of step 1

**Per pattern (2–8):** each `docs/0N-*.md` ends with a copy-pasteable verification — the command to
trigger it, a `mosquitto_sub` one-liner for the topic, and the expected payload shape.

**End-to-end:** `scripts/healthcheck.ps1` subscribes across `icc26/site1/#` and `spBv1.0/#`,
triggers each pattern, and reports pass/fail per pattern plus both trial clocks. First item on the
stage-day checklist.
