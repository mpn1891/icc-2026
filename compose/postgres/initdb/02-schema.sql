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

-- ── lims: sample results ─────────────────────────────────────────────────────
-- Pattern 4's holding area. Analyzer results land here as status='received'; a
-- human Approve flips them to 'verified' and writes one outbox row in the same
-- transaction. This is not "the single most important table in the demo" — that
-- comment dated from the convergence design, when patterns 4, 5 and 6 all
-- surfaced the same rows. Since 2026-08-19 only pattern 4 touches this table.
--
-- `batch_id` is free text, not a FK. The analyzer names batches the LIMS may not
-- have opened yet, and refusing the insert would drop a real result.
-- `UNIQUE (sample_id, analyte)` is a demo simplification: a real LIMS repeats
-- tests. It exists so QoS 1 redelivery is a no-op (ON CONFLICT DO NOTHING).
CREATE TABLE lims.sample_result (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id     text NOT NULL,
    batch_id      text,
    analyte       text NOT NULL,             -- 'glucose' | 'lactate' | 'osmolality' | …
    value         numeric(12,4) NOT NULL,
    uom           text NOT NULL,
    collected_at  timestamptz NOT NULL,      -- vendor SampleTime — the acquisition instant
    created_at    timestamptz NOT NULL DEFAULT now(),  -- when the row appeared
    analyst       text,                      -- the approver, not the instrument operator
    status        text NOT NULL DEFAULT 'received',  -- received | verified | rejected
    verified_at   timestamptz,
    CONSTRAINT uq_sample_analyte UNIQUE (sample_id, analyte)
);

CREATE INDEX ix_sample_result_id_created ON lims.sample_result (id, created_at);
CREATE INDEX ix_sample_result_created    ON lims.sample_result (created_at);
CREATE INDEX ix_sample_result_status     ON lims.sample_result (status, collected_at);

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
