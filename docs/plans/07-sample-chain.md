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

> ~~**Still worth one glance.** This was read out of class names, not out of a running registry.
> The first time somebody opens Event Streams → New, confirm an MQTT source type is in the
> dropdown.~~ **Settled 2026-08-30, and the dropdown was never opened.** The stream was written
> to disk with `type: com.cirruslink.mqtt.engine.gateway.mqtt.source`, applied with `scan`, and
> the gateway logged its own subscription. Route 1 is not needed. § *As built*.

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
  lets the reader judge. Where the block is genuinely empty that is a finding in its own right —
  since 2026-09-06 a null `environment` is the `environment_unverifiable` violation, not a
  silently omitted key. (Silence now means *compliant*; it never means *unknown*.)
- **Therefore the age field is load-bearing.** Put it at the top level of the reading block, not
  buried in it. A forty-minute-old reading must not be able to read as current.

Pattern 6's timer bounds the normal case to **≤ 27.2 s** (pattern 6 CP7), and the live table
shows readings landing every 10 s.

> **Corrected 2026-08-31.** This paragraph used to end *"a large age only happens when nobody
> pressed Start — the pre-show failure `tasks.py health` already names."* Both halves were wrong.
> A **stale cursor** produces a large age too, with the instrument sampling happily, and that is
> what actually happened: the CP6 verification restarted `icc26-sim-metone` on 2026-08-30 and left
> the cursor past the end, so `em.reading` froze for 15.5 hours while every check stayed green.
> `health` did not name it — it read the simulator's buffer and never asked whether anything was
> landing. **It does now**, on `max(ingested_at)`, and it prints the cursor tag to clear.

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
            topic       icc26/site1/qc/deviation
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

**Publish only a deviation.** *(Revised 2026-09-06. Until then this decision read "Always
publish" and 07 emitted one composite per review.)* 07 now publishes only when the sample
violated something and names what in `values.violations` — a list that is never empty on a
message that exists. Silence is the compliant case.

**The gate lives in the filter, not the transform**, and that was measured rather than chosen:
a transform returning `None` publishes the literal string `"None"` onto the topic, because
`transformEncoder` is `ignition.string`. The filter is the only stage that can stop a message.
So `filter.userCode` is `return sample_chain.is_deviation(event.data)` and the transform is
unchanged. Both run the same two lookups — four queries per review at demo volume, which is
cheaper than a cache shared between two stages that could race.

The gate reads two flags and computes nothing, which is what keeps this compatible with
decision 1: `em.reading.status` came from `metone_poll` against `config/excursion_threshold`,
and `disposition` from the analyst in the LIMS. `qualified_window` is deliberately **not** a
trigger — `QUALIFIED` is `("GROWTH",)` of five operations, so gating on it would make the
deviation the normal case; it stays in the document as context.

**A lookup that finds nothing still produces a `null` block with a `reason` beside it** — never
a missing key, never a silent default. Only the decision to publish changed, not the shape. A
null `environment` is itself a violation (`environment_unverifiable`): a sample whose room
cannot be evidenced is not one anybody can release.

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
   `icc26/site1/qc/deviation`, then approve a sample in the LIMS review screen: a clean one
   publishes **nothing**. Press **Dirty** on the MET ONE panel (<http://localhost:8089>), wait
   for a fresh reading, draw and approve again — that one publishes.
4. **The talk track**, `docs/talk-tracks/07-sample-chain.md`. The repo's two-document convention
   makes this the closing step, not an optional extra.

### The stack is staged for this

`br-201` is parked in **GROWTH** on batch `B-20260830-02` — deliberately, because that is the
qualified window. A sample drawn now should come back `qualified_window: true`. Advancing the
reactor past HARVEST will change that; put it back in GROWTH before demoing.

---

## As built — 2026-08-30

Built to this file in one pass, same evening it was written. Files:
[`sample_chain/code.py`](../../ignition/projects/icc-2026/ignition/script-python/sample_chain/code.py),
[`07_chain/lims-review/config.json`](../../ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/07_chain/lims-review/config.json),
and this file's sibling talk track. Applied with `python tasks.py scan` — no gateway restart, no
Designer session. **No table, no ACL, no datasource, and nothing in patterns 1–6 was touched.**

### The MQTT Engine source, as it actually configures

Route 0 held. The dropdown was still never opened — the gateway settled it out loud instead:

```
[c.c.m.e.g.e.EventStreamMqttSource] The '07_chain/lims-review' stream subscribed
    on topic: icc26/site1/qc/lims/sample-result with QoS: 1
```

Four facts worth keeping, all read off the 5.0.4 jars and then confirmed running:

| | |
|---|---|
| source `type` | `com.cirruslink.mqtt.engine.gateway.mqtt.source` (Sparkplug's is `…gateway.sparkplug.source`) |
| source `config` | exactly `topic` and `qos`. Defaults `EventStreams/#` and `0` |
| what the transform is handed | the MQTT payload as a **`byte[]`** — `EngineCallback` builds `EventPayload.builder(mqttMessage.getPayload())` with the topic as metadata |
| so `sourceEncoder` | `ignition.string` / UTF-8, unchanged from the template. `StringEncoder` takes `byte[]` and returns a String, so `build()` receives text and decodes it exactly as `metone_poll.build_document` does |

**The subscription does not disturb MQTT Engine's tag ingest.** Engine's custom namespace is
`icc26/site1/upstream/br-201/sample-valve-01/#` only, so the review topic was never in it; the
event stream's subscription is the only one on that topic.

> **Not measured: what happens to the subscription when the broker drops.**
> `EventStreamMqttSource.onStartup` subscribes each **connected** `CirrusClient` and there is no
> visible re-subscribe hook on it. Given the Chariot trial lapses every two hours, this is worth
> settling before the conference: bounce the broker, then approve a sample and see whether a
> composite still lands. If it does not, disabling and re-enabling the stream is the recovery.

### Five shapes decided at the keyboard

The spec fixed the document; these are the places it left a choice, recorded here so the next
reader does not have to re-derive them from the code.

1. **`seq` carries the review's outbox delivery id** (`23`, `24`, `25` on the three live runs) — not the
   literal `0` the document sketch shows, which was copied from the LIMS's own pre-INSERT
   placeholder. 07 mints nothing: it holds no table, and an in-memory counter restarts at 1 on
   every gateway restart and tells a subscriber nothing — the reason `bes_cdc` gives for using
   its row id. The review and its composite sharing one `seq` is the statement that this document
   *is* that review, answered. **One line to change if that reads wrong.**
2. **The reasons are named siblings**, `batch_context_reason` and `environment_reason`, always
   present and `null` on the success path. "A `null` block with a reason beside it, never a
   missing key" is easiest to consume if the *shape* never moves either.
3. **`age_s` is a distance, with `nearest_side` beside it** (`"before"` / `"after"`), rather than
   one signed number whose sign quietly changes what it means. Nearest-either-side makes the side
   real information; overloading the sign of the age field would hide it.
4. **`em.reading.environment` lands as `environment.conditions`** — flow, temperature, humidity,
   untouched. A key named after its own parent tells a reader nothing.
5. **`equipment_identifier` is a tag read**, `asset_data/equipment_identifier` off the bioreactor
   UDT, per decision 4 — not a join, and `null` rather than fatal if it is unreadable.

### The two probes, and what they left behind

CP4 and the null-with-reason path were closed with two **transient probe messages** published
straight onto `icc26/site1/qc/lims/sample-result` as `ign-transmission`, ids `S-CP4-PROBE` and
`S-NULLPATH-PROBE`. They exist because the reactor could not be advanced from a shell and because
no real sample was ever drawn in the IDLE window. **They wrote nothing.** 07 has no table, the
composites went out unretained, and neither id is in `lims.sample`, `bes.batch_event` or
`em.reading` — the only trace is two gateway log lines. Do not go looking for rows.

## Checkpoints

| CP | Check | State |
|---|---|---|
| **1** | An MQTT source type exists in the Event Stream source dropdown | **closed** 08-30 — better than the dropdown: the gateway logged `The '07_chain/lims-review' stream subscribed on topic: icc26/site1/qc/lims/sample-result with QoS: 1`. Type id `com.cirruslink.mqtt.engine.gateway.mqtt.source`, config keys `topic` + `qos` |
| **2** | Approving a sample lands one message on `icc26/site1/qc/sample-chain` | **closed** 08-30 — `S-20260830-0085`, one message, watched live as `observer` |
| **3** | `batch_context.operation` is `GROWTH` and `qualified_window` is `true` for a sample drawn now | **closed** 08-30 — same message, `B-20260830-02`, `as_of` 22:55:37.922Z |
| **4** | Advance past HARVEST, draw again: operation reads `IDLE`, `qualified_window` false, and **nothing is empty** | **closed** 08-30, **by a different route** — the reactor was *not* advanced, because the stack is parked in GROWTH on purpose. Probed instead with a sample instant inside the real IDLE window rows 34→35 already hold: `operation: "IDLE"`, `qualified_window: false`, `event_type: "batch_end"`, `batch_id: "B-20260830-01"`, no empty value anywhere. That instant also lands after rows 33 and 34, which **share** `22:50:19.034Z` — so this closes the `id DESC` tie-break too |
| **5** | `environment.age_s` is present and under 30 s with the counter sampling | **closed** 08-30 — **2.5 s** on both live approvals with the counter sampling (`S-20260830-0085`, `S-20260830-0084`), and 1.4 s on the CP4 probe |
| **6** | Stop the MET ONE sim, draw again: the block is `null` with a reason, and the message still publishes | **closed** 08-30, **and the wording was wrong** — see below |
| **7** | A **rejected** sample produces a composite with `disposition: "fail"` | **closed** 08-30 — `S-20260830-0084`, rejected, composite published with `disposition: "fail"` |
| **8** | `docs/talk-tracks/07-sample-chain.md` exists | **closed** 08-30 |

CP4 and CP6 are the two that matter most on stage: **a gap is a finding, and 07 still speaks.**

### CP6, corrected

**Decision 7 and CP6 disagree, and decision 7 wins.** *Nearest either side, no tolerance* means
07 always answers with the nearest reading it can find, so the `environment` block goes `null`
**only when `em.reading` holds no row at all for `particle-counter-01`** — which on any database
that has run for ten minutes it never does. Stopping the simulator does not produce a silence; it
produces a stale reading with a growing `age_s`, which is the finding, in a field. Measured:
`icc26-sim-metone` stopped at 23:39:11Z, `S-20260830-0087` drawn at 23:40:55Z and approved four
minutes later, composite published unchanged with **`age_s: 122.6`, `nearest_side: "before"`** —
against the 27.2 s pattern 6's timer normally guarantees.

**And note which two instants that age is between.** It is the sample against the reading, not
*now* against the reading, so it does not climb while the sample sits in review — a sample drawn
one minute after the counter died reads 60-odd seconds however long the analyst takes. That is
the right behaviour for a record about a sample, and it is worth saying out loud before somebody
watches the number fail to move and thinks it is stuck.

Both halves of what CP6 was *for* are closed anyway:

- **07 still speaks with a lookup missing.** Probed with a sample instant that predates the
  reactor's first batch event: `batch_context: null`,
  `batch_context_reason: "no bes.batch_event row for br-201 at or before 2026-08-20T12:00:00.000Z"`,
  every other key present, message published. The null-with-reason machinery is the same code on
  both blocks.
- **A stale reading cannot read as current.** The same probe's nearest particle count was
  **nine days** from its sample instant and said so: `age_s: 786110.5`, `nearest_side: "after"`.

The talk track carries this correction as a boxed note, because it is the beat somebody will try
on stage and be surprised by.

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

> **All of this moved to [`00-post-07.md`](00-post-07.md) on 2026-08-30**, which is the current
> step and adds the two items 07's build turned up — the review queue's twelve null-`equipment_id`
> reviewables, and the re-subscribe question below. Kept here so this file still reads whole.

- **The Chariot licence is the largest stage risk in the stack.** The trial is **two hours**, the
  broker refuses to start when it lapses, and the container reports `healthy` throughout. It has
  expired mid-session twice on 2026-08-30 alone. A Cirrus Link demo key is a prerequisite for the
  conference, not an improvement.
- **Talk tracks for 03, 05 and 06 do not exist, and 04's is stale** — it still describes a queue
  the analyzer fills, which is not what the code has done since 2026-08-26.
- **`S-EQTEST-001`** is synthetic test data in `lims.sample`, verified, outbox row 22. Delete it.
- **Three `nonUseCount` tag-provider files** reappear as a diff on every gateway restart. Worth
  gitignoring.
- **New, from the build: the MQTT source's subscription across a broker drop is unmeasured.**
  § *As built*. It matters because the Chariot trial lapses on its own, and it is one bounce and
  one approval to settle.

## Progress log

| Date | |
|---|---|
| 2026-08-30 | Spec written, from [`00-pre-07-cleanup.md`](00-pre-07-cleanup.md)'s four closed checkpoints and seven decisions |
| 2026-08-30 | **Built and broker-verified the same evening. All eight checkpoints closed.** `sample_chain` + Event Stream `07_chain/lims-review`, applied with `scan` — no restart, no Designer, and nothing outside the two new resources changed. Route 0 held and the gateway said so in its own log; the source's type id, config keys and `byte[]` payload are recorded in § *As built* along with five document shapes the spec left open. Two real approvals (`S-20260830-0085` pass, `S-20260830-0084` fail) and two transient probes closed the eight. **One correction to this file:** CP6's wording contradicts decision 7 — nearest-either-side means the environment block is `null` only on an empty `em.reading`, so a stopped MET ONE shows a growing `age_s`, not a silence. Both halves of what CP6 was for are closed anyway. One new open item: the source's re-subscribe behaviour across a broker drop |
| 2026-09-06 | **Reversed to publish only a deviation, and broker-verified the same afternoon.** Topic moved `icc26/site1/qc/sample-chain` → `icc26/site1/qc/deviation`; `values.violations` names what was wrong and is never empty on a message that exists. Triggers are `em.reading.status == "excursion"` and `disposition == "fail"`, both read as flags their owning modules already set — 07 still computes nothing. `qualified_window` is reported but deliberately not a trigger. **One Ignition finding cost the first implementation:** a transform returning `None` does **not** suppress a message, it publishes the four-byte string `None`, because `transformEncoder` is `ignition.string` — watched live on the topic. The filter is the only stage that can stop a message, so the gate moved to `filter.userCode` → `sample_chain.is_deviation(event.data)` and the fact went to [`../00-architecture.md`](../00-architecture.md). Verified on the real gesture, no fixtures: **Dirty** at :8089 → 163 counts → 3482/4303/4218, badge `B-1042` → `S-20260906-0006` → **approved**, and it deviated anyway on `environmental_excursion`, `age_s` 2.4 `after`, `GROWTH`, `qualified_window: true`, `disposition: pass` — a sample that passed every analytical spec and still failed the room. **Clean** → `S-20260906-0008` → silence, `... is clean; no deviation published` in the gateway log. A rejection published `failed_review` on its own. Applied with `scan` — no restart, no Designer |
