# 04 — NovaFlex HTTPS webhook

> Talk track for pattern 4. The spec this will be built from is
> [`plans/04-novaflex-webhook.md`](plans/04-novaflex-webhook.md). Architecture decisions live
> in [`00-architecture.md`](00-architecture.md); this file is what you speak.
>
> **Planned — not yet rebuilt.** Do not demo `:8000` as pattern 4. The as-built LIMS talk
> track is in Extra: [`extra/lims-webhook.md`](extra/lims-webhook.md).

| | |
|---|---|
| **Pattern** | 4 of 7 — webhook, because that is all the analyzer can emit |
| **Mechanism tag** | `meta.mechanism = "webhook"` |
| **Source** | the same NovaFlex as pattern 3, HTTPS POST on sample complete |
| **Ignition** | Event Stream HTTP source → Transmission |
| **Topic** | `icc26/site1/qc/analyzers/novaflex-01/result` (same as pattern 3) |
| **Depends on** | the NovaFlex simulator (already live). Does **not** consume pattern 3's MQTT |
| **Blocks** | nothing |

## Talk points

**A lot of lab instruments do not speak MQTT, OPC UA, or SQL. They POST JSON.** Pattern 3 is
the FLEX2 as Nova actually ships it. Pattern 4 is the same instrument imagined with only a
callback URL in a config screen — which is what most of the room has actually integrated.

**The Event Stream is the same shape as pattern 3.** Tag-change in, HTTP POST in; both leave
through Transmission. The mechanism is not the transport.

**If Ignition is down, the POST fails, and the backbone never hears about that sample.** There
is no outbox on this pattern. The durable version of the same problem is pattern 5: the
instrument wrote a row in a database you do not own, and you tail the WAL.

One sample, two colours, one topic. `meta.correlation_id` is `sample_id`. That is how you
prove the namespace does not leak the mechanism.

## The chain

```
opcua-novaflex ──HTTPS POST──▶ Ignition Event Stream ──▶ Transmission
                                                          │
                    icc26/site1/qc/analyzers/novaflex-01/result   (mechanism: webhook)
```

Pattern 3 is the OPC UA path onto the **same topic** (`mechanism: opcua-event`). The analyzer
publishes nothing itself.

## The failure demo

1. Complete a sample. One webhook message, same topic as OPC UA. Normal.
2. Stop Ignition (or disable the Event Stream). Complete another sample. The POST fails.
   Show the silent topic.
3. Say the line: the result is on the analyzer, and unless it queued the POST, we will never
   see it. If this instrument had written a local database instead, that is pattern 5.
