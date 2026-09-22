# Data flows

One diagram per pattern: what produces the data, every hop it crosses, and the topic it lands
on. Nothing here is new — it is [`00-architecture.md`](00-architecture.md) and the seven
[`plans/`](plans/) specs drawn instead of written, for the moment on stage when somebody asks
"wait, how does that one get there?"

If a box or a label here disagrees with [`00-architecture.md`](00-architecture.md), that file
wins and this one is stale.

**Read the arrows as ownership boundaries, not just as transport.** The interesting line in
every diagram below is the one where the data stops being the source system's problem — that is
the line each pattern exists to demonstrate, and it is marked in each.

---

## All seven at once

```
  sim-valve-mqtt ─1─┐                                                   ┌── MQTT Engine
  sim-valve-spb  ─2─┤                                                   │   custom + Sparkplug
                    ├──────▶  ┌───────────────────────┐  ─────────────▶ │   namespaces (1, 2)
  Ignition       ─3─┤         │   Chariot MQTT Server │                 └── MQTT Engine
  (Transmission) ─4─┤         │   :1883  :8090(ws)    │                     MQTT source (7)
                 ─5─┤         └───────────┬───────────┘
                 ─6─┘                     │
                    ▲                     ├─────────────▶  lims :8000   (subscribes 1 and 3)
                    │                     └─────────────▶  7's Event Stream (subscribes 4)
                    │
              7 publishes back ◀── the only pattern that both subscribes and publishes
```

Patterns 1 and 2 speak to the broker directly, from their own containers. Patterns 3, 4, 5 and 6
all reach it through **Ignition → MQTT Transmission**, differing only in how the data got into
Ignition. Pattern 7 is not a new inbound transport at all: it subscribes to pattern 4 and
publishes one document.

**No mechanism is visible on the wire at all.** `meta.mechanism` is set by nothing: every
pattern publishes `ts` and `values`, and provenance is the topic it arrived on. Patterns 1, 2
and 3 were always that way; 4, 6 and 7 joined them on 2026-09-13 and pattern 5 followed later
the same day, which retired the field. See
[`00-architecture.md` § *Payload envelope*](00-architecture.md).

---

## Pattern 1 — native MQTT

```
   ┌──────────────────────────────────────────────────────────┐
   │  sim-valve-mqtt        config page :8085                 │
   │  SV-2000-0417 on BR-201's sample port                    │
   │  topic / QoS / retained are FREE-TEXT FIELDS             │
   └──────────────────────────┬───────────────────────────────┘
                              │  badge scan → valve stroke → close
                              │  paho, publish-only, LWT armed at CONNECT
                              ▼
   ═════════ nothing on the backbone can open this valve ═════════
                              │
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Chariot :1883      as user sample-valve-01              │
   │  ACL grants publish on icc26/site1/upstream/#            │
   │  — the AREA, not the exact topic. Conformance is the     │
   │    ACL's job here, because the topic box is free-text.   │
   └──────────────────────────┬───────────────────────────────┘
                              │
   icc26/site1/upstream/br-201/sample-valve-01/
       event/vlv-badge-scanned      every badge, granted or denied
       event/sample-acq-completed   only when a sample actually ran
       status                       online/offline, RETAINED — the will is the offline half
       telemetry                    air supply / enclosure temp, every 5 s
                              │
                   ┌──────────┴─────────────────────────────┐
                   ▼                                        ▼
   ┌───────────────────────────────┐      ┌───────────────────────────────────┐
   │ MQTT Engine                   │      │ lims :8000  (pattern 4)           │
   │ custom namespace              │      │ event/sample-acq-completed OPENS  │
   │ `icc26-native`                │      │ the lims.sample entry             │
   │ subscription                  │      └───────────────────────────────────┘
   │   icc26/site1/upstream/#      │
   │                               │
   │ jsonPayload, qos1,            │
   │ writeableTags FALSE           │
   │                               │
   │ Tags auto-created from        │
   │ whatever JSON arrives:        │
   │   {event/vlv-badge-scanned,   │
   │    event/sample-acq-completed │
   │    status, telemetry}         │
   │      /ts  /values/…           │
   │                               │
   │ No UDT. No Event Stream.      │
   │ No transform. No files.       │
   └───────────────────────────────┘
```

The two event **subtypes** are two topics because Engine's custom namespace mirrors whatever
document arrives — one topic carrying two shapes gives you one `values` folder holding the union
of both, half of it stale from the other shape. `icc26/+/+/+/+/event/#` still catches both,
because `#` matches zero levels.

---

## Pattern 2 — Sparkplug B

The same device, the same `valve.py`, byte for byte. Everything that differs below is a
difference the protocol caused.

```
   ┌──────────────────────────────────────────────────────────┐
   │  sim-valve-spb         config page :8086                 │
   │  SV-202 on BR-202                                        │
   │  topic / QoS / retained are DISABLED, with the spec      │
   │  clause that fixed them printed beside each one          │
   └──────────────────────────┬───────────────────────────────┘
                              │
        NBIRTH  on connect          — node online, bdSeq
        DBIRTH  on connect          — the metric list, with units and types
        DDATA   on change           — report by exception, seq increments
        NDEATH  registered as the WILL, so the broker sends it for you
                              │
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Chariot :1883      as user sample-valve-02              │
   └──────────────────────────┬───────────────────────────────┘
                              │
   spBv1.0/ICC26-Site1-UPSTREAM/{NBIRTH|NDEATH}/SAMPLE-VALVE-02
   spBv1.0/ICC26-Site1-UPSTREAM/{DBIRTH|DDATA|DDEATH}/SAMPLE-VALVE-02/SV-202
                              │
   ═════ the topic is not the device's to choose — the spec fixed it ═════
                              │
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  MQTT Engine  default namespace `Sparkplug B`            │
   │  subscription spBv1.0/#     defaultTagsEnabled: true     │
   │                                                          │
   │  MQTT Engine/Edge Nodes/ICC26-Site1-UPSTREAM/            │
   │      SAMPLE-VALVE-02/                                    │
   │          Node Control/Rebirth    Node Info/ (16)         │
   │          SV-202/Valve/…  Badge/…  Sample/…  Line/…       │
   │          SV-202/Device Info/     (9)                     │
   │                                                          │
   │  ZERO FILES CHANGED. Engineering units (%, bar, degC, s) │
   │  arrived because Engine parsed the DBIRTH property sets. │
   └──────────────────────────────────────────────────────────┘
```

Adding a metric is a device-side edit and the tag tree follows on its own. That is the whole
argument, and it is why pattern 1's diagram has an ACL box in it and this one does not.

---

## Pattern 3 — OPC UA → Event Stream → MQTT

```
   ┌──────────────────────────────────────────────────────────┐
   │  opcua-cell-analyzer                                     │
   │    :4841  OPC UA server, the vendor's address space      │
   │    :8087  the instrument's own sample-login screen       │
   │                                                          │
   │  A PERSON reads S-YYYYMMDD-NNNN off BR-201's page and    │
   │  types it in. SAMPLE_INTERVAL_S = 0 — nothing free-runs. │
   │                                                          │
   │  On run: writes HistoricalSampleResults/*, and writes    │
   │  SampleTime LAST so nothing downstream fires early.      │
   └──────────────────────────┬───────────────────────────────┘
                              │  OPC UA subscription
   ═══ the instrument is qualified and untouched. The gateway does the work. ═══
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Ignition — OPC connection → `cell_analyzer` UDT         │
   │             instance cell-analyzer-01                    │
   │                                                          │
   │  tag change on …/result/sample_time                      │
   │    (skips initialChange and Bad quality)                 │
   │      │                                                   │
   │      ▼ system.eventstream.publishEvent(resultFolder)     │
   │  Event Stream  03_opcua/cell-analyzer-result             │
   │    transform  opcua_event.build_cell_analyzer_result     │
   │      reads the historical UDT siblings, Bad → JSON null  │
   │      returns ts + values. No seq, no source, no meta.    │
   │      │                                                   │
   │      ▼ handler: MQTT Transmission (ign-transmission)     │
   └──────────────────────────┬───────────────────────────────┘
                              ▼
   icc26/site1/qc/analyzers/cell-analyzer-01/sample-analyzed
                              │
                              └─────────▶ lims :8000 appends the analytes (pattern 4)
```

---

## Pattern 4 — webhook

The only pattern with a human in the middle of it, and the only one where the answer is not
ready when the question is asked.

```
   pattern 1  …/sample-valve-01/event/sample-acq-completed ────────┐
   pattern 3  icc26/site1/qc/analyzers/+/sample-analyzed ──────────┤ subscribe QoS 1, as lims-bridge
                                                                   ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  lims :8000   FastAPI + paho on its own thread                       │
   │                                                                      │
   │   valve event  ──▶ INSERT lims.sample     the entry OPENS here,      │
   │                                           at collection, not result  │
   │   analyzer result ─▶ INSERT lims.sample_result rows, matched on the  │
   │                      TRANSCRIBED id. Mismatch → parked, reattachable │
   │                      with the wrong id preserved.                    │
   │                                                                      │
   │   ┌──── the review screen ────┐                                      │
   │   │  a human clicks           │   ONE TRANSACTION:                   │
   │   │  Approve  or  Reject      │──▶ UPDATE status                     │
   │   └───────────────────────────┘    + INSERT lims.webhook_delivery    │
   │                                      (the transactional outbox)      │
   │                                              │                       │
   │   outbox drainer, retries, at-least-once ◀───┘                       │
   └──────────────────────────┬───────────────────────────────────────────┘
                              │  POST, shared secret + idempotency key
                              │  https://ignition:8043/system/webdev/
                              │      icc-2026/lims/sample-result
   ═══ hours after the physical event. Out of sequence, on purpose. ═══
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Ignition WebDev  lims/sample-result                     │
   │                     → lims_webhook.handle()              │
   │    authenticates and dedupes. Adds NOTHING to            │
   │    the document — no seq, no source, no meta             │
   │      ▼ Transmission                                      │
   └──────────────────────────┬───────────────────────────────┘
                              ▼
   icc26/site1/qc/lims/sample-results-released     analyst + disposition ∈ pass | fail
                              │
                              └─────────▶ pattern 7's Event Stream
```

`values.sample_id` is minted once at the valve and survives the whole chain unchanged, so one
sample is traceable from the analyzer result through the LIMS review to the deviation, side by
side in one `mosquitto_sub`. It used to be copied into `meta.correlation_id` as well; that copy
came out on 2026-09-13 along with the rest of the envelope.

---

## Pattern 5 — change data capture

```
   ┌──────────────────────────────────────────────────────────┐
   │  Tag Explorer: click br-201 batch_data/manual_advance    │
   │  (Boolean memory tag — a click, not a dwell, so the      │
   │   reactor can be parked in GROWTH before the badge)      │
   │      │ valueChanged, rising edge                         │
   │      ▼                                                   │
   │  bes_batch   CIP → SIP → INOC → GROWTH → HARVEST         │
   │    writes qualified_window AT INSERT TIME — true only on │
   │    the operation_start of GROWTH. Resets the tag on      │
   │    every exit path, refusals included.                   │
   │      │ JDBC, as user icc26                               │
   │      ▼                                                   │
   │  bes.batch_event   two rows per click:                   │
   │      operation_end + operation_start, same timestamp     │
   │      equipment_id holds 'br-201' — the TOPIC form        │
   │      payload {"qualified_window": bool}                  │
   └──────────────────────────┬───────────────────────────────┘
                              │
   ═══ THE WRITER STOPS HERE. It has no broker credentials, imports ═══
   ═══ nothing from Cirrus, and has no idea anybody is watching.    ═══
                              │
                              ▼  WAL — wal_level = logical
   ┌──────────────────────────────────────────────────────────┐
   │  Debezium Server :8083     as user cdc, NOT icc26        │
   │    pgoutput                                              │
   │    publication  icc26_cdc — bes.batch_event and NOTHING  │
   │                 ELSE. tasks.py health asserts it.        │
   │    slot         icc26_debezium                           │
   │    snapshot.mode = no_data                               │
   └──────────────────────────┬───────────────────────────────┘
                              │  POST https://ignition:8043/… + query-string token
                              │  (:8088 returns 302 and Debezium does not follow it)
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  WebDev cdc-sink → bes_cdc.handle()                      │
   │    builds the topic from equipment_id                    │
   │    publishes ts + values; nothing names the mechanism    │
   │      ▼ Transmission, retained false                      │
   └──────────────────────────┬───────────────────────────────┘
                              ▼
   icc26/site1/upstream/br-201/batch/event
                              │
                              └─────────▶ read back by pattern 7 — from the TABLE, not the topic
```

---

## Pattern 6 — poll and diff

```
   ┌──────────────────────────────────────────────────────────┐
   │  sim-particle-counter                                    │
   │    :8443  GraphQL over HTTPS, JWT bearer, cursor paging  │
   │    :8089  the operator's touchscreen — Start, location,  │
   │           and the Dirty button that forces an excursion  │
   │                                                          │
   │  10 s sample duration. Nothing free-runs; the operator   │
   │  starts it and Ignition never mutates the instrument.    │
   └──────────────────────────▲───────────────────────────────┘
                              │  getSamples(cursor, limit)
                              │  → samples AFTER the bookmark, plus a fresh cursor
   ═════ nobody is pushing. Ignition asks, on its own clock. ═════
                              │
   ┌──────────────────────────┴───────────────────────────────┐
   │  Ignition gateway timer  06-poll   30 s fixed delay      │
   │      ▼ particle_counter_poll.poll()                      │
   │    walks the cursor — the cursor IS the watermark        │
   │    thresholds RAW 0.5 µm counts (1660) → status          │
   │      ∈ normal | excursion, computed AT INGEST            │
   │      │ JDBC, as user icc26                               │
   │      ▼                                                   │
   │  em.reading    ← deliberately NOT in publication         │
   │                  icc26_cdc, or pattern 6 arrives by CDC  │
   │                  too and two mechanisms quietly become   │
   │                  one                                     │
   │      │ system.util.jsonEncode(record) → publishEvent     │
   │      ▼                                                   │
   │  Event Stream  06_poll/particle-counter-result           │
   │    its `batch` block is why the three analyses of one    │
   │    poll do not leave together                            │
   │      ▼ Transmission                                      │
   └──────────────────────────┬───────────────────────────────┘
                              ▼
   icc26/site1/env_monitoring/particle-counter-01/sample-analyzed
                              │
                              └─────────▶ read back by pattern 7 — from the TABLE, not the topic
```

The wire shows a burst of three every 30 s, and a 7.2 / 17.2 / 27.2 s lag sawtooth. The gap
between polls is the honest cost, and it goes in the assessment.

---

## Pattern 7 — the composite

Not a new inbound transport. It subscribes, joins, and speaks only when something was violated.

```
   icc26/site1/qc/lims/sample-results-released   (pattern 4's review)
                              │
                              ▼  MQTT Engine's own MQTT source, qos 1
                                 com.cirruslink.mqtt.engine.gateway.mqtt.source
                                 — a genuine backbone subscription. No WebDev hop,
                                   no subscriber service, nothing in 1–6 touched.
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Event Stream  07_chain/lims-review                                  │
   │                                                                      │
   │   filter    sample_chain.is_deviation(event.data)                    │
   │             ┌───────────────────────────────────────────────┐        │
   │             │  A CLEAN SAMPLE STOPS HERE. Silently.         │        │
   │             │  Silence on the topic is the compliant case.  │        │
   │             └───────────────────────────────────────────────┘        │
   │                                                                      │
   │   transform  sample_chain.build(event.data)                          │
   │                                                                      │
   │     valve half + analyte half ──── arrive ON the review message      │
   │                                    itself (patterns 1 and 3, via     │
   │                                    the LIMS, which persisted them)   │
   │                                                                      │
   │     lookup 1 ──▶ bes.batch_event        (pattern 5)                  │
   │                  WHERE equipment_id = ? AND occurred_at <= ?         │
   │                  ORDER BY occurred_at DESC, id DESC  LIMIT 1         │
   │                  ↑ the tie-break is not optional: one advance        │
   │                    writes two rows sharing a timestamp               │
   │                                                                      │
   │     lookup 2 ──▶ em.reading             (pattern 6)                  │
   │                  WHERE device_id = 'particle-counter-01'             │
   │                  ORDER BY abs(occurred_at - ?) ← nearest EITHER SIDE │
   │                  LIMIT 1                                             │
   │                                                                      │
   │   IT COMPUTES NOTHING. qualified_window and status were both         │
   │   decided at ingest by the pattern that produced them. 07 reads      │
   │   the flags their owning modules already set.                        │
   │                                                                      │
   │   A lookup that finds nothing still produces a null block with a     │
   │   `reason` beside it — never an empty key, never a silence.          │
   │      ▼ Transmission, retained false                                  │
   └──────────────────────────┬───────────────────────────────────────────┘
                              ▼
   icc26/site1/qc/deviation      ts + values — the topic says what it is
                                 values.violations names what was violated —
                                 an environmental excursion, or a failed review.
                                 Never empty on a message that exists.
```

`qualified_window` is reported but deliberately **not** a trigger: it is false for four
operations of five, and a flag that fires most of the time is not a finding.

> **Lookup 1 is drawn as it runs today, and it is scheduled to change.** The `batch_id` it
> returns names the batch at the reactor's last *advance*, not at the sample instant — the same
> thing only because nothing in this demo interleaves them. It moves to a tag-history query on
> `br-201/batch_data/batch_id` at the sample instant:
> [`plans/07-sample-chain.md`](plans/07-sample-chain.md) § *2a*, tracked as
> [`plans/00-post-07.md`](plans/00-post-07.md) § 7. `operation` and `qualified_window` may or may
> not follow it off this row — that is one of the decisions 2a leaves open.

---

## Where each pattern's data comes to rest

| # | Origin | Crosses into Ignition via | Publishes to | Also lands in |
|---|---|---|---|---|
| 1 | `sim-valve-mqtt` :8085 | Engine custom namespace `icc26-native` (`icc26/site1/upstream/#`) | `…/sample-valve-01/{event/*,status,telemetry}` | `lims.sample` |
| 2 | `sim-valve-spb` :8086 | Engine default namespace `Sparkplug B` | `spBv1.0/ICC26-Site1-UPSTREAM/…` | Edge Nodes tag tree |
| 3 | `opcua-cell-analyzer` :4841 | OPC connection → `cell_analyzer` UDT | `…/cell-analyzer-01/sample-analyzed` | `lims.sample_result` |
| 4 | a human on :8000 | WebDev `lims/sample-result` | `…/qc/lims/sample-results-released` | `lims.webhook_delivery` |
| 5 | `manual_advance` click | Debezium → WebDev `cdc-sink` | `…/br-201/batch/event` | `bes.batch_event` (first) |
| 6 | `sim-particle-counter` :8443 | gateway timer `06-poll` | `…/particle-counter-01/sample-analyzed` | `em.reading` (first) |
| 7 | pattern 4's topic | Engine MQTT source | `…/qc/deviation` | nothing — it stores no state |

Patterns 5 and 6 are the two that hit the database **before** the broker; everything else
publishes first. That ordering is what makes them available to pattern 7's lookups, and it is
also why `em.reading` must stay out of the `icc26_cdc` publication.

---

## The relay shape, said once

Patterns 3, 6 and 7 all end the same way:

```
   something triggers  ──▶  Event Stream  ──▶  transform (a script module)  ──▶  Transmission
```

Only the trigger differs — an instrument finishing a run, a timer firing, a message arriving.
Say that on stage **once**, in one of the three, rather than three times.
