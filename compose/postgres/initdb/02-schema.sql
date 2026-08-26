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
-- Pattern 5's CDC source (2026-08-23). An Ignition timer writes phase changes
-- for BR-201 (CIP/SIP/INOC/GROWTH/HARVEST); Debezium tails this table.
-- 04-cdc.sql currently also names lims.sample_result — drop that table from
-- the publication when pattern 5 is built, so a LIMS review is not a CDC event.
--
-- Named `bes`, not `mes` (renamed 2026-08-23, before anything was built on it):
-- the writer stands in for a batch execution system specifically -- CIP → SIP →
-- INOC → GROWTH → HARVEST is an ISA-88 phase model -- and pattern 5's verify
-- step puts this table on screen right after the talk track says "BES".
CREATE TABLE bes.batch_event (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id      text NOT NULL,
    event_type    text NOT NULL,             -- 'phase_start' | 'phase_end' | 'deviation' | …
    phase         text,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_batch_event_batch ON bes.batch_event (batch_id, occurred_at);

-- ── Grants ───────────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA lims, bes, plant TO icc26;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA lims, bes, plant TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA lims, bes, plant TO icc26;

-- Debezium needs to read the tables it decodes, and to own a publication.
GRANT USAGE  ON SCHEMA lims, bes, plant TO cdc;
GRANT SELECT ON ALL TABLES IN SCHEMA lims, bes, plant TO cdc;
ALTER DEFAULT PRIVILEGES IN SCHEMA lims, bes, plant GRANT SELECT ON TABLES TO cdc;
