# Architecture

The decisions in this stack that are not obvious from reading the compose file, the reasoning
behind them, and every trap that cost real time to find. **This is the reference — one home per
fact.** If something here is contradicted elsewhere, this file wins; if you learn something new,
add it here rather than in a status note.

- What is built and what is not: [`plans/00-status.md`](plans/00-status.md).
- What is still to be built, and in what order: [`plans/00-master-plan.md`](plans/00-master-plan.md).
- Talk tracks, as they are written: [`talk-tracks/01-native-mqtt.md`](talk-tracks/01-native-mqtt.md),
  [`talk-tracks/02-sparkplug-b.md`](talk-tracks/02-sparkplug-b.md),
  [`talk-tracks/04-lims-webhook.md`](talk-tracks/04-lims-webhook.md).

---

## The stack

```
                     ┌──────────────────────────────┐
   pattern 1  ──────▶│                              │◀────── pattern 2  (the SAME valve,
   pattern 3  ──────▶│   Chariot MQTT Server        │                    Sparkplug B)
    pattern 6  ──────▶│   :1883 / :8090(ws) / :8081  │◀────── pattern 7  (listens, then
                     └──────────────┬───────────────┘                    publishes aggregate)
                                    │
                          MQTT Engine / Transmission
                                    │
   pattern 4 (webhook) ──▶ ┌────────┴─────────┐
   pattern 5 (CDC/HTTP) ─▶ │  Ignition 8.3.8  │──── JDBC ────▶ PostgreSQL 17
   pattern 6 (HTTP poll) ─▶│  + Cirrus 5.0.4  │                (wal_level=logical)
                           └──────────────────┘                   │
                                                             Debezium
                                                              Server
                                                         (tails bes.batch_event)
```

Patterns 1 and 2 are one smart sample valve assembly in two firmwares, in their own containers.
Everything else publishes through Ignition. Pattern 7 is not a new inbound transport: it
**subscribes** to the LIMS review topic and publishes one aggregate document.

| Service | Host ports | Built in |
|---|---|---|
| `postgres` | 5432 | step 1 |
| `ignition` | 8088, 8043 | step 1 |
| `chariot` | 1883, 8883, 8090, 8081, 8444 | step 1 |
| `opcua-novaflex` | 4841 | step 4 |
| `sim-valve-mqtt` | 8085 (config page) | pattern 1 |
| `sim-valve-spb` | 8086 (config page) | pattern 2 |
| `lims` | 8000 (approval screen) | pattern 4 — built; pass/fail remaining |
| `debezium` | 8083 | pattern 5 — planned |
| `sim-metone` | TBD (HTTP API) | pattern 6 — planned |

`lims` is built: see [`plans/04-lims-webhook.md`](plans/04-lims-webhook.md) and
[`talk-tracks/04-lims-webhook.md`](talk-tracks/04-lims-webhook.md). Odoo, the AMS stub, the DCS OPC server and a
Modbus particle counter were the 2026-08-19 plan and are not in this stack.

**Patterns 1 and 2 are the same physical device in two firmwares** — a badge-operated smart
sample valve assembly, one on `BR-201` speaking plain MQTT and one on `BR-202` speaking
Sparkplug B. `valve.py` and `webui.py` are byte-for-byte identical between the two build
contexts, so everything that differs between the containers is a difference the protocol
caused. Each serves its own device commissioning webpage (8085, 8086), and **the difference
between those two pages is as much of the talk as the traffic is**: on one the topic, QoS and
retained flag are editable fields, on the other the same three controls are disabled with the
specification clause that fixed them. Both valves are publish-only — nothing on the backbone
can open either. Talk tracks: [`talk-tracks/01-native-mqtt.md`](talk-tracks/01-native-mqtt.md) and
[`talk-tracks/02-sparkplug-b.md`](talk-tracks/02-sparkplug-b.md). Build specs:
[`plans/01-native-mqtt.md`](plans/01-native-mqtt.md) and
[`plans/02-sparkplug-b.md`](plans/02-sparkplug-b.md).

The retired vibration-gateway implementation was **deleted on 2026-08-23**: the `vibsim` script
module, both `vibration-gw-*` event streams and `services/sim-vibration/`. They had been "on
disk, wired to nothing" since 2026-08-17, except that `vibration-gw-listener` was still
`enabled: true` and subscribed to a topic no service published — a live gateway resource for a
withdrawn pattern. Pattern 7 no longer uses a waveform or `cmd`/`response`, so nothing was
waiting on them.

**Two pieces of that implementation were kept, and one of them is not retired at all.**

- **`icc26-native` is the sample valve's Engine namespace, not the vibration gateway's.** Its
  subscription is `icc26/site1/upstream/br-201/sample-valve-01/#`. It was repurposed when
  pattern 1 became the valve, and earlier revisions of this document wrongly listed it as part
  of the retired set. **Deleting it breaks pattern 1.**
- The `vibration_sensor` UDT and `br-201`'s `asset_data/agitator_vibration` member are now
  orphaned, but `tag-type-definition/default/udts.json` has to be opened anyway to give the
  `bioreactor` type its phase tag. Remove them with pattern 5's spec rather than in a second
  pass over the same file.
- **A `br-202` `bioreactor` instance was added on 2026-08-25 and looks like a mistake.** It
  carries the same orphaned `asset_data/agitator_vibration` member, and that member's `uns_path`
  parameter reads `site1/upstream/br-201/agitator-vib` — the wrong reactor. `BR-202` is the
  Sparkplug valve's reactor and its data arrives through Engine's Sparkplug namespace, not
  through a `default`-provider UDT. Drop the instance, or keep it with pattern 5's phase tag and
  nothing else.

The Engine Edge Node tag trees under `MQTT Engine/Edge Nodes/` are gitignored and regenerate
from the broker, so they needed no cleanup.

**Nothing may depend on the internet at runtime.** It is a conference network and a stage. Every
image is pulled ahead of time, no page loads from a CDN, and the acceptance test is that the
whole demo runs with networking disabled. (This rule was written with the Perspective firehose
in mind — that piece is cut, see *Cut on 2026-08-25*, but the rule outlives it and applies to
anything served on stage.)

**One repo, not several.** Every meaningful change here is cross-cutting — changing the topic
namespace touches Ignition config, the simulators, Chariot ACLs, Debezium config and the docs.
That is one commit and one review in a monorepo; across four repos it is four PRs with an
ordering dependency, and bisecting gets painful. Compose also builds from the tree
(`build: ./services/...`), so splitting services out would mean publishing images to a registry
— a CI pipeline and a network dependency bolted onto a demo whose main virtue is running
offline. The usual reason to reach for a split, "the gateway writes noise into my repo", is
solved by `.gitignore` instead: only `data/config` and `data/projects` are committed.

### LIMS contract — one surface, not four

`lims` served four patterns under the original convergence design. **Since 2026-08-19 it serves
exactly one HTTP surface**, and that is still true after the 2026-08-23 re-source: **a sample
result, received off the backbone, reviewed by a human, pushed to Ignition over HTTP with
`analyst` and `disposition` ∈ `pass | fail`.** See
[`plans/04-lims-webhook.md`](plans/04-lims-webhook.md). SENAITE was under consideration when four
patterns depended on this; for one webhook, a small FastAPI service is the right size.

Pattern 7 **consumes the MQTT message** that webhook produces. That is not a second LIMS
contract — it is a backbone subscriber, addressed like any other. With the firehose cut, pattern
7 is now the *only* thing in the stack that subscribes to the backbone, which makes "one event
backbone" a claim resting on one consumer. Say it that way rather than implying a crowd.

The three retired HTTP/SQL surfaces (`GET /results?since_id=N`, a Debezium-tailed insert into
`lims.sample_result`, and a query against the LIMS for an aggregation script) are retired, not
pending. Pattern 5 now tails `bes.batch_event`. Pattern 6 polls a MET ONE HTTP API. Pattern 7
joins MQTT topics.

---

## Topic namespace

Organized by **ISA-95 physical hierarchy, never by ingestion mechanism.** A subscriber must
not have to know *how* data arrived in order to find it. If the LIMS migrates from polling
to CDC, nothing downstream should break.

```
icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}
```

```
icc26/site1/upstream/br-201/sample-valve-01/event/badge-scan       # 1  every badge, granted or denied
icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete  # 1  only when a sample ran
icc26/site1/upstream/br-201/sample-valve-01/status     # 1  online/offline, retained; also the LWT
icc26/site1/upstream/br-201/sample-valve-01/telemetry  # 1  air supply / enclosure temp, every 5 s
icc26/site1/qc/analyzers/flex-01/result                # 3  Nova result (renamed from novaflex-01, 2026-08-25)
icc26/site1/qc/lims/sample-result                      # 4  review: analyst + pass/fail
icc26/site1/upstream/br-201/batch/event                # 5  CDC of bes.batch_event
icc26/site1/qc/analyzers/particle-counter-01/result    # 6  MET ONE analysis (provisional)
icc26/site1/upstream/br-201/sample-chain/event         # 7  aggregate (provisional)

spBv1.0/ICC26-Site1-UPSTREAM/{NBIRTH|NDEATH}/SAMPLE-VALVE-02             # 2 — spec-mandated
spBv1.0/ICC26-Site1-UPSTREAM/{DBIRTH|DDATA|DDEATH}/SAMPLE-VALVE-02/SV-202 # 2 — spec-mandated
```

Areas: `upstream`, `downstream`, `qc`, `utilities`. These are **places** — in a biologics
facility upstream and downstream really are segregated suites, with their own cleanroom grades,
HVAC and personnel flow, so the process name and the physical area coincide. The industry would
write these `usp`/`dsp`; spelled out they cost four characters and stop `dsp` colliding with
*digital signal processing* in a talk that plots bearing spectra.

Message types are a closed set: `telemetry`, `event`, `event/<subtype>`, `waveform`, `status`,
`state`, `cmd/<verb>`, `response/<what>`, `ack`.

**`status` is liveness; `state` is process condition.** Pattern 1 publishes `status` — a
retained `online`/`offline` pair, the will being the `offline` half — and publishes no `state`
at all, having decided valve position is nobody else's business (2026-08-25). `state` stays in
the set as vocabulary; like `cmd` and `response`, **no pattern currently uses it.**

**`event/<subtype>` is optional and was added 2026-08-23**, for pattern 1. A device that emits
more than one *shape* of event may name each one, on the same two-token form as `cmd/<verb>` —
and must, if it wants a usable tag tree, because Engine's custom namespace mirrors whatever
document arrives and writes only the keys that document contains. Two shapes on one topic means
one `values` folder holding the union of both, with half the tags stale from the other shape.
See [`plans/01-native-mqtt.md § Why the two event subtypes are two topics`](plans/01-native-mqtt.md).

`icc26/+/+/+/+/event/#` catches both forms, because **`#` matches zero levels** — so a
subscriber written against plain `…/event` keeps working, and no other pattern is obliged to
grow a subtype. The subtype is part of the *taxonomy the device invented*, which is exactly what
pattern 2 does not have to do: Sparkplug declares one metric list in DBIRTH and there is no
second shape to name.

`cmd/<verb>` and `response/<what>` are the two-token pair, and they go together: wherever one
appears the other should too. **All three of `cmd`, `response` and `ack` are currently used by
nobody.** Patterns 1 and 2 are both publish-only field devices — a sample valve that needs the
network's permission to open is a sample valve that stops working when the network does — and
no other pattern has yet needed to address a device. They stay in the set as the names to
reach for rather than inventing new ones later.

Pattern 7 was going to be the first user of the pair (an AMS asking for a vibration reading).
**That design is gone as of 2026-08-23.** Pattern 7 now listens for a LIMS review and publishes
one document; it does not address a device. `cmd`, `response` and `ack` stay in the set with
no user.

The last two topics above are **provisional** — patterns 6 and 7 have no spec yet, and a topic
is settled when its spec is written. `downstream` and `utilities` still have no user.

**Pattern 6 moved from `upstream/br-201` to `qc/analyzers` on 2026-08-25.** The MET ONE now sits
in the analyzer path beside the Nova rather than beside the reactor. It is an *instrument that
runs analyses*, which is what the `qc/analyzers` cell holds, and putting it there keeps the
Ignition tag path and the MQTT topic identical — every other pattern has that property and a
split would be the first exception. The cost is the "environmental reading taken beside the
reactor" framing: the reactor association now lives in the payload and in pattern 7's join,
not in the address. Nothing on stage depends on the old address.

**Pattern 3's device id is `flex-01`, renamed from `novaflex-01` on 2026-08-25.** Topic and
Ignition UDT instance both. The service, the compose entry, the Event Stream name and the
reference model doc all keep `novaflex` — those name the *simulator and its source manual*, not
the instrument on the wire.

> **The rename is half-landed, and pattern 3 does not publish until it is finished.** The UDT
> *instance* is renamed; four other places still say `novaflex-01`:
>
> | Where | Effect |
> |---|---|
> | the instance's own `uns_path` parameter, in `tag-definition/.../qc/analyzers/udts.json` | named one thing, parametrized as another |
> | `script-python/opcua_event/code.py` → `SOURCE_ID` | envelope still says `"source": {"id": "novaflex-01"}` |
> | `event-streams/03_opcua/novaflex-result/config.json` → handler `topic` | still publishes to the old topic |
> | `compose/postgres/initdb/03-seed.sql` → `plant.equipment` | equipment id no longer matches the topic id, which the rule above requires |
>
> The tag-change script is bound to
> `[default]icc26/site1/qc/analyzers/novaflex-01/result/sample_time` — a path the renamed
> instance no longer creates, **so the trigger fires on nothing.** Finish it in one pass or
> revert it; half-done is the one state that fails silently.

**There is no `mes` area, deliberately.** An MES is a piece of software, not a place, and an
area slot filled with a system name is the same mistake as organising by ingestion mechanism —
one level higher up. A batch event happens in a *suite*, so it publishes under the cell that
produced it (`upstream/br-201/batch/event`) and names its source system in the payload. The
Postgres schema **is** a system-of-record namespace, so it is named after the system: the table
is `bes.batch_event`, and naming it after the writer is exactly what a schema is for.

> **Known remaining wart:** `qc/lims/sample-result` still puts a software system in the
> line-or-cell slot. Revisited in spec 04 (2026-08-19) and **kept**. The better address is
> under BR-201, naming the LIMS in the payload — the same rule `bes.batch_event` already
> follows. The topic is referenced by an ACL and by pattern 7's subscription, and the
> conference is four weeks out. The reversal made this *less* defensible, not more:
> `qc/lims/` was easier to justify when three patterns keyed off one topic. Now one does,
> and the calendar is what holds the address. Say so on stage.

**Every topic here is device-addressed.** There is currently no exception, which was not true
of the earlier vibration-gateway design and is worth knowing changed: that pattern had a
fleet-broadcast command topic and a flat response topic, both non-device-addressed, and both
went away with it.

**Pattern 1's conformance is enforced by an ACL, not by the protocol.** Its device has a
free-text topic box on its config page, so the only thing keeping sample data out of the `qc`
area is `sample-valve-01`'s publish grant of `icc26/site1/upstream/#` in
`compose/chariot/mqtt-users.json` — deliberately the *area* rather than the exact topic, so
the valve can be re-addressed to another cell on stage but cannot leave upstream. Pattern 2
needs no such grant to be well-behaved, because its topic is not its to choose.

**That contrast was the point of running both, and as of 2026-08-25 the ACL no longer shows
it.** `sample-valve-02` was widened to `spBv1.0/#` both ways after a commissioned group change
put its NDEATH will outside the old grant and Chariot refused the CONNECT. The *argument* still
holds — Sparkplug still fixes the topics, so the tight ACL was still free — but the file no
longer demonstrates it, and pattern 2's account is now looser than pattern 1's. See
[`compose/chariot/README.md`](../compose/chariot/README.md).

Equipment ids in `plant.equipment` (see `compose/postgres/initdb/03-seed.sql`) are the same
strings that appear in topics. Keep it that way.

### Patterns 4, 5 and 6 used to share one topic. Reversed 2026-08-19

The original design had all three carrying the same logical data — one LIMS sample result —
acquired three different ways and landing identically, so that disabling the webhook and enabling
CDC on stage changed nothing but `meta.mechanism`. **That is no longer the design**, and the
reasoning for the reversal matters more than the reversal:

**Nobody webhooks, tails and polls the same table in production. You pick one.** The convergence
demo was a pedagogical device, and a well-informed audience would recognise it as one.

**What was given up:** a very good set-piece, and the ability to prove the interchangeability
claim in one gesture.

**What was not given up, and is the part worth protecting:** the namespace still must not leak
the mechanism. A subscriber reading the topic list still cannot tell which pattern uses CDC.

### Sources as of 2026-08-23

A second re-source, after 2026-08-19's Odoo / Modbus MET ONE / vibration-AMS-DCS plan. Patterns
1–3 did not move. Pattern 4 stayed the LIMS webhook and gained a pass/fail disposition.

| # | Mechanism | Source now | What it publishes |
|---|---|---|---|
| 4 | `webhook` | LIMS review (already built) | `analyst` + `disposition` pass/fail |
| 5 | `cdc` | Ignition timer → `bes.batch_event` → Debezium | reactor operation cycle |
| 6 | `poll` | MET ONE HTTP API in `qc/analyzers`, Ignition poll → Event Stream | particle-count analysis |
| 7 | `aggregate` | MQTT listener on the LIMS review | sample chain: valve, Nova, batch, env |

Pattern 5 is an application we own. Say that on stage: the textbook CDC case is an app you
cannot modify; this engine is a stand-in for the **batch execution system** we would not patch,
and the point is still that the writer never publishes MQTT. Pattern 6 is a pull API, not
Modbus; vendor routes land when the docs are dropped in. Pattern 7 is a join of the others, not
a third copy of the LIMS row.

**Say BES, not MES.** `CIP → SIP → INOC → GROWTH → HARVEST` is an ISA-88 phase model, which is
batch execution specifically; MES is the wider L3 layer that also covers scheduling, genealogy
and dispatch, none of which the timer does. This room runs DeltaV Batch, PAS-X or Syncade — say
MES and they will expect work orders. **The schema follows the words: it is `bes.batch_event`,
renamed from `mes.` on 2026-08-23 before anything was built on it.** An earlier revision of this
section argued for keeping `mes.` to avoid churn; that argument lost, because pattern 5's verify
step puts the table on screen immediately after the talk track says "BES", and a schema prefix
that contradicts the sentence just spoken is the one place the churn would have been visible.
`02-schema.sql` and `04-cdc.sql` already carry the new name.

Consequences that are easy to miss:

- The `lims-bridge` ACL no longer enforces convergence. It still exists, and it still earns its
  place — see the cycle hazard in [`plans/04-lims-webhook.md`](plans/04-lims-webhook.md).
- **`lims.sample_result` is still only pattern 4's table.** Pattern 7 reads the MQTT review,
  not this table.
- **`bes.batch_event` has a consumer again.** Pattern 5 CDC-tails it. Drop `lims.sample_result`
  from `04-cdc.sql`'s publication when that spec is written; do not tail both.
- Pattern 4's message stays sample-shaped. `disposition` is a new field on that object, not a
  return to row-granular CDC.

### Cut on 2026-08-25

Three removals, all from the master-plan revision of that date. They are recorded here because
each one leaves references behind in older text, and the reason matters more than the deletion.

**Spec 08 — presentation, firehose and runbook — is cut.** There are no Perspective views for
this demo, no `meta.mechanism`-coloured firehose (neither the vendored-mqtt.js primary nor the
Engine-subscribed fallback), and no `docs/demo-runbook.md`. The scope was one greenfield UI
build plus a rehearsal document, four weeks out, competing with two unbuilt patterns. **The
demo surface is now the broker itself** — `mosquitto_sub` on `icc26/#` — plus the two device
config pages on 8085/8086 and the LIMS approval screen on 8000, all of which already exist and
are each somebody's real product screen rather than a dashboard about the demo.

What this costs, named rather than hidden: there is no single screen showing seven mechanisms
at once, so the "one backbone" claim is carried by a terminal and by the narration. The
pre-show trial checklist that lived in the runbook now lives in *Trial timers* below. Pattern
2's long-standing caveat — that Sparkplug carries no envelope and so cannot be coloured by
`meta.mechanism` — is **moot**, not resolved: there is no colouring.

**Countess is out of the demo.** `services/opcua-countess` still builds and runs, the
`cell_analyzer` OPC UA connection and UDT type are still in the repo, and
[`reference/countess-3fl-opcua-model.md`](reference/countess-3fl-opcua-model.md) is still the
designed-model reference. What is gone is its stage time: the `countess-01` UDT instance is
deleted, its MQTT publish is not to be finished, and pattern 3 is the Nova alone. The
two-analyzer contrast — "the model we would design versus the one a vendor ships" — survives as
a sentence, not a second running instrument.

**Pattern 8's numbering is retired with it.** Seven patterns, seven specs, `01…07`. Do not
renumber the remaining seven to close the gap; every doc, topic comment and talk track keys off
the current numbers.

### Pattern 7 needs a store, not just a subscription

New with the 2026-08-25 revision, and it is the one item that grew rather than shrank.

Pattern 7 fires on the pattern-4 LIMS review and then has to answer four questions **about the
past**: when the valve opened, when the Nova ran, what phase was running at the sample-open
instant, and what the nearest MET ONE reading said. A subscriber holding only the message that
woke it up cannot answer any of them. So **patterns 1, 3, 5 and 6 need their events persisted,
not just published** — today only pattern 5's events land in a table, and that table is the CDC
*source*, not a history the aggregator can query.

This is unspecified work, and it is the largest thing standing between pattern 7 and a build.
The shape is not decided; the two candidates are Ignition tag history on the bound tags, and an
`events` table in `icc26` written by the same Event Streams that publish. Whichever it is, it
must exist before 07 can be specified — and it does not change any envelope or topic, because a
store is a consumer, not a new mechanism.

### Derived flags travel with the fact that produced them

Added 2026-08-23 alongside the demo through line, which needs three booleans to reach its
payoff. **Each one is computed by the component that owns the fact and put on the wire — never
re-derived by the consumer.**

| Field | On | Set by | Meaning |
|---|---|---|---|
| `values.qualified_window` | pattern 5 `batch/event` | the Ignition timer, at insert time, into `bes.batch_event.payload` | the protocol qualifies sampling for `GROWTH` only |
| `values.status` | pattern 6 `…/result` | the MET ONE simulator, or the poll script at ingest | `normal` or `excursion` against a configured cleanroom limit |
| `values.outside_qualified_window`, `values.environmental_excursion` | pattern 7 aggregate | the aggregation script, from the two above | the composite event's two claims |

**Why the flag and not the raw value.** Pattern 5 is the only component that knows which phase
the batch protocol qualifies for sampling; pattern 6 is the only one that knows the cleanroom
grade. If pattern 7 tested `phase = 'GROWTH'` or compared counts to a limit itself, the
aggregation script would hold a second copy of the batch protocol and of the cleanroom spec, and
the two copies would drift. Pattern 7 derives only from flags it was handed.

`qualified_window` goes into `bes.batch_event.payload` (jsonb, already present — **no schema
change**) rather than being computed in the `cdc-sink` WebDev endpoint, so that it is in the WAL
Debezium tails. A flag added after the tail is a flag the CDC demo did not actually observe.

All three are `values` fields. None appears in a topic or in `meta`: the namespace must not leak
the mechanism, and it must not leak the verdict either.

### The sample id, and pattern 1 mints it

The rule, settled here so that no spec re-litigates it: **the valve mints the sample id, because
the sample begins when material leaves the reactor.** Everything downstream carries it unchanged.
The Nova sim already reads `SampleInformation/SampleID` from writable OPC UA nodes, so Ignition
writes the valve's id into the analyzer before the run instead of the analyzer inventing one.

**This is not done.** `services/sim-valve-mqtt/valve.py` produces `S-YYYYMMDD-NNNN` locally
while the Nova produces `S-NNNNN`, in two containers that never talk, so the valve-open →
analysis-complete leg of the join does not correlate today. It is a gap, not a design, and it is
pattern 1's open item 1.

**Which field carries it is per-pattern, and that is deliberate as of 2026-08-23.** Patterns 3
and 4 stamp `meta.correlation_id` and keep it — it is built, verified and carried through a
review workflow and a Postgres outbox. **Pattern 1 does not have the field**; its id travels as
`values.sample_id`, inside the record it belongs to, where an Ignition tag binding or a column
mapping reaches it without crossing into a sibling folder. Pattern 7 therefore reads two shapes,
and can, because it has no spec yet to be broken by it.

The cost is named rather than hidden: a consumer joining across all seven patterns no longer has
one field in one place. If that becomes painful, the fix is additive — adding `meta.correlation_id`
to pattern 1 later breaks no consumer — so nothing here is load-bearing on the choice.

This matters more than it looks. Both of pattern 7's derived flags are evaluated **at the
sample-open instant** — which phase was running when the valve opened, which particle-count
analysis was nearest. Without a shared id there is no sample-open instant to evaluate them
against, and the composite event cannot be built at all.

### Payload envelope

Every non-Sparkplug payload **that Ignition publishes** — patterns 3, 4, 5, 6 and 7:

```json
{
  "ts": "2026-08-07T14:03:22.145Z",
  "seq": 1041,
  "source": { "id": "flex-01", "type": "analyzer" },
  "meta": { "mechanism": "cdc", "ingest_ts": "…", "correlation_id": "…" },
  "values": { }
}
```

`meta.mechanism` ∈ `opcua-event | webhook | cdc | poll | aggregate`. The two field-device
patterns are absent by construction: pattern 2 is Sparkplug and carries no envelope, and
pattern 1 carries no `meta`. **Neither of the two patterns that most need a mechanism tag can
be given one** — which is the honest version of what this field buys.

**Pattern 1 does not use this envelope at all** (2026-08-25). Its documents are `ts` and
`values` and nothing else — no `seq`, no `source`, no `meta`. That is not an oversight to be
tidied up later: pattern 1 is a device somebody *bought*, and a bought device ships whatever
its firmware author decided, not your site's metadata conventions. Everything a consumer knows
about where a pattern-1 message came from, it knows from the topic string typed into a text
box. See [`plans/01-native-mqtt.md § Payload contracts`](plans/01-native-mqtt.md).

The envelope is therefore the house standard for **the patterns we write**, which is exactly
the set that can be held to one. `meta.correlation_id` is **optional** within it — patterns 3,
4 and 7 carry it, patterns 5 and 6 have nothing to correlate to.

This field is how a mechanism stays legible without the namespace leaking it. It used to have a
second job — the Perspective firehose coloured by it — and that view is **cut as of
2026-08-25**. What remains is the wire itself: `mosquitto_sub -t 'icc26/#' -v` shows every
mechanism side by side, and `meta.mechanism` is the field that tells them apart in the output
**for the five patterns that carry it**. The verification that matters is unchanged, and is now
the only one: a subscriber reading the topic list cannot tell which pattern used CDC.

### Two things that become talk content

**A Last Will belongs to whoever owns the session.** It is registered in the MQTT CONNECT
packet, so only the client that opened the connection can set one — every publish API is "send
this message now" on a session that already exists. Both valves own their own sessions, so
both get a will, and comparing them is the cleanest version of the Sparkplug argument:

**Sparkplug does not give you a death mechanism, it standardises the one MQTT already had.**
NDEATH *is* an LWT, and it inherits every one of the LWT's constraints. Its payload is frozen
at CONNECT, so a Sparkplug death certificate cannot carry the time of death either — the
consumer stamps that. DDEATH is not a will at all; it is an ordinary publish, so one connection
still buys exactly one will in Sparkplug too. What changed is the *agreement*: a spec-mandated
topic, a `bdSeq` payload that identifies the session, and a rule every consumer applies.
Hand-rolled — pattern 1 publishes a retained JSON document with `state: "offline"` on a
`status` topic it chose — you invent the topic, the payload and the semantics, you have to tell
every consumer separately, and the next vendor invents them differently. The device pairs it
with a retained `online` published on connect, which is the [conventional
companion](https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/) and
still only a convention.

Pattern 1's will has one further flaw worth showing live: it is only useful to a late
subscriber if the retained flag happens to be ticked, and that flag is a checkbox on the
device's config page covering *all* its messages. Tick it off to spare the telemetry topic and
you silently disarm the death certificate for everyone not already listening.

**Chariot is MQTT 3.1.1**, so there are no MQTT 5 response-topic or correlation-data
properties. Nothing in the demo currently needs them: every pattern is one-way. Patterns 1 and
2 are publish-only field devices, and patterns 3–7 are Ignition publishing outward. If a
request/response pattern is ever added, this is the constraint it will run into first, and
`meta.correlation_id` is the field already reserved in the envelope for it.

**`meta.correlation_id` has a working first user in pattern 4.** The analyzer envelope stamps
`sample_id` into it; the LIMS carries it through review and the webhook republishes it under
`mechanism=webhook`. Pattern 7 reuses the same id on the aggregate, so one sample is
traceable from Nova through the LIMS review to the sample-chain document. That still does
not need MQTT 5 response-topic properties — pattern 7 is a subscriber, not a requester.

### MQTT Engine has two ingest surfaces, and they produce different things

Both are enabled at once, both feed the same `MQTT Engine` tag provider, and the contrast
between them is most of patterns 1 and 2. Verified 2026-08-17.

| | **Custom Namespace** (pattern 1) | **Sparkplug B default namespace** (pattern 2) |
|---|---|---|
| Config | one subscription per device, hand-written | `spBv1.0/#`, shipped enabled |
| Resource | `com.cirruslink.mqtt.engine.gateway/custom-namespace/<name>/` | `…/default-namespace/Sparkplug B/` |
| Tag tree | mirrors the **JSON document**, under `MQTT Engine/<topic path>/` | mirrors the **device**, under `MQTT Engine/Edge Nodes/<group>/<node>/<device>/` |
| Datatypes | inferred per value; `numbersAsFloats` makes every counter a Float8; timestamps are String | declared in DBIRTH — Int64, Float, Boolean, String, DateTime |
| Engineering units | nowhere | on the wire, applied to the tag |
| A null field | **no tag is created at all** | a typed null: the tag exists, correctly typed, with no value |
| Adding a device | edit the subscription | nothing |

**Neither tree is configuration, and both are gitignored** —
`tag-definition/MQTT Engine/Edge Nodes/` and `tag-definition/MQTT Engine/icc26/`. The modules
rebuild them at runtime from whatever traffic that machine happened to see, so committing them
means every gateway churns another machine's leftovers into every diff. The anchored paths
leave the static `MQTT Engine/Engine Info/Edge Nodes/` folder tracked, as it should be.

A caveat that costs an hour if you meet it cold: **a MANAGED provider's tag tree is only
partially on disk.** Ignition persists a tag definition only where the config is non-default, so
the Sparkplug device's nineteen metrics (twenty once `Sample/LastCycleResult` lands) write four
`tags.json` entries — the ones carrying
engineering units — and the other fifteen leave empty folders. Counting files gives the wrong
answer. The authoritative read is
`GET /data/api/v1/tags/export?provider=MQTT Engine&type=json&recursive=true`, which needs an
API key.

---

## Broker: why Chariot and not Distributor

The talk's thesis is "seven mechanisms, one event backbone." If the broker lives inside
Ignition, the architecture stops matching the slide — Ignition becomes both the backbone and
half the clients, and you cannot restart it on stage without taking the demo down.

Chariot is also better suited to a committed repo: fully env-var configured, `MQTT_USERS`
reads a JSON file so ACLs are a diffable artifact, and port 8090 gives MQTT-over-WebSocket.
That last one was chosen for a browser-based firehose; **the firehose is cut** (see *Cut on
2026-08-25*) and 8090 now has no user. It stays exposed — it costs nothing and it is the port
anyone would reach for if a browser client is ever wanted again.

**The cost is a second, independent 2-hour trial timer.** MQTT Distributor is therefore still
in `modules.manifest.json` as break-glass: if Chariot's trial bites mid-talk, enable the
Distributor module in the gateway and repoint Engine/Transmission at `localhost:1883`.
Distributor is a *module*, not a container, so there is nothing to switch in compose — which
is also why this stack has no compose profiles at all.

A Chariot demo key from Cirrus Link removes the whole problem. Worth requesting before the
conference.

### Chariot will not open its MQTT listener without a trial

This one costs you the whole demo if you hit it cold. Chariot 3.0.1 serves its **web UI on
8081 while port 1883 refuses every connection**, which looks exactly like a broken network or
a wrong port. It is neither. The log line is:

```
WARN  c.c.chariot.server.impl.Server - Not starting Chariot MQTT Server, license not active
```

Unlike Ignition, Chariot's trial does **not** start automatically in the container, and
`LICENSE_TYPE` only accepts `online` or `floating` — there is no trial value.

**Starting it is a manual step:** the web UI at `:8081` → **License** → start trial, or
install a Cirrus Link demo key on the same page. `tasks.py` does not automate it. There is an
undocumented `POST /license?action=start-trial-timer` in the UI bundle, and `up` used to call
it, but licensing is not something to drive from a script on a stage machine — an undocumented
route that changes under you takes the demo with it. So `up`, `trial` and `health` only *read*
license state and print the URL to go press the button.

Reads authenticate with a bearer token from `POST /login` (Basic auth is rejected), so the
calls run via `docker exec` against the container's own loopback using the curl it ships.

Two practical notes:

- **Chariot seeds its admin user asynchronously**, well after the web port starts answering.
  Poll the API, not the port — `wait_for_chariot()` does.
- The `Accept: application/json;api-version=1.0` header contains a `;`. Any shell in the call
  path will eat it, and the header arrives as a bare `Accept: application/json`; Chariot then
  rejects the request with *"'api-version' not specified"*. `_chariot_curl()` hands the
  argument list to `docker exec` directly, with no shell anywhere in between, which is why the
  problem no longer exists. If you extend it, do not reintroduce a `sh -c`.

`tasks.py health` checks the **listener**, not the web port, because the web port answering
proves nothing.

---

## Seeding: why the first boot is different

**Ignition 8.3 seeds `data/` from the image on first launch of an empty volume.** If empty
host directories are bind-mounted over `data/config` and `data/projects` at that moment, the
seeding is blocked and the gateway comes up broken.

So the first run uses `docker-compose.seed.yml`, which boots the gateway on the `ign-data`
named volume with **no config bind mounts**. Once it reports RUNNING, `tasks.py` copies out of
the seed container and stops it. From then on `docker-compose.yml` mounts `./ignition/` back in
over the same, now-initialized volume.

```bash
git clone <repo> && cd icc-2026
# .modl files into compose/ignition/modules/
python tasks.py init     # .env
python tasks.py seed     # once per machine; pauses for browser commissioning
python tasks.py up
```

### Two seeds, one command

Once the config is committed, "has this machine been seeded?" and "is `ignition/config`
populated?" stop being the same question — a fresh clone has populated config and no volume.
Deciding from config alone breaks both ways: a teammate's `up` proceeds against a volume that
was never initialized, or `seed` overwrites their committed config with the vanilla baseline.

So `seed` reads both axes — does `<project>_ign-data` exist (`docker volume inspect`), and is
`ignition/config` populated:

| Volume | `ignition/config` | What happens |
|---|---|---|
| absent | empty | **Full seed.** The original case: export the whole baseline to `./ignition/` |
| absent | populated | **Clone seed.** Export *only* the gitignored identity paths; committed config is never touched |
| exists | populated | Refuses — already seeded, use `nuke` to rebuild |
| exists | empty | Refuses — half-initialized, a previous export did not finish; use `nuke` |

The clone seed copies three paths out of the seed container, all of them gitignored and all of
them regenerated per machine: `config/local/`, `config/resources/local/`, and
`config/ignition/tags/valueStore.idb`. A missing source warns and continues rather than failing
the seed. The success criterion for the clone path is that `git status` is **clean** afterwards.

`up` gates on the volume for the same reason, and both `seed` and `up` hard-fail (non-zero) if
the Cirrus modules are missing or the wrong version. `init` only warns, because it is what you
run *before* fetching them.

You need to repeat `seed` only after `tasks.py nuke`. Note that `nuke` destroys volumes but
never touches your committed `ignition/config` and `ignition/projects` — which is precisely why
the post-`nuke` re-seed takes the clone path.

### Keeping gateway and repo in sync

Bidirectional, and the second direction is the one people forget:

- **Designer / Gateway UI edit** → gateway writes files → shows up in `git status`.
- **`git pull` or on-disk edit** → gateway does *not* notice → **`python tasks.py scan`**.

The gateway reads `data/config` at startup and does **not** watch it. This was tested directly:
editing `systemName` on disk on a running gateway produced no log activity and no effect until
the files were applied. Any workflow that assumes a file watcher is wrong.

**`python tasks.py scan` is the default apply.** It POSTs to `/data/api/v1/scan/config` and
`/data/api/v1/scan/projects`. 8.3 guards those routes with an API key
(Platform > Security > API Keys) in an `X-Ignition-API-Token` header, not the admin password,
and the header value is the complete `name:secret` token shown once at creation. `tasks.py`
uses `IGNITION_API_TOKEN_HTTPS` and validates the gateway certificate; it deliberately has no
HTTP credential fallback. API keys are machine-local, so each clone must create its own key
with a security level granted Gateway read/write access.

Fall back to `python tasks.py restart ignition` only when scan is unavailable (no API key yet)
or when the change is a **container-consumed `.env` secret** — those are process environment
and scan cannot pick them up.

The first key remains a manual bootstrap step. `/data/api/v1/api-token/generate` and the API
token resource routes require an already authenticated write-capable actor, while these routes
do not accept the gateway admin password as HTTP Basic auth. Automating key creation would
therefore require scripting the browser's session/CSRF login flow or shipping a shared
credential, neither of which is appropriate for this demo. After seeding, each user creates a
secure-channel key in the Gateway UI and copies its complete `name:secret` value into the
gitignored `.env`. Once that key exists, creating additional keys through the API is possible.

**Two API-key traps, both met on 2026-08-17, both of which look like "no token":**

- The variable was once called `IGNITION_API_TOKEN`. An older `.env` still carrying that name
  reads as no token at all, with no error naming the mismatch.
- A key is bound to the gateway that minted it, so a key copied from another checkout returns
  **401 — indistinguishable from having no key.** Each clone mints its own.

If a pulled change "didn't take", `python tasks.py scan` before debugging anything else.

### Where each service's config actually comes from

Established by direct inspection. This is the map to reason from whenever something is "set" but
is not taking effect.

**Ignition — five sources:**

1. **Git, via bind mounts.** `./ignition/config` → `data/config`, `./ignition/projects` →
   `data/projects`. Tag definitions, the `icc-2026` project, `systemName`, and the four
   `Embedded`-ciphertext MQTT/OPC config files.
2. **The image, via first-launch volume seeding.** Everything else under `data/`, including
   `data/var/ignition/modl/` (the Cirrus modules) and `data/modules.json`.
3. **Generated locally by `seed`.** `config/local/`, `config/resources/local/`,
   `config/ignition/tags/valueStore.idb` — gitignored, machine-specific, regenerated cleanly
   from nothing.
4. **`.env` and compose environment.** Admin credentials, the per-machine HTTPS API token,
   host ports, edition, TZ, and the pinned `hostname`.
5. **A browser, once per fresh data volume.** Module certificate fingerprints and
   `licenseAgreementHash`, written into `data/modules.json` *inside* the volume. Cannot be
   pre-seeded — tested, see Commissioning below.

The precedence rule that falls out: **a bind mount beats the image, and the volume beats
nothing.** Anything under `data/` that is not bind-mounted is frozen at whatever the image held
when the volume was first created. That is why the stale-image trap below was invisible until
somebody created a fresh volume.

**Chariot — nothing is config-as-code:**

1. The `chariot-config` named volume — its persistent store.
2. `compose/chariot/mqtt-users.json`, bind-mounted read-only, seeding the ACL'd accounts —
   **on first run only**, per `MQTT_USERS`. Editing it does nothing to a Chariot that already
   has a user store; that needs a `nuke` or hand-editing in the UI.
3. `SERVER_CONFIG`, inline in `docker-compose.yml` — ports and `allowAnonymous`. Read at
   **every container start**, so it is the one Chariot setting a restart can change.
4. `ADMIN_PASSWORD` from `.env`.
5. The trial — started by hand in the web UI, per volume. Runtime state, not config.

A clone reproduces Chariot exactly, because everything lives in compose — but only against a
fresh volume.

---

## Module path — resolved

The plan flagged this as ambiguous across sources. It is now settled empirically.

**The answer is `data/var/ignition/modl`**, read straight out of the 8.3 entrypoint:

```
-Dignition.gateway.externalModulesFolder=data/var/ignition/modl
```

The other two candidates are wrong for 8.3. `/modules` and `GATEWAY_MODULE_RELINK` are
8.1-era — the 8.3 entrypoint contains no handling for either, and modules placed in
`/modules` are silently ignored. `data/local/modl` appears in the 8.3 docs but is not what
the image actually sets.

**Modules are baked into a derived image** (`compose/ignition/Dockerfile`), not bind-mounted.
Three findings forced this, each verified by breaking it:

1. **Modules are only discovered on the first launch of a fresh data volume.** Dropping a
   `.modl` in later and restarting does nothing.
2. **You cannot bind-mount into the data volume.** Docker seeds a named volume from the image
   only when the volume is *empty*. Mounting anything at `data/var/ignition/modl` makes it
   non-empty before seeding, so `data/config` and `data/projects` are never created and the
   gateway comes up with no configuration whatsoever. This is the same failure mode as
   bind-mounting `config`/`projects` on first launch, one level deeper.
3. **Do not create `data_clean/`.** The entrypoint treats its existence as "the payload lives
   here", copies only its contents into `data/`, then deletes it — silently replacing the
   real configuration with whatever you staged.

Consequence: adding or upgrading a module needs `tasks.py nuke` then `seed`. A rebuild alone
is not enough, because an existing volume is never re-seeded.

**All three Cirrus module versions must match exactly** (5.0.4). Cirrus documents
class-loading instability and gateway crashes otherwise. Note the 8.1-era 4.x downloads have
**identical filenames** to the 5.x ones, so `tasks.py verify-modules` reads `<version>` out
of each `.modl`'s `module.xml` rather than trusting the filename.

### The stale-image trap

This one shipped a broken repo and stayed invisible for two days. It is the most expensive bug
found in step 1, so it gets stated in full.

`compose/ignition/modules/` reaches the gateway **only** by being baked into the
`icc26/ignition:8.3.8` image, and **compose builds that image only when the tag is missing.**
Updating a `.modl` on disk therefore changes nothing: the tag already exists, `up` reuses it, and
the newer file is never seen by any container. A clone came up running Cirrus **4.0.8** Engine
and Transmission out of a stale image while the correct 5.0.4 files sat on disk and
`verify-modules` reported all three green:

```
W [ModuleInstance] Module "MQTT Engine" requires Ignition 8.0.16 (b0) and is not compatible with Ignition 8.3.8
```

Two things follow, and the second is the general lesson:

1. **`verify-modules` validates host files the gateway may never load.** Any check that does not
   compare against what is actually *in the image* is measuring the wrong thing.
2. **Upgrading a module needs the image tag deleted too**, not just `nuke` + `seed`:

```powershell
docker image rm icc26/ignition:8.3.8
python tasks.py nuke
python tasks.py seed
```

Landing the module sha256s in `modules.manifest.json` and hashing the `.modl` files *inside the
image* — or forcing `docker compose build` on every `seed` — is what makes a stale image fail
loudly instead of passing green.

**Fixed 2026-08-17.** `tasks.py` now passes `--build` on both `up` and `seed`, and the three
manifest `sha256` fields are filled in. The layer cache keeps an unchanged build to a couple of
seconds. Three things to keep straight about what that buys:

- **`up --build` fixes source edits reaching a container.** This is the everyday case, and it
  was silently broken for every `build:` service, not just the gateway — a one-line edit to
  `services/sim-valve-mqtt/app.py` never reached the running valve. Verified by making exactly
  that edit and watching it appear in the container's log.
- **`seed --build` is what fixes module upgrades**, and only there. The `.modl` files live at
  `data/var/ignition/modl`, *inside* the `ign-data` volume, and Docker seeds a volume from the
  image only while the volume is empty. A module version can therefore only change during a
  seed, so a stale tag at that moment is baked in permanently. `docker image rm` + `nuke` +
  `seed` is still the upgrade path.
- **The manifest hashes still only validate host files**, which is the wrong thing to measure.
  They catch a corrupt or swapped download, nothing more.

## Commissioning

The presence of **any** third-party module makes the gateway halt in commissioning on first
launch, waiting for a human to accept the module certificate in the browser.

The trap: it answers `/StatusPing` with `{"state":"RUNNING","details":"COMMISSIONING"}`. A
health check that greps for `RUNNING` calls that healthy while the gateway is serving only
the setup wizard and has created neither `data/config` content nor `data/projects`. Both
`wait_for_gateway()` and `tasks.py health` distinguish the two states for exactly this reason.

Pre-seeding `data/modules.json` with correct certificate fingerprints **does not** bypass it.
That was built and tested — including validating the fingerprint derivation by reproducing
Inductive Automation's own `88338069eb9c3f2d46a4baf701e4fa71bf073293` from their `.modl` —
and the gateway still demanded commissioning. The code was removed rather than kept as dead
complexity. A second finding from that experiment is worth remembering: **`modules.json` is
authoritative, not additive.** If the file exists, the gateway does not merge its built-ins
in, so a partial file silently disables every built-in module.

So commissioning stays a one-time manual step per fresh volume. `tasks.py seed` detects it,
prints the URL, and waits.

## Gateway HTTPS

HTTPS setup is part of `tasks.py seed`, immediately after commissioning and before the
machine-local identity is exported. The seed container uses Ignition's own `keytool` to create
`config/local/ignition/webserver/keystore/ssl.pfx`, reloads the keystore, and exports the public
certificate to the gitignored `ignition/certificates/icc26-ignition.crt`. On Windows it also
adds that public certificate to the current user's Trusted Root store. On macOS it adds the
certificate as a trusted root in the current user's login keychain (a locked keychain may
prompt for its password). Neither path exports the private key or requires machine-wide trust.
`tasks.py enable-ssl` repeats the same process idempotently for an existing gateway.

The generated certificate always covers `localhost`, `127.0.0.1`, and the host's current
hostname/FQDN. Extra stable conference-network names and addresses can be set before seeding
with `IGNITION_SSL_DNS_NAMES` and `IGNITION_SSL_IP_ADDRESSES`. Trusting it on the gateway host
does not make it trusted on audience devices: those devices must import the exported public
certificate too, and the URL they use must match one of its SANs.

Both Compose files bind Ignition's HTTP port to `127.0.0.1` only. It remains available for
local commissioning and maintenance, but network clients can reach only the HTTPS port.

---

## Postgres

`wal_level=logical` is set in the compose `command:` override rather than left to pattern 5.
It is a server start parameter, so changing it later means dropping the data volume — cheap
to set on day one, expensive to discover late.

`compose/postgres/initdb/` runs **only** on an empty volume. Editing those files against an
existing volume changes nothing; you need `tasks.py nuke` first.

Three roles, deliberately separate:

| Role | Purpose |
|---|---|
| `ignition` | Gateway's JDBC target — historian, audit log |
| `icc26` | Demo data (`lims`, `bes`, `plant` schemas) |
| `cdc` | Debezium's login, has `REPLICATION` |

`cdc` being distinct from the application user is part of pattern 5's point: CDC is an
out-of-band observer the application knows nothing about.

### The JDBC datasource, and the look-alike that will waste your afternoon

Pattern 5's timer writes `bes.batch_event` through an Ignition datasource named **`ICC26`** →
`jdbc:postgresql://postgres:5432/icc26`, user `icc26`. **It does not exist yet** — create it
UI-first, then commit what `git status` reveals under `ignition/database-connection/`.

**There is already a `database-connection/pg_db` in the repo, and it is not that.** It points at
the **`postgres` database as user `ignition`** — wrong database, wrong user. It will pass a
glance in the datasource dropdown and then write nowhere useful, and nothing about the failure
says "you picked the wrong connection". Create `ICC26` properly, and decide whether `pg_db` is
deleted rather than left where somebody can select it by mistake.

`lims.sample_result` and `bes.batch_event` are set to `REPLICA IDENTITY FULL` so Debezium
receives complete row pre-images on UPDATE and DELETE. It costs WAL volume — fine for two
demo tables, not something to enable blindly across a real database. Pattern 5 tails
**`bes.batch_event` only**; drop `lims.sample_result` from the publication when that spec
is written. `lims.webhook_delivery` is pattern 4's outbox and is not in the publication.

---

## Host platforms

### The task runner

`tasks.py` is the one implementation, on every platform. The only other entry point is
`Makefile`, a two-line forwarder for Linux/macOS muscle memory. It contains no logic.

There was a `tasks.cmd` Windows forwarder too, so you could type `tasks up`. It was deleted:
`python tasks.py up` works everywhere, and one documented invocation beats three.

It was not always that way. The runner started as `tasks.ps1` with a hand-written `Makefile`
mirroring it, and the mirror drifted — by the end of step 1 it had lost the `.modl` version
check, the COMMISSIONING detection, and the wait for Chariot's async admin seeding. That is to
say: the Linux path faithfully reproduced every bug this document exists to record. Two
implementations of the same knowledge is one too many. If you add a task, it goes in
`tasks.py`; if you find yourself adding logic to `Makefile`, stop.

Python rather than PowerShell 7 because the pattern services are already Python
(`asyncua`, `paho-mqtt`, FastAPI), so it is not a new dependency. Standard library only —
`zipfile` reads the `.modl` version, `urllib` does the HTTP, `hashlib` the checksums. No pip,
no venv.

### Bind mounts

Ignition runs as UID 2003 and must *write* to `data/config`.

**Linux:** bind mounts pass host UIDs straight through, so set `IGNITION_UID` / `IGNITION_GID`
in `.env` to your own `id -u` / `id -g`, or the gateway cannot write back and config-as-code
is read-only in practice.

**Windows and macOS:** Docker Desktop's translation layer fakes ownership, so the defaults
work — but it is slow. This mostly costs gateway write-back latency, not correctness.

For a machine you are presenting *from* on Windows, **clone into WSL2**
(`\\wsl$\Ubuntu\home\...`) and run the stack from there. Fast, correct ownership, no
surprises.

### Two checkouts cannot run at the same time

`container_name` is pinned in `docker-compose.yml` (`icc26-ignition`, `icc26-chariot`,
`icc26-postgres`), the network is pinned (`name: icc26`), and host ports come from `.env`. A
second stack collides on all three regardless of `COMPOSE_PROJECT_NAME`. Bring one down first.

**Stopped is not enough — the containers must be removed.** A container name is claimed by an
*existing* container, running or not, so a four-day-old `Exited (0)` gateway still blocks the
other checkout with `Conflict. The container name "/icc26-ignition" is already in use`. Use
`python tasks.py down`, which removes containers and the network while leaving volumes alone.
Compose also warns `a network with name icc26 exists but was not created for project ...` when
the other project created it first; harmless, and it clears once the owning project is down.

Set `COMPOSE_PROJECT_NAME` anyway in a scratch clone: it separates **volumes only**, which is
exactly what stops a `nuke` over there from reaching your gateway state over here.

**The scratch clone is downstream of main, always.** It holds no unique work, so never
`git commit` from inside it — sync it the other way:

```bash
git -C .../icc26-clone fetch C:/Users/matt/repos/icc-2026 main
git -C .../icc26-clone reset --hard FETCH_HEAD
```

Its `.env` is gitignored and survives the reset, which is the point of doing it that way.

---

## Things that look broken and are not

**`health` green does not mean MQTT works.** It checks Chariot's *listener*, not Ignition's
*client connection to it*. MQTT Engine once looped on a bad credential every 3 seconds for a
whole day behind a green `health`. When MQTT is the thing you care about, check the gateway logs
or Chariot's client list.

**Chariot validates credentials that ARE supplied, even with `allowAnonymous: true`.** Anonymous
access only helps clients that supply *no* credentials. A client with a username and a wrong (or
missing) password is rejected, not waved through:

```
CONNECT - Bad username and/or password. username true:admin, password false:*****
```

Good news for testing — anonymous cannot paper over a genuinely broken credential — but it means
`username: admin` with no password block is a *failure*, not a fallback. Set `username` to `""`
to connect anonymously on purpose.

**Transmission logs `Failed to subscribe to TARGET elements` immediately after connecting.**
Unexplained since 2026-08-17. Possibly the `ign-transmission` ACL, possibly transmitter config.
It connects and it publishes, so it may block nothing at all — but it is the first thing to
re-read if a Transmission-side publish ever goes missing, and it is worth an hour before the
pattern 5/6 event-stream work rather than during it.

`allowAnonymous` is currently `true` for the initial rollout, deliberately and temporarily. The
ACL'd accounts in `mqtt-users.json` are still seeded and still work. **Before the talk:** set it
back to `false`, restart, and confirm every client still connects with its own credential —
**start with MQTT Engine**, which is the one currently riding on anonymous: its server config
(`com.cirruslink.mqtt.engine.gateway/server/Chariot SCADA/config.json`) has `"username": ""`,
so turning `allowAnonymous` off without setting the `ign-engine` credential first breaks Engine,
and with it both patterns 1 and 2. Chariot's client list shows it connected as `username: None`
beside a properly authenticated `ign-transmission`. `compose/chariot/README.md` carries the
reminder; this is the detail behind it.

### Environment facts worth not rediscovering

- Chariot's version lives at `/Chariot/version.properties` (3.0.1). There is no image label.
- `curl` exists in both images. Chariot also has `wget` and `nc`.
- The Ignition image ships a **JRE, not a JDK** — no `javac`, no `jshell`. `java Foo.java` fails
  with *"Module jdk.compiler not in boot Layer"*.
- `ignition-secrets-tool.sh` only manages root/KEK keys. It cannot encrypt or decrypt a value.
- `docker exec <ctr> test -e <path>` gives **false negatives** — there is no `test` binary in
  these images. Use `sh -c '[ -e ... ]'`.
- Git Bash mangles container paths in `docker exec` (`/Chariot/...` becomes
  `C:/Program Files/Git/Chariot/...`). Prefix `MSYS_NO_PATHCONV=1` and use `//Chariot/...`, or
  just use PowerShell.

### Committed secrets are `Embedded`, on purpose

The four Cirrus/OPC config files hold `"type": "Embedded"` JWE ciphertext, committed. Converting
them to a Secret Provider was planned and then cut, on this basis: **this gateway has no
encryption key files at all** — no `data/config/ignition/keys`, no `root.json`, no `kek.json`.
Reading `SystemEncryptionServiceFactory`, that is what happens when `IGNITION_ROOT_KEY_PASSWORD`
is unset: the gateway falls back to `DefaultSystemEncryptionService`, whose key is built into the
jar rather than generated per machine. So committed ciphertext should decrypt on any 8.3.8
gateway that also has no root key password.

Two caveats. Ignition 8.3.8 ships only `internal`, `file` and `remote` provider types — **there
is no environment-variable Secret Provider**, which is what the original plan assumed. And the
portability claim above was inferred from bytecode; it is now **proven end-to-end (2026-08-17)**.
A gateway seeded from an empty volume, on a machine that had never held this gateway's identity,
connected MQTT Transmission to Chariot as `ign-transmission` — confirmed in Chariot's own client
list as well as the gateway log, with zero `Unable to decrypt ciphertext` lines. No Secret
Provider is needed. Demo-grade committed credentials are an
accepted trade here: portability is the goal, not secrecy.

One connection is worth knowing about separately: `opc-connection/Ignition OPC UA Server/config.json`
(the loopback) holds two `Embedded` secrets, one of them paired with a keystore in gitignored
`local/`. Fine as-is, and untouched by the portability proof above — but it is the one place to
look first if the loopback OPC connection faults on a fresh clone. Pattern 3 depends on OPC UA.

---

## Working rules

Not architecture, but they belong in the reference rather than in a status note, because getting
one wrong costs a dirty commit or a confusing hour.

- **Commit only what you meant to change.** Every gateway write stamps `lastModification*` into
  neighbouring `resource.json` files. `git add` your files, then `git restore .` for the rest.
- **Never commit** `ignition/config/local/`, `ignition/config/resources/local/`,
  `valueStore.idb`, `.modl` files, or `.env`.
- **Unknown gateway schemas: UI first, read `git status`, then commit.** Known formats — tags,
  project scripts, WebDev, Perspective views — can be authored as files directly. WebDev python
  resources need `"resource-type": "python-resource"` in `config.json`; any other discriminator
  mounts the URL and then 500s.
- **On-disk config or project changes: `python tasks.py scan`**, not a container restart.
  Restart only when scan is unavailable (no API key yet) or the change is a container-consumed
  `.env` secret — see *Keeping gateway and repo in sync*.
- **`icc26_ign-data` is not precious.** It was once the only place a working 5.0.4 Engine and
  Transmission existed; the image carries them now, so the main checkout can be nuked and
  reseeded like any other. It still costs one commissioning wizard and one API key.
- **Before the talk:** set `allowAnonymous` back to `false`, starting with MQTT Engine — see
  *Things that look broken and are not*.

---

## Trial timers

Two independent 2-hour clocks: Ignition's and Chariot's.

**Both are reset by hand, in each product's own web UI.** `tasks.py` reads them and never
writes them:

- **Ignition** — gateway UI → Config → Licensing. `GET /data/api/v1/trial` is unauthenticated;
  `python tasks.py trial` reads it over verified HTTPS. The matching `POST` resets it but needs
  an 8.3 write-capable API key, and the reset **only succeeds once the trial has already
  expired** (an active trial returns 403). So the procedure is *let it expire, then reset* —
  you cannot top it up before walking on stage, scripted or not.
- **Chariot** — web UI at `:8081` → License → start trial. Does not auto-start at all; see
  the broker section above.

Neither can be done on demand from the command line, so **both are a manual pre-show step** —
start Chariot's trial and let Ignition's expire-then-reset cycle land before you walk on. There
is no runbook document to hold that checklist (spec 08 is cut, see *Cut on 2026-08-25*); this
section is its home. `python tasks.py trial` is the one command that tells you where you stand.
