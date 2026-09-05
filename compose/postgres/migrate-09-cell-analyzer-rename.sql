-- Live apply of the 2026-09-05 novaflex -> cell analyzer rename onto an
-- already-initialized volume. initdb/03-seed.sql is the source of truth for a
-- nuke; this is the same change written so pgdata survives. Safe to run twice.
--
-- What changes:
--   1. plant.equipment's analyzer row: 'novaflex-01' -> 'cell-analyzer-01',
--      and its description loses the vendor's product name
--
-- **This is the file that actually runs.** initdb/ executes on an EMPTY volume
-- only, so editing 03-seed.sql changes nothing about a running database.
-- Same note as migrate-07 and -08. docs/00-architecture.md section Postgres.
--
-- Run it as `postgres`, not as `icc26`.

\connect icc26

-- ── 1. the analyzer's equipment_id ───────────────────────────────────────────
--
-- Cosmetic, and deliberately so. plant.equipment is NOT on pattern 7's path
-- (sample_chain/code.py) and nothing joins to this row: the LIMS keys on the
-- VESSEL's equipment_id ('br-201', parsed from pattern 1's topic), never on the
-- analyzer's. plant.batch references plant.equipment but holds only BR-201 and
-- BR-202, so no FK cascade is involved and no dependent row moves.
--
-- It is still worth applying: this table is the demo's asset register, and an
-- id here that disagrees with the topic, the UDT instance and the tag path is
-- exactly the kind of drift the rename was done in one pass to avoid.
--
-- Guarded so a re-run, or a volume seeded fresh from 03-seed.sql, is a no-op.

UPDATE plant.equipment
   SET equipment_id = 'cell-analyzer-01',
       description  = 'Cell-culture analyzer — pattern 3, OPC UA to MQTT'
 WHERE equipment_id = 'novaflex-01';

-- Fresh volumes already carry the new id; correct the description only if an
-- older seed left the vendor's name behind.
UPDATE plant.equipment
   SET description = 'Cell-culture analyzer — pattern 3, OPC UA to MQTT'
 WHERE equipment_id = 'cell-analyzer-01'
   AND description <> 'Cell-culture analyzer — pattern 3, OPC UA to MQTT';

-- ── verify ───────────────────────────────────────────────────────────────────
--   SELECT equipment_id, equipment_type, description
--     FROM plant.equipment WHERE equipment_type = 'analyzer';
-- Expect exactly one row, 'cell-analyzer-01', no vendor product name in it.
