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

-- Demo data: lims / mes / plant schemas. `lims` is leftover until pattern 4's
-- rebuild. Patterns 5 and 6 do NOT live here -- they use the separate catalog
-- `apconnect` created below. (Earlier drafts called that catalog `turbidity`.)
CREATE ROLE icc26 WITH LOGIN PASSWORD 'icc26';
CREATE DATABASE icc26 OWNER icc26;

-- The turbidity meter's data-management application (patterns 5 and 6). Anton
-- Paar AP Connect owns this catalog; we did not design it and do not write to
-- it. Debezium tails it; Ignition polls it. Both are observers.
--
-- The real product runs on Microsoft SQL Server. Postgres here is a deliberate
-- substitution -- see docs/plans/05-cdc-turbidity.md, "The engine decision".
CREATE ROLE apconnect WITH LOGIN PASSWORD 'apconnect';
CREATE DATABASE apconnect OWNER apconnect;

-- Debezium's login. REPLICATION is required for logical decoding; it is
-- deliberately a distinct role from the application user so the demo shows CDC
-- as an out-of-band observer that the application knows nothing about.
CREATE ROLE cdc WITH LOGIN REPLICATION PASSWORD 'cdc';
