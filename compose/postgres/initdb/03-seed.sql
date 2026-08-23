-- ─────────────────────────────────────────────────────────────────────────────
-- 03 — Seed data
--
-- Just enough physical model for the stack to be coherent on first boot. Rows
-- here mirror the ISA-95 topic namespace exactly (docs/00-architecture.md), so
-- equipment_id values are the same strings that appear in topics.
--
-- Pattern-specific sample/event data is NOT seeded here — that arrives with each
-- pattern's own step.
-- ─────────────────────────────────────────────────────────────────────────────
\connect icc26

-- Bioreactors have no line: each one IS a cell, and its sample valve sits in it.
--
-- Patterns 1 and 2 are the SAME smart sample valve assembly in two firmwares, one on each
-- vessel, so they can run side by side and be compared live. Two rows rather than one
-- because they really are two assemblies bolted to two vessels — the thing being compared
-- is the protocol, and nothing else about them differs.
INSERT INTO plant.equipment (equipment_id, site, area, line, equipment_type, description) VALUES
    ('BR-201',          'site1', 'upstream', NULL,        'bioreactor',        '2000L single-use bioreactor'),
    ('BR-202',          'site1', 'upstream', NULL,        'bioreactor',        '2000L single-use bioreactor'),
    ('novaflex-01',     'site1', 'qc',       'analyzers', 'analyzer',          'Nova Flex cell-culture analyzer — pattern 3 OPC UA, pattern 4 HTTPS POST (planned)'),
    ('sample-valve-01', 'site1', 'upstream', 'br-201',    'sample-valve',      'RFID sample valve assembly, s/n SV-2000-0417 — pattern 1, native MQTT'),
    ('sample-valve-02', 'site1', 'upstream', 'br-202',    'sample-valve',      'RFID sample valve assembly, s/n SV-2000-0418 — pattern 2, Sparkplug B'),
    -- Patterns 5 and 6, and the first user of the `downstream` area. Ids are
    -- LOWERCASE and match the topic tokens exactly:
    --   icc26/site1/downstream/tff-301/turbidity-01/telemetry
    -- ('BR-201' above is an existing wart — do not add another.)
    --
    -- The meter is an Anton Paar Haze 3001 turbidity MODULE. It is not on the
    -- network: it plugs into a host instrument, and the host files completed
    -- measurements into AP Connect, whose catalog is `apconnect` (05-apconnect.sql).
    ('tff-301',         'site1', 'downstream', NULL,      'tff-skid',          'Tangential-flow filtration skid'),
    ('turbidity-01',    'site1', 'downstream', 'tff-301', 'turbidity-meter',   'Anton Paar Haze 3001 module on a DMA 5002 host — pattern 5 CDC, pattern 6 JDBC poll');

INSERT INTO plant.batch (batch_id, equipment_id, product, started_at, status) VALUES
    ('B-2026-0142', 'BR-201', 'mAb-7 clinical', now() - interval '38 hours', 'running'),
    ('B-2026-0143', 'BR-202', 'mAb-7 clinical', now() - interval '11 hours', 'running');
