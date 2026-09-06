# 04 — LIMS webhook: the record a person has to sign

> Talk track for pattern 4. The spec this was built from is
> [`plans/04-lims-webhook.md`](../plans/04-lims-webhook.md) — read § *Revised 2026-08-26* first,
> because it supersedes half of that file. Architecture decisions live in
> [`00-architecture.md`](../00-architecture.md); this file is what you speak.
>
> **Rewritten 2026-09-06.** The previous version described a queue the analyzer filled, which is
> not what this service has done since 2026-08-26: **the entry is opened by the valve, at
> collection, and the analyzer appends to it.** Both review outcomes have published since
> 2026-08-30, so the *Remaining* line is gone too.

| | |
|---|---|
| **Pattern** | 4 of 7 — webhook, because a person has to sign it off |
| **Mechanism tag** | `meta.mechanism = "webhook"` |
| **Container** | `lims` — [`services/lims/`](../../services/lims/) |
| **Review screen** | <http://localhost:8000> — three panels, and the middle one is the pattern |
| **Depends on** | pattern 1's `event/sample-complete` (opens the entry) and pattern 3's result (appends the analytes) |
| **Blocks** | pattern 7. The review message is its trigger, on **both** outcomes |
| **Signal contributed** | the analyst's disposition — the only fact in this stack a machine did not produce |
| **GxP hook** | Authenticated inbound connection, and the result arrives out of sequence with the physical event it describes |

## The segment

**Intro.** A LIMS that opens a sample record **when material leaves the reactor**, not when the
instrument speaks. The valve's event creates the entry; the analyzer's result attaches to it
minutes later; an analyst signs one record holding both halves.

**Demo.** Badge at :8085, type that id into the analyzer at :8087, approve at :8000. One message,
carrying the collection and the analytes together. Then do it wrong — transpose a character —
and watch the cost land on one screen.

**Risk.** A naive webhook loses data permanently, and the fix people reach for is a worse copy of
something Postgres has had all along.

**Close.** *(unassigned — see the master plan's open items)*

## Talk points

**1. A webhook exists because the answer is not ready when you ask.** If the LIMS could answer
synchronously, the correct design is an HTTP response, not a callback. The callback is what you
build when the work is asynchronous — and here it is asynchronous for the most ordinary reason in
a regulated plant: **a person has to sign it off.**

**2. The record exists before the instrument speaks, and that is a correction the build made to
itself.** The LIMS used to have no concept of a sample: a sample was the `GROUP BY sample_id` of
whatever analyte rows arrived, so **the record did not exist until the analyzer produced one.**
That is backwards for a LIMS and backwards for the talk. The sample begins when material leaves
the reactor. Two subscriptions now, and the order they arrive in *is* the pattern:

| | Topic | What it does |
|---|---|---|
| 1 | pattern 1's `…/sample-valve-01/event/sample-complete` | **Opens the entry** — badge holder, `sample_start`, `open_duration_s`, `cycle_result` |
| 2 | pattern 3's `…/qc/analyzers/+/result` | **Appends the analytes**, minutes later |

**3. The two ids match because a person makes them match.** The valve mints `S-YYYYMMDD-NNNN` on
the badge grant. Somebody reads it off BR-201's page and types it into the analyzer's own
sample-login screen. An Ignition tag write would do the same job and could not be typed wrong —
**which is exactly why it is not what we built.** Pattern 1's GxP hook is *the record originates
at the point of action, no transcription, no intermediary*; this is the intermediary, on stage,
with a keyboard.

**4. One transposed character shows the whole cost on one screen.** The entry sits open with no
analysis, and the analysis parks with no entry. **`reported_sample_id` holds what the instrument
said, verbatim, and is never rewritten** — the correction is recorded *beside* it, with who
attached it and when. You do not fix a record by overwriting what the instrument reported. There
is no foreign key on that column, deliberately: an unmatched result still has to insert.

**5. A naive webhook loses data, permanently.** One delivery attempt, over a network the sender
does not control, against a receiver that may be restarting. When the retries exhaust there is
nothing to replay from and the sender has already forgotten — and **no other mechanism in this
demo covers the same data**, so nothing rescues it.

The fix is the one an audience can apply on Monday: **a transactional outbox.** Commit the result
and the intent-to-deliver in the same transaction, then let a separate worker drain the outbox
with retries and at-least-once delivery. That is what `lims.webhook_delivery` is for.

**6. Then the line worth saving for last: an outbox is change data capture that you wrote by
hand.** You end up rebuilding, badly and inside your application, the thing Postgres already does
in the write-ahead log — **which is pattern 5's argument arriving from the opposite direction.**
If 5 comes after 4 in the running order, this is the sentence that hands over.

**7. Both outcomes publish.** Approve writes `disposition: "pass"`, reject writes
`"fail"`, each through the same outbox row in the same transaction. A rejection used to be
silent, which made it the one review outcome invisible to every consumer — and left pattern 7
with half a trigger. **Nothing downstream should have to infer a rejection from silence.**

## The chain

```
      sim-valve-mqtt ──▶ icc26/…/sample-valve-01/event/sample-complete
                                      │  (retained, QoS 1)
      opcua-cell-analyzer ──▶ icc26/site1/qc/analyzers/cell-analyzer-01/result
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
        icc26/site1/qc/lims/sample-result     (mechanism: webhook)
                                     │
                                     └──▶ pattern 7 subscribes here
```

**The LIMS publishes nothing.** `lims-bridge` has `publishTopics: []`, and its two subscribe
grants are named to the exact topics rather than wildcarded to the site. Widen that grant to
`icc26/#` and the service consumes its own release message — **the same ACL file that stops the
valve leaving `upstream` is what stops this service talking to itself.**

## The wire

One message per sample, carrying both halves of the record. The envelope is the contract's; the
identifiers, the badge holder and the two analytes are `S-20260831-0103` from the 2026-08-31
walk, and the timestamps are illustrative — the walk's exact pair is quoted underneath:

```json
{
  "ts": "2026-08-31T15:41:04.000Z",
  "seq": 26,
  "source": { "id": "lims", "type": "lims" },
  "meta": {
    "mechanism": "webhook",
    "ingest_ts": "2026-08-31T15:41:12.000Z",
    "correlation_id": "S-20260831-0103"
  },
  "values": {
    "sample_id": "S-20260831-0103",
    "batch_id": "B-20260831-01",
    "equipment_id": "br-201",
    "analyst": "mnorris",
    "disposition": "pass",
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

**`ts` is the acquisition instant and `ingest_ts` is the signature, and the gap between them is
the pattern.** `ts` is what the analyzer measured — pattern 3's own `ts`, its vendor `SampleTime`,
stored as `collected_at` at ingest and never restamped. `ingest_ts` is when a person clicked.

The number to say out loud is the one the whole walk measured: **the valve closed at
`15:40:00.618` and the record was assembled at `15:41:13.184` — 72.6 seconds**, which is a person
copying an id off one screen, typing it into an instrument, and signing for the result. In a real
plant it is hours. **That distance is provenance, not lag**, and this is the only mechanism in
the stack where the distance is a human being.
[`end-to-end-test.md`](../end-to-end-test.md) § *Known good*.

**`values.collection` is pattern 1's contribution, republished under `mechanism: webhook`.** It is
what makes the released record self-contained: who drew the sample, when the valve opened, and
how the cycle ended, beside the numbers somebody just signed for.

**Two analytes, not three.** The osmometer is unfitted on the shipped defaults, so `osmo` arrives
null from pattern 3 and the absent-versus-zero rule writes no row. Deterministic, and not a bug
to fix on stage.

**`equipment_id` is the vessel and `batch_id` is not from the valve.** The valve opens on a badge,
not on a work order, so the batch identity arrives with the analysis — and pattern 7 takes it from
`bes.batch_event` regardless. `equipment_id` is what stops 07 having to hardcode a reactor.

## The failure demo — rehearse it, it is the engineering half of the segment

The buttons are on the review screen itself: **Pause outbound** and **Resume outbound**, with a
lamp beside them.

1. **Normal.** Approve a sample. One message, `mechanism: webhook`.
2. **Naive delivery, broken.** Press **Pause outbound**, then approve. The result is verified
   *inside* the LIMS and the backbone never hears about it. Show the row, then show the silent
   topic. **This is the state a naive webhook leaves you in permanently.**
3. **The outbox, working.** Press **Resume outbound**. Restart the container first if you like —
   the queue is in Postgres, not in memory. The delivery lands, late, with `attempts > 1`.
4. **Say the line.** What we just built to make a webhook trustworthy is a worse copy of the
   write-ahead log Postgres has had the whole time. Which is pattern 5.

**And the transcription failure, which is the other half.** Repeat the sample with a transposed
character at :8087: the entry stays *awaiting analysis*, the result lands under **Unmatched
results**, and attaching it records who decided. Then show in Postgres that
`reported_sample_id` still holds the wrong id.

## The risk beat — four things measured, not asserted

**1. Retained plus clean session is a redelivery source people forget they signed up for.**
Pattern 1 publishes `sample-complete` **retained**, and this client connects with
`clean_session=True`, so the broker replays the last one on every reconnect. Without
`ON CONFLICT (sample_id) DO NOTHING`, a `docker restart icc26-lims` **resurrects the most recent
sample — already approved, already released — back into the review queue.** Verified: the restart
logs the redelivery as a no-op. It sits right next to the QoS-1 at-least-once redelivery this
pattern already argues about.

**2. `awaiting-analysis` is the one state a signature cannot be applied to**, and both the screen
and the store refuse it — saying *"has no analysis yet — nothing to sign off"* rather than reading
a status string back at somebody. **A failed cycle is the exception**: if `cycle_result` is not
`normal`, no material reached the analyzer, nothing is coming, and the entry opens straight into
reviewable. Verified by sagging the air supply at :8085 — a `failed-to-seat` entry, reviewable
immediately with no analytes, and Reject works on it.

**3. Idempotency is in-memory on the Ignition side, and is lost on a gateway restart.** The last
~500 keys in an `OrderedDict`: a replayed key returns `409` and publishes nothing, which is what
makes an at-least-once outbox look exactly-once. **The `ICC26` datasource exists now**, so this
could be durable and is not — recorded rather than fixed, because the outbox that matters is the
one in Postgres.

**4. Wrong secret is `401`, and the secret is a bare shared token rather than an HMAC over the
body.** Demo-grade committed credentials are an accepted trade here, and worth naming as a trade
rather than leaving somebody to notice it.

## The topic wart, kept on purpose

`icc26/site1/qc/lims/sample-result` puts **a software system in the line-or-cell slot.** The
better address is under `BR-201`. It was revisited when the spec was written and kept: three
patterns key off this topic and the conference was four weeks out.

It is a spoken aside — a violation of our own naming rule that we found, could justify fixing, and
chose not to fix this close to a deadline. **Say it before somebody in the room says it**, because
this is the segment where the namespace rule is freshest in their minds.

## On stage

Watcher, in its own terminal — both halves of the chain:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/qc/analyzers/+/result' -t 'icc26/site1/qc/lims/sample-result' -v
```

| Beat | Trigger | What lands |
|---|---|---|
| The entry opens at collection | Badge `B-1042` at :8085 | ~15 s later the sample is on :8000 as **Awaiting analysis**, badge holder and open duration populated, **no Approve button** |
| The transcription | Type that id into :8087, press Run | The **same** entry flips to ready with two analytes and a batch id. No second entry |
| Nothing until a signature | Watch the topic | **Nothing** on `qc/lims/sample-result` until somebody clicks |
| The signature | Approve | One message — `values.collection` beside the analytes. Read `ts` against `meta.ingest_ts` out loud |
| A rejection is a disposition | Reject a different sample | Same topic, same outbox path, `disposition: "fail"`. **Not silence** |
| Do it wrong | Repeat with a transposed character | Entry stays awaiting; result parks under **Unmatched results**; attach it and the wrong id survives in Postgres |
| The outbox | **Pause outbound** → approve → **Resume outbound** | Silence, then a late delivery with `attempts > 1` |
| Replay | Re-POST a delivered idempotency key | `409`, and no second message |

**Do not click the first thing on the review screen.** The queue orders by `sample_completion`
ascending, so a block of pre-`migrate-08` rows with a null `equipment_id` sits at the top — and
they are the only other clickable rows there. Approving one produces a pattern 7 document with
`batch_context: null` and **looks exactly like a broken join** in front of the room. Find your own
sample id. [`plans/00-post-07.md`](../plans/00-post-07.md) § 1 has the measurement and the open
decision about clearing them out.

**Walk the whole loop once before the talk.** Badge → type → approve is the only path that
exercises patterns 1, 3, 4 and 7 in one gesture, and it takes about two minutes:
[`end-to-end-test.md`](../end-to-end-test.md).

## Deviations, knowingly

| Shortfall | Why it is acceptable here |
|---|---|
| Bare shared secret, not an HMAC over the body | Demo-grade committed credentials, named as a trade |
| `UNIQUE (reported_sample_id, analyte)` | A real LIMS repeats tests. This keeps ingest dedupe to one constraint, on what the instrument said |
| Ignition-side dedupe is in-memory, lost on gateway restart | Risk beat 3. The durable queue is the one in Postgres |
| The instrument operator is not a column | `analyst` is the approver; `collection.badge_holder` is who drew the sample. Nobody records who pressed Run |
| `qc/lims/sample-result` names a system in the line-or-cell slot | Kept for schedule — see above |
| Gateway certificate SAN is `localhost` only | The LIMS verifies the mounted public certificate and skips hostname matching. Ignition 302s `:8088` → `:8043`, so the URL is HTTPS. Pattern 5's Debezium makes the same trade for the same reason |
| A released sample carries two analytes, not three | The osmometer is unfitted, deterministically. **Do not "fix" it** |

## Progress log

| Date | Change |
|---|---|
| 2026-09-06 | **Rewritten for the 2026-08-26 rebuild**, which this file had not described for eleven days: the entry is opened by pattern 1 at collection and the analyzer appends to it, so the transcription beat, the unmatched-results panel and `values.collection` are all new here. The *Remaining — publish reject* line is gone: both outcomes have carried `disposition` since 2026-08-30. The wire example is the measured `S-20260831-0103` rather than a synthetic one, which puts a real 72.6 s human gap on the page. Added the stage warning about the twelve stale reviewables at the top of the queue, found while verifying pattern 7. |
| 2026-08-20 | Service, schema, ACL, WebDev + `lims_webhook`, review screen on :8000. Ingest verified without a nuke; publish checkpoints verified after a trial reset — approve publishes once, a replayed idempotency key returns `409`, a wrong secret `401`, and two queued deliveries survived `docker restart icc26-lims`. |
| 2026-08-23 | Split out of [`plans/04-lims-webhook.md`](../plans/04-lims-webhook.md), which stays the build spec. |
