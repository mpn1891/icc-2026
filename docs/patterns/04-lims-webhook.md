# 04 — LIMS webhook: the record a person has to sign

> Pattern 4 of 7. Built from [`plans/04-lims-webhook.md`](../plans/04-lims-webhook.md) (read its
> § *Revised 2026-08-26* first, because it supersedes half of that file); architecture decisions
> are in [`00-architecture.md`](../00-architecture.md).

| | |
|---|---|
| **Pattern** | 4 of 7: webhook, because a person has to sign it off |
| **Container** | `lims`, [`services/lims/`](../../services/lims/) |
| **Review screen** | <http://localhost:8000>: three panels, and the middle one is the pattern |
| **Depends on** | pattern 1's `event/sample-acq-completed` (opens the entry) and pattern 3's result (appends the analytes) |
| **Feeds** | pattern 7. The review message is its trigger, on **both** outcomes |
| **Signal contributed** | the analyst's disposition: the only fact in this stack a machine did not produce |
| **GxP relevance** | Authenticated inbound connection, and the result arrives out of sequence with the physical event it describes |

## Overview

A LIMS that opens a sample record **when material leaves the reactor**, not when the instrument
speaks. The valve's event creates the entry; the analyzer's result attaches to it minutes later;
an analyst signs one record holding both halves, and the LIMS POSTs it to Ignition.

Badge at :8085, type that id into the analyzer at :8087, approve at :8000: one message, carrying
the collection and the analytes together. Transpose a character on the way and the cost of the
mistake lands on one screen.

The risk: a naive webhook loses data permanently, and the usual fix is a worse copy of something
Postgres has had all along.

## Key ideas

**1. A webhook exists because the answer is not ready when you ask.** If the LIMS could answer
synchronously, the correct design is an HTTP response, not a callback. The callback is what you
build when the work is asynchronous, and here it is asynchronous for the most ordinary reason in
a regulated plant: **a person has to sign it off.**

**2. The record exists before the instrument speaks.** The sample begins when material leaves
the reactor, so the LIMS has two subscriptions, and the order they arrive in *is* the pattern:

| | Topic | What it does |
|---|---|---|
| 1 | pattern 1's `…/sample-valve-01/event/sample-acq-completed` | **Opens the entry**: badge holder, `sample_start`, `open_duration_s`, `cycle_result` |
| 2 | pattern 3's `…/qc/analyzers/+/sample-analyzed` | **Appends the analytes**, minutes later |

An earlier build had no concept of a sample: a sample was the `GROUP BY sample_id` of whatever
analyte rows arrived, so the record did not exist until the analyzer produced one. That is
backwards for a LIMS.

**3. The two ids match because a person makes them match.** The valve mints `S-YYYYMMDD-NNNN` on
the badge grant. Somebody reads it off BR-201's page and types it into the analyzer's own
sample-login screen. An Ignition tag write would do the same job and could not be typed wrong,
**which is exactly why it is not what we built.** Pattern 1's GxP point is *the record originates
at the point of action, no transcription, no intermediary*; this is the intermediary, with a
keyboard.

**4. One transposed character shows the whole cost on one screen.** The entry sits open with no
analysis, and the analysis parks with no entry. **`reported_sample_id` holds what the instrument
said, verbatim, and is never rewritten**: the correction is recorded *beside* it, with who
attached it and when. You do not fix a record by overwriting what the instrument reported. There
is no foreign key on that column, deliberately: an unmatched result still has to insert.

**5. A naive webhook loses data, permanently.** One delivery attempt, over a network the sender
does not control, against a receiver that may be restarting. When the retries exhaust there is
nothing to replay from and the sender has already forgotten, and **no other mechanism in this
demo covers the same data**, so nothing rescues it.

The fix is **a transactional outbox**: commit the result and the intent-to-deliver in the same
transaction, then let a separate worker drain the outbox with retries and at-least-once delivery.
That is what `lims.webhook_delivery` is for.

**6. An outbox is change data capture that you wrote by hand.** You end up rebuilding, inside
your application, the thing Postgres already does in the write-ahead log, which is pattern 5's
argument arriving from the opposite direction.

**7. Both outcomes publish.** Approve writes `disposition: "pass"`, reject writes `"fail"`, each
through the same outbox row in the same transaction. **Nothing downstream should have to infer a
rejection from silence.**

## The chain

```
      sim-valve-mqtt ──▶ icc26/…/sample-valve-01/event/sample-acq-completed
                                      │  (retained, QoS 1)
      opcua-cell-analyzer ──▶ icc26/site1/qc/analyzers/cell-analyzer-01/sample-analyzed
                                      │
                       subscribe as lims-bridge — publishTopics: []
                                      ▼
                        ┌──────────────────────────┐
                        │        lims :8000        │  1. entry opened at collection
                        │                          │  2. analytes appended by id
                        │   a human clicks Approve │  3. UPDATE + outbox row,
                        └────────────┬─────────────┘     one transaction
                                     │
              POST + shared secret + idempotency key  (HTTPS :8043)
                                     ▼
                    Ignition WebDev ──▶ Transmission
                                     ▼
        icc26/site1/qc/lims/sample-results-released     (ts + values)
                                     │
                                     └──▶ pattern 7 subscribes here
```

**The LIMS publishes nothing to the broker.** `lims-bridge` has `publishTopics: []`, and its two
subscribe grants are named to the exact topics rather than wildcarded to the site. Widen that
grant to `icc26/#` and the service consumes its own release message: the same ACL file that stops
the valve leaving `upstream` is what stops this service talking to itself.

## The wire

One message per sample, carrying both halves of the record. The identifiers, the badge holder and
the two analytes are `S-20260831-0103` from the 2026-08-31 walk; the timestamps are illustrative,
and the walk's exact pair is quoted underneath:

```json
{
  "ts": "2026-08-31T15:41:04.000Z",
  "values": {
    "sample_id": "S-20260831-0103",
    "equipment_id": "br-201",
    "analyst": "mmartin",
    "disposition": "pass",
    "verified_at": "2026-08-31T15:41:13.184Z",
    "collection": {
      "badge_id": "B-1042", "badge_holder": "Jordan Reyes",
      "sample_start": "2026-08-31T15:39:47.109Z",
      "sample_completion": "2026-08-31T15:40:00.618Z",
      "open_duration_s": 13.51, "cycle_result": "normal"
    },
    "results": [
      { "analyte": "glucose", "value": 5.92, "uom": "g/L" },
      { "analyte": "lactate", "value": 0.14, "uom": "g/L" }
    ]
  }
}
```

**`ts` is the acquisition instant and `verified_at` is the signature, and the gap between them is
the pattern.** `ts` is what the analyzer measured (pattern 3's own `ts`, its vendor `SampleTime`),
stored as `collected_at` at ingest and never restamped. `verified_at` is when a person clicked,
taken from the same `UPDATE` that flipped the status rather than re-read off the clock.

On the 2026-08-31 walk **the valve closed at `15:40:00.618` and the record was assembled at
`15:41:13.184`: 72.6 seconds** of a person copying an id off one screen, typing it into an
instrument, and signing for the result. In a real plant it is hours. **That distance is
provenance, not lag**, and this is the only mechanism in the stack where the distance is a human
being. [`end-to-end-test.md`](../end-to-end-test.md) § *Known good*.

**The envelope is `ts` and `values`**, the same as patterns 1–3. A subscriber knows this came
from the LIMS because it arrived on `icc26/site1/qc/lims/sample-results-released`, and that is
the only answer any other pattern gives either.

**`values.collection` is pattern 1's contribution, carried through the review.** It makes the
released record self-contained: who drew the sample, when the valve opened, and how the cycle
ended, beside the numbers somebody just signed for.

**Two analytes, not three.** The osmometer is unfitted on the shipped defaults, so `osmo` arrives
null from pattern 3 and the absent-versus-zero rule writes no row. Deterministic, and not a bug.

**`equipment_id` is the vessel, and there is no `batch_id`.** The valve opens on a badge, not on
a work order, and the analyzer only echoes whatever somebody typed at it, so nothing in the lab
actually knows which batch this material came from. A LIMS-side copy of that field was only ever
a guess dressed as a fact. **Pattern 7 asks the batch system instead**: `equipment_id` plus the
acquisition instant against `bes.batch_event` gives the batch that was genuinely running when the
valve opened.

## Failure modes

The buttons are on the review screen itself: **Pause outbound** and **Resume outbound**, with a
lamp beside them.

1. **Normal.** Approve a sample. One message on `qc/lims/sample-results-released`.
2. **Naive delivery, broken.** Press **Pause outbound**, then approve. The result is verified
   *inside* the LIMS and the backbone never hears about it. **This is the state a naive webhook
   leaves you in permanently.**
3. **The outbox, working.** Press **Resume outbound**. Restart the container first if you like:
   the queue is in Postgres, not in memory. The delivery lands, late, with `attempts > 1`.

**And the transcription failure.** Repeat a sample with a transposed character at :8087: the
entry stays *awaiting analysis*, the result lands under **Unmatched results**, and attaching it
records who decided. `reported_sample_id` in Postgres still holds the wrong id.

## Risks, measured

**1. Retained plus clean session is a redelivery source people forget they signed up for.**
Pattern 1 publishes `sample-acq-completed` **retained**, and this client connects with
`clean_session=True`, so the broker replays the last one on every reconnect. Without
`ON CONFLICT (sample_id) DO NOTHING`, a `docker restart icc26-lims` **resurrects the most recent
sample (already approved, already released) back into the review queue.** Verified: the restart
logs the redelivery as a no-op.

**2. `awaiting-analysis` is the one state a signature cannot be applied to**, and both the screen
and the store refuse it, saying *"has no analysis yet — nothing to sign off"*. **A failed cycle is
the exception**: if `cycle_result` is not `normal`, no material reached the analyzer, nothing is
coming, and the entry opens straight into reviewable. Verified by sagging the air supply at
:8085: a `failed-to-seat` entry, reviewable immediately with no analytes, and Reject works on it.

**3. Idempotency is in-memory on the Ignition side, and is lost on a gateway restart.** The last
~500 keys in an `OrderedDict`: a replayed key returns `409` and publishes nothing, which is what
makes an at-least-once outbox look exactly-once. This could be durable in the `ICC26` datasource
and is not, because the outbox that matters is the one in Postgres.

**4. Wrong secret is `401`, and the secret is a bare shared token rather than an HMAC over the
body.** Demo-grade committed credentials are an accepted trade here.

## Known shortfalls

| Shortfall | Why it is acceptable here |
|---|---|
| Bare shared secret, not an HMAC over the body | Demo-grade committed credentials, named as a trade |
| `UNIQUE (reported_sample_id, analyte)` | A real LIMS repeats tests. This keeps ingest dedupe to one constraint, on what the instrument said |
| Ignition-side dedupe is in-memory, lost on gateway restart | Risk 3. The durable queue is the one in Postgres |
| The instrument operator is not a column | `analyst` is the approver; `collection.badge_holder` is who drew the sample. Nobody records who pressed Run |
| `qc/lims/sample-results-released` puts a software system in the line-or-cell slot | A violation of the project's own naming rule. The better address is under `BR-201`; it was kept because three patterns key off this topic |
| Gateway certificate SAN is `localhost` only | The LIMS verifies the mounted public certificate and skips hostname matching. Pattern 5's Debezium makes the same trade |
| A released sample carries two analytes, not three | The osmometer is unfitted, deterministically |

## Try it

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer \
  -t 'icc26/site1/qc/analyzers/+/sample-analyzed' -t 'icc26/site1/qc/lims/sample-results-released' -v
```

| Step | Do | What you see |
|---|---|---|
| The entry opens at collection | Badge `B-1042` at :8085 | ~15 s later the sample is on :8000 as **Awaiting analysis**, badge holder and open duration populated, **no Approve button** |
| The transcription | Type that id into :8087, press Run | The **same** entry flips to ready with two analytes. No second entry |
| Nothing until a signature | Watch the topic | **Nothing** on `qc/lims/sample-results-released` until somebody clicks |
| The signature | Approve | One message, `values.collection` beside the analytes. Compare `ts` with `values.verified_at` |
| A rejection is a disposition | Reject a different sample | Same topic, same outbox path, `disposition: "fail"`. **Not silence** |
| Do it wrong | Repeat with a transposed character | Entry stays awaiting; result parks under **Unmatched results**; attach it and the wrong id survives in Postgres |
| The outbox | **Pause outbound** → approve → **Resume outbound** | Silence, then a late delivery with `attempts > 1` |
| Replay | Re-POST a delivered idempotency key | `409`, and no second message |

**Find your own sample id in the queue.** It orders by `sample_completion` ascending, so on a
long-lived database older rows with a null `equipment_id` can sit at the top, and approving one
produces a pattern 7 document with `batch_context: null` that looks like a broken join.
[`plans/00-post-07.md`](../plans/00-post-07.md) § 1 has the details.

Badge → type → approve exercises patterns 1, 3, 4 and 7 in one gesture and takes about two
minutes: [`end-to-end-test.md`](../end-to-end-test.md).
