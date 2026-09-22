# Provenance — the `i3x` project

This project is **vendored third-party code**, not ours. It is Travis Cox's reference
implementation of the CESMII i3X API (spec v1.0) on top of Ignition 8.3, dropped into this repo so
the demo gateway serves a real, vendor-neutral information-model API at
`https://localhost:8043/system/webdev/i3x` without anyone having to import a project by hand.
`docker-compose.yml` already bind-mounts `./ignition/projects` to the gateway's `data/projects`, so
the folder name *is* the project name — it must stay `i3x`, because the WebDev base URL and every
`i3x.*` script reference are derived from it.

Read `README.md` in this folder for what the API does; it is the upstream README, unmodified.

## Upstream

| | |
|---|---|
| Repository | <https://github.com/iatraviscox/i3x-project> |
| Commit | `72323fdb716b06e7bb7ad2012536631242d4b92f` |
| Commit subject | "Fixed bugs with HasChildren for tags with alarms" |
| Commit date | 2026-07-11 |
| Ignition Exchange | resource **2916** (<https://inductiveautomation.com/exchange/2916>) |
| Vendored on | 2026-09-18 |

**The upstream repository carries no `LICENSE` file.** Nothing in it states terms. We are using it
as a demo of an Exchange resource, credited to its author here and in the talk track; that is the
whole of the claim. If this ever leaves the demo, ask the author for terms first.

## What was and was not copied

Copied, in full and unaltered except where the next section says otherwise:

- `README.md`, `project.json`
- `ignition/script-python/i3x/{handlers,ignition,tag,utils}/{code.py,resource.json}`
- `com.inductiveautomation.webdev/resources/{info,namespaces,objecttypes,relationshiptypes,objects,subscriptions}/*`
  — every file, including the disabled `doPut`/`doDelete`/`doHead`/`doOptions`/`doPatch`/`doTrace`
  stubs each resource ships with

Deliberately left behind:

- `.DS_Store` — noise.
- `com.inductiveautomation.perspective/` and `com.inductiveautomation.vision/` — the API is served
  entirely by WebDev; the client is the desktop i3X Explorer. The Vision resource is an opaque
  `data.bin` we could not review, which is reason enough on its own.
- ~~`ignition/global-props/`~~ — **copied after all, later the same day.** It is the project's
  General properties, and without it every authenticated endpoint answers `500 No user source for
  project`, because WebDev's `require-auth` with an empty `user-source` means *the project's* user
  source. The gzip-wrapped `data.bin` is Ignition's serialised `GlobalProps` and names the auth
  profile `default`, which is also this gateway's user source, so the upstream blob works
  unaltered. It is byte-for-byte the same object this repo's own `icc-2026` project carries.

Line endings were normalised CRLF → LF on the way in, per this repo's `.gitattributes`
(`* text=auto eol=lf`): these files are bind-mounted straight into a Linux container. Tabs — the
upstream indentation throughout the script library — are untouched.

## Local edits

Two constants and four features, in the seven sections below. The constants are retuned for this
gateway; the features are the reason this folder is a fork rather than a copy. **Three of the
sections are one feature applied to three types** — event-store history, which is a single
dispatch table (`_EVENT_HISTORY`) with one entry per type. Each still gets its own section,
because each type keys on a different thing and the key is the part that goes silently wrong.

### `ignition/script-python/i3x/ignition/code.py` — `PROVIDERS`

Added `PROVIDERS = ("default",)` beside the other module constants, and made `getTagProviders()`
return only the browsed providers named in it.

Upstream returns every tag provider on the gateway, which is right for a general server and wrong
here. This gateway carries six: `default`, `MQTT Engine`, `MQTT Transmission`, `MQTT Distributor`,
`pm-sensors` and `System`. Only `default` holds the UDT instances the demo is about. The other five
cost twice: they put five extra roots of chaff at the top of the i3X address space, where the whole
point of the Explorer beat is that a stranger can find `br-201` without being told where to look;
and they are browsed by the structural build in `getUdtInstances`, which queries every provider for
UDT instances and alarm status and is expensive enough that upstream hides it behind a 5 s cache in
`i3x.utils`. Narrowing the browse makes the first uncached request after any tag change faster, and
the address space honest.

### `ignition/script-python/i3x/ignition/code.py` — `ALARM_JOURNAL`

Added 2026-09-19. Changed `ALARM_JOURNAL` from upstream's `"Journal"` to `"icc26_alarm"`.

Not a behaviour change — upstream's own comment on the constant says to set it to match the
gateway's journal. It is recorded here only because it is a diff against upstream that a future
merge will show. The journal was created for the `High Pressure` alarm on `biorx_components/pressure`
and writes to `pg_db`, the connection `pg-historian` already uses. The mismatch is silent by design:
`_historyValue` catches the failed `queryJournal` and returns empty history with a warning under
`i3x.objects`, so a wrong name here looks exactly like an alarm that has never fired.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — declared relationships

Added 2026-09-18. Upstream derives every relationship from the tag tree: HasParent from the
folder path, ComponentOf from UDT nesting, HasAlarm from alarm status, and the type list in
`getRelationshipTypes` is a literal dict of those eight. Nothing else about how the plant is wired
can be expressed, and the demo wants one such fact: the cell analyzer draws its samples from two
bioreactors that sit in another folder entirely.

A UDT instance may now declare its own edges through a String parameter named `Relationships`
(constant `RELATIONSHIPS_PARAM` in `i3x.ignition`) holding a JSON list of
`{"type", "reverse", "targets": [full tag paths]}`. The declaring instance is the source; the
reverse edge on each target is filled by upstream's own bidirectional pass, both directions are
to-many, and the declared pair is served from `/relationshiptypes` under the declaring instance's
`NamespaceUri`. Parameters were the right carrier because the structural build already fetches
them for every instance (that is how `NamespaceUri` reaches the server), so a declaration costs no
extra tag read. Every unusable declaration — bad JSON, a name that collides with a built-in, a
target that is not an object, a pair that contradicts an earlier one — is logged under
`i3x.ignition` and dropped, never raised, so one typo cannot blank the address space.

Touched, each marked `Local fork (icc-2026)` in a comment:

- `i3x/ignition/code.py`: constants `RELATIONSHIPS_PARAM`, `BUILTIN_RELATIONSHIP_IDS`,
  `BUILTIN_REVERSE`, `BUILTIN_TO_MANY`; new `declaredRelationships` and
  `getDeclaredRelationshipTypes`; a declared-edge pass in `getUdtInstances` just before the
  bidirectional pass, which now builds its `REVERSE`/`TO_MANY` tables from the constants plus the
  declared pairs instead of two literals.
- `i3x/handlers/code.py`: the literal type dict moved into `_relationshipTypeTable`, which merges
  the declared pairs in (built-ins win); `_relatedObjects` walks the declared ids after its fixed
  six.

The declaring side in this repo is `cell_analyzer` (parameter added to the type with default `[]`,
overridden on `cell-analyzer-01` with `AnalyzesSamplesFrom` / `SamplesAnalyzedBy` → `br-201`,
`br-202`). Bioreactor declares nothing; its `SamplesAnalyzedBy` edges are the server's doing.

Auth is **as shipped**: `require-auth: true` on every endpoint but `/info`, `required-roles` empty,
`require-https` false. No user or role was added. Clients log in as `admin` over HTTPS on :8043.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — event-store history for `lims_review`

Added 2026-09-18. Upstream serves every object's history from the tag historian, by collecting the
object's atomic tags and reassembling object states from their per-member point series: one row per
distinct change instant across all members, each member forward-filled to its last value as of that
instant. That is the correct reading of *state* — a process value and its limits move on their own
cadences, and forward-fill is exactly what you want — and it is the wrong reading of an *event*.

`lims_review` is an event. Its thirteen members are written together from one MQTT message by
`model_feed.write_review`, so they have no independent cadence, and reassembly invents states the
object was never in. Measured on `br-201`, 2026-09-18: fourteen history rows for one real review —
thirteen of them a 23 ms burst from the members initialising at their own milliseconds — and the
one real row reported `glucose_g_l: 0.0`, forward-filled from initialisation, while the live tag and
the LIMS both said `5.92`. Nothing errored; the object simply lied.

A UDT type named in `REVIEW_TYPE` is now served from `lims.review_event` instead
(`EVENT_STORE_DATASOURCE`, named `LIMS_DATASOURCE` until the analyzer store joined it; migrate-11), keyed to the vessel through the object's nearest UDT ancestor.
`_reviewHistory` in `i3x/handlers/code.py` runs the query and `_historyValue` gains one branch
ahead of the generic one, in the same position and shape as upstream's own `ignition-alarm` branch
— which is the precedent for all of this: upstream already serves alarm history from the alarm
journal rather than the historian, so "an object's history comes from whatever store holds that
object's truth" is upstream's design, not ours.

**Extended 2026-09-19 to the composition children.** The branch above is taken only for the object
the request names. A child reached through its parent — `POST /objects/history` on `br-201` with
`maxDepth: 2`, which is what the Explorer's history panel asks — went down the generic path
instead, so one review answered two ways: twenty-nine forward-filled member rows with `null` where
the live object had values and no `document` member at all (that one is deliberately not
historised), against the single event row it returns when asked for by its own id. Same
`elementId`, two shapes, and the wrong one is the shape a client gets by browsing.

`i3x/utils/code.py`'s `addChildrenHistory` now takes the two arguments `addChildrenValues` always
had — the instance map and, in place of the live path's per-child value, a `historyOverride`
callback — both defaulted to `None` so the signature stays compatible. `_historyValue` passes a
closure that makes the same `REVIEW_TYPE` test its own top level makes. The same change carries the
child's real `isComposition`: it was hard-coded `False` for every child, so `temperature`,
`pressure` and `do` each reported `true` from `/objects/value` and `false` from
`/objects/history` — an object contradicting itself across two endpoints.

**The returned document is handed back untouched.** `lims.review_event.document` is stored already
shaped as the object's member document, in the same encoding `/objects/value` uses, because this
project *cannot* import `model_feed` — neither project is inheritable and neither is the other's
parent. Any mapping written on this side would be a second copy of the write side, free to drift.
One writer, one shape, no translation. That constraint is why the store holds a shaped document
rather than the raw message, and it is the first thing to re-check if either side is edited.

Objects whose members genuinely move independently stay on the historian. `process_value` and its
nested `process_limits` are the reference case and were verified unchanged by this edit.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — event-store history for `cell_analyzer`

Added 2026-09-19. The `lims_review` section above moved one event off the historian and left the
test for it as an `elif`. This adds the second such type and turns the pair into a table.

**Why the analyzer needs it.** Pattern 3 ingests and publishes but never stores, so
`POST /objects/history` fell through to the tag historian, which was asked to reassemble one
analysis out of ~38 OPC nodes. Three faults, all measured 2026-09-19 on `cell-analyzer-01`:

| | |
|---|---|
| Rows | One analysis came back as many, the first of them reporting `sample_id: None` and `viability_percent: None` with `gluc` already set — states the object was never in. |
| `result_json` | Null in every row. The vendor's own payload, the one member that makes a row whole, is not historised at all and cannot be. |
| Collisions | 52 nested leaves flatten to 47 keys. `sample_id`, `sample_type`, `vessel_id` and `cell_type` each exist under both `command/` and `result/`; `osmo` is both the Float8 reading (`result/osmo`) and the Boolean module flag (`result/modules_used/osmo`), and history returned `osmo: 0` — the flag won. With the module on, the measurement is silently overwritten. |

The burst is **not** a subscription artifact, which was worth establishing before blaming the
ingestion. A subscription registered on the instance at `maxDepth 1` took one analysis as *one*
push carrying all 29 changed members together — every `result/*` leaf, `result_json`,
`sample_complete_counter` 1→2 and `state`→Completed — because Ignition coalesces the
instrument's write batch into one UDT change event. No consumer ever observes a partial analysis.
The disassembly happens on the way out of the historian, not on the way in.

`qc.analyzer_result` (migrate-12) now holds the analysis, written from the
`03_opcua/cell-analyzer-result` Event Stream transform before it publishes — store then publish,
pattern 6's `poll` order. The stored `document` is the whole UDT instance value taken by a single
`readBlocking` on the instance path, which is the same call and the same `toDict()` this server
makes for `/objects/value`; `_analyzerHistory` hands it back untouched. That passthrough is sound
only because nothing reshapes it on the way out: the `i3x` project cannot import `opcua_event`,
neither project inheriting the other, so a mapping written here would be a second copy of the
writer, free to drift. Same constraint, same answer, as `lims_review`.

**The dispatch is now a table, `_EVENT_HISTORY`, and it is reader-only.** The plan for this pass
proposed `type suffix -> (store reader, key rule)`. The key rule came out: each reader already
scopes itself and nothing else consumes the rule, so a second tuple element would be a protocol
with one consumer apiece. The scoping genuinely differs — a review hangs under the vessel it is
about and keys on `parentUdt`; the analyzer is a standalone instrument that samples from several
vessels (its `AnalyzesSamplesFrom` edges name `br-201` and `br-202`), its parent is the `analyzers`
folder, which identifies nothing, so it keys on its own `device_id` parameter.

**Matched by `endswith`, deliberately, and not by a dict lookup on `typeId`.** A `typeId` carries
its provider — `[default]_types_/cell_analyzer` — so a dict keyed on the whole string would never
match and `_historyValue` would fall through to the historian. An object silently served from the
wrong store is exactly the failure the table exists to prevent, so the match stays a suffix test
over a two-entry tuple.

**The key is the `device_id` parameter on both sides.** `_analyzerHistory` reads
`udtInstance["parameters"][DEVICE_ID_PARAM]`, the same place `_coalesceMillis` reads
`HistoryCoalesceMs`, and the writer reads the same parameter through
`opcua_event._device_id`. The instance is named `cell-analyzer-01` and its `device_id` is
`CELL-ANALYZER-01`; taking the key from the display name, or from an upper-cased copy of it, makes
every query return `[]` quietly. The `analyzer_id` *member* is the trap on the writer's side — it
sits in the document and reads `CELL-ANALYZER-01` today, but it is OPC-bound to the instrument's
own `Settings/AnalyzerID` node, so it is free to diverge and would split the store in two without
erroring.

**`uptime` is stored with the rest of the document.** It is a liveness tick frozen at write time
and it is noise on a history row, but excluding it would make the stored document differ from what
`/objects/value` returns, and that identity is the whole reason for storing pre-shaped. It is
tied to the heartbeat-split decision and moves with it, not before.

Touched, each marked `Local fork (icc-2026)` in a comment:

- `i3x/ignition/code.py`: `REVIEW_TYPE` joined by `ANALYZER_TYPE` and `DEVICE_ID_PARAM`;
  `LIMS_DATASOURCE` renamed `EVENT_STORE_DATASOURCE`, since one Ignition datasource now reaches
  two stores and naming it after the first was misleading.
- `i3x/handlers/code.py`: new `_analyzerHistory`; the `_EVENT_HISTORY` table and
  `_eventHistoryReader`; `_historyValue`'s `elif` and its `childHistory` closure both go through
  that one lookup, so the object cannot answer one way by id and another through its parent.

Tag history on the 38 `result/` members is left **on** for now. `/objects/history` no longer reads
it, and it has no other consumer in this project — no Perspective views, no scripted historian
reads — but the event branch returns `[]` on a query failure rather than falling back, so turning
it off converts a Postgres outage from degraded into total. Land, measure, then flip.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — event-store history for `particle_counter`

Added 2026-09-20. The third type to come off the historian, and the last of them: after this the
only objects the historian serves are the ones whose members genuinely move independently.

**Why the counter needs it.** Eighteen members written by `particle_counter_poll._write_current`
in one `writeBlocking`, so they have no cadence of their own and the reassembly invents states
again. Measured 2026-09-19 on `particle-counter-01` with `POST /objects/history`, `maxDepth: 1`,
one hour: **four rows inside 32 ms for one analysis, every member null but `total_volume_l`.**
The failure this causes downstream is worse than the rows look, and it is silent:
`sample_chain._environment_i3x` picks the row nearest the sample instant, and a partial row can
win that contest — so pattern 7's i3x path could report `status: None` on a perfectly good
excursion and fall through to `environment_unverifiable`, because every field of that block is
legitimately nullable and nothing errors.

`em.reading` is **not a new store** — it has held one row per analysis since 2026-08-27 and
pattern 7's `sql` source has always read it. migrate-13 adds `document` to it, the same reading in
the object's shape, and `_counterHistory` hands that back untouched. Same passthrough, same
reason, as the two sections above: the `i3x` project cannot import `particle_counter_poll`, so a
mapping written here would be a second copy of the writer.

**The document is the instance's `current/` folder only**, which is narrower than the analyzer's
whole-instance document, and the difference is about the writers, not about taste. The analyzer's
values arrive from an OPC subscription — there is no list of members to build from, so one
`readBlocking` on the instance was the only honest capture and it takes `command/` and `uptime`
with it. The counter's poll authors every value it stores, so the document is nested from the same
`members` list that goes to the tags and contains exactly that list. `state/` is the poll's cursor
and watermark; `config/` is the cleanroom threshold. Neither is a fact about a reading, and
neither is something the backfill of 13,000 existing rows could honestly have invented. So
`/objects/value` on this object returns three folders and a history row returns one — and that
one is identical in shape to its namesake in the live value, which is the property that matters.

**The key is the instance's own NAME**, a third key rule in a three-entry table. The review scopes
to its parent vessel, the analyzer to a `device_id` parameter, and this one to the last segment of
its own tag path — because that is the string `em.reading.device_id` holds, and it holds it
because `particle_counter_poll._device_id` derives it from the last segment of the same path.
One rule applied from both ends, which is why the two cannot drift; `sample_chain` makes the same
observation about `EM_TAG` in its own comment.

Touched, each marked `Local fork (icc-2026)` in a comment:

- `i3x/ignition/code.py`: `COUNTER_TYPE`, and the note on the third disease beside the other two.
- `i3x/handlers/code.py`: new `_counterHistory`; one more row in `_EVENT_HISTORY`. Nothing else —
  the table and `_eventHistoryReader` were built for this on 2026-09-19 and took the third type
  without a change, which is the test of whether that refactor was worth making.

Tag history on the 18 `current/` members is left **on**, on the analyzer's precedent and for the
analyzer's reason: `/objects/history` no longer reads it and nothing else in this project does
either, but the event branch returns `[]` on a query failure rather than falling back, so turning
it off converts a Postgres outage from degraded into total. Land, measure, then flip — both sets
together.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — coalesced history windows

Added 2026-09-19. The section above moves an *event* off the historian entirely. This one is the
other half: the objects that stay on it, whose members are still written by one message.

Upstream keys a history row on each distinct stored timestamp, unioned across the object's members.
That is right when members move on their own cadences and wrong when they arrive together, and
whether they arrive together is decided by the ingestion path, not by the equipment. Sparkplug
carries a payload timestamp that Engine applies to every metric in the message, so those members
land on one timestamp. A custom namespace has nowhere to put one, so Engine stamps each key as it
walks the JSON document.

Both valves are the same `sample_valve` UDT, and measuring one real sample on each, 2026-09-19,
shows the difference is entirely the ingestion:

| Instance | Bound to | Rows for one sample |
|---|---|---|
| `br-202` | `[MQTT Engine]Edge Nodes/…/SV-202/Sample/*` (Sparkplug) | 1 |
| `br-201` | `[MQTT Engine]icc26/site1/upstream/…/sample-acq-completed/values/*` (custom namespace) | 3, spanning 10 ms |

br-201's first two rows hold states the valve was never in — a sample id with no cycle result, then
a cycle result with no completion time — because forward-fill had nothing yet to carry for the
members that had not been stamped.

`_queryHistory` now groups the change timestamps into windows before walking them, and emits one row
per window stamped with the window's **last** timestamp: the instant at which every value in the row
genuinely held at once. A window opens at a timestamp and closes `coalesceMillis` later, anchored on
the open rather than on the previous point — chained on the previous point, a steady stream half a
window apart would merge into one unbounded row covering the whole request. Forward-fill *between*
windows is untouched and stays correct: a member that did not change in this event still carries its
value from the last one.

**The window is declared per instance**, through the Int8 UDT parameter named in `COALESCE_PARAM`
(`HistoryCoalesceMs`), read by `_coalesceMillis`. Per instance and not as a constant here, because
br-201 and br-202 are the same type reached by different ingestion and no module-level number can
tell them apart. The type declares `0`; br-201 overrides it to `2000`. 0 is also the default for
every object that declares nothing, so every other object in the project returns exactly the history
it returned before this edit. The precedent for configuring this server from a UDT parameter is
`RELATIONSHIPS_PARAM` and `NamespaceUri`, both already read the same way.

**2000 ms is chosen against two measurements, not by taste.** The observed spread is 10 ms, and the
floor on genuine events is about 13.5 s — a sample must finish before the next can start
(`SAMPLE_WINDOW_S` 12.0 plus `VALVE_STROKE_S` 1.5 in `services/sim-valve-mqtt`). 2 s sits 200× above
the spread and 6.75× below the floor. It is not tighter because that 10 ms is not bounded by
anything: it is how fast Engine happened to walk one document on an idle gateway, and it will
stretch under load. It fails safe — if br-201 is ever rebound to Sparkplug, a 2 s window over
identical timestamps is a no-op.

**A window that is too wide reports itself.** If a *single* tag contributes more than one stored
point to one window, that window absorbed a real change and the intermediate values are gone from
the result. `_queryHistory` logs a warning naming the object, the window and the tags, because
losing a change quietly is precisely the failure this edit exists to remove — replacing a loud wrong
answer with a quiet one would be worse than leaving it alone. Bound points seeding the
carry-forward are excluded from that count; only points at or after the window opened are changes it
absorbed.

Objects that declare no window are unaffected, including every single-member component: with one
tag there is no union of timestamps to coalesce and every row was already whole.

## Updating from upstream

Re-clone the repo at the new commit, re-copy the file list above, re-apply the `PROVIDERS` and
`ALARM_JOURNAL` edits, the declared-relationships edit, the three event-store history edits
(`lims_review`, `cell_analyzer` and `particle_counter`, which share the `_EVENT_HISTORY` table)
and the coalesced history windows — diff this commit's three `code.py` files (`i3x/ignition`, `i3x/handlers`,
`i3x/utils`) against upstream's before overwriting; the features land in the places named above.
Then update the commit row in this file. There is nothing else of ours in here.
