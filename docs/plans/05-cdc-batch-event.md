# 05 — CDC on `bes.batch_event`

> **Supersedes the pattern-5 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> Written 2026-08-26 alongside the build. Talk track:
> [`../talk-tracks/05-cdc.md`](../talk-tracks/05-cdc.md), written 2026-09-06 — after 07 landed,
> which is why its close is *the MVP shape needed no change*.
>
> **Two things changed from the master-plan sketch and are settled here:** the batch engine is a
> **manual advance**, not an auto-cycling timer with a dwell; and the ISA-88 element is an
> **operation**, not a phase — tag, column, `event_type` values and wire field all say
> `operation`. Both are argued below. Do not "fix" either back toward the older summary.
>
> ## ⚠ This is an MVP
>
> **It runs end to end and it is not finished.** What is here proves the mechanism — the writer is
> innocent, Debezium tails the WAL out of band, a message lands on the backbone — which is
> everything the *talk* needs. It has not been shaped by an actual consumer, because pattern 7 is
> not written yet.
>
> Expect to revisit at least these when 07 lands, and do not treat any of them as settled:
>
> - **The `values` shape.** It is a flat transcription of the row. If 07 wants the batch context
>   nested, or wants `batch_id` promoted to `meta.correlation_id` after all, that is a reasonable
>   change, not a regression.
> - **The pair model.** Two rows per click was chosen for fidelity to a batch log. If 07's lookup
>   never reads an `operation_end`, the closing row is pure noise on the wire and one row per click
>   is better.
> - **`batch_end` carries no summary.** No duration, no operation count, nothing an aggregate could
>   use. It is currently a marker.
> - **Nothing writes `deviation`**, though `event_type` allows it. The most obvious missing feature,
>   and probably the most useful one for the GxP framing.
> - **No `plant.batch` integration.** `batch_id` is free text typed into a tag with no FK and no
>   validation, so a typo is a new batch. Fine for a demo, wrong for anything else.
>
> The verification below passed on 2026-08-26. That is a floor, not a ceiling.

| | |
|---|---|
| **Mechanism** | `cdc` |
| **Signal contributed to the spine** | the batch operation running at a given instant, and whether sampling is qualified in it |
| **Topic** | `icc26/site1/upstream/{equipment_id}/batch/event` — QoS 1, **retain false** |
| **Writer** | `bes_batch` (Ignition project script), fired by a tag event script |
| **Store** | `bes.batch_event` in the `icc26` database, written through the `ICC26` JDBC datasource as user `icc26` |
| **Tail** | Debezium Server, pgoutput, publication `icc26_cdc`, slot `icc26_debezium`, as user `cdc` |
| **Sink** | WebDev `cdc-sink` → `bes_cdc.handle()` → Transmission as `ign-transmission` |

## The claim, and the thing that makes it true

**The writer does not know MQTT exists.** `bes_batch` writes two rows and stops. It holds no
broker credentials, imports nothing from Cirrus, and has no idea anybody is watching. Debezium
reads the write-ahead log as a *different Postgres role*, out of band, and a message appears on
the backbone that nobody in the application asked for.

That is the whole pattern, and it is one line of code away from being a lie. If `bes_batch` ever
grows a `system.cirruslink.transmission.publish` call — even "just for the demo", even as a
fallback — pattern 5 becomes pattern 3 with extra steps. The module docstring says so; keep it
there.

**Say BES, not MES.** `CIP → SIP → INOC → GROWTH → HARVEST` is batch execution specifically. See
[`../00-architecture.md` § *Sources as of 2026-08-23*](../00-architecture.md) for why this room
will hear "MES" and expect work orders.

**Pattern 5 is an application we own, and that is worth saying out loud.** The textbook CDC case
is a system you cannot modify. This one you could — so the honest framing is that this timer
stands in for a Batch Execution System nobody is going to patch to emit events, and everything
that makes CDC the right answer there is present here: the writer is innocent, the `cdc` role is
not the application role, and turning the tail off leaves the reactor cycling with a silent topic.

## `operation`, not `phase`

Renamed everywhere on 2026-08-26, before anything was built on the table.

In ISA-88 a **phase** is the smallest element that does process action — "Add Water", "Agitate",
"Hold at 37 °C". `CIP` / `SIP` / `INOC` / `GROWTH` / `HARVEST` sit one level up: they are
**operations**. The tag somebody clicks in Tag Explorer already said `operation`, and the column
it wrote to said `phase`.

Same reasoning that renamed `mes.` → `bes.` on 2026-08-23: **the schema follows the words spoken
on stage.** This pattern's verify step puts `SELECT * FROM bes.batch_event` on screen moments
after the talk track has used the word, and a column header that contradicts the sentence just
spoken is the one place the churn would have been visible.

Cost: one migration ([`../../compose/postgres/migrate-06-batch-operation.sql`](../../compose/postgres/migrate-06-batch-operation.sql))
and four documents.

## The batch engine: manual, not timed

The master plan specified a gateway timer that auto-cycles on a dwell, gated by enable/disable
tags. **Dropped.** What is built:

```
IDLE ──click──▶ CIP ──▶ SIP ──▶ INOC ──▶ GROWTH ──▶ HARVEST
  ▲                                                    │
  └──────────── click (operation_end + batch_end) ◀─────┘
```

Three tags in a `batch_data` folder on the `bioreactor` UDT type:

| Tag | | |
|---|---|---|
| `batch_id` | String, memory | typed in by hand. `bes_batch` refuses to advance without one |
| `manual_advance` | Boolean, memory | **the trigger.** A `valueChanged` script fires on the rising edge; the module resets it to `false` on every exit path, refusals included |
| `operation` | String, memory, default `IDLE` | written **after** the commit, so the tag can never claim an operation the database does not have |

**Why manual beats a dwell.** Pattern 7's rehearsal needs the reactor parked in `GROWTH` when
the valve is badged, and then parked outside it for the second run. A timer makes that a waiting
game on stage; a button makes it deterministic. The failure demo is unaffected — stop Debezium,
click three times, show the silent topic.

**Why a boolean in Tag Explorer and not a screen.** Spec 08 was cut on 2026-08-25; there are no
Perspective views in this demo. Tag Explorer is also the more honest surface: it looks like
somebody poking a gateway, which is what it is.

`batch_data` lives on the **type**, so `br-202` gets a live `manual_advance` button too. That is
what `equipment_id` is for — see below.

## Schema

Full DDL in [`../../compose/postgres/initdb/02-schema.sql`](../../compose/postgres/initdb/02-schema.sql);
live-apply for an existing volume in
[`../../compose/postgres/migrate-06-batch-operation.sql`](../../compose/postgres/migrate-06-batch-operation.sql).
**Run that migration as `postgres`, not `icc26`** — initdb created the table as the superuser,
and both `RENAME COLUMN` and `ALTER PUBLICATION` are owner-only.

```
bes.batch_event
    id            bigint  identity pk
    batch_id      text    not null
    equipment_id  text    not null      -- 'br-201', the TOPIC form
    event_type    text    not null      -- operation_start | operation_end | batch_end | deviation
    operation     text                  -- null on batch_end
    payload       jsonb   not null      -- {"qualified_window": bool}
    occurred_at   timestamptz not null
```

Three things in it are load-bearing.

**`equipment_id` is the topic form, not `plant.equipment`'s.** It holds `br-201`, taken from the
tag path, and the sink builds the topic from it. Without the column, a stray click on `br-202`
would publish onto `br-201`'s address. `plant.equipment` stores `BR-201` for the same vessel, so
the two do not currently match — a known wart recorded in
[`../00-architecture.md`](../00-architecture.md), not fixed here.

**`payload.qualified_window` is written at insert time, never in the sink.** The batch protocol
qualifies sampling for `GROWTH` only — pulling material during `CIP`/`SIP` makes no sense, and
`INOC`/`HARVEST` are outside the characterized production phase. That rule has exactly one home,
the `QUALIFIED` tuple in `bes_batch`. Computing the flag in `cdc-sink` instead would put it on
the wire without it ever having been in the change event: **a flag added after the tail is a
flag the CDC demo did not actually observe.**
[`../00-architecture.md` § *Derived flags travel with the fact*](../00-architecture.md).

**One click writes two rows, and they share an `occurred_at`.** An advance closes the outgoing
operation and opens the incoming one, in one transaction, at one instant.

## Two consequences of the pair model that will bite quietly

Both are here because they are invisible until pattern 7 gets a wrong answer.

### `qualified_window` is `false` on every `operation_end`

The flag means **"sampling is qualified in the interval that begins at `occurred_at`"**. Nothing
is running between an operation's end and the next one's start, so an `operation_end` row carries
`false` — *including the one that closes `GROWTH`*.

Get this wrong and pattern 7 reads the closing row of `GROWTH`, sees `true`, and reports a sample
as inside the qualified window when the reactor had already moved on. It would be right almost
every time, and wrong exactly at the boundary the whole demo is about.

### Pattern 7 must order by `occurred_at DESC, id DESC`

Both rows of one click carry the same timestamp, so `ORDER BY occurred_at DESC LIMIT 1` picks
one of them arbitrarily. The `id` tie-break is what makes the `operation_start` win:

```sql
SELECT operation, payload->>'qualified_window'
  FROM bes.batch_event
 WHERE equipment_id = ? AND occurred_at <= ?
 ORDER BY occurred_at DESC, id DESC
 LIMIT 1;
```

`ix_batch_event_lookup` exists for exactly this query. Both facts are in the schema comment too,
so 07 inherits them rather than rediscovering them.

## Ignition resources

| Resource | Path |
|---|---|
| JDBC datasource | `ignition/config/.../database-connection/ICC26/` — **created UI-first**, see below |
| UDT type | `.../tag-type-definition/default/udts.json` → `bioreactor` → `batch_data` |
| Writer | `ignition/projects/icc-2026/ignition/script-python/bes_batch/code.py` |
| Sink logic | `ignition/projects/icc-2026/ignition/script-python/bes_cdc/code.py` |
| Sink endpoint | `ignition/projects/icc-2026/com.inductiveautomation.webdev/resources/cdc-sink/` |

### The datasource, and the look-alike

`ICC26` → `jdbc:postgresql://postgres:5432/icc26`, user `icc26`. Created in the Gateway UI, then
committed from what `git status` reveals.

**`database-connection/pg_db` is not that connection.** It points at the **`postgres` database as
user `ignition`** — wrong database, wrong user. It passes a glance in the dropdown and then writes
nowhere useful, and nothing about the failure says "you picked the wrong connection".
[`../00-architecture.md` § *The JDBC datasource*](../00-architecture.md).

### The Gateway Scripting Project

A tag event script runs in **gateway scope**, so `bes_batch` resolves only if
**Config → Gateway Settings → Gateway Scripting Project** is set to `icc-2026`. This had never
come up before: pattern 3's tag script calls only `system.eventstream.publishEvent`, a system
function, and the `opcua_event` module it feeds is reached from an Event Stream transform, which
is already project-scoped.

Set it once in the UI and commit whatever appears. Patterns 6 and 7 will both want it.

## Debezium

[`../../compose/debezium/application.properties`](../../compose/debezium/application.properties),
mounted read-only at **`/debezium/config`** — see CP8; the image's own error message names the
wrong directory. Four settings there are traps rather than preferences:

| Setting | Why |
|---|---|
| `snapshot.mode=no_data` | The default `initial` replays **every existing row** as an insert on first connect, so a rehearsal's worth of batch history lands on the topic the moment the container starts. Older Debezium spells this `never` |
| `publication.autocreate.mode=disabled` | Left on, Debezium helpfully creates a `FOR ALL TABLES` publication when it cannot find one — putting every LIMS write onto the backbone as `mechanism=cdc` |
| `slot.name=icc26_debezium` | Matches the `pg_drop_replication_slot` hint in `04-cdc.sql`. A slot left behind by a removed subscriber pins WAL forever, and `docker compose down` does not drop it — the slot lives in `pgdata`, not in the container |
| offsets on `debezium-data` | Without the volume the container forgets its WAL position on restart, and the stop-click-restart demo produces nothing instead of catching up. That catch-up **is** the demo |

**No `unwrap` transform.** Keeping `op` and `source.lsn` is what lets the wire show the log
position the event was decoded from, and what lets the sink reject an `UPDATE` instead of
republishing edited history.

### The sink is HTTPS on `:8043`, and it has to be

`http://ignition:8088/...` returns a **302** to `:8043`, and Debezium's `java.net.http.HttpClient`
defaults to `followRedirects(NEVER)` — so every event exhausts its retries on the redirect and the
connector dies with *"Exceeded maximum number of attempts to publish event"*, a message naming
neither the redirect nor the status code. It looks like a sink bug and is a transport one.

`docker-compose.yml` imports `ignition/certificates/icc26-ignition.crt` into a JKS **at container
start** rather than baking it into an image — `tasks.py seed` re-mints that certificate, so a
runtime import always picks up the current one instead of needing a rebuild — and passes it in
`JAVA_TOOL_OPTIONS`.

`-Djdk.internal.httpclient.disableHostnameVerification=true` goes with it, and it is not
laziness: the restored `ssl.pfx` has `SAN: DNS:localhost, IP:127.0.0.1` and does not name
`ignition`, which is the host Debezium dials. The signature is still verified against the mounted
cert. **The LIMS makes the same trade for the same reason** —
[`../../services/lims/README.md`](../../services/lims/README.md) § *TLS*. Reminting the keystore
with the wider SAN list already in `tasks.py` retires both at once.

**The publication names `bes.batch_event` and nothing else.** It used to also name
`lims.sample_result`, from the abandoned design where patterns 4, 5 and 6 all carried one LIMS
row. Tailing it would deliver a single analyst review twice, under two different mechanisms.
`tasks.py health` now asserts the publication's membership so it cannot drift back.

## Payload contract

```json
{
  "ts": "2026-08-26T19:12:04.318Z",
  "seq": 7,
  "source": { "id": "bes", "type": "bes" },
  "meta": {
    "mechanism": "cdc",
    "ingest_ts": "2026-08-26T19:12:04.402Z"
  },
  "values": {
    "batch_id": "B-2026-0142",
    "equipment_id": "br-201",
    "event_type": "operation_start",
    "operation": "GROWTH",
    "qualified_window": true
  }
}
```

- **`source.id` is `bes`, not an area.** There is no `bes` area in the namespace and there is not
  going to be — an area is a place, and a BES is software. The batch event happens in a suite, so
  it publishes under the cell that produced it and names its source system in the payload.
  `id == type`, matching pattern 4's `{"id": "lims", "type": "lims"}`.
- **`seq` is `bes.batch_event.id`** — the database row id, exactly as pattern 4 uses its outbox
  id. Durable and monotonic. An in-memory counter (which this had in its first revision) restarts
  at 1 on every gateway restart and tells a subscriber nothing.
- **`meta` carries the three documented keys and no more.** An earlier revision added `meta.op`
  and `meta.lsn`, on the argument that the log position is the one field no other mechanism can
  produce. Removed 2026-08-26: no other pattern extends `meta`, and **a payload that advertises
  its own transport is a strange thing for a demo whose claim is that a subscriber cannot tell how
  anything arrived.** The LSN is still logged by `bes_cdc` and by Debezium, which is where somebody
  investigating would look for it.
- **`meta.correlation_id` is absent.** Pattern 5 has nothing to correlate to; pattern 7 joins it
  by time, not by id.
- **`ts` is milliseconds**, like every other pattern, though Debezium hands over six-digit
  microseconds. `_to_millis` trims it rather than letting pattern 5 be the one message on the bus
  with a different precision.
- **Retain is false.** A retained batch event replays a stale operation to every reconnecting
  subscriber and presents it as current — the same hazard
  [`04-lims-webhook.md`](04-lims-webhook.md) documents for the valve's `sample-complete`. The
  current operation is what the tag is for.

### Sink behaviour

| | |
|---|---|
| valid token, `op="c"` | `200`, publishes |
| missing or wrong token | `401`, no publish |
| `op != "c"` | `200`, **no publish**, and the body says which op it was |
| unparseable body / no `after` / no `equipment_id` | `400`, no publish |

`bes.batch_event` is append-only, so an `UPDATE` or `DELETE` reaching the sink means somebody is
editing history. Rejecting it visibly is a better answer on stage than filtering it away.

**Auth is a query-string token, not a header.** Debezium Server's custom-header support is
version-dependent, and a demo should not be one image bump away from silently unauthenticated
POSTs. `TOKEN` in `bes_cdc` and `CDC_SINK_URL` in `.env` have to agree.

## Checkpoints

Record the answers here as they come in — several of these are guesses until measured.

| CP | Check | Result |
|---|---|---|
| **0** | `ICC26` datasource | **Pass.** Created UI-first; `bes_batch` writes through it |
| **1** | Schema + publication | **Pass**, after `migrate-06` — which had to grow a step 0 renaming `mes` → `bes`, because that 2026-08-23 rename had never reached a running volume |
| **2** | Tag event script resolves `bes_batch` | **Pass.** The Gateway Scripting Project setting was the answer, and `eventScripts` on a **memory** tag works — the one prior example in the repo was an OPC tag |
| **3** | Row shape per click | **Pass.** Row 1 was a lone `operation_start`/`CIP`; rows 2+3 shared `occurred_at 16:36:43.199` exactly as designed |
| **4** | No snapshot burst; slot active | **Pass.** `snapshot.mode=no_data` held — the 7 pre-existing rows did not replay; slot `icc26_debezium` active, pgoutput |
| **5** | **What Debezium actually sends** for `occurred_at` (timestamptz) and `payload` (jsonb) | **Measured 2026-08-26, and the timestamp guess was wrong.** `occurred_at` arrives as an **ISO-8601 string with 6-digit microseconds** — `"2026-08-26T21:39:15.740000Z"` — not int64 micros. `payload` arrives as a **JSON string** as predicted: `"{\"qualified_window\": false}"`. `_timestamp` handles it (the `int()` raises, and it passes the string through) and `_decode_payload` handles the second decode. Note the wire then carries `.740000Z` where the envelope contract's example shows milliseconds; harmless for parsing, cosmetically inconsistent |
| **6** | `qualified_window` polarity | **Pass.** Row 7 `GROWTH`/`operation_start` → `true`; row 6 `INOC`/`operation_end` and row 8 `GROWTH`/`operation_end` → `false` |
| **7** | `?::jsonb` cast through pgjdbc | **Pass.** No `stringtype=unspecified` needed; rows carry `{"qualified_window": false}` |
| **8** | Debezium's config mount path — `/debezium/conf` vs `/debezium/config` | **`/debezium/config`.** And the image's own error message says the other one: *"check you have a correct Debezium server config in **/debezium/conf**/application.properties"* while Quarkus is demonstrably scanning `/debezium/config` (it warns about the `.example` files it finds there). Mounted at `conf/` the container starts, reads nothing, and dies on a missing `debezium.sink.type` in a restart loop. **Do not trust that error message.** Measured 2026-08-26 |
| **9** | Plain HTTP `:8088` to the sink | **It bites. Measured 2026-08-26.** `POST http://ignition:8088/system/webdev/icc-2026/cdc-sink` returns **302** to `https://ignition:8043/...`, and Debezium's `java.net.http.HttpClient` defaults to `followRedirects(NEVER)`. Every event exhausts its retries at the 302 and the connector dies with *"Exceeded maximum number of attempts to publish event"* from `HttpChangeConsumer.handleBatch` — a message that names neither the redirect nor the status code. **Resolved with a runtime JKS import + HTTPS on :8043**, § *The sink is HTTPS on `:8043`* |

## Verification

```powershell
python tasks.py up
python tasks.py health
```

Watcher in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

In Tag Explorer, on `[default]icc26/site1/upstream/bioreactors/br-201/batch_data/`:

1. Set `batch_id` to `B-2026-0142`.
2. Click `manual_advance`. → `operation` reads `CIP`, `manual_advance` back to `false`, **one**
   message on `batch/event` with `qualified_window: false`.
3. Click three more times → `GROWTH`. Two messages per click from here on. The
   `operation_start` of `GROWTH` is the only one carrying `qualified_window: true`.

```bash
docker exec icc26-postgres psql -U postgres -d icc26 -c \
  "SELECT id, event_type, operation, payload, occurred_at FROM bes.batch_event ORDER BY id;"
```

**The failure demo — run it, it is half the segment:**

```bash
docker stop icc26-debezium     # click twice: rows land, the watcher is silent
docker start icc26-debezium    # the missed changes arrive
```

The reactor kept cycling and the topic went quiet. Nothing failed, nothing alarmed, and the only
symptom was an absence. Then the slot and the offsets file hand back everything that happened
while nobody was listening — **that durability is the CDC argument**, and it is what a polling
consumer (pattern 6) cannot claim.

**Negative check:**

```bash
docker exec icc26-postgres psql -U icc26 -d icc26 -c \
  "UPDATE bes.batch_event SET batch_id = batch_id WHERE id = 1;"
```

The sink returns `200` with `"published": false, "op": "u"`. Nothing on the wire.

**Namespace check:** read `icc26/site1/upstream/br-201/batch/event` to somebody who has not seen
the build and ask which mechanism it uses. Nothing in the topic string says CDC; `meta.mechanism`
is the only tell.

**Rehearsal path for pattern 7:** advance to `GROWTH`, badge the valve on :8085 → the sample is
inside the qualified window. Advance to `HARVEST` and badge again → it is not.

## Open items

1. **Nothing is broker-verified yet.** Every CP above is `pending`. Until CP2 and CP5 are
   answered, treat the Gateway Scripting Project dependency and the Debezium type encodings as
   assumptions.
2. ~~**`../talk-tracks/05-cdc.md` is not written.**~~ **Written 2026-09-06.** It carries the
   accidental failure demo out of the progress log below, on the grounds that it is the strongest
   evidence in this pattern and nobody staged it.
3. **`pg_db` is still in the repo** beside `ICC26`, still selectable, still wrong. Decide whether
   it is deleted.
4. **`plant.equipment` holds `BR-201` while topics and `bes.batch_event` hold `br-201`.** The rule
   in [`../00-architecture.md`](../00-architecture.md) says those should be the same string. Not
   fixed here; fix it in one pass across the seed, the topics and pattern 7's joins, or drop the
   rule.
5. **The `bes.batch_event` store is pattern 7's, not just pattern 5's.** Whatever 07 uses for
   pattern 6's readings should probably match this shape rather than invent a second one.

## Progress log

**2026-08-26 — broker-verified end to end.** Writer → `bes.batch_event` → Debezium → `cdc-sink` →
Transmission → `icc26/site1/upstream/br-201/batch/event`, confirmed in the gateway log:

```
16:48:36  bes_cdc: cdc sink published operation_end/GROWTH    (lsn 81302288)
16:48:38  bes_cdc: cdc sink published operation_start/HARVEST (lsn 81304288)
```

Slot `icc26_debezium` active, caught up to within a couple of KB of `pg_current_wal_lsn()`.

**Those two messages arrived as an unplanned failure demo, and it is the best evidence in this
file.** They were written while the sink was still broken, sat undelivered behind an un-flushed
offset through three container restarts, and were delivered the moment the transport was fixed —
with their original LSNs. Nobody re-ran anything. **That is the property CDC has and polling does
not**, and it happened by accident before the scripted version was ever run.

Three checkpoints failed before it worked, all transport, none design: the config mount path
(CP8), the HTTP→HTTPS redirect (CP9), and — twice — a stale container, because `docker compose up
-d` does not recreate on a bind-mount path change reliably enough to trust. **`--force-recreate`
is the habit to keep.**

**2026-08-26 — found: the `mes.` → `bes.` rename had never reached a running volume.** Checking
the live database before writing the runbook turned up a `mes` schema, an `mes.batch_event` with
a `phase` column, and a publication naming it — three days after the rename was recorded as done
in `02-schema.sql`, `00-architecture.md` and `00-status.md`. `initdb/` runs on an empty volume
only, and nobody wrote the migration. `migrate-06` step 0 now does it. The general lesson is in
[`../00-architecture.md` § *Postgres*](../00-architecture.md): an initdb edit is not a change to
any running database.

**2026-08-26 — built, not yet run.** Schema renamed (`phase` → `operation`) and given
`equipment_id`; publication narrowed to `bes.batch_event`; `batch_data` finished on the
`bioreactor` UDT with the `valueChanged` trigger; `bes_batch` and `bes_cdc` written; `cdc-sink`
WebDev resource added; Debezium wired into compose with a named offsets volume; `tasks.py health`
gained a publication-membership assertion, a Debezium check, a replication-slot check, and the
`'mes'` → `'bes'` fix that had been silently passing since the rename. **Outstanding before any
of it runs: the `ICC26` datasource and the Gateway Scripting Project, both UI-first.**
