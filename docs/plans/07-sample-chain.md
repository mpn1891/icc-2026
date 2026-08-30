# Pattern 7 — the sample chain

> **Written 2026-08-30**, the evening the pre-07 cleanup closed. Branch `pattern7_and_cleanup`,
> five commits ahead of `main` at `19fb732`.
>
> **Written to be executed cold**, in the shape [`00-pre-07-cleanup.md`](00-pre-07-cleanup.md)
> had — everything needed is here or one link away, and nothing below asks the reader to
> reconstruct a decision. Every claim was checked against the running stack on 2026-08-30, not
> read off a spec. Where something is unknown it says so.

| | |
|---|---|
| **Goal** | One composite record per reviewed sample, assembled from what the other patterns already published, on the backbone |
| **Touches** | one new script module `sample_chain`, one new event stream `07_chain/lims-review`, `docs/plans/*` |
| **Does not touch** | `metone_poll`, `bes_batch`, `bes_cdc`, `lims_webhook`, the UDTs, the simulators, the schema. **07 adds no tables, no ACL change, no new datasource** |
| **Blocked by** | Nothing. Every prerequisite is built, committed and verified |
| **Unblocks** | The last talk track, and the demo's closing argument |

## Why this pattern is different

Six acquisition mechanisms publish. **07 is the first thing in this stack that reads what the
others wrote** — and that is the whole point of it on stage. Patterns 1–6 each argue that a
particular kind of equipment can be brought onto a common backbone. 07 is the argument that
having done so, a question nobody could answer before becomes a single message:

> *This sample, drawn at this instant by this person, analysed to these numbers, signed off by
> this analyst — what was the reactor doing, and what was the room doing, at the moment it was
> taken?*

Nothing in 07 is clever. It does no arithmetic, holds no process knowledge, and computes no
flags. Every fact it publishes was computed at ingest by the pattern that produced it. **07's
only job is to be the thing that asks.**

---

## What was built before it, and verified

The four checkpoints below were closed on 2026-08-30. Do not re-open them.

| CP | Check | Evidence |
|---|---|---|
| **1** | Debezium survives restarts | 3 imports / 0 failures across 3 starts; slot `icc26_debezium` active |
| **2** | Advance reaches the wire, `qualified_window` both polarities | seq 35–41 captured live on `.../br-201/batch/event`, `qw=True` on `operation_start GROWTH` |
| **3** | Both review outcomes publish `disposition` | seq 20 `pass`, seq 21 `fail`, 409 replay holds on both verbs and across them |
| **6** | `tasks.py health` fully green | no WARN lines, first time since 2026-08-26 |

Three changes landed with them that 07 depends on directly:

- **`batch_end` carries `IDLE`**, not an empty operation. Verified: `bes.batch_event` rows 23 and
  34. Row 11, from 2026-08-26, is the last one that reads empty — leave it as the before-picture.
- **`batch_id` is minted, not typed.** `B-YYYYMMDD-NN`, stamped on the first advance out of IDLE
  and cleared at `batch_end`. Verified on the wire: `B-20260830-02` on seq 35.
- **`lims.sample.equipment_id`** exists and the review message carries `values.equipment_id`,
  parsed from pattern 1's topic. Verified on the wire.

---

## The decisions 07 inherits

All seven were made 2026-08-30. **07's spec does not re-argue them; this section IS the record.**

### 1. The trigger is an MQTT Engine Event Stream source

`00-pre-07-cleanup.md` left this open with three ranked routes and a ten-second Designer check.
The check was unnecessary — the module ships the source, and the `.modl` says so.
`MQTT-Engine-signed.modl`'s `module.xml` declares a hard dependency on
`com.inductiveautomation.eventstream` in **both** Designer and Gateway scope, and the jars carry:

```
me-gateway   EventStreamSourceRegistry, EventStreamMqttSource, EventStreamSparkplugSource
me-designer  EventStreamMqttSourceEditor, EventStreamSourceDesignRegistry
MQTTCommon   EventStreamMqttSourceConfig, EventStreamTopicFilterValidator,
             EventPayloadContentType {STRING, JSON_OBJECT, BYTE_ARRAY, UNSUPPORTED}
```

`EventStreamMqttSourceConfig` holds exactly two fields: `SUBSCRIPTION_TOPIC` and `QOS`.

**This is better than route 1 in the old plan.** No subscriber service, no WebDev hop, and **07
stays a genuine backbone subscriber** — which [`04-lims-webhook.md`](04-lims-webhook.md) leans on
explicitly (*"this pattern and pattern 7 are the only two there are"*). That claim survives.

> **Still worth one glance.** This was read out of class names, not out of a running registry.
> The first time somebody opens Event Streams → New, confirm an MQTT source type is in the
> dropdown. If it is not, fall back to route 1 in `00-pre-07-cleanup.md` § 4.

### 2. Batch identity comes from `bes.batch_event`, never from the review message

`values.batch_id` on the review message is **empty for every sample the demo produces**, and
there are now **four** conventions live, not the three the old plan recorded:

| Where | Value | Count |
|---|---|---|
| every sample pattern 1 mints | *(empty)* | 232 |
| `lims.sample` | `BR-2026-014` | 59 |
| `lims.sample` seed | `B-2026-0142` | 10 |
| `bes.batch_event`, pre-2026-08-30 | `12345` | all old rows |

07 takes `batch_id` off the `bes.batch_event` row it already lands on for the operation. It is
free — same row, same query — and batch identity in the composite then comes from the batch
system rather than from the lab's copy of it.

### 3. `operation` is never empty

Handled at the source rather than in 07. See "What was built before it" above. **07 needs no
filter and no reason string** — it reads whatever the row says, and after 2026-08-30 that is
always a real ISA-88 operation or `IDLE`.

### 4. `plant.equipment` is not on 07's path

Deferred deliberately. The composite carries the bare `equipment_id` and the new
`asset_data/equipment_identifier`, and joins nothing. The table still holds `BR-201` in the wrong
case and four `vib-*` leftovers; that is written down here so the next person does not lose an
afternoon to it. **Do not add a join to make it tidy.**

### 5. Pattern 2 and `br-202` are out of scope

`br-202` is a live UDT instance fed over Sparkplug by `icc26-sim-valve-spb`, but **`lims-bridge`'s
ACL does not subscribe to its topic**, so its samples never reach the LIMS at all. 07 therefore
has no path to it. Either wire it deliberately later or leave it — but it looks live in Tag
Explorer and is not, and that is the trap.

### 6. `pg_db` stays

Not unused, as the old plan assumed. It is bound to the `pg-historian` historian provider **and**
to `System/Gateway/StoreAndForward/pg_db/Pipelines/TagHistory`. Deleting it breaks both. Scripts
still must not select it — `bes_batch` and `metone_poll` both carry the warning comment already.

### 7. The MET ONE rule: nearest either side, no tolerance

Chosen 2026-08-30 over a before-only lookup and over a 60-second cutoff.

- **Nearest either side.** A reading three seconds *after* the valve closed is better evidence
  than one twenty-five seconds before.
- **No tolerance.** 07 always reports the nearest reading and **always reports its age**, and
  lets the reader judge. A gap is a finding, not a silence.
- **Therefore the age field is load-bearing.** Put it at the top level of the reading block, not
  buried in it. A forty-minute-old reading must not be able to read as current.

Pattern 6's timer bounds the normal case to **≤ 27.2 s** (pattern 6 CP7), and the live table
shows readings landing every 10 s. So a large age only happens when nobody pressed Start — the
pre-show failure `tasks.py health` already names.

---

## What 07 actually does

### Trigger

An Event Stream, `07_chain/lims-review`, whose **source** is the MQTT Engine MQTT source
subscribed to:

```
icc26/site1/qc/lims/sample-result        qos 1
```

**Structurally this is a copy of [`06_poll/metone-result`](../../ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/06_poll/metone-result/config.json)
with the source swapped.** That file is the template — take its `handlers`, `batch`, `filter`,
`transformEncoder` and `onError` blocks as they are, and change three things: the source type,
the handler's `topic`, and the transform's one line.

```
transform   return sample_chain.build(event.data)
handler     com.cirruslink.mqtt.transmission.gateway.mqtt.handler
            serverName  chariot_broker
            topic       icc26/site1/qc/sample-chain
            qos 1, retained false
```

**No broker change.** `ign-engine` already subscribes `icc26/#` and `ign-transmission` already
publishes `icc26/#`, so nothing in
[`../../compose/chariot/mqtt-users.json`](../../compose/chariot/mqtt-users.json) moves.

**Retained false**, for the same reason `bes_cdc` gives in its own comment: a retained composite
replays a stale record to every new subscriber.

### The two lookups

Both are single rows, both hit an existing index, and **they do not key on the same column** —
this is the detail most likely to be got wrong.

```sql
-- 1. what the reactor was doing.  Keyed on the SAMPLE's equipment_id.
--    ix_batch_event_lookup (equipment_id, occurred_at DESC, id DESC)
SELECT batch_id, operation, event_type, payload->>'qualified_window'
FROM   bes.batch_event
WHERE  equipment_id = ?           -- values.equipment_id from the review message
  AND  occurred_at <= ?           -- the sample instant
ORDER  BY occurred_at DESC, id DESC
LIMIT  1;
```

```sql
-- 2. what the room was doing.  Keyed on a DEVICE, not on the reactor.
--    ix_em_reading_lookup (device_id, occurred_at DESC, id DESC)
--    em.reading has no equipment_id column at all.
SELECT status, channels, environment, occurred_at, location
FROM   em.reading
WHERE  device_id = 'particle-counter-01'
ORDER  BY abs(extract(epoch FROM (occurred_at - ?)))   -- nearest EITHER SIDE
LIMIT  1;
```

> **`em.reading` is keyed by `device_id`, and the counter is not per-reactor.** Its `location`
> reads `USP Suite A - BR-201 sample port` — that string is how the room ties to the vessel, and
> it is prose for a human, **not a join key**. Carry it into the composite so the reader can see
> the association; do not parse it.
>
> The nearest-either-side ordering does **not** use `ix_em_reading_lookup`, because the index is
> ordered and this is not. At demo volumes that is irrelevant. If it ever matters, the shape is
> two indexed queries — nearest before, nearest after — and pick the closer in Python.

### The `ORDER BY occurred_at DESC, id DESC` tie-break

Non-negotiable on lookup 1, and [`05-cdc-batch-event.md`](05-cdc-batch-event.md) explains why:
one advance writes `operation_end` and `operation_start` **sharing a timestamp to the
millisecond**, and only the `id` tie-break makes the incoming operation win. Verified live —
seq 40 (`operation_end INOC`) and seq 41 (`operation_start GROWTH`) at the same instant.

### The document

Follow the house envelope. Every other pattern uses it and 07 must not invent a new one.

```json
{
  "ts":   "<the sample instant — values.collection.sample_completion>",
  "seq":  0,
  "source": { "id": "sample-chain", "type": "aggregate" },
  "meta": {
    "mechanism": "aggregate",
    "ingest_ts": "<now>",
    "correlation_id": "<sample_id>"
  },
  "values": {
    "sample_id":    "...",
    "equipment_id": "br-201",
    "batch_id":     "B-20260830-02",
    "disposition":  "pass",
    "analyst":      "...",

    "collection": { "...": "carried through from the review message, unchanged" },
    "results":    [ { "analyte": "glucose", "value": 1.4, "uom": "g/L" } ],

    "batch_context": {
      "operation":        "GROWTH",
      "qualified_window": true,
      "event_type":       "operation_start",
      "as_of":            "<the batch_event row's occurred_at>"
    },

    "environment": {
      "device_id":  "particle-counter-01",
      "location":   "USP Suite A - BR-201 sample port",
      "age_s":      7.4,
      "status":     "normal",
      "occurred_at": "...",
      "channels":   { "...": "..." }
    }
  }
}
```

**`ts` is the acquisition instant, not the assembly instant** — the event being described is the
measurement. `meta.ingest_ts` is when 07 built it, and the gap between the two is the whole
record's provenance, visible on stage. This is the same rule
[`04-lims-webhook.md`](04-lims-webhook.md) states for the review message.

**Always publish.** If a lookup finds nothing, the block is `null` with a `reason` beside it —
never a missing key, never a silent default. `00-master-plan.md` states this for the MET ONE
section and it applies to both lookups here.

---

## Build order

1. **`sample_chain` script module**, gateway-scoped, `DATASOURCE = "ICC26"`. Jython 2.7 — no
   f-strings, no type hints. Copy the header conventions from
   [`metone_poll`](../../ignition/projects/icc-2026/ignition/script-python/metone_poll/code.py),
   including its datasource warning comment.
   - `build(document)` → dict. Takes the review message, returns the composite.
   - Two private lookups, one per query above. Each returns `(block, reason)`.
   - **No process knowledge.** Do not test `operation == "GROWTH"` — read
     `qualified_window`. `bes_batch`'s `QUALIFIED` tuple is the only copy of that rule and
     [`00-architecture.md`](../00-architecture.md) § *Derived flags travel with the fact that
     produced them* says why.
2. **The event stream**, per the config above.
3. **Verify on the wire**, not in the database. Subscribe as `observer` on
   `icc26/site1/qc/sample-chain`, then approve a sample in the LIMS review screen.
4. **The talk track**, `docs/talk-tracks/07-sample-chain.md`. The repo's two-document convention
   makes this the closing step, not an optional extra.

### The stack is staged for this

`br-201` is parked in **GROWTH** on batch `B-20260830-02` — deliberately, because that is the
qualified window. A sample drawn now should come back `qualified_window: true`. Advancing the
reactor past HARVEST will change that; put it back in GROWTH before demoing.

---

## Checkpoints

| CP | Check | State |
|---|---|---|
| **1** | An MQTT source type exists in the Event Stream source dropdown | pending |
| **2** | Approving a sample lands one message on `icc26/site1/qc/sample-chain` | pending |
| **3** | `batch_context.operation` is `GROWTH` and `qualified_window` is `true` for a sample drawn now | pending |
| **4** | Advance past HARVEST, draw again: operation reads `IDLE`, `qualified_window` false, and **nothing is empty** | pending |
| **5** | `environment.age_s` is present and under 30 s with the counter sampling | pending |
| **6** | Stop the MET ONE sim, draw again: the block is `null` with a reason, and the message still publishes | pending |
| **7** | A **rejected** sample produces a composite with `disposition: "fail"` | pending |
| **8** | `docs/talk-tracks/07-sample-chain.md` exists | pending |

CP4 and CP6 are the two that matter most on stage: **a gap is a finding, and 07 still speaks.**

---

## What this deliberately does not do

- **No new tables, no ACL change, no new datasource.** If a build step seems to need one,
  something has been misread.
- **No arithmetic.** Both flags were computed at ingest by the pattern that produced them.
- **It does not touch patterns 1–6.** They are finished and verified. The prerequisite changes
  they needed were made and committed on 2026-08-30 before this file was written.
- **It does not fix `poll_interval_s`.** Pattern 6 open item 9 records that the tag is decorative
  and both honest repairs are bigger than the demo needs.

## Still open, and not 07's problem

- **The Chariot licence is the largest stage risk in the stack.** The trial is **two hours**, the
  broker refuses to start when it lapses, and the container reports `healthy` throughout. It has
  expired mid-session twice on 2026-08-30 alone. A Cirrus Link demo key is a prerequisite for the
  conference, not an improvement.
- **Talk tracks for 03, 05 and 06 do not exist, and 04's is stale** — it still describes a queue
  the analyzer fills, which is not what the code has done since 2026-08-26.
- **`S-EQTEST-001`** is synthetic test data in `lims.sample`, verified, outbox row 22. Delete it.
- **Three `nonUseCount` tag-provider files** reappear as a diff on every gateway restart. Worth
  gitignoring.
