-- Live apply of pattern 7's prerequisite onto an already-initialized volume.
-- initdb/02-schema.sql is the source of truth for a nuke; this is the same
-- change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. lims.sample gains `equipment_id` -- which vessel the sample came from
--
-- **This is the file that actually runs.** initdb/ executes on an EMPTY volume
-- only, so editing 02-schema.sql changes nothing about a running database.
-- Same note as migrate-07. docs/00-architecture.md § Postgres.
--
-- Run it as `postgres`, not as `icc26`.

-- ── 1. equipment_id ──────────────────────────────────────────────────────────
--
-- Pattern 7 keys BOTH of its lookups on equipment_id -- as a literal against
-- bes.batch_event.equipment_id and em.reading.equipment_id. Until now the only
-- place a sample's vessel existed was inside `source_topic`, so an aggregator
-- had to either parse that string or hardcode 'br-201'. Both are wrong the
-- moment there is a second reactor, and there already is one: br-202 is a live
-- UDT instance fed over Sparkplug.
--
-- Deliberately nullable and deliberately not a foreign key, for the same reason
-- bes.batch_event.equipment_id is not one: plant.equipment holds 'BR-201' in
-- the wrong case and is not on pattern 7's path. The join key is the topic-form
-- string, lowercase, and it is the same string in all three tables.
--
-- Backfill is intentionally NOT attempted. Every existing row's vessel would
-- have to be inferred from source_topic, and a guessed equipment_id in a GxP
-- record is worse than a null one -- pattern 7 reports a gap as a finding.
ALTER TABLE lims.sample ADD COLUMN IF NOT EXISTS equipment_id text;

COMMENT ON COLUMN lims.sample.equipment_id IS
    'Vessel the sample was drawn from, topic form and lowercase (br-201). '
    'Parsed from the valve event topic by the LIMS bridge. Pattern 7''s join '
    'key into bes.batch_event and em.reading. Null on rows created before '
    '2026-08-30; not backfilled, because it would be a guess.';

-- ── verify ───────────────────────────────────────────────────────────────────
--   \d lims.sample                     -- equipment_id text, nullable
--   SELECT count(*) FILTER (WHERE equipment_id IS NULL) FROM lims.sample;
