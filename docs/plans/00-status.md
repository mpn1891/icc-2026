# Status & handoff

> **Rewritten 2026-08-09**, at the end of the session that cut Phase C down and executed
> Phase D. Conference is ~5 weeks out.
>
> **Resume with the clone test:** [`00-clone-test.md`](00-clone-test.md). It is written to be
> handed to a fresh session with no other context.

## Read these first, in this order

1. This file — where things actually stand.
2. [`00-clone-test.md`](00-clone-test.md) — the next task, self-contained.
3. [`00-master-plan.md`](00-master-plan.md) — the approved plan, still the authority on Part 2.
   **Phases A, B and D are done; Phase C was cut down and no longer matches what is written
   there.** Treat those sections as history.
4. [`../00-architecture.md`](../00-architecture.md) — settled architectural truth.

Where this file and the master plan disagree, **this file is newer**.

---

## Where the work stands

### Done — Part 1 Phase A (`tasks.py` lifecycle + guardrails)

- **A1** `main()` propagates handler results (`False` → exit 1, else 0). `up` exits non-zero
  when the stack comes up unhealthy — deliberate, so Phase E can be scripted.
- **A2** `verify-modules` hard-gates `seed` and `up`. `init` stays warn-only.
- **A3** Seed is a state machine over two axes — does `<project>_ign-data` exist, and is
  `ignition/config` populated. `task_export_identity()` copies only the gitignored identity
  paths.
- **A4** `up` gates on volume existence.
- **A5** README and architecture docs describe the real clone → `.modl` → `init` → `seed` → `up`
  flow.

### Done — Part 1 Phase B (config/compose edits)

All seven items landed. `systemName` is `ICC26-Ignition` with `hostname: icc26-ignition` pinned
in both compose files. Sparkplug IDs are `ICC26-Site1-USP` / `USP-EDGE-01`. Chariot pinned to
3.0.1. Healthchecks on ignition (`/StatusPing`) and chariot (root, no `curl -f`).

### Phase C — cut down, mostly not done

**C1 (Secret Provider) and C2 (Embedded → Referenced) were cut.** See finding 3 below for why.
Nothing was built for either, and the four Cirrus/OPC config files still hold `Embedded`
ciphertext. That is now the intended state, not a blocker.

Consequences to clean up when convenient:

- `docker-compose.yml` passes `MQTT_ENGINE_PASSWORD`, `MQTT_TRANSMISSION_PASSWORD`,
  `MQTT_DISTRIBUTOR_PASSWORD` and `ICC26_DB_PASSWORD` into the Ignition container for a Secret
  Provider that will never exist. **Dead weight — remove them.** `.env.example` documents them
  in the same terms and needs the same treatment.

Still outstanding:

- **C3** Postgres JDBC datasource `ICC26` → `jdbc:postgresql://postgres:5432/icc26`, user
  `icc26`. Password can just be Embedded now. Patterns 6/7 need it and the README already
  claims it exists.
- **C4** `python tasks.py hash-modules` → paste sha256s into `modules.manifest.json`. Not
  urgent: `verify-modules` skips the hash check when the field is blank (`tasks.py:439`).
- **C5** Ignition 8.3 API key, so `scan` and `reset-trial` work. Gateway UI → Platform →
  Security → API Keys → `IGNITION_API_TOKEN` in `.env`; over plain HTTP also disable *Require
  secure connections for API Keys*.

**MQTT Engine was fixed by hand this session and now connects.** `url` is `tcp://chariot:1883`,
the password block is gone, and `username` is `""` — so it connects anonymously. That works
only because `allowAnonymous` is currently true; see finding 5.

### Done — Part 1 Phase D (first commit + remote)

Five commits on `main`, pushed to <https://github.com/mpn1891/icc-2026>. 946 tracked files.
Split as planned: tooling/infra first, Ignition config+projects second.

Not done: **teammates have not been added as collaborators**, and repo visibility was never
confirmed. `gh` is not installed on this machine, so D3 was completed by hand.

### Next — Part 1 Phase E

[`00-clone-test.md`](00-clone-test.md). E2 + E3, plus the part of E1 that can be proven without
destroying the working stack. **E1 proper is deliberately deferred** — do not `nuke` the main
checkout to test something the clone test already answers more realistically.

---

## Findings that are NOT in the master plan

### 1. `tasks.py scan` has never worked, and there is no file watcher

Unchanged from the previous session, still true:

- `POST /data/api/v1/scan/{config,projects}` returns **401** to the admin password. It needs an
  API key as `X-Ignition-API-Token` (C5).
- **The gateway does not watch `data/config`.** Verified: edited `systemName` on disk on a
  running gateway, zero effect until restart.

**Until C5 is done, apply a pulled config change with `python tasks.py restart ignition`.**

### 2. The trial route — solved

`GET /data/api/v1/license-status` returns 404 because **it is the wrong path**, not because of
auth. The real routes, read out of `LicensingRoutes` in the gateway jar and confirmed live:

- `GET /data/api/v1/trial` — **works on plain basic auth**, returns `licenseMode`, `trialState`,
  `trialSecondsLeft`, `expired`.
- `POST /data/api/v1/trial` — resets. Requires WRITE, so it needs C5's API key.

**`tasks.py` has not been fixed yet.** `task_trial()` and `task_reset_trial()` still point at
`license-status`. This is a small, self-contained fix and it restores half your stage-time
visibility with no API key needed.

### 3. There is no environment-variable Secret Provider in 8.3.8 — and C1/C2 were cut

Ignition 8.3.8 ships exactly three provider types: `internal`, `file` (8.3.5+), `remote`
(8.3.3+). There is **no env-var provider**. The plan assumed one existed.

The fallback would have been the `file` provider over a bind-mounted secrets directory. It was
cut instead, on this basis: **this gateway has no encryption key files at all.** There is no
`data/config/ignition/keys`, no `root.json`, no `kek.json` anywhere on the filesystem. Reading
`SystemEncryptionServiceFactory`, that is what happens when `IGNITION_ROOT_KEY_PASSWORD` is
unset — the gateway falls back to `DefaultSystemEncryptionService`, whose key is built into the
jar rather than generated per machine.

If that holds, committed `Embedded` ciphertext decrypts on any 8.3.8 gateway that also has no
root key password, and C2 was solving a problem that does not exist.

> **This is inferred from bytecode, not proven.** Checkpoint 5 of the clone test is the verdict.
> If it fails, C1 and C2 come back onto the plan — and the failure mode is silent, because
> `health` stays green while Ignition's MQTT auth fails.

### 4. `allowAnonymous` is now `true`, deliberately and temporarily

Set for the initial rollout so the team can develop without credential friction. The six ACL'd
accounts in `mqtt-users.json` are still seeded and still work.

Deleting the users instead was considered and rejected: `MQTT_USERS` applies on first run only,
so re-adding them later would mean a `nuke` or hand-building six ACLs in the Chariot UI.
Flipping the boolean back is one line plus `restart chariot`. `compose/chariot/README.md` has a
"before the talk" reminder.

### 5. Chariot still validates credentials that ARE supplied

Anonymous access only helps clients that supply *no* credentials. Demonstrated the hard way:
MQTT Engine, left on `username: admin` with no password, was rejected every 3 seconds —

```
CONNECT - Bad username and/or password. username true:admin, password false:*****
```

— and looped like that behind a green `health` check. Fixed this session by setting `username`
to `""`, at which point it connected and stayed connected.

Two things follow. **Anonymous did not paper over a bad credential, which is good news** — it
means the clone test's checkpoint 5 is still a valid test of the secrets question. And **Engine
is now riding on anonymous access**, so it is on the "before the talk" list: when
`allowAnonymous` goes back to `false`, Engine needs `ign-engine` and its password, or it starts
looping again. `compose/chariot/README.md` carries that reminder.

### 6. MQTT Transmission connects, but fails to subscribe

Transmission reaches Chariot fine as `ign-transmission` at `tcp://chariot:1883` — which also
confirms its `Embedded` password is valid on the gateway that encrypted it. But immediately
after:

```
E [c.c.m.t.g.TransmissionClient] Failed to subscribe to TARGET elements
```

Unexplained. Possibly the `ign-transmission` ACL, possibly transmitter config. **Not yet
investigated.**

### 7. `export-config` was a footgun and is gone from the CLI

It `_rmtree`s `ignition/config` and `ignition/projects` before copying, and the normal stack
bind-mounts both — so `python tasks.py export-config` deleted the files it was about to copy.
The function survives because `seed` calls it legitimately against the seed container, which
runs without those mounts; it now defaults to `icc26-ignition-seed`.

`tasks.cmd` was also removed — `python tasks.py` works everywhere.

### 8. Two checkouts cannot run at the same time

`container_name` is pinned in `docker-compose.yml`, the network is pinned (`name: icc26`), and
host ports come from `.env`. A second stack collides on all three regardless of
`COMPOSE_PROJECT_NAME`. The project name separates **volumes only** — which is still exactly
why you set it in a scratch clone.

### 9. Environment facts worth not rediscovering

- Chariot version lives at `/Chariot/version.properties` (3.0.1). No image label.
- `curl` exists in both images. Chariot also has `wget` and `nc`.
- The Ignition image ships a **JRE, not a JDK** — no `javac`, no `jshell`. `java Foo.java` fails
  with "Module jdk.compiler not in boot Layer".
- `ignition-secrets-tool.sh` only manages root/KEK keys. It cannot encrypt or decrypt a value.
- Git Bash mangles container paths in `docker exec` (`/Chariot/...` →
  `C:/Program Files/Git/Chariot/...`). Use `MSYS_NO_PATHCONV=1` and `//Chariot/...`, or
  PowerShell.
- `docker exec <ctr> test -e <path>` gives false negatives — no `test` binary. Use
  `sh -c '[ -e ... ]'`.

---

## Verification status — what is proven vs assumed

**Proven:**

- The A1–A4 state machine, via `tests/test_tasks.py` — 21 checks, no Docker needed:
  `python tests/test_tasks.py`. Re-run after touching `tasks.py`. Still passing.
- `GET /data/api/v1/trial` returns 200 on basic auth; `POST` returns 401.
- Chariot accepts anonymous connections and still rejects bad supplied credentials.
- MQTT Transmission connects to Chariot; MQTT Engine does not.
- The first-commit audit: 946 files, no `.env`, no `.modl`, no `local/`, no `valueStore.idb`,
  no `.resources/`.

**Assumed, not proven:**

- **`Embedded` ciphertext is portable between gateways.** The entire basis for cutting C1/C2.
  Inferred from bytecode. Clone test checkpoint 5.
- **The clone-seed happy path.** Stub-tested only; no real gateway has taken that branch. Clone
  test checkpoint 1, and the highest-risk unverified thing in Part 1.
- Whether Ignition 8.3 regenerates the three identity paths gracefully when absent.

---

## Repo state

- **5 commits on `main`**, pushed to <https://github.com/mpn1891/icc-2026>. 946 tracked files.
- Working tree clean as of this rewrite.
- The four `Embedded` secret files are committed **on purpose** now — that is the C1/C2 cut.

## Working rules

- **Commit only what you meant to change.** Every gateway write stamps `lastModification*` into
  neighbouring `resource.json` files. `git add` your files, then `git restore .` for the rest.
- **Never commit** `ignition/config/local/`, `ignition/config/resources/local/`,
  `valueStore.idb`, `.modl` files, or `.env`.
- **Unknown gateway schemas: UI first, then read `git status`, then commit.** Known formats
  (tags, project scripts, WebDev, Perspective views) can be authored as files.
- **Changed `.env` secrets need `python tasks.py restart ignition`**, not `scan`.
- **`health` green does not mean MQTT works.** It checks Chariot's listener, not Ignition's
  client connection to it. Engine has been failing behind a green `health` all day.

## Open questions for Matt

1. **Transmission's "Failed to subscribe to TARGET elements"** — how hard to chase before the
   pattern work starts. It connects, so it may not block anything yet.
2. **Keep `up` exiting non-zero when health fails?** Still open from last session. Honest and
   makes E2 scriptable, but a trial lapse turns `up` red.
3. **The OPC UA connection** (`ignition/opc-connection/Ignition OPC UA Server/config.json`)
   holds two `Embedded` secrets, one of them paired with a keystore in gitignored `local/`. With
   C2 cut it stays as-is, but if the clone test shows the loopback OPC UA connection faulted on
   the clone, it needs its own decision. Pattern 3 uses OPC UA.
