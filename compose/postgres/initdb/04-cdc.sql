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

-- Pattern 5 CDC-tails **bes.batch_event, and only that**. This publication used
-- to name lims.sample_result as well, from the abandoned design where patterns
-- 4, 5 and 6 all carried one LIMS row; that table came out on 2026-08-26 when
-- pattern 5 was built. Do not put it back: a LIMS review is pattern 4's webhook,
-- and tailing the same row again would make one review arrive twice under two
-- different mechanisms. wal_level=logical stays — it is a server start parameter
-- and Debezium needs it.
CREATE PUBLICATION icc26_cdc FOR TABLE bes.batch_event;

-- REPLICA IDENTITY FULL makes Postgres write the complete pre-image of a row into
-- the WAL on UPDATE and DELETE. Without it, Debezium's `before` field carries only
-- the primary key. It costs WAL volume: fine for one append-only demo table, not
-- something to switch on blindly across a real database.
--
-- bes.batch_event is only ever INSERTed into, so `before` is always null in
-- normal operation and this buys nothing at runtime. It is kept for the negative
-- check in pattern 5's verification: hand-UPDATE a row and the sink has to show
-- op='u' and publish nothing.
ALTER TABLE bes.batch_event REPLICA IDENTITY FULL;

-- Debezium creates its own replication slot on first connect, named by
-- `debezium.source.slot.name` in compose/debezium/application.properties. If it
-- ever needs clearing by hand:
--   SELECT pg_drop_replication_slot('icc26_debezium');
--
-- A slot left behind by a removed subscriber pins WAL forever. `docker compose
-- down` does not drop it, because the slot lives in the pgdata volume, not in
-- the Debezium container.
