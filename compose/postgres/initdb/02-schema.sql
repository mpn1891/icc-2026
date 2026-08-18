-- ─────────────────────────────────────────────────────────────────────────────
-- 02 — Demo schemas
--
-- Created up front, in step 1, so that later pattern work has somewhere to land
-- without another volume rebuild. Deliberately minimal: columns the patterns
-- actually need, nothing speculative.
-- ─────────────────────────────────────────────────────────────────────────────
\connect icc26

CREATE SCHEMA IF NOT EXISTS lims  AUTHORIZATION icc26;
CREATE SCHEMA IF NOT EXISTS mes   AUTHORIZATION icc26;
CREATE SCHEMA IF NOT EXISTS plant AUTHORIZATION icc26;

-- ── plant: physical model ────────────────────────────────────────────────────
-- Mirrors the ISA-95 topic namespace (see docs/00-architecture.md). Kept here so
-- pattern 7's aggregation script has real equipment metadata to join against
-- rather than hardcoded strings.
CREATE TABLE plant.equipment (
    equipment_id  text PRIMARY KEY,          -- e.g. 'BR-201', 'sample-valve-01'
    site          text NOT NULL,             -- 'site1'
    area          text NOT NULL,             -- 'upstream' | 'downstream' | 'qc' | 'utilities'
                                             -- Places, not systems. There is no 'mes' area:
                                             -- see docs/00-architecture.md § Topic namespace.
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
-- The single most important table in the demo. Patterns 4 (webhook), 5 (CDC) and
-- 6 (poll) all surface rows from THIS table, by three different mechanisms, onto
-- ONE topic: icc26/site1/qc/lims/sample-result
--
-- `id` is a monotonic bigint on purpose — pattern 6 watermarks on it, and part of
-- that pattern's talk track is why an id watermark beats a timestamp watermark.
CREATE TABLE lims.sample_result (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id     text NOT NULL,
    batch_id      text REFERENCES plant.batch(batch_id),
    analyte       text NOT NULL,             -- 'glucose' | 'lactate' | 'osmolality' | …
    value         numeric(12,4) NOT NULL,
    uom           text NOT NULL,
    collected_at  timestamptz NOT NULL,      -- when the sample was drawn
    created_at    timestamptz NOT NULL DEFAULT now(),  -- when the row appeared
    analyst       text
);

-- Pattern 6 polls `WHERE id > :watermark ORDER BY id`.
CREATE INDEX ix_sample_result_id_created ON lims.sample_result (id, created_at);
-- Supports the timestamp-watermark variant, so the demo can show both and
-- contrast their failure modes side by side.
CREATE INDEX ix_sample_result_created    ON lims.sample_result (created_at);

-- ── mes: batch lifecycle events ──────────────────────────────────────────────
-- Pattern 5's other CDC source. Discrete state transitions, which is what CDC is
-- genuinely good at capturing.
CREATE TABLE mes.batch_event (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id      text NOT NULL,
    event_type    text NOT NULL,             -- 'phase_start' | 'phase_end' | 'deviation' | …
    phase         text,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_batch_event_batch ON mes.batch_event (batch_id, occurred_at);

-- ── Grants ───────────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA lims, mes, plant TO icc26;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA lims, mes, plant TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA lims, mes, plant TO icc26;

-- Debezium needs to read the tables it decodes, and to own a publication.
GRANT USAGE  ON SCHEMA lims, mes, plant TO cdc;
GRANT SELECT ON ALL TABLES IN SCHEMA lims, mes, plant TO cdc;
ALTER DEFAULT PRIVILEGES IN SCHEMA lims, mes, plant GRANT SELECT ON TABLES TO cdc;
