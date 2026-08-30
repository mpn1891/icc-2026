# The work before 07

> **Written 2026-08-29, the evening pattern 6 closed.** Branch `pattern7_and_cleanup`, off `main`
> at `b3a373a` (PR #9, pattern 6 merged).
>
> **Written to be executed cold**, like [`00-next-step.md`](00-next-step.md) was for patterns 1
> and 2 — everything needed is here or one link away, and nothing below asks the reader to
> reconstruct a decision. That file stays where it is as the record of 2026-08-17; this is the
> current step.
>
> Every claim in here was checked against the running stack on 2026-08-29, not read off a spec.
> Where something is unknown it says so.

| | |
|---|---|
| **Goal** | Everything 07 stands on is correct, and every decision 07's spec cannot be written without is made |
| **Touches** | `docker-compose.yml`, `services/lims/app.py`, `docs/plans/*`, possibly `compose/postgres/`, one new talk track each for 03/05/06 |
| **Does not touch** | `metone_poll`, `bes_batch`, `bes_cdc`, the UDTs, the simulators. Patterns 1, 2, 3 and 6 are finished and this branch has no business in them |
| **Blocked by** | Nothing |
| **Unblocks** | `docs/plans/07-*.md`, and after it pattern 7 itself |

## Why this file exists

**Six acquisition mechanisms are built and broker-verified. 07 is not another one — it is the
first thing in this stack that *reads* what the others wrote**, and the difference matters: a
defect that was invisible while every pattern only published becomes a wrong field in a GxP
document the moment something joins them.

Three of the items below are exactly that shape. They have sat harmlessly in the repo for weeks
because nothing consumed them:

- `bes.batch_event` can answer *"what operation was running"* with an **empty string** (item 6).
- Every sample pattern 1 has ever minted carries an **empty `batch_id`** (item 5).
- Pattern 4's **reject publishes nothing at all**, so half of 07's trigger does not exist
  (item 3).

The rest is ordinary hygiene, with one exception worth reading first because it takes down the
demo rather than a document.

## The order

**Items 1–3 are code.** Items **4–7 are decisions** that produce spec text rather than diffs — they
need a person, not a keyboard. Item 8 is tidying, and item 9 runs in parallel with all of it.

Only item 1 has to come first. Everything else can be reordered to suit the day.

---

### 1. Debezium's entrypoint is not idempotent, and that is stage-fatal

**Do this first. It is one line and it is broken right now.**

`icc26-debezium` has been in a restart loop since some point before 2026-08-29. Reproduced
directly rather than inferred:

```
$ keytool -importcert -noprompt -alias icc26-ignition -file /certs/icc26-ignition.crt \
      -keystore /tmp/ign.jks -storepass changeit
Certificate was added to keystore                                    # first  -> exit 0
keytool error: java.lang.Exception: Certificate not imported,
               alias <icc26-ignition> already exists                 # second -> exit 1
```

The debezium entrypoint in `docker-compose.yml` imports the gateway certificate and then
`|| { echo "debezium: FAILED to import gateway cert"; exit 1; }`. **A container *restart* reuses
the writable layer, so `/tmp/ign.jks` survives**, the second import fails, the entrypoint exits 1,
`restart: unless-stopped` starts it again, and it fails identically forever. Nothing recovers it
but a force-recreate.

So **the first time that container restarts for any reason — a crash, a host reboot, a
`docker restart` — pattern 5's wire leg is dead for the rest of the session.** On a conference
floor that is a segment lost, with no diagnosis available in the time you have.

The import is deliberately at container start rather than baked into the image, and the comment
above it says why: `tasks.py seed` re-mints the gateway certificate and a runtime import always
picks up the current one. **Keep that.** Make the import repeatable instead:

```sh
rm -f /tmp/ign.jks
/usr/lib/jvm/java-21/bin/keytool -importcert -noprompt \
  -alias icc26-ignition -file /certs/icc26-ignition.crt \
  -keystore /tmp/ign.jks -storepass changeit >/dev/null \
  && echo "debezium: imported icc26-ignition.crt into /tmp/ign.jks" \
  || { echo "debezium: FAILED to import gateway cert"; exit 1; }
```

Deleting the keystore is right rather than skipping past the collision: a re-minted certificate
under the same alias is exactly the case the runtime import exists for, and an overwrite is what
you want, not a no-op.

**Done when:** `docker compose up -d --force-recreate debezium`, then `docker restart
icc26-debezium` **twice**, and the container comes back healthy both times with
`debezium: imported icc26-ignition.crt` in the log.

**Why restart twice.** Once proves the fix; twice proves the thing that was actually broken. A
single `up --force-recreate` passes even with the bug still in place, because a fresh container
has no keystore to collide with.

---

### 2. Pattern 5's first end-to-end run since 2026-08-26

Falls out of item 1 and deserves its own line, because the wire leg has been down long enough
that it should be watched rather than assumed.

`python tasks.py health` currently reports the slot INACTIVE and warns that pattern 5's topic
will be silent. Once Debezium holds: click `manual_advance` in Tag Explorer and watch
`icc26/site1/upstream/br-201/batch/event` on `mosquitto_sub`.

**Watch `qualified_window` on both polarities while you are there** — advance into `GROWTH`
(`true`) and out of it (`false`). That is 05's sharpest checkpoint and the flag 07 is about to
consume; it has not been observed in three days.

---

### 3. Pattern 4 publishes a disposition, on both outcomes

Master-plan **Order item 1**, still unbuilt. Confirmed by reading the source rather than by
trusting the status doc — `services/lims/app.py`'s `reject()` ends:

```python
LOG.info("rejected %s by %s -- nothing published", sample_id, analyst)
return {"ok": True, "sample_id": sample_id, "published": False}
```

and the string `disposition` appears **nowhere** in `services/` or `ignition/` except prose in
`services/README.md` and one line of review-screen HTML.

Two changes, both small:

- **`reject()` writes an outbox row**, the way `approve()` does, inside the same transaction as
  the status update. The outbox is what makes delivery exactly-once, and a rejection is a
  disposition with consequence — it must not be the one outcome that leaves no trace on the
  backbone.
- **The payload builder gains `values.disposition`** — `pass` on approve, `fail` on reject.
  `values.analyst` is already there.

**Why this is 07's problem and not only 04's.** 07 fires on the review, pass or fail. Today a
rejection is silent, so half of 07's trigger does not exist and the failure demo — *a sample
rejected during an excursion* — cannot be staged at all.

**Watch:** `reject()` guards on `WHERE status = 'received'`. Confirm that is still the right
guard once it publishes; an outbox row for a row that was not actually transitioned is the
failure mode to avoid.

**Done when:** approve and reject each land one message on `icc26/site1/qc/lims/sample-result`
with `values.disposition` correct, the 409 replay path behaves for both, and a
`docker restart icc26-lims` mid-flight still delivers. Those are 04's existing checkpoints —
re-run them rather than inventing new ones.

---

### 4. Decide how 07 is triggered

**Ten seconds of looking decides the shape of the whole pattern, so look before designing.**

Open the Designer, Event Streams, New, and read the **source** dropdown. Both existing streams
(`03_opcua/novaflex-result`, `06_poll/metone-result`) use `ignition.gatewayEvent`, which fires
from script. 07 needs the opposite: something that fires when a message *arrives* on
`icc26/site1/qc/lims/sample-result`.

**If MQTT Engine registers an Event Stream source type, take it and stop reading.** 07 becomes a
copy of `06_poll/metone-result` with the source swapped, a transform calling
`sample_chain.build(event.data)`, and the same Transmission handler on a new topic.

*This could not be settled from the `.modl` files.* `MQTT-Engine-signed.modl` does ship an
`eventstream-1.0.1.jar`, but it is `software.amazon.eventstream` — the AWS SDK's binary framing
codec, unrelated. A deeper search was inconclusive for a boring reason (`strings` is not on this
machine), so **treat the question as open, not as answered no.**

If there is no such source, three routes, ranked:

| | Route | Cost |
|---|---|---|
| **1** | A small subscriber service → Ignition WebDev → `sample_chain` | One service. Reuses two idioms already proven here — `lims-bridge`'s subscribe, and `cdc-sink`'s WebDev-into-a-script-module. 07 stays a genuine backbone consumer |
| **2** | Call `sample_chain` from `lims_webhook` directly | Nearly free — that module already holds the whole document as a dict before it publishes. The cost is that **07 stops being a backbone subscriber**, which [`04-lims-webhook.md`](04-lims-webhook.md) leans on explicitly: *"this pattern and pattern 7 are the only two there are"* |
| **3** | MQTT Engine custom namespace plus a gateway tag-change script | **Avoid.** Three problems, and the third is the real one |

**Why route 3 is wrong**, written down so nobody re-proposes it:

- The custom-namespace tag tree is **gitignored and rebuilt at runtime**, so the trigger surface
  is not a file and cannot be committed. The UDT-embedded event script idiom that patterns 3 and
  5 use is unavailable, because these are not UDT members.
- The tree *mirrors the JSON document*, and the review payload contains a nested `results`
  **array**. How Engine renders a JSON array into tags is unknown here. `numbersAsFloats` and
  *"a null field creates no tag at all"* both apply as well.
- Firing on one leaf leaves you holding a tag path, and 07 must then reassemble the document from
  sibling tags — **exactly the reassembly problem [`06-poll-metone.md`](06-poll-metone.md) § *The
  UDT* rejected** when it chose SQL over tag history, for the same reason: the alignment is wrong
  precisely at a boundary.

Route 2 is the schedule-bites answer, and it is honest as long as the talk track does not claim
07 subscribes. Route 1 is what the master plan describes.

**No broker change either way.** `ign-engine` already subscribes `icc26/#` and
`ign-transmission` already publishes `icc26/#`, so nothing in
[`../../compose/chariot/mqtt-users.json`](../../compose/chariot/mqtt-users.json) has to move for
07 to consume the review or publish the composite. Route 1 adds one subscribe-only user, in the
shape `lims-bridge` already has.

**Done when:** the choice and its reason are a section in 07's spec, not a note here.

---

### 5. Decide where the composite's `batch_id` comes from

**Three batch-id conventions are live in this stack**, which is two more than anybody has noticed:

| Where | Value |
|---|---|
| `lims.sample` seed row `S-MQTT-001` | `B-2026-0142` |
| `bes.batch_event`, all 12 rows | `12345` |
| **Every sample pattern 1 has actually minted** | **empty** |

Checked against the four most recent real entries (`S-20260830-0183` … `-0186`): `batch_id` is
blank on all of them, because nothing in the valve → LIMS path ever sets one.

So `values.batch_id` on the review message — the natural place for 07 to take batch identity
from — **is empty for every sample the demo will actually produce.** Two ways out:

- **Take it from `bes.batch_event`.** 07 already queries that table for the operation, and the
  row it lands on carries `batch_id`. Free, and it has the pleasing property that batch identity
  in the composite comes from the batch system rather than from the lab's copy of it.
- **Make it flow.** Pattern 1 or the LIMS starts stamping a batch id, which means deciding who
  owns it and reconciling `12345` with `B-2026-0142`.

**Recommendation: take it from `bes.batch_event`**, and write it into 07's spec as a decision
rather than letting it read like an accident. The alternative is a re-scope of pattern 1 four
weeks out for no demo benefit.

**Either way, fix the seed** so `12345` and `B-2026-0142` are not both on screen during one talk.

---

### 6. Decide what `operation` says when `batch_end` is the newest row

[`05-cdc-batch-event.md`](05-cdc-batch-event.md) § *Pattern 7 must order by `occurred_at DESC,
id DESC`* explains that both rows of one click share a timestamp and the `id` tie-break is what
makes `operation_start` win. It does — **except against `batch_end`**, which the live table shows
sharing its timestamp with the closing `operation_end` and taking the higher id:

```
 id | operation |   event_type    |  qw   |        occurred_at
 10 | HARVEST   | operation_end   | false | 2026-08-26 16:52:59.956-05
 11 |           | batch_end       | false | 2026-08-26 16:52:59.956-05   <- wins id DESC
 12 | CIP       | operation_start | false | 2026-08-26 16:53:13.611-05
```

A sample drawn between 16:52:59 and 16:53:13 resolves to **`operation = ''`**. An empty string is
not an answer a GxP document can carry — and it is not a bug in the query, which is doing exactly
what 05 designed it to do.

05 already flags the empty `batch_end` as MVP-open (§ *This is an MVP*). **07 is the consumer that
forces the call.** Three candidates:

- **`batch_end` carries the operation it closed** — here `HARVEST`. Cheapest, and arguably true.
- **07 filters `event_type <> 'batch_end'`.** Keeps 05 untouched, but puts the knowledge in the
  consumer — the thing 05 avoided by writing the flag at insert time.
- **07 reports `operation: null` with a reason** — *"no operation running: the batch had ended"* —
  which fits the *always publish, a gap is a finding* rule
  [`00-master-plan.md`](00-master-plan.md) already states for the MET ONE section.

**Recommendation: the third, implemented with the second.** A sample drawn after the batch ended
is a genuinely interesting finding, and flattening it into `HARVEST` hides it.

**If 05 changes, its spec and its checkpoints change with it.** That is the "shaped by a consumer"
pass 05's own MVP note asks for, and this branch is where it happens.

---

### 7. Decide whether 07 touches `plant.equipment`, then fix it or leave it deliberately

The table currently holds:

```
 BR-201 | novaflex-01 | vib-01 | vib-02 | vib-03 | vib-04
```

Three problems, all latent:

- **`BR-201` against `br-201`** everywhere else — every topic, every tag path, and all 12
  `bes.batch_event` rows. Logged in [`00-status.md`](00-status.md) on 2026-08-26 as *"whoever
  writes pattern 7's joins hits it first"*. This is that moment.
- **No `particle-counter-01` row** (pattern 6 open item 3) and **no `sample-valve-01` row**,
  despite the schema comment naming it as an example.
- **`vib-01` … `vib-04` are leftovers** from the vibration / AMS plan withdrawn 2026-08-23.

**Neither of 07's lookups joins this table.** Both key on `equipment_id` as a literal against
`bes.batch_event` / `em.reading`, and `bes.batch_event.equipment_id` is deliberately not a foreign
key. So this blocks nothing.

**The decision is whether the composite carries equipment metadata at all.** If yes: lowercase
`BR-201`, add the two missing rows, drop the four `vib-*`. If no: leave it, and put one line in
07's spec saying the table is not on 07's path. An unfixed mismatch that is written down is fine;
one nobody mentioned is what costs somebody an afternoon in a year.

---

### 8. Tidying, once the above is settled

Small, and none of it blocks anything:

- **`ignition/projects/icc-2026/ignition/script-python/sample_valve_trigger/`** is an empty
  directory — no files, nothing tracked. Delete it.
- **`database-connection/pg_db`** is still in the repo beside `ICC26`, still selectable in every
  dropdown, still pointing at the **`postgres` database as user `ignition`**. 05's spec calls it
  the look-alike that will waste your afternoon, and 07 is about to add another script that picks
  a datasource from a dropdown. **Delete it, or write down why it stays.**

[`00-status.md`](00-status.md) is already current as of this file — its pattern 6 row and its
*Not built* section were rewritten when this brief was written, and it points here.

---

### 9. The four talk tracks — start now, in parallel

Not a blocker for 07, and the largest remaining lump of work: **03, 05 and 06 have none, and 04's
predates the 2026-08-26 rebuild** — it still describes a queue the analyzer fills.

The repo's two-document convention makes the talk track the closing step of each pattern, so three
are overdue and the fourth is wrong. **06's writes itself**: the stale cursor recovered live by
clearing one tag, ending on a monitoring system that is up, connected, authenticated and blind.

Do these while the decisions in 4–7 are still open. They need no code, and they are the thing most
likely to be squeezed if 07 runs long — **07 is the designated cut if the schedule bites**, and
talk tracks for patterns that already work are worth more than a seventh pattern that does not.

---

## Checkpoints

| CP | Check | State |
|---|---|---|
| **1** | `docker restart icc26-debezium` **twice**; healthy both times, cert imported both times | **closed** 08-30 — 3 imports, 0 failures, slot active |
| **2** | A `manual_advance` click reaches `.../br-201/batch/event` on the wire, `qualified_window` correct on both polarities | **closed** 08-30 — seq 35–41 captured live, `qw=True` on `operation_start GROWTH` |
| **3** | Reject publishes; both outcomes carry `values.disposition`; 04's existing checkpoints still pass | **closed** 08-30 — seq 20 `pass`, seq 21 `fail`, 409 replay holds both verbs |
| **4** | The Event Stream source dropdown is read, and 07's trigger route is chosen with a reason | **closed** 08-30 — MQTT Engine ships `EventStreamMqttSource`; settled from the `.modl`, dropdown unread |
| **5** | `batch_id`, `batch_end` and `plant.equipment` each have a written decision | **closed** 08-30 — all three in [`07-sample-chain.md`](07-sample-chain.md) |
| **6** | `python tasks.py health` fully green — no WARN lines | **closed** 08-30 |
| **7** | Talk tracks exist for 03, 05 and 06; 04's is rewritten | **pending** — the largest remaining lump |
| **8** | `docs/plans/07-*.md` written, with items 4, 5, 6 and 7 recorded as decisions | **closed** 08-30 — [`07-sample-chain.md`](07-sample-chain.md) |

**Items 1, 3, 5 and 6 were also built, not just decided.** `batch_end` now carries `IDLE`,
`batch_id` is minted rather than typed, `lims.sample` has an `equipment_id` the review message
carries, and the bioreactor UDT has `asset_data/equipment_identifier`. Five commits,
`0194226` → `19fb732`. The one thing the old plan got wrong: **`pg_db` is in use** — the
historian provider and store-and-forward are both bound to it — so it stays.

**Item 9's licence finding, which this file predates:** Chariot's trial is **two hours**, the
broker refuses to start when it lapses, and the container reports `healthy` throughout. It is now
the largest stage risk in the stack.

## What this deliberately does not do

- **It does not touch patterns 1, 2, 3 or 6.** They are finished. Pattern 6 closed all ten of its
  checkpoints on 2026-08-29, and nothing here has a reason to open that file except to read it.
- **It does not build any part of 07.** Everything above is either a defect in what 07 stands on
  or a decision 07's spec needs. The spec comes after this file, and the build after that.
- **It does not fix `poll_interval_s`.** Pattern 6 open item 9 records that the tag is decorative
  and cannot be made otherwise; both honest repairs are bigger than the demo needs.
- **It does not re-open the event-store question.** That closed on 2026-08-29 with master-plan
  Order item 4: `bes.batch_event` and `em.reading` were built to the same lookup shape on purpose,
  so 07 has one query idiom rather than two.

## What 07 inherits, and what stays its own

Settled — 07's spec should not re-argue any of it:

- Both flags read persisted rows, with an index each (`ix_batch_event_lookup`,
  `ix_em_reading_lookup`) and one idiom:
  `WHERE <id> = ? AND occurred_at <= ? ORDER BY occurred_at DESC, id DESC LIMIT 1`.
- Patterns 1 and 3 need no lookup at all — the LIMS review carries
  `values.collection.sample_start`, the analytes, and the analyst.
- **07 does no arithmetic.** It reads `status` from pattern 6 and `qualified_window` from pattern
  5, both computed at ingest by the pattern that produced the fact.
- No new tables, no ACL change, no new datasource.

Still 07's own — [`06-poll-metone.md`](06-poll-metone.md) § *What pattern 7 gets from this* hands
both over deliberately:

- **How stale is too stale** for the nearest MET ONE reading. The timer now bounds this: with the
  counter sampling, the nearest reading is **≤ 27.2 s old** (pattern 6 CP7). So the tolerance only
  bites when nobody pressed Start — the pre-show failure `tasks.py health` already names.
- **Nearest before, or nearest either side.** A reading 3 s *after* the valve opened is arguably
  better evidence than one 25 s before.
