# 06 — Poll / watermark of the turbidity meter's database

> Talk track for pattern 6. The spec this will be built from is
> [`plans/06-poll-turbidity.md`](plans/06-poll-turbidity.md). Architecture decisions live
> in [`00-architecture.md`](00-architecture.md); this file is what you speak.
>
> **Planned — not yet built.** Shares pattern 5's database. The MET ONE particle counter
> is not the source.

| | |
|---|---|
| **Pattern** | 6 of 7 — poll, because the system of record will not call you |
| **Mechanism tag** | `meta.mechanism = "poll"` |
| **Source** | the same `turbidity.reading` table as pattern 5 |
| **Path** | Ignition JDBC, `WHERE id > :watermark`, Transmission |
| **Topic** | `icc26/site1/downstream/tff-301/turbidity-01/telemetry` (same as pattern 5) |
| **Depends on** | pattern 5's simulator + database. Not on Debezium |
| **Blocks** | nothing |

## Talk points

**Polling is the integration most of the room has actually shipped.** A timer, a
`WHERE id > :last_id`, a tag that remembers the watermark. Pattern 5 tails the WAL.
This is the other door out of the same store.

**The failure is a stalled loop, not a wrapping Modbus buffer.** Rows accumulate; when
the poll resumes you are late (catch-up) or you dropped data (jump the watermark). Show
the gap. CDC, running beside it, did not drop them.

**A subscriber still cannot tell from the topic which colour is which.** Same address,
`meta.mechanism` carries the demo. That is the namespace claim, live.

The 2026-08-19 rejection of turbidity was about the *signal* (deadband overlaps Sparkplug
RBE). This pattern needs the *store*. The particle counter's Rotate Buffer checkbox was a
good aside; it is not this talk.

## The chain

```
sim-turbidity ──INSERT──▶ turbidity.reading
                              │  JDBC, SELECT only
                              ▼
                    Ignition timer + watermark tag
                              │
                              ▼
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: poll)
```

## The failure demo

1. Debezium off. Poll publishes contiguous ids, `mechanism=poll`. Normal.
2. Clear `poll_enabled`. Let several rows land.
3. Set it true. Burst of late messages, ids contiguous, `ingest_ts` is now, `ts` is the
   stall. Catch-up.
4. Optional: jump the watermark instead. Ids skip. Say that is a one-line implementation
   choice, and it loses data.
5. Debezium on during the stall: the skipped (or late) ids are already on the topic as
   `cdc`. Two colours, one address.
