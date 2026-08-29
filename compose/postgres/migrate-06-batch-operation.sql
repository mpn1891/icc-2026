-- Live apply of pattern 5's 2026-08-26 schema onto an already-initialized volume.
-- initdb/02-schema.sql and initdb/04-cdc.sql are the source of truth for a nuke;
-- this is the same change written as ALTER so pgdata and ign-data survive.
-- Safe to run twice.
--
-- What changes:
--   0. schema mes -> bes                             (see below -- volumes older
--      than 2026-08-23 never got this)
--   1. bes.batch_event.phase          -> operation   (ISA-88: CIP/SIP/INOC/
--      GROWTH/HARVEST are operations, not phases)
--   2. bes.batch_event gains equipment_id            (the cdc-sink builds the
--      MQTT topic from it, so a click on br-202 cannot publish onto br-201)
--   3. lims.sample_result leaves the icc26_cdc publication
--   4. the pattern-7 lookup index
--
-- **Step 0 exists because the rename was never actually applied to a running
-- volume.** `mes.` became `bes.` in initdb/02-schema.sql on 2026-08-23, and the
-- docs recorded it as done -- but initdb runs on an empty volume only, and no
-- migration was written. Any checkout seeded before that date still has a `mes`
-- schema, an `mes.batch_event` with a `phase` column, and a publication naming
-- it. Found 2026-08-26 on the machine pattern 5 was built on, where every other
-- doc had been asserting `bes` for three days.
--
-- Run it as `postgres`, not as `icc26`. initdb created these tables as the
-- superuser, so icc26 holds ALL PRIVILEGES but is not the owner, and
-- ALTER TABLE ... RENAME COLUMN is owner-only: as icc26 it fails with
-- "must be owner of table batch_event" and rolls the whole thing back.
-- ALTER PUBLICATION is owner-only too.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-06-batch-operation.sql

BEGIN;

-- ── 0. mes -> bes ────────────────────────────────────────────────────────────
-- Grants, default privileges and the publication's table membership all follow
-- the schema object, so this is the whole rename. If `bes` somehow already
-- exists alongside `mes`, stop and look rather than merging them blindly.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'mes') THEN
        IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bes') THEN
            RAISE EXCEPTION
                'both mes and bes schemas exist -- resolve by hand, not by migration';
        END IF;
        ALTER SCHEMA mes RENAME TO bes;
    END IF;
END
$$;

-- The 02-schema.sql grants name `bes` explicitly; re-assert them so a renamed
-- schema ends up identical to a freshly initialized one.
GRANT USAGE ON SCHEMA bes TO icc26;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA bes TO icc26;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA bes TO icc26;
GRANT USAGE  ON SCHEMA bes TO cdc;
GRANT SELECT ON ALL TABLES IN SCHEMA bes TO cdc;
ALTER DEFAULT PRIVILEGES IN SCHEMA bes GRANT SELECT ON TABLES TO cdc;

-- ── 1. phase -> operation ────────────────────────────────────────────────────
-- Guarded so a second run is a no-op rather than an error.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'bes' AND table_name = 'batch_event'
                  AND column_name = 'phase') THEN
        ALTER TABLE bes.batch_event RENAME COLUMN phase TO operation;
    END IF;
END
$$;

-- ── 2. equipment_id ──────────────────────────────────────────────────────────
-- The topic form ('br-201'), not plant.equipment's 'BR-201'. Added nullable so
-- the migration cannot fail on pre-existing rows, then backfilled and tightened.
-- On a volume where nothing has ever written this table both statements are
-- no-ops and the NOT NULL simply takes.
ALTER TABLE bes.batch_event ADD COLUMN IF NOT EXISTS equipment_id text;
UPDATE bes.batch_event SET equipment_id = 'br-201' WHERE equipment_id IS NULL;
ALTER TABLE bes.batch_event ALTER COLUMN equipment_id SET NOT NULL;

-- ── 3. narrow the publication ────────────────────────────────────────────────
-- Pattern 5 tails bes.batch_event and nothing else. A LIMS review is pattern 4's
-- webhook; tailing the same row again would deliver one review twice under two
-- mechanisms. DROP TABLE on a publication is idempotent-unsafe (it errors if the
-- table is not a member), hence the guard.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'icc26_cdc'
                  AND schemaname = 'lims' AND tablename = 'sample_result') THEN
        ALTER PUBLICATION icc26_cdc DROP TABLE lims.sample_result;
    END IF;
END
$$;

-- No longer tailed, so stop paying the WAL cost of a full pre-image on every
-- UPDATE. lims.sample_result is updated on every reattach, unlike batch_event.
ALTER TABLE lims.sample_result REPLICA IDENTITY DEFAULT;

-- ── 4. pattern 7's lookup ────────────────────────────────────────────────────
-- One click writes operation_end + operation_start in one transaction sharing
-- one occurred_at, so "what was running at time T" tie-breaks on id.
CREATE INDEX IF NOT EXISTS ix_batch_event_lookup
    ON bes.batch_event (equipment_id, occurred_at DESC, id DESC);

COMMIT;

-- Verify:
--   \d bes.batch_event                 -- operation, equipment_id, no phase
--   \dRp+ icc26_cdc                    -- bes.batch_event only
--   SELECT slot_name, active, restart_lsn FROM pg_replication_slots;
