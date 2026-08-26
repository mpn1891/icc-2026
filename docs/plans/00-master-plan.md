# Pattern build specs

> Each spec below gets written up as its own file
> (`docs/plans/01…07-*.md`) and handed to an agent or teammate one at a time.
>
> [`../00-architecture.md`](../00-architecture.md) is settled truth. It is *usually* not changed
> from here — but it was on **2026-08-19** (shared-topic set-piece dropped), again on
> **2026-08-23** (patterns 5, 6 and 7 re-sourced, pattern 4 gained a pass/fail disposition), and
> again alongside this revision, when patterns 5–7 were given the explicit qualified-window /
> excursion logic the through line needs. Read those sections before touching 04, 05, 06 or 07.
> Where a per-pattern file disagrees with its summary here, **the per-pattern file is newer.**

Seven data-transaction/event patterns on Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17,
config-as-code via bind-mounted `ignition/config` + `ignition/projects`. Two or three teammates
clone, run, and collaborate via push/pull. Conference is ~4 weeks out.

All seven `meta.mechanism` values still have exactly one user each. Patterns 1–6 each carry a
different signal; pattern 7 is the join of several of them into one composite document. The
namespace still must not leak the mechanism.

Locked in: GitHub private repo under Matt's account; mixed Windows + macOS/Linux team; FastAPI
stub for LIMS; demo-grade committed credentials are acceptable.

**Each pattern gets two documents**, as patterns 1, 2 and 4 now do:

- `docs/plans/0N-*.md` — the **build spec**: the physical model, files to create (with
  sketches), Ignition resources (exact paths where known, UI-then-commit where schemas are
  unknown), MQTT user + topics, payload contracts, empirical checkpoints, copy-pasteable
  verification, open items, progress log. It states what the thing *is* and how it was measured;
  why it earns a slot in the talk belongs to the talk track.
- `docs/talk-tracks/0N-*.md` — the **talk track**, written as the closing step: talk points, the
  segment's intro → demo → risk → close beats, **the signal it contributes to the spine**, **the
  GxP hook**, the wire shot, and the on-stage trigger list.

Neither should re-argue the other. Settled truth goes to
[`../00-architecture.md`](../00-architecture.md) rather than being copied into both.

## Demo through line

**"Becoming Event-Driven in Operational Technology with Ignition."** All seven patterns draw
from **a single fed-batch bioprocess run.** Each pattern contributes one signal from that batch;
the audience watches events get assembled across seven demos, not seven unrelated vignettes.
Full narrative in [`../demo-through-line.md`](../demo-through-line.md) (merged from the source
doc — see that file for segment structure, presenter framing and the closing beat). Pattern
numbering below is fixed and referenced from there; **do not renumber.**

**The GxP through line:** contemporaneous record of a sampling and analysis event, and whether that sample
was pulled inside the qualified phase window.

**The composite event (pattern 7 payoff):**
> Sample pulled outside qualified phase window with concurrent environmental excursion.

Each pattern segment runs **intro (business context) → demo → risk/complexity in GxP → a
positive closing message** — the upbeat beat after the risk speaker is deliberate, so no segment
(and not the talk overall) ends on risk. The patterns will likely be introduced 1-4 as summaries, then a demo for all three and then swapping to 5-7 and doing the same thing.  The **GxP hook** line in each spec below is that segment's risk beat.

## Cross-cutting conventions

These apply to every spec below and belong at the top of `00-conventions.md` when that file
is written (planned — not present yet).

- **Envelope + namespace**: reference `docs/00-architecture.md`; `meta.mechanism` ∈
  `native-mqtt | sparkplug | opcua-event | webhook | cdc | poll | aggregate`.
- **Ignition-originated publishes go through Transmission**:
  `system.cirruslink.transmission.publish("chariot_broker", topic, payload, qos, retain)` is the manual option. spB transmitters can publish entire
  tag structures, or event streams can publish to a specific topic..
  Discovered constraint: the `ign-engine` ACL only allows publishing commands
  (`.../cmd/#`, NCMD/DCMD) — `ign-transmission` has `icc26/#` publish rights. Engine is for
  ingest + command-out; Transmission/eventstream for event-out.
- **Authoring Ignition config**: known file formats (tags, project resources: scripts, WebDev,
  Perspective views) → edit files + `python tasks.py scan`; unknown gateway-scoped schemas
  (device connections, OPC UA connections, Engine namespaces) → create via UI once, read the
  files `git status` reveals, commit, then parametrize.
- **Python service skeleton**: paho-mqtt 2.x, env-config (`BROKER_HOST=chariot`, creds), retained
  LWT where specified, reconnect-with-backoff, envelope helper (~20 lines, duplicated per
  service — no shared lib, keeps build contexts self-contained).
- **Compose service pattern**: `build: ./services/<name>`, `container_name: icc26-<name>`,
  `restart: unless-stopped`, broker reconnection handled in-app (no depends_on chariot).
- **Verification harness**: `docker run --rm -it --network icc26 eclipse-mosquitto:2
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v` (pull the image once, before
  the conference; observer ACL already covers `icc26/#`, `spBv1.0/#`, `$SYS/#`).
- Commit hygiene: commit the files you meant to change and `git restore` the timestamp churn;
  never commit gitignored identity paths. See *Working rules* in
  [`../00-architecture.md`](../00-architecture.md).

## The seven specs

### 01 and 02 Smart Sample Valve
**01 and 02 are one device in two firmwares, and they must be read together.**
Talk tracks —
[`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md) and
[`../talk-tracks/02-sparkplug-b.md`](../talk-tracks/02-sparkplug-b.md) — carry the talk points,
the pattern 1 vs 2 comparison table, the segment beats and the GxP risk material. Build specs —
[`01-native-mqtt.md`](01-native-mqtt.md) and [`02-sparkplug-b.md`](02-sparkplug-b.md) — carry
the contracts, the measured Ignition-side findings and verification. **All four supersede this
entry entirely.**

A **smart sample valve assembly**: a sanitary sample valve with an RFID badge reader on a
bioreactor's sample port. Badge in, the valve strokes open for a sampling window, the valve
closes; every badge scan is published, granted or denied. `services/sim-valve-mqtt` on `BR-201`
speaks plain MQTT; `services/sim-valve-spb` on `BR-202` is the *same device* speaking Sparkplug
B v3.0.0. `valve.py` and `webui.py` are byte-for-byte identical between the two build contexts,
so everything that differs between the containers is a difference the protocol caused.

Both are **publish-only** — no command topic, nothing on the backbone can open either valve,
authorization is decided locally against a badge roster. Each container serves its own **device
commissioning webpage** (8085, 8086), and the difference between those two pages is as much of
the talk as the traffic: a free-text topic, a QoS dropdown and a retained checkbox on one; the
same three controls disabled, with the TCK clause that fixed each, on the other. Talk point:
spec-mandated versus hand-rolled.

**Signal contributed to the spine:** sample actuation event (pattern 1) — pattern 7 reads
pattern 1's `sample-valve-01` event for *when material left the reactor*. Pattern 2's device
liveness / session state is a **stage argument, not a section of pattern 7's document**: this
valve is `sample-valve-02` on `BR-202`, and nothing in the aggregate reads it.

**GxP hooks:** Pattern 1 — the record originates at the point of action; no transcription, no
intermediary. Pattern 2 — you can prove the valve was alive when it said nothing or at least know if you missed messages between becauase of the spec enforced seq numbering

**03 — OPC UA → MQTT** — no dependencies. **Built, and written up in
[`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md), which supersedes this entry.**

The demo instrument is `services/opcua-novaflex`: a Nova Biomedical BioProfile FLEX2. The
vendor ships a licensed OPC UA server; we transcribed its real tag list. Address space is
**Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands`. Completion signal **none
vendor-side** — Ignition publishes off `HistoricalSampleResults/SampleTime`;
`ICC26Extensions` remains in the address space but is not the MQTT trigger. Actions are
**command bits only, no methods**. Ignition UDT `bioanalyzer`, 57 bound tags.

This is what vendors actually ship, and it is the one we would have to integrate: a 2024
vendor product with 104 writable bits and zero methods, because a SCADA tag write cannot
invoke a method.

Done: `result/sample_time` → Event Stream `03_opcua/novaflex-result` →
`icc26/site1/qc/analyzers/novaflex-01/result`, `mechanism=opcua-event`. Verify: one message
per completed sample, never per-value, nothing on abort/fail/QC. Talk point:
event-on-completion, keyed off the field the vendor actually ships.

Countess (`services/opcua-countess`) is **out of the demo** — do not finish its MQTT
publish, and do not treat it as remaining Pattern 3 work.

**Signal contributed to the spine:** viable cell density / viability reading — pattern 7 reads
Nova's result for *when the sample was actually run* (`meta.correlation_id` = `sample_id`).

**GxP hook:** a qualified instrument, read by a platform, one-way — the change-control boundary
is explicit: the instrument is untouched, the gateway does the work.

**04 — LIMS approval webhook (`services/lims` + WebDev)** — **built 2026-08-20.**
**Written up in [`04-lims-webhook.md`](04-lims-webhook.md), which supersedes this entry
except for the remaining delta below.** Do not "fix" the built shape back toward an older
summary: subscribe to pattern 3, hold for a human, webhook into Ignition, no LIMS MQTT
publish rights, transactional outbox. Port 8000, approval screen served by the LIMS itself.

**Remaining (2026-08-23):** both review outcomes publish. Approve and reject each write an
outbox row and land on `icc26/site1/qc/lims/sample-result` with `analyst` and
`values.disposition` ∈ `pass | fail`. Reject is no longer silent — pattern 7 listens for the
review, not only for a pass. The rest of the contract is unchanged.

**Signal contributed to the spine:** assay result, already carrying business meaning — this is
the analyst review that triggers pattern 7's join.

**GxP hook:** an authenticated inbound connection, and the result — an analyst's disposition on
a sample already drawn — arrives out of sequence with the physical event it describes. Contrast
the payload against the tag-change data pattern 3 emits; the semantic gap is visible in one
screenshot.

**05 — CDC on our own batch table, standing in for a BES** — decided 2026-08-23, refined here to
carry the qualified-window flag pattern 7 needs.

A **very simple batch engine inside Ignition**: a gateway timer, started and stopped from
enable/disable tags, auto-cycles `BR-201` through `CIP → SIP → INOC → GROWTH → HARVEST`. The
timer writes the bioreactor UDT (add an operation/phase tag to the existing `bioreactor` type)
and inserts `bes.batch_event` in the same step. The sequencer publishes **nothing** onto MQTT.

**Qualified sampling window (new):** the batch protocol qualifies sampling for **`GROWTH`
only** — a real bioprocess constraint: pulling material during `CIP`/`SIP` doesnt make sense, and `INOC`/`HARVEST` are outside the characterized production phase. `bes.batch_event` already carries `phase`; add `payload.qualified_window := (phase = 'GROWTH')` at insert time so
the flag travels with the row Debezium tails, rather than being recomputed downstream.

Debezium tails **our** Postgres — `quay.io/debezium/server:3.x`, pgoutput, user `cdc`,
publication on `bes.batch_event` only (drop `lims.sample_result` from `04-cdc.sql`).
HTTP sink → WebDev (`cdc-sink`) → envelope `mechanism=cdc`, `values.phase`,
`values.qualified_window` → `icc26/site1/upstream/br-201/batch/event`. Named volume for offsets.

**GxP hook:** reading a validated system's internals without its owner in the loop. State it
plainly and hand it to the risk speaker — the honest framing is that this timer stands in for a
Batch Execution System we would not patch to emit events: the writer does not know MQTT exists,
the `cdc` role is not `icc26`, and disabling Debezium leaves the reactor cycling with a silent
topic. That is the failure demo, not a fake ERP UI.

The JDBC datasource `ICC26` is now on the critical path (the timer writes the table). It is
not in the repo yet; **UI first, then commit.**

Verify: flip the enable tag → phase tags advance on a dwell → a row appears in
`bes.batch_event` with `qualified_window` set correctly → a message on `batch/event` within
~1 s. Stop Debezium, leave the timer running, show the gap.

**06 — Poll of a MET ONE environmental analyzer** — decided 2026-08-23, refined here to carry
the excursion flag pattern 7 needs.

A simulated Hach MET ONE particle counter inside the analyzer tag path, speaking an **HTTP API** (not
Modbus). New service `services/sim-metone`. The exact routes and record shape are **TBD** —
vendor API docs will be dropped in; until then treat it as "a pull API that returns completed
particle-count analysis events with a timestamp and channel counts," watermarked on record id
or analysis time.

**Excursion status (new):** the simulator (or the Ignition poll script, at ingest) compares the
returned channel counts against a configured cleanroom-grade limit and sets
`values.status ∈ normal | excursion`. This is what through-line calls "environmental excursion
status" — it must exist on the wire, not just be inferable from raw counts, so pattern 7 can
key off it directly.

An Ignition gateway timer polls that API. On a new analysis, the script submits the payload to
an Event Stream (`06_poll/metone-result`), which publishes through Transmission to
`icc26/site1/qc/analyzers/particle-counter-01/result` with `mechanism=poll`. Same relay
shape as pattern 3; the acquisition is the poll.

**GxP hook:** a characterized detection gap. Not fatal on its own, but it goes in the
assessment — show the polling interval, then ask what could have happened between polls.

Verify: one HTTP analysis → one Event Stream fire → one MQTT message, `status` correct against
the configured limit. Stall the poll and show a missed (or late) analysis.

**07 — Scripted aggregation: the composite GxP event** — decided 2026-08-23, reframed here
around the through line's payoff. **This is the designated cut** if the schedule bites: it is
the join of 01, 03, 04, 05 and 06, and it cannot start until those four sources exist.

A gateway script **listens for the pattern-4 LIMS review** on MQTT (`qc/lims/sample-result`,
pass or fail). On that message it builds one composite document, `mechanism=aggregate`, on
`icc26/site1/upstream/br-201/sample-chain/event`:

| Section | Source | What it answers |
|---|---|---|
| Sample actuation | pattern 1 event (`sample-valve-01`) | when material left the reactor |
| VCD reading + assay result | pattern 3 Nova result + pattern 4 review | when the sample was run, and its disposition |
| Batch phase and qualified window | pattern 5 `batch/event` at sample time | `phase` and `qualified_window` when the valve opened |
| Environmental excursion status | pattern 6 MET ONE, nearest reading to the Nova timestamp | `status` at (or nearest) the sample instant |

The script derives two flags from those sections — `values.outside_qualified_window` (pattern
5's `qualified_window` was `false` at the sample-open instant) and
`values.environmental_excursion` (pattern 6's nearest reading was `status = excursion`). **Always
publish**, whatever the flags say: a dirty or missing MET ONE reading is a finding in the
document, not a refusal. When both flags are `true`, the payload *is* the through line's
composite event.

This will require a history/database for the other events to log to in addition to just the batch ones

> Sample pulled outside qualified phase window with concurrent environmental excursion.

— the moment no single source system could have produced on its own. The correlating piece is
the `sample_id` already stamped in 3 and 4, so one sample shows up under four `meta.mechanism`
values in one `mosquitto_sub` (opcua-event, webhook, and whatever 5/6 contributed) plus the
aggregate.

**Framing:** no single source knows this. Four systems each hold a fragment that means nothing
alone. Patterns 1–6 are about acquisition; pattern 7 is about meaning.

**GxP hook:** derived data with disposition consequence — the heaviest item on the list, and the
right one to end on.

Verify: badge the valve during `GROWTH` with a clean MET ONE reading → aggregate publishes with
both flags `false`. Then badge it during `CIP`/`HARVEST`, or with a forced MET ONE excursion (or
both) → aggregate publishes with the relevant flag(s) `true`, reproducing the composite event
above on demand for rehearsal.

## Order

**Rewritten 2026-08-25**, when the status doc was gutted and this became the only place the work
is sequenced. Patterns 1–3 are built, 4 is built minus its disposition. Pattern 7 is the join,
so it is last of the seven, and it is still **the designated cut** if the schedule bites.

1. **04 pass/fail.** Small, and pattern 7 listens for that message. Confirmed unbuilt:
   `services/lims/app.py` `reject()` writes no outbox row, and the payload builder has no
   `disposition` key at all.
2. **The sample id correlation.** ~~Ignition writes the valve's id into the Nova.~~ **Done
   2026-08-26, and not this way.** The analyzer got its own sample-login screen on :8087 and **a
   person types the valve's id in.** No Ignition tag write, no `meta.correlation_id` on pattern 1
   — the id still travels as `values.sample_id`. The transcription is fallible on purpose and is
   now pattern 1's sharpest risk beat. The LIMS opens its sample entry from
   `event/sample-complete` and appends the analyzer result to it, so the released review message
   carries the sample-open instant and **07's event store is no longer blocked on patterns 1 and
   3** — only on 5 and 6. See
   [`../00-architecture.md` § *The sample id, and pattern 1 mints it*](../00-architecture.md)
   and [`04-lims-webhook.md` § *Revised 2026-08-26*](04-lims-webhook.md).
3. **05 and 06 in parallel.** 05 is the timer + the `ICC26` JDBC datasource + Debezium; create
   the datasource first and read the `pg_db` look-alike trap in
   [`../00-architecture.md` § *Postgres*](../00-architecture.md) before you do. The `bioreactor`
   UDT also needs its phase tag. 06 is the MET ONE simulator + poll script + Event Stream; it
   waits on vendor API notes, so stub the routes if they have not arrived — the excursion flag
   and its limit config are ours, not the vendor's, and need not wait on somebody else's PDF.
4. **Decide pattern 7's event store.** New on 2026-08-25, **narrowed 2026-08-26 to patterns 5
   and 6 only.** Both of 07's flags are evaluated against the *past*, so their inputs have to be
   persisted somewhere 07 can query. Patterns 1 and 3 no longer need that: the LIMS stores the
   valve event as a `lims.sample` row and republishes the sample-open instant on the review
   message 07 already listens for. Two candidates for the remaining two: Ignition tag history on
   the bound tags, or an `events` table in `icc26` written by the same Event Streams that
   publish. Still blocks writing 07's spec, not just building it.
5. **07** — the composite aggregate, last of the seven.

Each item ends with: `tasks.py health` green, the pattern's verification passing, `git status`
showing only the files you meant to touch, push.

## Closing beat

After the final risk segment (pattern 7's):

> That composite event took days to surface in a paper-and-review world. The room just watched
> it surface in seconds, from systems nobody had to requalify.

## Verification (whole effort)

1. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
2. **Per pattern, the failure too.** A webhook whose delivery is lost without an outbox; a
   batch engine that keeps cycling while Debezium is down; a poll that stalls and misses an
   analysis; an aggregate that still publishes when the nearest MET ONE is missing.
   A mechanism shown only working is a mechanism nobody can judge.
3. **The composite event itself.** Force `outside_qualified_window` and `environmental_excursion`
   both `true` in one rehearsal run and confirm the aggregate payload reads exactly as the
   through line states it. This is the one verification step the audience's payoff depends on.
4. End-to-end: all seven mechanisms firing → one `mosquitto_sub -t 'icc26/#'` shows seven
   distinct `meta.mechanism` values; one sample's
   valve → Nova → LIMS review → aggregate on the same `correlation_id`. Run once with
   networking disabled to prove offline viability.
5. **A subscriber cannot tell how anything arrived.** Read the topic list to somebody who has not
   seen the build and ask them which patterns use CDC. That, not the old switch-over, is now how
   the namespace claim gets tested.
