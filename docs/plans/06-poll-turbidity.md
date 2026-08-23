# 06 — Poll / watermark of the turbidity meter's database

> **Supersedes the pattern-6 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> Written **2026-08-23.** The MET ONE particle counter (Modbus TCP, rotating buffer) is not
> the source. That choice is reversed, on purpose — see below.
>
> Shares the database specified in [`05-cdc-turbidity.md`](05-cdc-turbidity.md). Vendor API
> still TBD; the poll contract is an incrementing identity column, not a vendor register map.
>
> Talk track (draft): [`../06-poll-turbidity.md`](../06-poll-turbidity.md).
>
> **Build the simulator and database with pattern 5 first.** This spec is the Ignition poll
> path on top. Do not invent a second writer.

| | |
|---|---|
| **Pattern** | 6 of 7 — poll, because the system of record will not call you |
| **Mechanism tag** | `meta.mechanism = "poll"` |
| **Depends on** | pattern 5's database + `sim-turbidity` (not on Debezium) |
| **Blocks** | nothing |
| **Pairs with** | [`05-cdc-turbidity.md`](05-cdc-turbidity.md) — same table, same topic, `cdc` |
| **Nuke?** | no extra nuke. Uses the volume pattern 5 already rebuilt |

## Objective

Ignition polls the turbidity meter's local database with a **high-water mark on `reading.id`**,
and publishes new rows onto the backbone with `meta.mechanism = "poll"`.

Same catalog, same table, same topic as pattern 5. Different mechanism.

## Talk point

**Polling is what you do when the system of record will not call you.** Pattern 5 tails the
WAL. Pattern 6 is the integration most of the room has actually shipped: a timer, a
`WHERE id > :last_id`, a tag that remembers the watermark.

The failure is a stalled loop, not a wrapping Modbus buffer. Rows accumulate in the instrument
database; when the poll resumes you either catch up (and are late) or you jump the watermark
(and you dropped data). Show the gap. CDC, running beside it, did not drop them — that is the
comparison, and it is why these two patterns share a source.

## Why this reversed the 2026-08-19 turbidity rejection

The master plan chose a MET ONE particle counter over a turbidity meter because turbidity is a
continuous value with a deadband, and deadband-on-poll overlaps Sparkplug report-by-exception.
That argument was about **the signal**. The poll problem we actually need is **the store**: a
vendor database you do not own, an index you watermark, and a timer you can stall.

The identity column is the indexed buffer. Pattern 5 is the other way out of the same store.
Together they are a real Monday-morning choice. The particle counter's Rotate Buffer checkbox
was a good aside; it is not this talk any more.

This is a **partial** walk-back of "nobody tails and polls the same table." We still do not
webhook, tail, *and* poll one LIMS table. We tail and poll one instrument database, because
that is the decision CDC vs poll is about.

## The chain

```
sim-turbidity ──INSERT──▶ database `turbidity`.reading
                              │
                              │  JDBC, user `ignition`, SELECT only
                              ▼
                    Ignition timer script
                    WHERE id > {watermark}
                              │
                              ▼
                    Transmission  (ign-transmission)
                              │
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: poll)
```

No new container. The writer is pattern 5's.

## Decisions

**Catch-up on resume, not a jump.** Default implementation: when the timer starts again it
publishes every row with `id > watermark`, in order. That is late, not lost. The jump
(`watermark = max(id)` without publishing) is a one-line alternative — keep it as a commented
branch or a memory-tag flag `jump_on_resume` so the talk can show both.

**Watermark is a memory tag.** `[default]icc26/site1/downstream/tff-301/turbidity-01/poll_watermark`
(Int8). Loss on gateway restart is itself a talkable failure: the next poll either replays
from 0 (duplicates, at-least-once) or, if you re-seed the tag from `max(id)`, you skip
whatever landed while the gateway was down. Do **not** persist it in `icc26` for v1; a small
table would hide the failure. Record as-built if a durable tag turns out to be less noisy
than the demo wants.

**Datasource `TURBIDITY`**, not `ICC26`. URL `jdbc:postgresql://postgres:5432/turbidity`,
user `ignition` (SELECT already granted in `05-turbidity.sql`). The existing `pg_db`
connection points at database `postgres` and is leftover; do not reuse it. UI first, then
commit.

**Timer, not Modbus.** Gateway Timer (or Event Stream scheduled source if that schema is
pleasant). Period **2 s** — slow enough to see on stage, fast enough that a 15 s stall
drops a handful of rows. Unknown gateway schemas: UI first.

**Publish through Transmission**, envelope matching pattern 5 except `mechanism=poll`.
`meta.ingest_ts` is the poll instant; `ts` is the row's `ts`. The lag is the point.
`seq` and `meta.correlation_id` are `id`, same as CDC, so two colours join on one row.

**Batch the query, publish one message per row.** `SELECT id, ts, ntu, status FROM reading WHERE id > ? ORDER BY id`. Cap at 100 rows per tick so a long stall does not freeze the
gateway. Leftover rows drain on subsequent ticks — that burst *is* the catch-up visual.

**Stall is a memory tag**, `poll_enabled` (Boolean, default true). The timer always fires;
the script returns immediately when the flag is false. Disabling the timer resource from
the UI also works but is slower on stage.

**Do not use a Modbus device connection.** That was the particle-counter plan.

## Build order

1. Pattern 5 foundation is up: rows appearing in `turbidity.reading`.
2. JDBC datasource `TURBIDITY` in the UI. Test query in the gateway: `SELECT max(id) FROM reading`.
3. Memory tags for watermark and enable flag. Script module `poll_turbidity`.
4. Gateway Timer 2 s → `poll_turbidity.tick()`.
5. Failure demo against live CDC (spec 05 checkpoint 7).
6. Talk track + status.

## Files to change

| Path | What |
|---|---|
| Ignition datasource `TURBIDITY` | **UI first.** `jdbc:postgresql://postgres:5432/turbidity`, user `ignition`, password `ignition`. Commit `ignition/config/resources/core/ignition/database-connection/TURBIDITY/` (name as written by the gateway) |
| `ignition/projects/icc-2026/ignition/script-python/poll_turbidity/code.py` | poll loop, envelope, Transmission publish |
| Memory tags | `[default]icc26/site1/downstream/tff-301/turbidity-01/poll_watermark` (Int8, 0), `…/poll_enabled` (Boolean, true). Author as files if the tag JSON is known; otherwise UI then commit |
| Gateway Timer `poll-turbidity` | **UI first.** 2 s, delay 0, `poll_turbidity.tick()`. If Event Stream has a scheduled source that is less awkward, use that and note the choice as-built |
| `services/sim-turbidity/` | already required by pattern 5; no extra output |

No MQTT user to add. Publish is `ign-transmission`, which already has `icc26/#`.

### Script (sketch)

Jython 2.7. Copy `_iso` from `opcua_event`. Datasource name must match whatever the UI
saved — `TURBIDITY` is the name we ask for.

```python
# ignition/projects/icc-2026/ignition/script-python/poll_turbidity/code.py

LOGGER_NAME = "poll_turbidity"
DATASOURCE = "TURBIDITY"
BROKER = "chariot_broker"
TOPIC = "icc26/site1/downstream/tff-301/turbidity-01/telemetry"
MECHANISM = "poll"
SOURCE_ID = "turbidity-01"
SOURCE_TYPE = "turbidity-meter"
BATCH = 100

WATERMARK_TAG = "[default]icc26/site1/downstream/tff-301/turbidity-01/poll_watermark"
ENABLED_TAG = "[default]icc26/site1/downstream/tff-301/turbidity-01/poll_enabled"

def tick():
    logger = system.util.getLogger(LOGGER_NAME)
    enabled = system.tag.readBlocking([ENABLED_TAG])[0]
    if enabled.value is False:
        return
    mark = system.tag.readBlocking([WATERMARK_TAG])[0]
    last_id = int(mark.value or 0)

    py = system.db.runPrepQuery(
        "SELECT id, ts, ntu, status FROM reading WHERE id > ? ORDER BY id LIMIT ?",
        [last_id, BATCH],
        DATASOURCE,
    )
    if py is None or py.rowCount == 0:
        return

    new_mark = last_id
    for row in py:
        rid = int(row["id"])
        envelope = {
            "ts": _iso(row["ts"]),
            "seq": rid,
            "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
            "meta": {
                "mechanism": MECHANISM,
                "ingest_ts": _iso(),
                "correlation_id": str(rid),
            },
            "values": {
                "id": rid,
                "ntu": float(row["ntu"]),
                "status": row["status"] or "ok",
            },
        }
        payload = system.util.jsonEncode(envelope)
        system.cirruslink.transmission.publish(BROKER, TOPIC, payload, 1, False)
        new_mark = rid
    system.tag.writeBlocking([WATERMARK_TAG], [new_mark])
```

`system.cirruslink.transmission.publish` is what the retired LIMS webhook used. If 8.3.8
wants `system.cirruslink.transmission.publish` under a slightly different path, copy the
call from `lims_webhook/code.py` exactly — that path is proven on this gateway.

`row["ts"]` may already be a Java Date; `_iso` must accept that. If `runPrepQuery` returns
column indexes not names, use `row[0]…row[3]` and note it as-built.

### Jump-on-resume (optional, one line)

```python
# if system.tag.readBlocking(["…/poll_jump"])[0].value:
#     hi = system.db.runScalarQuery("SELECT COALESCE(max(id),0) FROM reading", DATASOURCE)
#     system.tag.writeBlocking([WATERMARK_TAG], [int(hi)])
#     return
```

Show catch-up first. Then flip the flag, stall, resume, and the ids skip. CDC still has
them.

## Ignition resources

| Resource | How |
|---|---|
| JDBC `TURBIDITY` | Config → Databases → Connections. Postgres, URL above, user `ignition` / `ignition`, validation `SELECT 1`. **UI first** — password is Embedded ciphertext, same as `pg_db` |
| Memory tags | tag provider `default`, folder `icc26/site1/downstream/tff-301/turbidity-01/`. Known format: edit `tag-definition` JSON if you are sure; otherwise UI |
| Script `poll_turbidity` | files + `python tasks.py scan` |
| Gateway Timer | Config → Gateway Events → Timer. 2 s. UI first. Do not attach this to a Perspective session |

Existing `pg_db` (`jdbc:postgresql://postgres:5432/postgres`) stays. It is not this
pattern. Status already notes `ICC26` on `icc26` is leftover.

## MQTT user + topics

None new. Topic:

```
icc26/site1/downstream/tff-301/turbidity-01/telemetry
```

Same string as pattern 5. `ign-transmission` already may publish `icc26/#`.

## Envelope

Identical to pattern 5 except `mechanism`.

```json
{
  "ts": "2026-08-23T14:03:22.145Z",
  "seq": 41,
  "source": { "id": "turbidity-01", "type": "turbidity-meter" },
  "meta": {
    "mechanism": "poll",
    "ingest_ts": "2026-08-23T14:03:24.010Z",
    "correlation_id": "41"
  },
  "values": {
    "id": 41,
    "ntu": 4.12,
    "status": "ok"
  }
}
```

`ingest_ts` minus `ts` is the poll lag. On a 2 s timer it is ~0–2 s. After a stall it is
the stall length. That is the slide.

## Empirical checkpoints

**1 — JDBC answers.** Gateway Status → Databases → `TURBIDITY` valid. Query `SELECT count(*) FROM reading` returns a growing number while the sim runs.

**2 — Poll with Debezium off.** Stop `icc26-debezium` (and the mapper). Let the sim run.
Poll publishes `mechanism=poll`, ids contiguous, `seq` matches `values.id`.

**3 — Stall, catch-up.** Set `poll_enabled` false. Watch ids advance in `psql`. Set it true.
A burst of late messages, ids contiguous with the last pre-stall id, `ingest_ts` clustered
now, `ts` spread across the stall. **No gaps.**

**4 — Stall, jump.** Flip `poll_jump` (or run the commented branch). Repeat 3. Ids skip.
Say: that is the other implementation, and it is a one-line change.

**5 — CDC still had them.** With Debezium on during the stall, the missing poll ids are on
the same topic as `mechanism=cdc`. Two colours, one address. A subscriber cannot tell from
the topic which is which.

**6 — Gateway restart.** Restart Ignition. Memory watermark is 0. Next tick replays from
the beginning (duplicates) unless you re-seed. That is at-least-once, and it is honest.
Do not "fix" it in v1.

**7 — SELECT only.** As `ignition`, `INSERT INTO reading …` must fail. If it succeeds, the
grant is wrong and the demo is lying about being an observer.

## Verification (copy-paste)

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' -v
```

Filter on `mechanism` with `jq` if the firehose is noisy:

```
... mosquitto_sub ... -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' `
  | findstr poll
```

## Closing step

Write [`../06-poll-turbidity.md`](../06-poll-turbidity.md) as the as-built talk track. Update
architecture (JDBC `TURBIDITY` row — status already expected it), `00-status.md`, and the
firehose runbook: the failure is a stalled watermark, not a wrapping particle-counter
buffer. Deviations table: timer vs Event Stream scheduled source, memory vs durable
watermark, named query vs `runPrepQuery`, catch-up vs jump as the default.
