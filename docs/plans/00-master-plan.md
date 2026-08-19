# Pattern build specs

> What is left to build. Infra (Part 1) is done and the stale-image blocker is cleared — see
> [`00-status.md`](00-status.md). Each spec below gets written up as its own file
> (`docs/plans/01…08-*.md`) and handed to an agent or teammate one at a time.
>
> [`../00-architecture.md`](../00-architecture.md) is settled truth. It is *usually* not changed
> from here — but it was on **2026-08-19**, when patterns 5, 6 and 7 were given their own sources
> and the shared-topic set-piece was dropped. Read that section before touching 04, 05, 06 or 07.
> Where a per-pattern file disagrees with its summary here, **the per-pattern file is newer.**

Seven data-transaction/event patterns on Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17,
config-as-code via bind-mounted `ignition/config` + `ignition/projects`. Two or three teammates
clone, run, and collaborate via push/pull. Conference is ~4 weeks out.

All seven `meta.mechanism` values still have exactly one user each, which is the one thing the
re-sourcing did not change — and is now the *only* thing tying the seven patterns together, since
no two of them carry the same data any more.

Locked in: GitHub private repo under Matt's account; mixed Windows + macOS/Linux team; FastAPI
stub for LIMS; demo-grade committed credentials are acceptable.

**Each spec must carry:** objective + talk point, files to create (with sketches), Ignition
resources (exact paths where known, UI-then-commit where schemas are unknown), MQTT user +
topics, empirical checkpoints, copy-pasteable verification, and "update the `docs/0N-*.md`
talk-track doc" as the closing step.

## Cross-cutting conventions

These apply to every spec below and belong at the top of `00-conventions.md` when that file
is written (planned — not present yet).

- **Envelope + namespace**: reference `docs/00-architecture.md`; `meta.mechanism` ∈
  `native-mqtt | sparkplug | opcua-event | webhook | cdc | poll | aggregate`.
- **Ignition-originated publishes go through Transmission**:
  `system.cirruslink.transmission.publish("chariot_broker", topic, payload, qos, retain)`.
  Discovered constraint: the `ign-engine` ACL only allows publishing commands
  (`.../cmd/#`, NCMD/DCMD) — `ign-transmission` has `icc26/#` publish rights. Engine is for
  ingest + command-out; Transmission for event-out. This is itself a talk point.
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
  never commit gitignored identity paths. See *Working rules* in [`00-status.md`](00-status.md).

## The eight specs

**01 and 02 are one device in two firmwares, and they must be read together.**
**Written up in [`01-native-mqtt.md`](01-native-mqtt.md) and
[`02-sparkplug-b.md`](02-sparkplug-b.md), which supersede this entry entirely.** Both moved a
very long way from the sketches that were here — pattern 1 was a wireless vibration gateway
simulated inside Ignition, pattern 2 was a bioreactor UDT on the Programmable Device Simulator.
Each file carries the reasoning and a deviations table; do not "fix" either back toward an older
summary.

A **smart sample valve assembly**: a sanitary sample valve with an RFID badge reader on a
bioreactor's sample port. Badge in, the valve strokes open for a sampling window, the valve
closes; every scan is published, granted or denied. `services/sim-valve-mqtt` on `BR-201`
speaks plain MQTT; `services/sim-valve-spb` on `BR-202` is the *same device* speaking Sparkplug
B v3.0.0. `valve.py` and `webui.py` are byte-for-byte identical between the two build contexts,
so everything that differs between the containers is a difference the protocol caused.

Both are **publish-only** — no command topic, nothing on the backbone can open either valve,
authorization is decided locally against a badge roster. Each container serves its own **device
commissioning webpage** (8085, 8086), and the difference between those two pages is as much of
the talk as the traffic: a free-text topic, a QoS dropdown and a retained checkbox on one; the
same three controls disabled, with the TCK clause that fixed each, on the other. Talk point:
spec-mandated versus hand-rolled — the topic, the datatypes, the units, the loss detection and
the death certificate, one protocol giving you all five and the other giving you three form
fields and a wiki page.

**03 — OPC UA → MQTT** — no dependencies. **Both servers built, and written up in
[`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md), which supersedes this entry.**

Two analyzers, and they **run together rather than as alternatives** — that decision is
resolved. Both have a server, an Ignition connection and a bound UDT. Nova MQTT publish is
built (vendor `SampleTime` → Event Stream → Transmission). Countess still needs its publish.

| | `services/opcua-countess` | `services/opcua-novaflex` |
|---|---|---|
| Instrument | Thermo Fisher Countess 3 FL cell counter | Nova Biomedical BioProfile FLEX2 |
| Vendor OPC server | **none** — the instrument writes CSV | **yes**, licensed, and we transcribed its real tag list |
| Address space | ours, DI + LADS shaped | **Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands` |
| Completion signal | counter + events, designed in | **none vendor-side** — Ignition publishes off `HistoricalSampleResults/SampleTime`; `ICC26Extensions` remains in the address space but is not the MQTT trigger |
| Actions | a method *and* a command bit | **command bits only, no methods** |
| Ignition UDT | `cell_analyzer`, 44 bound tags | `bioanalyzer`, 57 bound tags |

The second earns its stage time by being what vendors actually ship: the Countess is the model
we would design, the FLEX2 is the one we would have to integrate. It also settles the argument
in §6.1 of the Countess model doc — a 2024 vendor product with 104 writable bits and zero
methods, because a SCADA tag write cannot invoke a method.

Nova is done: `result/sample_time` → Event Stream `03_opcua/novaflex-result` →
`icc26/site1/qc/analyzers/novaflex-01/result`, `mechanism=opcua-event`. Remaining: the same
shape for Countess on `count_completed_counter`. Verify Nova: one message per completed
sample, never per-value, nothing on abort/fail/QC. Talk point: event-on-completion, keyed
off the field the vendor actually ships.

**04 — LIMS approval webhook (`services/lims` + WebDev)** — **blocks nothing any more.**
It blocked 05/06/07 for as long as those three read from the LIMS. Since the convergence reversal
on 2026-08-19 they have their own sources, so all four can be built in parallel. This is the
single biggest schedule win available right now and it was a side effect, not the goal.
**Written up in [`04-lims-webhook.md`](04-lims-webhook.md), which supersedes this entry
entirely.** The shape moved a long way from the sketch that was here on 2026-08-19 and the file
carries the reasoning; do not "fix" it back toward an older summary.

Three changes worth knowing from this level. The LIMS no longer **generates** sample results — it
**subscribes** to pattern 3's analyzer topic, which makes 04/05/06/07 all downstream of a publish
path that has never been watched on a broker, and makes verifying pattern 3 the critical path for
four patterns. The webhook fires on **manual analyst approval**, not on receipt, which is what
gives the callback an honest reason to be asynchronous. And the LIMS gets **no MQTT publish rights
at all** — its only output is the HTTP callback into Ignition, which is what stops the first
component that both consumes and causes publishes from being a feedback loop.

One `nuke` pays for both config changes it needs (`02-schema.sql` and `mqtt-users.json`), and it
must happen before 05/06/07 are built against the old shape. Port 8000, and the approval screen
is served by the LIMS itself rather than Perspective, like the two valve config pages.

**05 — CDC on Odoo (`compose/debezium/` + an `odoo` service)** — decided 2026-08-19, no spec yet.
Debezium tails **Odoo's** Postgres, not ours. `quay.io/debezium/server:3.x`, pgoutput, user `cdc`,
**HTTP sink** → a WebDev endpoint (`cdc-sink`) mapping change events to the envelope,
`mechanism=cdc`, manufacturing-order state changes →
`icc26/site1/upstream/br-201/batch/event`. Named volume for offsets. Verify: change an MO state in
Odoo's own UI on stage → topic message within ~1 s, with Odoo never having been configured to
tell anyone.

Why Odoo rather than our own table: **CDC's real use case is an application you do not own and
cannot make emit events.** `../00-architecture.md` already claims the `cdc` role is separate
because CDC is an out-of-band observer the application knows nothing about; against Odoo that
stops being staged and becomes literally true. It also vindicates the "no `mes` area" decision —
an Odoo manufacturing order publishes under the cell that produced it and names Odoo in the
payload, which is exactly the rule that section argues for.

Four findings to start the spec from, so they are not rediscovered:

- **Odoo's Quality app is Enterprise-only** (confirmed against Odoo's own edition comparison,
  along with Studio, PLM, Barcode and IoT). Tail Community MRP and Inventory — `mrp_production`,
  `stock_move`, `stock_move_line`, lot records. Do not design the demo around quality checks.
- **Logical replication publications are per-database, and Debezium Server runs one source
  connector per process.** So Odoo in its own database means either a second Debezium container or
  no CDC on `icc26` at all. The second is now fine: `mes.batch_event` has no consumer left, so
  `04-cdc.sql`'s publication should probably be retired with this spec.
- **Odoo adds a third manual per-volume init step** beside Ignition commissioning and Chariot's
  trial, and `nuke` wipes it. Script it into `tasks.py`
  (`odoo -d odoo -i base,stock,mrp --stop-after-init`) rather than adding a fourth thing a human
  does before walking on stage. Pre-pull the image; check RAM on the presenting laptop.
- **Odoo writes constantly to `bus_bus`, `ir_cron` and `mail_message`.** Tail broadly and you
  drown. Knowing which of ~800 ORM tables matter is the actual work of CDC against a real
  application, and it is the best argument for `04-cdc.sql` naming tables explicitly instead of
  letting a connector choose.

**06 — Poll / diff of a MET ONE particle counter** — decided 2026-08-19, no spec yet.
A simulated Hach MET ONE airborne particle counter (3400 or 6000 series) speaking **Modbus TCP**,
in a cleanroom beside `BR-201`. New service (`pymodbus`), plus an Ignition Modbus device
connection — a connection type this repo has never created, so **UI first, then commit** per the
conventions above. Gateway timer script drains the instrument's record buffer, watermarks on
record index, publishes `mechanism=poll` to
`icc26/site1/upstream/br-201/particle-counter-01/telemetry`.

This is the strongest of the three sources decided that day, and the reason is in the vendor
manual rather than in our design:

- **The instrument has no push capability at all.** That is the honest justification for polling,
  and it is a much better one than polling an HTTP API that could have called you back.
- **There is a real buffered record block.** Appendix A.6 of the MET ONE 6000 manual: "the
  buffered record block gives a remote application the ability to access data that is stored in
  the instrument… continuously updated with new sample data," with a separate buffered-sample
  status register. So reading records by index is the vendor's own interface, not our invention —
  the watermark argument in hardware.
- **The buffer has two failure modes, and which one you get is a checkbox.** 50–5000 samples, and
  a *Rotate Buffer* option. Unchecked (the **default**): "once the data buffer is completely
  filled, no new data is loaded" — the instrument silently stops recording. Checked: "the oldest
  data in the buffer is overwritten with the latest." **Both lose data**, and a checkbox on the
  instrument's own config screen decides how. This is pattern 1's retained-flag argument on a real
  vendor product, and it belongs on the simulator's config page for exactly that reason.
- Also: "a change to the buffer size causes all current buffer data to be lost and
  unrecoverable," and the manual says the register tables "may become updated — contact Hach
  Company for updated tables." An unversioned register map you obtain by email is a good aside.

Chosen over a turbidity meter deliberately. Turbidity gives a continuous value and a deadband, and
deadband-on-poll overlaps with report-by-exception, which pattern 2's Sparkplug already does
better. The particle counter's indexed buffer is a polling problem nothing else in the demo has.

Verify: stall the poll loop long enough for the buffer to wrap, then show the gap — the failure,
not just the mechanism.

**07 — Scripted aggregation: a vibration reading and the evidence it was valid** — leaning,
2026-08-19. No spec, and **this is the designated cut** if the schedule bites.

Three sources into one composite document → `mechanism=aggregate` on
`icc26/site1/downstream/tff-301/asset-summary`: a **vibration waveform** (resurrects
`services/sim-vibration/`, currently on disk and wired to nothing), a **webhook request from an
asset management system** asking for the reading, and a **steady-state signal from a DCS**.

**Frame it as validity, not as three sources joined.** ISO 20816 acceptance criteria assume rated
steady operating conditions, so a waveform captured during a ramp is not a poor measurement — it
is a meaningless one. The composite document's value is that it carries the measurement *and the
evidence the measurement was admissible*: the operating state that made it valid and the work
order that asked for it. That is the strongest justification for aggregation available, and it is
much better than needing a third source to fill a slot. It also answers the "third source
undecided" question that stood here since pattern 2 stopped being a bioreactor.

It puts three unused things in `../00-architecture.md` to work at once: the `waveform` message
type, the `cmd/<verb>` + `response/<what>` pair, and MQTT 3.1.1's missing response-topic
properties as a live constraint rather than a footnote. It also gives `downstream` its first user.

Two scope cuts to take **before** starting, not after:

- **The DCS is a small OPC UA server.** It is the cheapest new source this repo can add, because
  the server skeleton, connection-by-directory-copy and UDT binding are all proven — see
  [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md).
- **The AMS is a copy of the LIMS FastAPI skeleton**, same shape with a different noun, not a
  novel service. Build it after 04 and it is an afternoon.

Note the AMS webhook is **inbound trigger**, and the published mechanism is `aggregate`, not
`webhook`. Two patterns sharing a transport while carrying different mechanisms is worth saying
out loud: the transport is not the mechanism.

Verify: one message containing all three sections, and a **refusal** — request a reading while the
DCS says the unit is not at steady state, and show that nothing is published.

**08 — Presentation + runbook** — **start the fallback firehose now, not last.**
Perspective: overview page, one page per pattern, and the **firehose** colored by
`meta.mechanism`. Firehose: primary = WebDev-served static HTML with **vendored** mqtt.js over
Chariot WS :8090 as `observer`, embedded in Perspective (offline-safe; riskiest UI piece);
fallback = Engine-subscribed wildcard → gateway script appends to a dataset tag →
Perspective table. `docs/demo-runbook.md` (planned — write with this spec): T-15 dual-trial
checklist, per-pattern trigger + recovery.

Two changes from the convergence reversal. There is **no shared "LIMS result" page with mechanism
toggles** — that was the set-piece control panel, and the set-piece is gone; each of 04/05/06 now
gets its own page because each has its own source. And the runbook's choreography is no longer
"webhook off → CDC on"; it is per-pattern failure demonstrations instead (a webhook whose retries
exhaust, a particle-counter buffer that wraps, an aggregation that refuses because the unit is not
at steady state).

**Build the fallback firehose in the next working session.** It is the only deliverable whose
absence is visible from the audience, it is the one piece with no dependencies on any pattern, and
scheduling it last means it competes with whatever is running late.

Two notes for patterns 1 and 2 specifically. Their Perspective page is mostly **a link to the
two device config pages on 8085 and 8086** — the comparison is between those two screens, and
rebuilding them inside Perspective would only make them less convincing. And **pattern 2 will
not appear on a firehose that colours by `meta.mechanism`**: Sparkplug payloads carry no
envelope, by design. Either colour it by topic prefix as a special case, or say out loud that
the one pattern which needed no agreement is the one that does not fit the field invented to
track agreements.

## Execution order

**Rewritten 2026-08-19.** The old order had everything funnelling through 04 because 05, 06 and 07
all read the LIMS. They no longer do, so the dependency graph is almost flat and the constraint is
people, not ordering.

- **Wave 0** — done. 01, 02, 03 built (03's publish still unwatched, below).
- **Wave 1**, and these four are genuinely independent:
  - **Verify pattern 3 on the broker.** Small, and pattern 4 consumes its topic.
  - **08's fallback firehose.** No dependencies, and the only thing the audience sees.
  - **04**, per [`04-lims-webhook.md`](04-lims-webhook.md).
  - **06's Modbus simulator.** The longest new build; start it earliest.
- **Wave 2**: 05 (Odoo + Debezium), 07 (vibration + AMS + DCS), 08's primary firehose.
- **Wave 3**: runbook, dual-trial rehearsal, the offline run.
- Each wave ends with: `tasks.py health` green, pattern verification passes, `git status` shows
  only intended files, push.

**The one sequencing rule left:** the `nuke` that pattern 4 needs also takes Odoo's database with
it. Land 04's schema change before Odoo is initialized, or plan on re-initializing Odoo.

## Verification (whole effort)

1. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
2. **Per pattern, the failure too.** With no convergence set-piece to carry the argument, each
   mechanism has to demonstrate its own weakness: a webhook whose delivery is lost without an
   outbox, a record buffer that wraps between polls, an aggregation that refuses because the unit
   is not at steady state. A mechanism shown only working is a mechanism nobody can judge.
3. End-to-end: all seven mechanisms firing → firehose shows seven colors; run once with networking
   disabled to prove offline viability.
4. **A subscriber cannot tell how anything arrived.** Read the topic list to somebody who has not
   seen the build and ask them which patterns use CDC. That, not the old switch-over, is now how
   the namespace claim gets tested.
