-- ─────────────────────────────────────────────────────────────────────────────
-- 05 — The turbidity meter's data-management application (patterns 5 and 6)
--
-- Anton Paar AP Connect 4.0 is the lab data management server a Haze 3001
-- turbidity module's measurements land in. It does not speak MQTT, it will not
-- be modified, and the only ways in are its store and its REST API. Pattern 5
-- tails this catalog with Debezium; pattern 6 polls it over JDBC. Both are
-- observers; neither is invited.
--
-- TWO SUBSTITUTIONS ARE RECORDED HERE SO NOBODY MISTAKES THIS FOR A TRANSCRIPT:
--
--   1. AP Connect really persists to Microsoft SQL Server (bundled SQL Express
--      2019, or an existing 2016/2017/2019/2022 instance). Postgres is a
--      deliberate substitution -- see docs/plans/05-cdc-turbidity.md, "The
--      engine decision". The mechanism argument is engine-independent; what is
--      given up is written down there rather than quietly dropped.
--
--   2. AP Connect's own table schema is NOT PUBLISHED anywhere in the vendor
--      documentation set. The manual says how to point AP Connect at a server;
--      it never says what AP Connect writes there. So this models the REST
--      API's documented data model instead, which is the closest thing to
--      ground truth that exists. See docs/reference/apconnect-haze3001-model.md.
--
-- Runs once, on first initialization of an empty data volume. `tasks.py nuke`
-- to re-run it.
-- ─────────────────────────────────────────────────────────────────────────────
\connect apconnect

-- One row per completed measurement. Modelled on AP Connect's REST data model
-- (Measurement -> metadata / results[] -> values[]), flattened to one table
-- because the demo consumes one measurement at a time and a recursive
-- sub-result tree would add nothing an audience can see.
CREATE TABLE measurement (
    -- metadata.measurementNo -- the vendor documents this as "a strictly,
    -- consecutively increasing number starting by 1". Pattern 6 watermarks on
    -- it, so an identity column is a faithful stand-in.
    measurement_no            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- metadata.id -- the vendor's global identifier. THE CORRELATION ID on both
    -- patterns 5 and 6, because it survives a measurementNo reset and the
    -- counter does not. A subscriber joins the CDC copy and the polled copy of
    -- one measurement on this value.
    id                        uuid NOT NULL UNIQUE,

    -- apc_measurementCompletionNo -- the completed-measurement counter that the
    -- REST poll filters on (`apc_FromMeasurementCompletionNo`). Tracked so the
    -- model stays faithful even though pattern 6's JDBC poll watermarks on
    -- measurement_no instead. Kept a SEPARATE counter on purpose.
    measurement_completion_no integer NOT NULL,

    measurement_name          text NOT NULL,

    -- WellKnownMeasurementStatus, transcribed from the REST manual. CANCELED
    -- and FAILURE are the "do not publish a reading" cases -- the analogue of
    -- pattern 3's abort branch. They carry a row and no haze values.
    status                    text NOT NULL
        CHECK (status IN ('SUCCESS', 'SUCCESS_WITH_WARNING', 'SUCCESS_WITH_ERROR',
                          'CANCELED', 'FAILURE')),
    -- WellKnownMeasurementAssessment uses the same five strings: whether the
    -- results sat inside their configured constraints.
    assessment                text,

    -- AP Connect carries two timestamps on two different objects:
    -- metadata.timestamp is usually the START, and the result metadata's
    -- timestamp is usually the END. The envelope's `ts` is completed_ts.
    started_ts                timestamptz NOT NULL,
    completed_ts              timestamptz NOT NULL,

    type_id                   text NOT NULL DEFAULT 'SingleMeasurement',
    sample_name               text,
    product                   text,
    method                    text,

    -- The Haze 3001 is a MODULE. The host instrument -- a DMA 4002/5002/6002 or
    -- an Xsample 3200 -- is the thing that reaches the network, and it is the
    -- host that AP Connect records. That is why the instrument fields name a
    -- density meter rather than a turbidity meter.
    instrument_type           text NOT NULL DEFAULT 'DMA 5002',
    instrument_serial         text NOT NULL,
    instrument_alias          text NOT NULL,
    operator_login            text,
    operator_name             text,

    -- MeasurementResult.values[] -- the vendor's Variant array, in the vendor's
    -- own shape:
    --   {"id": "Haze/Haze", "name": "Haze", "type": "QUANTITY",
    --    "value": {"numeric": 4.12, "unit": "EBC", "quantity": "HAZE"}}
    --
    -- DELIBERATELY NOT FLATTENED INTO COLUMNS. AP Connect hands you generic
    -- key/value Variants rather than a schema, and flattening here would hide
    -- that. Projecting Variants into named envelope fields is the mapper's job
    -- (services/cdc-mapper) and the poll script's job (pattern 6) -- and that
    -- projection is a talk line: CDC handed you the row exactly as the vendor
    -- stores it, and meeting the backbone contract is still your problem.
    --
    -- Unit strings legitimately contain non-ASCII (°C, ², ³, ·). Everything
    -- reading this column must be UTF-8 clean end to end.
    result_values             jsonb NOT NULL
);

-- initdb scripts run as the superuser, so without this the table would be owned
-- by `postgres` and the application role could not write to its own store.
-- Ownership also carries the identity sequence.
ALTER TABLE measurement OWNER TO apconnect;

-- Complete `before` images in the WAL on UPDATE and DELETE. Without it,
-- Debezium's `before` field carries only the primary key -- and checkpoint 5 of
-- spec 05 is precisely "does `before` arrive populated". It costs WAL volume;
-- fine for one demo table, not something to switch on blindly.
ALTER TABLE measurement REPLICA IDENTITY FULL;

-- Logical replication publications are PER-DATABASE and Debezium Server runs
-- one source connector per process, which is why this catalog exists separately
-- from `icc26` at all. `debezium.source.publication.autocreate.mode=disabled`
-- in compose/debezium/application.properties means Debezium expects to find
-- this already here and will not create it.
CREATE PUBLICATION apconnect_cdc FOR TABLE measurement;

-- The observers.
--
-- `cdc` is Debezium (created in 01-databases.sql with REPLICATION). `ignition`
-- is pattern 6's JDBC poll; it is granted now so that building pattern 6 does
-- not cost a second nuke.
--
-- NEITHER GETS INSERT. If the observer can write, the demo is lying about being
-- an observer -- spec 06 checkpoint 10 tests exactly this. Ignition's trigger
-- for a new measurement is an HTTP POST to the simulator, never an INSERT.
GRANT CONNECT ON DATABASE apconnect TO cdc;
GRANT CONNECT ON DATABASE apconnect TO ignition;
GRANT USAGE  ON SCHEMA public TO cdc, ignition;
GRANT SELECT ON TABLE measurement TO cdc, ignition;

-- Debezium creates replication slot `debezium_apconnect` itself on first
-- connect, so none is defined here. If it ever needs clearing by hand:
--   SELECT pg_drop_replication_slot('debezium_apconnect');
