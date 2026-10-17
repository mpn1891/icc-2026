# 06 — Poll: the monitoring system that is up, connected, and blind

> Pattern 6 of 7. Built from
> [`plans/06-poll-particle-counter.md`](../plans/06-poll-particle-counter.md); architecture
> decisions are in [`00-architecture.md`](../00-architecture.md). Every number below was measured
> on the running stack and is recorded in the spec's checkpoints.

| | |
|---|---|
| **Pattern** | 6 of 7: poll and diff, on somebody else's HTTP API |
| **Container** | `sim-particle-counter`: GraphQL over HTTPS on `:8443`, JWT auth |
| **Operator touchscreen** | <http://localhost:8089>: Start/Stop, the sample point, **In-spec / OOS** room |
| **Acquisition** | An Ignition gateway timer, 30 s fixed delay. **Nothing pushes** |
| **Depends on** | the `ICC26` datasource and the Gateway Scripting Project |
| **Feeds** | pattern 7 reads `em.reading` for the room at the sample instant |
| **Signal contributed** | environmental excursion status, at or nearest to the sample instant |
| **GxP relevance** | A **characterized** detection gap. Not fatal, but it belongs in the assessment |

## Overview

A particle counter in the suite, on the sample port of `BR-201`. It has a real API (GraphQL, JWT,
cursor pagination) and **it does not know Ignition exists.** Ignition finds out on a timer.

Press Start on the instrument's touchscreen and within 30 seconds a burst of results appears on
the backbone: three at a time, not one every ten seconds. Press **OOS** and `status` flips.

Two risks: the detection gap, which is a number rather than a worry, and the failure that
matters most: the poll working perfectly and publishing nothing.

## Key ideas

**1. The gap is characterized, and that is the GxP move.** Not *"we might miss something"* but:
**we sample for 10 seconds and look every 30, so a reading is on the backbone within 40 seconds
of the air going out of specification, worst case.** Measured, the steady state is tighter: a
**7.2 / 17.2 / 27.2 second sawtooth**, three analyses stacked under one poll, repeating exactly.
The question an assessment has to answer is what that number would need to be.

**2. The vendor shipped a change feed and documented it as pagination.** `getSamples(cursor,
limit)` returns everything *after* a bookmark, oldest first, plus a fresh bookmark. Because new
analyses get ever-increasing ids appended to the end, *"everything after bookmark 42"* and
*"everything new since I last looked"* are the same request: paging through a static list and
following a growing one are the identical operation. Nothing had to be added to the vendor's API
to make this a poller.

**3. What we did not add to their API matters.** No `since_id`. No `status` on the record. No
`location` field. No "give me everything since restart" reset. Each absence has a cost and we pay
it on our side of the boundary: we keep the watermark, the poll script computes the flag, the
operator sets the room, and **the stale-cursor trap stays, because removing it would have meant
editing somebody else's product.** `startSampling`, `stopSampling` and `clearSamples` are all in
the schema, and **Ignition calls none of them.**

**4. The excursion flag is Ignition's rule, in exactly one place.** The poll compares the ≥0.5 µm
raw count against `config/excursion_threshold` and writes `status ∈ normal | excursion` at ingest.
The number is **1660**: 352,000 per cubic metre at the 4.717 L a 10-second draw takes, which is
ISO 14644-1 Class 7 / EU GMP Grade C at rest. In-spec measures 113–137, OOS 3346–4350. An order of
magnitude of headroom either side, and a limit that is a **real grade** rather than a number
picked to sit between two histograms. Pattern 7 reads that flag and never compares counts itself.

> **One channel, not two.** The 5.0 µm count is published and stored and is **not** compared: at
> Class 7 the two limits are two orders of magnitude apart, so one threshold either never fires on
> the coarse channel or fires constantly on the fine one. A second threshold tag would be a
> **second copy of the cleanroom spec**, which is the thing the derived-flags rule exists to
> prevent.

**5. Raw counts bind the threshold to a sample volume.** At 28.3 LPM for 10 seconds each analysis
draws ~4.7 L, so a raw count means something only while the duration is 10. Real EM systems
compare to a limit in counts per cubic metre, and that conversion (about 35×) is the classic place
for an arithmetic bug between systems. Two cheap mitigations are in place: the threshold tag's
documentation string records the duration it was chosen for and the per-m³ equivalent, and
`values.total_volume_l` goes on the wire so a consumer can normalise even though we did not.

**6. The instrument's clock and the backbone's are different clocks, and every message says so.**
`ts` is the instrument's `completedAt`; `values.ingest_ts` is when the poll found it. Those two
differing by tens of seconds, in every message, **is the detection gap on the wire**.

## The chain

```
        an operator presses Start on the instrument's own panel  :8089
                                  │
                sim-particle-counter — GraphQL over HTTPS  :8443
                  10 s draws, its own clock, nothing pushed
                                  │
                     ┌────────────┴────────────┐
                     │   Ignition gateway timer │  30 s fixed delay
                     └────────────┬────────────┘
                                  ▼
                    particle_counter_poll.poll()
             authenticate → getSamples(cursor) → walk while hasMore
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                            ▼
              em.reading                system.eventstream.publishEvent
        (UNIQUE on the vendor's                  │
         analysis uuid)                          ▼
                                   Event Stream 06_poll/particle-counter-result
                                                 │  transform builds the document
                                                 ▼
                                            Transmission
                                                 ▼
        icc26/site1/env_monitoring/particle-counter-01/sample-analyzed
```

**The watermark is two memory tags**, `state/cursor` and `state/last_sequence`. They survive a
gateway restart on the tag provider's value persistence and can still be cleared by hand in Tag
Explorer, which is the recovery below.

## The wire

```json
{
  "ts": "2026-08-29T14:03:22.145Z",
  "values": {
    "ingest_ts": "2026-08-29T14:03:41.002Z",
    "sequence_number": 1041,
    "status": "normal",
    "location": "USP Suite A - BR-201 sample port",
    "operator": "Admin User",
    "started_at": "2026-08-29T14:03:12.145Z",
    "completed_at": "2026-08-29T14:03:22.145Z",
    "total_volume_l": 4.717,
    "channels": [
      { "size_um": 0.3, "count": 254 },
      { "size_um": 0.5, "count": 140 },
      { "size_um": 5.0, "count": 1 }
    ],
    "flow_rate_lpm": 28.31,
    "temperature_c": 22.4,
    "humidity_pct": 45.2
  }
}
```

**`values.sequence_number` is the instrument's own `sequenceNumber`**: the source system's
monotonic number, never one we invented.

**`ts` + `values` and nothing else**, the same shape as pattern 3. Nothing subscribes to this topic
(pattern 7 reads `em.reading` out of Postgres), so the only extra field that earns its place is
the ingest instant.

### What one poll actually looks like

Three analyses per poll do **not** leave together, and the shape is the Event Stream's batching
rather than anything in the poll script:

| | published by `poll()` | on the wire |
|---|---|---|
| first analysis | 00:52:08.970 | 00:52:08.977 (**+8 ms**) |
| second | 00:52:08.975 | 00:52:09.238 (**+263 ms**) |
| third | 00:52:08.980 | 00:52:09.239 (**+260 ms**) |

A leading-edge 250 ms debounce: the first event goes straight through and the rest of the window
is coalesced. Identical across five consecutive polls, order preserved end to end, newest last.
So the wire shows a burst of three every 30 seconds, not one message every 10: **the instrument
samples on its own clock and the backbone learns in clumps.**

## Failure modes

**The stall.** Uncheck `config/enabled`. The instrument keeps sampling and the backbone goes
quiet. Re-enable, and the backlog arrives in one burst: the cursor walk draining `hasMore`,
visible on the wire.

> Use `config/enabled`, **not** `config/poll_interval_s`. That tag is decorative: a gateway
> timer's delay is read when the resource loads, no tag can change it at runtime, and the poll
> never reads it. The cadence has two written homes (the timer resource and the tag that
> documents the number) and they are changed together, by hand.

**The silent stale cursor.** `docker restart icc26-sim-particle-counter`. The buffer regenerates
from id 1 while the stored bookmark still says 45, the server answers *"nothing after 45"*
(correctly), and **the poll runs perfectly while publishing nothing.**

The gateway is up. The timer fires. The HTTP call returns 200. The token refreshes on schedule.
`state/last_error` stays empty. **Every health check is green.** A monitoring system that is up,
connected, authenticated, and blind.

> The panel at `:8089` has a **Sequence persistence** switch below the instrument case. It must
> read **OFF** to reproduce this: on, the counter survives the restart and the poll simply picks
> up. Off is the default and the switch persists.

**The recovery is one tag.** Clear `state/cursor` in Tag Explorer and leave `state/last_sequence`
alone: the poll drops its dedupe floor to zero whenever the cursor is empty, and the unique key on
the vendor's analysis uuid is what makes that safe. The backlog drains fast (**measured at 414
records in a single poll**) because one poll walks every page rather than one page per cycle.

> **It happened for real.** On 2026-08-30 a verification restarted the simulator and left the
> cursor past the end. Pattern 6 was dark for **15.5 hours** and every check stayed green.
> `tasks.py health` now warns when nothing has landed in `em.reading` for 180 s and prints the tag
> to clear. It keys on `ingested_at`, not `occurred_at`, because during the recovery's own backlog
> drain the instrument's timestamps read hours stale, and the obvious column would fire a warning
> at the exact moment somebody is fixing it.

## Risks, measured

**1. One analysis, one row, one message, and zero happens exactly once, deliberately.** 96
analyses became 96 rows and 96 messages by hand, then 664 rows with 664 distinct analysis ids on
the timer. An insert that affects no rows suppresses the publish, which is what makes a
redelivery harmless.

**2. The dedupe key had to be the vendor's uuid, not the sequence number.** Sequence numbers
restart at 1 when the instrument restarts (which *is* the stale-cursor case), so keying on them
makes every reading of a fresh run collide with the old run's rows and vanish.

**3. A token expiry is invisible when it works.** 315 seconds after the previous poll, against a
300-second TTL, the cached token was rejected; the script logged *"token expired;
re-authenticating"* and published all **32** analyses that had accumulated. No gap, no lost record.

**4. There is a second way to look exactly like the stale cursor, and it is the broker.** When the
Chariot trial lapses, Transmission reconnect-loops, the poll stores and returns normally, the
cursor advances, `last_error` stays empty, and the wire is silent. **The store and the wire
disagree, which the stale cursor never does**, so one query tells them apart, and it is the one
`tasks.py health` runs.

## What this pattern deliberately does not do

**It does not command the instrument.** Start, Stop and Clear are in the vendor's schema and
Ignition calls none of them. The same change-control boundary pattern 3 keeps with the analyzer's
104 writable bits.

**It does not normalise to counts per cubic metre.** See key idea 5: the volume goes on the wire
so a consumer can, and the threshold's documentation string records what it was chosen against.

**It does not enforce that only one poller exists.** Two gateways against one instrument would
each advance their own cursor and publish duplicates.

## Try it

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/env_monitoring/particle-counter-01/sample-analyzed' -v
```

| Step | Do | What you see |
|---|---|---|
| Nothing free-runs | `python tasks.py health` | The `sim-particle-counter` line must say SAMPLING. A forgotten **Start** looks exactly like the stale cursor |
| The burst | Press **Start** at <http://localhost:8089> | Within 30 s, **three** messages ~270 ms apart, not one every 10 s |
| The gap, on the wire | Compare `ts` with `values.ingest_ts` | Tens of seconds, in every message |
| The room goes out of spec | Press **OOS** | The next analysis publishes `status: "excursion"`: 3346–4350 counts against 1660 |
| …and back in spec | Press **In-spec** | `normal` on the one after. **Do this**, or every later sample deviates in pattern 7 |
| The stall | Uncheck `config/enabled`, wait, re-enable | Silence, then the backlog in one burst |
| Up, connected, and blind | `docker restart icc26-sim-particle-counter` | Everything green, nothing on the wire. Clear `state/cursor` to recover |

**The room condition, the sample point and the run state all survive that restart**, so the
instrument comes back sampling and nothing has to be re-typed. Only Ignition is blind.
