# Architecture

The decisions in this stack that are not obvious from reading the compose file, the reasoning
behind them, and every trap that cost real time to find. **This is the reference — one home per
fact.** If something here is contradicted elsewhere, this file wins; if you learn something new,
add it here rather than in a status note.

- What is true *right now* and what to do next: [`plans/00-status.md`](plans/00-status.md).
- What is still to be built: [`plans/00-master-plan.md`](plans/00-master-plan.md).
- Talk tracks, as they are written: [`04-novaflex-webhook.md`](04-novaflex-webhook.md).
- Kept but not the talk: [`extra/README.md`](extra/README.md) (LIMS, Countess, other dropped sources).

---

## The stack

```
                     ┌──────────────────────────────┐
   pattern 1  ──────▶│                              │◀────── pattern 2  (the SAME valve,
   pattern 3  ──────▶│   Chariot MQTT Server        │                    Sparkplug B)
   pattern 5  ──────▶│   :1883 / :8090(ws) / :8081  │
                     └──────────────┬───────────────┘
                                    │
                          MQTT Engine / Transmission
                                    │
   pattern 4 (HTTPS POST) ──▶ ┌────────┴─────────┐
   pattern 6 (JDBC poll)  ──▶ │  Ignition 8.3.8  │──── JDBC ────▶ PostgreSQL 17
                              │  + Cirrus 5.0.4  │                (wal_level=logical)
                              └──────────────────┘                   │
                                                               database `turbidity`
                                                               Debezium Server
                                                               (pattern 5 → MQTT)
```

Patterns 1 and 2 are one smart sample valve assembly in two firmwares, in their own containers.
Pattern 5 (Debezium MQTT sink) publishes onto Chariot as an out-of-band observer. Patterns 3, 4
and 6 publish through Ignition. Pattern 7 is TBD.

Pattern 4 is an HTTPS POST from the NovaFlex into an Event Stream, not a LIMS. **The transport
is not the mechanism**: HTTP in, MQTT out, `meta.mechanism = "webhook"`. Pattern 3 is the same
instrument over OPC UA onto the same topic.

| Service | Host ports | Built in |
|---|---|---|
| `postgres` | 5432 | step 1 |
| `ignition` | 8088, 8043 | step 1 |
| `chariot` | 1883, 8883, 8090, 8081, 8444 | step 1 |
| `sim-valve-mqtt` | 8085 (config page) | pattern 1 |
| `sim-valve-spb` | 8086 (config page) | pattern 2 |
| `opcua-novaflex` | 4841 | pattern 3 (OPC UA) and pattern 4 (planned HTTPS POST) |
| `opcua-countess` | 4840 | **Extra — not the talk.** Still in compose. Docs: [`extra/`](extra/README.md) |
| `lims` | 8000 (approval screen) | **Extra — not the talk.** Still in compose until pattern 4 is rebuilt. Docs: [`extra/lims-webhook-spec.md`](extra/lims-webhook-spec.md) |
| `sim-turbidity` | config page TBD | patterns 5 and 6 — planned. Writes only to database `turbidity` |
| `debezium` | 8083 | pattern 5 — planned, tails `turbidity` |
| `sim-vibration` | — | retired (was pattern 1, then a pattern-7 candidate). On disk, unwired |
| `ams` | — | **not planned.** Pattern 7 is TBD |
| `opcua-dcs` | — | **not planned.** Pattern 7 is TBD |
| `sim-particle-counter` | — | **not planned.** Pattern 6 is the turbidity database, not Modbus |

**Odoo was the 2026-08-19 candidate for pattern 5 and was dropped 2026-08-20** — do not add
it back. Pattern 5's source is now the turbidity meter's local database. Pattern 4's current
LIMS implementation is superseded; the rebuild is [`plans/04-novaflex-webhook.md`](plans/04-novaflex-webhook.md).
Pattern 7 is TBD and is the designated cut.

**Patterns 1 and 2 are the same physical device in two firmwares** — a badge-operated smart
sample valve assembly, one on `BR-201` speaking plain MQTT and one on `BR-202` speaking
Sparkplug B. `valve.py` and `webui.py` are byte-for-byte identical between the two build
contexts, so everything that differs between the containers is a difference the protocol
caused. Each serves its own device commissioning webpage (8085, 8086), and **the difference
between those two pages is as much of the talk as the traffic is**: on one the topic, QoS and
retained flag are editable fields, on the other the same three controls are disabled with the
specification clause that fixed them. Both valves are publish-only — nothing on the backbone
can open either. See [`plans/01-native-mqtt.md`](plans/01-native-mqtt.md) and
[`plans/02-sparkplug-b.md`](plans/02-sparkplug-b.md).

The retired vibration-gateway implementation (`vibsim`, the `vibration_sensor` UDT, the
`vibration-gw-*` event streams, the `icc26-native` Engine namespace, and
`services/sim-vibration/`) is still on disk and is wired to nothing.

**Nothing may depend on the internet at runtime.** It is a conference network and a stage. Every
image is pulled ahead of time, the Perspective firehose vendors its JavaScript rather than
loading a CDN, and the acceptance test is that the whole demo runs with networking disabled.

**One repo, not several.** Every meaningful change here is cross-cutting — changing the topic
namespace touches Ignition config, the simulators, Chariot ACLs, Debezium config and the docs.
That is one commit and one review in a monorepo; across four repos it is four PRs with an
ordering dependency, and bisecting gets painful. Compose also builds from the tree
(`build: ./services/...`), so splitting services out would mean publishing images to a registry
— a CI pipeline and a network dependency bolted onto a demo whose main virtue is running
offline. The usual reason to reach for a split, "the gateway writes noise into my repo", is
solved by `.gitignore` instead: only `data/config` and `data/projects` are committed.

### Pattern 4 is a NovaFlex HTTPS POST, not a LIMS

`lims` served four patterns under the original convergence design, then one (a human-approved
sample result) from 2026-08-19. **On 2026-08-23 that last surface left the talk.** Pattern 4 is
now the same NovaFlex as pattern 3, imagined with no OPC UA and no MQTT — only an HTTPS POST
into an Ignition Event Stream. See [`plans/04-novaflex-webhook.md`](plans/04-novaflex-webhook.md).

The FastAPI LIMS, its outbox, `lims-bridge`, and `qc/lims/sample-result` stay in the tree until
the rebuild unwires them. They are not the talk. SENAITE is not coming back.

The three earlier retired surfaces (`GET /results?since_id=N`, a Debezium-tailed insert, and a
query for the aggregation script) stay retired. The comments in `02-schema.sql` and `04-cdc.sql`
still say so; pattern 5's spec retires the publication itself.

---

## Topic namespace

Organized by **ISA-95 physical hierarchy, never by ingestion mechanism.** A subscriber must
not have to know *how* data arrived in order to find it. If the turbidity meter moves from
polling to CDC, nothing downstream should break.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

```
icc26/site1/upstream/br-201/sample-valve-01/event      # 1  badge scan + sample complete
icc26/site1/upstream/br-201/sample-valve-01/state      # 1  valve position, retained; also the LWT
icc26/site1/upstream/br-201/sample-valve-01/telemetry  # 1  line pressure / temp, every 5 s
icc26/site1/qc/analyzers/novaflex-01/result            # 3 and 4  (opcua-event | webhook)
icc26/site1/downstream/tff-301/turbidity-01/telemetry  # 5 and 6  (cdc | poll)
# pattern 7 TBD — no topic until it has a spec

spBv1.0/ICC26-Site1-UPSTREAM/{NBIRTH|NDEATH}/SAMPLE-VALVE-02             # 2 — spec-mandated
spBv1.0/ICC26-Site1-UPSTREAM/{DBIRTH|DDATA|DDEATH}/SAMPLE-VALVE-02/SV-202 # 2 — spec-mandated
```

Areas: `upstream`, `downstream`, `qc`, `utilities`. These are **places** — in a biologics
facility upstream and downstream really are segregated suites, with their own cleanroom grades,
HVAC and personnel flow, so the process name and the physical area coincide. The industry would
write these `usp`/`dsp`; spelled out they cost four characters and stop `dsp` colliding with
*digital signal processing* in a talk that plots bearing spectra.

Message types are a closed set: `telemetry`, `event`, `waveform`, `state`, `cmd/<verb>`,
`response/<what>`, `ack`.

`cmd/<verb>` and `response/<what>` are the two-token pair, and they go together: wherever one
appears the other should too. **All three of `cmd`, `response` and `ack` are currently used by
nobody.** Patterns 1 and 2 are both publish-only field devices — a sample valve that needs the
network's permission to open is a sample valve that stops working when the network does — and
no other pattern has yet needed to address a device. They stay in the set as the names to
reach for rather than inventing new ones later.

**Pattern 7 was going to be the first user of the pair.** It is TBD as of 2026-08-23, and these
names stay unused until a spec claims them. MQTT 3.1.1's missing response-topic properties remain
a footnote until then.

The turbidity topic is **settled in spec 05/06**, not provisional. It is also **`downstream`'s
first user**, so the area list stops being aspirational. `utilities` still has none. Pattern 7
has no topic until it has a spec.

**There is no `mes` area, deliberately.** An MES is a piece of software, not a place, and an
area slot filled with a system name is the same mistake as organising by ingestion mechanism —
one level higher up. A batch event happens in a *suite*, so it publishes under the cell that
produced it (`upstream/br-201/batch/event`) and names its source system in the payload. The
Postgres schema stays `mes.batch_event`: a database schema is a system-of-record namespace, and
that is exactly what it should be named after.

> **Wart, retired 2026-08-23:** `qc/lims/sample-result` put a software system in the
> line-or-cell slot. It existed because pattern 4 was a LIMS. Pattern 4 now publishes the
> NovaFlex result onto the analyzer topic, so the wart goes away with the LIMS rather than
> being renamed. Leave the ACL and firehose colouring until the rebuild unwires them; do
> not spend calendar on a tidy-up of a topic we are deleting.

**Every topic here is device-addressed.** There is currently no exception, which was not true
of the earlier vibration-gateway design and is worth knowing changed: that pattern had a
fleet-broadcast command topic and a flat response topic, both non-device-addressed, and both
went away with it.

**Pattern 1's conformance is enforced by an ACL, not by the protocol.** Its device has a
free-text topic box on its config page, so the only thing keeping sample data out of the `qc`
area is `sample-valve-01`'s publish grant of `icc26/site1/upstream/#` in
`compose/chariot/mqtt-users.json` — deliberately the *area* rather than the exact topic, so
the valve can be re-addressed to another cell on stage but cannot leave upstream. Pattern 2
needs no such grant to be well-behaved, because its topic is not its to choose. That contrast
is the point of running both.

Equipment ids in `plant.equipment` (see `compose/postgres/initdb/03-seed.sql`) are the same
strings that appear in topics. Keep it that way.

### Shared sources, as of 2026-08-23

**2026-08-19** dropped the LIMS triple (webhook + CDC + poll of one table). That reversal
stands: nobody webhooks, tails *and* polls the same table in production.

**2026-08-23** walks the "never share a source" part back, once, for a reason:

| Patterns | Shared thing | Why it is honest |
|---|---|---|
| 3 and 4 | NovaFlex result, **same topic** | Two vendor surfaces on one instrument (OPC UA vs HTTPS POST). The namespace must not leak which one fired |
| 5 and 6 | Turbidity meter **database**, same topic | The instrument only writes locally. CDC vs poll *is* the Monday-morning choice |
| 4 vs 5/6 | nothing | Different instruments. The webhook is not the turbidity table |

The rule worth protecting did not change: **the namespace still must not leak the mechanism.**
A subscriber reading `…/novaflex-01/result` or `…/turbidity-01/telemetry` cannot tell how the
document arrived. `meta.mechanism` carries that, and the firehose colours by it.

What 2026-08-19 gave up (the LIMS switch-over set-piece) stays given up. What 2026-08-19
rejected about turbidity (continuous value, deadband overlaps Sparkplug RBE) was about the
*signal*. The poll problem we need is the *store*: an identity column and a timer you can stall.
That is why turbidity comes back as a database, not as a 4–20 mA loop.

Consequences that are easy to miss:

- **`lims-bridge` is leftover.** The cycle hazard was load-bearing when the LIMS subscribed
  and caused publishes. Pattern 4 no longer subscribes. Drop the user when the LIMS is unwired.
- **`lims.sample_result` and `mes.batch_event` have no talk consumer.** Pattern 5 tails
  `turbidity.reading`. Retire `04-cdc.sql`'s publication with that spec.
- Pattern 4's message is analyzer-shaped because it *is* an analyzer result, sharing pattern
  3's topic rather than a LIMS topic. The `qc/lims/` wart dies with the rebuild.

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

**A Last Will belongs to whoever owns the session.** It is registered in the MQTT CONNECT
packet, so only the client that opened the connection can set one — every publish API is "send
this message now" on a session that already exists. Both valves own their own sessions, so
both get a will, and comparing them is the cleanest version of the Sparkplug argument:

**Sparkplug does not give you a death mechanism, it standardises the one MQTT already had.**
NDEATH *is* an LWT, and it inherits every one of the LWT's constraints. Its payload is frozen
at CONNECT, so a Sparkplug death certificate cannot carry the time of death either — the
consumer stamps that. DDEATH is not a will at all; it is an ordinary publish, so one connection
still buys exactly one will in Sparkplug too. What changed is the *agreement*: a spec-mandated
topic, a `bdSeq` payload that identifies the session, and a rule every consumer applies.
Hand-rolled — pattern 1 publishes a retained JSON document with `state: "offline"` on a topic
it chose — you invent the topic, the payload and the semantics, you have to tell every
consumer separately, and the next vendor invents them differently.

Pattern 1's will has one further flaw worth showing live: it is only useful to a late
subscriber if the retained flag happens to be ticked, and that flag is a checkbox on the
device's config page covering *all* its messages.

**Chariot is MQTT 3.1.1**, so there are no MQTT 5 response-topic or correlation-data
properties. Nothing in the demo currently needs them: every pattern is one-way. Patterns 1 and
2 are publish-only field devices. Patterns 3, 4 and 6 publish outward through Ignition.
Pattern 5's preferred path is Debezium's MQTT sink (Ignition is not in the publish path).
Pattern 7 is TBD. If a request/response pattern is ever added, this is the constraint it will
run into first, and `meta.correlation_id` is the field already reserved in the envelope for it.

**`meta.correlation_id` is `sample_id` on the NovaFlex.** Pattern 3 stamps it; pattern 4 stamps
the same value on the HTTPS path. One sample, two colours, one topic. That is the first real
user, and it does not need MQTT 5. Pattern 7 would have been the request/response user; it is
TBD.

### MQTT Engine has two ingest surfaces, and they produce different things

Both are enabled at once, both feed the same `MQTT Engine` tag provider, and the contrast
between them is most of patterns 1 and 2. Verified 2026-08-17.

| | **Custom Namespace** (pattern 1) | **Sparkplug B default namespace** (pattern 2) |
|---|---|---|
| Config | one subscription per device, hand-written | `spBv1.0/#`, shipped enabled |
| Resource | `com.cirruslink.mqtt.engine.gateway/custom-namespace/<name>/` | `…/default-namespace/Sparkplug B/` |
| Tag tree | mirrors the **JSON document**, under `MQTT Engine/<topic path>/` | mirrors the **device**, under `MQTT Engine/Edge Nodes/<group>/<node>/<device>/` |
| Datatypes | inferred per value; `numbersAsFloats` makes every counter a Float8; timestamps are String | declared in DBIRTH — Int64, Float, Boolean, String, DateTime |
| Engineering units | nowhere | on the wire, applied to the tag |
| A null field | **no tag is created at all** | a typed null: the tag exists, correctly typed, with no value |
| Adding a device | edit the subscription | nothing |

**Neither tree is configuration, and both are gitignored** —
`tag-definition/MQTT Engine/Edge Nodes/` and `tag-definition/MQTT Engine/icc26/`. The modules
rebuild them at runtime from whatever traffic that machine happened to see, so committing them
means every gateway churns another machine's leftovers into every diff. The anchored paths
leave the static `MQTT Engine/Engine Info/Edge Nodes/` folder tracked, as it should be.

A caveat that costs an hour if you meet it cold: **a MANAGED provider's tag tree is only
partially on disk.** Ignition persists a tag definition only where the config is non-default, so
the Sparkplug device's nineteen metrics write four `tags.json` entries — the ones carrying
engineering units — and the other fifteen leave empty folders. Counting files gives the wrong
answer. The authoritative read is
`GET /data/api/v1/tags/export?provider=MQTT Engine&type=json&recursive=true`, which needs an
API key.

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
`LICENSE_TYPE` only accepts `online` or `floating` — there is no trial value.

**Starting it is a manual step:** the web UI at `:8081` → **License** → start trial, or
install a Cirrus Link demo key on the same page. `tasks.py` does not automate it. There is an
undocumented `POST /license?action=start-trial-timer` in the UI bundle, and `up` used to call
it, but licensing is not something to drive from a script on a stage machine — an undocumented
route that changes under you takes the demo with it. So `up`, `trial` and `health` only *read*
license state and print the URL to go press the button.

Reads authenticate with a bearer token from `POST /login` (Basic auth is rejected), so the
calls run via `docker exec` against the container's own loopback using the curl it ships.

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
- **`git pull` or on-disk edit** → gateway does *not* notice → **`python tasks.py scan`**.

The gateway reads `data/config` at startup and does **not** watch it. This was tested directly:
editing `systemName` on disk on a running gateway produced no log activity and no effect until
the files were applied. Any workflow that assumes a file watcher is wrong.

**`python tasks.py scan` is the default apply.** It POSTs to `/data/api/v1/scan/config` and
`/data/api/v1/scan/projects`. 8.3 guards those routes with an API key
(Platform > Security > API Keys) in an `X-Ignition-API-Token` header, not the admin password,
and the header value is the complete `name:secret` token shown once at creation. `tasks.py`
uses `IGNITION_API_TOKEN_HTTPS` and validates the gateway certificate; it deliberately has no
HTTP credential fallback. API keys are machine-local, so each clone must create its own key
with a security level granted Gateway read/write access.

Fall back to `python tasks.py restart ignition` only when scan is unavailable (no API key yet)
or when the change is a **container-consumed `.env` secret** — those are process environment
and scan cannot pick them up.

The first key remains a manual bootstrap step. `/data/api/v1/api-token/generate` and the API
token resource routes require an already authenticated write-capable actor, while these routes
do not accept the gateway admin password as HTTP Basic auth. Automating key creation would
therefore require scripting the browser's session/CSRF login flow or shipping a shared
credential, neither of which is appropriate for this demo. After seeding, each user creates a
secure-channel key in the Gateway UI and copies its complete `name:secret` value into the
gitignored `.env`. Once that key exists, creating additional keys through the API is possible.

If a pulled change "didn't take", `python tasks.py scan` before debugging anything else.

### Where each service's config actually comes from

Established by direct inspection. This is the map to reason from whenever something is "set" but
is not taking effect.

**Ignition — five sources:**

1. **Git, via bind mounts.** `./ignition/config` → `data/config`, `./ignition/projects` →
   `data/projects`. Tag definitions, the `icc-2026` project, `systemName`, and the four
   `Embedded`-ciphertext MQTT/OPC config files.
2. **The image, via first-launch volume seeding.** Everything else under `data/`, including
   `data/var/ignition/modl/` (the Cirrus modules) and `data/modules.json`.
3. **Generated locally by `seed`.** `config/local/`, `config/resources/local/`,
   `config/ignition/tags/valueStore.idb` — gitignored, machine-specific, regenerated cleanly
   from nothing.
4. **`.env` and compose environment.** Admin credentials, the per-machine HTTPS API token,
   host ports, edition, TZ, and the pinned `hostname`.
5. **A browser, once per fresh data volume.** Module certificate fingerprints and
   `licenseAgreementHash`, written into `data/modules.json` *inside* the volume. Cannot be
   pre-seeded — tested, see Commissioning below.

The precedence rule that falls out: **a bind mount beats the image, and the volume beats
nothing.** Anything under `data/` that is not bind-mounted is frozen at whatever the image held
when the volume was first created. That is why the stale-image trap below was invisible until
somebody created a fresh volume.

**Chariot — nothing is config-as-code:**

1. The `chariot-config` named volume — its persistent store.
2. `compose/chariot/mqtt-users.json`, bind-mounted read-only, seeding the ACL'd accounts —
   **on first run only**, per `MQTT_USERS`. Editing it does nothing to a Chariot that already
   has a user store; that needs a `nuke` or hand-editing in the UI.
3. `SERVER_CONFIG`, inline in `docker-compose.yml` — ports and `allowAnonymous`. Read at
   **every container start**, so it is the one Chariot setting a restart can change.
4. `ADMIN_PASSWORD` from `.env`.
5. The trial — started by hand in the web UI, per volume. Runtime state, not config.

A clone reproduces Chariot exactly, because everything lives in compose — but only against a
fresh volume.

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

### The stale-image trap

This one shipped a broken repo and stayed invisible for two days. It is the most expensive bug
found in step 1, so it gets stated in full.

`compose/ignition/modules/` reaches the gateway **only** by being baked into the
`icc26/ignition:8.3.8` image, and **compose builds that image only when the tag is missing.**
Updating a `.modl` on disk therefore changes nothing: the tag already exists, `up` reuses it, and
the newer file is never seen by any container. A clone came up running Cirrus **4.0.8** Engine
and Transmission out of a stale image while the correct 5.0.4 files sat on disk and
`verify-modules` reported all three green:

```
W [ModuleInstance] Module "MQTT Engine" requires Ignition 8.0.16 (b0) and is not compatible with Ignition 8.3.8
```

Two things follow, and the second is the general lesson:

1. **`verify-modules` validates host files the gateway may never load.** Any check that does not
   compare against what is actually *in the image* is measuring the wrong thing.
2. **Upgrading a module needs the image tag deleted too**, not just `nuke` + `seed`:

```powershell
docker image rm icc26/ignition:8.3.8
python tasks.py nuke
python tasks.py seed
```

Landing the module sha256s in `modules.manifest.json` and hashing the `.modl` files *inside the
image* — or forcing `docker compose build` on every `seed` — is what makes a stale image fail
loudly instead of passing green.

**Fixed 2026-08-17.** `tasks.py` now passes `--build` on both `up` and `seed`, and the three
manifest `sha256` fields are filled in. The layer cache keeps an unchanged build to a couple of
seconds. Three things to keep straight about what that buys:

- **`up --build` fixes source edits reaching a container.** This is the everyday case, and it
  was silently broken for every `build:` service, not just the gateway — a one-line edit to
  `services/sim-valve-mqtt/app.py` never reached the running valve. Verified by making exactly
  that edit and watching it appear in the container's log.
- **`seed --build` is what fixes module upgrades**, and only there. The `.modl` files live at
  `data/var/ignition/modl`, *inside* the `ign-data` volume, and Docker seeds a volume from the
  image only while the volume is empty. A module version can therefore only change during a
  seed, so a stale tag at that moment is baked in permanently. `docker image rm` + `nuke` +
  `seed` is still the upgrade path.
- **The manifest hashes still only validate host files**, which is the wrong thing to measure.
  They catch a corrupt or swapped download, nothing more.

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

## Gateway HTTPS

HTTPS setup is part of `tasks.py seed`, immediately after commissioning and before the
machine-local identity is exported. The seed container uses Ignition's own `keytool` to create
`config/local/ignition/webserver/keystore/ssl.pfx`, reloads the keystore, and exports the public
certificate to the gitignored `ignition/certificates/icc26-ignition.crt`. On Windows it also
adds that public certificate to the current user's Trusted Root store. On macOS it adds the
certificate as a trusted root in the current user's login keychain (a locked keychain may
prompt for its password). Neither path exports the private key or requires machine-wide trust.
`tasks.py enable-ssl` repeats the same process idempotently for an existing gateway.

The generated certificate always covers `localhost`, `127.0.0.1`, and the host's current
hostname/FQDN. Extra stable conference-network names and addresses can be set before seeding
with `IGNITION_SSL_DNS_NAMES` and `IGNITION_SSL_IP_ADDRESSES`. Trusting it on the gateway host
does not make it trusted on audience devices: those devices must import the exported public
certificate too, and the URL they use must match one of its SANs.

Both Compose files bind Ignition's HTTP port to `127.0.0.1` only. It remains available for
local commissioning and maintenance, but network clients can reach only the HTTPS port.

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
| `icc26` | Demo data (`lims`, `mes`, `plant` schemas). `lims` is leftover until pattern 4's rebuild |
| `turbidity` | **Planned.** Instrument catalog for patterns 5 and 6 — a database the meter owns, not a schema in `icc26` |
| `cdc` | Debezium's login, has `REPLICATION`. Will be granted on `turbidity`, not used on `icc26` |

`cdc` being distinct from the application user is part of pattern 5's point: CDC is an
out-of-band observer the application knows nothing about. The turbidity simulator writes as
role `turbidity`; Debezium reads as `cdc`; Ignition polls as a SELECT-only JDBC user.

`lims.sample_result` and `mes.batch_event` are still `REPLICA IDENTITY FULL` and still named
in `04-cdc.sql`. Nothing reads that publication. Pattern 5's spec retires it and puts
`REPLICA IDENTITY FULL` on `turbidity.reading` instead. `lims.webhook_delivery` was pattern
4's outbox; it is leftover with the LIMS.

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

### Two checkouts cannot run at the same time

`container_name` is pinned in `docker-compose.yml` (`icc26-ignition`, `icc26-chariot`,
`icc26-postgres`), the network is pinned (`name: icc26`), and host ports come from `.env`. A
second stack collides on all three regardless of `COMPOSE_PROJECT_NAME`. Bring one down first.

**Stopped is not enough — the containers must be removed.** A container name is claimed by an
*existing* container, running or not, so a four-day-old `Exited (0)` gateway still blocks the
other checkout with `Conflict. The container name "/icc26-ignition" is already in use`. Use
`python tasks.py down`, which removes containers and the network while leaving volumes alone.
Compose also warns `a network with name icc26 exists but was not created for project ...` when
the other project created it first; harmless, and it clears once the owning project is down.

Set `COMPOSE_PROJECT_NAME` anyway in a scratch clone: it separates **volumes only**, which is
exactly what stops a `nuke` over there from reaching your gateway state over here.

---

## Things that look broken and are not

**`health` green does not mean MQTT works.** It checks Chariot's *listener*, not Ignition's
*client connection to it*. MQTT Engine once looped on a bad credential every 3 seconds for a
whole day behind a green `health`. When MQTT is the thing you care about, check the gateway logs
or Chariot's client list.

**Chariot validates credentials that ARE supplied, even with `allowAnonymous: true`.** Anonymous
access only helps clients that supply *no* credentials. A client with a username and a wrong (or
missing) password is rejected, not waved through:

```
CONNECT - Bad username and/or password. username true:admin, password false:*****
```

Good news for testing — anonymous cannot paper over a genuinely broken credential — but it means
`username: admin` with no password block is a *failure*, not a fallback. Set `username` to `""`
to connect anonymously on purpose.

`allowAnonymous` is currently `true` for the initial rollout, deliberately and temporarily. The
ACL'd accounts in `mqtt-users.json` are still seeded and still work. **Before the talk:** set it
back to `false`, restart, and confirm every client still connects with its own credential —
including MQTT Engine, which is currently riding on anonymous. `compose/chariot/README.md`
carries that reminder.

### Environment facts worth not rediscovering

- Chariot's version lives at `/Chariot/version.properties` (3.0.1). There is no image label.
- `curl` exists in both images. Chariot also has `wget` and `nc`.
- The Ignition image ships a **JRE, not a JDK** — no `javac`, no `jshell`. `java Foo.java` fails
  with *"Module jdk.compiler not in boot Layer"*.
- `ignition-secrets-tool.sh` only manages root/KEK keys. It cannot encrypt or decrypt a value.
- `docker exec <ctr> test -e <path>` gives **false negatives** — there is no `test` binary in
  these images. Use `sh -c '[ -e ... ]'`.
- Git Bash mangles container paths in `docker exec` (`/Chariot/...` becomes
  `C:/Program Files/Git/Chariot/...`). Prefix `MSYS_NO_PATHCONV=1` and use `//Chariot/...`, or
  just use PowerShell.

### Committed secrets are `Embedded`, on purpose

The four Cirrus/OPC config files hold `"type": "Embedded"` JWE ciphertext, committed. Converting
them to a Secret Provider was planned and then cut, on this basis: **this gateway has no
encryption key files at all** — no `data/config/ignition/keys`, no `root.json`, no `kek.json`.
Reading `SystemEncryptionServiceFactory`, that is what happens when `IGNITION_ROOT_KEY_PASSWORD`
is unset: the gateway falls back to `DefaultSystemEncryptionService`, whose key is built into the
jar rather than generated per machine. So committed ciphertext should decrypt on any 8.3.8
gateway that also has no root key password.

Two caveats. Ignition 8.3.8 ships only `internal`, `file` and `remote` provider types — **there
is no environment-variable Secret Provider**, which is what the original plan assumed. And the
portability claim above was inferred from bytecode; it is now **proven end-to-end (2026-08-17)**.
A gateway seeded from an empty volume, on a machine that had never held this gateway's identity,
connected MQTT Transmission to Chariot as `ign-transmission` — confirmed in Chariot's own client
list as well as the gateway log, with zero `Unable to decrypt ciphertext` lines. No Secret
Provider is needed. Demo-grade committed credentials are an
accepted trade here: portability is the goal, not secrecy.

---

## Trial timers

Two independent 2-hour clocks: Ignition's and Chariot's.

**Both are reset by hand, in each product's own web UI.** `tasks.py` reads them and never
writes them:

- **Ignition** — gateway UI → Config → Licensing. `GET /data/api/v1/trial` is unauthenticated;
  `python tasks.py trial` reads it over verified HTTPS. The matching `POST` resets it but needs
  an 8.3 write-capable API key, and the reset **only succeeds once the trial has already
  expired** (an active trial returns 403). So the procedure is *let it expire, then reset* —
  you cannot top it up before walking on stage, scripted or not.
- **Chariot** — web UI at `:8081` → License → start trial. Does not auto-start at all; see
  the broker section above.

The runbook's T-15 checklist exists precisely because neither of these can be done on demand
from the command line.
