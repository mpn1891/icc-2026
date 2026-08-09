-- ─────────────────────────────────────────────────────────────────────────────
-- 01 — Roles and databases
--
-- Runs once, on first initialization of an empty data volume. If you change
-- anything here you must `tasks.py nuke` (drops the volume) for it to re-run.
-- ─────────────────────────────────────────────────────────────────────────────

-- Ignition's SQL connection target: tag historian, audit log, and anything the
-- gateway writes via JDBC. (Ignition 8.3 keeps its *configuration* in files, not
-- here — that is what makes this repo possible.) Kept separate from demo data so
-- the CDC publication can never accidentally pick up historian churn.
CREATE ROLE ignition WITH LOGIN PASSWORD 'ignition';
CREATE DATABASE ignition OWNER ignition;

-- Demo data: the lims / mes / plant schemas the seven patterns read and write.
CREATE ROLE icc26 WITH LOGIN PASSWORD 'icc26';
CREATE DATABASE icc26 OWNER icc26;

-- Debezium's login. REPLICATION is required for logical decoding; it is
-- deliberately a distinct role from the application user so the demo shows CDC
-- as an out-of-band observer that the application knows nothing about.
CREATE ROLE cdc WITH LOGIN REPLICATION PASSWORD 'cdc';
