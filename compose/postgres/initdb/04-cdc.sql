-- ─────────────────────────────────────────────────────────────────────────────
-- 04 — Change data capture prerequisites (pattern 5)
--
-- Set up here rather than at pattern-5 time because logical decoding depends on
-- `wal_level=logical`, which is a server start parameter set in the compose
-- command override. Getting it wrong means dropping the data volume, so we
-- prove it works on day one.
--
-- Verify after first boot:  SHOW wal_level;  -->  logical
-- ─────────────────────────────────────────────────────────────────────────────
\connect icc26

-- This publication still names two tables nothing currently reads. Pattern 5
-- moved to Odoo's own database on 2026-08-19, so neither lims.sample_result nor
-- mes.batch_event has a CDC consumer. Kept as a reviewable artifact until the
-- pattern-5 spec retires it; do not treat a row appearing here as a live
-- integration. wal_level=logical stays — that is a server start parameter and
-- Odoo will need it.
CREATE PUBLICATION icc26_cdc FOR TABLE lims.sample_result, mes.batch_event;

-- REPLICA IDENTITY FULL makes Postgres write the complete pre-image of a row into
-- the WAL on UPDATE and DELETE. Without it, Debezium's `before` field carries only
-- the primary key. It costs WAL volume: fine for two demo tables, not something
-- to switch on blindly across a real database. Harmless while the publication
-- has no subscriber.
ALTER TABLE lims.sample_result REPLICA IDENTITY FULL;
ALTER TABLE mes.batch_event    REPLICA IDENTITY FULL;

-- Debezium creates its own replication slot on first connect, so none is defined
-- here. If it ever needs clearing by hand:
--   SELECT pg_drop_replication_slot('icc26_debezium');
