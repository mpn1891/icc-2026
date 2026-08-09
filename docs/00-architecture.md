# Architecture

The decisions in this stack that are not obvious from reading the compose file, and the
reasoning behind them. `00-plan.md` holds the full build plan; this is the reference for
things you will need to look up again later.

---

## The stack

```
                     ┌──────────────────────────────┐
   pattern 1  ──────▶│                              │
   pattern 3  ──────▶│   Chariot MQTT Server        │◀────── pattern 2 (Sparkplug B)
   pattern 6  ──────▶│   :1883 / :8090(ws) / :8081  │◀────── pattern 7 (scripted aggregate)
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

| Service | Host ports | Built in |
|---|---|---|
| `postgres` | 5432 | step 1 |
| `ignition` | 8088, 8043 | step 1 |
| `chariot` | 1883, 8883, 8090, 8081, 8444 | step 1 |
| `sim-vibration` | — | step 2 |
| `opcua-novaflex` | 4840 | step 4 |
| `lims` | 8000 | step 5 (implementation TBD) |
| `debezium` | 8083 | step 6 |

---

## Topic namespace

Organized by **ISA-95 physical hierarchy, never by ingestion mechanism.** A subscriber must
not have to know *how* data arrived in order to find it. If the LIMS migrates from polling
to CDC, nothing downstream should break.

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
Message types are a closed set: `telemetry`, `event`, `waveform`, `state`, `cmd/<verb>`, `ack`.

Equipment ids in `plant.equipment` (see `compose/postgres/initdb/03-seed.sql`) are the same
strings that appear in topics. Keep it that way.

### Patterns 4, 5 and 6 deliberately share one topic

Same logical data — a LIMS sample result — acquired three completely different ways, landing
identically. On stage you disable the webhook, enable CDC, and the subscriber never notices.

This demonstrates the architectural point better than three separate branches would: *the
mechanism is an implementation detail of the edge, not a property of the data.* It is also
why `lims-bridge` in `compose/chariot/mqtt-users.json` is scoped to exactly one sample-result
topic — the ACL enforces the convergence rather than trusting everyone to remember it.

### Payload envelope

Every non-Sparkplug payload:

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

This field carries the demo. The Perspective firehose view filters and colors by it, so you
get per-pattern legibility on screen without encoding the mechanism into the namespace.

### Two things that become talk content

**Native-MQTT publishers set a retained LWT on `.../state`,** hand-rolling what Sparkplug
gives you for free. That is the direct contrast between patterns 1 and 2.

**Chariot is MQTT 3.1.1**, so there are no MQTT 5 response-topic or correlation-data
properties. Pattern 1's request/response is hand-built via `correlation_id` in the payload —
an honest moment about what MQTT is and is not.

---

## Broker: why Chariot and not Distributor

The talk's thesis is "seven mechanisms, one event backbone." If the broker lives inside
Ignition, the architecture stops matching the slide — Ignition becomes both the backbone and
half the clients, and you cannot restart it on stage without taking the demo down.

Chariot is also better suited to a committed repo: fully env-var configured, `MQTT_USERS`
reads a JSON file so ACLs are a diffable artifact, and port 8090 gives MQTT-over-WebSocket
for a browser-based firehose.

**The cost is a second, independent 2-hour trial timer.** MQTT Distributor is therefore still
in `modules.manifest.json` as break-glass: if Chariot's trial bites mid-talk, enable the
Distributor module in the gateway and repoint Engine/Transmission at `localhost:1883`.
Distributor is a *module*, not a container, so there is nothing to switch in compose — which
is also why this stack has no compose profiles at all.

A Chariot demo key from Cirrus Link removes the whole problem. Worth requesting before the
conference.

### Chariot will not open its MQTT listener without a trial

This one costs you the whole demo if you hit it cold. Chariot 3.0.1 serves its **web UI on
8081 while port 1883 refuses every connection**, which looks exactly like a broken network or
a wrong port. It is neither. The log line is:

```
WARN  c.c.chariot.server.impl.Server - Not starting Chariot MQTT Server, license not active
```

Unlike Ignition, Chariot's trial does **not** start automatically in the container, and
`LICENSE_TYPE` only accepts `online` or `floating` — there is no trial value. The trial is
started by an undocumented endpoint, found by reading the Chariot UI bundle:

```
POST /license?action=start-trial-timer
```

`tasks.py up` calls it automatically, and `chariot-trial` does it on demand. Auth is a bearer
token from `POST /login` (Basic auth is rejected), so the calls run via `docker exec` against
the container's own loopback using the curl it already ships.

Two practical notes:

- **Chariot seeds its admin user asynchronously**, well after the web port starts answering.
  Poll the API, not the port — `wait_for_chariot()` does.
- The `Accept: application/json;api-version=1.0` header contains a `;`. Any shell in the call
  path will eat it, and the header arrives as a bare `Accept: application/json`; Chariot then
  rejects the request with *"'api-version' not specified"*. `_chariot_curl()` hands the
  argument list to `docker exec` directly, with no shell anywhere in between, which is why the
  problem no longer exists. If you extend it, do not reintroduce a `sh -c`.

`tasks.py health` checks the **listener**, not the web port, because the web port answering
proves nothing.

---

## Seeding: why the first boot is different

**Ignition 8.3 seeds `data/` from the image on first launch of an empty volume.** If empty
host directories are bind-mounted over `data/config` and `data/projects` at that moment, the
seeding is blocked and the gateway comes up broken.

So the first run uses `docker-compose.seed.yml`, which boots the gateway on the `ign-data`
named volume with **no config bind mounts**. Once it reports RUNNING, `tasks.py` copies out of
the seed container and stops it. From then on `docker-compose.yml` mounts `./ignition/` back in
over the same, now-initialized volume.

```bash
git clone <repo> && cd icc-2026
# .modl files into compose/ignition/modules/
python tasks.py init     # .env
python tasks.py seed     # once per machine; pauses for browser commissioning
python tasks.py up
```

### Two seeds, one command

Once the config is committed, "has this machine been seeded?" and "is `ignition/config`
populated?" stop being the same question — a fresh clone has populated config and no volume.
Deciding from config alone breaks both ways: a teammate's `up` proceeds against a volume that
was never initialized, or `seed` overwrites their committed config with the vanilla baseline.

So `seed` reads both axes — does `<project>_ign-data` exist (`docker volume inspect`), and is
`ignition/config` populated:

| Volume | `ignition/config` | What happens |
|---|---|---|
| absent | empty | **Full seed.** The original case: export the whole baseline to `./ignition/` |
| absent | populated | **Clone seed.** Export *only* the gitignored identity paths; committed config is never touched |
| exists | populated | Refuses — already seeded, use `nuke` to rebuild |
| exists | empty | Refuses — half-initialized, a previous export did not finish; use `nuke` |

The clone seed copies three paths out of the seed container, all of them gitignored and all of
them regenerated per machine: `config/local/`, `config/resources/local/`, and
`config/ignition/tags/valueStore.idb`. A missing source warns and continues rather than failing
the seed. The success criterion for the clone path is that `git status` is **clean** afterwards.

`up` gates on the volume for the same reason, and both `seed` and `up` hard-fail (non-zero) if
the Cirrus modules are missing or the wrong version. `init` only warns, because it is what you
run *before* fetching them.

You need to repeat `seed` only after `tasks.py nuke`. Note that `nuke` destroys volumes but
never touches your committed `ignition/config` and `ignition/projects` — which is precisely why
the post-`nuke` re-seed takes the clone path.

### Keeping gateway and repo in sync

Bidirectional, and the second direction is the one people forget:

- **Designer / Gateway UI edit** → gateway writes files → shows up in `git status`.
- **`git pull`** → gateway does *not* notice → `python tasks.py restart ignition`.

The gateway reads `data/config` at startup and does **not** watch it. This was tested directly:
editing `systemName` on disk on a running gateway produced no log activity and no effect until
a restart. Any workflow that assumes a file watcher is wrong.

`python tasks.py scan` POSTs to `/data/api/v1/scan/config` and `/data/api/v1/scan/projects` and
avoids the restart, but 8.3 changed the auth on those routes: they take an API key
(Platform > Security > API Keys) in an `X-Ignition-API-Token` header, not the admin password,
and reject keys over plain HTTP unless *Require secure connections for API Keys* is disabled.
`tasks.py` sends the key when `IGNITION_API_TOKEN` is set in `.env` and otherwise fails with a
401 that says all of this. **Creating that key is still an open task** — until it is done,
restart is the only way to apply a pulled change.

If a pulled change "didn't take", apply it before debugging anything else.

---

## Module path — resolved

The plan flagged this as ambiguous across sources. It is now settled empirically.

**The answer is `data/var/ignition/modl`**, read straight out of the 8.3 entrypoint:

```
-Dignition.gateway.externalModulesFolder=data/var/ignition/modl
```

The other two candidates are wrong for 8.3. `/modules` and `GATEWAY_MODULE_RELINK` are
8.1-era — the 8.3 entrypoint contains no handling for either, and modules placed in
`/modules` are silently ignored. `data/local/modl` appears in the 8.3 docs but is not what
the image actually sets.

**Modules are baked into a derived image** (`compose/ignition/Dockerfile`), not bind-mounted.
Three findings forced this, each verified by breaking it:

1. **Modules are only discovered on the first launch of a fresh data volume.** Dropping a
   `.modl` in later and restarting does nothing.
2. **You cannot bind-mount into the data volume.** Docker seeds a named volume from the image
   only when the volume is *empty*. Mounting anything at `data/var/ignition/modl` makes it
   non-empty before seeding, so `data/config` and `data/projects` are never created and the
   gateway comes up with no configuration whatsoever. This is the same failure mode as
   bind-mounting `config`/`projects` on first launch, one level deeper.
3. **Do not create `data_clean/`.** The entrypoint treats its existence as "the payload lives
   here", copies only its contents into `data/`, then deletes it — silently replacing the
   real configuration with whatever you staged.

Consequence: adding or upgrading a module needs `tasks.py nuke` then `seed`. A rebuild alone
is not enough, because an existing volume is never re-seeded.

**All three Cirrus module versions must match exactly** (5.0.4). Cirrus documents
class-loading instability and gateway crashes otherwise. Note the 8.1-era 4.x downloads have
**identical filenames** to the 5.x ones, so `tasks.py verify-modules` reads `<version>` out
of each `.modl`'s `module.xml` rather than trusting the filename.

## Commissioning

The presence of **any** third-party module makes the gateway halt in commissioning on first
launch, waiting for a human to accept the module certificate in the browser.

The trap: it answers `/StatusPing` with `{"state":"RUNNING","details":"COMMISSIONING"}`. A
health check that greps for `RUNNING` calls that healthy while the gateway is serving only
the setup wizard and has created neither `data/config` content nor `data/projects`. Both
`wait_for_gateway()` and `tasks.py health` distinguish the two states for exactly this reason.

Pre-seeding `data/modules.json` with correct certificate fingerprints **does not** bypass it.
That was built and tested — including validating the fingerprint derivation by reproducing
Inductive Automation's own `88338069eb9c3f2d46a4baf701e4fa71bf073293` from their `.modl` —
and the gateway still demanded commissioning. The code was removed rather than kept as dead
complexity. A second finding from that experiment is worth remembering: **`modules.json` is
authoritative, not additive.** If the file exists, the gateway does not merge its built-ins
in, so a partial file silently disables every built-in module.

So commissioning stays a one-time manual step per fresh volume. `tasks.py seed` detects it,
prints the URL, and waits.

---

## Postgres

`wal_level=logical` is set in the compose `command:` override rather than left to pattern 5.
It is a server start parameter, so changing it later means dropping the data volume — cheap
to set on day one, expensive to discover late.

`compose/postgres/initdb/` runs **only** on an empty volume. Editing those files against an
existing volume changes nothing; you need `tasks.py nuke` first.

Three roles, deliberately separate:

| Role | Purpose |
|---|---|
| `ignition` | Gateway's JDBC target — historian, audit log |
| `icc26` | Demo data (`lims`, `mes`, `plant` schemas) |
| `cdc` | Debezium's login, has `REPLICATION` |

`cdc` being distinct from the application user is part of pattern 5's point: CDC is an
out-of-band observer the application knows nothing about.

`lims.sample_result` and `mes.batch_event` are set to `REPLICA IDENTITY FULL` so Debezium
receives complete row pre-images on UPDATE and DELETE. It costs WAL volume — fine for two
demo tables, not something to enable blindly across a real database.

---

## Host platforms

### The task runner

`tasks.py` is the one implementation, on every platform. The only other entry point is
`Makefile`, a two-line forwarder for Linux/macOS muscle memory. It contains no logic.

There was a `tasks.cmd` Windows forwarder too, so you could type `tasks up`. It was deleted:
`python tasks.py up` works everywhere, and one documented invocation beats three.

It was not always that way. The runner started as `tasks.ps1` with a hand-written `Makefile`
mirroring it, and the mirror drifted — by the end of step 1 it had lost the `.modl` version
check, the COMMISSIONING detection, and the wait for Chariot's async admin seeding. That is to
say: the Linux path faithfully reproduced every bug this document exists to record. Two
implementations of the same knowledge is one too many. If you add a task, it goes in
`tasks.py`; if you find yourself adding logic to `Makefile`, stop.

Python rather than PowerShell 7 because the pattern services are already Python
(`asyncua`, `paho-mqtt`, FastAPI), so it is not a new dependency. Standard library only —
`zipfile` reads the `.modl` version, `urllib` does the HTTP, `hashlib` the checksums. No pip,
no venv.

### Bind mounts

Ignition runs as UID 2003 and must *write* to `data/config`.

**Linux:** bind mounts pass host UIDs straight through, so set `IGNITION_UID` / `IGNITION_GID`
in `.env` to your own `id -u` / `id -g`, or the gateway cannot write back and config-as-code
is read-only in practice.

**Windows and macOS:** Docker Desktop's translation layer fakes ownership, so the defaults
work — but it is slow. This mostly costs gateway write-back latency, not correctness.

For a machine you are presenting *from* on Windows, **clone into WSL2**
(`\\wsl$\Ubuntu\home\...`) and run the stack from there. Fast, correct ownership, no
surprises.

---

## Trial timers

Two independent 2-hour clocks: Ignition's and Chariot's.

**The Ignition trial reset only succeeds once the trial has already expired** — POSTing
against an active trial returns 403. You cannot top it up before walking on stage. Plan
around it: `python tasks.py trial` shows the remaining seconds, and the runbook's T-15 checklist
exists precisely because this cannot be done on demand.
