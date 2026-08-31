-- ─────────────────────────────────────────────────────────────────────────────
-- 02 — Demo schemas
--
-- Created up front, in step 1, so that later pattern work has somewhere to land
-- without another volume rebuild. Deliberately minimal: columns the patterns
-- actually need, nothing speculative.
-- ─────────────────────────────────────────────────────────────────────────────
\connect icc26

CREATE SCHEMA IF NOT EXISTS lims  AUTHORIZATION icc26;
CREATE SCHEMA IF NOT EXISTS bes   AUTHORIZATION icc26;
CREATE SCHEMA IF NOT EXISTS plant AUTHORIZATION icc26;

-- ── plant: physical model ────────────────────────────────────────────────────
-- Mirrors the ISA-95 topic namespace (see docs/00-architecture.md). Kept here so
-- pattern 7's aggregation script has real equipment metadata to join against
-- rather than hardcoded strings.
CREATE TABLE plant.equipment (
    equipment_id  text PRIMARY KEY,          -- e.g. 'BR-201', 'sample-valve-01'
    site          text NOT NULL,             -- 'site1'
    area          text NOT NULL,             -- 'upstream' | 'downstream' | 'qc' | 'utilities'
                                             -- Places, not systems. There is no 'bes' area (nor
                                             -- 'mes'): docs/00-architecture.md § Topic namespace.
    line          text,                      -- 'pumpskid1'
    equipment_type text NOT NULL,            -- 'bioreactor' | 'analyzer' | 'sample-valve'
    description   text
);

CREATE TABLE plant.batch (
    batch_id      text PRIMARY KEY,          -- e.g. 'B-2026-0142'
    equipment_id  text NOT NULL REFERENCES plant.equipment(equipment_id),
    product       text NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    ended_at      timestamptz,
    status        text NOT NULL DEFAULT 'running'   -- running | complete | aborted
);

-- ── lims: the sample entry, and the results appended to it ───────────────────
-- Pattern 4's holding area. **The entry is opened by the sample valve, not by
-- the analyzer** (2026-08-26): `event/sample-complete` on pattern 1's backbone
-- creates one `lims.sample` row, and the Nova result appends analyte rows to it
-- later. The sample begins when material leaves the reactor, which is the rule
-- docs/00-architecture.md § *The sample id, and pattern 1 mints it* already set;
-- this is that rule expressed in the schema.
--
-- The valve event carries no `batch_id` — it does not know one. That column is
-- filled from the analyzer result when it attaches, and stays null on a sample
-- that never gets analysed.
CREATE TABLE lims.sample (
    sample_id         text PRIMARY KEY,      -- minted by the valve on the badge grant
    batch_id          text,                  -- from the analyzer result, not the valve
    -- Which vessel this was drawn from, topic form and lowercase (br-201).
    -- Pattern 7's join key into bes.batch_event and em.reading; parsed from the
    -- valve event topic by the bridge, because pattern 1's payload does not
    -- name the reactor. Not a foreign key to plant.equipment -- that table
    -- holds 'BR-201' in the wrong case and is not on pattern 7's path.
    equipment_id      text,
    badge_id          text,
    badge_holder      text,
    sample_start      timestamptz,           -- valve open-finish
    sample_completion timestamptz,           -- valve close-finish
    open_duration_s   numeric(8,2),
    cycle_result      text,                  -- normal | failed-to-seat | stroke-timeout
    cycle_count       bigint,
    -- Pattern 1 carries no `meta` and does not name itself, so the topic string
    -- is the only provenance there is. Store it rather than pretend otherwise.
    source_topic      text,
    status            text NOT NULL DEFAULT 'awaiting-analysis',
                      -- awaiting-analysis | received | verified | rejected
    created_at        timestamptz NOT NULL DEFAULT now(),
    analyst           text,                  -- the approver, not the badge holder
    verified_at       timestamptz
);

CREATE INDEX ix_sample_status  ON lims.sample (status, sample_completion);
CREATE INDEX ix_sample_created ON lims.sample (created_at);

-- One row per analyte the analyzer reported. Two id columns, and the split is
-- load-bearing:
--
--   `reported_sample_id` is what the instrument said, verbatim, and is NEVER
--   rewritten. A person types the valve's id into the Nova by hand, so it can
--   be typed wrong, and correcting a record by overwriting what the instrument
--   reported is exactly what a LIMS must not do.
--
--   `sample_id` is the entry this result is attached to. NULL means no entry
--   with that id exists yet: the result is parked, visible, and reattachable.
--   No FK, because an unmatched result still has to insert.
--
-- `UNIQUE (reported_sample_id, analyte)` is the whole QoS 1 ingest dedupe
-- (ON CONFLICT DO NOTHING). One-test-per-sample is a demo simplification; a
-- real LIMS repeats tests.
--
-- status / analyst / verified_at live on lims.sample now. Review is a decision
-- about a sample, not about a row of it.
CREATE TABLE lims.sample_result (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reported_sample_id text NOT NULL,        -- what the instrument said
    sample_id          text,                 -- the entry it belongs to; NULL = unmatched
    batch_id           text,
    analyte            text NOT NULL,        -- 'glucose' | 'lactate' | 'osmolality' | …
    value              numeric(12,4) NOT NULL,
    uom                text NOT NULL,
    collected_at       timestamptz NOT NULL, -- vendor SampleTime — the acquisition instant
    created_at         timestamptz NOT NULL DEFAULT now(),
    attached_at        timestamptz,          -- when it found its entry
    attached_by        text,                 -- null if it matched on arrival; analyst if reattached
    CONSTRAINT uq_reported_sample_analyte UNIQUE (reported_sample_id, analyte)
);

CREATE INDEX ix_sample_result_id_created ON lims.sample_result (id, created_at);
CREATE INDEX ix_sample_result_created    ON lims.sample_result (created_at);
CREATE INDEX ix_sample_result_sample     ON lims.sample_result (sample_id);

-- The outbox. One row per sample reviewed, not per result row: one review is
-- one delivery (pass or fail). Delivery state is not domain state, which is why
-- this is a separate table. Pattern 5 used to tail sample_result; attempt
-- counters on the result row would have made every retry a CDC event. Pattern 5
-- now tails bes.batch_event, so that hazard stays gone. The table still earns
-- its keep — you can query it, retry it, and put it on the approval screen.
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

-- ── bes: batch lifecycle events ──────────────────────────────────────────────
-- Pattern 5's CDC source. The writer is an Ignition tag event script
-- (`bes_batch`) fired by clicking `manual_advance` on the bioreactor UDT:
-- somebody types a batch_id, clicks the boolean, and the reactor steps
-- IDLE → CIP → SIP → INOC → GROWTH → HARVEST → IDLE. Debezium tails this table
-- as user `cdc`. **The writer publishes nothing.** That is the whole pattern:
-- if bes_batch ever grows a Transmission publish call, pattern 5 is gone.
--
-- Auto-cycling on a dwell was the 2026-08-23 design and was dropped 2026-08-26.
-- A manual advance is deterministic on stage — you park the reactor in GROWTH
-- before badging the valve, which is exactly what pattern 7's rehearsal needs.
--
-- Named `bes`, not `mes` (renamed 2026-08-23, before anything was built on it):
-- the writer stands in for a batch execution system specifically, and pattern
-- 5's verify step puts this table on screen right after the talk track says
-- "BES".
--
-- `operation`, not `phase` (renamed 2026-08-26, same rule, same reason). In
-- ISA-88 a *phase* is the smallest element that does process action -- "Add
-- Water", "Agitate". CIP / SIP / INOC / GROWTH / HARVEST are *operations*, one
-- level up. The tag you click, this column, the event_type values and the wire
-- field all say `operation`, so nothing on screen contradicts the sentence
-- just spoken.
CREATE TABLE bes.batch_event (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id      text NOT NULL,
    -- The topic form of the vessel id ('br-201'), taken from the tag path --
    -- NOT plant.equipment's 'BR-201'. `batch_data` lives on the bioreactor
    -- *type*, so br-202 has a live manual_advance button too; without this
    -- column a stray click over there would publish onto br-201's topic. The
    -- cdc-sink builds the topic from this value.
    equipment_id  text NOT NULL,
    event_type    text NOT NULL,             -- 'operation_start' | 'operation_end' | 'batch_end' | 'deviation'
    operation     text,                      -- NULL on batch_end: nothing is running
    -- payload.qualified_window is set HERE, at insert time, so the flag is in
    -- the WAL Debezium tails rather than computed in the sink. A flag added
    -- after the tail is a flag the CDC demo did not actually observe. See
    -- docs/00-architecture.md § Derived flags travel with the fact.
    --
    -- It means "sampling is qualified in the interval that BEGINS at
    -- occurred_at", which is why an operation_end row carries false even when
    -- the operation it closes was GROWTH: nothing is running after it.
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at   timestamptz NOT NULL DEFAULT now()
);

-- One click writes two rows (operation_end + operation_start) in one
-- transaction sharing one occurred_at, so pattern 7's "what was running at
-- time T" lookup has to tie-break on id:
--
--   SELECT operation, payload->>'qualified_window'
--     FROM bes.batch_event
--    WHERE equipment_id = ? AND occurred_at <= ?
--    ORDER BY occurred_at DESC, id DESC
--    LIMIT 1;
CREATE INDEX ix_batch_event_batch  ON bes.batch_event (batch_id, occurred_at);
CREATE INDEX ix_batch_event_lookup ON bes.batch_event (equipment_id, occurred_at DESC, id DESC);

-- ── em: environmental monitoring readings ────────────────────────────────────
-- Pattern 6's store, written by the Ignition poll script (`metone_poll`) as it
-- publishes -- so what pattern 7 reads is the same record the backbone saw,
-- including `status`, which is OURS and not the instrument's. The MET ONE
-- reports counts; the cleanroom limit is Ignition's rule and lives in exactly
-- one place, `config/excursion_threshold` on the particle_counter UDT.
-- docs/00-architecture.md § Derived flags travel with the fact that produced them.
--
-- Deliberately the same lookup shape as bes.batch_event, so pattern 7 has one
-- query idiom for both of its flags rather than two:
--
--   SELECT status, occurred_at FROM em.reading
--    WHERE device_id = ? AND occurred_at <= ?
--    ORDER BY occurred_at DESC, id DESC
--    LIMIT 1;
--
-- **em.reading must never join the icc26_cdc publication.** Pattern 5's
-- exclusivity is the point of that publication naming exactly one table; adding
-- this one would make pattern 6 arrive by CDC as well and quietly turn two
-- mechanisms into one. `tasks.py health` asserts the membership.
CREATE SCHEMA IF NOT EXISTS em AUTHORIZATION icc26;

CREATE TABLE em.reading (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       text NOT NULL,             -- 'particle-counter-01', the topic form
    -- The vendor's own uuid for the analysis. It is the dedupe key rather than
    -- `sequence_number`, and that is a correction from measurement, not a
    -- preference: sequence numbers restart at 1 when the instrument restarts, so
    -- keying on them makes every reading of a fresh run collide with the
    -- previous run's rows and vanish in silence -- exactly the failure the
    -- stale-cursor demo is supposed to RECOVER from.
    analysis_id     text NOT NULL,
    sequence_number bigint NOT NULL,           -- the instrument's own counter
    -- The operator's sample point, set on the instrument's touchscreen and
    -- carried in the vendor record's `deviceName` -- that record has no location
    -- field, and this is the overload, named rather than hidden.
    location        text,
    operator        text,
    status          text NOT NULL,             -- 'normal' | 'excursion' -- OURS
    -- The volume actually drawn. A raw count means something only against this,
    -- so it travels: a consumer can normalise to /m3 even though we did not.
    total_volume_l  numeric(10,3),
    channels        jsonb NOT NULL,            -- [{"size_um":0.5,"count":842}, ...]
    environment     jsonb,                     -- flow / temperature / humidity averages
    occurred_at     timestamptz NOT NULL,      -- completedAt, the instrument's clock
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    -- The dedupe guard, enforced. `metone_poll` also skips anything at or below
    -- its last_sequence watermark, but a restarted gateway replaying a page must
    -- not be able to double-insert. An insert that affects no rows is how the
    -- poll knows not to publish the analysis a second time.
    UNIQUE (device_id, analysis_id)
);

-- occurred_at is the INSTRUMENT's clock; ingested_at is when the poll found out.
-- The difference between those two columns is the detection gap, per row:
--
--   SELECT occurred_at, ingested_at, ingested_at - occurred_at AS detection_lag,
--          status FROM em.reading ORDER BY id DESC LIMIT 10;
CREATE INDEX ix_em_reading_lookup ON em.reading (device_id, occurred_at DESC, id DESC);

-- ── Grants ───────────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA lims, bes, plant, em TO icc26;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA lims, bes, plant, em TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA lims, bes, plant, em TO icc26;

-- Debezium needs to read the tables it decodes, and to own a publication.
-- `em` is deliberately NOT granted here: the cdc role has no business reading
-- pattern 6's store, and a role that cannot SELECT it cannot accidentally end up
-- tailing it. See the em.reading comment above.
GRANT USAGE  ON SCHEMA lims, bes, plant TO cdc;
GRANT SELECT ON ALL TABLES IN SCHEMA lims, bes, plant TO cdc;
ALTER DEFAULT PRIVILEGES IN SCHEMA lims, bes, plant GRANT SELECT ON TABLES TO cdc;
