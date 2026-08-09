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

INSERT INTO plant.equipment (equipment_id, site, area, line, equipment_type, description) VALUES
    ('BR-201',      'site1', 'usp',       NULL,        'bioreactor',       '2000L single-use bioreactor — pattern 2, Sparkplug B edge node'),
    ('novaflex-01', 'site1', 'qc',        'analyzers', 'analyzer',         'Nova Flex cell-culture analyzer — pattern 3, OPC UA to MQTT'),
    ('vib-01',      'site1', 'utilities', 'pumpskid1', 'vibration-sensor', 'Accelerometer, pump 1 drive end — pattern 1, native MQTT'),
    ('vib-02',      'site1', 'utilities', 'pumpskid1', 'vibration-sensor', 'Accelerometer, pump 1 non-drive end — pattern 1'),
    ('vib-03',      'site1', 'utilities', 'pumpskid1', 'vibration-sensor', 'Accelerometer, pump 2 drive end — pattern 1'),
    ('vib-04',      'site1', 'utilities', 'pumpskid1', 'vibration-sensor', 'Accelerometer, pump 2 non-drive end — pattern 1');

INSERT INTO plant.batch (batch_id, equipment_id, product, started_at, status) VALUES
    ('B-2026-0142', 'BR-201', 'mAb-7 clinical', now() - interval '38 hours', 'running');
