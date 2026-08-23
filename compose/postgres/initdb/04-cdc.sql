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

-- Pattern 5 (2026-08-23) CDC-tails mes.batch_event. This publication still
-- also names lims.sample_result; drop that table when the pattern-5 spec is
-- written so a LIMS review is not also a CDC event. wal_level=logical stays —
-- it is a server start parameter and Debezium needs it.
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
