# 07 — The sample chain: the composite record

> Pattern 7 of 7. Built from [`plans/07-sample-chain.md`](../plans/07-sample-chain.md);
> architecture decisions are in [`00-architecture.md`](../00-architecture.md).
>
> Every pattern before this one brings a kind of equipment onto a common backbone. This one is the
> reason for having done so.

| | |
|---|---|
| **Pattern** | 7 of 7: scripted aggregation, the composite GxP event |
| **New container** | **none.** One script module, one Event Stream, no new table, no ACL change, no new datasource |
| **Depends on** | 1 (the draw), 3 (the numbers), 4 (the signature and the trigger), 5 (the batch), 6 (the room) |
| **Feeds** | nothing. This is the end of the chain |
| **Signal contributed** | **the composite**: the one record no source system could have produced |
| **GxP relevance** | Derived data with disposition consequence, the heaviest item on the list |

## Overview

Six mechanisms have published. Every one of them was a different way of getting a fact onto the
wire. **Pattern 7 is the first thing in this stack that reads.**

It listens for the LIMS review, joins in what the batch system and the particle counter knew at
the sample instant, and publishes to `icc26/site1/qc/deviation` **only when something was
violated**. Approve an in-spec sample and nothing is published, which is the compliant case.
Press **OOS** on the particle counter panel, draw and approve again, and one message lands whose
`values.violations` says what was wrong.

Every flag in that document was computed by the pattern that produced it. 07 decides *whether* to
speak; it never decides what is out of spec. And when a lookup finds nothing, the document says
so instead of guessing.

## Key ideas

**1. Nothing in this pattern is clever, and that is the whole claim.** 07 does no arithmetic. It
holds no process knowledge. It computes no flags. `qualified_window` was decided by `bes_batch`
at the moment the operator advanced the reactor; its `QUALIFIED` tuple is the only copy of the
batch protocol in the stack. `status` was decided by `particle_counter_poll` against
`config/excursion_threshold`, the only copy of the cleanroom limit. **07's only job is to be the
thing that asks.** If it ever tested `operation == "GROWTH"` itself, the stack would hold two
copies of a rule, and two copies of a rule drift.
[`00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md).

**2. The question is one nobody could answer before.** Not *"was the sample in spec"*; the LIMS
answers that. The question is:

> *This sample, drawn at this instant by this person, analysed to these numbers, signed off by
> this analyst — what was the reactor doing, and what was the room doing, at the moment it was
> taken?*

Four systems each hold a fragment that means nothing alone. The valve knows who drew it. The
analyzer knows the numbers. The batch system knows the operation. The particle counter knows the
room. **No single one of them can be asked this question**, and none of them was modified to
answer it.

**3. Only deviations publish.** A sample with no violations produces no message at all, so a
quiet topic is the compliant state and the gateway logs `... is clean; no deviation published`.
The triggers are `em.reading.status == "excursion"` and `disposition == "fail"`, both read as flags
their owning modules already set. `qualified_window` is reported but deliberately not a trigger,
because it is false for four operations of five.

**4. The two lookups do not key on the same column.** `bes.batch_event` keys on the *sample's*
`equipment_id`: `br-201`, parsed by pattern 1 out of its own topic and carried the whole way.
`em.reading` has **no `equipment_id` column at all**: it keys on `device_id`, a constant, because
the particle counter is one instrument for the room and not one per vessel. Its `location` field
reads

```
USP Suite A - BR-201 sample port
```

and that string is how the room ties to the vessel. **It is prose for a human, not a join key.**
07 carries it into the composite so a reader can see the association, and parses nothing. That is
how a real plant usually looks: the association exists, and it lives in a text field somebody
typed.

**5. A gap is a finding, not a silence.** The particle counter rule is *nearest reading either
side of the sample, no tolerance, age always reported*. A reading three seconds **after** the
valve closed is better evidence than one twenty-five seconds before, so the search is not
restricted to the past. There is no cutoff at which 07 refuses to answer: it reports the nearest
reading and its age, and lets the reader judge. That makes `age_s` load-bearing, so it sits at the
**top** of the block: a forty-minute-old count must not be able to read as current.

When a lookup genuinely finds nothing, the block is `null` and a `_reason` sits beside it: never a
missing key, never a silent default. An `environment` block that is null is itself a violation
(`environment_unverifiable`), because a sample whose room cannot be evidenced cannot be released.

**6. A subscriber still cannot tell how any of it arrived.** One `mosquitto_sub` shows the same
`values.sample_id` on several topics whose names say nothing about transport, and no field in any
payload says it either. Patterns 4 and 7 are the only two backbone subscribers, so 07 is a genuine
subscriber, not a database job wearing a hat.

## The chain

```
                            a person clicks Approve (or Reject)
                                          │
                     icc26/site1/qc/lims/sample-results-released   (ts + values)
                                          │
                        MQTT Engine Event Stream source, QoS 1
                                          ▼
                            ┌──────────────────────────┐
   bes.batch_event ────────▶│   sample_chain.build()   │◀──────── em.reading
   WHERE equipment_id = ?   │                          │   WHERE device_id = 'particle-counter-01'
   AND occurred_at <= ?     │  no arithmetic, no flags │   ORDER BY nearest either side
   ORDER BY occurred_at     │  no process knowledge    │
            DESC, id DESC   └────────────┬─────────────┘
                                         │  filter: sample_chain.is_deviation()
                                    Transmission
                                         ▼
                     icc26/site1/qc/deviation      (ts + values)
                                  QoS 1, retained false
```

**The filter is the only stage that can stop a message.** A transform returning `None` does
**not** suppress one in Ignition's Event Streams: it publishes the four-byte string `None`. So the
gate is in `filter.userCode`, not in the transform.

**Retained false**, because a retained composite replays a stale record to every new subscriber
and presents it as current.

**Nothing on the broker changed.** `ign-engine` already subscribed `icc26/#` and
`ign-transmission` already published `icc26/#`, so
[`mqtt-users.json`](../../compose/chariot/mqtt-users.json) is untouched.

## The wire

The document's shape, trimmed. The `collection`, `results`, `batch_context` and timing are from
the 2026-08-30 capture (`S-20260830-0085`); the room's excursion and `violations` entry are the
shape `sample_chain._violations` produces when the room was out of spec:

```json
{
  "ts":  "2026-08-30T23:34:54.774Z",
  "values": {
    "violations": [
      {
        "code": "environmental_excursion",
        "detail": "particle counter reported excursion, 2.5s before the sample",
        "source": "em.reading.status"
      }
    ],
    "sample_id": "S-20260830-0085",
    "equipment_id": "br-201",
    "equipment_identifier": "br-201",
    "batch_id": "B-20260830-02",
    "disposition": "pass",
    "analyst": "M. Martin",
    "assessed_at": "2026-08-30T23:35:35.963Z",

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
      "status": "excursion",
      "occurred_at": "2026-08-30T23:34:52.235Z",
      "channels": [ { "size_um": 0.5, "count": "…" }, "…" ],
      "conditions": { "flow_rate_lpm": 28.3, "temperature_c": 22.6, "humidity_pct": 43.5 }
    },
    "environment_reason": null
  }
}
```

**`violations` is never empty on a message that exists.** Each entry names its evidence: `code` is
what a subscriber routes on (`environmental_excursion`, `environment_unverifiable`,
`failed_review`), `detail` is prose, and `source` names the field the flag came off. A sample that
passed every analytical spec can still deviate on the room.

Three gaps, with no arithmetic in 07:

- **`ts` against `values.assessed_at`: 41 seconds.** `ts` is the acquisition instant, the moment
  the valve closed. `assessed_at` is when 07 assembled the record. The gap between them **is** the
  record's provenance, visible in one message.
- **`batch_context.as_of` against `ts`: 39 minutes.** The reactor entered GROWTH at 22:55 and the
  sample was drawn at 23:34. Nobody computed that; it falls out of carrying the row's own timestamp
  instead of just its answer.
- **`environment.age_s`: 2.5 seconds.** The nearest particle count fell two and a half seconds
  before the valve closed. That is what makes it evidence.

**The join itself is cheap.** Measured on the gateway log, review message on the broker →
composite built: **22 ms and 67 ms** on the two runs where both ends were captured. Two indexed
single-row queries and a tag read.

## Risks, measured

**1. The batch lookup has a tie-break that is not optional.** One `manual_advance` click writes
`operation_end` and `operation_start` in one transaction **sharing one `occurred_at` to the
millisecond**. Order on `occurred_at` alone and it is the planner's choice which row 07 reads, and
the wrong one names the operation that just *ended*. `ORDER BY occurred_at DESC, id DESC` is what
makes the incoming operation win. Verified against rows 33 and 34, which share
`2026-08-30T22:50:19.034Z`: a sample drawn after them resolves to `batch_end / IDLE`, the higher
id, and not to `operation_end / HARVEST`.

**2. `IDLE` is an answer; empty was not.** A `batch_end` row once carried an empty operation, so a
sample drawn between batches produced a composite with a blank where the reactor state should be.
An empty string is not something a GxP document can carry. `bes_batch` now writes `IDLE`, which is
what the reactor is actually in and what the tag already read.

**3. A missing lookup is stated, not hidden.** Measured with a sample instant that predates the
reactor's first batch event:

```json
"batch_context": null,
"batch_context_reason": "no bes.batch_event row for br-201 at or before 2026-08-20T12:00:00.000Z"
```

The document kept every other key in place.

**4. Age is what stops a stale reading lying.** The same probe found a particle count nine days
away from its sample instant and reported it: `age_s: 786110.5`, `nearest_side: "after"`. Nothing
refused, nothing was silently substituted, and no reader could mistake that for evidence.

> **What a stopped particle counter actually looks like.** *Nearest either side* means the
> environment block goes `null` only when `em.reading` holds **no row at all** for the device.
> Stop the simulator and 07 keeps answering with the last reading it has, and `age_s` grows:
> the finding is in a field, rather than a gap. Measured: simulator stopped, sample drawn two
> minutes later, **`age_s: 122.6`** against the 27.2 s pattern 6 normally guarantees.
>
> The age is **sample against reading**, not *now* against reading, so it does not climb while the
> sample sits in review.

## What this pattern deliberately does not do

**No new tables, no ACL change, no new datasource.** 07 reads `bes.batch_event` and `em.reading`
through the existing `ICC26` datasource and writes nothing anywhere.

**It does not join `plant.equipment`.** That table still holds `BR-201` in the wrong case and four
`vib-*` leftovers. The composite carries the bare `equipment_id` and the bioreactor UDT's own
`asset_data/equipment_identifier`, and a join added to tidy it up would put a fifth spelling of
the vessel into a GxP document.

**It does not touch patterns 1–6.** Everything they needed to change for 07 was changed before
07's spec was written.

**`br-202` is not in scope.** It is a live UDT instance fed over Sparkplug, but `lims-bridge`'s
ACL does not subscribe to its topic, so its samples never reach the LIMS and 07 has no path to
it. It looks live in Tag Explorer and is not.

## Try it

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/qc/#' -v
```

| Step | Do | What you see |
|---|---|---|
| Silence is the pass | Approve an in-spec sample at <http://localhost:8000> | The review lands on `lims/sample-results-released`. **Nothing** on `deviation` |
| The deviation | Press **OOS** at <http://localhost:8089>, wait ~30 s for a reading, draw and approve | One message on `deviation`, `values.violations[0].code` = `environmental_excursion` |
| Three gaps | Read the message | `ts` vs `assessed_at`, `as_of` vs `ts`, `age_s` |
| A rejection is a disposition | Reject instead | Publishes on its own, `violations[0].code` = `failed_review` |
| Outside the window | Advance past HARVEST in Tag Explorer, badge again, approve | `operation: "IDLE"`, `qualified_window: false`, **and nothing empty** (and no message unless something else was violated) |
| A stale reading | `docker stop icc26-sim-particle-counter`, wait, approve again | `age_s` climbs past the 27 s the poll normally guarantees. A stale reading, not a silence, so this alone is not a deviation |

**Put `br-201` back in `GROWTH`** when you are done, and press **In-spec** on the particle
counter. Walking the reactor round is four clicks (`CIP → SIP → INOC → GROWTH`).

**Check the Chariot trial first.** It is two hours, the broker refuses to start when it lapses,
and the container reports `healthy` throughout. A dead broker looks exactly like a bug in this
pattern.

## Read from outside the gateway

[`services/i3x-client/`](../../services/i3x-client/) reaches the same environmental verdict over
the i3X API, from a separate container, on one Ignition login, and can publish its finding to
`icc26/site1/qc/i3x-meta-review-completed`. Its page at <http://localhost:8092> shows every
request and response.
