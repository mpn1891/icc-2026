# 04 — LIMS approval webhook

> **Supersedes the pattern-4 entry in [`00-master-plan.md`](00-master-plan.md) entirely.** Written
> 2026-08-19 and **rewritten the same day**, smaller, after patterns 5, 6 and 7 were given their
> own sources — see [`../00-architecture.md` § *Patterns 4, 5 and 6 used to share one
> topic*](../00-architecture.md).
>
> **Built 2026-08-20 and broker-verified the same day.** Talk track:
> [`../talk-tracks/04-lims-webhook.md`](../talk-tracks/04-lims-webhook.md). Schema and the `lims-bridge` subscribe grant
> take effect on an empty volume; on this checkout they were applied live (`migrate-04-lims.sql`
> + Chariot `PUT /mqttusers/lims-bridge`) without a nuke. `python tasks.py nuke` then `seed`
> remains the clean-room path. Odoo is no longer in the stack (2026-08-23).
>
> **Revised 2026-08-26: the LIMS now opens the sample entry from pattern 1's
> `event/sample-complete` and appends the analyzer result to it.** Read
> [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result)
> first — it supersedes several sections further down, which are left in place
> because the reasoning in them is still why this pattern is shaped the way it is.

## Revised 2026-08-26 — the entry is opened at collection, not at result

**This section supersedes [*Objective*](#objective), [*The chain*](#the-chain),
[*Granularity*](#granularity), [*Schema change*](#schema-change--do-it-in-one-nuke) and
[*Envelope*](#envelope) below. Everything else in this file still stands** — the outbox
argument, the cycle hazard, the manual-approval decision and the failure demo are unchanged and
are still the engineering content.

### What changed

The LIMS had no concept of a sample. A sample was the `GROUP BY sample_id` of whatever analyte
rows the analyzer happened to produce, so **the record did not exist until the instrument
spoke**. That is backwards for a LIMS and backwards for the talk: the sample begins when
material leaves the reactor, which is the rule
[`../00-architecture.md` § *The sample id, and pattern 1 mints it*](../00-architecture.md)
already settled.

Now there are two subscriptions and the order they arrive in *is* the pattern:

| | Topic | What it does |
|---|---|---|
| 1 | `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` (pattern 1) | **Opens the entry.** One `lims.sample` row carrying the badge holder, `sample_start`, `open_duration_s` and `cycle_result` |
| 2 | `icc26/site1/qc/analyzers/+/result` (pattern 3) | **Appends the analytes** to that entry, minutes later |

The analyst reviews one record holding both halves. Approve publishes both halves.

### The transcription, which is the point

The two ids only match because **a person makes them match.** The valve mints
`S-YYYYMMDD-NNNN` on the badge grant; the analyzer's own sample-login screen
(`services/opcua-cell-analyzer/webui.py`, port 8087) is where somebody reads that id off BR-201's
page and types or scans it in. The analyzer echoes it back on its result because that is what the
vendor's writable `SampleInformation/SampleID` node is for.

An Ignition tag write into `cell_analyzer/command/sample_id` would do the same job and could not
be typed wrong. **That is exactly why it is not what we built.** Pattern 1's GxP hook is *the
record originates at the point of action; no transcription, no intermediary* — and this is the
intermediary, on stage, with a keyboard. One transposed character and the LIMS shows the whole
cost of it on one screen: an entry sitting open with no analysis, and an analysis parked with
no entry.

Consequence: **the analyzer no longer free-runs** (`SAMPLE_INTERVAL_S=0`). It cannot. A
self-driving instrument invents ids nobody transcribed, and every result it produced would park
unmatched. QC moved to a button on the same page for the same reason — it used to ride on
free-running cycles.

### What this costs pattern 7, and what it buys

Pattern 4 now performs the valve↔analyzer half of the join pattern 7 exists to demonstrate. Decided
deliberately, and it is a trade rather than a loss: the released review message carries
`values.collection.sample_start`, which is **the instant both of pattern 7's derived flags are
evaluated at**. Two of the four sources its unspecified event store had to persist are no longer
its problem. Pattern 7 still joins batch phase (05) and the nearest MET ONE (06), which is where
both flags actually come from.

### Schema

`lims.sample` is the entry; `lims.sample_result` is the analyte rows appended to it. Full DDL in
[`../../compose/postgres/initdb/02-schema.sql`](../../compose/postgres/initdb/02-schema.sql);
live-apply for an existing volume in
[`../../compose/postgres/migrate-05-sample-entry.sql`](../../compose/postgres/migrate-05-sample-entry.sql).
**Run that migration as `postgres`, not `icc26`** — initdb created these tables as the
superuser, and `ALTER TABLE ... RENAME COLUMN` is owner-only.

Two things in it are load-bearing:

- **`sample_result` has two id columns.** `reported_sample_id` is what the instrument said,
  verbatim, and is never rewritten — the dedupe constraint moved onto it
  (`UNIQUE (reported_sample_id, analyte)`). `sample_id` is the entry it was attached to, and is
  **null when nothing carries that id**. No FK, because an unmatched result still has to insert.
  You do not correct a record by overwriting what the instrument reported; you record the
  correction beside it, with `attached_by` and `attached_at`.
- **`ON CONFLICT (sample_id) DO NOTHING` on the entry insert is not tidiness.**
  `sample-complete` is published **retained** and this client connects `clean_session=True`, so
  the broker replays the last one on every reconnect. Without the conflict clause,
  `docker restart icc26-lims` resurrects the most recent sample — already approved, already
  released — back into the review queue. Retained plus clean session is a redelivery source
  people forget they signed up for, and it sits right next to the QoS-1 one this pattern already
  argues about.

### Entry lifecycle

```
awaiting-analysis ──(analytes attach)──▶ received ──(e-sign)──▶ verified | rejected
        │
        └─ cycle_result ≠ normal opens straight into `received`:
           no material reached the analyzer, nothing is coming, and it still
           needs a signature
```

`awaiting-analysis` is the one state a signature cannot be applied to — there is nothing yet to
have reviewed. Both the screen and `Store._not_reviewable` refuse it, and the refusal says
*"has no analysis yet — nothing to sign off"* rather than reading a status string back at
somebody.

`batch_id` arrives with the analysis, not with the valve: the valve opens on a badge, not on a
work order, and its event carries no batch at all.

### The review screen

Three panels on `:8000` — **Open samples**, **Unmatched results**, **Release transmissions**.
The unmatched panel is the transcription error made legible: each parked analysis with a picker
of the open entries, and an Attach button that records who decided.

### Envelope

> **Extended by [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result).** `values.collection` was added; everything below is otherwise current.


Unchanged except for one added object. `values` now carries:

```json
"collection": {
  "badge_id": "B-1042",
  "badge_holder": "R. Okafor",
  "sample_start": "2026-08-26T14:02:58.412Z",
  "sample_completion": "2026-08-26T14:03:11.922Z",
  "open_duration_s": 13.51,
  "cycle_result": "normal"
}
```

`ts` is still `collected_at` — the event being described is the measurement, and the gap to
`meta.ingest_ts` is still the point. On a sample that was never analysed there is no acquisition
instant, so the valve's close time stands in: that genuinely is when the record was made.

**No Ignition change was needed for any of this.** `lims_webhook.handle()` republishes the
envelope wholesale and only fills `meta` / `seq` / `source` when absent — there is no field
whitelist to widen.

### ACL

`lims-bridge` gains one subscribe topic, named to the one device rather than wildcarded.
`publishTopics` stays `[]`, which is still the enforcement described in
[*The cycle hazard*](#the-cycle-hazard).

```json
"subscribeTopics": [
  "icc26/site1/qc/analyzers/+/result",
  "icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete"
]
```

On a live stack: Chariot `PUT /mqttusers/lims-bridge` — `mqtt-users.json` seeds on first run
only.

### Verification

1. Badge `B-1042` on `:8085`. ~15 s later the entry appears on `:8000` as **Awaiting analysis**,
   badge holder and open duration populated, no Approve button.
2. Type that id into `:8087`, press Run. The *same* entry flips to ready with two analytes and
   a batch id — two, not three; see [the mapping table](#granularity). **No second entry appears.**
3. Approve. One message on `icc26/site1/qc/lims/sample-result` carrying `values.collection`
   beside the analytes.
4. **Do it wrong.** Repeat step 2 with a transposed character: the entry stays awaiting, the
   result lands under Unmatched results. Attach it, then confirm in Postgres that
   `reported_sample_id` still holds the wrong id.
5. **Failed cycle.** Sag the air supply on `:8085`, badge again. The entry arrives
   `failed-to-seat`, reviewable immediately with no analytes, and Reject works.
6. **Retained replay.** `docker restart icc26-lims` — nothing is created, nothing resurrects.
7. The existing outbox-survival test still passes unchanged.

---

## Objective

> **Superseded in part by [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result).** The entry is opened by pattern 1's valve event; the analyzer result is appended to it.


A sample result produced by the pattern-3 analyzer is received by the LIMS off the event backbone,
held unreviewed, released by a human, and only then pushed into Ignition over HTTP and published
to `icc26/site1/qc/lims/sample-result` with `meta.mechanism = "webhook"`, `analyst`, and
`disposition` ∈ `pass | fail`. Both approve and reject publish.

## Talk point

**A webhook exists because the answer is not ready when you ask.** If the LIMS could answer
synchronously, the correct design is an HTTP response, not a callback. The callback is what you
build when the work is asynchronous, and here it is asynchronous for the most ordinary reason in a
regulated plant: a person has to sign it off.

The second point is the failure mode, and since the convergence set-piece went away it has to
stand on its own — which turns out to make it *better*, because the honest version is more useful
to an audience than "switch to CDC" was.

**A naive webhook loses data, permanently.** One delivery attempt, over a network the sender does
not control, against a receiver that may be restarting. When the retries exhaust there is nothing
to replay from, and the sender has already forgotten. There is no other mechanism in this demo
covering the same data, so nothing rescues it.

The fix is the one an audience can actually apply on Monday: **a transactional outbox.** Commit
the result and the intent-to-deliver in the same transaction, then let a separate worker drain the
outbox with retries and at-least-once delivery. That is what `lims.webhook_delivery` is for, and
building it is most of this pattern's engineering content.

And then the line worth saving for last: **an outbox is change data capture that you wrote by
hand.** You end up rebuilding, badly and inside your application, the thing Postgres already does
in the WAL — which is pattern 5's argument arriving from the opposite direction, without either
pattern having to share a topic with the other.

## What changed from the master-plan sketch

| | Sketch in `00-master-plan.md` | As specified here | Why |
|---|---|---|---|
| Data origin | a generator invents sample results on an interval | **subscribes to `icc26/site1/qc/analyzers/+/result`**; the generator survives as a fallback | A service inventing numbers is the least convincing artifact on the stage. Chaining pattern 3 into 4 makes one sample's journey the spine of the talk |
| Inbound transport | n/a (the LIMS was the origin) | **MQTT subscribe** | Nothing else in this stack *consumes* the backbone except pattern 7. "One event backbone" is a weak claim with no subscribers on it — and since the firehose was cut (2026-08-25) this pattern and pattern 7 are the only two there are |
| Webhook trigger | on insert / sample-complete | **on manual approval**, and only on approval | Gives the callback a real reason to be asynchronous |
| Approval UI | Perspective | **served by the LIMS itself on `:8000`** | Same house style as the two valve config pages on 8085/8086. A LIMS screen should look like a LIMS, not like SCADA, and it keeps Perspective off this pattern's critical path |
| LIMS MQTT publish rights | `sample-result` + `batch/event` | **none — `publishTopics: []`** | See [The cycle hazard](#the-cycle-hazard) |
| Contract surfaces | four, one per pattern | **one** | Patterns 5, 6 and 7 have their own sources as of 2026-08-19 |
| Message granularity | unstated | **one message per sample**, all analytes | See [Granularity](#granularity) |
| Delivery durability | "retry/backoff" | **a transactional outbox** | It is the pattern's main engineering content now that no other mechanism covers this data |

## The chain

> **Superseded by [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result).** The diagram below is the analyzer leg only, and is still accurate for that leg. The valve leg that now opens the entry is missing from it.


```
opcua-cell-analyzer ──OPC UA──▶ Ignition ──Event Stream──▶ Transmission
                                                          │
                          icc26/site1/qc/analyzers/cell-analyzer-01/result   (mechanism: opcua-event)
                                                          │
                                              subscribe (QoS 1, lims-bridge)
                                                          ▼
                                                   ┌──────────────┐
                                                   │  lims :8000  │  INSERT status='received'
                                                   │              │
                                                   │  approval UI │◀── a human clicks Approve
                                                   └──────┬───────┘    UPDATE + outbox row,
                                                          │            one transaction
                                        POST + shared secret + idempotency key
                                                          ▼
                                              Ignition WebDev  ──▶ Transmission
                                                          │
                                 icc26/site1/qc/lims/sample-result   (mechanism: webhook)
```

`meta.correlation_id` is stamped once, upstream, and survives the whole chain, so one sample is
traceable across two `meta.mechanism` values on the wire — `opcua-event` then `webhook`, visible
side by side in one `mosquitto_sub`. (This used to be phrased as "two colours on the firehose";
the firehose was cut on 2026-08-25 and the terminal is the demo surface.) **This requires a
one-line addition to pattern 3** — see [Ignition resources](#ignition-resources).

## Decisions, and the reasoning

### Granularity

> **Superseded by [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result).** Still one message per sample; the analyte mapping below is unchanged. What changed is that the sample exists before any of it arrives.


**One message per sample, carrying all analytes.** `lims.sample_result` is one row per analyte, so
a sample is several rows, and the earlier draft of this spec published one message per *row* to
match the row-granular CDC stream that pattern 5 was going to produce from the same table.

Pattern 5 no longer tails this table, so the message can be the domain object
instead of the table's shape. Keep the analyte list short anyway, so the payload stays readable on
a projector:

| Analyte | Source field in pattern 3's envelope | `uom` |
|---|---|---|
| `glucose` | `values.chem.gluc` | `g/L` |
| `lactate` | `values.chem.lac` | `g/L` |
| `osmolality` | `values.osmo` | `mOsm/kg` |

`collected_at` ← the envelope's top-level `ts`, which is the vendor's `SampleTime` and therefore
genuinely the acquisition instant. `sample_id` ← `values.sample_id`, `batch_id` ←
`values.batch_id`. A `null` analyte value (Bad OPC quality, per pattern 3's `_value`) must produce
**no row at all**, not a zero — same absent-vs-zero discipline as the analyzer.

**A released sample carries two of these three.** `CELL_ANALYZER_OSMO_INSTALLED` is `false` on the
shipped defaults, so `Osmo/Result` sits at `Bad_NoData`, the rule above produces no row, and
osmolality never appears — deterministically, because `CELL_ANALYZER_SENSOR_ERROR_RATE` is `0.0` as
well. The third row is the mapping for a stack with the module installed, not a description of
what the room will see. Measured on the wire 2026-08-31 (`S-20260831-0103`: glucose, lactate).

### Approval is manual, and nothing is released without it

No auto-approve timer. Consequences to accept deliberately:

- **The demo cannot run unattended**, which touches the T-15 checklist and the offline acceptance
  test in `00-master-plan.md` § *Verification*. Both need a human to click Approve.
- **A pending queue builds up** while the analyzer cycles every 120 s. Good stage material rather
  than a problem: a screen showing fourteen samples awaiting QA release is what a real LIMS looks
  like at 9 a.m., and it makes Approve the most legible click in the talk.
- **Both review outcomes publish.** Approve writes `disposition=pass`; reject writes
  `disposition=fail`. Each path writes one outbox row. Pattern 7 listens for the review,
  not only for a pass. (Until 2026-08-23 reject was silent, mirroring pattern 3's
  abort-does-not-publish rule. That is withdrawn.)

### The cycle hazard

The LIMS is the first component in this stack that both consumes the backbone and causes publishes
onto it. That is not a feedback loop **only** because it has no publish rights at all: its sole
output is the HTTP callback into Ignition, and Transmission does the publishing. Widen
`lims-bridge`'s subscription to `icc26/#` and you have an infinite loop; give it publish rights and
you have one it can build by itself.

So the ACL is the enforcement, exactly as it is for pattern 1's free-text topic box. That is a talk
point, not plumbing: *the same file that stops the valve leaving the upstream area stops the LIMS
talking to itself.*

### The outbox is a separate table, and the reason changed

`lims.webhook_delivery` is not part of `lims.sample_result`. When pattern 5 tailed that table, this
was load-bearing: attempt counters on the result row would have made **every retry an UPDATE and
every UPDATE a CDC event**, so the webhook's failure handling would have generated spurious
pattern-5 traffic precisely during the demo where it was failing on purpose.

Pattern 5 tails `bes.batch_event`, not this table, so that hazard is gone and the
decision now stands on ordinary grounds:
delivery state is not domain state, and an outbox you can query, retry and show on screen is worth
a table. Recorded rather than quietly re-justified, because a reason that has evaporated is worth
knowing about — if this table ever looks like overkill, the argument that put it there is no longer
the argument keeping it there.

## Schema change — do it in one nuke

> **Superseded by [*Revised 2026-08-26*](#revised-2026-08-26--the-entry-is-opened-at-collection-not-at-result).** `lims.sample` now exists and `sample_result` carries two id columns. The DDL below is the 2026-08-20 shape.


Two files change, and both only take effect on an empty volume (`../00-architecture.md` §
*Postgres*). Batch them into a single `tasks.py nuke` + `seed`, which costs one commissioning
wizard and one API key.

**Sequencing rule (withdrawn 2026-08-23):** this used to say "nuke before Odoo init."
Odoo is no longer in the stack. `nuke` still rebuilds `icc26`; do it before pattern 5
connects Debezium if the publication is changing.

`compose/postgres/initdb/02-schema.sql`:

```sql
ALTER TABLE lims.sample_result
    ADD COLUMN status      text NOT NULL DEFAULT 'received',  -- received | verified | rejected
    ADD COLUMN verified_at timestamptz;

-- Idempotency for at-least-once MQTT redelivery (see the checkpoints). A real LIMS repeats
-- tests, so this uniqueness is a demo simplification — recorded in the deviations table.
ALTER TABLE lims.sample_result ADD CONSTRAINT uq_sample_analyte UNIQUE (sample_id, analyte);

-- The outbox. One row per sample approved, not per result row, because one approval is one
-- delivery.
CREATE TABLE lims.webhook_delivery (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id   text NOT NULL UNIQUE,
    payload     jsonb NOT NULL,          -- built at approval time, inside the transaction
    attempts    int  NOT NULL DEFAULT 0,
    state       text NOT NULL DEFAULT 'pending',  -- pending | delivered | abandoned
    last_error  text,
    next_try_at timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_webhook_delivery_due ON lims.webhook_delivery (state, next_try_at);
```

An earlier draft added a `release_seq` column and sequence, so pattern 6 could watermark on the
order results were *released* rather than the order they arrived. Pattern 6 no longer polls the
LIMS, so it is cut. See `00-master-plan.md` § 06.

`compose/chariot/mqtt-users.json` — replace the `lims-bridge` entry:

```json
{
  "username": "lims-bridge",
  "password": "lims-bridge",
  "acl": {
    "subscribeTopics": ["icc26/site1/qc/analyzers/+/result"],
    "publishTopics": []
  }
}
```

Its `icc26/site1/upstream/br-201/batch/event` publish grant was for pattern 5, which publishes
through Transmission like everything else. Nothing loses a grant it was using.

Also worth doing while the file is open: `02-schema.sql` calls `lims.sample_result` "the single
most important table in the demo." That is not true — only pattern 4 writes it. Pattern 5
CDC-tails `bes.batch_event` (2026-08-23); drop `lims.sample_result` from `04-cdc.sql` when
that spec is written so a LIMS approval is not also a CDC event.

## Files to create

### `services/lims/app.py`

House style from `services/opcua-cell-analyzer/app.py`: `_env`/`_env_float`/`_env_bool` helpers, a
`Config` class, docstrings that say *why*. FastAPI + `psycopg` + `paho-mqtt`, with the paho network
loop on its own thread (`loop_start()`) rather than in the event loop, and the outbox drainer on a
third.

```python
# ── ingest ────────────────────────────────────────────────────────────────────
# Subscribed at QoS 1, which is AT-LEAST-ONCE: a redelivery must not create a
# second row. ON CONFLICT DO NOTHING against uq_sample_analyte is the whole
# dedupe, and it is the reason that constraint exists.
ANALYTES = [
    # (analyte, dotted path under envelope["values"], uom)
    ("glucose",    "chem.gluc", "g/L"),
    ("lactate",    "chem.lac",  "g/L"),
    ("osmolality", "osmo",      "mOsm/kg"),
]

# ── approval ──────────────────────────────────────────────────────────────────
# ONE transaction: flip the rows AND write the outbox row. If the process dies
# between them, a sample is released with nobody obliged to deliver it -- which
# is the exact failure this pattern exists to argue about, so it must not be
# possible to cause it by accident here.
def approve(sample_id, analyst):
    with conn.transaction():
        rows = _verify_rows(sample_id, analyst)   # -> status/verified_at/analyst
        _enqueue_delivery(sample_id, _build_envelope(rows))
```

Surfaces:

| Method + path | For | Notes |
|---|---|---|
| `GET /` | the approval screen | server-rendered HTML: pending queue, Approve / Reject, and the outbox with its attempt counts |
| `POST /samples/{sample_id}/approve` | approval | form-posted by the screen; also curl-able |
| `POST /samples/{sample_id}/reject` | review | same outbox path as approve; `disposition=fail` |
| `POST /webhook/disable` · `/enable` | the failure demo | disable stops the drainer, not the enqueue — the outbox is meant to fill up |
| `POST /trigger` | fallback generator | synthesises one sample without an analyzer |
| `GET /healthz` | compose healthcheck | broker connected + DB reachable |

**Keep the fallback generator.** With MQTT ingest, pattern 4 sits downstream of pattern 3's publish
path, which is authored but has never been watched on a broker (`00-status.md`). `POST /trigger`
means a broken analyzer cannot block this pattern.

### `services/lims/requirements.txt`

```
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
paho-mqtt>=2.1,<3
psycopg[binary]>=3.2,<4
```

All have manylinux wheels — nothing compiles, which matters on a conference network.

### `services/lims/Dockerfile`

Copy `services/opcua-cell-analyzer/Dockerfile`: `python:3.12-slim`, `PYTHONUNBUFFERED`, non-root
(`--uid 10004 lims`), `EXPOSE 8000`, `STOPSIGNAL SIGTERM`, `CMD ["python", "-u", "app.py"]`.

### `services/lims/README.md`

The one contract surface, the ACL reasoning, the two dedupe points, and the outbox.

### `docker-compose.yml`

Replace the `lims` placeholder comment at the bottom of the services block.

```yaml
  lims:
    build: ./services/lims
    container_name: icc26-lims
    restart: unless-stopped
    environment:
      BROKER_HOST: chariot
      MQTT_USERNAME: lims-bridge
      MQTT_PASSWORD: lims-bridge
      RESULT_TOPIC: icc26/site1/qc/analyzers/+/result
      PGHOST: postgres
      PGDATABASE: icc26
      PGUSER: icc26
      WEBHOOK_URL: ${LIMS_WEBHOOK_URL:-https://ignition:8043/system/webdev/icc-2026/lims/sample-result}
      WEBHOOK_SECRET: ${LIMS_WEBHOOK_SECRET:-icc26-webhook-secret}
      WEBHOOK_MAX_ATTEMPTS: ${LIMS_WEBHOOK_MAX_ATTEMPTS:-5}
      GENERATOR_INTERVAL_S: ${LIMS_GENERATOR_INTERVAL_S:-0}   # 0 = off; POST /trigger still works
      LOG_LEVEL: ${LIMS_LOG_LEVEL:-INFO}
      TZ: ${TZ:-America/Chicago}
    ports:
      - "${LIMS_PORT:-8000}:8000"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks: [icc26]
```

No `depends_on: chariot` — reconnect-with-backoff is in-app, per the conventions. The gateway
certificate is self-signed, so the container needs `ignition/certificates/icc26-ignition.crt`
mounted and trusted, **or** `WEBHOOK_URL` pointed at `http://ignition:8088/...`. Note that compose
binds 8088 to `127.0.0.1` on the *host* only; container-to-container traffic on the `icc26` network
is unaffected. Prefer the mounted certificate — "we turned TLS verification off for the demo" is a
bad sentence to say on a stage.

## Ignition resources

**One edit to pattern 3, first.**
`ignition/projects/icc-2026/ignition/script-python/opcua_event/code.py` does not currently set
`meta.correlation_id`. Add it inside the `meta` dict so the chain is traceable:

```python
"correlation_id": _value(by_name["sample_id"]),
```

`sample_id` is already unique per run and already the join key the LIMS uses, so it needs no new
identifier. Then `python tasks.py scan`.

**WebDev POST resource.** The 8.3 discriminator is `resource-type` in `config.json`, value
`python-resource`. A guessed `resourceType: python` mounts the URL but every GET/POST returns
`500 Unknown resource factory:` with an empty name. Method bodies live in `doGet.py` / `doPost.py`.
`lims_webhook` uses `logger.infof` / `logger.warnf` — LoggerEx is not Python logging.

Behaviour:

| Condition | Response | Publishes |
|---|---|---|
| Valid secret, new idempotency key | `200` | yes |
| Missing or wrong secret | `401` | no |
| Idempotency key already seen | `409` | **no** |
| Well-formed but unparseable body | `400` | no |

The `409` is not decoration. An outbox delivers **at least once**, so a redelivery after a response
was lost in flight is normal operation, and the receiver is where exactly-once is manufactured.

**Script module `lims_webhook`** in the same project: validate the shared secret, dedupe, build the
envelope, `system.cirruslink.transmission.publish("chariot_broker", ...)`. Jython 2.7 — no
f-strings, no type hints.

Dedupe is a **module-level bounded dict** (last ~500 keys), not a table. A gateway restart loses
the window, which is honest and acceptable; a durable version needs the `ICC26` JDBC datasource,
which does not exist yet — and note the `pg_db` look-alike trap in
[`../00-architecture.md` § *Postgres*](../00-architecture.md). Since pattern 4 no longer blocks anything, adopting that
datasource later is a small separate change rather than a scheduling risk.

## MQTT user and topics

| | |
|---|---|
| Subscribes | `icc26/site1/qc/analyzers/+/result`, QoS 1, as `lims-bridge` |
| Publishes | **nothing.** `publishTopics: []` |
| Ignition publishes | `icc26/site1/qc/lims/sample-result` via Transmission as `ign-transmission` |

### Topic, and the wart we are not fixing

`../00-architecture.md` flags `qc/lims/sample-result` as putting a software system in the
line-or-cell slot, and says to revisit at spec 04. Revisited, and **kept as-is.**

The doc's own precedent points the other way — `bes.batch_event` published to
`upstream/br-201/batch/event` and named its source system in the payload, and by that rule a sample
result drawn from BR-201 belongs under BR-201 too. It is the better address. But the topic is
referenced by an ACL entry and by pattern 7's subscription, and the conference is four
weeks out. Changing it buys correctness in a document and risks the demo.

So it stays, and becomes a spoken aside instead: *here is a violation of our own rule that we
found, could justify fixing, and chose not to fix this close to a deadline.* That is more useful for
an audience to hear than a namespace with no scars in it.

Note that the reversal made this **less** defensible, not more: `qc/lims/` was easier to justify
when three patterns keyed off one topic. Now one pattern does, and the only thing holding the
address in place is the calendar. Say so.

## Envelope

Per `../00-architecture.md` § *Payload envelope*. One message per sample.

```json
{
  "ts": "2026-08-19T14:03:22.145Z",
  "seq": 1041,
  "source": { "id": "lims", "type": "lims" },
  "meta": {
    "mechanism": "webhook",
    "ingest_ts": "2026-08-19T14:07:01.002Z",
    "correlation_id": "S-2026-0819-014"
  },
  "values": {
    "sample_id": "S-2026-0819-014",
    "batch_id": "B-2026-0042",
    "collected_at": "2026-08-19T14:03:22.145Z",
    "analyst": "mnorris",
    "disposition": "pass",
    "results": [
      { "analyte": "glucose",    "value": 4.21,  "uom": "g/L" },
      { "analyte": "lactate",    "value": 1.08,  "uom": "g/L" },
      { "analyte": "osmolality", "value": 312.0, "uom": "mOsm/kg" }
    ]
  }
}
```

`ts` is `collected_at`, not the approval instant — the event being described is the measurement.
The approval instant is `meta.ingest_ts`, and the gap between the two is visible on stage, which is
the point of the whole pattern.

## Empirical checkpoints

Falsifiable, in order. Do not proceed past a red one.

1. **Pattern 3 is actually on the broker.** `mosquitto_sub` on
   `icc26/site1/qc/analyzers/cell-analyzer-01/result`, trigger `ESMScheduleAnalysis`, exactly one
   message per completed sample. It has never been watched (`00-status.md`).
2. `correlation_id` is present in that message.
3. Nuke, reseed, confirm the schema: `status`, `verified_at`, `uq_sample_analyte` and
   `lims.webhook_delivery` all exist.
4. `lims-bridge` connects and appears in Chariot's client list with **its own username** — not
   anonymous, which is the trap MQTT Engine is currently in.
5. One analyzer message → three rows, all `status='received'`.
6. **Redelivery is a no-op.** Republish the same captured message; row count does not change.
7. Approve → three rows flip to `verified` with `verified_at` and `analyst`; **one** outbox row;
   **one** message on `icc26/site1/qc/lims/sample-result`, `mechanism` is `webhook`,
   `disposition` is `pass`.
8. **Reject publishes `disposition=fail`.** Same topic, same outbox path, `analyst` set. Not silence.
9. Replay a delivered idempotency key by hand → `409`, and **no** second message.
10. Wrong secret → `401`, no message.
11. **The outbox survives a restart.** Disable the drainer, approve two samples, `docker restart
    icc26-lims`, re-enable. Both deliver. This is the whole argument for the outbox and it is the
    one checkpoint that cannot be faked by a retry loop in memory.
12. **Approval is atomic.** Kill the container mid-approval (or force an error between the row
    update and the outbox insert) and confirm no sample ends up `verified` with no outbox row.

## Copy-pasteable verification

Terminal 1 — watch both topics, so the chain is visible end to end:

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/qc/analyzers/+/result' -t 'icc26/site1/qc/lims/sample-result' -v
```

Terminal 2:

```powershell
# Synthesise a sample without waiting on the analyzer
curl.exe -X POST http://localhost:8000/trigger

# Release it (or click Approve at http://localhost:8000/)
curl.exe -X POST http://localhost:8000/samples/S-2026-0819-014/approve -d "analyst=mnorris"
```

Expected: a message on the analyzer topic at trigger time, then **nothing** on
`qc/lims/sample-result` until the approve call, then exactly one message.

```powershell
docker exec -it icc26-postgres psql -U icc26 -d icc26 -c `
  "SELECT sample_id, analyte, value, status, verified_at FROM lims.sample_result ORDER BY id DESC LIMIT 10;"

docker exec -it icc26-postgres psql -U icc26 -d icc26 -c `
  "SELECT sample_id, attempts, state, last_error FROM lims.webhook_delivery ORDER BY id DESC LIMIT 10;"
```

## The failure demo

This replaces the convergence set-piece, and it needs to be rehearsed because it is now the only
argument this pattern makes about durability.

1. Approve a sample. One message, `mechanism: webhook`. Normal.
2. **Naive delivery, broken.** Point `WEBHOOK_URL` at a closed port with the outbox drainer
   disabled so nothing retries, and approve. The result is verified and released inside the LIMS,
   and the backbone never hears about it. Show the row, then show the silent topic.
3. **The outbox, working.** Re-enable the drainer. Restart the gateway. The queued delivery lands
   on the same topic, minutes late, with `attempts > 1` — and checkpoint 11 says it survives a
   restart of the *sender* too.
4. Say the line: what we just built to make a webhook trustworthy is a worse copy of the write-ahead
   log Postgres has had the whole time. Which is pattern 5.

## Gotchas, as found

| Symptom | Cause | What actually worked |
|---|---|---|
| WebDev `402` | two-hour trial lapsed | `GET /data/api/v1/trial` → reset in Gateway UI → Config → Licensing. `tasks.py` must not write trial state |
| WebDev `500 Unknown resource factory:` | `config.json` used `resourceType: python` | `"resource-type": "python-resource"` |
| POST 500 after a successful publish | `logger.info("… %s", key)` | `logger.infof` / `logger.warnf` |
| TLS error following `:8088` → `:8043` | self-signed cert, SAN is `localhost` only | mount the public cert, `CERT_REQUIRED`, `check_hostname = False` |
| Two rows from a live analyzer sample, not three | osmometer unfitted, `osmo` is null | a null analyte produces **no row**. `/trigger` synthesises all three |
| Second `/trigger` in the same second is a no-op | sample id is `S-%Y%m%d-%H%M%S` | wait a second, or use a live analyzer sample |
| `mqtt-users.json` edit does nothing | Chariot seeds users on first run only | `PUT /mqttusers/lims-bridge` against the running broker, or nuke |

## Deviations, knowingly

| Shortfall | Why it is acceptable here |
|---|---|
| Bare shared secret, not an HMAC over the body | Demo-grade committed credentials are an accepted trade (`00-master-plan.md`). An HMAC is the correct answer and is worth one sentence on stage |
| `UNIQUE (sample_id, analyte)` | A real LIMS repeats tests. Simplifies the demo's ingest dedupe to one constraint |
| Ignition-side dedupe is in-memory and lost on restart | Avoids depending on the `ICC26` datasource, which does not exist yet |
| The instrument operator (`values.operator`) is dropped | No column for it; `analyst` is the approver, a different person and the one an audit trail cares about |
| `qc/lims/sample-result` still names a system in the line-or-cell slot | Kept for schedule reasons, and *less* defensible than it was. See above |
| **Cut:** `GET /results?since_id=N`, `GET /results/latest`, the Debezium-tailed insert | Patterns 6, 7 and 5 respectively, all of which now have their own sources. The surfaces would have had no consumers |
| **Cut:** `release_seq` and the three-watermark demo | Pattern 6 no longer polls the LIMS |
| **Remaining 2026-08-23:** reject is still silent in the running service | Spec now requires `disposition` pass/fail on both review outcomes; implement before pattern 7 |

## Closing step

Write `docs/04-lims-webhook.md`, the talk-track doc. It will be the **first** of them — `docs/`
currently holds only `00-architecture.md` — so there is no house style to match yet and this one
sets it.

Then update, in this order:

- `00-status.md` — the built/not-built table only; durable findings go to `../00-architecture.md`
  and sequencing to `00-master-plan.md` § *Order*.
- `services/README.md` — the `lims/` row still reads *implementation undecided*, and § *The LIMS
  contract* still describes four surfaces.
- `../00-architecture.md` — record that `meta.correlation_id` has a working first user, and that
  the retired surfaces are retired rather than pending.
- `compose/postgres/initdb/02-schema.sql` and `04-cdc.sql` — comments: pattern 4 only on
  `lims.sample_result`; pattern 5 CDC-tails `bes.batch_event`.
