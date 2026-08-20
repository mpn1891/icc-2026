-- Live apply of pattern-4 schema onto an already-initialized volume.
-- initdb/02-schema.sql is the source of truth for a nuke; this is the same
-- change, written as ALTER/CREATE so we do not have to destroy ign-data.
-- Safe to run more than once.

ALTER TABLE lims.sample_result DROP CONSTRAINT IF EXISTS sample_result_batch_id_fkey;
ALTER TABLE lims.sample_result ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'received';
ALTER TABLE lims.sample_result ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE lims.sample_result DROP CONSTRAINT IF EXISTS uq_sample_analyte;
ALTER TABLE lims.sample_result ADD CONSTRAINT uq_sample_analyte UNIQUE (sample_id, analyte);
CREATE INDEX IF NOT EXISTS ix_sample_result_status ON lims.sample_result (status, collected_at);

CREATE TABLE IF NOT EXISTS lims.webhook_delivery (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id   text NOT NULL UNIQUE,
    payload     jsonb NOT NULL,
    attempts    int  NOT NULL DEFAULT 0,
    state       text NOT NULL DEFAULT 'pending',
    last_error  text,
    next_try_at timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_webhook_delivery_due ON lims.webhook_delivery (state, next_try_at);

GRANT ALL PRIVILEGES ON TABLE lims.webhook_delivery TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA lims TO icc26;
GRANT SELECT ON TABLE lims.webhook_delivery TO cdc;
