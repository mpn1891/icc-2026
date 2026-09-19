-- Live apply of the review-event store onto an already-initialized volume.
-- initdb/02-schema.sql is the source of truth for a nuke; this is the same
-- change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. lims.review_event + its i3X lookup index
--   2. grants for the `icc26` role (and deliberately NOT for `cdc`)
--   3. a guard that review_event has not been added to the icc26_cdc publication
--
-- Run it as `postgres`, not as `icc26`. initdb creates these objects as the
-- superuser, so a table created here by icc26 would have a different owner from
-- one created by a nuke-and-reseed -- and ALTER PUBLICATION, needed by the
-- guard in step 3, is owner-only.
--
--   docker exec -i icc26-postgres psql -U postgres -d icc26 -v ON_ERROR_STOP=1 \
--     < compose/postgres/migrate-11-review-event.sql

BEGIN;

-- ── 1. the table ─────────────────────────────────────────────────────────────
-- **A review is an event, and this is where the event lives.** `lims.sample` is
-- a different writer's projection of the same review -- the LIMS workflow row,
-- carrying `status` in {awaiting-analysis, rejected, verified}. That is not the
-- analyst's `disposition`, it has no column for the acquisition instant `ts`,
-- and it does not keep the message. Reconstructing the vessel's `last_review`
-- object from it would serve a partial object under the object's own schema.
--
-- `document` is the review as the MODEL sees it: exactly the member document
-- `model_feed.write_review` writes to `qc_data/last_review`, keys and all, with
-- DateTime members as epoch milliseconds because that is how the i3X server
-- encodes a DateTime on `/objects/value` (measured 2026-09-18; see
-- `sample_chain._row_instant`). Storing it pre-shaped is what lets the i3X
-- history branch hand the row straight back: the `i3x` project cannot import
-- `model_feed` -- neither project inherits the other -- so any mapping written
-- on the read side would be a second copy of the write side, free to drift.
-- One writer, one shape, no translation.
--
-- The raw MQTT message is still inside it, under the `document` member, exactly
-- as it is on the tag.
CREATE TABLE IF NOT EXISTS lims.review_event (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id    text        NOT NULL,
    equipment_id text        NOT NULL,

    -- `ts` is the acquisition instant and `verified_at` is when a person
    -- clicked; the gap between them is the whole of pattern 4. Both are real
    -- columns and not just keys inside `document`, because the history lookup
    -- filters and orders on `ts` and a jsonb extract cannot use the index.
    ts           timestamptz NOT NULL,
    verified_at  timestamptz NOT NULL,

    document     jsonb       NOT NULL,

    -- When the row landed, as against when the review happened. Same
    -- occurred_at/ingested_at split em.reading draws, for the same reason.
    stored_at    timestamptz NOT NULL DEFAULT now(),

    -- The identity of a review is the sample plus the instant a person signed
    -- it, so a redelivery is a no-op and a genuine re-review of the same sample
    -- is still a second row. Written when the review arrived over MQTT at QoS 1
    -- through an aborting-and-retrying Event Stream, where a second arrival was
    -- routine; since 2026-09-19 `model_feed.write_review` is called by
    -- `lims_webhook.handle`, which answers a repeated idempotency key with 409
    -- before it runs, so this is the second line rather than the first.
    CONSTRAINT uq_review_event UNIQUE (sample_id, verified_at)
);

-- The i3X history lookup: one vessel, a time window, oldest first. Deliberately
-- the same shape as ix_em_reading_lookup and bes.batch_event's, so there is one
-- query idiom across all three stores rather than three.
CREATE INDEX IF NOT EXISTS ix_review_event_lookup
    ON lims.review_event (equipment_id, ts DESC, id DESC);

-- ── 2. grants ────────────────────────────────────────────────────────────────
-- `icc26` is the role the Ignition ICC26 datasource logs in as. NOT `pg_db`,
-- which is the historian's own store (the `ignition` database as user
-- `ignition`) and will pass a glance in the dropdown before writing nowhere
-- useful.
--
-- SELECT and INSERT only. Nothing rewrites a review event: a correction is a
-- new review with a new `verified_at`, which is what makes the history a record
-- rather than a current state with extra steps.
GRANT USAGE ON SCHEMA lims TO icc26;
GRANT SELECT, INSERT ON lims.review_event TO icc26;

-- The `cdc` role is deliberately absent, as it is on em.reading. Debezium has
-- no business tailing pattern 4's store, and a role that cannot SELECT it
-- cannot end up tailing it by accident.

-- ── 3. the publication guard ─────────────────────────────────────────────────
-- **lims.review_event must never join icc26_cdc.** Pattern 5's exclusivity is
-- the point of that publication naming exactly one table; adding this one would
-- put every review onto the backbone by CDC as well as by MQTT, under two
-- different `meta.mechanism` values. `tasks.py health` asserts the membership
-- too -- this is the second lock on the same door.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'icc26_cdc'
                  AND schemaname = 'lims' AND tablename = 'review_event') THEN
        RAISE NOTICE 'lims.review_event was in icc26_cdc -- removing it; pattern 4 is MQTT';
        ALTER PUBLICATION icc26_cdc DROP TABLE lims.review_event;
    END IF;
END
$$;

COMMIT;

-- Verify:
--   \d lims.review_event               -- the columns, the unique constraint
--   \dRp+ icc26_cdc                    -- bes.batch_event ONLY
--   SELECT sample_id, equipment_id, ts, verified_at,
--          verified_at - ts AS review_lag, stored_at - verified_at AS ingest_lag
--     FROM lims.review_event ORDER BY id DESC LIMIT 10;
