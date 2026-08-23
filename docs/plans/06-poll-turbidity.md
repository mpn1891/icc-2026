# 06 — Poll / watermark of the turbidity meter's data-management database

> **Supersedes the pattern-6 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> First written 2026-08-23 against a placeholder schema. **Re-sourced the same day against the real
> vendor documentation**: the instrument is an Anton Paar **Haze 3001** and its data lands in
> **AP Connect**. The MET ONE particle counter (Modbus TCP, rotating buffer) is not the source; that
> choice is reversed on purpose — see below.
>
> **Vendor reference:** [`../reference/apconnect-haze3001-model.md`](../reference/apconnect-haze3001-model.md).
> Read it before this file.
>
> Shares the database specified in [`05-cdc-turbidity.md`](05-cdc-turbidity.md). The poll contract is
> an incrementing identity column, not a vendor register map.
>
> Talk track (draft): [`../06-poll-turbidity.md`](../06-poll-turbidity.md).
>
> **Build the simulator and database with pattern 5 first.** This spec is the Ignition poll path on
> top. Do not invent a second writer.

| | |
|---|---|
| **Pattern** | 6 of 7 — poll, because the system of record will not call you |
| **Mechanism tag** | `meta.mechanism = "poll"` |
| **Depends on** | pattern 5's database + `sim-apconnect` (not on Debezium) |
| **Blocks** | nothing |
| **Pairs with** | [`05-cdc-turbidity.md`](05-cdc-turbidity.md) — same table, same topic, `cdc` |
| **Nuke?** | no extra nuke. Uses the volume pattern 5 already rebuilt |

## Objective

Ignition polls AP Connect's database with a **high-water mark on `measurement.measurement_no`**, and
publishes new measurements onto the backbone with `meta.mechanism = "poll"`.

Same catalog, same table, same topic as pattern 5. Different mechanism.

## Talk point

**Polling is what you do when the system of record will not call you.** Pattern 5 tails the WAL.
Pattern 6 is the integration most of the room has actually shipped: a timer, a
`WHERE measurement_no > :last`, and a tag that remembers the watermark.

The failure is a stalled loop, not a wrapping Modbus buffer. Measurements accumulate in the vendor's
database; when the poll resumes you either catch up (and are late) or you jump the watermark (and
you dropped data). Show the gap. CDC, running beside it, did not drop them — that is the comparison,
and it is why these two patterns share a source.

## Why this reversed the 2026-08-19 turbidity rejection

The master plan chose a MET ONE particle counter over a turbidity meter because turbidity is a
continuous value with a deadband, and deadband-on-poll overlaps Sparkplug report-by-exception. That
argument was about **the signal**. The poll problem we actually need is **the store**: a vendor
application you do not own, an index you watermark, and a timer you can stall.

The vendor docs make this stronger than the placeholder did. A Haze 3001 does not stream a
continuous value at you at all — it produces a *discrete completed measurement*, which AP Connect
files with a strictly consecutive number. There is no deadband question left. The identity column is
the indexed buffer, and pattern 5 is the other way out of the same store.

This is a **partial** walk-back of "nobody tails and polls the same table." We still do not webhook,
tail, *and* poll one LIMS table. We tail and poll one vendor application, because that is the
decision CDC vs poll is actually about.

## What the vendor docs changed

| | First draft | This spec |
|---|---|---|
| Datasource | `TURBIDITY` → database `turbidity` | **`APCONNECT`** → database `apconnect` |
| Table | `reading` | `measurement` |
| Watermark column | `id` | **`measurement_no`** — AP Connect's own *"strictly, consecutively increasing"* item number |
| Correlation id | the integer | **the GUID** `measurement.id`. AP Connect's global identifier, and it survives a counter reset |
| Values | `ntu` column | `result_values` **jsonb**, the vendor's `Variant` array, projected in the script |
| Status | `ok`/`hold`/`fault` | `WellKnownMeasurementStatus`; `CANCELED` / `FAILURE` carry no reading |

**A real alternative was considered and rejected.** AP Connect exposes
`GET /api/v1/measurements/completed?apc_FromMeasurementCompletionNo=N`, plus a cheap
`/completed/latest` returning a `dataRevision` the vendor says to compare *for inequality* every few
seconds. That is a REST poll with a vendor-supplied watermark, and it is what you would build
against a real AP Connect. **Decided 2026-08-23 to poll the database over JDBC instead**, so that
patterns 5 and 6 sit on exactly the same rows and the CDC-versus-poll comparison has no other
variable in it. Record it as a deviation and say the API exists if anyone asks — "we would use their
API in production; here it would have made the two patterns incomparable" is a good answer.

## The chain

```
sim-apconnect ──INSERT──▶ database `apconnect`.measurement
                              │
                              │  JDBC, user `ignition`, SELECT only
                              ▼
                    Ignition timer script
                    WHERE measurement_no > {watermark}
                              │
                              ▼
                    Transmission  (ign-transmission)
                              │
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: poll)
```

No new container. The writer is pattern 5's.

## No gateway UI

**Decided 2026-08-23: everything in this pattern is authored as files.** No "UI first, then commit"
steps. Two of the three Ignition resources have a known on-disk shape; the third does not, and that
is the one risk in this spec.

| Resource | On-disk shape | Confidence |
|---|---|---|
| Database connection `APCONNECT` | `database-connection/pg_db/` is committed and works | **High.** Copy it |
| Memory tags | `tag-definition/default/icc26/site1/...` is committed and works | **High.** Copy the folder pattern |
| Gateway timer | `ignition/projects/icc-2026/ignition/timer/` **exists but is empty** | **Low.** Inferred |

Apply with `python tasks.py scan`, not a restart.

### The datasource password trick

`pg_db/config.json` stores its password as an `Embedded` ciphertext blob, and its user is `ignition`.
Pattern 6 needs the **same credential** — `05-apconnect.sql` grants SELECT on `apconnect` to that
same `ignition` role. The blob is decrypted with the gateway's own key, and `pg_db` is committed and
survives a clone-and-seed, so **copy the entire `password` object verbatim** into the new connection
and it decrypts to the same password.

Do not invent a ciphertext, do not put a plaintext password in the file, and do not switch the
connection to a different database user just to avoid this.

If the copied blob does not decrypt on some gateway, that is the one place this spec falls back to a
UI step: retype the password in Config → Databases → Connections, then commit what changed. Record
it as-built.

## Decisions

**Catch-up on resume, not a jump.** Default implementation: when the timer starts again it publishes
every measurement with `measurement_no > watermark`, in order. That is late, not lost. The jump
(`watermark = max(measurement_no)` without publishing) is a one-line alternative — keep it as a
memory-tag flag `poll_jump` so the talk can show both.

**Watermark is a memory tag.**
`[default]icc26/site1/downstream/tff-301/turbidity-01/poll_watermark` (Int8). Loss on gateway restart
is itself a talkable failure: the next poll either replays from 0 (duplicates, at-least-once) or, if
you re-seed the tag, you skip whatever landed while the gateway was down. Do **not** persist it in
`icc26` for v1; a small table would hide the failure. Record as-built if a durable tag turns out to
be less noisy than the demo wants.

**Datasource `APCONNECT`**, not `ICC26`. URL `jdbc:postgresql://postgres:5432/apconnect`, user
`ignition`. The existing `pg_db` connection points at database `postgres` and is leftover; do not
reuse it and do not delete it.

**Timer, not Modbus.** Period **2 s** — slow enough to see on stage, fast enough that a 15 s stall
drops a handful of measurements.

**Publish through Transmission**, envelope matching pattern 5 except `mechanism=poll`.
`meta.ingest_ts` is the poll instant; `ts` is `completed_ts`. The lag is the point. `seq` is
`measurement_no` and `meta.correlation_id` is the **GUID**, same as CDC, so two colours join on one
measurement.

**Project the Variants in the script, with the same map as the mapper.** `result_values` arrives as
a jsonb string over JDBC. Decode it, walk it, and emit the same flat keys `cdc-mapper` emits. The
two documents must be indistinguishable except for `meta.mechanism` and `meta.ingest_ts`. Keep the
map in one dict at the top of the module so a vendor-id correction is a single edit in each of the
two places it exists.

**Absent stays absent.** A `CANCELED` or `FAILURE` measurement publishes `status` and **no** haze
keys. Never `0`. Same rule as patterns 3, 4 and 5.

**Batch the query, publish one message per row.** Cap at 100 per tick so a long stall does not freeze
the gateway. Leftover rows drain on subsequent ticks — that burst *is* the catch-up visual.

**Stall is a memory tag**, `poll_enabled` (Boolean, default true). The timer always fires; the script
returns immediately when the flag is false.

**Do not use a Modbus device connection.** That was the particle-counter plan.

**Do not call the AP Connect REST API from this pattern.** It is not simulated, and the reason for
JDBC is written above.

## Build order

1. Pattern 5 foundation is up: rows appearing in `apconnect.measurement`.
2. Datasource `APCONNECT` authored as files. `python tasks.py scan`. Gateway Status → Databases shows
   it valid.
3. Memory tags authored as files. Script module `poll_turbidity`.
4. Gateway timer authored as files, 2 s → `poll_turbidity.tick()`.
5. Failure demo against live CDC (spec 05 checkpoint 9).
6. Talk track + status.

## Files to change

| Path | What |
|---|---|
| `ignition/config/resources/core/ignition/database-connection/APCONNECT/config.json` | **new.** Copy `pg_db/config.json`; change `connectURL` to `jdbc:postgresql://postgres:5432/apconnect`; keep `username: ignition` and the `password` blob **verbatim** |
| `ignition/config/resources/core/ignition/database-connection/APCONNECT/resource.json` | **new.** Copy `pg_db/resource.json`; drop the `uuid` / `lastModificationSignature` attributes rather than reusing `pg_db`'s |
| `ignition/config/resources/core/ignition/tag-definition/default/icc26/site1/downstream/unary-resource.json` | **new.** Empty folder resource, `"files": []` |
| `…/downstream/tff-301/unary-resource.json` | **new.** Empty folder resource |
| `…/downstream/tff-301/turbidity-01/unary-resource.json` | **new.** `"files": ["tags.json"]` |
| `…/downstream/tff-301/turbidity-01/tags.json` | **new.** The three memory tags |
| `ignition/projects/icc-2026/ignition/script-python/poll_turbidity/code.py` | **new.** Poll loop, Variant projection, envelope, Transmission publish |
| `ignition/projects/icc-2026/ignition/script-python/poll_turbidity/resource.json` | **new.** Copy the shape from `opcua_event/resource.json` |
| `ignition/projects/icc-2026/ignition/timer/poll-turbidity/` | **new, inferred shape.** 2 s, `poll_turbidity.tick()` |
| `services/sim-apconnect/` | already required by pattern 5; no extra output |

No MQTT user to add. Publish is `ign-transmission`, which already has `icc26/#`.

### Memory tags

Folder `default` provider, path `icc26/site1/downstream/tff-301/turbidity-01/`. Mirror the committed
`tag-definition/default/icc26/site1/qc/analyzers/` pattern: one `unary-resource.json` per folder
level, and a JSON array of tag objects in the leaf.

```json
[
  { "name": "poll_watermark", "tagType": "AtomicTag", "valueSource": "memory",
    "dataType": "Int8", "value": 0 },
  { "name": "poll_enabled",   "tagType": "AtomicTag", "valueSource": "memory",
    "dataType": "Boolean", "value": true },
  { "name": "poll_jump",      "tagType": "AtomicTag", "valueSource": "memory",
    "dataType": "Boolean", "value": false }
]
```

The committed examples are all `UdtInstance`, so the plain-memory-tag keys above are **inferred**.
If `scan` rejects them, the fix is one tag created in the UI and then read back off disk — do that
once and correct this file.

### Script (sketch)

Jython 2.7. Copy `_iso` from `opcua_event`. No f-strings.

```python
# ignition/projects/icc-2026/ignition/script-python/poll_turbidity/code.py

LOGGER_NAME = "poll_turbidity"
DATASOURCE = "APCONNECT"
BROKER = "chariot_broker"
TOPIC = "icc26/site1/downstream/tff-301/turbidity-01/telemetry"
MECHANISM = "poll"
SOURCE_ID = "turbidity-01"
SOURCE_TYPE = "turbidity-meter"
BATCH = 100

TAG_ROOT = "[default]icc26/site1/downstream/tff-301/turbidity-01/"
WATERMARK_TAG = TAG_ROOT + "poll_watermark"
ENABLED_TAG = TAG_ROOT + "poll_enabled"
JUMP_TAG = TAG_ROOT + "poll_jump"

# Must stay identical to VARIANT_MAP in services/cdc-mapper/app.py. Two copies,
# on purpose -- no shared library across Jython and CPython -- so change both.
VARIANT_MAP = {
    "Haze/Haze":               "haze_ebc",
    "Haze/HazeNTU":            "haze_ntu",
    "Haze/S25S0":              "s25_s0",
    "Haze/S90S0":              "s90_s0",
    "Haze/AbsorbanceS0":       "absorbance_s0",
    "Density/CellTemperature": "cell_temperature_c",
}

def _project_values(raw):
    """Variant array -> flat dict. Absent stays absent; never substitute 0."""
    if raw is None:
        return {}
    if isinstance(raw, (str, unicode)):
        raw = system.util.jsonDecode(raw)
    out = {}
    for v in raw or []:
        key = VARIANT_MAP.get(v.get("id"))
        if key is None:
            continue
        val = v.get("value")
        if isinstance(val, dict):
            val = val.get("numeric")
        if val is not None:
            out[key] = float(val)
    return out

def tick():
    logger = system.util.getLogger(LOGGER_NAME)
    if system.tag.readBlocking([ENABLED_TAG])[0].value is False:
        return

    last = int(system.tag.readBlocking([WATERMARK_TAG])[0].value or 0)

    # The other implementation, one flag away: skip the backlog entirely.
    if system.tag.readBlocking([JUMP_TAG])[0].value:
        hi = system.db.runScalarQuery(
            "SELECT COALESCE(max(measurement_no), 0) FROM measurement", DATASOURCE)
        system.tag.writeBlocking([WATERMARK_TAG], [int(hi)])
        logger.info("poll_jump: watermark advanced to %d without publishing" % int(hi))
        return

    rows = system.db.runPrepQuery(
        "SELECT measurement_no, id, status, completed_ts, sample_name,"
        "       instrument_serial, result_values"
        "  FROM measurement WHERE measurement_no > ?"
        " ORDER BY measurement_no LIMIT ?",
        [last, BATCH], DATASOURCE)
    if rows is None or rows.rowCount == 0:
        return

    mark = last
    for row in rows:
        no = int(row["measurement_no"])
        guid = str(row["id"])
        values = {
            "measurement_no": no,
            "measurement_id": guid,
            "status": row["status"],
            "sample_name": row["sample_name"],
            "instrument_serial": row["instrument_serial"],
        }
        values.update(_project_values(row["result_values"]))

        envelope = {
            "ts": _iso(row["completed_ts"]),
            "seq": no,
            "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
            "meta": {
                "mechanism": MECHANISM,
                "ingest_ts": _iso(),
                "correlation_id": guid,
            },
            "values": _drop_nones(values),
        }
        system.cirruslink.transmission.publish(
            BROKER, TOPIC, system.util.jsonEncode(envelope), 1, False)
        mark = no

    system.tag.writeBlocking([WATERMARK_TAG], [mark])
```

`system.cirruslink.transmission.publish` is what the retired LIMS webhook used. If 8.3.8 wants a
slightly different path, copy the call from `lims_webhook/code.py` exactly — that path is proven on
this gateway.

`row["completed_ts"]` will likely be a `java.sql.Timestamp`; `_iso` must accept that as well as a
string. `row["result_values"]` will likely be a `String` holding JSON, but the PostgreSQL driver may
hand over a `PGobject` — call `str()` on it before decoding if `jsonDecode` complains. If
`runPrepQuery` returns column indexes rather than names, use `row[0]…row[6]` and note it as-built.

The `poll_jump` branch is deliberately **live code**, not a comment. It is a stage control: show
catch-up first, then flip the flag, stall, resume, and watch the numbers skip. CDC still has them.

### Gateway timer — the one inferred resource

`ignition/projects/icc-2026/ignition/timer/` exists in the committed tree and is empty, so the
resource type is right and the on-disk schema is unknown. Author
`ignition/projects/icc-2026/ignition/timer/poll-turbidity/` with a `code.py` calling
`poll_turbidity.tick()` and a `resource.json` mirroring the shape used by other project resources
(`scope`, `version`, `files`, `attributes`), plus whatever config file the pattern of sibling
resource types suggests — a `config.json` carrying delay, and a fixed-delay vs fixed-rate choice, is
the likely shape.

**Expect this one to need a correction.** It is the only resource here with no committed example.
Two fallbacks, in order of preference:

1. Create the timer once in Gateway → Config → Gateway Events, read what the gateway wrote to disk,
   and commit that — a one-time UI action that then makes the file authoritative forever.
2. Drive `tick()` from an Event Stream scheduled source if that schema turns out to be pleasanter,
   and note the choice as-built.

Do not attach this to a Perspective session.

## Ignition resources

| Resource | How |
|---|---|
| JDBC `APCONNECT` | **Files.** Copy `pg_db`, change the URL, keep the `password` blob verbatim |
| Memory tags | **Files.** Mirror `tag-definition/default/icc26/site1/qc/analyzers/` |
| Script `poll_turbidity` | Files + `python tasks.py scan` |
| Gateway timer | **Files, inferred.** See above |

Existing `pg_db` (`jdbc:postgresql://postgres:5432/postgres`) stays. It is not this pattern. Status
already notes that `ICC26` on `icc26` is leftover.

## MQTT user + topics

None new. Topic:

```
icc26/site1/downstream/tff-301/turbidity-01/telemetry
```

Same string as pattern 5. `ign-transmission` already publishes `icc26/#`.

## Envelope

Identical to pattern 5 except `mechanism` and `ingest_ts`.

```json
{
  "ts": "2026-08-23T14:03:22.145Z",
  "seq": 41,
  "source": { "id": "turbidity-01", "type": "turbidity-meter" },
  "meta": {
    "mechanism": "poll",
    "ingest_ts": "2026-08-23T14:03:24.010Z",
    "correlation_id": "6f0a1c2e-9b41-4d38-8a77-1f2b3c4d5e6f"
  },
  "values": {
    "measurement_no": 41,
    "measurement_id": "6f0a1c2e-9b41-4d38-8a77-1f2b3c4d5e6f",
    "status": "SUCCESS",
    "sample_name": "TFF-301 filtrate",
    "instrument_serial": "83012345",
    "haze_ebc": 4.12,
    "haze_ntu": 16.48,
    "s25_s0": 0.0132,
    "s90_s0": 0.0087,
    "absorbance_s0": 0.2147,
    "cell_temperature_c": 20.03
  }
}
```

`ingest_ts` minus `ts` is the poll lag. On a 2 s timer it is ~0–2 s. After a stall it is the stall
length. That is the slide.

## Empirical checkpoints

**1 — JDBC answers.** Gateway Status → Databases → `APCONNECT` valid. `SELECT count(*) FROM
measurement` returns a growing number while the sim runs. If the connection is faulted with an
authentication error, the copied password blob did not decrypt — see *The datasource password trick*.

**2 — Poll with Debezium off.** Stop `icc26-debezium` and `icc26-cdc-mapper`. Let the sim run. Poll
publishes `mechanism=poll`, `measurement_no` contiguous, `seq == values.measurement_no`.

**3 — The two documents match.** Turn Debezium back on. For one measurement, capture both messages
and diff them. Everything must be equal except `meta.mechanism` and `meta.ingest_ts`. Any other
difference is a projection bug in one of the two `VARIANT_MAP`s.

**4 — Stall, catch-up.** Set `poll_enabled` false. Watch `measurement_no` advance in `psql`. Set it
true. A burst of late messages, numbers contiguous with the last pre-stall one, `ingest_ts` clustered
now, `ts` spread across the stall. **No gaps.**

**5 — Stall, jump.** Set `poll_jump` true, stall, resume. Numbers skip. Say: that is the other
implementation, and it is one flag.

**6 — CDC still had them.** With Debezium on during the stall, the missing poll numbers are on the
same topic as `mechanism=cdc`. Two colours, one address. A subscriber cannot tell from the topic
which is which.

**7 — A failed measurement carries no reading.** Force a `FAILURE` from :8087. The poll message has
`status: "FAILURE"` and no `haze_ebc` key. Matches spec 05 checkpoint 6.

**8 — Gateway restart.** Restart Ignition. The memory watermark is 0. The next tick replays from the
beginning (duplicates) unless you re-seed. That is at-least-once, and it is honest. Do not "fix" it
in v1.

**9 — SELECT only.** As `ignition`, `INSERT INTO measurement …` must fail. If it succeeds, the grant
is wrong and the demo is lying about being an observer.

## Verification (copy-paste)

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' -v
```

Filter on `mechanism` if the firehose is noisy:

```
... mosquitto_sub ... -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' `
  | findstr poll
```

## Closing step

Write [`../06-poll-turbidity.md`](../06-poll-turbidity.md) as the as-built talk track. Update
architecture (the JDBC `APCONNECT` row — status expected a `TURBIDITY` one, correct it),
`00-status.md`, and the firehose runbook: the failure is a stalled watermark, not a wrapping
particle-counter buffer.

Deviations table, at minimum:

- **JDBC poll chosen over AP Connect's own REST watermark** (`apc_FromMeasurementCompletionNo` +
  `dataRevision`), and why.
- Whether the copied datasource password blob decrypted, or a UI retype was needed.
- The gateway timer's real on-disk schema, once known — and whether it took a UI round-trip.
- Whether the memory-tag JSON keys were right first time.
- Timer vs Event Stream scheduled source; memory vs durable watermark; catch-up vs jump as the
  default.
- Whether `runPrepQuery` gave column names or indexes, and what type `result_values` arrived as.
