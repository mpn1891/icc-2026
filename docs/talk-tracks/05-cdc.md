# 05 — CDC: the writer that does not know MQTT exists

> Talk track for pattern 5. The spec this was built from is
> [`plans/05-cdc-batch-event.md`](../plans/05-cdc-batch-event.md). Architecture decisions live in
> [`00-architecture.md`](../00-architecture.md); this file is what you speak.
>
> **Written 2026-09-06.** The spec still carries an *"⚠ this is an MVP — it has not been shaped
> by a consumer, because pattern 7 is not written"* banner. Pattern 7 is written now, it consumes
> `bes.batch_event` exactly as the MVP shaped it, and **nothing had to change** — which is a
> better close for this segment than anything that was in the spec.

| | |
|---|---|
| **Pattern** | 5 of 7 — change data capture, log tailing |
| **Mechanism tag** | `meta.mechanism = "cdc"` |
| **New container** | `debezium` — Debezium Server, pgoutput, slot `icc26_debezium` |
| **Surface** | Tag Explorer. There is no screen, on purpose |
| **Depends on** | the `ICC26` datasource and the Gateway Scripting Project. Nothing else |
| **Blocks** | pattern 7 reads `bes.batch_event` for the operation live at the sample instant |
| **Signal contributed** | the batch operation, and whether sampling is qualified in it |
| **GxP hook** | Reading a validated system's internals without its owner in the loop. State it plainly and hand it to the risk speaker |

## The segment

**Intro.** A batch engine stepping `BR-201` through `CIP → SIP → INOC → GROWTH → HARVEST`,
standing in for a Batch Execution System. Every advance writes its rows to Postgres and stops —
two of them, an operation closing and the next one opening. **It holds no broker credentials,
imports nothing from Cirrus, and has no idea anybody is watching.**

**Demo.** Click a boolean in Tag Explorer. Two rows land in `bes.batch_event`, and a message
nobody in the application asked for appears on the backbone.

**Risk.** Somebody is reading another system's database, out of band, as a role that system's
owner did not issue. It works, it is durable, and it is a change-control conversation.

**Close.** *(unassigned — see the master plan's open items)*

## Talk points

**1. The whole pattern is one property, and it is one line of code away from being a lie.**
`bes_batch` writes and stops. Debezium reads the write-ahead log **as a different Postgres role**,
out of band, and the message appears. If that script ever grows a
`system.cirruslink.transmission.publish` call — even "just for the demo", even as a fallback —
**pattern 5 becomes pattern 3 with extra steps.** The module docstring says so; that is why the
docstring is there.

**2. It is an application we own, and saying so is stronger than pretending otherwise.** The
textbook CDC case is a system you cannot modify. This one you could. So the honest framing is:
this engine stands in for a BES nobody is going to patch to emit events, and **everything that
makes CDC the right answer there is present here** — the writer is innocent, the `cdc` role is
not the application role, and turning the tail off leaves the reactor cycling with a silent
topic.

**3. Say BES, not MES.** `CIP → SIP → INOC → GROWTH → HARVEST` is batch execution specifically.
This room hears "MES" and thinks work orders.

**4. These are operations, not phases — and the schema was renamed to match the sentence.** In
ISA-88 a *phase* is the smallest element that does process action: "Add Water", "Agitate", "Hold
at 37 °C". These five sit one level up. The tag people click already said `operation` and the
column it wrote to said `phase`, so the column moved. **The verify step in this segment puts
`SELECT * FROM bes.batch_event` on screen moments after you have said the word**, and a column
header contradicting the sentence just spoken is the one place that churn would have been
visible.

**5. `qualified_window` is decided by the writer, at insert time, and never in the sink.** The
batch protocol qualifies sampling for `GROWTH` only — that rule has exactly one home, the
`QUALIFIED` tuple in `bes_batch`. Computing it in `cdc-sink` instead would put a flag on the wire
that **was never in the change event**: a flag added after the tail is a flag the CDC demo did not
actually observe. Pattern 7 reads it and computes nothing.
[`00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md).

**6. The payload does not advertise its own transport.** An earlier revision carried `meta.op`
and `meta.lsn`, on the argument that the log position is the one field no other mechanism could
produce. They came off on 2026-08-26. **A payload that announces how it arrived is a strange
thing in a demo whose claim is that a subscriber cannot tell.** The LSN is still logged by
`bes_cdc` and by Debezium, which is where somebody investigating would look for it.

## The chain

```
        a person clicks manual_advance in Tag Explorer
                            │
                       bes_batch                 ← holds no broker credentials
                            │  INSERT × 2, one transaction, one occurred_at
                            ▼
                    bes.batch_event               (user icc26)
                            │
                    Postgres write-ahead log
                            │  publication icc26_cdc, slot icc26_debezium
                            ▼
                 Debezium Server                  (user cdc — a different role)
                            │  POST https://ignition:8043/…/cdc-sink?token=…
                            ▼
              WebDev cdc-sink → bes_cdc.handle()
                            │  op != "c" → 200, no publish
                            ▼
                       Transmission
                            ▼
        icc26/site1/upstream/br-201/batch/event   (mechanism: cdc, retain false)
```

**Retain is false**, and for the reason that recurs across this repo: a retained batch event
replays a stale operation to every reconnecting subscriber and presents it as current. The
current operation is what the tag is for.

**`equipment_id` is in the row, not just in the topic.** It holds `br-201` — the topic form, taken
from the tag path — and the sink builds the address from it. `batch_data` lives on the
`bioreactor` UDT *type*, so `br-202` has a live `manual_advance` button too, and without that
column a stray click over there would publish onto `br-201`'s address.

## The wire

One click into `GROWTH`, two rows, two messages. The second one, in the envelope the spec's
payload contract fixes — with the `batch_id` in the minted `B-YYYYMMDD-NN` form the code has
produced since 2026-08-30, rather than the hand-typed one the contract was written against:

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
    "batch_id": "B-20260826-01",
    "equipment_id": "br-201",
    "event_type": "operation_start",
    "operation": "GROWTH",
    "qualified_window": true
  }
}
```

**`seq` is the database row id**, exactly as pattern 4's is its outbox id. Durable and monotonic
across gateway restarts, which an in-memory counter is not — this had one in its first revision,
and it restarted at 1 every time the gateway did.

**`source.id` is `bes`, not an area.** There is no `bes` area in the namespace and there is not
going to be: an area is a place, a BES is software. The event happens in a suite, so it publishes
under the cell that produced it and names its source system in the payload.

**`ts` to `meta.ingest_ts` is machine-speed here**, and that is worth one sentence: this is the
one pattern in the stack where the gap is small, because nothing in the path waits for a person
or a clock. Set it beside pattern 4's minutes and pattern 6's tens of seconds — three mechanisms,
three completely different distances between *when it happened* and *when the backbone knew*.

## The failure demo — run it, it is half the segment

```powershell
docker stop icc26-debezium     # click twice: rows land, the watcher stays silent
docker start icc26-debezium    # the missed changes arrive, with their original LSNs
```

The reactor kept cycling and the topic went quiet. **Nothing failed, nothing alarmed, and the
only symptom was an absence.** Then the replication slot and the offsets file hand back
everything that happened while nobody was listening.

**That durability is the CDC argument, and it is exactly what pattern 6 cannot claim.** A poller
that was not running when the value changed has no way to learn it ever did.

> **It happened by accident first, which is the best evidence in the file.** On 2026-08-26 two
> real events — `operation_end/GROWTH` and `operation_start/HARVEST`, LSNs 81302288 and 81304288
> — were written while the sink was still broken, sat undelivered behind an unflushed offset
> through **three container restarts**, and were delivered the moment the transport was fixed,
> with their original LSNs. Nobody re-ran anything. The scripted version above had not been run
> yet.

**The negative check is worth thirty seconds too.** `bes.batch_event` is append-only, so an
`UPDATE` reaching the sink means somebody is editing history:

```bash
docker exec icc26-postgres psql -U icc26 -d icc26 -c \
  "UPDATE bes.batch_event SET batch_id = batch_id WHERE id = 1;"
```

`200`, `"published": false, "op": "u"`, and **nothing on the wire.** Rejecting it visibly is a
better answer on stage than filtering it away.

## The risk beat — four things measured, not asserted

**1. `qualified_window` is `false` on every `operation_end`, including the one that closes
`GROWTH`.** The flag means *"sampling is qualified in the interval beginning at `occurred_at`"*,
and nothing is running between one operation ending and the next beginning. Get this wrong and
pattern 7 reads the closing row of `GROWTH`, sees `true`, and reports a sample as inside the
qualified window when the reactor had already moved on. **It would be right almost every time,
and wrong exactly at the boundary this whole talk is about.**

**2. One click writes two rows sharing `occurred_at` to the millisecond**, so the consumer's
tie-break is not optional. `ORDER BY occurred_at DESC, id DESC` is what makes the incoming
operation win. Verified against rows 33 and 34, which share `2026-08-30T22:50:19.034Z`: a sample
drawn after them resolves to the higher id and not to the operation that just ended.

**3. Debezium will helpfully do the wrong thing three ways if you let it**, and every default is
the dangerous one:

| Left at its default | What happens |
|---|---|
| `snapshot.mode` | `initial` replays **every existing row** as an insert on first connect — a rehearsal's worth of batch history on the topic the moment the container starts |
| `publication.autocreate.mode` | Debezium creates a `FOR ALL TABLES` publication when it cannot find one, putting every LIMS write on the backbone as `mechanism=cdc` |
| offsets on a container filesystem | The container forgets its WAL position on restart, and the stop-click-restart demo above produces nothing instead of catching up |

`tasks.py health` asserts the publication names `bes.batch_event` and nothing else, because it
used to also name `lims.sample_result` — which would have delivered a single analyst review
twice, under two different mechanisms.

**4. Three of this build's failures were transport, none were design**, and two of them lie to
you:

- **The config mount path is `/debezium/config`** — and the image's own error message says
  `/debezium/conf`. Mounted where the error tells you, the container starts, reads nothing, and
  dies in a restart loop on a missing `debezium.sink.type`. **Do not trust that message.**
- **Plain HTTP to the sink returns a 302** to `:8043`, and Debezium's Java HTTP client defaults
  to `followRedirects(NEVER)`. Every event exhausts its retries at the redirect and the connector
  dies with *"Exceeded maximum number of attempts to publish event"* — a message naming neither
  the redirect nor the status code. It looks like a sink bug and it is a transport one.
- **A stale container survives `docker compose up -d`** after a bind-mount path change. It cost
  this build twice. `--force-recreate` is the habit to keep.

## What this pattern deliberately does not do

**No screen.** Spec 08 was cut on 2026-08-25 and there are no Perspective views in this demo. Tag
Explorer is also the more honest surface — it looks like somebody poking a gateway, which is what
it is.

**No dwell timer.** The master plan specified auto-cycling on an interval; it is a button
instead, because pattern 7's rehearsal needs the reactor parked in `GROWTH` when the valve is
badged and then parked outside it for the second run. A timer makes that a waiting game on stage.

**No `unwrap` transform.** Keeping `op` and `source.lsn` is what lets the sink reject an `UPDATE`
rather than republish edited history — see the negative check above.

**No `plant.batch` integration.** `batch_id` is minted by `bes_batch` on the first advance out of
`IDLE` and cleared at `batch_end`, with no foreign key and no validation. Fine for a demo, wrong
for anything else, and written down as such.

## On stage

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/upstream/#' -v
```

Tag Explorer, on `[default]icc26/site1/upstream/bioreactors/br-201/batch_data/`:

| Beat | Trigger | What lands |
|---|---|---|
| A batch begins | Click `manual_advance` once from `IDLE` | `operation` reads `CIP`, the button resets itself, **one** message — `qualified_window: false`. The `batch_id` was minted, not typed |
| Two rows per click | Click on to `SIP`, `INOC` | An `operation_end` and an `operation_start` per click, sharing a timestamp to the millisecond |
| The qualified window | Click once more, into `GROWTH` | The **only** message in the run carrying `qualified_window: true` |
| The row behind the message | `SELECT id, event_type, operation, payload, occurred_at FROM bes.batch_event ORDER BY id;` | The table says `operation`, which is the word you just used |
| **The silence** | `docker stop icc26-debezium`, click twice | Rows land. Nothing on the wire. Nothing alarms |
| **The catch-up** | `docker start icc26-debezium` | Both missed changes arrive, in order, with their original LSNs |
| Editing history is refused | `UPDATE bes.batch_event …` | `200`, `"published": false, "op": "u"`, nothing on the wire |
| Nothing says how it arrived | Read the topic aloud to somebody who did not watch the build | `icc26/site1/upstream/br-201/batch/event`. `meta.mechanism` is the only tell |

**Leave `br-201` in `GROWTH` when you are done.** Pattern 7's positive beat needs a sample drawn
inside the qualified window, and walking the reactor back around costs four clicks —
`CIP → SIP → INOC → GROWTH`, which also mints a new batch.

**The Gateway Scripting Project must be `icc-2026`.** A tag event script runs in gateway scope,
and that setting is the whole of what makes `bes_batch` resolve. It had never come up before this
pattern, and patterns 6 and 7 both depend on it now.

## Progress log

| Date | Change |
|---|---|
| 2026-09-06 | Written from [`plans/05-cdc-batch-event.md`](../plans/05-cdc-batch-event.md) as the closing step, per the two-document convention — after pattern 7 landed rather than before, which changed the close: the spec's MVP banner predicted the `values` shape and the two-rows-per-click model would need revisiting once a consumer existed, and neither did. The accidental failure demo of 2026-08-26 is promoted out of the spec's progress log, because it is the strongest evidence in the pattern and it was not staged. |
