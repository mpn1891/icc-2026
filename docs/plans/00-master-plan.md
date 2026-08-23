# Pattern build specs

> What is left to build. Infra (Part 1) is done and the stale-image blocker is cleared — see
> `[00-status.md](00-status.md)`. Each spec below gets written up as its own file
> (`docs/plans/01…08-*.md`) and handed to an agent or teammate one at a time.
>
> [`../00-architecture.md`](../00-architecture.md) is settled truth. It is *usually* not changed
> from here — but it was on **2026-08-19** (patterns 5/6/7 got their own sources) and again on
> **2026-08-23** (pattern 4 became a NovaFlex HTTPS POST; 5 and 6 share a turbidity-meter
> database; pattern 7 is TBD). Read that file's *Shared sources* section before touching 04–07.
> Where a per-pattern file disagrees with its summary here, **the per-pattern file is newer.**

Seven data-transaction/event patterns on Ignition 8.3.8 + Cirrus 5.0.4 + Chariot + Postgres 17,
config-as-code via bind-mounted `ignition/config` + `ignition/projects`. Two or three teammates
clone, run, and collaborate via push/pull. Conference is ~4 weeks out.

All seven `meta.mechanism` values still have exactly one user each. Patterns 3 and 4 carry the
same NovaFlex result onto the same topic (two vendor surfaces). Patterns 5 and 6 carry the same
turbidity reading onto the same topic (CDC vs poll of one local database). That is deliberate;
see architecture § *Shared sources*.

Locked in: GitHub public repo under Matt's account; mixed Windows + macOS/Linux team; FastAPI stub for LIMS; demo-grade committed credentials are acceptable.

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
- **Verification harness**: `docker run --rm -it --network icc26 eclipse-mosquitto:2 mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v` (pull the image once, before
the conference; observer ACL already covers `icc26/#`, `spBv1.0/#`, `$SYS/#`).
- Commit hygiene: commit the files you meant to change and `git restore` the timestamp churn;
never commit gitignored identity paths. See *Working rules* in `[00-status.md](00-status.md)`.



## The eight specs

**01 and 02 are one device in two firmwares, and they must be read together.**
**Written up in** `[01-native-mqtt.md](01-native-mqtt.md)` **and**
`[02-sparkplug-b.md](02-sparkplug-b.md)`**, which supersede this entry entirely.** Both moved a
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

**03 — OPC UA → MQTT** — no dependencies. **Both servers built, and written up in**
`[03-opcua-analyzer-playbook.md](03-opcua-analyzer-playbook.md)`**, which supersedes this entry.**

Two analyzers, and they **run together rather than as alternatives** — that decision is
resolved. Both have a server, an Ignition connection and a bound UDT. Nova MQTT publish is
built (vendor `SampleTime` → Event Stream → Transmission). Countess still needs its publish.


|                   | `services/opcua-countess`                | `services/opcua-novaflex`                                                                                                                                      |
| ----------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Instrument        | Thermo Fisher Countess 3 FL cell counter | Nova Biomedical BioProfile FLEX2                                                                                                                               |
| Vendor OPC server | **none** — the instrument writes CSV     | **yes**, licensed, and we transcribed its real tag list                                                                                                        |
| Address space     | ours, DI + LADS shaped                   | **Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands`                                                                                                      |
| Completion signal | counter + events, designed in            | **none vendor-side** — Ignition publishes off `HistoricalSampleResults/SampleTime`; `ICC26Extensions` remains in the address space but is not the MQTT trigger |
| Actions           | a method *and* a command bit             | **command bits only, no methods**                                                                                                                              |
| Ignition UDT      | `cell_analyzer`, 44 bound tags           | `bioanalyzer`, 57 bound tags                                                                                                                                   |


The second earns its stage time by being what vendors actually ship: the Countess is the model
we would design, the FLEX2 is the one we would have to integrate. It also settles the argument
in §6.1 of the Countess model doc — a 2024 vendor product with 104 writable bits and zero
methods, because a SCADA tag write cannot invoke a method.

Nova is done: `result/sample_time` → Event Stream `03_opcua/novaflex-result` →
`icc26/site1/qc/analyzers/novaflex-01/result`, `mechanism=opcua-event`. Remaining: the same
shape for Countess on `count_completed_counter`. Verify Nova: one message per completed
sample, never per-value, nothing on abort/fail/QC. Talk point: event-on-completion, keyed
off the field the vendor actually ships.

**04 — NovaFlex HTTPS webhook** — **written up in
[`04-novaflex-webhook.md`](04-novaflex-webhook.md), which supersedes this entry.** Rewritten
2026-08-23. The LIMS approval webhook is the previous design (built 2026-08-20); it is not the
talk. Do not "fix" this back toward a LIMS.

The BioProfile FLEX2 — the same instrument as pattern 3 — can output a completed sample only
by HTTPS POST. Ignition receives that POST on an Event Stream HTTP source and publishes to
`icc26/site1/qc/analyzers/novaflex-01/result` with `mechanism=webhook`. Same topic as pattern
3; `correlation_id` is `sample_id` on both. The analyzer does not subscribe to MQTT.

The LIMS container stays in compose until the rebuild unwires it. Pattern 4 no longer depends
on pattern 3's MQTT path; they are parallel vendor surfaces.

**05 — CDC of a turbidity meter's local database** — **written up in
[`05-cdc-turbidity.md`](05-cdc-turbidity.md), which supersedes this entry.** The meter only
stores readings in database `turbidity`. Debezium Server tails that catalog (preferred: MQTT
sink onto Chariot) and publishes `mechanism=cdc` to
`icc26/site1/downstream/tff-301/turbidity-01/telemetry`. Vendor API TBD; placeholder schema
in the spec. Odoo is not the source. `04-cdc.sql`'s publication on `lims` / `mes` retires
with the build.

**06 — Poll / watermark of the same turbidity database** — **written up in
[`06-poll-turbidity.md`](06-poll-turbidity.md), which supersedes this entry.** Ignition JDBC
`WHERE id > :watermark`, `mechanism=poll`, **same topic as pattern 5**. The MET ONE particle
counter is not the source. The 2026-08-19 turbidity rejection was about the *signal* (deadband
overlaps Sparkplug RBE); the poll problem we need is the *store*. Failure demo: stall the
timer, show the gap. CDC, beside it, did not drop the rows.

**07 — TBD.** Designated cut if the schedule bites. The vibration + AMS + DCS aggregation is
**not** the plan. `cmd` / `response`, MQTT 5 response-topic, and `mechanism=aggregate` stay
unused until a spec claims them. `downstream`'s first user is now the turbidity meter
(patterns 5 and 6), so that slot is no longer why 07 has to exist.

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
"webhook off → CDC on"; it is per-pattern failure demonstrations instead (a webhook POST that
fails while Ignition is down, a poll watermark that stalls while CDC still catches the rows).
Pattern 7 has no failure demo until it has a spec.

**Build the fallback firehose in the next working session.** It is the only deliverable whose
absence is visible from the audience, it is the one piece with no dependencies on any pattern, and
scheduling it last means it competes with whatever is running late.

Two notes for patterns 1 and 2 specifically. Their Perspective page is mostly **a link to the
two device config pages on 8085 and 8086** — the comparison is between those two screens, and
rebuilding them inside Perspective would only make them less convincing. And **pattern 2 will
not appear on a firehose that colours by** `meta.mechanism`: Sparkplug payloads carry no
envelope, by design. Either colour it by topic prefix as a special case, or say out loud that
the one pattern which needed no agreement is the one that does not fit the field invented to
track agreements.

## Execution order

**Rewritten 2026-08-23.** Pattern 4 no longer consumes pattern 3's MQTT topic. 05 and 06 share
a database and should be built together (sim + schema first, then Debezium, then the poll).
07 is TBD.

- **Wave 0** — done. 01, 02, 03 built (Nova path broker-verified).
- **Wave 1**, independent:
  - **08's fallback firehose.** No dependencies, and the only thing the audience sees.
  - **04 rebuild**, per [`04-novaflex-webhook.md`](04-novaflex-webhook.md) (HTTPS POST + Event
    Stream; unwire the LIMS in the same pass).
  - **05/06 simulator + `turbidity` database.** Shared foundation; start it early.
- **Wave 2**: Debezium (05), Ignition JDBC poll (06), 08's primary firehose.
- **Wave 3**: runbook, dual-trial rehearsal, the offline run. Pattern 7 only if a spec exists.
- Each wave ends with: `tasks.py health` green, pattern verification passes, `git status` shows
  only intended files, push.

**Nuke:** pattern 5's new database and the retired `04-cdc.sql` publication need an empty
volume. Batch them. Pattern 4's LIMS schema can die in the same nuke.

## Verification (whole effort)

1. Per pattern: the spec's copy-pasteable check (trigger + `mosquitto_sub` + expected envelope).
2. **Per pattern, the failure too.** With no convergence set-piece to carry the argument, each
   mechanism has to demonstrate its own weakness: a webhook POST that is lost when Ignition is
   down, a poll watermark that stalls while CDC still catches the rows. Pattern 7 has no failure
   until it has a spec. A mechanism shown only working is a mechanism nobody can judge.
3. End-to-end: every built mechanism firing → firehose shows that many colours (seven only if 07
   lands); run once with networking disabled to prove offline viability.
4. **A subscriber cannot tell how anything arrived.** Read the topic list to somebody who has not
  seen the build and ask them which patterns use CDC. That, not the old switch-over, is now how
   the namespace claim gets tested.

