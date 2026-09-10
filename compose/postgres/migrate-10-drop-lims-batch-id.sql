-- Live apply of the 2026-09-09 removal of the LIMS's own batch_id onto an
-- already-initialized volume. initdb/02-schema.sql is the source of truth for a
-- nuke; this is the same change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. lims.sample.batch_id        -> dropped
--   2. lims.sample_result.batch_id -> dropped
--
-- **This is the file that actually runs.** initdb/ executes on an EMPTY volume
-- only, so editing 02-schema.sql changes nothing about a running database.
-- Same note as migrate-07, -08 and -09. docs/00-architecture.md section Postgres.
--
-- Run it as `postgres`, not as `icc26`.

\connect icc26

-- ── why these columns go ─────────────────────────────────────────────────────
--
-- Nothing in the lab knows the work order. The valve opens on a badge, not on a
-- batch record, and mints an entry with an empty batch. The analyzer has a
-- vendor BatchID tag but only ever echoes what somebody typed at its sample
-- login screen -- and that field was removed from the screen the same day, so it
-- now echoes a hardcoded fallback.
--
-- The result was four competing conventions for one identifier, recorded in
-- docs/plans/07-sample-chain.md section "The decisions 07 inherits", decision 2:
--
--     every sample pattern 1 mints   (empty)         232 rows
--     lims.sample                    BR-2026-014      59 rows
--     lims.sample seed               B-2026-0142      10 rows
--     bes.batch_event pre-08-30      12345           all old rows
--
-- Only the last of those comes from the system that owns batch identity, and it
-- is the one pattern 7 already reads: sample_chain._batch_context() takes
-- batch_id off the bes.batch_event row it lands on for the sample instant, in
-- the same query it already runs for `operation`. It never read the LIMS copy,
-- and the review message's `values.batch_id` is gone with these columns.
--
-- Nothing else joins on either column. No index, constraint or FK references
-- them; `ix_sample_result_id_created` and `ix_sample_result_created` do not
-- include batch_id, and lims.sample_result's only unique constraint is
-- (reported_sample_id, analyte).

ALTER TABLE lims.sample        DROP COLUMN IF EXISTS batch_id;
ALTER TABLE lims.sample_result DROP COLUMN IF EXISTS batch_id;

-- Note for the CDC publication: lims.sample_result came OUT of icc26_cdc on
-- 2026-08-26 (bes.batch_event is the only table in it), so no publication needs
-- refreshing after this. Verify with:
--   SELECT * FROM pg_publication_tables WHERE pubname = 'icc26_cdc';
