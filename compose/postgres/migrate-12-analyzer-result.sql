-- Live apply of the analyzer-result store onto an already-initialized volume.
-- initdb/02-schema.sql is the source of truth for a nuke; this is the same
-- change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. schema qc, and qc.analyzer_result + its i3X lookup index
--   2. grants for the `icc26` role (and deliberately NOT for `cdc`)
--   3. a guard that analyzer_result has not been added to the icc26_cdc publication
--
-- Run it as `postgres`, not as `icc26`, for the reason migrate-11 gives: initdb
-- creates these objects as the superuser, so a table created here by icc26 would
-- have a different owner from one created by a nuke-and-reseed, and
-- ALTER PUBLICATION, needed by the guard in step 3, is owner-only.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-12-analyzer-result.sql

BEGIN;

-- ── 1. the schema and the table ──────────────────────────────────────────────
-- **`qc` and not `lims`.** The schema matches the tag path the object lives at,
-- `icc26/site1/qc/analyzers/…`, the way `lims` matches the LIMS's own tags and
-- `em` matches environmental monitoring. `lims.sample_result` is the LIMS's
-- projection of this instrument's analytes -- one row per analyte, keyed by the
-- sample id the instrument reported -- and it is a different writer's view of
-- the same run. It has no column for the analyzer's document and no notion of
-- an analysis revision. Filing the analyzer's own record under it would serve a
-- partial object under another party's schema, which is the argument migrate-11
-- made for lims.review_event over lims.sample.
CREATE SCHEMA IF NOT EXISTS qc AUTHORIZATION icc26;

-- **An analysis is an event, and this is where the event lives.**
--
-- `document` is the analyzer instance as the i3X server serves it: exactly the
-- nested member document `POST /objects/value` returns for
-- `cell-analyzer-01` -- `result.chem.gluc`, `command.vessel_id`, `result_json`
-- and all -- captured by one `readBlocking` over the instance at the instant the
-- Event Stream fired. Storing it pre-shaped is what lets the i3X history branch
-- hand the row straight back untouched, exactly as `_reviewHistory` does: the
-- `i3x` project cannot import `opcua_event` -- neither project inherits the
-- other -- so any mapping written on the read side would be a second copy of the
-- write side, free to drift. One writer, one shape, no translation.
--
-- It is also the only place `result_json` -- the vendor's own payload, the one
-- member that makes a row whole -- is kept at all. It is not historised, so it
-- is null in every tag-historian row this store replaces.
CREATE TABLE IF NOT EXISTS qc.analyzer_result (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The i3X key. This is the instance's `device_id` PARAMETER
    -- ("CELL-ANALYZER-01"), never the display name ("cell-analyzer-01") nor an
    -- upper-cased copy of one: the reader takes it from
    -- udtInstance["parameters"], which is where `_coalesceMillis` already reads
    -- HistoryCoalesceMs from. Get the case wrong and every history query returns
    -- an empty list quietly.
    device_id     text        NOT NULL,
    sample_id     text        NOT NULL,

    -- `ts` is the acquisition instant (the instrument's `result/sample_time`)
    -- and `modified_time` is when the result was last written. The same split
    -- lims.review_event draws between ts and verified_at, for the same reason:
    -- the history lookup filters and orders on ts, and a jsonb extract cannot
    -- use the index.
    ts            timestamptz NOT NULL,
    modified_time timestamptz NOT NULL,

    document      jsonb       NOT NULL,

    -- When the row landed, as against when the analysis happened. Same
    -- occurred_at/ingested_at split em.reading draws.
    stored_at     timestamptz NOT NULL DEFAULT now(),

    -- **The unit is one analysis VERSION, not one analysis.** The tag
    -- documentation on `result/modified_time` says it "runs later if CDV images
    -- are reanalyzed or the result is edited after the fact -- so a result CAN
    -- change after you have already read it." Keyed on sample_id alone, such a
    -- revision is silently dropped: the store would keep the first reading of a
    -- result the instrument has since corrected, and nothing downstream could
    -- detect it. With modified_time in the key a revision lands as a second row
    -- and a duplicate stream fire is still a no-op.
    CONSTRAINT uq_analyzer_result UNIQUE (device_id, sample_id, modified_time)
);

-- The i3X history lookup: one analyzer, a time window, oldest first.
-- Deliberately the same shape as ix_review_event_lookup, ix_em_reading_lookup
-- and bes.batch_event's, so there is one query idiom across all four stores
-- rather than four.
CREATE INDEX IF NOT EXISTS ix_analyzer_result_lookup
    ON qc.analyzer_result (device_id, ts DESC, id DESC);

-- ── 2. grants ────────────────────────────────────────────────────────────────
-- `icc26` is the role the Ignition ICC26 datasource logs in as. NOT `pg_db`,
-- which is the historian's own store (the `ignition` database as user
-- `ignition`) and will pass a glance in the dropdown before writing nowhere
-- useful.
--
-- SELECT and INSERT only. Nothing rewrites an analysis: a correction is a new
-- row with a new modified_time, which is what makes the history a record rather
-- than a current state with extra steps.
GRANT USAGE ON SCHEMA qc TO icc26;
GRANT SELECT, INSERT ON qc.analyzer_result TO icc26;

-- The `cdc` role is deliberately absent, as it is on em.reading and
-- lims.review_event. Debezium has no business tailing pattern 3's store, and a
-- role that cannot SELECT it cannot end up tailing it by accident.

-- ── 3. the publication guard ─────────────────────────────────────────────────
-- **qc.analyzer_result must never join icc26_cdc.** Pattern 5's exclusivity is
-- the point of that publication naming exactly one table; adding this one would
-- put every analysis onto the backbone by CDC as well as by MQTT, under two
-- different `meta.mechanism` values. `tasks.py health` asserts the membership
-- too -- this is the second lock on the same door.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'icc26_cdc'
                  AND schemaname = 'qc' AND tablename = 'analyzer_result') THEN
        RAISE NOTICE 'qc.analyzer_result was in icc26_cdc -- removing it; pattern 3 is MQTT';
        ALTER PUBLICATION icc26_cdc DROP TABLE qc.analyzer_result;
    END IF;
END
$$;

COMMIT;

-- Verify:
--   \dn                                -- bes, em, lims, plant, qc
--   \d qc.analyzer_result              -- the columns, the unique constraint
--   \dRp+ icc26_cdc                    -- bes.batch_event ONLY
--   SELECT device_id, sample_id, ts, modified_time,
--          stored_at - ts AS ingest_lag,
--          jsonb_typeof(document -> 'result_json') AS has_result_json
--     FROM qc.analyzer_result ORDER BY id DESC LIMIT 10;
