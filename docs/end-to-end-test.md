# End-to-end test — one sample, six mechanisms

> **How to prove the stack is sound, in about ten minutes, without editing anything.**
> Written 2026-08-31, the first time the whole path was walked in one sitting. Every number below
> was measured on that walk rather than predicted.
>
> **This is not the demo runbook.** That was spec 08 and it was cut on 2026-08-25 — no Perspective
> views, no mechanism-coloured firehose, no per-segment script. This file answers *does the stack
> work*. What you **say** is [`talk-tracks/`](talk-tracks/); why the segments run in the order they
> do is [`demo-through-line.md`](demo-through-line.md).

| | |
|---|---|
| **Proves** | Patterns 1, 3, 4, 5, 6 and 7 in one chain of gestures — the only path that exercises them together |
| **Costs** | ~2 minutes, most of it a 13.5 s valve stroke and an analyzer run |
| **Changes** | One batch advance and one sample. No file is edited, no service restarted, nothing seeded |
| **Needs** | A stack already up (`python tasks.py up`, see [`../README.md`](../README.md)) and a Chariot trial with time left on it |
| **Does not prove** | **Pattern 2.** `br-202`'s samples never reach the LIMS by design — `lims-bridge`'s ACL does not subscribe to its topic. It looks live in Tag Explorer and is not. [`plans/07-sample-chain.md`](plans/07-sample-chain.md) decision 5 |

---

## 0. Health first

```powershell
python tasks.py health
```

All green is the bar. Three lines carry the whole pre-flight:

| Line | What it has to say |
|---|---|
| `chariot   MQTT listener RUNNING on :1883 (trial NNN min)` | **The trial is two hours.** Under ~20 min, restart the broker now rather than halfway through the walk |
| `sim-metone SAMPLING, N record(s) buffered` | Anything else means press **Start** on <http://localhost:8089>. `SEED_SAMPLES: 0` — nothing free-runs |
| `em.reading  last stored N s ago` | Under 180 s. If it warns `STALLED`, do step 1 |

**Two failures this check cannot see.** It reads the store, not the wire, so a lapsed trial leaves
it green while nothing publishes. And it asks whether the instrument is sampling *now*, not whether
it was sampling an hour ago. On 2026-08-30 both were true at once and every check stayed green for
15.5 hours.

## 1. Clear the MET ONE cursor, if it is stale

Gateway UI at <http://localhost:8088> → Tag Explorer → clear this one tag:

```
[default]icc26/site1/qc/analyzers/particle-counter-01/state/cursor
```

Clear **only** that one — an empty cursor resets the `last_sequence` guard on the same poll. Leave
`state/last_sequence` alone, or the replay is filtered straight back out.

**The drain finishes before you can watch it** — measured at 414 records in a single poll, because
one poll walks every page rather than one page per cycle. Re-run `health`: `last stored` should be
back under 30 s.

## 2. Start the watcher

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

`icc26/#`, not the composite topic alone. If the composite never lands, the only way to know which
hop went quiet is to have been watching all of them.

Within 30 s you should see a **burst of three** MET ONE results rather than one every 10 s — the
poll batches three analyses and the Event Stream debounces them.

## 3. Put `br-201` in `GROWTH`

Tag Explorer:

```
[default]icc26/site1/upstream/bioreactors/br-201/batch_data/manual_advance
```

Click once per operation, letting `operation` settle between clicks:
`CIP → SIP → INOC → GROWTH`. **Stop at GROWTH.**

**This mints a new batch.** `batch_id` is assigned at CIP, so you get today's — you are not resuming
whatever the reactor was parked on. From `SIP` onward each click puts two messages on
`icc26/site1/upstream/br-201/batch/event`, an `operation_end` and an `operation_start` sharing a
timestamp to the millisecond.

`GROWTH`/`operation_start` is the only row carrying `qualified_window: true`, and step 7 reads the
newest row for this vessel — which is why you stop there.

## 4. Draw the sample — <http://localhost:8085>

Press badge **`B-1042`**, the authorized one. **Copy the `sample_id` off the page exactly.** You type
it by hand in the next step, and that transcription is the point of the pattern rather than an
inconvenience.

The valve strokes for ~13.5 s. `event/badge-scan` publishes the instant the badge is read;
`event/sample-complete` when it closes. The LIMS opens its entry from the second one, so within a few
seconds the sample exists at :8000 as **Awaiting analysis**, with no Approve button.

## 5. Run the analysis — <http://localhost:8087>

Type that id into the analyzer's sample-login screen and press Run. Nothing free-runs here either —
`CELL_ANALYZER_SAMPLE_INTERVAL_S` is `0` deliberately, so that a person types the id in.

The *same* entry at :8000 flips to `received` with a batch id. **No second entry appears** — if one
does, the id was transposed; see the troubleshooting table.

**Expect two analytes, glucose and lactate.** Not three: `CELL_ANALYZER_OSMO_INSTALLED` is `false` on the
shipped defaults, so `Osmo/Result` sits at `Bad_NoData` and the absent-vs-zero rule correctly writes
no row. [`plans/04-lims-webhook.md`](plans/04-lims-webhook.md) § *Granularity*.

## 6. Approve — <http://localhost:8000>

**Do not click the first thing on the screen.** The queue orders by `sample_completion` ascending, so
a block of pre-`migrate-08` rows with a null `equipment_id` sits at the top, and they are the only
other clickable rows there. Approving one produces a composite with `batch_context: null` and looks
exactly like a broken join.

Find **your** `sample_id` and approve that one. Reject works too and is worth doing once: same
document, `disposition: "fail"`, not silence.

## 7. Nothing is published — and that is the pass

**Since 2026-09-06 pattern 7 speaks only on a deviation.** A clean sample produces no message at
all, so the watcher stays quiet on `icc26/site1/qc/deviation` and the gateway logs
`... is clean; no deviation published`. If a message appears here, something really was wrong —
read it with the table in step 8.

If you rejected in step 6 instead of approving, you get a message now: `violations[0].code` is
`failed_review`.

## 8. Make a deviation and read it

Press **Dirty** on the MET ONE panel (<http://localhost:8089>) and wait about 30 s for a fresh
reading to land — dirty draws measure 3346–4350 counts against a threshold of 1660. Then repeat
steps 4–6 with a new sample.

One message lands on `icc26/site1/qc/deviation`. It passes if all of these hold:

| Field | Expected | Why it matters |
|---|---|---|
| `values.violations[0].code` | `environmental_excursion` | Why the message exists at all |
| `values.violations[0].source` | `em.reading.status` | Names the module that owns the rule — 07 computed nothing |
| `meta.correlation_id` | your `sample_id` | The chain held onto the right sample |
| `meta.mechanism` | `aggregate` | Pattern 7's own mechanism tag |
| `values.batch_id` | today's, e.g. `B-20260831-01` | Not `null` — the `bes.batch_event` lookup hit |
| `values.batch_context.operation` | `GROWTH` | Pattern 5's fact, read rather than recomputed |
| `values.batch_context.qualified_window` | `true` | The GxP claim the whole talk builds to |
| `values.batch_context_reason` | `null` | Named sibling, `null` on the success path |
| `values.environment.age_s` | single digits | Pattern 6 is live; nearest reading either side |
| `values.environment.nearest_side` | `before` or `after` | A distance, not a signed number |
| `values.environment_reason` | `null` | Same discipline as above |
| `values.results` | two analytes | glucose and lactate — see step 5 |
| `values.collection.badge_holder` | `Jordan Reyes` | Pattern 1's provenance survived the whole chain |
| `values.disposition` / `values.analyst` | `pass` / your user | Pattern 4 signs on both outcomes |
| `values.equipment_identifier` | `br-201` | A tag read off the UDT — `null` if unreadable, never fatal |
| `seq` | non-zero | The review's outbox delivery id |

**Press Clean again when you are done**, or every subsequent sample deviates.

**Read the three gaps out loud, because they are the argument:** `ts` against `meta.ingest_ts` is the
human in the loop, `batch_context.as_of` against `ts` is how far into the operation the sample was
drawn, and `age_s` is how close the room reading was. No arithmetic on stage.

### Known good — the 2026-08-31 walk

Trimmed from `S-20260831-0103`, which passed every row above. **It predates the 2026-09-06
deviation change**, so it carries no `values.violations` and was published on the old
`icc26/site1/qc/sample-chain`; every other field is unchanged:

```json
{"meta":{"ingest_ts":"2026-08-31T15:41:13.184Z","correlation_id":"S-20260831-0103",
 "mechanism":"aggregate"},
 "values":{"disposition":"pass","analyst":"mnorris","sample_id":"S-20260831-0103",
  "batch_id":"B-20260831-01","equipment_id":"br-201","equipment_identifier":"br-201",
  "batch_context":{"operation":"GROWTH","event_type":"operation_start",
                   "qualified_window":true,"as_of":"2026-08-31T15:39:24.600Z"},
  "batch_context_reason":null,
  "environment":{"device_id":"particle-counter-01","age_s":4.9,"nearest_side":"before",
                 "status":"normal","occurred_at":"2026-08-31T15:39:55.761Z"},
  "environment_reason":null,
  "collection":{"badge_id":"B-1042","badge_holder":"Jordan Reyes",
                "sample_start":"2026-08-31T15:39:47.109Z","open_duration_s":13.51,
                "cycle_result":"normal","sample_completion":"2026-08-31T15:40:00.618Z"},
  "results":[{"analyte":"glucose","value":5.92,"uom":"g/L"},
             {"analyte":"lactate","value":0.14,"uom":"g/L"}]},
 "source":{"id":"sample-chain","type":"aggregate"},"seq":26,
 "ts":"2026-08-31T15:40:00.618Z"}
```

`ts` to `ingest_ts` was 72.6 s on that walk — the time it took a person to copy an id, type it into
an instrument and sign. That gap is provenance, not lag.

## When it does not work

| Symptom | Cause | Fix |
|---|---|---|
| The review message landed, the composite never did | The Event Stream MQTT source may not re-subscribe after a broker drop — **unmeasured as of 2026-08-31** | Disable and re-enable `07_chain/lims-review`, then approve again |
| `batch_context: null`, reason `the review message carried no equipment_id` | You approved one of the pre-`migrate-08` rows at the top of the queue | Approve your own sample (step 6) |
| `qualified_window: false` | The reactor is not in `GROWTH`, or the sample was drawn before the advance | Step 3, then draw again. Note this is also a deliberate demo beat |
| `environment: null` with a reason beside it | `em.reading` is empty | Step 1 |
| `age_s` in the thousands | The poll recovered but the instrument is not sampling | Press **Start** on <http://localhost:8089> |
| Nothing on any topic and `health` is green | The Chariot trial lapsed. The container still reports `healthy` | Restart `icc26-chariot` |
| The entry stays `awaiting-analysis` and the result appears under **Unmatched results** | The id was transposed at :8087 | Attach it from that screen. The wrong id is preserved on purpose |
| Two analytes rather than three | Correct — `CELL_ANALYZER_OSMO_INSTALLED` is `false` | None. Do not "fix" it |
| `git status` dirty afterwards | A gateway restart rewrote the tag-provider files — `uuid` and `nonUseCount` churn | Known and unresolved. [`plans/00-post-07.md`](plans/00-post-07.md) § 6 |

## What this test does not cover

- **Pattern 2** — see the header table.
- **The failure paths.** A sagged air supply at :8085 yields a `failed-to-seat` entry, reviewable
  immediately with no analytes; a transposed id parks under Unmatched results and reattaches with the
  wrong id preserved. Both are verified in [`plans/04-lims-webhook.md`](plans/04-lims-webhook.md)
  § *Verification*, and both are worth a lap before the talk.
- **The negative window.** Advancing past `HARVEST` and drawing again should give `IDLE`,
  `qualified_window: false`, **and nothing empty**. That is pattern 7's sharpest beat, and it costs
  four clicks to walk the reactor back afterwards.
- **Restart survival.** `docker restart icc26-lims` logs the retained redelivery as a no-op and the
  outbox survives. Covered in pattern 4's spec.

## Progress log

| Date | |
|---|---|
| 2026-08-31 | Written after the first end-to-end walk in one sitting, `S-20260831-0103`. Assembled from [`plans/00-post-07.md`](plans/00-post-07.md) § 1, which found the loop while diagnosing the review queue, and [`talk-tracks/07-sample-chain.md`](talk-tracks/07-sample-chain.md) § *On stage*, which had the beats but started at Approve. The *Known good* block and the 414-in-one-poll drain are from that walk |
