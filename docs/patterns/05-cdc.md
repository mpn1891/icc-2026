# 05 — CDC: the writer that does not know MQTT exists

> Pattern 5 of 7. Built from [`plans/05-cdc-batch-event.md`](../plans/05-cdc-batch-event.md);
> architecture decisions are in [`00-architecture.md`](../00-architecture.md).
>
> The spec carries an MVP banner saying its payload had not yet been shaped by a consumer.
> Pattern 7 has since consumed `bes.batch_event` exactly as the MVP shaped it, and nothing had to
> change.

| | |
|---|---|
| **Pattern** | 5 of 7: change data capture, log tailing |
| **Container** | `debezium`: Debezium Server, pgoutput, slot `icc26_debezium` |
| **Surface** | Tag Explorer. There is no screen, on purpose |
| **Depends on** | the `ICC26` datasource and the Gateway Scripting Project. Nothing else |
| **Feeds** | pattern 7 reads `bes.batch_event` for the operation live at the sample instant |
| **Signal contributed** | the batch operation, and whether sampling is qualified in it |
| **GxP relevance** | Reading a validated system's internals without its owner in the loop. It works, it is durable, and it is a change-control conversation |

## Overview

A batch engine stepping `BR-201` through `CIP → SIP → INOC → GROWTH → HARVEST`, standing in for a
Batch Execution System. Every advance writes its rows to Postgres and stops: two of them, an
operation closing and the next one opening. **It holds no broker credentials, imports nothing
from Cirrus, and has no idea anybody is watching.**

Click a boolean in Tag Explorer. Two rows land in `bes.batch_event`, and a message nobody in the
application asked for appears on the backbone.

## Key ideas

**1. The whole pattern is one property, and it is one line of code away from being a lie.**
`bes_batch` writes and stops. Debezium reads the write-ahead log **as a different Postgres role**,
out of band, and the message appears. If that script ever grows a
`system.cirruslink.transmission.publish` call, even as a fallback, **pattern 5 becomes pattern 3
with extra steps.** The module docstring says so; that is why the docstring is there.

**2. It is an application we own.** The textbook CDC case is a system you cannot modify. This one
you could. The honest framing is that this engine stands in for a BES nobody is going to patch to
emit events, and **everything that makes CDC the right answer there is present here**: the writer
is innocent, the `cdc` role is not the application role, and turning the tail off leaves the
reactor cycling with a silent topic.

**3. BES, not MES.** `CIP → SIP → INOC → GROWTH → HARVEST` is batch execution specifically. "MES"
suggests work orders.

**4. These are operations, not phases.** In ISA-88 a *phase* is the smallest element that does
process action: "Add Water", "Agitate", "Hold at 37 °C". These five sit one level up, so the
column is `operation`, matching the tag people click.

**5. `qualified_window` is decided by the writer, at insert time, and never in the sink.** The
batch protocol qualifies sampling for `GROWTH` only, and that rule has exactly one home: the
`QUALIFIED` tuple in `bes_batch`. Computing it in `cdc-sink` instead would put a flag on the wire
that **was never in the change event**. Pattern 7 reads it and computes nothing.
[`00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md).

**6. The payload does not advertise its own transport.** An earlier revision carried `meta.op`
and `meta.lsn`; they came off on 2026-08-26. **A payload that announces how it arrived is a
strange thing in a demo whose claim is that a subscriber cannot tell.** The LSN is still logged by
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
                            │  op != "c" → audit topic, not this one
                            ▼
                       Transmission
                            ▼
        icc26/site1/upstream/br-201/batch/event   (retain false)
```

**Retain is false**, for the reason that recurs across this repo: a retained batch event replays a
stale operation to every reconnecting subscriber and presents it as current. The current operation
is what the tag is for.

**`equipment_id` is in the row, not just in the topic.** It holds `br-201`, the topic form, taken
from the tag path, and the sink builds the address from it. `batch_data` lives on the `bioreactor`
UDT *type*, so `br-202` has a live `manual_advance` button too, and without that column a stray
click over there would publish onto `br-201`'s address.

## The wire

One click into `GROWTH`, two rows, two messages. The second one:

```json
{
  "ts": "2026-08-26T19:12:04.318Z",
  "values": {
    "batch_id": "B-20260826-01",
    "equipment_id": "br-201",
    "event_type": "operation_start",
    "operation": "GROWTH",
    "qualified_window": true
  }
}
```

**Six fields, and not one of them says CDC.** Put this message beside pattern 1's valve document
and there is nothing in either one that tells you a write-ahead log was involved in one of them.

**The latency is machine-speed; it is just not in the message.** It is in `bes_cdc`'s gateway log
line and in Debezium's. This is the one pattern in the stack where the gap is small, because
nothing in the path waits for a person or a clock. Set it beside pattern 4's minutes and pattern
6's tens of seconds: three mechanisms, three completely different distances between *when it
happened* and *when the backbone knew*. If you want that gap on the wire, it is one `values`
field, the way pattern 4 kept `verified_at`.

## Failure modes

```bash
docker stop icc26-debezium     # click twice: rows land, the watcher stays silent
docker start icc26-debezium    # the missed changes arrive, with their original LSNs
```

The reactor kept cycling and the topic went quiet. **Nothing failed, nothing alarmed, and the only
symptom was an absence.** Then the replication slot and the offsets file hand back everything that
happened while nobody was listening.

**That durability is the CDC argument, and it is exactly what pattern 6 cannot claim.** A poller
that was not running when the value changed has no way to learn it ever did.

> **It happened by accident first.** On 2026-08-26 two real events (`operation_end/GROWTH` and
> `operation_start/HARVEST`, LSNs 81302288 and 81304288) were written while the sink was still
> broken, sat undelivered behind an unflushed offset through **three container restarts**, and
> were delivered the moment the transport was fixed, with their original LSNs. Nobody re-ran
> anything.

**Amendments are published, elsewhere.** `bes.batch_event` is append-only, so an `UPDATE`
reaching the sink means somebody is editing history:

```bash
docker exec icc26-postgres psql -U icc26 -d icc26 -c \
  "UPDATE bes.batch_event SET batch_id = batch_id WHERE id = 1;"
```

`200`, `"published": true, "op": "u"`, and it lands on `icc26/site1/audit/bes/batch-event`
carrying the `before` image, **not** on the operational topic. An edit is not a batch event, and
an amendment nobody can see is worse than one anybody can.

## Risks, measured

**1. `qualified_window` is `false` on every `operation_end`, including the one that closes
`GROWTH`.** The flag means *"sampling is qualified in the interval beginning at `occurred_at`"*,
and nothing is running between one operation ending and the next beginning. Get this wrong and
pattern 7 reads the closing row of `GROWTH`, sees `true`, and reports a sample as inside the
qualified window when the reactor had already moved on. **It would be right almost every time,
and wrong exactly at the boundary that matters.**

**2. One click writes two rows sharing `occurred_at` to the millisecond**, so the consumer's
tie-break is not optional. `ORDER BY occurred_at DESC, id DESC` is what makes the incoming
operation win. Verified against rows 33 and 34, which share `2026-08-30T22:50:19.034Z`: a sample
drawn after them resolves to the higher id and not to the operation that just ended.

**3. Debezium will helpfully do the wrong thing three ways if you let it**, and every default is
the dangerous one:

| Left at its default | What happens |
|---|---|
| `snapshot.mode` | `initial` replays **every existing row** as an insert on first connect: all of the batch history on the topic the moment the container starts |
| `publication.autocreate.mode` | Debezium creates a `FOR ALL TABLES` publication when it cannot find one, putting every LIMS write on the backbone |
| offsets on a container filesystem | The container forgets its WAL position on restart, and the stop/start above produces nothing instead of catching up |

`tasks.py health` asserts the publication names `bes.batch_event` and nothing else, because it
used to also name `lims.sample_result`, which would have delivered a single analyst review twice,
under two different mechanisms.

**4. Three of this build's failures were transport, none were design**, and two of them mislead:

- **The config mount path is `/debezium/config`**, and the image's own error message says
  `/debezium/conf`. Mounted where the error tells you, the container starts, reads nothing, and
  dies in a restart loop on a missing `debezium.sink.type`. **Do not trust that message.**
- **Plain HTTP to the sink returns a 302** to `:8043`, and Debezium's Java HTTP client defaults to
  `followRedirects(NEVER)`. Every event exhausts its retries at the redirect and the connector
  dies with *"Exceeded maximum number of attempts to publish event"*, a message naming neither the
  redirect nor the status code. It looks like a sink bug and it is a transport one.
- **A stale container survives `docker compose up -d`** after a bind-mount path change.
  `--force-recreate` is the habit to keep.

## What this pattern deliberately does not do

**No screen.** Tag Explorer is the more honest surface: it looks like somebody poking a gateway,
which is what it is.

**No dwell timer.** Advancing is a button rather than an interval, because pattern 7 needs the
reactor parked in `GROWTH` when the valve is badged, and parked outside it to show the negative
case.

**No `unwrap` transform.** Keeping `op` and `source.lsn` is what lets the sink route an `UPDATE`
to the audit topic instead of the operational one.

**No `plant.batch` integration.** `batch_id` is minted by `bes_batch` on the first advance out of
`IDLE` and cleared at `batch_end`, with no foreign key and no validation. Fine for a demo, wrong
for anything else.

## Try it

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/upstream/#' -v
```

In Tag Explorer, on `[default]icc26/site1/upstream/bioreactors/br-201/batch_data/`:

| Step | Do | What you see |
|---|---|---|
| A batch begins | Click `manual_advance` once from `IDLE` | `operation` reads `CIP`, the button resets itself, **one** message with `qualified_window: false`. The `batch_id` was minted, not typed |
| Two rows per click | Click on to `SIP`, `INOC` | An `operation_end` and an `operation_start` per click, sharing a timestamp to the millisecond |
| The qualified window | Click once more, into `GROWTH` | The **only** message in the run carrying `qualified_window: true` |
| The row behind the message | `SELECT id, event_type, operation, payload, occurred_at FROM bes.batch_event ORDER BY id;` | The rows that produced each message |
| The silence | `docker stop icc26-debezium`, click twice | Rows land. Nothing on the wire. Nothing alarms |
| The catch-up | `docker start icc26-debezium` | Both missed changes arrive, in order, with their original LSNs |
| Editing history | `UPDATE bes.batch_event …` (above) | `200`, `"op": "u"`, on `…/audit/bes/batch-event` with the `before` image. Operational topic silent |

**Leave `br-201` in `GROWTH` when you are done.** Pattern 7's positive case needs a sample drawn
inside the qualified window, and walking the reactor back around costs four clicks
(`CIP → SIP → INOC → GROWTH`), which also mints a new batch.

**The Gateway Scripting Project must be `icc-2026`.** A tag event script runs in gateway scope,
and that setting is what makes `bes_batch` resolve. Patterns 6 and 7 depend on it too.
