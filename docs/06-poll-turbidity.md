# 06 — Poll / watermark of the turbidity meter's data-management database

> Talk track for pattern 6. The spec it was built from is
> [`plans/06-poll-turbidity.md`](plans/06-poll-turbidity.md). Architecture decisions live in
> [`00-architecture.md`](00-architecture.md); the vendor model is
> [`reference/apconnect-haze3001-model.md`](reference/apconnect-haze3001-model.md). This file is
> what you speak.
>
> **Authored, not run.** Every Ignition resource in this pattern was written as a file with the
> stack down and the gateway untouched. Nothing below has been executed against a gateway, a
> broker or a database. Two of the four resource types had **no committed example to copy**, so
> their on-disk schemas are inferred — see *Fields that are guesses*. Work the runbook at the
> bottom once before trusting any of this on stage.
>
> Depends on pattern 5's database and simulator, which are built in a separate branch. Until that
> branch is merged there is nothing to poll.

| | |
|---|---|
| **Pattern** | 6 of 7 — poll, because the system of record will not call you |
| **Mechanism tag** | `meta.mechanism = "poll"` |
| **Source** | `apconnect.measurement` — the same table pattern 5 tails |
| **Path** | Ignition JDBC (`APCONNECT`), `WHERE measurement_no > :watermark`, Transmission |
| **Topic** | `icc26/site1/downstream/tff-301/turbidity-01/telemetry` (same as pattern 5) |
| **Watermark** | `measurement_no`. **Correlation id is the GUID**, not the integer |
| **Period** | 60 s, fixed delay |
| **Depends on** | pattern 5's database + `sim-apconnect`. **Not** on Debezium |
| **Blocks** | nothing. Adds no container and no MQTT user |

## Talk points

**Polling is what you do when the system of record will not call you.** AP Connect files a
completed measurement in its own database and that is the end of its interest in the subject. It
does not speak MQTT, it will not be modified, and the only two ways in are the store and the API.
Pattern 5 takes one door. This is the other.

**This is the integration most of the room has actually shipped.** A timer, a
`WHERE measurement_no > :last`, and a tag that remembers the watermark. There is nothing clever
here, and that is the point — it is the honest baseline the other six patterns are measured
against.

**The period is sixty seconds on purpose.** A minute of lag between a completed measurement and
its arrival on the backbone is what polling costs. Put the same measurement's `cdc` envelope
beside it — CDC's `ingest_ts` is milliseconds after `ts`, poll's is up to a minute — and the gap
is the entire comparison. A two-second timer would have hidden the thing the pattern is about.

**The failure is a stalled loop, not a wrapping buffer.** Measurements accumulate in the vendor's
database while the poll is down. When it resumes you either catch up — late, but complete — or you
jump the watermark, and you dropped data. Both are one flag apart in the same script, and CDC,
running beside it on the same topic, dropped neither.

**A subscriber cannot tell from the topic which colour is which.** Same address, two mechanisms.
`meta.mechanism` carries it and the firehose colours by it. That is the namespace claim, live.

**`meta.correlation_id` is the GUID, not the integer.** AP Connect's `measurement_no` is a counter
and the vendor says it can reset; the GUID survives that. Both patterns stamp the GUID, so two
colours of one measurement join on one field.

**Ignition appears twice, for two unrelated reasons.** Once as the poller — that is the pattern.
Once as the operator's button — that is a stage prop. A real Haze 3001 is started by a person at
the instrument, and `measure_now` stands in for that person. Say so; do not let it read as part of
the mechanism.

**Ignition never writes to the vendor's database.** The button POSTs to the simulator, which does
the INSERT as the application would. The `ignition` role holds SELECT and nothing else, and
checkpoint 10 exists to keep that honest. An observer that can write is not an observer.

## The chain

```
measure_now tag ──tag-change script──POST /measure──▶ sim-apconnect
(the operator prop)                                        │ INSERT
                                                           ▼
                                 database `apconnect`.measurement
                                                           │
                                                           │ JDBC, user `ignition`, SELECT only
                                                           ▼
                                     Ignition timer script, every 60 s
                                     WHERE measurement_no > {watermark}
                                                           │
                                                           ▼
                                     Transmission  (ign-transmission)
                                                           │
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: poll)
```

No new container. The writer is pattern 5's.

## What is on disk

| Path | What | Confidence |
|---|---|---|
| `ignition/config/resources/core/ignition/database-connection/APCONNECT/` | `config.json` + `resource.json`. Copied from `pg_db`; only `connectURL` differs | **High** — copied from a committed, working resource |
| `…/tag-definition/default/icc26/site1/downstream/{,tff-301/,tff-301/turbidity-01/}` | Three `unary-resource.json` folder levels + `tags.json` | **Medium** — folder shape copied from `qc/analyzers/`; the tag keys are inferred |
| `ignition/projects/icc-2026/ignition/script-python/poll_turbidity/` | `code.py` + `resource.json`. The whole pattern | **High** — `resource.json` copied from `opcua_event/` |
| `ignition/projects/icc-2026/ignition/timer/poll-turbidity/` | `config.json` + `code.py` + `resource.json`. 60 s → `poll_turbidity.tick()` | **Low** — inferred, no committed example |
| `ignition/projects/icc-2026/ignition/tag-change/measure-now/` | `config.json` + `code.py` + `resource.json`. Rising edge → `poll_turbidity.measure_now()` | **Low** — inferred, folder name is itself a guess |

Apply with `python tasks.py scan`, not a restart.

All logic lives in the script module, so during a demo you can run it by hand from the script
console — `poll_turbidity.tick()` — without waiting for a tick or trusting the timer resource.
That is also the workaround if the timer resource turns out not to load.

## Envelope

Identical to pattern 5's except `mechanism` and `ingest_ts`.

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

`ingest_ts` minus `ts` is the poll lag: 0–60 s normally, the stall length after a stall.

**Absent stays absent.** A `CANCELED` or `FAILURE` measurement publishes `status` and no haze keys
at all — never `0`. Same rule as patterns 3, 4 and 5.

## The failure demo

1. Debezium off. Trigger three measurements with the button. Within a minute, three `poll`
   messages, `measurement_no` contiguous, `seq == values.measurement_no`. Normal.
2. Debezium on. Trigger one. The `cdc` message lands immediately; the `poll` message lands at the
   next tick with the **same `ts`**. Show the two `ingest_ts` values side by side.
3. Clear `poll_enabled`. Trigger four — you know it is exactly four, because a human pressed the
   button four times. Nothing happens for as long as you like.
4. Set `poll_enabled`. Four late messages, contiguous with the last pre-stall number, `ingest_ts`
   clustered at now, `ts` spread across the stall. **No gaps.** That is catch-up.
5. Set `poll_jump`, stall, trigger four more, resume. The numbers **skip** and those four are never
   published. Say: that is the other implementation, and it is one flag.
6. CDC had all of them the whole time, on the same topic. Two colours, one address.

## Fields that are guesses

Everything in this table was inferred. Nothing in it was run. The failure-mode column is what to
look for when it is wrong.

| # | File | Field / choice | What I wrote | If it is wrong |
|---|---|---|---|---|
| 1 | `tag-definition/.../turbidity-01/tags.json` | initial value key | **`defaultValue`** | Tags appear but read null/0, or `scan` rejects the file. The spec said `value: 0`; I changed it — see deviation D5 |
| 2 | same | data file name | `tags.json`, declared in `unary-resource.json` `files` | Folder appears empty. The **only** data-file name proven anywhere in the committed tag tree is `udts.json`; rename the file and the `files` entry to match and re-scan |
| 3 | same | `"dataType": "Int8"` for `poll_watermark` | Int8 (64-bit) | Watermark truncates or the tag faults. `Int8` and `Int4` both appear in the committed UDTs, so the vocabulary is right; the width is the guess |
| 4 | `timer/poll-turbidity/` | **the folder name** `ignition/timer/` | as spec | Resource never loads, nothing in the gateway's project resource list. The spec said this folder "exists but is empty" — it does **not** exist in the committed tree at all (git does not track empty directories), so it is weaker evidence than the spec implies |
| 5 | `timer/poll-turbidity/config.json` | `delay` | `60000` | Wrong period, or resource rejected |
| 6 | same | `delayType` | `"FIXED_DELAY"` | Enum constant name/casing is a guess. A bad enum value usually fails the **whole resource**, not just the field |
| 7 | same | `threadType` | `"DEDICATED"` | Same enum risk. Chosen so a JDBC poll that overruns cannot block other gateway timer scripts; `"SHARED"` is the product default if this is rejected |
| 8 | `timer/poll-turbidity/` | script as separate `code.py` vs inline `"script"` in `config.json` | separate `code.py`, listed in `files` | Timer loads but runs nothing. The event-stream resources put user code **inline**; the WebDev resources use **separate `.py` files**. Both shapes exist in this repo, so this is a coin-flip |
| 9 | `timer/.../code.py` | bare script body vs a `def` the runtime calls | bare body | `NameError`, or no execution. Bare body matches the 8.1 project-export convention the `ignition/timer/` path implies |
| 10 | `tag-change/measure-now/` | **the folder name** `ignition/tag-change/` | as spec's guess | Resource never loads. There is no gateway tag-change folder on disk at all, so this is the single least-evidenced thing in the pattern |
| 11 | `tag-change/measure-now/config.json` | `tagPaths` (array) | one absolute `[default]…/measure_now` path | Script loads but never fires |
| 12 | same | `changeTypes: ["VALUE"]` | value changes only, not quality or timestamp | Either never fires, or fires on every quality blip and double-triggers measurements |
| 13 | `tag-change/.../code.py` | binding names `currentValue`, `previousValue`, `initialChange` | as Ignition's documented tag-change bindings | `NameError` in the gateway log on first write |
| 14 | `poll_turbidity/code.py` | what `runPrepQuery` returns | handled **both** ways — see below | Should not fail. The one-time probe reports what it actually was |
| 15 | `poll_turbidity/code.py` | type of `result_values` over JDBC | handled String, PGobject and already-decoded list | Should not fail. The probe reports the real class |
| 16 | `poll_turbidity/code.py` | `completed_ts` is a `java.sql.Timestamp` | `_iso` accepts `Date`/`Timestamp`, ISO string, or epoch millis | Should not fail. Probe reports the real class |
| 17 | `database-connection/APCONNECT/config.json` | the copied `Embedded` password blob decrypts | copied byte-identical from `pg_db` | Connection faults with an authentication error. Retype the password in Config → Databases → Connections and commit what changed |

### The probe

Items 14–16 are guesses I could **remove** rather than document, so I did. `poll_turbidity.tick()`
unwraps whatever `runPrepQuery` returns to the underlying `Dataset` and indexes positionally off
the column names, which works whether it hands back a `PyDataSet` or a bare `Dataset`. Then, once
per gateway session, on the first tick that returns rows, it logs at INFO under logger
`poll_turbidity`:

```
probe: runPrepQuery returned <class>; columns [...]; completed_ts is <class>;
       result_values is <class>; sample of result_values <first 200 chars>
```

Read that line after the first real run and fold the answers into this table. That converts three
guesses into three facts for the cost of one log line, and the same trick paid off on pattern 4.

### Fallbacks for the two inferred resources

In the spec's order of preference:

1. **Create both once in Gateway → Config → Gateway Events**, read back what the gateway wrote to
   disk, and commit that. One UI trip makes the files authoritative forever. **Do both in the same
   trip** — they fail the same way and it is the same round trip.
2. **Timer only:** drive `tick()` from an Event Stream scheduled source. The Event Stream schema
   *is* committed and known (`com.inductiveautomation.eventstream/event-streams/…`), so if the
   timer resource fights back this is the cheaper fix. Note it as-built.
3. **Trigger only:** fall back to the simulator's own *measure now* button on `:8087`. The demo
   still works; you lose the Ignition-side control. **Do not** fall back to letting the simulator
   free-run — an operator-caused row is what makes a missing row impossible to miss.

There is also a fourth option worth knowing about, deliberately **not** taken. A tag-level
`valueChanged` event script — `eventScripts: [{"eventid": "valueChanged", "script": "…"}]` on the
tag itself — is **proven on disk in this repo** (the vibration UDT's `collect_trigger` uses exactly
that, including a `system.cirruslink.transmission.publish` call). It has no unknowns at all. It was
not used because the spec calls for a gateway tag-change script and silently substituting a
different mechanism is worse than an inferred one. If option 1 is inconvenient, this is the
zero-risk escape hatch: move the body of `tag-change/measure-now/code.py` into an `eventScripts`
entry on the `measure_now` tag in `tags.json` and delete the tag-change resource. Do not run both —
they would double-trigger.

## Deviations

| # | Decision | Why |
|---|---|---|
| D1 | **JDBC poll, not AP Connect's own REST watermark.** The vendor exposes `GET /api/v1/measurements/completed?apc_FromMeasurementCompletionNo=N` plus a cheap `/completed/latest` returning a `dataRevision` to compare **for inequality** every few seconds | That is what you would build against a real AP Connect, and it is the better production answer. It was rejected so patterns 5 and 6 sit on *exactly* the same rows and the CDC-versus-poll comparison has no second variable. Say: "we would use their API in production; here it would have made the two patterns incomparable" |
| D2 | Postgres stands in for AP Connect's Microsoft SQL Server | Inherited from spec 05. Worth one aside: on SQL Server, CDC is **not** log streaming — the capture job writes rows into change tables and Debezium polls those. SQL Server CDC is itself a poll |
| D3 | Datasource password is `pg_db`'s `Embedded` ciphertext blob, copied byte-identical | Decrypts with the gateway's own key, and `pg_db` survives clone-and-seed. No plaintext password in the repo, no UI retype. **Whether it actually decrypts on this gateway is unverified** — checkpoint 1 |
| D4 | `_rows_as_dicts()` normalises the JDBC result instead of assuming `row["column"]` | The spec allowed for `runPrepQuery` returning column indexes and said to note it as-built. Rather than guess and be wrong once, the module handles both shapes and logs which one it got |
| D5 | **Memory tags use `defaultValue`, not the spec's `value`** | The spec's `{"value": 0}` is very likely wrong. On disk, every `valueSource: "memory"` tag with an initial value uses `defaultValue` (`{"dataType": "Boolean", "defaultValue": false, …}`), and `"value"` is used for something else entirely — a parameter **binding** object `{"bindType": "parameter", "binding": "{…}"}`. Writing `"value": 0` would at best be ignored and at worst parsed as a malformed binding. Evidence: `tag-type-definition/default/udts.json`, the vibration UDT's `collect_request` / `collect_steady_state` |
| D6 | Timer resource, not an Event Stream scheduled source | Spec's first choice. Event Stream is the documented fallback and its schema is the better-known one; if the timer resource does not load, switch and record it |
| D7 | Watermark is a **memory** tag, not durable | Deliberate. A gateway restart resets it to 0 and the next tick replays from the beginning — at-least-once, and honest. A table would hide the failure. Checkpoint 9 |
| D8 | **Catch-up is the default; jump is a live flag** | `poll_jump` is real code, not a comment, because showing both is the point. Catch-up is late; jump loses data |
| D9 | `tick()` refuses to run when the control tag reads `None` | Not in the spec. Without it, an unscanned tag folder reads as `None`, the watermark falls back to 0, and every tick republishes the entire table onto the shared topic. It logs a warning naming the tag instead |
| D10 | On a publish failure `tick()` stops at that row and does not advance the watermark past it | Not in the spec. Keeps the sequence in order and at-least-once; the row is retried next tick. The alternative silently skips it |
| D11 | Batched the control-tag reads into one `readBlocking` | Matches `opcua_event`'s batched-read idiom. No semantic change |
| D11b | `measure_now()` logs `response.text`, not the spec sketch's `resp.body` | `.body` is a byte array and logs as `[B@1a2b3c`. Cosmetic, but it is on the demo path |
| D12 | The reciprocal `VARIANT_MAP` comment in `services/cdc-mapper/app.py` is **not** written | That file belongs to pattern 5's branch and this branch must not touch it. The Jython side names the CPython side; **the CPython side still needs the comment pointing back**. Whoever merges the two branches should add it |
| D13 | The 60 s period, catch-up-vs-jump default, and whether the stage wanted it faster | Unknown — never run |
| D14 | What `runPrepQuery` returned, and what type `result_values` arrived as | **Unknown.** Never run. The probe answers both on the first tick that returns rows |
| D15 | Whether the two gateway events' inferred schemas are right, and whether they took a UI round-trip | **Unknown.** Never run. This is the pattern's main risk |
| D16 | Whether the memory-tag JSON keys were right first time | **Unknown**, though D5 makes `defaultValue` much better evidenced than the spec's `value` |

## Runbook — the 10 checkpoints, in one pass

> **Unverified.** None of this has been executed. It is written to be worked top to bottom in one
> sitting the first time the stack comes up, and it is where the guesses above get settled.
>
> **Checkpoints 1 and onward all need pattern 5's branch merged first** — the `apconnect` database,
> `05-apconnect.sql`, and the `sim-apconnect` container all live there. Checkpoints 3, 4 and 7 also
> need Debezium and `cdc-mapper` from that branch. Nothing here can run against this branch alone.
>
> Pattern 5 changes `compose/postgres/initdb/`, which only runs on an **empty volume**, so the
> first step after merging is a nuke.

### 0 — Bring it up

```
python tasks.py nuke
python tasks.py up
python tasks.py scan
```

`scan` is what applies the Ignition resources in this branch. Expect `scanned config` and
`scanned projects`.

Then confirm the four resources actually loaded — this is the first place the two inferred ones
can fail silently:

```
docker logs icc26-ignition --tail 200 | findstr /I "poll_turbidity poll-turbidity measure-now APCONNECT"
```

Expected: no stack traces mentioning any of those names. A resource that failed to deserialise
logs a warning at startup/scan and then simply does not exist.

### 1 — JDBC answers

Gateway UI → Status → Connections → Databases. `APCONNECT` shows **Valid**.

```
docker exec -it icc26-postgres psql -U ignition -d apconnect -c "SELECT count(*) FROM measurement;"
```

Expected: a number, and it grows as you trigger measurements later.

If the datasource is **Faulted with an authentication error**, the copied password blob did not
decrypt — deviation D3. Retype the password in Config → Databases → Connections, then commit what
changed on disk.

### 2 — The trigger works

In the tag browser, write `true` to
`[default]icc26/site1/downstream/tff-301/turbidity-01/measure_now`.

Expected, all three:

- the tag flips back to `false` on its own,
- `docker logs icc26-sim-apconnect --tail 20` shows a filed measurement,
- a new row exists **before any poll has run**:

```
docker exec -it icc26-postgres psql -U ignition -d apconnect -c "SELECT measurement_no, status, completed_ts FROM measurement ORDER BY measurement_no DESC LIMIT 5;"
```

If the tag **stays true**, the tag-change script did not load — guesses 10–13. Go to the fallbacks.
The rest of the runbook still works using the simulator's own button on <http://localhost:8087>.

### 3 — Poll with Debezium off

```
docker stop icc26-debezium icc26-cdc-mapper
```

Subscribe:

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' -v
```

Trigger three measurements. Within 60 s expect three messages, `"mechanism": "poll"`,
`measurement_no` contiguous, and `seq` equal to `values.measurement_no` in each.

Now read the probe line — this is the one-shot, so do it here:

```
docker logs icc26-ignition --tail 300 | findstr /I "probe:"
```

Record what it says in *Fields that are guesses*, items 14–16.

If nothing publishes and the log shows
`poll skipped: … poll_enabled is not readable`, the memory tags did not load — guesses 1–3.

If nothing publishes and there is no log line at all, the timer did not load — guesses 4–9. Prove
the module itself is fine from the script console before chasing the timer:

```
poll_turbidity.tick()
```

### 4 — The two documents match, and the lag is visible

```
docker start icc26-debezium icc26-cdc-mapper
```

With the subscriber still running, trigger **one** measurement and capture both messages.

Expected: everything equal except `meta.mechanism` and `meta.ingest_ts`. Any other difference is a
projection bug in one of the two `VARIANT_MAP`s — they are deliberately duplicated across Jython
and CPython (deviation D12).

The `cdc` message arrives within milliseconds of the button. The `poll` message arrives at the next
tick, up to a minute later, carrying the **same `ts`**. That gap, on one measurement, on one topic,
is the whole comparison.

### 5 — Stall, catch-up

Write `poll_enabled` = `false`. Trigger **four** measurements. Write `poll_enabled` = `true`.

Expected: four late messages, numbers contiguous with the last pre-stall one, `ingest_ts` clustered
at now, `ts` spread across the stall. **No gaps.**

### 6 — Stall, jump

Write `poll_jump` = `true`. Stall with `poll_enabled` = `false`, trigger four more, then
`poll_enabled` = `true`.

Expected: the log shows `poll_jump: watermark advanced N -> M without publishing`, the numbers
**skip**, and those four are never published. Set `poll_jump` back to `false` afterwards.

### 7 — CDC still had them

Debezium was on throughout 6. The four numbers the poll skipped are on the **same topic** as
`"mechanism": "cdc"`. Two colours, one address, and a subscriber cannot tell from the topic which
is which.

### 8 — A failed measurement carries no reading

Force a `FAILURE` from the simulator page on <http://localhost:8087> (or
`POST /measure` with `{"status": "FAILURE"}`).

Expected: the poll message has `"status": "FAILURE"` and **no** `haze_ebc` key — not `0`, absent.
Matches spec 05 checkpoint 6.

### 9 — Gateway restart

```
python tasks.py restart ignition
```

Expected: `poll_watermark` is back to 0, and the next tick replays from the beginning — duplicates.
That is at-least-once and it is honest. **Do not fix it in v1.** The 60 s period means you wait up
to a minute to see it.

### 10 — SELECT only

```
docker exec -it icc26-postgres psql -U ignition -d apconnect -c "INSERT INTO measurement (id, measurement_completion_no, measurement_name, status, started_ts, completed_ts, instrument_serial, instrument_alias, result_values) VALUES (gen_random_uuid(), 1, 'x', 'SUCCESS', now(), now(), 'x', 'x', '[]'::jsonb);"
```

Expected: **`ERROR: permission denied for table measurement`.**

If it succeeds the grant is wrong and the demo is lying about being an observer. The `measure_now`
trigger must not have weakened this — it POSTs to the simulator, it does not write to the database.

### Filtering a noisy firehose

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' | findstr poll
```
