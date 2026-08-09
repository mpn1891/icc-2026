# Status & handoff

> **Written 2026-08-09**, at the end of the session that executed Part 1 Phases A and B of
> [`00-master-plan.md`](00-master-plan.md). Conference is ~5 weeks out.
>
> **Resume at Part 1 Phase C.** Nothing is committed yet — the repo still has 0 commits.

## Read these first, in this order

1. [`00-master-plan.md`](00-master-plan.md) — the approved plan. Still the authority on *what*
   to build. Phases A and B are now done; treat their sections as history, not instructions.
2. This file — what actually happened, including findings that contradict the plan.
3. [`../00-architecture.md`](../00-architecture.md) — settled architectural truth (baked
   modules, two-pass seed, manual commissioning, Chariot trial API). Updated this session with
   the seed state machine and the config-reload reality.

Where this file and the master plan disagree, **this file is newer**.

---

## Where the work stands

### Done — Part 1 Phase A (`tasks.py` lifecycle + guardrails)

- **A1** `main()` propagates handler results (`False` → exit 1, else 0). `task_health`,
  `task_scan`, `task_trial` return real booleans. `up` exits non-zero when the stack comes up
  unhealthy — deliberate, so the Phase E tests can be scripted.
- **A2** `verify-modules` hard-gates `seed` and `up` with a `compose/ignition/MODULES.md`
  pointer. `init` stays warn-only (it is what you run *before* fetching the modules).
- **A3** Seed is now a state machine over two axes — does `<project>_ign-data` exist, and is
  `ignition/config` populated. Full seed / clone seed / already-seeded / half-initialized. New
  `task_export_identity()` copies only the three gitignored identity paths and never touches
  committed config. Helpers: `compose_project()`, `data_volume()`, `volume_exists()`.
- **A4** `up` gates on volume existence; the config-populated check is now secondary.
- **A5** README, `ignition/README.md` and `docs/00-architecture.md` describe the real
  clone → drop `.modl` → `init` → `seed` → `up` flow. All "directories are empty until seed"
  claims removed.

### Done — Part 1 Phase B (config/compose edits)

All seven items (B1–B7) landed. Notable specifics:

- `systemName` is `ICC26-Ignition`, with `hostname: icc26-ignition` pinned in **both** compose
  files so the entrypoint stops deriving it from the container ID.
- Sparkplug IDs are `ICC26-Site1-USP` / `USP-EDGE-01`.
- Chariot pinned to **3.0.1** (read from `/Chariot/version.properties` in the running
  container). `CHARIOT_MQTTS_PORT` defaults to 18883 in both `.env.example` and compose.
- Healthchecks added for ignition (`/StatusPing`) and chariot (root, **without** `curl -f` —
  it is an authenticated UI and a 401 is still a live broker). `curl` confirmed present in both
  images. Caveat recorded in-file: `/StatusPing` reports RUNNING during commissioning, so
  healthy ≠ commissioned.

### Not started

- **Part 1 Phase C** (empirical, gateway UI) — resume here.
- **Part 1 Phase D** (first commit + GitHub remote) and **Phase E** (round-trip, fresh-clone
  acid test, guardrail negatives).
- **All of Part 2** — the eight per-pattern spec docs and every pattern build.

---

## Resume here: Phase C

Phase C is UI-then-commit work against the running stack. **Never hand-author an unknown
gateway schema** — create it in the UI once, read the files `git status` reveals, commit those.

The master plan lists C1–C4. This session added a fifth. Suggested order:

1. **C1 — Secret Provider.** Gateway UI → create an environment-variable-backed provider named
   `env`. The four variables are already being passed into the container (`MQTT_ENGINE_PASSWORD`,
   `MQTT_TRANSMISSION_PASSWORD`, `MQTT_DISTRIBUTOR_PASSWORD`, `ICC26_DB_PASSWORD`) — see
   `docker-compose.yml`. If 8.3.8 has no env-var provider type, fall back to a file-based
   provider over a bind-mounted secrets file and record the deviation.
   Note `/usr/local/bin/ignition/ignition-secrets-tool.sh` exists in the image and was not
   investigated — it may be relevant.
2. **C2 — Convert Embedded secrets to Referenced.** Four files currently hold
   `"type": "Embedded"` JWE ciphertext, encrypted with a gitignored gateway-local key. **This
   is the single biggest blocker to sharing**: a teammate's clone gets ciphertext their gateway
   cannot decrypt, and MQTT auth fails silently while `tasks.py health` still prints green
   (health checks Chariot's listener, not Ignition's connection to it).
   - `.../com.cirruslink.mqtt.engine.gateway/server/Chariot SCADA/config.json` — also fix
     `url` from `tcp://localhost:1883` → `tcp://chariot:1883`, and try username `ign-engine`
     (currently `admin`). If the `ign-engine` ACL breaks Engine, keep `admin` and note it.
   - `.../com.cirruslink.mqtt.transmission.gateway/server/chariot_broker/config.json` —
     `password` **and** `rpcPassword`.
   - `.../com.cirruslink.mqtt.distributor.gateway/user/admin/config.json`.
   - `.../ignition/opc-connection/Ignition OPC UA Server/config.json` — **not in the master
     plan.** Decide whether to convert it; Pattern 3 uses OPC UA.

   Exit check: `grep -r '"type": "Embedded"' ignition/config` returns nothing, both modules
   show connected in the gateway UI and in Chariot's client list.
3. **C3 — Postgres JDBC datasource** `ICC26` → `jdbc:postgresql://postgres:5432/icc26`, user
   `icc26`, password as a Referenced secret (`ICC26_DB_PASSWORD`, already `icc26` in the init
   SQL). Verify Valid. Patterns 6/7 need it and the README already claims it exists.
4. **C5 (new) — Ignition 8.3 API key**, so `tasks.py scan` works. See the finding below.
   Gateway UI → Platform → Security → API Keys; put the value in `.env` as `IGNITION_API_TOKEN`
   (the plumbing is already written); over plain HTTP also disable *Require secure connections
   for API Keys*. Then confirm `python tasks.py scan` exits 0.
5. **C4 — Module hashes.** `python tasks.py hash-modules` → paste sha256s into
   `compose/ignition/modules.manifest.json` → `verify-modules` green.

After C, go to Phase D (commit + push), then run E1 and E2 back-to-back — one `nuke`, one
browser commissioning round-trip, and the clone test then proves A, B and C together.

---

## Findings that are NOT in the master plan

These were discovered empirically this session. They change how the tooling and the teammate
workflow behave.

### 1. `tasks.py scan` has never worked, and there is no file watcher

Two independent problems, both verified directly:

- `POST /data/api/v1/scan/{config,projects}` returns **401** to the admin password. Ignition
  8.3 guards those routes with an API key sent as `X-Ignition-API-Token`, not Basic auth, and
  refuses keys over plain HTTP unless *Require secure connections for API Keys* is disabled.
- **The gateway does not watch `data/config`.** Tested: edited `systemName` on disk on a
  running gateway, waited, got zero log activity and no effect until restart. Any workflow
  assuming a watcher is wrong.

Nobody caught this because `main()` used to return 0 unconditionally.

**Until C5 is done, the only way to apply a pulled config change is
`python tasks.py restart ignition`.** `tasks.py` already sends the token when
`IGNITION_API_TOKEN` is set, and otherwise fails with a 401 that explains itself. README,
`ignition/README.md` and `docs/00-architecture.md` have been corrected to say this.

### 2. `tasks.py trial` cannot read the Ignition clock

`GET /data/api/v1/license-status` returns **404** on 8.3.8 — wrong path for this build, not an
auth problem. The Chariot half works. So the Ignition trial clock is currently readable only
from the gateway UI, which is half your stage-time visibility. **Unresolved; needs its own
investigation.** `reset-trial` posts to the same path and is presumably broken the same way.

### 3. Deviations from the master plan as written

- **`POSTGRES_DEMO_PASSWORD` was deleted** from `.env.example`, which the plan did not list.
  `ICC26_DB_PASSWORD` now covers that secret; keeping both would recreate the two-sources-of-
  truth problem B4 exists to remove.
- **A third `Edge Nodes` directory exists** that the plan's two gitignore patterns do not
  cover: `.../tag-definition/MQTT Engine/Engine Info/Edge Nodes/`. It holds only a
  `unary-resource.json` folder definition with no per-node children, so it is **deliberately
  still tracked**. The patterns are anchored paths, which is what makes that work — verified
  with `git check-ignore -v`. Do not "fix" this by broadening them.
- `README.md:30`'s dead modules link was fixed during A5 rather than B6; B6 then cleaned up the
  remaining three.

### 4. Environment facts worth not rediscovering

- Chariot version lives at `/Chariot/version.properties` inside the container (currently 3.0.1).
  The image carries no version label and the tag was `latest`.
- `curl` exists in **both** the Ignition and Chariot images. Chariot also has `wget` and `nc`.
- `gwcmd.sh` has no config-reload/scan command. `ignition-util.sh` refuses to run standalone.
- Git Bash mangles container paths in `docker exec` (`/Chariot/...` becomes
  `C:/Program Files/Git/Chariot/...`). Prefix with `MSYS_NO_PATHCONV=1` and use `//Chariot/...`.
- `docker exec <ctr> test -e <path>` gives false negatives — there is no `test` binary. Use
  `sh -c '[ -e ... ]'`.

---

## Verification status — what is proven vs assumed

**Proven this session:**

- The A1–A4 state machine, via `tests/test_tasks.py` — 21 checks covering the four matrix
  cells, both module gates, both `up` gates and the exit-code plumbing. Stubs only the
  functions that shell out, so it needs no Docker and runs anywhere:
  `python tests/test_tasks.py`. Re-run it after touching `tasks.py`.
- Against the real environment: `seed` refuses correctly when already seeded (exit 1); `up`
  refuses with an unseeded project name without invoking compose (exit 1); clone-path selection
  resolves correctly for an unseeded project; `up` on the live stack exits 0 with health green;
  all three identity source paths exist in the container; both new healthchecks pass; the
  gitignore patterns match exactly the intended paths and nothing else.

**Assumed, not proven:**

- **The clone-seed happy path has only ever been stub-tested.** No real gateway has taken that
  branch. This is E1's job and it is the highest-risk unverified thing in Part 1.
- Whether Ignition 8.3 regenerates the three identity paths gracefully when absent. The export
  is cheap insurance either way; a missing source warns and continues.

---

## Repo state

- **0 commits, branch `main`, no remote.** 944 files would be added by a first commit.
- Audited clean: no `.env`, no `.modl`, no `local/`, no `valueStore.idb`, no edge-node churn
  trees. `.resources/` is correctly ignored — it caches Embedded ciphertext, so this matters.
- Still uncommittable-as-is: the four Embedded secret files (C2).

## Working rules

- **Commit only what you meant to change.** Every gateway write stamps `lastModification*` into
  neighbouring `resource.json` files. `git add` your files, then `git restore .` for the rest.
- **Never commit** `ignition/config/local/`, `ignition/config/resources/local/`,
  `valueStore.idb`, `.modl` files, or `.env`.
- **Unknown gateway schemas: UI first, then read `git status`, then commit.** Known formats
  (tags, project scripts, WebDev, Perspective views) can be authored as files.
- **Changed `.env` secrets need `python tasks.py restart ignition`**, not `scan` — env vars are
  read once at process start.

## Open questions for Matt

1. Convert the fourth Embedded secret (`Ignition OPC UA Server`) in C2, or leave it?
2. How hard to chase the `license-status` 404 — it is the Ignition trial clock, and the talk
   depends on knowing how much time is left on stage.
3. Keep `up` exiting non-zero when health fails? It is honest and makes E2 scriptable, but it
   means a Chariot trial lapse turns `up` red.
