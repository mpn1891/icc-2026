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
    -- BR-201 has no line: it IS the cell that agitator-vib sits in.
    ('BR-201',       'site1', 'upstream', NULL,        'bioreactor',        '2000L single-use bioreactor — pattern 2, Sparkplug B edge node'),
    ('novaflex-01',  'site1', 'qc',       'analyzers', 'analyzer',          'Nova Flex cell-culture analyzer — pattern 3, OPC UA to MQTT'),
    -- Pattern 1 is one 4-channel wireless gateway with a single provisioned channel, not
    -- four independent sensors. Both the gateway and the sensor it carries are equipment,
    -- and each has its own topic branch.
    --
    -- vib-gw-01's line is 'vibration-gw', NOT a cell. An Erbessd gateway is a radio
    -- concentrator serving several skids at once, so it belongs to the area's gateway fleet
    -- and its channels resolve to whichever cell each sensor is mounted on. Putting it in
    -- br-201 would assert the fleet cannot cross cells, which is false.
    ('vib-gw-01',    'site1', 'upstream', 'vibration-gw', 'vibration-gateway', 'Wireless vibration gateway, 4ch, serial 12345678 — pattern 1'),
    ('agitator-vib', 'site1', 'upstream', 'br-201',    'vibration-sensor',  'Agitator drive-end accelerometer, gateway ch0 — pattern 1, native MQTT');

INSERT INTO plant.batch (batch_id, equipment_id, product, started_at, status) VALUES
    ('B-2026-0142', 'BR-201', 'mAb-7 clinical', now() - interval '38 hours', 'running');
