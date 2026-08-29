# 06 — Poll of a MET ONE particle counter

> **Supersedes the pattern-6 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> Written 2026-08-29 from [`../reference/particle_counter_sim.md`](../reference/particle_counter_sim.md)
> — the vendor API — plus the eight decisions taken that day, recorded in *Decisions* below.
> Talk track (`../talk-tracks/06-poll.md`) is written as the closing step, once this is
> broker-verified.
>
> ## Built and broker-verified 2026-08-29 — except the timer
>
> This document was written **before** the build, unlike
> [`05-cdc-batch-event.md`](05-cdc-batch-event.md), so every checkpoint was a prediction. They
> are now measured; see *Checkpoints*. **Everything runs except the gateway timer script**,
> which is UI-first because its file format is unknown — `metone_poll.poll()` was driven by hand
> for the verification below.
>
> **Three predictions were wrong, and the corrections are inline below rather than quietly
> applied:**
>
> 1. `system.eventstream.publishEvent` **cannot take a dict** — it coerces its data argument to
>    String. The poll publishes `system.util.jsonEncode(record)` (§ *The poll, step by step*).
> 2. **Value persistence is a tag-*provider* setting in 8.3, not a per-tag one**, and the
>    `default` provider already has it. So the watermark tags persist, and so does everything
>    else in the provider (§ *The UDT*).
> 3. **`UNIQUE (device_id, sequence_number)` was the wrong dedupe key.** Sequence numbers restart
>    at 1 when the simulator restarts — which is the stale-cursor demo — so keying on them makes
>    every reading of a fresh run collide with the old run's rows and vanish. The key is the
>    vendor's own analysis uuid (§ *Schema*).
>
> The thing most likely to lose an afternoon did not: `system.net.httpClient` POSTs GraphQL to
> the self-signed endpoint with a bearer header from gateway scope, with
> `bypass_cert_validation=True`, exactly as guessed.

| | |
|---|---|
| **Mechanism** | `poll` |
| **Signal contributed to the spine** | environmental excursion status at (or nearest to) the sample instant |
| **Topic** | `icc26/site1/qc/analyzers/particle-counter-01/result` — QoS 1, **retain false** |
| **Instrument** | `services/sim-metone` — GraphQL over HTTPS `:8443`, JWT auth; operator touchscreen on `:8089` |
| **Acquisition** | Ignition **gateway timer**, 30 s, → `metone_poll.poll()` |
| **Store** | `em.reading` in the `icc26` database, through the `ICC26` JDBC datasource as user `icc26` |
| **Publish** | Event Stream `06_poll/metone-result` → Transmission as `ign-transmission` |

## Decisions, 2026-08-29

Taken before writing, so no section below re-argues them.

| | Decision |
|---|---|
| The API | **Vendor-faithful.** `particle_counter_sim.md` is treated as a transcribed vendor surface, like [`../reference/novaflex2-opcua-model.md`](../reference/novaflex2-opcua-model.md) is for the Nova. We build the simulator from it and **add nothing to it** |
| Excursion | Computed **by the poll script at ingest**, on **raw channel counts** against a configured threshold — not normalised to /m³ |
| Forcing one | An **operator touchscreen on `:8089`**, styled as the instrument's own panel |
| Store | **`em.reading` in `icc26`**, written by the poll script, shaped to match `bes.batch_event`'s lookup |
| Who starts sampling | **The operator, on `:8089`.** Nothing free-runs; Ignition never mutates |
| Cadence | **10 s sample duration, 30 s poll**, the interval in a tag so it can be widened live |
| Location | **Set by the operator on `:8089`**, carried in the vendor's `deviceName`, stamped by the poll into `values.location` |
| Watermark | Two **memory tags with value persistence = database**, so they survive a gateway restart and can still be cleared by hand in Tag Explorer |
| Device id | The sim reports `particle-counter-01` directly (`DEVICE_ID` in compose) |

## The claim, and the thing that makes it true

**Nothing pushes. The instrument does not know Ignition exists, and Ignition finds out on a
timer.** That is the entire acquisition, and the honest consequence is a **detection gap**: for
up to one poll interval plus one sample duration, the room can be out of spec and the backbone
can be silent about it. Every other pattern in this stack learns things when they happen;
this one learns them when it next looks.

The gap is **characterized**, which is the GxP hook: not "we might miss something" but "we
sample for 10 seconds, we look every 30, so a reading is on the backbone within 40 seconds of
the air being dirty, worst case." Put the interval on screen, then ask the room what the number
would have to be before it went in an assessment.

**Same relay shape as pattern 3, different acquisition.** Both end in an Event Stream handing a
document to Transmission. Pattern 3's trigger is the instrument completing a run; pattern 6's is
a clock. Per [`../demo-through-line.md`](../demo-through-line.md) § *Still open*, name the Event
Stream → Transmission relay **once** on stage — patterns 3, 6 and 7 all use it and saying it
three times spends the audience's attention on plumbing.

## The vendor API, and the one real finding in it

GraphQL over HTTPS, JWT bearer auth, cursor pagination. Read
[`../reference/particle_counter_sim.md`](../reference/particle_counter_sim.md) for the schema;
what follows is what it means for a poller.

### The cursor is a watermark, and the vendor did not call it one

`getSamples(cursor, limit)` returns samples **after** the bookmark, oldest-first, plus a fresh
bookmark. The example cursor in the doc, `eyJpZCI6MX0=`, is base64 of `{"id": 1}` — a keyset
marker, not an offset:

| Poll | Instrument holds | We send | Server returns | We store |
|---|---|---|---|---|
| first ever | 1,2,3 | *no cursor* | 1,2,3 · next→`after 3` · `hasMore false` | `after 3` |
| +30 s | 4,5,6 added | `after 3` | 4,5,6 · next→`after 6` · `hasMore false` | `after 6` |
| +60 s | nothing new | `after 6` | *empty* · next→`after 6` | unchanged |
| +90 s | 7…40 added | `after 6` | 7…(6+limit) · **`hasMore true`** | call again immediately |

**`hasMore: true` means the server truncated at `limit`**, so the poll calls again with the
cursor it was just handed, and again, until `hasMore` is `false`. Two loops: an inner walk
draining the backlog, an outer timer every 30 s.

Because new analyses get ever-increasing ids appended to the end, *"everything after bookmark
42"* and *"everything new since I last looked"* are the same sentence. **Paging through a static
list and following a growing one are the identical operation** — the vendor shipped a change
feed and documented it as pagination. That is the sentence to say on stage, and it is why
staying vendor-faithful costs this pattern nothing.

### The trap that comes with it

Restart the simulator and the buffer regenerates: ids start at 1 again while the stored bookmark
still says 45. The server answers *"nothing after 45"* — correctly — and **the poll goes quiet
forever with every health check green.** The gateway is up, the timer fires, the HTTP call
returns 200, the tag values do not change, and nobody notices until somebody asks why the room
has no readings.

**Keep this.** It is a better failure demo than stalling the poll, because the poll is working
perfectly. Guard against it in production terms with the `last_sequence` dedupe tag below, and
recover on stage by clearing the cursor tag in Tag Explorer.

### What we do not add to the API, and what each absence costs

The rule is [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md)'s: the vendor's
surface is the vendor's, and the awkwardness stays on our side of the boundary.

| Not added | What it would have made easy | What we do instead |
|---|---|---|
| `since_id` / `since_time` on `getSamples` | a one-call poll with no state | walk the cursor; keep the watermark ourselves |
| `status` on the record | the excursion flag would arrive with the data | the poll script computes it (§ *Excursion*) |
| a `location` field | the room would be the instrument's own fact | the operator sets it; it rides in `deviceName` |
| a "give me everything since restart" reset | the stale-cursor trap would not exist | keep the trap; clear the tag by hand |

`startSampling`, `stopSampling` and `clearSamples` stay in the schema and **Ignition never calls
any of them.** The vendor shipped a control surface we deliberately do not touch — the same
change-control boundary pattern 3 makes with the Nova's 104 writable bits.

## The instrument — `services/sim-metone`

New service. Python, schema-first GraphQL, self-signed TLS, and a touchscreen.

```
services/sim-metone/
  particle_sim/
    __init__.py
    __main__.py        # CLI entry point: python -m particle_sim --port 8443
    server.py          # HTTPS + GraphQL (ariadne + uvicorn), and the :8089 panel
    schema.py          # SDL from particle_counter_sim.md, verbatim, + resolvers
    auth.py            # JWT mint/verify, admin role for clearSamples
    data.py            # sample generation, the rolling buffer, the dirty-room mode
    config.py          # env + CLI flags
    panel.html         # the operator touchscreen
  requirements.txt     # ariadne, uvicorn[standard], PyJWT, cryptography
  Dockerfile
  README.md
```

Follow `services/lims/Dockerfile`: `python:3.12-slim`, non-root uid, `STOPSIGNAL SIGTERM`,
wheels only so nothing compiles on a conference network. The self-signed cert is generated at
**container start**, not at build, so a rebuilt image does not ship a fixed key.

**Keep `python -m particle_sim --port 8443` working**, because the reference doc says it does.
A vendor README that lies about its own quickstart is a bug in the transcription.

### Compose

```yaml
  sim-metone:
    build: ./services/sim-metone
    container_name: icc26-sim-metone
    restart: unless-stopped
    environment:
      DEVICE_ID: ${METONE_DEVICE_ID:-particle-counter-01}
      DEVICE_NAME: ${METONE_DEVICE_NAME:-USP Suite A - BR-201 sample port}
      DURATION: ${METONE_DURATION:-10}   # vendor default is 60; 10 is ours, and visibly ours
      CHANNELS: ${METONE_CHANNELS:-0.3,0.5,1,3,5,10}
      SEED_SAMPLES: ${METONE_SEED_SAMPLES:-0}   # nothing exists until somebody presses Start
      LOG_LEVEL: ${METONE_LOG_LEVEL:-INFO}
    ports:
      - "${METONE_API_PORT:-8443}:8443"
      - "${METONE_PANEL_PORT:-8089}:8089"
    volumes:
      - metone-config:/config            # sample point + room + run state survive a restart
    healthcheck:                         # the PANEL, not the API
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8089/healthz', timeout=3)"]
    networks: [icc26]
```

Both host ports are free: Chariot holds 8883/8444/8090/8081, Ignition 8088/8043, Debezium 8083,
the valves 8085/8086, the Nova 8087.

**The healthcheck probes the panel, not the GraphQL endpoint**, because the API's certificate is
self-signed and generated at container start — probing it would mean shipping a `curl -k` into
the image to learn something the panel already answers. The check that actually matters for this
pattern is not liveness at all but *is the buffer non-empty*, and that one lives in
`tasks.py health`.

### The touchscreen on `:8089`

Every demo surface in this stack is somebody's real product screen — the two valve config pages,
the Nova's sample login, the LIMS review. This is the instrument's own panel, and it carries
exactly four things:

| Control | Why it exists |
|---|---|
| **Start / Stop sampling** | The operator starts the instrument. Nothing free-runs, for the same reason the Nova stopped free-running on 2026-08-26 |
| **Sample point** | Free text, defaults from `DEVICE_NAME`, persisted to `/config`. This is the location, and the operator owns it |
| **Room condition: clean / dirty** | The excursion. A physical gesture on stage rather than a `curl` |
| **Live readout** | Last analysis, its counts, seconds to the next one. Proves the instrument is running independently of whether Ignition is looking |

**`SEED_SAMPLES: 0` makes "press Start" a pre-show step that can be forgotten.** Mitigate, do
not remove: the panel shows a large idle banner when the buffer is empty, and `tasks.py health`
**now** reports the run state and the buffer count — both built. The forgotten-Start symptom is
worth naming, because it is why that check exists: a poll against an empty buffer looks exactly
like the stale-cursor failure from the outside. Green everywhere, nothing on the wire. The alternative — seeding 50 historical samples —
would put readings on the backbone that nobody in the room saw the instrument take.

**Location rides in `deviceName`, and that is an overload.** The vendor record has no location
field, and `deviceName` is the only free text in it. A portable counter labelled with its
sampling point is what actually happens in the field, so it is defensible; it is still us
putting our meaning in the vendor's box, and the spec says so rather than hiding it. If the
vendor doc ever grows a `location`, move it and delete this paragraph.

## Excursion — raw counts, and what raw costs

The poll script compares the **`≥0.5 µm` channel count** against
`config/excursion_threshold` on the UDT and sets `values.status ∈ normal | excursion`.

**One channel, not two — corrected 2026-08-29 when the arithmetic was done.** This section
originally said the poll compares `≥0.5 µm` *and* `≥5.0 µm` against the same tag, which the
UDT's own listing already contradicted by describing that tag as a "raw count on ≥0.5 µm". They
cannot share a number: at ISO 14644-1 Class 7 the limits are 352,000/m³ at 0.5 µm and 2,930/m³
at 5.0 µm, two orders of magnitude apart, so one threshold either never fires on the coarse
channel or fires constantly on the fine one. A second threshold tag would be a **second copy of
the cleanroom spec**, which is exactly what the derived-flags rule exists to prevent. So the
5 µm count is published and stored — `values.channels` and `em.reading.channels` both carry it —
and it is not compared. If a coarse-channel limit is ever wanted it needs its own tag, and a
sentence here arguing why two copies became defensible.

**The number is 1660**, measured (Open item 2, closed):

| | ≥0.5 µm counts per analysis, ~4.717 L |
|---|---|
| clean room | 113 – 137 |
| **threshold** | **1660** ≡ 352,000 /m³ ≡ ISO 14644-1 Class 7 / EU GMP Grade C at rest |
| dirty room | 3346 – 4350 |

An order of magnitude of headroom on each side, so nothing flaps, and a limit that is a real
grade rather than a number picked to sit between two histograms.

**This is Ignition's rule, not the instrument's**, and there is exactly one copy of it — the
same discipline as pattern 5's `QUALIFIED` tuple in `bes_batch`
([`../00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md)).
Pattern 7 reads `status` and must never compare counts itself.

**Raw counts bind the threshold to a fixed sample volume.** At 28.3 LPM for 10 s each analysis
draws ~4.7 L, so a raw count means something only while `DURATION` is 10. Change the duration
and every threshold in the system silently means something else, with nothing to catch it.
Two mitigations, both cheap:

- The threshold tag's documentation string records the duration it was chosen for **and** the
  equivalent per-m³ figure, so the number is defensible when somebody asks which grade this is.
- `values.total_volume_l` goes on the wire, so a consumer can normalise even though we did not.

The honest framing on stage: real EM systems compare to a limit in counts per cubic metre, and
that conversion — ~35× between a 28.3 L sample and a m³ — is the sort of arithmetic that is
correct in the vendor's software, correct in the LIMS, and wrong exactly once in the spreadsheet
in between.

## Ignition resources

| Resource | Path | How |
|---|---|---|
| UDT type `particle_counter` | `tag-type-definition/default/udts.json` | **built** — files + `tasks.py scan`, no restart |
| UDT instance `particle-counter-01` | `tag-definition/default/icc26/site1/qc/analyzers/udts.json` | **built** — files + `scan`. No parameters: nothing here is OPC-bound, so there is nothing to substitute |
| Script module `metone_poll` | `ignition/script-python/metone_poll/code.py` | **built** — files + `scan` |
| Event Stream `06_poll/metone-result` | `com.inductiveautomation.eventstream/event-streams/06_poll/metone-result/` | **built** — copied `03_opcua/novaflex-result` and changed the topic, the transform and the filter, exactly as predicted |
| **Gateway timer script** | `ignition/timer/…` — **schema unknown, the directory is empty** | **NOT built. UI first, then commit what `git status` reveals** |
| `ICC26` datasource | already exists, built for pattern 5 | — |

Everything above except the timer went in as files and applied with `python tasks.py scan` — no
gateway restart, and the UI was not opened once. The Event Stream copy worked with no surprises,
which is the second time that shortcut has paid (§ 6 of
[`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) records the first).

The tag path mirrors the topic exactly, which is why pattern 6 moved into `qc/analyzers` on
2026-08-25 — every other pattern has that property.

### The UDT

Nothing here is OPC-bound. The source is HTTP, so these are memory tags the poll script writes.

```
particle_counter/
  current/            written on every published analysis
    ts                 DateTime    completedAt from the instrument
    sequence_number    Int8        the vendor's own counter
    status             String      normal | excursion  <- the flag
    location           String      the operator's sample point
    operator           String      who the instrument says ran it
    total_volume_l     Float8
    ch_0_3 … ch_10_0   Int8        one per channel
  config/             ours to tune, on stage if need be
    poll_interval_s    Int4        30
    excursion_threshold Int8       raw count on >=0.5 um -- see § Excursion
    enabled            Boolean     the stall demo: uncheck it, keep the instrument running
  state/
    cursor             String      the watermark -- persists, see below
    last_sequence      Int8        the dedupe floor -- persists, see below
    last_poll_ts       DateTime
    last_error         String      the 401, the timeout, and NOT the stale-cursor silence
```

**Channel tag names are uniform: `ch_0_3 ch_0_5 ch_1_0 ch_3_0 ch_5_0 ch_10_0`.** The listing
above originally trailed off at `ch_10`; `metone_poll` derives the name from the channel size
(`"%.1f" % size`, dot replaced with underscore), so a rule that produces `ch_1_0` has to produce
`ch_10_0` as well.

**No tag historian is enabled on any of these.** `current/` is a live view — the latest
analysis, overwritten every 10 s — and nothing in it is what pattern 7 reads. The history is
`em.reading`, in SQL, and the reason is the shape of the record: an analysis is one row with six
channel counts, a status, a location and an operator, whereas tag history would store it as a
dozen independent scalar series that merely share a timestamp. Reassembling one analysis from
those means a `queryTagHistory` call per tag path plus alignment logic, and the alignment is
wrong exactly at a boundary — two analyses ten seconds apart is where it stitches values from
one onto the other. It also keeps pattern 7 on **one lookup idiom** for both of its flags
instead of a SQL one for pattern 5 and a historian one for this. Turning history on later is
additive and breaks nothing, but 07 should still read the table.

**`cursor` and `last_sequence` persist to the gateway's internal database**, so a gateway
restart resumes where the poll stopped rather than replaying the buffer — and they are still
memory tags, so clearing one in Tag Explorer forces a replay on stage. That is a better surface
than a Postgres row for both purposes. Verified with `docker restart icc26-ignition`: every tag
came back with its value and Good quality (CP3).

**Corrected: there is nothing to set per tag.** The decision table calls these "memory tags with
value persistence = database", which is how 8.1 is usually described. In 8.3 **value persistence
is a tag-*provider* setting**, and `tag-provider/default/config.json` already carries
`"valuePersistence": "Database"` — set long before pattern 6. So the watermark works, and it
works because of a provider-wide default rather than anything on these two tags. Two consequences
worth knowing: nothing in the UDT files declares the dependency, so somebody flipping the
provider setting breaks pattern 6 with no local sign of why; and **`current/` persists too**, so
after a gateway restart the live view shows the last analysis from before it — stale, but
carrying its own `ts`, so it is honest rather than misleading.

They are gateway-local state, not repo state. A `tasks.py nuke` clears them with `ign-data`,
which is correct: an offset into a buffer that no longer exists is worse than no offset — the
same reasoning already written on Debezium's `debezium-data` volume in `docker-compose.yml`.

### The poll, step by step

```
metone_poll.poll()                      # gateway timer, config/poll_interval_s
  if not config/enabled: return         # the stall demo
  cursor = read(state/cursor)
  last_sequence = read(state/last_sequence) if cursor else 0    # see below
  token = cached; re-auth on 401
  loop:
      page = getSamples(cursor, limit=50)
      for sample in page.samples:                  # oldest-first
          if sample.sequenceNumber <= last_sequence: continue     # the dedupe guard
          record = _build(sample)                  # + status, + location
          rows = _store(record)                    # INSERT ... ON CONFLICT DO NOTHING
          if rows:                                 # 0 = already stored, so already published
              publishEvent("icc-2026", "06_poll/metone-result",
                           jsonEncode(record), False)
              write(current/*)
          last_sequence = sample.sequenceNumber
      cursor = page.pagination.nextCursor
      if not page.pagination.hasMore: break
  write(state/cursor, cursor); write(state/last_sequence, last_sequence)
  write(state/last_poll_ts, now); write(state/last_error, "")
```

**`jsonEncode`, not the record.** The original said the poll "passes a plain dict and the
transform serialises it". It cannot: `system.eventstream.publishEvent` coerces its data argument
to String and a dict raises `TypeError: publishEvent(): 3rd arg can't be coerced to String`
(measured — pattern 3 never hit it because it passes a tag path). `build_document` decodes what
it is handed, so the envelope is still built in one readable place; only the wire format between
the poll and the transform changed.

**An empty cursor drops the dedupe floor to zero.** This is what makes the stage recovery *one*
gesture. Clearing `state/cursor` asks for a replay from the beginning; if `last_sequence` still
held 89 from before a simulator restart, every record of that replay would be skipped and the
"clear the cursor and it recovers" beat would not work. `em.reading`'s unique constraint is what
keeps a replay safe, so the floor can be dropped without risking a double publish. Verified: with
`last_sequence` deliberately left at 4, clearing the cursor alone published 3 fresh analyses.

Four things this ordering decides, deliberately:

1. **Store before publish.** A publish failure leaves a stored reading that never reached the
   backbone — recoverable, and pattern 7 can still find it. The reverse leaves a message on the
   wire that the store cannot corroborate, which is worse in a GxP argument. *This was observed
   before it was designed for:* the first end-to-end run failed on the `publishEvent` dict, and
   the row it had already written was sitting in `em.reading` afterwards, exactly as intended.
2. **The watermark advances only after the page is fully processed**, so a mid-page exception
   re-reads the page rather than skipping it. The `last_sequence` guard makes that safe, and
   `ON CONFLICT (device_id, analysis_id) DO NOTHING` makes it safe even across a gateway restart
   that loses the in-memory half. An insert affecting no rows suppresses the publish, because
   store-happens-first means an analysis already in the table is one the backbone already saw.
3. **The dedupe guard is per-record, not per-page**, because that is what catches the
   stale-cursor replay.
4. **`state/cursor` is written once per poll, not once per page**, so a partial walk leaves the
   watermark where the last complete page ended.

### Auth, and the cert

- **JWT.** `authenticate(username, password)` once, cache the token in a script-module global,
  and **re-authenticate on any 401** rather than tracking expiry. The sim issues short-lived
  tokens (~5 min) on purpose, so this path gets exercised on every demo instead of being dead
  code that fails in a year. It requires the simulator to answer an expired token with an HTTP
  **401**, not the GraphQL convention of `200` with an `errors` array — `_AuthGate` in
  `server.py` sits in front of the schema for exactly that reason, and `authenticate` is the one
  operation it lets past without a token. Measured: 315 s after the previous poll the cached
  token was rejected, the module logged *"token expired; re-authenticating"*, and the same poll
  went on to publish all 32 backlogged analyses (CP4).
- **The cert is self-signed**, generated at container start. Ignition trusts it with
  `system.net.httpClient(bypass_cert_validation=True)` — **confirmed the correct spelling on
  8.3.8**; `bypassCertValidation` is accepted too, and a wrong guess fails loudly with
  `TypeError: Got an unexpected keyword argument`. The gateway logs *"Bypassing certificate
  validation and hostname verification is highly insecure"* on every request through such a
  client. Say why on stage if asked: it is the right call for a simulator on a private compose
  network and the wrong one in a plant, and the honest version of that sentence is more useful
  than a truststore ceremony nobody would repeat.
- **Watch for pattern 5's redirect trap in reverse.** That build lost time to Ignition's
  `:8088 → :8043` redirect and a Java client that does not follow redirects
  ([`05-cdc-batch-event.md`](05-cdc-batch-event.md) § *The sink is HTTPS on `:8043`*). Here
  Ignition is the client and the sim is the server, so the direction is new — but if
  `--port 8443` ever serves plain HTTP, the same class of failure appears with a different
  message.

## Schema — `em.reading`

New schema `em`, one table. Full DDL goes in
[`../../compose/postgres/initdb/02-schema.sql`](../../compose/postgres/initdb/02-schema.sql);
the live-apply for an existing volume is a new
`compose/postgres/migrate-07-em-reading.sql`, following `migrate-06`'s conventions (run as
`postgres`, safe to run twice).

```sql
CREATE SCHEMA IF NOT EXISTS em AUTHORIZATION icc26;

-- Pattern 6's store. Written by the Ignition poll script (`metone_poll`) as it
-- publishes, so what pattern 7 reads is the same record the backbone saw --
-- including `status`, which is OURS and not the instrument's.
--
-- Deliberately the same lookup shape as bes.batch_event, so pattern 7 has one
-- query idiom for both of its flags rather than two:
--
--   SELECT status, occurred_at FROM em.reading
--    WHERE device_id = ? AND occurred_at <= ?
--    ORDER BY occurred_at DESC, id DESC
--    LIMIT 1;
CREATE TABLE em.reading (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       text NOT NULL,             -- 'particle-counter-01', the topic form
    analysis_id     text NOT NULL,             -- the vendor's uuid for the analysis
    sequence_number bigint NOT NULL,           -- the instrument's own counter
    location        text,                      -- operator-set sample point
    operator        text,
    status          text NOT NULL,             -- 'normal' | 'excursion'
    total_volume_l  numeric(10,3),
    channels        jsonb NOT NULL,            -- [{"size_um":0.5,"count":842}, ...]
    environment     jsonb,                     -- flow / temperature / humidity averages
    occurred_at     timestamptz NOT NULL,      -- completedAt, the instrument's clock
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device_id, analysis_id)            -- the dedupe guard, enforced
);
CREATE INDEX ix_em_reading_lookup ON em.reading (device_id, occurred_at DESC, id DESC);
```

**`analysis_id`, and why the unique key is not `sequence_number` — corrected 2026-08-29.** This
DDL originally read `UNIQUE (device_id, sequence_number)`, which is wrong in a way that only
shows up during pattern 6's own failure demo. **Sequence numbers restart at 1 when the simulator
restarts** — that is the whole stale-cursor trap — so after a restart every fresh reading
collides with a row from the previous run and `ON CONFLICT DO NOTHING` drops it in silence. The
poll would then run, report success, store nothing and publish nothing, immediately after the
operator cleared the cursor to recover. Measured on the live table: sequence numbers 1–4 each
appear three times across three simulator runs, with three distinct `analysis_id`s. The vendor's
own uuid is stable across restarts and is what an instrument-level dedupe should key on; the
sequence number stays as a column because it is the envelope's `seq`. It is deliberately **not**
on the wire — `values` is unchanged.

`occurred_at` is the **instrument's** `completedAt`, and `ingested_at` is when we found out.
The difference between those two columns *is* the detection gap, per row, queryable on stage:

```sql
SELECT occurred_at, ingested_at, ingested_at - occurred_at AS detection_lag, status
  FROM em.reading ORDER BY id DESC LIMIT 10;
```

> **`em.reading` must never join the `icc26_cdc` publication.** Pattern 5's exclusivity is the
> point of that publication naming exactly one table; adding this one would make pattern 6
> arrive by CDC as well and quietly turn two mechanisms into one. `tasks.py health` already
> asserts the membership, so a mistake here fails a check rather than a demo — that assertion
> was added on 2026-08-26 for `lims.sample_result` and it now covers this too.

## Payload contract

Full envelope, `mechanism=poll`.

```json
{
  "ts": "2026-08-29T14:03:22.145Z",
  "seq": 1041,
  "source": { "id": "particle-counter-01", "type": "analyzer" },
  "meta": { "mechanism": "poll", "ingest_ts": "2026-08-29T14:03:41.002Z" },
  "values": {
    "sequence_number": 1041,
    "status": "normal",
    "location": "USP Suite A - BR-201 sample port",
    "operator": "Admin User",
    "started_at": "2026-08-29T14:03:12.145Z",
    "completed_at": "2026-08-29T14:03:22.145Z",
    "total_volume_l": 4.717,
    "channels": [
      { "size_um": 0.3, "count": 254 },
      { "size_um": 0.5, "count": 140 },
      { "size_um": 1.0, "count": 36 },
      { "size_um": 3.0, "count": 7 },
      { "size_um": 5.0, "count": 1 },
      { "size_um": 10.0, "count": 0 }
    ],
    "flow_rate_lpm": 28.31,
    "temperature_c": 22.4,
    "humidity_pct": 45.2
  }
}
```

**`seq` is the instrument's `sequenceNumber`**, exactly as pattern 5's `seq` is the
`bes.batch_event` row id: the source system's own monotonic number, not one we invent.

**`ts` is the instrument's `completedAt`; `meta.ingest_ts` is when the poll found it.** Those
two differing by tens of seconds, visible in every message on `mosquitto_sub`, is the detection
gap on the wire without anybody having to explain it.

**This pattern carries the full envelope, and pattern 3 does not** — the Nova publishes `ts` and
`values` only ([`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) § 9). That is
not an inconsistency to tidy: pattern 3 relays the instrument's own document, while pattern 6's
document **contains fields the instrument never produced** — `status` and `location` are ours.
A record the site partly authored gets the site's envelope.

`meta.correlation_id` is absent. Pattern 6 has nothing to correlate to: it never sees a sample
id, and pattern 7 finds its reading by time, not by key
([`../00-architecture.md` § *Payload envelope*](../00-architecture.md)).

## Event Stream

Copy `03_opcua/novaflex-result` and change four things: the topic, the transform, the filter,
and the name. The `ignition.gatewayEvent` source and the Transmission handler are unchanged.

```json
  "handlers": [{ "type": "com.cirruslink.mqtt.transmission.gateway.mqtt.handler",
    "config": { "serverName": "chariot_broker",
                "topic": "icc26/site1/qc/analyzers/particle-counter-01/result",
                "qos": 1, "retained": false } }],
  "filter":    { "enabled": true, "userCode": "\treturn bool(event.data)\n" },
  "transform": { "enabled": true, "userCode": "\treturn metone_poll.build_document(event.data)\n" }
```

The poll script passes a plain dict and the transform serialises it, so the envelope is built in
one place a person can read, not in Event Stream user code.

## Checkpoints

Measured 2026-08-29 unless the row says otherwise. Two remain open and both wait on the gateway
timer, which is the user's to create.

| CP | Check | Result |
|---|---|---|
| **0** | `system.net.httpClient` POSTs GraphQL to an HTTPS endpoint with a self-signed cert and a bearer header, from gateway scope | **Pass, and the spec's guess was right.** `bypass_cert_validation=True` is the correct keyword on 8.3.8 (`bypassCertValidation` also accepted; `trust_all_certificates` raises `TypeError` naming the argument, so a wrong guess fails loudly). `authenticate` → 200, `getSamples` with a bearer header → 200 with `hasMore` honoured, junk token → **401**. `response.getJson()` decodes to Jython dicts with `long` integers. The gateway logs the insecure-bypass warning on every request. Measured from a WebDev resource, which is gateway-scoped like a timer; the transport question is answered, the timer question is CP1 |
| **1** | A gateway timer script resolves the `metone_poll` project module | **Open — the timer does not exist yet.** What *is* measured: a gateway-scoped project resource resolves project script modules (`bes_batch` and `metone_poll` both resolve from the WebDev handler), and **Gateway Scripting Project** has been `icc-2026` since pattern 5. A timer script is a project resource, so it should not need anything further. Unverified until the timer exists |
| **2** | The cursor walk: `hasMore true` drains, an empty page returns the same cursor, a restarted sim reproduces the stale-cursor silence | **Pass, all three.** 80 records at `limit=50` drained over 2 pages; an empty page returned the identical cursor; after `docker restart icc26-sim-metone` the poll returned **0 published, `state/last_error` empty, nothing on the wire** — working perfectly and blind. Clearing **only** `state/cursor` recovered it |
| **3** | Memory tag value persistence survives `docker restart icc26-ignition` | **Pass — and it is a tag-*provider* setting, not a per-tag one.** Every tag in the UDT came back with its value and Good quality across the restart, watermark included. Nothing needs setting per tag; `tag-provider/default/config.json` already carries `"valuePersistence": "Database"`. The design holds, but it rests on a provider-wide default that nothing in the UDT files declares |
| **4** | JWT expiry → 401 → re-auth → the poll continues without a gap | **Pass.** 315 s after the previous poll (TTL 300 s) the cached token was rejected, `metone_poll` logged *"token expired; re-authenticating"*, and the same poll published all **32** analyses that had accumulated. No gap, no lost record — and incidentally the stall demo, unplanned |
| **5** | One analysis → one `em.reading` row → one MQTT message. Never two, never zero | **Pass.** 96 analyses over the session → 96 rows → 96 messages on `mosquitto_sub`, with no duplicate `analysis_id`. Zero happens exactly once, deliberately: an insert that affects no rows suppresses the publish |
| **6** | `status` polarity | **Pass, both directions.** Dirty → sequence 84 published `excursion` at 3926 counts; clean → 87 published `normal` at 118. The flip is immediate rather than gradual because the simulator generates counts at completion instead of integrating over the sampling window — a simplification worth knowing before somebody reads meaning into a clean boundary |
| **7** | `occurred_at` vs `ingested_at` lag matches the configured cadence | **Partially measured; needs the timer.** Driving the poll by hand produced the right *shape* — a sawtooth of 4.4 / 14.4 / 24.4 / 34.4 s across one drained page, which is the 10 s sample duration stacked under one poll. Whether the steady-state maximum is ≤ ~40 s can only be measured against a real 30 s timer |
| **8** | `em.reading` is **not** in `icc26_cdc`; `tasks.py health` still green | **Pass.** The publication still names `bes.batch_event` only, before and after the migration. `migrate-07` carries a guard that drops `em.reading` from the publication if it is ever found there, and the `cdc` role is deliberately not granted `SELECT` on the `em` schema — a role that cannot read it cannot tail it |
| **9** | Two hours at 10 s sampling does not grow the sim's buffer without bound | **Pass, by construction and by test.** `BUFFER_MAX` is 500, ~83 minutes at 10 s. A throwaway container with `BUFFER_MAX=5` held at 5 records, evicted oldest-first, and logged each eviction with a running total. Eviction is safe for the cursor — keyset paging on "greater than N" does not care that earlier records left — but a poll that fell more than 83 minutes behind would skip records silently, which is why the eviction is a `WARNING` |

## Verification

Watcher in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/qc/analyzers/particle-counter-01/result' -v
```

0. `python tasks.py health` — the `sim-metone` line reports SAMPLING and a non-zero buffer.
   That check exists because `SEED_SAMPLES: 0` makes "press Start" a pre-show step that can be
   forgotten, and a forgotten Start looks exactly like the stale-cursor failure from the outside.
1. Open <http://localhost:8089>, set the sample point, press **Start**. Analyses begin every
   10 s; the panel shows them, and the idle banner clears.
2. Within 30 s, one message per analysis appears on the watcher — `mechanism=poll`,
   `status: "normal"`, `ts` behind `meta.ingest_ts` by up to a poll interval.
3. `SELECT status, occurred_at, ingested_at FROM em.reading ORDER BY id DESC LIMIT 5;` — one row
   per message, `status` matching.
4. Press **dirty**. The next analysis publishes `status: "excursion"`. Press clean; the one
   after returns to `normal`.

Verified in that order on 2026-08-29 with the poll driven by hand, since the timer does not exist
yet: 96 analyses, 96 rows, 96 messages, `status` correct on both transitions.

Proving the server before Ignition saw it was worth the hour it took — pattern 3's step 5,
applied. A throwaway Python client walked the cursor, forced the 401s and reproduced the
stale-cursor trap against `https://localhost:8443/graphql` first, so every failure afterwards
had exactly one candidate cause. The two Ignition-side surprises (`publishEvent` and the unique
key) were found in minutes rather than being confused with an API that might also be wrong.

**The failure demos — two, and the second is the better one:**

- **The stall.** Uncheck `config/enabled` (or widen `poll_interval_s` to 300). The instrument
  keeps sampling and the backbone goes quiet. Ask what happened between polls, then re-enable
  and watch the backlog arrive in one burst — the walk draining `hasMore`, visible on the wire.
- **The silent stale cursor.** `docker restart icc26-sim-metone`. The buffer regenerates from
  id 1, the stored bookmark still points past the end, and **the poll runs perfectly while
  publishing nothing.** Every health check green. Clear `state/cursor` in Tag Explorer and it
  recovers. This is the one to end the segment on: a monitoring system that is up, connected,
  authenticated, and blind.

  **Reproduced twice on 2026-08-29, and the recovery is genuinely one tag.** `state/last_sequence`
  is left alone; the poll drops its dedupe floor to zero whenever the cursor is empty, and
  `em.reading`'s unique key on the vendor's analysis uuid is what makes that safe. Note for
  stage: the room condition, the sample point and the run state all survive the restart, so the
  instrument comes back sampling and nothing has to be re-typed — only Ignition is blind.

## What pattern 7 gets from this

One query, the same shape as pattern 5's:

```sql
SELECT status, occurred_at FROM em.reading
 WHERE device_id = 'particle-counter-01' AND occurred_at <= ?   -- the sample-open instant
 ORDER BY occurred_at DESC, id DESC LIMIT 1;
```

`values.environmental_excursion` is `status = 'excursion'` on that row, and **pattern 7 does no
arithmetic** — it reads the flag pattern 6 computed, per the derived-flags rule.

Two things 07's spec has to decide and this one deliberately does not:

- **How stale is too stale.** At 10 s sampling the nearest reading is seconds away, but if the
  instrument was stopped the nearest could be an hour old and still be returned by that query.
  07 needs a tolerance, and a `null` environmental section — *a finding in the document, not a
  refusal to publish* ([`00-master-plan.md`](00-master-plan.md) § 07) — when nothing is inside it.
- **Nearest before, or nearest either side.** The query above takes the last reading at or
  before the sample instant. A reading 3 s *after* the valve opened is arguably better evidence
  than one 25 s before. Pattern 5's lookup has no such choice to make; this one does.

With this built, **master-plan Order item 4 is closed**: `bes.batch_event` stores pattern 5,
`em.reading` stores pattern 6, the LIMS review carries patterns 1 and 3, and 07 has no
unspecified store left in its way.

## Open items

1. **The gateway timer script is the one thing still missing.** Still true, and it is now the
   only thing between pattern 6 and a hands-off demo. `ignition/projects/icc-2026/ignition/timer/`
   exists and is empty. UI first, read what `git status` reveals, commit, then parametrize — the
   same route the `ICC26` datasource and the Gateway Scripting Project took in pattern 5. It
   needs to call `metone_poll.poll()` on `config/poll_interval_s` (30 s); everything downstream
   of that call is built and verified. CP1 and CP7 close when it exists.

   Until then, drive it by hand from the Designer script console:

   ```python
   metone_poll.poll()      # returns the number of analyses published
   ```

   That is a *Designer* scope call and it worked in gateway scope during the build, so it proves
   the poll and not the timer. The build's temporary harness — a WebDev resource that called
   `poll()` over HTTP — was deleted afterwards and is not in the repo.
2. ~~**The excursion threshold has no number yet.**~~ **Closed 2026-08-29: 1660** raw counts at
   ≥0.5 µm, which is 352,000/m³ at the 4.717 L a 10 s sample draws — ISO 14644-1 Class 7 / EU
   GMP Grade C at rest. Clean measured 113–137, dirty 3346–4350. The number, the volume it was
   chosen for and the per-m³ equivalent are all in the tag's documentation string, which is the
   one place somebody will look. § *Excursion*.
3. **`plant.equipment` has no `particle-counter-01` row.** Not needed by anything here — the
   location comes from the operator — but 07 may want it, and the
   `BR-201` / `br-201` case mismatch already logged in [`00-status.md`](00-status.md) is waiting
   there.
4. **Nothing enforces that only one poll runs.** Two gateways against one instrument would both
   advance their own cursors and publish duplicates. Out of scope for a demo; worth a sentence
   if anybody asks how this scales.
5. **The seven-mechanisms verification is already short.** Master plan § *Verification* step 4
   expects seven distinct `meta.mechanism` values in one `mosquitto_sub`; pattern 1 carries no
   `meta`, pattern 2 has no envelope, and pattern 3 as built publishes `ts` + `values` only. So
   the real count is four — 4, 5, 6 and 7. Recorded here because pattern 6 is the one that made
   it countable; fixing the claim belongs in the master plan, not in this spec.
6. **Talk track.** `../talk-tracks/06-poll.md`, written once this is broker-verified. It now is,
   so this is unblocked — and the closing beat writes itself: the stale cursor, recovered live by
   clearing one tag.
7. **New 2026-08-29: the watermark depends on a provider-wide setting nothing local declares.**
   `tag-provider/default/config.json`'s `"valuePersistence": "Database"` is what makes
   `state/cursor` survive a restart. Nothing in the UDT says so, so somebody changing that
   provider setting for an unrelated reason breaks pattern 6 with no local sign of why. Recorded
   rather than fixed: 8.3 offers no per-tag override to pin it with.
8. **New 2026-08-29: `metone_poll` is single-instrument by constant.** `BASE` and `DEVICE_ID`
   come from one module-level tag path, and `poll()` takes it as a default argument. A second
   counter means a second timer call with a different path, which works, but the module has not
   been exercised that way. The Event Stream topic is likewise a literal, not built from the
   device id the way `bes_cdc` builds pattern 5's — one instrument, one topic, and if a second
   ever appears that is the line to change first.

## Progress log

| Date | |
|---|---|
| 2026-08-23 | Pattern 6 re-sourced: a MET ONE HTTP API in `qc/analyzers`, not Modbus. Excursion flag added to the design |
| 2026-08-25 | Moved from `upstream/br-201` to `qc/analyzers`, so tag path and topic stay identical |
| 2026-08-29 | Vendor API arrived as [`../reference/particle_counter_sim.md`](../reference/particle_counter_sim.md). Eight decisions taken and this spec written. **Nothing built** |
| 2026-08-29 | **Built and broker-verified, same day, everything but the timer.** `services/sim-metone` (GraphQL over HTTPS + the :8089 touchscreen), the `em` schema and `migrate-07`, the `particle_counter` UDT and instance, `metone_poll`, and Event Stream `06_poll/metone-result`. 96 analyses → 96 `em.reading` rows → 96 messages on `icc26/site1/qc/analyzers/particle-counter-01/result`, `status` correct on both polarities, the stale-cursor trap reproduced twice and recovered by clearing one tag, and a real token expiry drove a re-auth mid-poll. `tasks.py health` gained the buffer check the spec asked for. **Three predictions in this document were wrong** — `publishEvent` refuses a dict, value persistence is a provider setting, and `sequence_number` is not a usable dedupe key — and all three are corrected inline above rather than quietly. The one predicted to cost an afternoon, `httpClient` against a self-signed cert, cost nothing. Four durable Ignition 8.3.8 facts went to [`../00-architecture.md`](../00-architecture.md) instead of here, because 07 will want them |
