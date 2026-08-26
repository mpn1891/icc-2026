# ICC 2026 — Data Transaction & Event Patterns

A live, reproducible demo environment for the ICC 2026 talk on how data actually gets from a
source system into an event-driven architecture — seven different mechanisms, one event
backbone.

**Stack:** Ignition 8.3.8 + Cirrus Link MQTT modules 5.0.4 + Chariot MQTT Server +
PostgreSQL 17, all under `docker compose`, all version controlled.

## The seven patterns

| # | Pattern | Demo subject | Built |
|---|---------|--------------|-------|
| 1 | Native MQTT pub/sub | Smart sample valve assembly — RFID badge scan opens a bioreactor sample valve | built |
| 2 | Sparkplug B edge node | **The same valve assembly**, other firmware — birth/death, RBE, self-describing metrics | built |
| 3 | OPC UA → MQTT | Nova Flex analyzer (`novaflex-01`); Ignition publishes on sample-complete. Sample id typed in on the instrument's own screen (:8087) | built |
| 4 | Webhook / Push API | LIMS opens the sample entry from the valve event, appends the analyzer result, POSTs the reviewed record to Ignition; remaining: pass/fail on both outcomes | built, pass/fail open |
| 5 | CDC / log tailing | Ignition batch timer → `bes.batch_event` → Debezium → MQTT | planned |
| 6 | Poll / diff | MET ONE HTTP API in `qc/analyzers`, Ignition poll → Event Stream | planned |
| 7 | Scripted aggregation | LIMS-review listener joins valve, Nova, batch phase, nearest MET ONE | planned |

**Patterns 1 and 2 are one device in two firmwares**, which is the point of running both: a
badge-operated sample valve on `BR-201` speaking plain MQTT, and the identical assembly on
`BR-202` speaking Sparkplug B. Each ships its own **device commissioning webpage** — and the
difference between those two pages is as much of the talk as the traffic. On one, the topic is
a text box, the QoS is a dropdown and Retained is a checkbox. On the other, the same three
controls are disabled, each labelled with the clause of the specification that fixed it.

**Current state:** infrastructure, patterns 1–3, and pattern 4 (minus pass/fail) are built.
Patterns 5–7 were re-sourced on 2026-08-23. On 2026-08-25 the presentation/firehose/runbook spec
was cut (the demo surface is the broker itself plus the three product screens the services
already serve), the Countess came out of the demo, and pattern 7 gained a requirement for an
event store. What is true today and what is next:
[`docs/plans/00-status.md`](docs/plans/00-status.md).

There is no pattern 8 — the numbering stops at 7.

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

Six things about that sequence, roughly in the order they will trip you up:

1. **The `.modl` files are not optional and are not in the repo.** `init` only warns, but
   `seed` and `up` both hard-fail with a pointer to
   [`compose/ignition/MODULES.md`](compose/ignition/MODULES.md).
2. **`seed` blocks partway through and prints a URL.** Ignition parks in commissioning until
   someone accepts the Cirrus module certificates in a browser. One-time per machine, cannot
   be automated away — accept it and `seed` carries on by itself. It then generates the
   gateway HTTPS certificate, exports it to `ignition/certificates/`, and trusts it in the
   current user's Windows certificate store or macOS login keychain.
3. **`seed` is once per machine, not once per session.** Run it against an already-seeded
   stack and it refuses rather than overwriting anything. You only repeat it after
   `python tasks.py nuke`.
4. **Two checkouts cannot run at the same time.** `container_name` and the network name are
   pinned in `docker-compose.yml`, and the host ports come from `.env`, so a second stack
   collides on all three no matter what you set. Run `python tasks.py down` in the first
   checkout before bringing up a second. Do still give the second one its own
   `COMPOSE_PROJECT_NAME` — that is what keeps their *volumes* apart, so a `nuke` or
   `down -v` over there cannot reach your gateway state over here.
5. **Both trial clocks are 2 hours, independent, and started by hand.** Chariot's does not
   even auto-start, so its MQTT port stays shut until you visit its License page. `up` and
   `health` tell you which clock needs attention; neither touches licensing itself. If the
   stack has been sitting overnight, expect `health` to have opinions.
6. **The HTTPS certificate is automatic; the first API key is not.** Ignition requires an
   authenticated write-capable session or key to create an API key, so a fresh gateway has a
   bootstrap problem that `tasks.py` cannot solve with the admin password. Each person creates
   one key in the Gateway UI and stores it only in their gitignored `.env`; details below.

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
| Ignition gateway | <https://localhost:8043> — `admin` / `password` |
| Chariot MQTT UI | <http://localhost:8081> — `admin` / `password` |
| MQTT broker | `localhost:1883` (see `compose/chariot/mqtt-users.json` for credentials) |
| PostgreSQL | `localhost:5432` — db `icc26`, user `icc26` |
| Sample valve — plain MQTT | <http://localhost:8085> — the device's own config page (pattern 1) |
| Sample valve — Sparkplug B | <http://localhost:8086> — the same page, three controls disabled (pattern 2) |
| Nova FLEX2 sample login | <http://localhost:8087> — the instrument's own screen, where the valve's sample id is typed in (pattern 3) |

> **Why the separate `seed` step?** Ignition 8.3 seeds `data/` from the image on first launch,
> and bind-mounting host directories over `data/config` at that moment blocks the seeding and
> breaks the gateway. `seed` boots once without those mounts, then hands off to the normal stack.
> It works out for itself whether you are a fresh clone (in which case **your checkout is not
> modified** — only gitignored machine identity is copied out) or the first build ever (the whole
> baseline is exported to `ignition/`). Either way it is once per machine, it refuses rather than
> overwriting if you run it twice, and you repeat it only after `tasks.py nuke`.
> [How and why](docs/00-architecture.md#seeding-why-the-first-boot-is-different).

## Working on the gateway

All Ignition 8.3 gateway configuration lives in files under `ignition/config` and
`ignition/projects`, bind-mounted into the container. The loop runs both ways:

- **You edit in the Designer or Gateway UI** → files change → `git status` shows the diff.
- **You `git pull` someone else's change, or you edit files on disk** → the gateway does
  **not** notice on its own. It does not watch the files (verified — an edit alone changes
  nothing). Apply it with **`python tasks.py scan`**.

  Scan POSTs to `/data/api/v1/scan/config` and `/data/api/v1/scan/projects` over HTTPS. It
  needs the one-time API key in `.env` as `IGNITION_API_TOKEN_HTTPS` (setup below). Until
  that key exists, `scan` returns 401 and tells you to fall back to
  `python tasks.py restart ignition`. *Applying the pull is the step people forget* — if a
  pulled change didn't take, scan before debugging anything else.

### One-time API key setup (per machine)

The first API key cannot be created automatically on a fresh gateway: the key-creation API
itself requires an existing authenticated actor with Gateway write access, and Ignition does
not accept the admin username/password as Basic auth on these routes.

1. Open <https://localhost:8043/app/platform/security/api-keys>.
2. Create a key that requires a secure channel.
3. Assign it a security level included in both Gateway **Read** and **Write** permissions.
4. Copy the complete value shown once at creation — `name:secret`, not the stored hash — into:

   ```env
   IGNITION_API_TOKEN_HTTPS=name:secret
   ```

5. Run `python tasks.py scan`; both scans should report `OK`.

`.env` is gitignored. Never commit or paste the token into documentation, issues, or chat.
Once this bootstrap key exists, additional API keys could be created through the API, but
every new clone still needs this one manual setup.

**Expect more diff than you made.** Any gateway write touches `lastModification` fields in the
neighbouring `resource.json` files, so `git status` routinely lists resources you never
knowingly edited. The rule:

```bash
git add <the files you meant to change>
git restore .                      # discard the rest — it is timestamp churn
```

Committing the churn is not harmful in itself, but it makes every teammate's next pull conflict
on files nobody changed. If a `resource.json` diff is *only* `lastModification*`, restore it.

**Changing a container-consumed secret in `.env` needs a container restart**
(`python tasks.py restart ignition`), not `scan`. The API token is the exception: `tasks.py`
reads `IGNITION_API_TOKEN_HTTPS` itself on every invocation, so updating it needs no restart.

## Common tasks

```bash
python tasks.py up | down | ps | logs [service] | restart [service]
python tasks.py scan             # apply on-disk config/project changes (use this, not restart)
python tasks.py health           # check every service
python tasks.py trial            # both trial clocks (Ignition and Chariot)
python tasks.py verify-modules   # are the Cirrus modules present and the right build
python tasks.py nuke             # destroy all volumes and start over
```

## Things that will bite you

Each of these was hit and diagnosed while building step 1. **Every one of them, with the
reasoning and the evidence, is in [`docs/00-architecture.md`](docs/00-architecture.md)** — this
is just the short list you need on day one.

**Get the right Cirrus modules.** Ignition 8.3 needs the **5.x** line, from the 8.3 tab. The
8.1-era 4.x downloads have *identical filenames*, so `python tasks.py verify-modules` opens each
`.modl` and reads its real version rather than trusting the name.

**Chariot serves its web UI while its MQTT port refuses connections.** Its broker does not start
without an active trial or license, and the trial does not auto-start. Start it at
<http://localhost:8081> → **License** → start trial.

**Both trial clocks are 2 hours and reset by hand**, in each product's own web UI —
`python tasks.py trial` reads them and tells you where to click. The Ignition reset only works
*after* the trial expires, so the procedure is *let it expire, then reset*, never *top it up
before going on stage*. A Chariot demo key from Cirrus Link removes half the problem.

**The gateway reports itself RUNNING while parked in commissioning**, so any check that greps for
`RUNNING` lies. A one-time certificate acceptance in the browser is needed per fresh volume;
`seed` detects this, prints the URL, and waits.

**Adding or upgrading a module needs `nuke` then `seed` — and the image tag deleted too.**
Modules are baked into `icc26/ignition:8.3.8`, compose rebuilds it only when the tag is missing,
and modules are only discovered on the first launch of a fresh volume. A `.modl` updated on disk
alone reaches nothing. See
[*The stale-image trap*](docs/00-architecture.md#the-stale-image-trap).

**`health` going green does not mean MQTT works.** It checks Chariot's listener, not Ignition's
client connection to it.

**Bind mounts behave differently per host**, and Ignition (UID 2003) must write to
`ignition/config`. On native Linux set `IGNITION_UID`/`IGNITION_GID` to your own; on Windows and
macOS Docker Desktop papers over it but is slow — for the machine you present from, clone into
WSL2.

## Layout

```
compose/          per-service config: postgres initdb SQL, chariot ACLs, module manifest
ignition/         config-as-code — committed, bind-mounted into the gateway
services/         pattern simulators
docs/             00-architecture.md is the reference; plans/ is current status + specs
tests/            `python tests/test_tasks.py` — task-runner guardrails, no Docker needed
tasks.py          the task runner — one implementation, every platform
Makefile          2-line Linux/macOS forwarder (make up)
```

**Which doc to read:** [`docs/00-architecture.md`](docs/00-architecture.md) for how anything
works and why; [`docs/plans/00-status.md`](docs/plans/00-status.md) for what is true today and
what to do next; [`docs/plans/`](docs/plans/) for per-pattern build specs.

## Topic namespace

Organized by ISA-95 physical hierarchy, **never** by ingestion mechanism — a subscriber
should not need to know how data arrived in order to find it.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

Each pattern publishes to its **own** topic. The mechanism lives in the payload's
`meta.mechanism` field, never in the address. Pattern 7 is the join: it listens for the
LIMS review and publishes one sample-chain document.

Full namespace and payload envelope in [`docs/00-architecture.md`](docs/00-architecture.md#topic-namespace).
