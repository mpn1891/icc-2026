-- Live apply of pattern 6's schema onto an already-initialized volume.
-- initdb/02-schema.sql is the source of truth for a nuke; this is the same
-- change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. new schema `em`
--   2. em.reading + its pattern-7 lookup index
--   3. grants for the `icc26` role (and deliberately NOT for `cdc`)
--   4. a guard that em.reading has not been added to the icc26_cdc publication
--
-- **This is the file that actually runs.** initdb/ executes on an EMPTY volume
-- only, so editing 02-schema.sql changes nothing about a running database --
-- the mistake that left a `mes` schema alive for three days after the rename
-- was recorded as done, and that migrate-06 step 0 had to catch up.
-- docs/00-architecture.md § Postgres.
--
-- Run it as `postgres`, not as `icc26`. initdb creates these objects as the
-- superuser, so a table created here by icc26 would have a different owner from
-- one created by a nuke-and-reseed -- and ALTER PUBLICATION, needed by the
-- guard in step 4, is owner-only.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-07-em-reading.sql

BEGIN;

-- ── 1. the schema ────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS em AUTHORIZATION icc26;

-- ── 2. the table ─────────────────────────────────────────────────────────────
-- Pattern 6's store, written by the Ignition poll script (`metone_poll`) as it
-- publishes -- so what pattern 7 reads is the same record the backbone saw,
-- including `status`, which is OURS and not the instrument's. The MET ONE
-- reports counts and has no idea what a cleanroom limit is; the limit is
-- Ignition's rule and lives in exactly one place, `config/excursion_threshold`
-- on the particle_counter UDT.
--
-- Deliberately the same lookup shape as bes.batch_event, so pattern 7 has one
-- query idiom for both of its flags rather than two:
--
--   SELECT status, occurred_at FROM em.reading
--    WHERE device_id = ? AND occurred_at <= ?
--    ORDER BY occurred_at DESC, id DESC
--    LIMIT 1;
CREATE TABLE IF NOT EXISTS em.reading (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       text NOT NULL,             -- 'particle-counter-01', the topic form
    -- The vendor's own uuid for the analysis. It is the dedupe key rather than
    -- `sequence_number`, and that is a correction from measurement, not a
    -- preference: sequence numbers restart at 1 when the instrument restarts, so
    -- keying on them makes every reading of a fresh run collide with the
    -- previous run's rows and vanish in silence -- exactly the failure the
    -- stale-cursor demo is supposed to RECOVER from.
    analysis_id     text NOT NULL,
    sequence_number bigint NOT NULL,           -- the instrument's own counter
    -- The operator's sample point, set on the instrument's touchscreen and
    -- carried in the vendor record's `deviceName` -- that record has no location
    -- field, and this is the overload, named rather than hidden.
    location        text,
    operator        text,
    status          text NOT NULL,             -- 'normal' | 'excursion' -- OURS
    -- The volume actually drawn. A raw count means something only against this,
    -- so it travels: a consumer can normalise to /m3 even though we did not.
    total_volume_l  numeric(10,3),
    channels        jsonb NOT NULL,            -- [{"size_um":0.5,"count":842}, ...]
    environment     jsonb,                     -- flow / temperature / humidity averages
    occurred_at     timestamptz NOT NULL,      -- completedAt, the instrument's clock
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    -- The dedupe guard, enforced. `metone_poll` also skips anything at or below
    -- its last_sequence watermark, but a restarted gateway replaying a page must
    -- not be able to double-insert. An insert that affects no rows is how the
    -- poll knows not to publish the analysis a second time.
    CONSTRAINT uq_em_reading_device_analysis UNIQUE (device_id, analysis_id)
);

-- occurred_at is the INSTRUMENT's clock; ingested_at is when the poll found out.
-- The difference between those two columns is the detection gap, per row:
--
--   SELECT occurred_at, ingested_at, ingested_at - occurred_at AS detection_lag,
--          status FROM em.reading ORDER BY id DESC LIMIT 10;
CREATE INDEX IF NOT EXISTS ix_em_reading_lookup
    ON em.reading (device_id, occurred_at DESC, id DESC);

-- ── 3. grants ────────────────────────────────────────────────────────────────
-- `icc26` is the role the Ignition ICC26 datasource logs in as. NOT `pg_db`,
-- which points at the `postgres` database as user `ignition` and will pass a
-- glance in the dropdown before writing nowhere useful.
GRANT USAGE ON SCHEMA em TO icc26;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA em TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA em TO icc26;
ALTER DEFAULT PRIVILEGES IN SCHEMA em GRANT ALL PRIVILEGES ON TABLES TO icc26;

-- The `cdc` role is deliberately absent. Debezium has no business reading
-- pattern 6's store, and a role that cannot SELECT it cannot end up tailing it.

-- ── 4. the publication guard ─────────────────────────────────────────────────
-- **em.reading must never join icc26_cdc.** Pattern 5's exclusivity is the point
-- of that publication naming exactly one table; adding this one would make
-- pattern 6 arrive by CDC as well and quietly turn two mechanisms into one, with
-- one analysis on the backbone twice under two different `meta.mechanism`
-- values. `tasks.py health` asserts the membership too, so a mistake here fails
-- a check rather than a demo -- this is the second lock on the same door.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'icc26_cdc'
                  AND schemaname = 'em' AND tablename = 'reading') THEN
        RAISE NOTICE 'em.reading was in icc26_cdc -- removing it; pattern 6 is a POLL';
        ALTER PUBLICATION icc26_cdc DROP TABLE em.reading;
    END IF;
END
$$;

COMMIT;

-- Verify:
--   \d em.reading                      -- the columns, the unique constraint
--   \dRp+ icc26_cdc                    -- bes.batch_event ONLY
--   SELECT occurred_at, ingested_at, ingested_at - occurred_at AS detection_lag,
--          status, sequence_number FROM em.reading ORDER BY id DESC LIMIT 10;
