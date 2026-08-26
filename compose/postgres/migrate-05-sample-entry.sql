-- Live apply of the 2026-08-26 sample-entry redesign onto an already-initialized
-- volume. initdb/02-schema.sql is the source of truth for a nuke; this is the
-- same change written as ALTER/CREATE so ign-data survives. Safe to run twice.
--
-- What changes: the sample entry becomes a row of its own, created by pattern 1's
-- event/sample-complete, and lims.sample_result becomes the analyte rows appended
-- to it. See docs/plans/04-lims-webhook.md.
--
-- Run it as `postgres`, not as `icc26`. initdb created these tables as the
-- superuser, so icc26 holds ALL PRIVILEGES but is not the owner, and
-- ALTER TABLE ... RENAME COLUMN is owner-only: as icc26 it fails with
-- "must be owner of table sample_result" and rolls the whole thing back.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-05-sample-entry.sql

BEGIN;

-- ── the entry ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lims.sample (
    sample_id         text PRIMARY KEY,
    batch_id          text,
    badge_id          text,
    badge_holder      text,
    sample_start      timestamptz,
    sample_completion timestamptz,
    open_duration_s   numeric(8,2),
    cycle_result      text,
    cycle_count       bigint,
    source_topic      text,
    status            text NOT NULL DEFAULT 'awaiting-analysis',
    created_at        timestamptz NOT NULL DEFAULT now(),
    analyst           text,
    verified_at       timestamptz
);
CREATE INDEX IF NOT EXISTS ix_sample_status  ON lims.sample (status, sample_completion);
CREATE INDEX IF NOT EXISTS ix_sample_created ON lims.sample (created_at);

-- ── split the id on the result rows ──────────────────────────────────────────
-- `reported_sample_id` is what the instrument said and is never rewritten;
-- `sample_id` is the entry it is attached to, NULL while unmatched.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'lims' AND table_name = 'sample_result'
                 AND column_name = 'sample_id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'lims' AND table_name = 'sample_result'
                         AND column_name = 'reported_sample_id') THEN
        ALTER TABLE lims.sample_result RENAME COLUMN sample_id TO reported_sample_id;
    END IF;
END $$;

ALTER TABLE lims.sample_result ADD COLUMN IF NOT EXISTS sample_id   text;
ALTER TABLE lims.sample_result ADD COLUMN IF NOT EXISTS attached_at timestamptz;
ALTER TABLE lims.sample_result ADD COLUMN IF NOT EXISTS attached_by text;

-- ── carry existing history forward ───────────────────────────────────────────
-- Rows that predate this change were created by the analyzer alone, so there is
-- no valve provenance to fill in. They get an entry keyed on what they reported,
-- with sample_completion standing in as the earliest collected_at.
-- Wrapped, because `status` is dropped further down: on a second run the column
-- is gone and a plain INSERT..SELECT naming it would fail. By then the entries
-- exist, so there is nothing left to carry.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'lims' AND table_name = 'sample_result'
                 AND column_name = 'status') THEN
        EXECUTE $sql$
            INSERT INTO lims.sample (sample_id, batch_id, sample_completion, status,
                                     created_at, analyst, verified_at)
            SELECT r.reported_sample_id,
                   max(r.batch_id),
                   min(r.collected_at),
                   -- Approve and reject update every row of a sample together, so a
                   -- mixed status cannot arise in practice; if one ever did, the
                   -- reviewed verdict beats 'received'.
                   coalesce(max(r.status) FILTER (WHERE r.status <> 'received'), 'received'),
                   min(r.created_at),
                   max(r.analyst),
                   max(r.verified_at)
            FROM lims.sample_result r
            GROUP BY r.reported_sample_id
            ON CONFLICT (sample_id) DO NOTHING
        $sql$;
    END IF;
END $$;

UPDATE lims.sample_result
SET sample_id = reported_sample_id, attached_at = created_at
WHERE sample_id IS NULL
  AND EXISTS (SELECT 1 FROM lims.sample s WHERE s.sample_id = sample_result.reported_sample_id);

-- ── review state moves up to the entry ───────────────────────────────────────
ALTER TABLE lims.sample_result DROP COLUMN IF EXISTS status;
ALTER TABLE lims.sample_result DROP COLUMN IF EXISTS analyst;
ALTER TABLE lims.sample_result DROP COLUMN IF EXISTS verified_at;
DROP INDEX IF EXISTS lims.ix_sample_result_status;

-- ── dedupe constraint follows the reported id ────────────────────────────────
ALTER TABLE lims.sample_result DROP CONSTRAINT IF EXISTS uq_sample_analyte;
ALTER TABLE lims.sample_result DROP CONSTRAINT IF EXISTS uq_reported_sample_analyte;
ALTER TABLE lims.sample_result
    ADD CONSTRAINT uq_reported_sample_analyte UNIQUE (reported_sample_id, analyte);
CREATE INDEX IF NOT EXISTS ix_sample_result_sample ON lims.sample_result (sample_id);

-- ── grants ───────────────────────────────────────────────────────────────────
GRANT ALL PRIVILEGES ON TABLE lims.sample TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA lims TO icc26;
GRANT SELECT ON TABLE lims.sample TO cdc;

COMMIT;
