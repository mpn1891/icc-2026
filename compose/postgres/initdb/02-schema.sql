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
-- Mirrors the ISA-95 topic namespace (see docs/00-architecture.md). Equipment
-- ids here are the same strings that appear in topics. Pattern 7 is TBD; this
-- table is still the physical model, not an aggregation join.
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

-- ── lims: sample results ── EXTRA. No consumer as of 2026-08-23 ──────────────
--
-- UNWIRED, NOT DROPPED. Pattern 4 was a LIMS until 2026-08-23 and is now a
-- NovaFlex HTTPS POST into an Event Stream, which touches no database at all.
-- The `lims` service is commented out of docker-compose.yml, `lims-bridge` is
-- gone from compose/chariot/mqtt-users.json, and nothing reads or writes either
-- table below.
--
-- They stay because dropping them means a volume rebuild, and the 05/06 work
-- needs one anyway — retire the whole schema in that pass, deliberately, rather
-- than adding a nuke to this one. Same filing as the retired vibration gateway:
-- on disk, wired to nothing, documented in docs/extra/lims-webhook-spec.md.
--
-- If you bring the LIMS back (it is the proven fallback for pattern 4 — see
-- docs/04-novaflex-webhook.md § Deviations), these are the tables it needs, and
-- the reasoning below is still correct:
--
-- Analyzer results land as status='received'; a human Approve flips them to
-- 'verified' and writes one outbox row in the same transaction.
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

-- The outbox. EXTRA, like the table above — nothing writes it since 2026-08-23.
-- Pattern 4 has no outbox by design: the instrument makes one POST attempt and
-- the failed POST is the demo, because building a durable retry queue here
-- would be building pattern 5 in the wrong place.
--
-- One row per sample approved, not per result row: one approval is
-- one delivery. Delivery state is not domain state, which is why this is a
-- separate table. Pattern 5 used to tail sample_result; attempt counters on the
-- result row would have made every retry a CDC event. That hazard is gone
-- (pattern 5 moved off this table) and the table still earns its keep — you can query
-- it, retry it, and put it on the approval screen.
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

-- ── mes: batch lifecycle events ──────────────────────────────────────────────
-- No consumer. Pattern 5 was going to CDC-tail this; it no longer does.
-- Kept so the physical model stays coherent and so pattern 5's spec can retire
-- it on purpose rather than leaving a table that looks live. 04-cdc.sql still
-- publishes it — retire that publication with the pattern-5 spec, not here.
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
