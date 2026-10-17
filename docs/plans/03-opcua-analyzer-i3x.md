# Pattern 3 over i3X — the cell analyzer's event store

> **Written 2026-09-19. Branch `i3x`.** This file covers **`cell-analyzer-01` and nothing else**.
> It exists because the analyzer and the particle counter were raised together in
> [`00-open-decisions-i3x.md`](00-open-decisions-i3x.md) and are being executed apart, one pattern
> at a time. Everything here can land, be measured and be demonstrated without touching pattern 6.
>
> **Out of scope, deliberately:** `particle-counter-01` (questions 1 and 2 of the open-decisions
> file, `em.reading.document`, `sample_chain._environment_i3x`), the `deviation` object, and the
> generic flat-history fault on objects that stay on the tag historian. Each gets its own pass.
>
> **Source of truth.** The branch plan is
> `C:\Users\matt\.claude\plans\if-i-wanted-to-rosy-lantern.md`; the six open questions are in
> [`00-open-decisions-i3x.md`](00-open-decisions-i3x.md). This file is the analyzer-shaped slice of
> questions 3, 4, 5 and 6 plus three sub-decisions those questions do not reach. When it is executed,
> its answers go back into the plan file and this file becomes the record.
>
> **Amended 2026-09-19, later the same day.** The push path was measured for the first time and is
> *not* the fault the history path has — but it surfaced a second one, `uptime` dragging the whole
> document every 5 s. That is decision 4a and step 0, in this pass because the stored document's
> shape is settled here and moving a member afterwards is a migration.

## Why this is one job and not two

Pattern 3 ingests and publishes but **never stores**. Pattern 6 polls, stores `em.reading`, then
publishes. That missing store is exactly why the analyzer's i3X history is wrong: with no store,
`POST /objects/history` falls through to the tag historian, which is asked to reassemble one
analysis out of ~38 OPC nodes that arrive milliseconds apart. It invents states the object was
never in — the same disease `lims_review` had, and the vendored server's own `ignition-alarm`
branch is the precedent for the cure: an object's history comes from whatever store holds that
object's truth. See [`../../ignition/projects/i3x/PROVENANCE.md`](../../ignition/projects/i3x/PROVENANCE.md).

So "finish the analyzer as a pattern 3 object" and "make the analyzer correct over i3X" are the
same work, done once.

## Already built and correct — do not redo

| | State |
|---|---|
| OPC UA ingest | 52 bound tags on `cell_analyzer`, `valueSource: "opc"`, bound through the `opc_server` / `namespace_uri` / `node_sep` parameters. Built and broker-verified 2026-08-20. |
| Publish | Event Stream `03_opcua/cell-analyzer-result`, source `ignition.gatewayEvent`, transform one line into `opcua_event.build_cell_analyzer_result`, MQTT handler on `icc26/site1/qc/analyzers/cell-analyzer-01/sample-analyzed`, QoS 1, `failureStrategy: ABORT`. |
| `POST /objects/value` | **Already correct.** Returns the nested live document straight off the OPC subscription — `result.chem.gluc`, `command.vessel_id`, `result_json` and all. Nothing on the value side needs changing. |
| Declared relationship | `AnalyzesSamplesFrom` → `br-201`, `br-202` (reverse `SamplesAnalyzedBy`), on the instance, serving. Plan decision 9. |

One caveat, and this pass now fixes it rather than only recording it (decision 4a): the envelope
`timestamp` on `/objects/value` is the newest member change on the instance, which is `uptime` ticking — measured 2026-09-19 advancing
`21:05:25.943Z` to `21:06:13.344Z` while `sample_time` sat still. It says the analyzer is alive, not
when it sampled. Sample age is `result.sample_time`.

## The fault, measured

`POST /objects/history` on `[default]icc26/site1/qc/analyzers/cell-analyzer-01`:

| Measured | |
|---|---|
| 2026-09-19, one hour, `maxDepth: 1` | **39 rows for ~3 analyses.** Rows 1 and 2 are 1 ms apart; the first reports `sample_id: None`, `viability_percent: None`, `co2_saturation: None` with `gluc` already set. |
| 2026-09-19, ten minutes | One analysis came back as **one clean row** — every historised member happened to store at the same millisecond. The burst is intermittent, which makes it worse to demonstrate, not better. |

Two further faults in the same payload, both generic to any UDT with folders:

- **Live nests, history flattens.** 52 leaves arrive as 47 flat keys, because
  [`i3x.utils.getTags`](../../ignition/projects/i3x/ignition/script-python/i3x/utils/code.py) walks
  through folders without recording them and `addChildrenHistory` keys each column by
  `getTagNameFromPath` — the leaf name only.
- **Five leaf names collide**, and one resolves wrong. `sample_id`, `sample_type`, `vessel_id` and
  `cell_type` each exist under both `command/` and `result/`; `osmo` exists as both the Float8
  reading (`result/osmo`) and the Boolean module flag (`result/modules_used/osmo`). History returns
  `osmo: 0` — the flag won. The module is off this run so the reading is null anyway, but with it on
  the measurement is silently overwritten. `gas` is not a collision but reads as one: the gas panel
  in the value document, the module flag in the history row, same key.

**Both dissolve for this object once its history comes from a stored document** — distinct paths in
a document cannot collide, and the document is stored in the shape `/objects/value` returns. They
are *not* being fixed generically; that stays open for objects still on the historian, where
forward-fill is right and a merged flat row is an honest description of what it is.

### The push path is a different fault — measured 2026-09-19

`POST /subscriptions/register` on `[default]icc26/site1/qc/analyzers/cell-analyzer-01`, `maxDepth: 1`,
drained, then one analysis watched over 45 s through `sync`:

| Measured | |
|---|---|
| One analysis | **2 pushes.** `command.sample_id` + `state`→Running at the login; then **31 members in a single push** — every `result/*` leaf, `result_json`, `modified_time`, `sample_time`, `sample_complete_counter` 1→2 and `state`→Completed, together. |
| Idle | **9 further pushes, `uptime` alone**, one every 5 s. |
| Payload | 4767–4772 bytes per push; 57 247 bytes over the 45 s. |

**The burst is a historian artifact, not a subscription one.** `sample_complete_counter` is written
*last* by the instrument — after every result leaf and after `State` — and it still arrives in the
same push, because Ignition's OPC subscription coalesces the write batch into one UDT change event.
No consumer ever observes a partial analysis. So there is nothing to debounce and no trigger tag to
declare: the live path is already correct, and the store's write trigger stays the Event Stream
(decision 3).

What the measurement did find is `uptime`. Nine of twelve pushes carried the whole 52-member
document, `result_json` blob included, to deliver a liveness string — about 3.4 MB/hour per
subscribed client, for an object whose content changes a few times an hour. That is the rule
`biorx_components/process_value` already states: *"the server ships the whole document on every
member change, so the only member that may change at pv's cadence is pv."* The analyzer breaks it,
and the envelope-`timestamp` caveat above is the same cause read from the other end. Decision 4a.

## Decisions

Numbered to match [`00-open-decisions-i3x.md`](00-open-decisions-i3x.md) where they correspond.
**"As recommended" is a complete answer.**

### 3. Where the analyzer's store is written from

`qc.analyzer_result` — new schema `qc`, matching the tag path `icc26/site1/qc/analyzers/…`
(`lims` is the LIMS's, `em` is environmental). `lims.sample_result` is the LIMS's projection of the
analytes, not the analyzer's document, on the same argument migrate-11 made for `lims.review_event`
over `lims.sample`.

**Recommended: from the `03_opcua/cell-analyzer-result` transform** — store then publish, the single
read backing both, wrapped so it cannot raise. Pattern 6's precedent (`poll` stores before it
publishes). A second `ignition.script` handler would keep the transform pure at the price of reading
the object again at a different instant, which is how a document ends up not matching the analysis
it is filed under. No new MQTT source either way, so the one-source-per-topic rule never comes up.

### 4. What the document contains

**Recommended: the whole instance value** — `analyzer_id`, `command/*`, `last_error`, `result/*`,
`result_json`, `sample_complete_counter`, `software_version`, `state` — 51 members, `uptime`
excluded because decision 4a moves it out of the instance document. The member document
is by definition what `/objects/value` returns, and a one-shot read of all of it is the state the
object was genuinely in at the analysis instant — the opposite of the disease, not a repeat of it.
It also closes the hole that `result_json`, the vendor's own payload, is not historised and is
therefore null in every history row today: the one member that would make a row whole is the one
missing from it.

### 4a. NEW — `uptime` moves into a nested `core_heartbeat` object

Not reached by question 4, and only visible once the push path was measured. `uptime` is the one
member that moves on its own cadence — nothing else changed between analyses across 45 s — and
because the server ships the whole document on any member change, it drags the other 51 with it
every 5 s.

**Recommended: a nested UDT instance `heartbeat`, of a new type `core_heartbeat`, holding `uptime`
alone.** Exactly the `process_value` / `process_limits` shape, for exactly its reason: a member with
its own cadence becomes a HasComponent child streamed as its own element, so a consumer registering
the analyzer at `maxDepth 1` hears the analysis and nothing else.

**Named for the vendor node, not for the cadence.** The OPC path is
`OPCSystemObjects/CoreHeartbeat/UpTime`, and section 6 of the manual names `CoreHeartbeat/UpTime`
and `DateTime/DateTime` as the pair to subscribe to to confirm the server is updating — the sim's
heartbeat loop writes both every 5 s. Only `UpTime` is bound as a tag today, so the type has an
obvious second member waiting rather than a future rename. A generic `device_uptime` was considered
and rejected: the only other member in this project with the same disease is
`sample_valve/last_sample_age_s`, which a type of that name could not house (see *Not in this pass*).
What those two share is cadence, not meaning, and cadence is the reason to split an object out —
never the thing to name it after. `process_limits` is the precedent: split for cadence, named for
what it holds.

**Why in this pass and not after it.** Decision 4 fixes the stored document's shape. Move `uptime`
later and every row already in `qc.analyzer_result` carries the old tree while `/objects/value`
returns the new one — the two shapes the store exists to keep identical. Free now, a migration later.

**Costs, stated:** a `cell_analyzer` type edit and an instance rebuild, which is the thing step 2
declines to do for the tag script — here bought for something. And the nested type has to reach the
parent's `opc_server` / `namespace_uri` / `node_sep` parameters to bind at all. No nested UDT in this
project passes parameters down yet, so that is the one genuinely unproven mechanic in this pass:
prove it before writing anything else.

### 3a. NEW — the dedupe key must include `modified_time`

Not reached by questions 3 or 4. `result/modified_time`'s own tag documentation says it *"runs later
if CDV images are reanalyzed or the result is edited after the fact — so a result CAN change after
you have already read it."* So the unit is not one analysis but **one analysis version**.

**Recommended: unique on `(device_id, sample_id, modified_time)`.** A revision then lands as a new
row and a duplicate fire is a no-op. Keyed on `sample_id` alone, a revision is silently dropped —
the exact failure the store exists to prevent, and one nothing downstream could detect.

### 3b. NEW — the i3X key rule reads the `device_id` parameter, not the instance name

The instance is `cell-analyzer-01`; its `device_id` parameter is `CELL-ANALYZER-01`. The store
reader must take the key from `udtInstance["parameters"]` — already in hand in `_historyValue`, which
is how `_coalesceMillis` reads `HistoryCoalesceMs` — and never from the display name or an
upper-cased copy of it. Get this wrong and every query returns `[]` quietly.

### 5. Tag history on the 38 `result/` members

**Recommended: off, but not in this pass.** `/objects/history` is their only consumer — there are no
Perspective views and no scripted historian reads in this project — and it will no longer use them.
**Costs, stated:** the event branch returns `[]` on a query failure rather than falling back to the
historian (deliberately, as the alarm branch does), so history-off turns a Postgres outage from
degraded into total. Land the store, measure it, then flip.

### 6. Mirror `lims.review_event` into `initdb/02-schema.sql`

**Recommended: yes**, in the same pass as migrate-12, and migrate-12 lands in both files from the
start. migrate-11 was applied live and never mirrored, so a nuke-and-reseed today comes back without
the review store and `_reviewHistory` answers every request with a warning and an empty list.
Verified 2026-09-19: `lims.review_event` exists in Postgres, and the schemas are `bes`, `em`, `lims`,
`plant` — there is no `qc`.

## Steps

### 0. `cell_analyzer` — `uptime` out of the value document

First, because both the stored document and the transform's member list depend on the shape landing
before they are written against it.

A new type `core_heartbeat` beside `cell_analyzer` in
`tag-type-definition/default/udts.json`, one member `uptime` carrying the `opcItemPath` and
`opcServer` parameter bindings it has today, plus `NamespaceUri` as every type here has. Nest it on
`cell_analyzer` as `heartbeat`, passing `opc_server`, `namespace_uri` and `node_sep` down from the
parent, and drop the top-level `uptime`. Per decision 4a, prove the parameter pass-through binds
before writing the rest of the pass — a nested instance whose OPC path does not resolve is
`Error_Configuration` in the browser and silent in the gateway log.

**Applied and measured 2026-09-19.** `core_heartbeat` added, `heartbeat` nested, the top-level
`uptime` removed, scanned, instance rebuilt. What it bought and what it did not is below — read that
before writing anything against it.

**The parameter pass-through, resolved.** Declaring `opc_server` / `namespace_uri` / `node_sep` on
`core_heartbeat` and setting each on the nested instance to the parent token does **not** work: the
child parameter shadows the parent's, `{namespace_uri}` resolves against itself, and the binding
keeps its literal braces — `/objects/list` returned `namespace_uri: "{namespace_uri}"` and `uptime`
read `null` at Good quality. The child must declare **no** such parameter at all; an undeclared
`{param}` in a nested member's binding resolves up the hierarchy to the enclosing instance. With the
three declarations removed, `heartbeat/uptime` reads `0d 09:23:40` and ticks.

Three consequences, all now measured:

- **`cell-analyzer-01` becomes a composition.** `/objects/value` at `maxDepth 1` returns the analysis
  document alone; `heartbeat` arrives under `components` at greater depth. That is what puts the stored
  document and the live document back on the same tree.
- **`heartbeat` answers `/objects/history` with an empty list**, because `uptime` is not historised.
  Correct, not a regression.
- **The push rate does not drop, and the envelope `timestamp` still tracks the heartbeat.** Both of
  those were expected here and both are wrong. Measured idle at `maxDepth 1`: **7 pushes in 30 s,
  4752 bytes each**, against 12 in 45 s before — the same rate. Consecutive idle pushes carry a
  **byte-identical `value`** and differ only in `timestamp`, and `/objects/value`'s envelope
  timestamp still advances every ~5 s.

  The reason is that nesting changes the *payload*, not the *notification*. `heartbeat` is still a
  member of the `cell_analyzer` tag UDT, so a change to `heartbeat/uptime` still fires the parent's
  tag-change event; `getTagValue` then strips the HasComponent children out of the document
  (`i3x.utils.removeChildren`), so what ships is an unchanged document with a fresh timestamp. The
  same mechanism applies to `biorx_components/process_value`, whose documentation claims a consumer
  registering the parent at `maxDepth 1` "hears pv alone" — true of the content, not of the wake-up.

  **So the split is worth keeping and is not sufficient.** It makes the heartbeat a real object,
  takes `uptime` out of the stored document (which is what decision 4a is for, and that still
  holds), and is a prerequisite for any consumer that wants liveness separately. Stopping the churn
  needs a second change, on the server side — see *Open*, below.

Nothing on the wire moves: the 29-entry `_FIELDS` projection is `result/` members only and has never
carried `uptime`, so pattern 4's contract is untouched. Nothing else in the repo reads `uptime`
either — only this file and the type definition mention it.

### 1. `compose/postgres/migrate-12-analyzer-result.sql`, and `initdb/02-schema.sql`

Schema `qc`; table `qc.analyzer_result` holding identity, the analysis instant, and `document`
jsonb; unique index per decision 3a. Mirror `lims.review_event` into `initdb/02-schema.sql` in the
same pass and land migrate-12 in both files from the start, per decision 6.

### 2. `opcua_event.build_cell_analyzer_result`

The Event Stream config does **not** change — its transform line stays
`return opcua_event.build_cell_analyzer_result(event.data)`. All of the work is inside the module:

1. **Reach the instance, not just the result folder.** The tag script hands in
   `…/cell-analyzer-01/sample-analyzed`; `command/*`, `analyzer_id`, `state`, `result_json` and `last_error`
   are one level up. `uptime` is no longer among them — after step 0 it is `heartbeat/uptime`, its
   own object, and out of the stored document entirely. Derive the instance path inside the function rather than changing
   the tag script — that script lives in the UDT definition, and editing it means a type edit and an
   instance rebuild for no gain.
2. **One `readBlocking` over the instance's 51 members**, as now — one read, one instant. 52 minus
   `uptime`, which step 0 moved under `heartbeat`.
3. **Build the member document nested** to match `/objects/value`'s tree (`result.chem.gluc`,
   `command.vessel_id`), not the envelope's `{ts, values}` tree. The nesting code effectively
   already exists here: `build_cell_analyzer_result` builds `gas`, `chem`, `cell_density`,
   `calculated` and `modules_used` today. It needs widening and re-rooting, not inventing.
4. **Store it, wrapped so it cannot raise.** The handler's `failureStrategy` is `ABORT`: a throw in
   the transform means the analysis is never published at all. A Postgres that is down must not cost
   the demo its publish — the same rule `model_feed._store_review` states.
5. **Project today's envelope from the same dict and return it byte-identical.** Pattern 3 is
   broker-verified and pattern 4's LIMS bridge parses this; the published shape must not move.

Keep the existing 29-entry `_FIELDS` as the explicit projection list beside the new full-instance
member list. They are not two copies of one shape: one says what the object *is*, the other says
what goes *on the wire*, and the wire one is a contract with pattern 4.

### 3. The i3X server — dispatch table plus the analyzer reader

`i3x.ignition.REVIEW_TYPE` (`"_types_/lims_review"`) becomes a table of
`type suffix -> (store reader, key rule)` rather than parallel `elif` branches in `_historyValue`.
The key rule genuinely differs per type: the review scopes to its parent vessel through `parentUdt`,
the analyzer scopes by its own `device_id` parameter (decision 3b). Used by **both** `_historyValue`'s
top level and the `childHistory` closure, so the object cannot answer one way by id and another way
through its parent — the bug that closure was added to fix on 2026-09-19.

The analyzer reader: `qc.analyzer_result` over `[startTime, endTime]` by `device_id`, rows out as
`{"value": document, "quality": "Good", "timestamp": …}`, and `[]` plus a warning on failure — the
same contract the alarm and review branches already keep.

This is the fifth local edit to the vendored server, after `PROVIDERS`, `ALARM_JOURNAL`, declared
relationships and the `lims_review` event store (itself since extended by coalesced windows).
**It gets its own section in [`../../ignition/projects/i3x/PROVENANCE.md`](../../ignition/projects/i3x/PROVENANCE.md)**,
as the other two did.

## Checkpoints — Matt runs these

1. One analysis → **exactly one** row from `POST /objects/history` over the minute. Phase 4 item 5 of
   the plan file, inverted: it asked whether ~35 OPC nodes over several ms split into many rows. They
   do, and this is the answer.
2. That row's `value` is nested identically to `/objects/value`'s tree, and **`result_json` is in
   it** — the member that was null in every historian row.
3. `osmo` is the reading and `modules_used.osmo` is the flag, in the same row, both present.
4. A re-analysis of the same sample (a second `modified_time`) lands as a second row rather than
   being swallowed; a duplicate stream fire lands as none.
5. Stop Postgres, run one analysis: the MQTT publish still happens, the store write logs and is
   swallowed, and `/objects/history` answers `[]` with a warning rather than a 500.
6. Explorer: browse to `cell-analyzer-01`, History panel over the demo window, rows render.
7. Idle at `maxDepth 1`: **zero** pushes. **Fails today** — 7 in 30 s, value byte-identical, only
   the timestamp moving. Blocked on the listener change under *Open*; the type split alone does not
   get there. One analysis still comes through as the login plus one whole-result push, with
   `sample_complete_counter` and `state`→Completed in the second, which is unchanged and correct.
8. `/objects/value` on the instance: `heartbeat` absent at `maxDepth 1`, present under `components`
   deeper — **passes**. Envelope `timestamp` the analysis instant rather than a heartbeat tick —
   **fails today**, same cause as 7.
9. `heartbeat/uptime` Good and ticking, not `Error_Configuration` — **passes**, once the shadowing
   parameters are removed (step 0).

## Open — raised by the step 0 measurement, not yet decided

**Should `I3XTagChangeListener` suppress a push whose value is unchanged?** Measured above: the
analyzer emits 4752 identical bytes every ~5 s, and the only thing that moved is the envelope
timestamp. A value-change subscription that re-sends an unchanged value is arguably just wrong, and
the listener already has everything it needs — keep the last pushed value per element on the
subscription and skip when the new one compares equal.

It would be the sixth local edit to the vendored server, and unlike the others it is general rather
than analyzer-shaped: it fixes `process_value`/`process_limits` and `sample_valve` in the same
stroke, without either of them having to be touched. Against it: a consumer relying on "still alive,
same value" as a heartbeat loses that, which is a real semantic change and is why this is a decision
rather than a step. The heartbeat is its own object now, which is where such a consumer should look.

Not started, and deliberately not folded into this pass without a decision.

## Not in this pass

- **Pattern 6 / the particle counter.** Questions 1 and 2. Its conversion is coupled to
  `sample_chain._environment_i3x` (`sample_chain/code.py:576`), which reads history by flat key and
  would silently report `environment_unverifiable` on good readings the moment the shape nests.
  Nothing in this repo reads the *analyzer's* history — 07's only two i3X history reads are the
  bioreactor at `sample_chain/code.py:520` and the counter at `:576` — so the analyzer converts with
  **zero consumer coupling**. That is why it goes first: it is the free proving run for the pattern.
- **Turning off tag history** on the 38 `result/` members. Decision 5, after measurement.
- **The generic flat-history fault.** It stops applying to this object but stays for everything still
  on the historian. A collision warning in `addChildrenHistory`, in the idiom `_queryHistory` already
  uses when a coalesce window swallows a point, is the candidate fix and belongs with that pass.
- **`sample_valve/last_sample_age_s`, which has the same disease and is worse.** It is
  `secondsBetween({[.]last_sample_time}, now())` with no rate argument, so Ignition recomputes it on
  the default 1 s poll and the Int8 changes every second — and `sample_valve` is nested in
  `bioreactor`, so a subscriber on `br-201` at `maxDepth 0` takes a push per second carrying the
  valve's whole document. Read from the type definition, **not measured**: the trial expired before a
  subscription could be run. Different pattern, different object; the fix is the same shape as step 0
  but it is not this pass. Nothing else in the `default` types ticks on a clock.
- **`docs/` and talk tracks.** Phase 5, after 0–4 land. `docs/plans/03-opcua-analyzer-playbook.md`
  and `docs/talk-tracks/03-opcua-analyzer.md` gain the store then, not now.

## Scope guard for anyone executing this

Files this pass may touch, and no others:

- `ignition/config/resources/core/ignition/tag-type-definition/default/udts.json` (step 0 only)
- `compose/postgres/migrate-12-analyzer-result.sql` (new), `compose/postgres/initdb/02-schema.sql`
- `ignition/projects/icc-2026/ignition/script-python/opcua_event/code.py`
- `ignition/projects/i3x/ignition/script-python/i3x/{handlers,ignition}/code.py`
- `ignition/projects/i3x/PROVENANCE.md`, this file

Jython 2.7 in every `.py` under `script-python`: no f-strings, no type hints, `%` formatting, and
match the neighbouring module's docstring voice — decisions and their reasons, in prose. Validate
with `ast.parse` on every `.py` and `json.tool` on every `.json`. **Do not** run `tasks.py scan`,
restart containers, or commit; Matt runs every verification step.
