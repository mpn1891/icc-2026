# 07 — The sample chain: the composite record

> Talk track for pattern 7. The spec this was built from is
> [`plans/07-sample-chain.md`](../plans/07-sample-chain.md). Architecture decisions live in
> [`00-architecture.md`](../00-architecture.md); this file is what you speak.
>
> **This is the last segment.** Everything before it argues that a kind of equipment can be
> brought onto a common backbone. This one is the argument for having done so.

| | |
|---|---|
| **Pattern** | 7 of 7 — scripted aggregation, the composite GxP event |
| **Mechanism tag** | `meta.mechanism = "aggregate"` |
| **New container** | **none.** One script module, one Event Stream, no new table, no ACL change, no new datasource |
| **Depends on** | 1 (the draw), 3 (the numbers), 4 (the signature and the trigger), 5 (the batch), 6 (the room) |
| **Blocks** | nothing. This is the close |
| **Signal contributed** | **the composite** — the one record no source system could have produced |
| **GxP hook** | Derived data with disposition consequence — the heaviest item on the list, and the right one to end on |

## The segment

**Intro.** Six mechanisms have published. Every one of them was a different way of getting a
fact onto the wire, and every one of them was somebody else's problem to solve. **Pattern 7 is
the first thing in this stack that reads.**

**Demo.** Approve one clean sample. **Nothing is published** — and that is the point. Press
**Dirty** on the MET ONE panel, draw and approve again: one message lands on
`icc26/site1/qc/deviation`, and `values.violations` says what was wrong. Read it out loud.

**Risk.** Every number in that document was computed by the pattern that produced it, and 07
can prove it — because when a lookup finds nothing, the document says so instead of guessing.
07 decides *whether* to speak; it never decides what is out of spec.

**Close.** *(the through line's closing argument — see the master plan)*

## Talk points

**1. Nothing in this pattern is clever, and that is the whole claim.** 07 does no arithmetic. It
holds no process knowledge. It computes no flags. `qualified_window` was decided by `bes_batch`
at the moment the operator advanced the reactor — its `QUALIFIED` tuple is the only copy of the
batch protocol in the stack. `status` was decided by `metone_poll` against
`config/excursion_threshold`, the only copy of the cleanroom limit. **07's only job is to be the
thing that asks.** If it ever tested `operation == "GROWTH"` itself, the stack would hold two
copies of a rule, and two copies of a rule drift.
[`00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md).

**2. The question is one nobody could answer before.** Not *"was the sample in spec"* — the LIMS
answers that. The question is:

> *This sample, drawn at this instant by this person, analysed to these numbers, signed off by
> this analyst — what was the reactor doing, and what was the room doing, at the moment it was
> taken?*

Four systems each hold a fragment that means nothing alone. The valve knows who drew it. The
analyzer knows the numbers. The batch system knows the operation. The particle counter knows the
room. **No single one of them can be asked this question**, and none of them was modified to
answer it.

**3. The two lookups do not key on the same column, and that is the honest bit of plumbing.**
`bes.batch_event` keys on the *sample's* `equipment_id` — `br-201`, parsed by pattern 1 out of
its own topic and carried the whole way. `em.reading` has **no `equipment_id` column at all**: it
keys on `device_id`, a constant, because the particle counter is one instrument for the room and
not one per vessel. Its `location` field reads

```
USP Suite A - BR-201 sample port
```

— and that string is how the room ties to the vessel. **It is prose for a human, not a join
key.** 07 carries it into the composite so a reader can see the association, and parses nothing.
That is a real plant, told the truth about: the association exists, and it lives in a text field
somebody typed.

**4. A gap is a finding, not a silence.** The MET ONE rule is *nearest reading either side of the
sample, no tolerance, age always reported*. A reading three seconds **after** the valve closed is
better evidence than one twenty-five seconds before, so the search is not restricted to the past.
And there is no cutoff at which 07 refuses to answer — it reports the nearest reading, reports
its age, and lets the reader judge. Which makes `age_s` load-bearing, so it sits at the **top**
of the block: a forty-minute-old count must not be able to read as current.

When a lookup genuinely finds nothing, the block is `null` and a `_reason` sits beside it —
never a missing key, never a silent default. **What 07 will not do is publish a clean sample.**
Since 2026-09-06 it speaks only on a deviation, so a quiet topic is the compliant state — and an
`environment` block that is null is itself a finding, because a sample whose room cannot be
evidenced cannot be released.

**5. Seven patterns, and a subscriber still cannot tell how any of it arrived.** One
`mosquitto_sub` shows the same `sample_id` under `opcua-event`, `webhook` and `aggregate`, on
three topics whose names say nothing about transport. `meta.mechanism` is the only field that
tells them apart. Pattern 4 and pattern 7 are the only two subscribers there are — 07 is a
genuine backbone subscriber, not a database job wearing a hat.

## The chain

```
                            a person clicks Approve (or Reject)
                                          │
                     icc26/site1/qc/lims/sample-result   (mechanism: webhook)
                                          │
                        MQTT Engine Event Stream source, QoS 1
                                          ▼
                            ┌──────────────────────────┐
   bes.batch_event ────────▶│   sample_chain.build()   │◀──────── em.reading
   WHERE equipment_id = ?   │                          │   WHERE device_id = 'particle-counter-01'
   AND occurred_at <= ?     │  no arithmetic, no flags │   ORDER BY nearest either side
   ORDER BY occurred_at     │  no process knowledge    │
            DESC, id DESC   └────────────┬─────────────┘
                                         │
                                    Transmission
                                         ▼
                     icc26/site1/qc/deviation      (mechanism: aggregate)
                                  QoS 1, retained false
```

**Retained false**, for the reason `bes_cdc` gives in its own comment: a retained composite
replays a stale record to every new subscriber and presents it as current.

**Nothing on the broker moved.** `ign-engine` already subscribed `icc26/#` and
`ign-transmission` already published `icc26/#`, so
[`mqtt-users.json`](../../compose/chariot/mqtt-users.json) is untouched.

## The wire

One approval, measured live on 2026-08-30 (`S-20260830-0085`, trimmed):

```json
{
  "ts":  "2026-08-30T23:34:54.774Z",
  "seq": 23,
  "source": { "id": "sample-chain", "type": "aggregate" },
  "meta": {
    "mechanism": "aggregate",
    "ingest_ts": "2026-08-30T23:35:35.963Z",
    "correlation_id": "S-20260830-0085"
  },
  "values": {
    "sample_id": "S-20260830-0085",
    "equipment_id": "br-201",
    "equipment_identifier": "br-201",
    "batch_id": "B-20260830-02",
    "disposition": "pass",
    "analyst": "M. Martin",

    "collection": {
      "badge_id": "B-1042", "badge_holder": "Jordan Reyes",
      "sample_start": "2026-08-30T23:34:41.264Z",
      "sample_completion": "2026-08-30T23:34:54.774Z",
      "open_duration_s": 13.51, "cycle_result": "normal"
    },
    "results": [
      { "analyte": "glucose",    "value": 4.21,  "uom": "g/L" },
      { "analyte": "lactate",    "value": 1.08,  "uom": "g/L" },
      { "analyte": "osmolality", "value": 312.0, "uom": "mOsm/kg" }
    ],

    "batch_context": {
      "operation": "GROWTH", "qualified_window": true,
      "event_type": "operation_start", "as_of": "2026-08-30T22:55:37.922Z"
    },
    "batch_context_reason": null,

    "environment": {
      "age_s": 2.5, "nearest_side": "before",
      "device_id": "particle-counter-01",
      "location": "USP Suite A - BR-201 sample port",
      "status": "normal",
      "occurred_at": "2026-08-30T23:34:52.235Z",
      "channels": [ { "size_um": 0.5, "count": 137 }, "…" ],
      "conditions": { "flow_rate_lpm": 28.3, "temperature_c": 22.6, "humidity_pct": 43.5 }
    },
    "environment_reason": null
  }
}
```

**Three things to point at, in this order.**

**`ts` against `meta.ingest_ts` — 41 seconds.** `ts` is the acquisition instant, the moment the
valve closed. `ingest_ts` is when 07 assembled the record. The gap between them **is** the
record's provenance, and it is visible in one message without anybody explaining it. Same rule
pattern 4 states for the review, and pattern 6 for the poll.

**`batch_context.as_of` against `ts` — 39 minutes.** The reactor entered GROWTH at 22:55 and the
sample was drawn at 23:34. Nobody computed that; it falls out of carrying the row's own
timestamp instead of just its answer.

**`environment.age_s` — 2.5 seconds.** The nearest particle count fell two and a half seconds
before the valve closed. That is what makes it evidence.

### The one number worth quoting for the join itself

Measured on the gateway log, review message on the broker → composite built: **22 ms and 67 ms**
on the two runs where both ends were captured. Two indexed single-row queries and a tag read.
The join is not the expensive part of anything.

## The risk beat — four things measured, not asserted

**1. The batch lookup has a tie-break that is not optional.** One `manual_advance` click writes
`operation_end` and `operation_start` in one transaction **sharing one `occurred_at` to the
millisecond**. Order on `occurred_at` alone and it is the planner's choice which row 07 reads —
and the wrong one names the operation that just *ended*. `ORDER BY occurred_at DESC, id DESC` is
what makes the incoming operation win. Verified against rows 33 and 34, which share
`2026-08-30T22:50:19.034Z`: a sample drawn after them resolves to `batch_end / IDLE`, the higher
id, and not to `operation_end / HARVEST`.

**2. `IDLE` is an answer; empty was not.** Before 2026-08-30 a `batch_end` row carried an empty
operation, so a sample drawn between batches produced a composite with a blank where the reactor
state should be. **An empty string is not something a GxP document can carry.** `bes_batch` now
writes `IDLE`, which is what the reactor is actually in and what the tag already read. Row 11,
from 2026-08-26, is the last one that reads empty — leave it as the before-picture.

**3. A missing lookup is stated, not hidden.** Measured with a sample instant that predates the
reactor's first batch event:

```json
"batch_context": null,
"batch_context_reason": "no bes.batch_event row for br-201 at or before 2026-08-20T12:00:00.000Z"
```

The message published anyway, with every other key in place. **That is the behaviour that
matters on stage** — the demo where something is missing is the demo people remember, and the
document has to survive it.

**4. Age is what stops a stale reading lying.** The same probe found a particle count nine days
away from its sample instant and reported it: `age_s: 786110.5`, `nearest_side: "after"`. Nothing
refused, nothing was silently substituted, and no reader could mistake that for evidence. That is
"no tolerance, age always reported" doing its job.

> **What a stopped MET ONE actually looks like.** *Nearest either side* means the environment
> block goes `null` only when `em.reading` holds **no row at all** for the device. Stop the
> simulator and 07 keeps answering with the last reading it has, and `age_s` grows — which is the
> finding, in a field, rather than a gap. Measured: sim stopped, sample drawn two minutes later,
> **`age_s: 122.6`** against the 27.2 s pattern 6 normally guarantees. Do not expect a `null` here
> on a database that has been running.
>
> The age is **sample against reading**, not *now* against reading, so it does not climb while the
> sample sits in review. That is right for a record about a sample, and it stops somebody reading
> a static number as a stuck one.

## What this pattern deliberately does not do

**No new tables, no ACL change, no new datasource.** If a step seems to need one, something has
been misread. 07 reads `bes.batch_event` and `em.reading` through the existing `ICC26`
datasource and writes nothing anywhere.

**It does not join `plant.equipment`.** That table still holds `BR-201` in the wrong case and
four `vib-*` leftovers. The composite carries the bare `equipment_id` and the bioreactor UDT's
own `asset_data/equipment_identifier`, and a join added to tidy it up would put a fifth spelling
of the vessel into a GxP document. **Do not add one.**

**It does not touch patterns 1–6.** They are finished and verified. Everything they needed to
change for 07 was changed and committed before 07's spec was written.

**`br-202` is not in scope.** It is a live UDT instance fed over Sparkplug, but `lims-bridge`'s
ACL does not subscribe to its topic, so its samples never reach the LIMS and 07 has no path to
it. **It looks live in Tag Explorer and is not** — that is the trap, and it is worth thirty
seconds only if somebody asks.

## On stage

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/qc/#' -v
```

| Beat | Trigger | What lands |
|---|---|---|
| Silence is the pass | Approve a clean sample at <http://localhost:8000> | The review lands on `lims/sample-result`. **Nothing** on `deviation` — the compliant case is quiet |
| The deviation | Press **Dirty** at <http://localhost:8089>, wait for a reading, draw and approve | One message on `deviation`, `values.violations[0].code` = `environmental_excursion` |
| Read it out loud | — | `ts` vs `ingest_ts`, `as_of` vs `ts`, `age_s`. Three gaps, no arithmetic |
| A rejection is a disposition | Reject instead | Publishes on its own, `violations[0].code` = `failed_review`. Nothing downstream infers a rejection from silence |
| Outside the window | Advance past HARVEST in Tag Explorer, badge again, approve | `operation: "IDLE"`, `qualified_window: false`, **and nothing empty** |
| A gap is a finding | `docker stop icc26-sim-metone`, wait, approve again | `age_s` climbs past the 27 s the poll normally guarantees — a stale reading, not a silence, so this alone is not yet a deviation |
| Nothing says how it arrived | Scroll the watcher | Three topics, three `meta.mechanism` values, one sample |

**Put `br-201` back in `GROWTH` before you demo.** It is parked there on `B-20260830-02` on
purpose, because that is the qualified window and a sample drawn now comes back
`qualified_window: true`. Advancing past HARVEST for the negative beat changes that, and the
reactor has to be walked round to GROWTH again — `CIP → SIP → INOC → GROWTH`, four clicks.

**And check the Chariot trial first.** It is two hours, the broker refuses to start when it
lapses, and the container reports `healthy` throughout. 07 is the segment where a dead broker
looks exactly like a bug in the pattern.

## Progress log

| Date | Change |
|---|---|
| 2026-08-30 | Written as the closing step of pattern 7's build, per the two-document convention. Every number in it was measured on the running stack the same evening — the 41 s provenance gap, the 22 / 67 ms join, the IDLE tie-break against rows 33/34, and the two probes that exercise the null-with-reason path. The stopped-MET ONE beat is corrected against the spec's checkpoint wording: *nearest either side* makes the environment block `null` only on an empty table, so a stopped simulator shows a growing `age_s`, not a silence. |
