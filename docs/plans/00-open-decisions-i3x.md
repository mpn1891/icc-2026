# Open decisions — i3X value/history for the analyzer and the particle counter

> **Raised 2026-09-19. Nothing is built until these are answered.** Six questions, each with a
> recommendation; *"as recommended"* is a complete answer to all six.
>
> **All six are now answered** — see *Answered 2026-09-20* below, which is the section to read
> first. Only question 5, the history flags, is deliberately left open. The questions themselves are
> kept below as written, because the reasoning in them is what the answers were chosen against.
>
> The working plan for this branch lives outside the repo at
> `C:\Users\matt\.claude\plans\if-i-wanted-to-rosy-lantern.md` and is the source of truth for
> phases and contracts; this file is the questions it cannot answer by itself. The answers go
> back into that file. **Branch: `i3x`.** Phase 5 (docs) has not landed, so the rest of `docs/`
> is still silent about i3X — this is the first file in here that mentions it.
>
> **Split 2026-09-19, later the same day.** These six were raised as one set and are being executed
> one pattern at a time. The **analyzer's** slice of questions 3, 4, 5 and 6 — plus two
> sub-decisions none of them reach — now lives in
> [`03-opcua-analyzer-i3x.md`](03-opcua-analyzer-i3x.md), which is the file to work from for
> `cell-analyzer-01`. What is left here and unsplit is the **particle counter**: questions 1 and 2,
> and the counter's half of 5. Question 6 is shared and lands with whichever pass runs first.

## Answered 2026-09-20 — the counter, built

**1 and 2, as recommended.** The counter's stored document is **nested**, matching
`/objects/value`, and the 13,215 rows already in `em.reading` were **backfilled** from the columns
they hold. `em.reading` gained a `document` jsonb column
([`migrate-13-em-reading-document.sql`](../../compose/postgres/migrate-13-em-reading-document.sql),
mirrored into `initdb/02-schema.sql` in the same pass), written by `particle_counter_poll` from one
`members` list that also feeds the tags, and served by `_counterHistory` in the i3X server's
`_EVENT_HISTORY` table. `sample_chain._environment_i3x` moved to `value["current"]` in the same
change, as question 1 said it must.

**One sub-decision the questions did not reach, made while building: the document is the
instance's `current/` folder only, not the whole instance value.** That is narrower than the
analyzer's (question 4, answered *whole instance* on 2026-09-19), and the difference is about the
two writers rather than about the two objects. The analyzer's values arrive from an OPC
subscription, so there is no members list to build a document from and one `readBlocking` on the
instance was the only honest capture — `command/` and `uptime` come along with it. The counter's
poll authors every value it stores, so the document is nested from the same list the tags get and
holds exactly that list: `state/` is the poll's cursor and watermark, `config/` is the cleanroom
threshold, and neither is a fact about a reading. It is also the only scope the backfill could
honestly produce — no row in that table records what the cursor was when it landed. **Reversing it
is one list in `particle_counter_poll._members` plus the backfill statement**, if the identity with
`/objects/value` is judged to be worth the poll's bookkeeping riding inside every reading.

**5 is still open and still deferrable.** Tag history stays on for all 56 members (38 under
`cell_analyzer/result/`, 18 under `particle_counter/current/`), on the reason the analyzer pass
gave: `/objects/history` no longer reads either set and nothing else in this project does, but the
event branch returns `[]` on a query failure rather than falling back, so turning it off converts a
Postgres outage from degraded into total. Land, measure, then flip — both sets together.

**6 landed with the analyzer pass.** `lims.review_event` is in `initdb/02-schema.sql`, and so are
`qc.analyzer_result` and now `em.reading.document`.

**3 and 4 were answered and built on 2026-09-19** — see
[`03-opcua-analyzer-i3x.md`](03-opcua-analyzer-i3x.md).

## What this is about

`lims_review` already reads correctly over i3X. Its history comes from `lims.review_event` rather
than the tag historian, because a review is an **event**: its thirteen members are written together
from one message, so they have no independent cadence, and upstream's reassembly — one row per
distinct change instant, every member forward-filled — invents states the object was never in. The
vendored server's own `ignition-alarm` branch is the precedent: an object's history comes from
whatever store holds that object's truth. See
[`../../ignition/projects/i3x/PROVENANCE.md`](../../ignition/projects/i3x/PROVENANCE.md),
§ *event-store history for `lims_review`*.

`cell-analyzer-01` and `particle-counter-01` have the same disease, measured 2026-09-19 with
`POST /objects/history`, `maxDepth: 1`, one hour:

| Object | Measured |
|---|---|
| `cell-analyzer-01` | 39 rows for ~3 analyses. Rows 1 and 2 are 1 ms apart; the first reports `sample_id: None, viability_percent: None, co2_saturation: None` with `gluc` already set. |
| `particle-counter-01` | 4 rows inside 32 ms, everything `None` but `total_volume_l`. |

They also have a second fault: **live nests, history flattens.** `/objects/value` returns
`{"result": {"sample_time": …, "chem": {"gluc": 5.41, …}}, …}`; history returns flat leaf names
(`gluc`, `total_volume_l`) with the folder structure gone, because `i3x.utils.getTags` flattens
across folders and `addChildrenHistory` keys rows by `getTagNameFromPath`. That one is generic to
any UDT with folders, and two folders sharing a leaf name would collide.

The two faults were filed as separable. **For the particle counter they are not** — see question 1.

## The questions

### 1. What shape is the counter's stored document?

Nested, matching `/objects/value` (`current.conditions.flow_rate_lpm`), or flat leaf names,
matching what history rows look like today?

**Recommended: nested.** It is migrate-11's rule — the document is stored pre-shaped, exactly the
member document, so the read side hands it back untouched and no second copy of the shape exists to
drift. It also makes history and value agree *by construction* for that object, which is the good
version of the second fault above: it does not get fixed generically, it dissolves for the objects
that get an event store and stays for the ones still on the historian, where forward-fill is right.

**Cost, and the reason this is question 1:** `sample_chain._environment_i3x`
([`sample_chain/code.py:554`](../../ignition/projects/icc-2026/ignition/script-python/sample_chain/code.py))
reads that history as flat keys today — `value.get("ts")`, `value.get("status")`,
`value.get("location")`, `_channels(value)` scanning for `ch_*`, and the three `conditions` names.
Nested, every one of those returns `None` and 07's i3x path reports `environment_unverifiable` on a
perfectly good reading, silently, because every field is legitimately nullable. So this question
decides whether 07 moves in the same change (~6 lines; `_channels` already takes a dict and can be
handed one level down).

Note that the coupling exists either way: `_environment_i3x` was written against upstream's flat
history, so a generic fix for the second fault breaks it too. This just forces the decision now.

### 2. Backfill `em.reading`, or start i3X history from the change?

`em.reading` is already the counter's record (11,098 rows) but holds *columns*, not a document, and
reading columns into an object shape on the i3X side is the read-side mapping migrate-11 forbids.
So it gains a `document` jsonb column (migrate-12), written by `particle_counter_poll` from one
`members` list feeding both sinks — the refactor `model_feed._store_review` already demonstrates,
where the stored document and the tag members cannot drift because they are literally the same list.

The existing 11,098 rows have no document. Backfill them with a one-shot `jsonb_build_object` over
the existing columns, or leave them null and let i3X history begin at the change?

**Recommended: backfill.** It is frozen at write rather than a living second copy of the shape —
but it *is* a second statement of that shape at the moment it runs, and the migration comment
should say so.

### 3. Where is the analyzer's store written from?

There is no analyzer event store. `lims.sample_result` is the LIMS's projection of the analytes,
not the analyzer's document — the same argument migrate-11 makes for `lims.review_event` over
`lims.sample`. So: a new `qc.analyzer_result` (new schema `qc`, matching the tag path
`icc26/site1/qc/analyzers/…`; `lims` is the LIMS's and `em` is environmental).

Written **from the `03_opcua/cell-analyzer-result` transform** — store then publish, the single read
that `opcua_event.build_cell_analyzer_result` already does backing both the stored document and the
published envelope, wrapped so it cannot raise — or **from a second `ignition.script` handler** on
the same stream, keeping the transform pure?

**Recommended: the transform**, on pattern 6's precedent (`particle_counter_poll.poll` stores before
it publishes). The handler keeps the transform pure at the price of reading the object a second time
at a different instant, which is how a document ends up not matching the analysis it is filed under.
The store write must never raise: the handler's failure strategy is `ABORT`, so a throw in the
transform means the analysis is never published at all.

Either way this avoids the MQTT question entirely — no new Event Stream, no new Engine source, so
neither the one-source-per-topic rule nor the namespace-string overlap can bite.

### 4. What does the analyzer's document contain?

The whole instance value — `analyzer_id`, `command/*`, `last_error`, `result/*`, `result_json`,
`sample_complete_counter`, `software_version`, `state`, `uptime` — or only `result/`?

**Recommended: the whole instance.** The member document is by definition what `/objects/value`
returns, and a one-shot read of all of it is the state the object was genuinely in at the analysis
instant — the opposite of the disease, not a repeat of it. It also puts the vendor's own
`result_json` inside ours, which is worth a sentence in the talk track.

Worth noting: `result_json` is not historised, so today it is a null column in every history row —
the one member that would make a row whole is the one missing from it. Same hole `document` left on
the review.

### 5. Does tag history stay on those 56 members?

38 tags under `cell_analyzer/result/`, 18 under `particle_counter/current/`, all historised to
`pg-historian` by phase 1a.

**Recommended: off, once the event branch lands.** There are no Perspective views in this project
and no scripted historian reads; `/objects/history` is their only consumer, and it will no longer
use them. A historised tag that nothing reads is the same empty claim as a bound tag that nothing
reads — the argument `opcua_event`'s docstring already makes about `batch_id`.

**Costs, stated:** `particle_counter_poll._write_current`'s docstring and
[`06-poll-particle-counter.md`](06-poll-particle-counter.md) get their third rewrite in two days —
phase 1a turned this history *on*, and the reason it gave was the i3X composite read path, which is
exactly what the event store replaces. And the event branch returns `[]` on a query failure rather
than falling back to the historian (deliberately, as the alarm branch does), so history-off makes a
Postgres outage total rather than degraded.

This is the one question that can safely wait: land the branch, measure, then flip.

### 6. Mirror `lims.review_event` into `initdb/02-schema.sql`?

It is not there. migrate-11 was applied live and never mirrored, and
[`migrate-07-em-reading.sql`](../../compose/postgres/migrate-07-em-reading.sql)'s own header says
initdb is the source of truth for a nuke. So a nuke-and-reseed today comes back without the review
store, and `_reviewHistory` answers every request with a warning and an empty list — the object
reverting to "no history" rather than to the wrong history, which is at least the quiet failure.

**Recommended: yes**, in the same pass as migrate-12, and migrate-12 lands in both files from the
start.

## Not a decision, unless you disagree

`i3x.ignition.REVIEW_TYPE` becomes a small table of `type suffix → (store reader, key rule)` rather
than three parallel `elif` branches in `_historyValue`. The key rule genuinely differs per type: the
review scopes to its parent vessel through `parentUdt`, while these two are top-level instances that
scope by their own name (`particle-counter-01` is `em.reading.device_id`). One helper, used by both
`_historyValue`'s top level and the `childHistory` closure, so an object cannot answer one way by id
and another way through its parent — which is the bug that closure was added to fix on 2026-09-19.

## Once these are answered

1. ~~migrate-12~~ — done, split in two: `qc.analyzer_result` plus the `lims.review_event` and
   `qc.analyzer_result` mirrors landed as migrate-12 on 2026-09-19; `em.reading.document` and its
   mirror landed as **migrate-13** on 2026-09-20.
2. ~~`particle_counter_poll` — one `members` list, two sinks.~~ Done: `_members` builds the list,
   `_document` nests it, `_store` and `_write_current` each take it.
3. ~~`opcua_event` / `model_feed` — the analyzer's document.~~ Done 2026-09-19, from the transform.
4. ~~`i3x/handlers/code.py` — the dispatch table, two more store readers.~~ Done: `_analyzerHistory`
   2026-09-19, `_counterHistory` 2026-09-20. The table took the third type without a change.
5. ~~`sample_chain._environment_i3x`~~ — done; question 1 said nested, so it reads `value["current"]`.
6. Phase 1a's history flags — **still open**, question 5. Both sets together, after measurement.
7. Docs: this file's answers folded back into the plan, then phase 5.
