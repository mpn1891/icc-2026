# Status

> **Updated 2026-08-30.** Conference is ~4 weeks out. **All seven patterns are built.** What is
> left is talk tracks and the Chariot licence.

What is built and what is not — nothing else. This file was 208 lines on 2026-08-25 and was
gutted the same day, because most of it was either duplicated or archaeology:

- **Durable facts and traps** → [`../00-architecture.md`](../00-architecture.md). The `pg_db`
  datasource look-alike, the Transmission `TARGET` warning, the API-key traps, the `br-202`
  mistake, and *Working rules* all live there now.
- **What to build, and in what order** → [`00-master-plan.md`](00-master-plan.md) § *Order*.
- **Part 1 history, the clone-test checkpoints, the proven/disproven list** → deleted. Git has
  them; the last revision carrying them is on this branch.

If this file disagrees with either of the other two, this file is newer, and the fix is to move
the fact rather than keep two copies.

## Built

| | State |
|---|---|
| **Infra** | Done. A gateway rebuilt from the repo loads Cirrus **5.0.4** Engine, Transmission and Distributor with no compatibility warnings. `tasks.py` forces `--build` on `up` and `seed`, so a stale image cannot come back quietly. |
| **01 — native MQTT** | Built, run and broker-verified 2026-08-17. Findings in [`01-native-mqtt.md`](01-native-mqtt.md) § *Ingest, as built*; talk track at [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md). |
| **02 — Sparkplug B** | Same. [`02-sparkplug-b.md`](02-sparkplug-b.md) § *Ingest, as built*, [`../talk-tracks/02-sparkplug-b.md`](../talk-tracks/02-sparkplug-b.md). |
| **03 — OPC UA → MQTT** | Nova path built and broker-verified 2026-08-20 (`S-00140` watched live). Device id is `novaflex-01`. The Countess is out of the demo. **2026-08-26: the instrument gained its own sample-login screen on :8087 and no longer free-runs** — `SAMPLE_INTERVAL_S` defaults to 0, and the sample id is typed in by a person. [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) § 8b. |
| **04 — LIMS webhook** | Verified end-to-end 2026-08-20: ingest, reject, atomic approve, webhook publish, 409 replay, 401 wrong secret, and outbox survival across `docker restart icc26-lims`. **Rebuilt 2026-08-26: the LIMS opens the sample entry from pattern 1's `event/sample-complete` and appends the analyzer result to it** — new `lims.sample` table, unmatched-result parking and reattach, valve provenance on the released document. **Broker-verified end to end 2026-08-26** (`S-20260826-0009` watched live): valve event opens the entry, the transcribed id brings the analyzer result onto it, approve publishes `values.collection` beside the analytes. Failure paths verified too — transposed id parks and reattaches with the wrong id preserved, a sagged air supply yields a `failed-to-seat` entry reviewable with no analytes, and a `docker restart icc26-lims` logs the retained redelivery as a no-op. **Remaining:** both review outcomes publish `analyst` + `disposition` pass/fail, unchanged. [`04-lims-webhook.md`](04-lims-webhook.md) § *Revised 2026-08-26*, [`../talk-tracks/04-lims-webhook.md`](../talk-tracks/04-lims-webhook.md). |
| **05 — CDC** | **Built and broker-verified 2026-08-26.** Click `manual_advance` in Tag Explorer → two rows in `bes.batch_event` → Debezium → `cdc-sink` → `icc26/site1/upstream/br-201/batch/event`, watched live with LSNs on the wire. `qualified_window` correct on both polarities. The batch engine is a **manual advance**, not the master plan's timer, and the ISA-88 element is an **operation**, not a phase. Three transport traps cost the build time — the Debezium config mount path, Ignition's HTTP→HTTPS redirect versus a Java client that does not follow redirects, and a stale container surviving `docker compose up -d`. **Treat it as an MVP** — it proves the mechanism but has not been shaped by a consumer, because 07 is not written. The `values` shape, the two-rows-per-click model and the empty `batch_end` are all open to change when 07 lands; nothing writes `deviation` yet. **Remaining:** the talk track. [`05-cdc-batch-event.md`](05-cdc-batch-event.md) § *This is an MVP* |
| **06 — poll** | **Built and broker-verified 2026-08-29.** `services/sim-metone` serves the vendor GraphQL API over HTTPS on 8443 with an operator touchscreen on 8089; Ignition's `metone_poll` walks the cursor, writes `em.reading`, and relays through Event Stream `06_poll/metone-result` to `icc26/site1/qc/analyzers/particle-counter-01/result`. 96 analyses to 96 rows to 96 messages, `status` correct on both polarities, and the stale-cursor trap reproduced and recovered with one tag. Three predictions in the spec were wrong and are corrected there: `publishEvent` needs a JSON string, value persistence is a provider setting not a per-tag one, and the store cannot dedupe on `sequence_number`. **The gateway timer landed the same evening** — `ignition/timer/06-poll/`, 30 s fixed delay, schema learned from an empty resource created in the Gateway UI. **All ten checkpoints are closed** and pattern 6 runs hands-off: a 7.2 / 17.2 / 27.2 s lag sawtooth, three analyses per poll, and the wire shows a burst of three every 30 s rather than one message every 10 s. **Remaining:** the talk track. [`06-poll-metone.md`](06-poll-metone.md) |
| **07 — the composite** | **Built and broker-verified 2026-08-30, the day its spec was written.** One script module `sample_chain` and one Event Stream `07_chain/lims-review` whose source is MQTT Engine's own MQTT source on `icc26/site1/qc/lims/sample-result` — **no new table, no ACL change, no new datasource, and nothing in patterns 1-6 touched**. On a review it lands one composite on `icc26/site1/qc/sample-chain`, joining what pattern 5 wrote to `bes.batch_event` and pattern 6 wrote to `em.reading` onto the sample the LIMS just released. **It computes nothing** — `qualified_window` and `status` were both decided at ingest by the pattern that produced them. **All eight checkpoints closed** on two real approvals (`S-20260830-0085` pass, `S-20260830-0084` fail) and two transient probes: `GROWTH` + `qualified_window: true` for a sample drawn now, `IDLE` + `false` + nothing empty for one drawn between batches, `age_s` 2.5 s live, and a null lookup published with its reason beside it. Applied with `scan` — no restart, no Designer. **Talk track written.** [`07-sample-chain.md`](07-sample-chain.md) § *As built*, [`../talk-tracks/07-sample-chain.md`](../talk-tracks/07-sample-chain.md) |

## Not built

~~**07**~~ — **built and broker-verified 2026-08-30**, see above. Its talk track exists, so the
remaining lump is 03, 05 and 06's, plus 04's rewrite.

**Talk tracks for 03, 05 and 06** — written as the closing step of each pattern, per the master
plan's two-document convention. **04's is stale**: it still describes a queue the analyzer fills,
which is not what the code has done since 2026-08-26. This is now the largest remaining piece of
work in the repo.

~~**Pattern 7's event store**~~ — **closed 2026-08-29**, and with it master-plan Order item 4.
The LIMS persists the valve event and republishes the sample-open instant, so patterns 1 and 3
need no store; `bes.batch_event` is pattern 5's; and `em.reading` is now pattern 6's, built to
the same lookup shape deliberately —
`WHERE device_id = ? AND occurred_at <= ? ORDER BY occurred_at DESC, id DESC LIMIT 1`.
**07 as built uses that idiom on `bes.batch_event` only.** Decision 7 chose *nearest either
side* for the MET ONE, so `em.reading` is read with
`ORDER BY abs(extract(epoch FROM (occurred_at - ?)))` instead and `ix_em_reading_lookup` goes
unused on that path — deliberately, and at demo volumes irrelevant.
[`07-sample-chain.md`](07-sample-chain.md) § *The two lookups*. **`em.reading` is not in the
`icc26_cdc` publication and must never be**, or pattern 6 arrives by CDC as well and two
mechanisms quietly become one; `tasks.py health` asserts it.

## Changed on 2026-08-30

**The pre-07 cleanup closed, 07's spec was written, and 07 was built** — all in one day, and the
last of the seven. The three prerequisites landed first (`batch_end` carries `IDLE`, `batch_id`
is minted rather than typed, `lims.sample` has an `equipment_id` the review message carries), then
[`07-sample-chain.md`](07-sample-chain.md) recorded seven decisions, then the build closed all
eight of its checkpoints against the running stack.

**Route 0 held: MQTT Engine ships an Event Stream MQTT source**, so 07 is a genuine backbone
subscriber with no WebDev hop and no subscriber service —
`com.cirruslink.mqtt.engine.gateway.mqtt.source`, config keys `topic` and `qos`, payload handed
over as a `byte[]` that `ignition.string` decodes. Pattern 4's claim that it and 07 are the only
two subscribers there are survives.

**Two corrections came out of the build.** CP6's wording contradicted decision 7 —
*nearest either side* means the environment block is `null` only on an empty `em.reading`, so a
stopped MET ONE shows a growing `age_s` rather than a silence — and the document's `seq` carries
the review's outbox delivery id rather than the literal `0` the sketch showed. Both are recorded
in [`07-sample-chain.md`](07-sample-chain.md) § *As built*.

**One new open item:** whether the MQTT source re-subscribes after the broker drops is
unmeasured, and the Chariot trial drops it every two hours.

## Changed on 2026-08-29

**Pattern 6 built** (master plan Order item 4). A new `sim-metone` service, the `em` schema, the
`particle_counter` UDT, `metone_poll`, and Event Stream `06_poll/metone-result` —
broker-verified the same day. The excursion threshold got its number, **1660 raw counts at
0.5 um**, which is 352,000 per cubic metre at the 4.717 L a 10 s sample draws — ISO 14644-1
Class 7. It applies to that one channel, because a single raw count cannot also threshold 5.0 um
two orders of magnitude below it.

**Four Ignition 8.3.8 facts were measured** and are recorded in
[`../00-architecture.md`](../00-architecture.md) rather than in pattern 6's spec, because 07
will want all four: the `httpClient` certificate-bypass keyword, `publishEvent` refusing a dict,
WebDev python resources not seeing their own module-level names, and value persistence being a
tag-*provider* setting.

## Changed on 2026-08-26

**Pattern 5 written** (master plan Order item 3, first half). The batch engine is a **manual
advance**, not the auto-cycling timer the master plan specified — three memory tags on the
`bioreactor` UDT and a click in Tag Explorer. `phase` became `operation` end to end (ISA-88: those
five are operations; a phase is the smallest process action). `bes.batch_event` gained
`equipment_id` so a click on `br-202` cannot publish onto `br-201`'s topic, and
`lims.sample_result` finally left the `icc26_cdc` publication. Reasoning and every checkpoint:
[`05-cdc-batch-event.md`](05-cdc-batch-event.md).

**The sample id correlation landed** (master plan Order item 2), by transcription rather than by
an Ignition tag write. Three services changed: the LIMS opens its entry from pattern 1, the Nova
gained a sample-login page and stopped free-running, and `lims-bridge` gained one subscribe
topic. Reasoning: [`04-lims-webhook.md` § *Revised 2026-08-26*](04-lims-webhook.md).

## Changed on 2026-08-25

The master plan revision cut four things loose. Reasoning for all of them:
[`../00-architecture.md` § *Cut on 2026-08-25*](../00-architecture.md).

- **Spec 08 is cut.** No Perspective views, no mechanism-coloured firehose, no
  `demo-runbook.md`. The demo surface is `mosquitto_sub` on `icc26/#`, the two valve config
  pages on 8085/8086, and the LIMS approval screen on 8000. There is no pattern 8.
- **The Countess is out of the demo.** Its server, compose service, `opc-connection/cell_analyzer`
  and the `cell_analyzer` UDT type stay in the repo as the worked example; the `countess-01`
  instance is deleted and its MQTT publish will not be wired.
- **Pattern 6 moved into `qc/analyzers`.**
- **Pattern 7 needs an event store** for patterns 1, 3, 5 and 6.

## Still open, and not on the order

- ~~`04-cdc.sql` publishes both `bes.batch_event` and `lims.sample_result`.~~ **Closed
  2026-08-26** with pattern 5. The publication names `bes.batch_event` only, on a fresh volume via
  `initdb/04-cdc.sql` and on an existing one via
  [`../../compose/postgres/migrate-06-batch-operation.sql`](../../compose/postgres/migrate-06-batch-operation.sql).
  `tasks.py health` now asserts the membership so it cannot drift back.
- **`database-connection/pg_db` is still in the repo beside `ICC26`**, still selectable, still
  pointing at the wrong database as the wrong user. Decide whether it is deleted.
- **`plant.equipment` holds `BR-201` while every topic, tag path and `bes.batch_event` row holds
  `br-201`.** The architecture doc's rule says those are the same string; for the vessels they are
  not. Recorded 2026-08-26, not fixed — whoever writes pattern 7's joins hits it first.
- **The talk track for 04 predates the 2026-08-26 rebuild** and still describes a queue the
  analyzer fills. Rewrite it with the two-subscription flow and the transcription beat.
- [`00-next-step.md`](00-next-step.md) is **done**, and kept only as the record of what was run
  on 2026-08-17. Everything durable from it has moved.
