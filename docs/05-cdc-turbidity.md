# 05 — CDC of a turbidity meter's local database

> Talk track for pattern 5. The spec this will be built from is
> [`plans/05-cdc-turbidity.md`](plans/05-cdc-turbidity.md). Architecture decisions live
> in [`00-architecture.md`](00-architecture.md); this file is what you speak.
>
> **Planned — not yet built.** Vendor API still TBD; the story does not wait on it.

| | |
|---|---|
| **Pattern** | 5 of 7 — CDC, because the application will not call you |
| **Mechanism tag** | `meta.mechanism = "cdc"` |
| **Source** | turbidity meter's local Postgres, database `turbidity` |
| **Path** | Debezium Server (MQTT) → envelope mapper → Chariot |
| **Topic** | `icc26/site1/downstream/tff-301/turbidity-01/telemetry` (same as pattern 6) |
| **Depends on** | new catalog + `sim-turbidity`. Not on Ignition |
| **Blocks** | pattern 6's poll (same table) |

## Talk points

**CDC's real use case is an application you do not own.** A workstation next to the TFF
skid, vendor software, a database that is the product. You will not get a webhook. You tail
the WAL as an out-of-band observer, under a login the application does not know about —
that is the `cdc` role, and it is why it is not `icc26`.

**Pattern 6 polls the same database.** Two mechanisms, one source, one topic. When an
instrument only writes locally, CDC vs poll *is* the Monday-morning choice. It is not the
old LIMS triple. Pattern 4 is a different instrument.

**If the observer is down, the rows wait.** Stop Debezium, let the meter keep writing,
start Debezium: no gaps. That is the WAL. Pattern 4's POST, if Ignition was down, is gone.

## The chain

```
sim-turbidity ──INSERT──▶ database `turbidity`
                              │
                              │  pgoutput, user `cdc`
                              ▼
                        Debezium Server ──MQTT──▶ mapper ──▶ Chariot
                                                          │
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: cdc)
```

Ignition is not in this path.

## The failure demo

1. Watch the topic. One insert, one `mechanism=cdc` message. Normal.
2. Stop Debezium. Let several rows land (`SELECT max(id) FROM reading`).
3. Start Debezium. Those ids appear, in order, no gaps.
4. Say the line: the instrument never knew we were listening. Then stall pattern 6's
   timer and show the same rows arriving late, or not at all, in the other colour.
