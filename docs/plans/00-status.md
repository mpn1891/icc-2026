# Status

> **Updated 2026-08-26.** Conference is ~4 weeks out.

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

## Not built

**05, 06 and 07.** All three were re-sourced on 2026-08-23 and refined on 2026-08-25; the
2026-08-19 Odoo / Modbus MET ONE / vibration-AMS-DCS plan is withdrawn. The specs are the master
plan entries; read
[`../00-architecture.md` § *Sources as of 2026-08-23*](../00-architecture.md) before touching
any of them.

**Talk tracks for 03, 05, 06 and 07** — written as the closing step of each pattern, per the
master plan's two-document convention.

**Pattern 7's event store** — unspecified, and it blocks 07's spec rather than just its build.
**Narrowed 2026-08-26 to patterns 5 and 6:** the LIMS now persists the valve event and
republishes the sample-open instant on the review message, so 1 and 3 no longer need storing.

## Changed on 2026-08-26

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

- `04-cdc.sql` publishes both `bes.batch_event` and `lims.sample_result`. Drop the LIMS table
  with pattern 5's spec; do not tail both. `payload` jsonb already exists, so `qualified_window`
  needs no schema change. **More urgent since 2026-08-26** — that table's columns changed and it
  is still in the `icc26_cdc` publication.
- **The talk track for 04 predates the 2026-08-26 rebuild** and still describes a queue the
  analyzer fills. Rewrite it with the two-subscription flow and the transcription beat.
- [`00-next-step.md`](00-next-step.md) is **done**, and kept only as the record of what was run
  on 2026-08-17. Everything durable from it has moved.
