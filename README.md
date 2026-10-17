# ICC 2026 — Data Transaction & Event Patterns

A reproducible demo environment for **"Becoming Event-Driven in Operational Technology with
Ignition"**, presented at ICC 2026. It shows seven different ways data gets from a source system
onto one event backbone, using a bioprocess QC scenario: a sample is drawn from a bioreactor,
analyzed, reviewed in a LIMS, and checked against the room it was drawn in.

**Stack:** Ignition 8.3.8 + Cirrus Link MQTT modules 5.0.4 + Chariot MQTT Server +
PostgreSQL 17 + Debezium, plus simulated instruments. All of it runs under `docker compose`
and all of the gateway configuration is version-controlled.

## The seven patterns

| # | Pattern | Demo subject |
|---|---------|--------------|
| 1 | [Native MQTT pub/sub](docs/patterns/01-native-mqtt.md) | Smart sample valve on `BR-201`. An RFID badge scan opens the bioreactor sample valve |
| 2 | [Sparkplug B edge node](docs/patterns/02-sparkplug-b.md) | **The same valve**, other firmware, on `BR-202`: birth/death, report-by-exception, self-describing metrics |
| 3 | [OPC UA → MQTT](docs/patterns/03-opcua-analyzer.md) | Cell analyzer. Ignition reads its OPC UA server and publishes each completed analysis |
| 4 | [Webhook / push API](docs/patterns/04-lims-webhook.md) | The LIMS opens a sample from the valve event, appends the analyzer result, and POSTs the analyst's pass/fail review to Ignition |
| 5 | [CDC / log tailing](docs/patterns/05-cdc.md) | A batch operation written to `bes.batch_event` → Debezium → MQTT. The writer holds no broker credentials |
| 6 | [Poll / diff](docs/patterns/06-poll.md) | A particle counter's own GraphQL API. A gateway timer walks its cursor → `em.reading` → MQTT. Nothing pushes |
| 7 | [Scripted aggregation](docs/patterns/07-sample-chain.md) | The LIMS review is the trigger. Joins valve, analyzer, batch operation and nearest particle count, and **publishes only when something was violated** |

**Patterns 1 and 2 are one device in two firmwares.** Each ships its own commissioning page, and
the difference between them is the argument: on the plain-MQTT one the topic, QoS and Retained
are yours to choose; on the Sparkplug one the same three controls are disabled, each labelled
with the clause of the specification that fixed it.

**And an i3X reader.** The gateway also serves the plant model over the CESMII
[i3X API](ignition/projects/i3x/PROVENANCE.md), and [`services/i3x-client/`](services/i3x-client/)
reaches pattern 7's conclusion from outside the gateway, on one Ignition login, with every
request and response shown on its page.

Every pattern runs end to end, and each one stops once its mechanism is proven. They're demos,
not products, and the known gaps are written down in the pattern's own spec under
[`docs/plans/`](docs/plans/).

## Quick start

**You need:**

- Docker Desktop with compose v2
- Python 3.8+ (standard library only: no pip install, no venv)
- The three Cirrus Link `.modl` files. They are licensed binaries and **not in this repo**; see
  [`compose/ignition/MODULES.md`](compose/ignition/MODULES.md) for which ones and where to get
  them. `seed` and `up` both refuse to run without them.

```bash
git clone https://github.com/mpn1891/icc-2026.git && cd icc-2026
# drop the three .modl files into compose/ignition/modules/
python tasks.py init     # creates .env from .env.example
python tasks.py seed     # ONE-TIME per machine; pauses for a browser step
python tasks.py up
python tasks.py health   # this going green is the success criterion
```

The same on Windows, macOS and Linux. Things that will trip you up, roughly in order:

1. **`seed` blocks partway through and prints a URL.** Ignition parks in commissioning until
   someone accepts the Cirrus module certificates in a browser. Accept them and `seed` carries
   on by itself, then generates the gateway HTTPS certificate and trusts it in your Windows
   certificate store or macOS login keychain.
2. **`seed` is once per machine, not once per session.** Run it against a seeded stack and it
   refuses rather than overwriting anything. You only repeat it after `python tasks.py nuke`.
   ([Why there is a separate seed step](docs/00-architecture.md#seeding-why-the-first-boot-is-different).)
3. **Both trial clocks are 2 hours, independent, and started by hand.** Chariot's does not
   auto-start, so its MQTT port stays shut until you start the trial on its License page at
   <http://localhost:8081>. `python tasks.py trial` reads both clocks and tells you where to
   click. The Ignition trial resets only *after* it expires.
4. **The first API key is manual.** `scan` needs one; see
   [One-time API key setup](#one-time-api-key-setup-per-machine).
5. **Only one checkout can run at a time.** Container names, the network and host ports are
   pinned, so run `python tasks.py down` in the first before bringing up a second. Give the
   second its own `COMPOSE_PROJECT_NAME` so the two never share volumes.

Day to day:

```bash
python tasks.py up
python tasks.py down     # stops the stack, keeps volumes
```

> **Windows:** use `python tasks.py`, not a `.ps1`; PowerShell's execution policy blocks
> unsigned local scripts by default. If `python` opens the Microsoft Store, install Python from
> [python.org](https://www.python.org/downloads/) with "Add to PATH" ticked.
>
> **Linux:** set `IGNITION_UID`/`IGNITION_GID` in `.env` to your own `id -u`/`id -g`. The
> gateway must write to the bind-mounted `ignition/config`.
>
> **macOS/Linux:** `make up` etc. also works. It is a two-line forwarder to `tasks.py`.

## What to open

| | |
|---|---|
| Ignition gateway | <https://localhost:8043> (`admin` / `password`) |
| Chariot MQTT UI | <http://localhost:8081> (`admin` / `password`) |
| MQTT broker | `localhost:1883` (accounts in `compose/chariot/mqtt-users.json`; anonymous connections are also allowed, see below) |
| PostgreSQL | `localhost:5432`, db `icc26`, user `icc26` |
| Sample valve, plain MQTT | <http://localhost:8085>, the device's own config page (pattern 1) |
| Sample valve, Sparkplug B | <http://localhost:8086>, the same page with three controls disabled (pattern 2) |
| Cell analyzer | <http://localhost:8087>, the instrument's screen, where the sample id is typed in (pattern 3) |
| LIMS review queue | <http://localhost:8000>, approve or reject a sample (pattern 4, and pattern 7's trigger) |
| Particle counter | <http://localhost:8089>, Start/Stop, sample point, **In-spec / OOS** room (pattern 6). Its API is GraphQL over HTTPS on `localhost:8443` |
| i3X API | <https://localhost:8043/system/webdev/i3x>, served by the gateway |
| i3X reader | <http://localhost:8092>, the external client and every call it made |

Watch everything land on the broker:

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

## Walk the demo

`health` going green says the stack is up, not that the mechanisms ran.
[`docs/end-to-end-test.md`](docs/end-to-end-test.md) is a ten-minute walk that proves it: badge
at :8085, type that sample id in at :8087, approve at :8000, and patterns 1, 3, 4, 5, 6 and 7 all
fire in one chain.

Two things worth knowing before they surprise you:

- **Silence is pattern 7 passing.** It publishes on `icc26/site1/qc/deviation` only when a sample
  violated something, so an in-spec sample produces nothing and the gateway logs
  `... is clean; no deviation published`. To make it speak, press **OOS** at :8089, wait ~30 s
  for the next poll, and walk the loop again. Press **In-spec** afterwards.
- **Pattern 2 is not on the walk.** `br-202`'s samples never reach the LIMS; nothing downstream
  of the broker consumes them.

## Working on the gateway

All Ignition gateway configuration lives in files under `ignition/config` and
`ignition/projects`, bind-mounted into the container. The loop runs both ways:

- **You edit in the Designer or Gateway UI** → files change → `git status` shows the diff.
- **You pull someone else's change, or edit files on disk** → the gateway does **not** notice.
  Apply it with **`python tasks.py scan`**. If a pulled change didn't take, scan before debugging
  anything else.

### One-time API key setup (per machine)

`scan` calls the gateway's scan API and needs a key. The first key cannot be created
automatically: creating one requires an already-authenticated actor, and the admin password is
not accepted as Basic auth on those routes.

1. Open <https://localhost:8043/app/platform/security/api-keys>.
2. Create a key that requires a secure channel.
3. Give it a security level included in both Gateway **Read** and **Write** permissions.
4. Copy the full value shown once at creation (`name:secret`, not the stored hash) into `.env`:

   ```env
   IGNITION_API_TOKEN_HTTPS=name:secret
   ```

5. Run `python tasks.py scan`; both scans should report `OK`.

`.env` is gitignored. Never commit the token. Until it exists, `scan` returns 401 and you can
fall back to `python tasks.py restart ignition`.

**Expect more diff than you made.** Gateway writes touch `lastModification` fields in
neighbouring `resource.json` files. Stage what you meant to change and `git restore .` the rest.

**A changed secret in `.env` needs `python tasks.py restart ignition`**, not `scan`. The API token
is the exception: `tasks.py` reads it on every run.

## Common tasks

```bash
python tasks.py up | down | ps | logs [service] | restart [service]
python tasks.py scan             # apply on-disk config/project changes (use this, not restart)
python tasks.py health           # check every service
python tasks.py trial            # both trial clocks (Ignition and Chariot)
python tasks.py verify-modules   # are the Cirrus modules present and the right build
python tasks.py nuke             # destroy all volumes and start over
python tests/test_tasks.py       # task-runner tests, no Docker needed
```

## Things that will bite you

Each of these was hit on the running stack. The reasoning and evidence for all of them is in
[`docs/00-architecture.md`](docs/00-architecture.md).

- **Get the 5.x Cirrus modules**, from the Ignition 8.3 tab. The 8.1-era 4.x downloads have
  identical filenames; `verify-modules` reads each `.modl`'s real version.
- **Chariot serves its web UI while its MQTT port refuses connections** until its trial is started.
- **The gateway reports RUNNING while parked in commissioning**, so a check that greps for
  `RUNNING` lies. `seed` detects this and waits.
- **Adding or upgrading a module needs `nuke`, then `seed`, with the image tag deleted.**
  Modules are baked into `icc26/ignition:8.3.8` and discovered only on a fresh volume's first
  launch. See [*The stale-image trap*](docs/00-architecture.md#the-stale-image-trap).
- **The broker allows anonymous connections, on purpose.** Every service has its own account
  and ACL, but `allowAnonymous` is `true` and MQTT Engine connects without credentials, so the
  ACLs are not a security boundary. Don't expose the broker beyond your machine. To enforce
  them, follow [`compose/chariot/README.md`](compose/chariot/README.md#anonymous-access-is-on-deliberately).
- **`health` green does not mean MQTT works.** It checks Chariot's listener, not Ignition's
  connection to it.
- **A poll can run perfectly and publish nothing.** Restart the particle counter and pattern 6's
  cursor points past the instrument's new sequence. Clear
  `[default]icc26/site1/env_monitoring/particle-counter-01/state/cursor` to recover; `health`
  warns when nothing has landed in `em.reading` for 180 s.

## Topic namespace

Organized by ISA-95 physical hierarchy, **never** by ingestion mechanism. A subscriber should not
need to know how data arrived in order to find it.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

```
icc26/site1/upstream/br-201/sample-valve-01/event/vlv-badge-scanned     # 1  every badge, granted or denied
icc26/site1/upstream/br-201/sample-valve-01/event/sample-acq-completed  # 1  only when a sample ran
icc26/site1/upstream/br-201/sample-valve-01/status                      # 1  online/offline, retained; also the LWT
icc26/site1/upstream/br-201/sample-valve-01/telemetry                   # 1  air supply / enclosure temp, every 5 s
icc26/site1/qc/analyzers/cell-analyzer-01/sample-analyzed               # 3  analyzer result
icc26/site1/qc/lims/sample-results-released                             # 4  review: analyst + disposition
icc26/site1/upstream/br-201/batch/event                                 # 5  CDC of bes.batch_event, an INSERT
icc26/site1/audit/bes/batch-event                                       # 5  an UPDATE/DELETE: somebody amended the record
icc26/site1/env_monitoring/particle-counter-01/sample-analyzed          # 6  particle count analysis
icc26/site1/qc/deviation                                                # 7  ONLY when something was violated
icc26/site1/qc/i3x-meta-review-completed                                #    the external i3X reader's finding

spBv1.0/ICC26-Site1-UPSTREAM/{NBIRTH|NDEATH}/SAMPLE-VALVE-02               # 2  spec-mandated
spBv1.0/ICC26-Site1-UPSTREAM/{DBIRTH|DDATA|DDEATH}/SAMPLE-VALVE-02/SV-202  # 2  spec-mandated
```

Every message is `ts` and `values`, and the mechanism is named nowhere, in the address or the
payload. Full namespace and payload rules:
[`docs/00-architecture.md`](docs/00-architecture.md#topic-namespace).

## Repository layout

```
compose/          per-service config: postgres initdb SQL, Chariot ACLs, module manifest
ignition/         gateway config-as-code, bind-mounted into the container
  projects/i3x/   vendored third-party i3X server (see License below)
services/         the simulated instruments, the LIMS and the i3X reader;
                  services/README.md says which pattern each one serves
docs/             architecture, data flows, the end-to-end walk, and the build history
tests/            task-runner tests
tasks.py          the task runner, one implementation for every platform
Makefile          two-line Linux/macOS forwarder
```

**Which doc to read:**

- [`docs/00-architecture.md`](docs/00-architecture.md): how anything works, and why.
- [`docs/data-flows.md`](docs/data-flows.md): each pattern's path from device to topic, one diagram apiece.
- [`docs/end-to-end-test.md`](docs/end-to-end-test.md): the walk that proves the stack works.
- [`docs/patterns/`](docs/patterns/): one per pattern, covering why it exists, what it puts
  on the wire, and how it fails.
- [`docs/plans/`](docs/plans/): the per-pattern build specs and the project's working notes,
  kept as a record of how it was built. See [`docs/plans/README.md`](docs/plans/README.md).
- [`docs/reference/`](docs/reference/): the vendor information models the simulators follow.

## License

[MIT](LICENSE), with these exceptions:

- **[`ignition/projects/i3x/`](ignition/projects/i3x/) is not covered by the MIT license.** It is
  Travis Cox's i3X reference implementation for Ignition
  ([upstream](https://github.com/iatraviscox/i3x-project),
  [Ignition Exchange 2916](https://inductiveautomation.com/exchange/2916)), vendored here for the
  demo with a few local changes. The upstream repository states no license, so its terms are
  its author's. See [`PROVENANCE.md`](ignition/projects/i3x/PROVENANCE.md).
- **Ignition, the Cirrus Link modules and Chariot** are commercial products under their vendors'
  licenses. None of their binaries are in this repo.
- **Vendor manuals** referenced in [`docs/reference/`](docs/reference/) are linked, not
  redistributed. Vendor and product names belong to their owners.
