-- Live apply of the particle counter's stored document onto an already-
-- initialized volume. initdb/02-schema.sql is the source of truth for a nuke;
-- this is the same change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. em.reading gains a `document` jsonb column, nullable
--   2. the existing rows are backfilled from the columns they already hold
--
-- No grant and no publication guard. `em` already grants ALL on its tables to
-- the icc26 role and a new column inherits that; em.reading's exclusion from
-- icc26_cdc is migrate-07 step 4 and adding a column does not change it.
--
-- Run it as `postgres`, not as `icc26`, for the reason migrate-11 gives: initdb
-- creates these objects as the superuser, so the ownership must match what a
-- nuke-and-reseed would produce.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-13-em-reading-document.sql

BEGIN;

-- ── 1. the column ────────────────────────────────────────────────────────────
-- **A reading is an event, and this is the shape it is served in.** em.reading
-- has been pattern 6's record since migrate-07 and it stays so; what it did not
-- hold is the reading as an OBJECT. The i3X server was therefore left to
-- reassemble one from the tag historian, and it did what it does for any UDT:
-- one row per distinct change instant across the members, each member
-- forward-filled. Measured 2026-09-19 on `particle-counter-01`: four rows
-- inside 32 ms for one analysis, every member null but `total_volume_l` --
-- states the object was never in. Same disease as lims_review (migrate-11) and
-- cell_analyzer (migrate-12), same cure.
--
-- `document` is the counter instance's own member document, nested exactly as
-- `POST /objects/value` returns it -- `current.status`, `current.ch_0_5`,
-- `current.conditions.flow_rate_lpm` -- and with the DateTime member
-- `current.ts` as epoch milliseconds, because that is how the i3X server
-- encodes a DateTime (measured 2026-09-18; see `sample_chain._row_instant`).
-- Storing it pre-shaped is what lets the i3X history branch hand the row
-- straight back untouched: the `i3x` project cannot import
-- `particle_counter_poll` -- neither project inherits the other -- so any
-- mapping written on the read side would be a second copy of the write side,
-- free to drift. One writer, one shape, no translation.
--
-- **`current/` only, and not the whole instance.** The analyzer's document
-- (migrate-12) is its whole instance value because its writer is an OPC
-- subscription with no list of members to build from -- one `readBlocking` on
-- the instance was the only honest capture. This writer authors every value it
-- stores, so the document is built from the same `members` list that goes to
-- the tags, which is what makes the two incapable of drifting. That list is the
-- analysis; `state/` is the poll's cursor and watermark and `config/` is the
-- cleanroom rule, and neither is a fact about the reading. It is also what the
-- backfill below can honestly produce: no row in this table records what the
-- cursor was when it landed. So `/objects/value` on the counter returns three
-- folders and a history row returns one, and the one it returns is identical in
-- shape to its namesake in the live value.
ALTER TABLE em.reading ADD COLUMN IF NOT EXISTS document jsonb;

-- **Nullable, and that is not laziness.** lims.review_event and
-- qc.analyzer_result declare `document NOT NULL` because both tables were new
-- and no writer could predate them. This column lands on a table with 13,000
-- rows and a writer that is deployed by an Ignition project scan, which is a
-- separate act from running this file and cannot be made atomic with it. NOT
-- NULL here means that whichever order they happen in, the gap is a poll that
-- throws on INSERT and loses a reading. Nullable means the gap is a reading
-- that is stored but not yet serveable as an object -- and the i3X reader
-- filters those out rather than serving a row with a null value, which would
-- be the invented state this whole change exists to remove.
COMMENT ON COLUMN em.reading.document IS
    'The reading as the i3X server serves it: the counter''s current/ member '
    'document, nested as POST /objects/value returns it, DateTime as epoch ms. '
    'Written by particle_counter_poll from the same members list the tags get. '
    'Nullable only for rows written before the column existed.';

-- ── 2. the backfill ──────────────────────────────────────────────────────────
-- **This is a second statement of the document's shape, and it is frozen at the
-- moment it runs.** The writer in `particle_counter_poll._document` is the
-- living definition; this is the one-off that gives the 13,000 rows already in
-- the table the same shape, so i3X history over the counter reaches back to the
-- start of the demo's data rather than beginning at the migration. It is
-- deliberately not a trigger, a generated column or a view: any of those would
-- be a permanent second copy of the shape, free to drift from the writer the
-- first time a member is added. If a member IS added later, this statement is
-- not updated -- it describes rows that were written before that member
-- existed, and inventing a value for them would be worse than their not having
-- the key.
--
-- The channel keys are `particle_counter_poll._channel_tag`'s rule in SQL:
-- 0.5 -> ch_0_5, 10.0 -> ch_10_0. Verified against live rows 2026-09-20.
-- `WHERE document IS NULL` makes the whole file re-runnable.
UPDATE em.reading r
   SET document = jsonb_build_object('current',
         jsonb_build_object(
           'ts', (extract(epoch from r.occurred_at) * 1000)::bigint,
           'sequence_number', r.sequence_number,
           'status', r.status,
           'location', r.location,
           'operator', r.operator,
           'total_volume_l', r.total_volume_l::float8,
           'conditions', jsonb_build_object(
              'flow_rate_lpm', r.environment -> 'flow_rate_lpm',
              'temperature_c', r.environment -> 'temperature_c',
              'humidity_pct',  r.environment -> 'humidity_pct')
         ) || coalesce(
           (SELECT jsonb_object_agg(
                     'ch_' || replace(to_char((c->>'size_um')::numeric, 'FM9990.0'), '.', '_'),
                     c->'count')
              FROM jsonb_array_elements(r.channels) AS c), '{}'::jsonb))
 WHERE r.document IS NULL;

COMMIT;

-- Verify:
--   \d em.reading                      -- document jsonb, nullable
--   SELECT count(*) FILTER (WHERE document IS NULL) AS unshaped, count(*) FROM em.reading;
--   SELECT jsonb_pretty(document) FROM em.reading ORDER BY id DESC LIMIT 1;
--   -- and the one that matters: a backfilled row and a written row agree in shape
--   SELECT DISTINCT jsonb_object_keys(document -> 'current') FROM em.reading
--    WHERE id IN ((SELECT min(id) FROM em.reading), (SELECT max(id) FROM em.reading));
