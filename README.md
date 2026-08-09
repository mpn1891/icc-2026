# ICC 2026 — Data Transaction & Event Patterns

A live, reproducible demo environment for the ICC 2026 talk on how data actually gets from a
source system into an event-driven architecture — seven different mechanisms, one event
backbone.

**Stack:** Ignition 8.3.8 + Cirrus Link MQTT modules 5.0.4 + Chariot MQTT Server +
PostgreSQL 17, all under `docker compose`, all version controlled.

## The seven patterns

| # | Pattern | Demo subject | Built |
|---|---------|--------------|-------|
| 1 | Native MQTT pub/sub | Vibration sensors behind a simulated local gateway — command in, waveform out | step 2 |
| 2 | Sparkplug B edge node | Bioreactor UDT publishing report-by-exception | step 3 |
| 3 | OPC UA → MQTT | Nova Flex analyzer; Ignition publishes on sample-complete | step 4 |
| 4 | Webhook / Push API | Event-capable but non-MQTT system POSTing to Ignition | step 5 |
| 5 | CDC / log tailing | Postgres WAL → Debezium → Ignition | step 6 |
| 6 | Poll / diff | REST and SQL polled on an incrementing high-water mark | step 7 |
| 7 | Scripted aggregation | Gateway script joining Postgres + REST + tags into one publish | step 8 |

**Current state: step 1 complete — infrastructure only.** Postgres, Ignition and Chariot come
up and talk to each other. No pattern work yet. Full plan in [`docs/00-plan.md`](docs/00-plan.md).

## Prerequisites

- Docker Desktop with compose v2
- Python 3.8+ (standard library only — no pip install, no venv)
- The Cirrus Link `.modl` files — **not in this repo** (licensed binaries), see
  [`compose/ignition/MODULES.md`](compose/ignition/MODULES.md). `seed` and `up` both refuse to
  run without them.

## Bringing it up

The minimum path to a running stack. Identical on Windows, macOS and Linux, and identical
whether you are the first person to build it or the fifth to clone it:

```bash
git clone https://github.com/mpn1891/icc-2026.git && cd icc-2026
# drop the three .modl files into compose/ignition/modules/
python tasks.py init     # creates .env from .env.example
python tasks.py seed     # ONE-TIME per machine — pauses for a browser step
python tasks.py up
python tasks.py health   # this going green is the success criterion
```

Five things about that sequence, roughly in the order they will trip you up:

1. **The `.modl` files are not optional and are not in the repo.** `init` only warns, but
   `seed` and `up` both hard-fail with a pointer to
   [`compose/ignition/MODULES.md`](compose/ignition/MODULES.md).
2. **`seed` blocks partway through and prints a URL.** Ignition parks in commissioning until
   someone accepts the Cirrus module certificates in a browser. One-time per machine, cannot
   be automated away — accept it and `seed` carries on by itself.
3. **`seed` is once per machine, not once per session.** Run it against an already-seeded
   stack and it refuses rather than overwriting anything. You only repeat it after
   `python tasks.py nuke`.
4. **Cloning a second copy next to a stack that is already running?** Change
   `COMPOSE_PROJECT_NAME` in the new `.env` before you `seed`, or the two checkouts fight over
   the same containers and volumes.
5. **Both trial clocks are 2 hours and independent.** `up` starts Chariot's for you. If the
   stack has been sitting overnight, expect `health` to have opinions.

After that, day to day is just:

```bash
python tasks.py up
python tasks.py down     # stops the stack, keeps volumes
```

`make up` also works on Linux/macOS — a two-line forwarder to `tasks.py`, no logic of its own.

> **Windows:** use `python tasks.py`, not a `.ps1`. PowerShell's execution policy blocks
> unsigned local scripts by default, and nothing here is worth making people run
> `Set-ExecutionPolicy` first. If `python` opens the Microsoft Store, install Python from
> [python.org](https://www.python.org/downloads/) with "Add to PATH" ticked.
>
> **Linux:** set `IGNITION_UID`/`IGNITION_GID` in `.env` to your own `id -u`/`id -g`. Bind
> mounts pass host UIDs through, and the gateway must write to `ignition/config`. Docker
> Desktop on Windows and macOS papers over this; native Linux does not.

| | |
|---|---|
| Ignition gateway | <http://localhost:8088> — `admin` / `password` |
| Chariot MQTT UI | <http://localhost:8081> — `admin` / `password` |
| MQTT broker | `localhost:1883` (see `compose/chariot/mqtt-users.json` for credentials) |
| PostgreSQL | `localhost:5432` — db `icc26`, user `icc26` |

> **Why the separate `seed` step?** Ignition 8.3 seeds `data/` from the image on first
> launch. Bind-mounting host directories over `data/config` at that moment blocks the seeding
> and the gateway comes up broken. `seed` boots once without those mounts, then hands off to
> the normal stack.
>
> What it copies out depends on which situation you are in, and it works this out for itself
> from whether the `ign-data` volume exists and whether `ignition/config` is populated:
>
> - **Fresh clone** (config from git, no volume) — initializes the volume and copies out only
>   the machine-local gateway identity, which is gitignored. **Your checkout is not modified**,
>   and `git status` should be clean afterwards.
> - **First build ever** (neither) — copies the whole vanilla baseline out to `ignition/`.
>
> Either way you repeat it only after `tasks.py nuke`. Run it when the stack is already seeded
> and it refuses rather than overwriting anything. Details in
> [`docs/00-architecture.md`](docs/00-architecture.md#seeding-why-the-first-boot-is-different).

## Working on the gateway

All Ignition 8.3 gateway configuration lives in files under `ignition/config` and
`ignition/projects`, bind-mounted into the container. The loop runs both ways:

- **You edit in the Designer or Gateway UI** → files change → `git status` shows the diff.
- **You `git pull` someone else's change** → the gateway does **not** notice on its own. It
  reads config from disk at startup and does not watch the files (verified — an edit alone
  changes nothing). Apply it with `python tasks.py restart ignition`.

  `python tasks.py scan` does the same thing without a restart, but needs an Ignition 8.3 API
  key first (Gateway UI → Platform → Security → API Keys → `IGNITION_API_TOKEN` in your `.env`;
  over plain HTTP also turn off "Require secure connections for API Keys"). Until that is set
  up, `scan` returns 401 and tells you so. *Applying the pull is the step people forget* — if a
  pulled change didn't take, do this before debugging anything else.

**Expect more diff than you made.** Any gateway write touches `lastModification` fields in the
neighbouring `resource.json` files, so `git status` routinely lists resources you never
knowingly edited. The rule:

```bash
git add <the files you meant to change>
git restore .                      # discard the rest — it is timestamp churn
```

Committing the churn is not harmful in itself, but it makes every teammate's next pull conflict
on files nobody changed. If a `resource.json` diff is *only* `lastModification*`, restore it.

**Changing a secret in `.env` needs a container restart** (`python tasks.py restart ignition`),
not `scan`. Environment variables are read at process start; scan only re-reads files.

## Common tasks

```bash
python tasks.py up | down | ps | logs [service] | restart [service]
python tasks.py scan             # gateway re-reads config + projects from disk
python tasks.py health           # check every service
python tasks.py trial            # how long is left on the Ignition trial
python tasks.py reset-trial      # only works once the trial has EXPIRED (403 while active)
python tasks.py verify-modules   # are the Cirrus modules present and the right build
python tasks.py nuke             # destroy all volumes and start over
```

## Things that will bite you

Each of these was hit and diagnosed while building step 1. Full detail in
[`docs/00-architecture.md`](docs/00-architecture.md).

**Get the right Cirrus modules.** Ignition 8.3 needs the **5.x** line. The 8.1-era 4.x
downloads have *identical filenames*, so there is no way to tell them apart on disk —
`python tasks.py verify-modules` opens each `.modl` and reads its real version. Download from the
**8.3** tab.

**Chariot serves its web UI while its MQTT port refuses connections.** Its broker does not
start without an active trial, and unlike Ignition the trial does not auto-start in the
container. `python tasks.py up` starts it for you; `python tasks.py chariot-trial` does it on demand.
`health` checks the listener rather than the web port, because the web port answering proves
nothing.

**The gateway reports itself RUNNING while parked in commissioning.** `/StatusPing` returns
`{"state":"RUNNING","details":"COMMISSIONING"}` when it is serving only the setup wizard. Any
third-party module triggers a one-time certificate acceptance in the browser; `seed` detects
this, prints the URL, and waits.

**There are two independent 2-hour trial timers** — Ignition's and Chariot's. Worse, the
Ignition trial reset only works *after* the trial expires; an active trial returns 403, so
you cannot top it up before going on stage. A Chariot demo key from Cirrus Link removes half
the problem and is worth requesting.

**Adding or upgrading a module needs a volume rebuild** (`nuke` then `seed`), not just a
restart. Modules are only discovered on the first launch of a fresh data volume.

**Bind mounts behave differently per host**, and Ignition (UID 2003) has to write to
`ignition/config`. On native Linux, set `IGNITION_UID`/`IGNITION_GID` to your own or the
gateway cannot write back. On Windows and macOS, Docker Desktop's translation layer handles
ownership but is slow — for the machine you present from, clone into WSL2. See
[`docs/00-architecture.md`](docs/00-architecture.md#host-platforms).

## Layout

```
compose/          per-service config: postgres initdb SQL, chariot ACLs, module manifest
ignition/         config-as-code — committed, bind-mounted into the gateway
services/         pattern simulators (steps 2–8, not started)
docs/             plan, architecture, and per-pattern talk tracks
tests/            `python tests/test_tasks.py` — task-runner guardrails, no Docker needed
tasks.py          the task runner — one implementation, every platform
Makefile          2-line Linux/macOS forwarder (make up)
```

## Topic namespace

Organized by ISA-95 physical hierarchy, **never** by ingestion mechanism — a subscriber
should not need to know how data arrived in order to find it.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

Patterns 4, 5 and 6 deliberately publish to the **same** topic
(`icc26/site1/qc/lims/sample-result`). Same data, three acquisition mechanisms, one
destination — swap between them live and no subscriber notices. The mechanism lives in the
payload's `meta.mechanism` field, not in the topic.

Full namespace and payload envelope in [`docs/00-architecture.md`](docs/00-architecture.md#topic-namespace).
