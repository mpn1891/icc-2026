# 05 — CDC of the turbidity meter's data-management database

> **Supersedes the pattern-5 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> First written 2026-08-23 against a placeholder schema. **Re-sourced the same day against the real
> vendor documentation**, which arrived after the first draft: the instrument is an Anton Paar
> **Haze 3001** turbidity module, and its data lands in **AP Connect**, Anton Paar's lab data
> management server.
>
> Odoo is not the source. `mes.batch_event` is not the source. The LIMS is not the source.
>
> **Vendor reference:** [`../reference/apconnect-haze3001-model.md`](../reference/apconnect-haze3001-model.md),
> distilled from the manuals, with [`../reference/apconnect-openapi-4.0.json`](../reference/apconnect-openapi-4.0.json)
> alongside it. Read the reference before this file. The data model below is not invented.
>
> Talk track (draft): [`../05-cdc-turbidity.md`](../05-cdc-turbidity.md).
>
> **Build with [`06-poll-turbidity.md`](06-poll-turbidity.md).** This file owns the shared
> foundation (database + simulator). Pattern 6 is the Ignition JDBC poll on top. Do not start 06
> until the simulator is inserting rows you can `SELECT`.

| | |
|---|---|
| **Pattern** | 5 of 7 — CDC, because the application will not call you |
| **Mechanism tag** | `meta.mechanism = "cdc"` |
| **Depends on** | nothing already built. Shares a new database with pattern 6 |
| **Blocks** | pattern 6 (needs the table and the simulator) |
| **Pairs with** | [`06-poll-turbidity.md`](06-poll-turbidity.md) — same table, same topic, `poll` |
| **Nuke?** | **yes.** `initdb/` only runs on an empty volume. Batch with retiring `04-cdc.sql` |

## Objective

A Haze 3001 turbidity module on the TFF skid measures through a host instrument, and the completed
measurement is stored by **AP Connect** in AP Connect's own database. AP Connect does not speak
MQTT, does not publish to a broker, and will not be modified. Debezium Server tails that database
and publishes onto the backbone with `meta.mechanism = "cdc"`.

## Talk point

**CDC's real use case is an application you do not own and cannot make emit events.** A workstation
next to the skid, vendor software, a database that is the product. You are not going to get the
vendor to add MQTT. You tail the log as an out-of-band observer, under a login the application does
not know about.

That is the `cdc` role, and it is why it is not `icc26`.

Pattern 6 polls **the same database** with a watermark on the identity column. Two mechanisms, one
source, one topic — and this time the shared source is honest: when the instrument's data only
exists inside a vendor application, CDC vs poll *is* the choice in front of you. It is not the
2026-08-19 LIMS triple (webhook + CDC + poll of one table). Pattern 4 is a different instrument.

The catch-up is the failure that pattern 4 cannot do: stop Debezium, let rows land, start Debezium,
no gaps. Pattern 6, stalled beside it, is late or missing.

## What the vendor docs changed, and what they did not

The first draft of this spec guessed a table `reading (id, ts, ntu, status)`. The real product is
richer and the differences are load-bearing, so they are written into the schema rather than
smoothed away.

| | First draft | This spec | Why |
|---|---|---|---|
| The application | unnamed "vendor software" | **AP Connect 4.0**, Anton Paar | Named, documented, and it has a real API model to copy |
| The instrument | generic turbidity meter | **Haze 3001** module on a DMA/Xsample host | It is a *module*; the host is what reaches the network. Reinforces "the instrument cannot talk to you" |
| Primary value | `ntu numeric` | `HAZE` in **EBC**, canonical vendor unit, with NTU derived (1 EBC = 4 NTU) | EBC is what the vendor calls canonical. Publishing both is free and honest |
| Extra values | none | `S25/S0`, `S90/S0`, absorbance `S0`, cell temperature | Real outputs of a three-angle meter. A payload that is not a naked float |
| Identity | `id bigint` | `measurement_no bigint` **and** a GUID `id` | AP Connect has both: a strictly consecutive item number *and* a global id. The int is the watermark; the GUID is the correlation id |
| Status | `ok`/`hold`/`fault`, invented | `SUCCESS` / `SUCCESS_WITH_WARNING` / `SUCCESS_WITH_ERROR` / `CANCELED` / `FAILURE` | `WellKnownMeasurementStatus`, transcribed |
| Values shape | columns | `result_values jsonb` holding the vendor's `Variant` array | AP Connect expresses measured values as generic key/value Variants, not columns. Storing them as Variants keeps the projection honest |
| Engine | Postgres | **Postgres, deliberately** — the real product is MS SQL Server | See below |

**What did not change: the story.** The instrument's data exists only inside an application you do
not own, and CDC is how you observe it without asking. That was true of the placeholder and it is
true of AP Connect.

### The engine decision

AP Connect really runs on **Microsoft SQL Server** (bundled Express 2019, or an existing 2016 /
2017 / 2019 / 2022 instance; connection string in
`%PROGRAMDATA%\Anton Paar\APConnect\configuration\database.config`).

**The demo uses Postgres anyway.** Decided 2026-08-23, deliberately, weighing:

- The mechanism argument is engine-independent.
- Postgres keeps the working `pgoutput` path, the Ignition driver pattern 6 needs, one `CREATE
  PUBLICATION` instead of `sp_cdc_enable_db` + `sp_cdc_enable_table` + a SQL Server Agent that has
  to be running or CDC silently captures nothing, and about 1.5 GB of laptop.
- Debezium's SQL Server connector would additionally need schema-history storage and
  `encrypt=false` / `trustServerCertificate=true`, none of it testable before it runs.

Two things are given up, and both belong in the deviations table rather than being quietly dropped:

1. Saying "this is the engine the vendor ships" and meaning it literally.
2. One genuinely good aside: **on SQL Server, CDC is not log streaming.** The engine's capture job
   reads the transaction log and writes rows into change tables, and Debezium then *polls those
   change tables*. On SQL Server, CDC is itself a poll. In a talk whose patterns 5 and 6 are
   CDC-versus-poll, that is a sharp point — and it is unavailable on Postgres, where Debezium
   really does stream from a replication slot.

Say the second one out loud as an aside if it fits. Do not imply the demo demonstrates it.

**AP Connect's actual table schema is not published anywhere in the documentation set.** The manual
says how to point AP Connect at a server; it never says what AP Connect writes there. So a faithful
schema simulation is impossible at any effort level. The schema below models the **REST API's
documented data model** instead — which is the closest thing to ground truth that exists, and is
the honest thing to say if asked.

## The chain

```
sim-apconnect ──INSERT──▶ database `apconnect`.measurement   (the application's own store)
                              │
                              │  pgoutput, user `cdc`, publication `apconnect_cdc`
                              ▼
                        Debezium Server
                              │  native change events on an internal topic
                              ▼
                        cdc-mapper  (thin envelope projector)
                              │
                              ▼
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: cdc)
```

**Expected sink:** Debezium Server MQTT onto Chariot, then a small Python mapper that turns a WAL
event into our envelope. Debezium JSON is not our envelope; pretending an SMT will emit
`meta.mechanism` is how this spec would slip. The mapper is also a talk line: CDC gives you the
change, you still project it into the backbone contract.

Ignition is not in this path. That is the observer argument.

**Fallback:** Debezium HTTP sink → Event Stream `05_cdc/measurement` → Transmission, same topic.
Take this only if the MQTT sink will not stay connected to Chariot. Note it as-built; it puts
Ignition back in the publish path and slightly weakens "nobody asked us."

Do not publish Debezium's native JSON on the UNS topic. The firehose colours by `meta.mechanism`; a
Debezium payload has none.

## Why a separate database

Not a schema in `icc26`. A catalog named `apconnect`, owned by an application role.

- Logical replication publications are **per-database**. Debezium Server runs **one source
  connector per process**. Pointing Debezium at `apconnect` means no CDC on `icc26`, which is fine
  — nothing there has a consumer.
- A second catalog named after the vendor's product is what "we did not design this application"
  looks like on stage.
- Pattern 6's JDBC datasource points at `apconnect` too, not at `icc26`.

`wal_level=logical` is already on. It stays.

`04-cdc.sql`'s publication on `lims.sample_result` and `mes.batch_event` **retires with this
build.** Those tables have no CDC consumer.

## Schema

Modelled on AP Connect's REST data model — see the reference doc's *The data model* section. One
row per completed measurement.

Load-bearing and not to be simplified away:

- **`measurement_no`** — the identity column. Pattern 6's watermark. AP Connect's own
  `metadata.measurementNo` is documented as *"a strictly, consecutively increasing number starting
  by 1"*, so an identity column is a faithful stand-in.
- **`id`** — the GUID. AP Connect's `metadata.id`. This is the **correlation id** on both patterns,
  because it is the vendor's own global identifier and it survives a counter reset.
- **`REPLICA IDENTITY FULL`** — complete `before` images.
- **`result_values jsonb`** — the `Variant` array, verbatim in the vendor's shape. Do not flatten it
  into columns in the table. Flattening is the *mapper's* job, and doing it in the store would hide
  that AP Connect hands you generic key/value pairs rather than a schema.

Two timestamps, because AP Connect has two: `metadata.timestamp` is usually the measurement start,
`result.metadata.timestamp` usually the end. `ts` in the envelope is the **completion** time.

Physical address: downstream TFF skid `tff-301`, meter `turbidity-01`. First user of `downstream`.
Seed `plant.equipment` with both ids, **lowercase**, matching the topic tokens (`BR-201` in the
current seed is an existing wart; do not add another).

## Build order

1. **Nuke** (empty Postgres volume). `initdb/` will not re-run otherwise.
2. Role + database + table + publication + grants.
3. `sim-apconnect` inserting on an interval. Prove with `psql` before Debezium exists.
4. MQTT users `debezium` + `cdc-mapper`.
5. Debezium Server + mapper. Prove catch-up.
6. Pattern 6's JDBC poll (separate spec, same volume).

## Files to create

| Path | What |
|---|---|
| `compose/postgres/initdb/01-databases.sql` | `apconnect` role + database |
| `compose/postgres/initdb/05-apconnect.sql` | **new.** Table, replica identity, publication, grants. `\connect apconnect` |
| `compose/postgres/initdb/04-cdc.sql` | stub: the `icc26` publication is retired |
| `compose/postgres/initdb/03-seed.sql` | `tff-301`, `turbidity-01` |
| `services/sim-apconnect/` | writer + config page. No MQTT |
| `services/cdc-mapper/` | WAL event → envelope. paho-mqtt 2.x |
| `compose/debezium/application.properties` | pgoutput source, MQTT sink, slot, publication name |
| `docker-compose.yml` | `sim-apconnect`, `debezium`, `cdc-mapper` |
| `compose/chariot/mqtt-users.json` | `debezium`, `cdc-mapper` |

Ignition: **nothing** on the expected path.

### `01-databases.sql` (additions)

```sql
-- The turbidity meter's data-management application (patterns 5 and 6). Anton
-- Paar AP Connect owns this catalog; we did not design it and do not write to
-- it. Debezium tails it; Ignition polls it. Both are observers.
--
-- The real product runs on Microsoft SQL Server. Postgres here is a deliberate
-- substitution -- see docs/plans/05-cdc-turbidity.md, "The engine decision".
CREATE ROLE apconnect WITH LOGIN PASSWORD 'apconnect';
CREATE DATABASE apconnect OWNER apconnect;
```

The `cdc` role already exists with `REPLICATION`. Do not create a second CDC user.

Update the stale comment on the `icc26` block: it currently says patterns 5 and 6 use a catalog
called `turbidity`. They use `apconnect`.

### `05-apconnect.sql` (new)

```sql
\connect apconnect

-- One row per completed measurement, modelled on AP Connect's REST data model
-- (docs/reference/apconnect-haze3001-model.md). AP Connect's own table schema is
-- not published by the vendor, so this models the API rather than the store.
CREATE TABLE measurement (
    -- metadata.measurementNo: "a strictly, consecutively increasing number
    -- starting by 1". Pattern 6 watermarks on this.
    measurement_no            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- metadata.id: the vendor's global identifier. Correlation id on both
    -- patterns. Survives a measurement_no reset; the counter does not.
    id                        uuid NOT NULL UNIQUE,

    -- apc_measurementCompletionNo: the completed-measurement counter that the
    -- REST poll filters on. Tracked so the model stays faithful even though the
    -- JDBC poll uses measurement_no.
    measurement_completion_no integer NOT NULL,

    measurement_name          text NOT NULL,

    -- WellKnownMeasurementStatus. CANCELED and FAILURE carry no reading.
    status                    text NOT NULL
        CHECK (status IN ('SUCCESS', 'SUCCESS_WITH_WARNING', 'SUCCESS_WITH_ERROR',
                          'CANCELED', 'FAILURE')),
    assessment                text,

    -- metadata.timestamp is the start; the result metadata timestamp is the end.
    -- The envelope's `ts` is completed_ts.
    started_ts                timestamptz NOT NULL,
    completed_ts              timestamptz NOT NULL,

    type_id                   text NOT NULL DEFAULT 'SingleMeasurement',
    sample_name               text,
    product                   text,
    method                    text,

    instrument_type           text NOT NULL DEFAULT 'DMA 5002',
    instrument_serial         text NOT NULL,
    instrument_alias          text NOT NULL,
    operator_login            text,
    operator_name             text,

    -- The MeasurementResult.values[] Variant array, in the vendor's own shape.
    -- Deliberately not flattened into columns: AP Connect hands you generic
    -- key/value Variants, and the projection into named fields is the mapper's
    -- job, not the store's.
    result_values             jsonb NOT NULL
);

ALTER TABLE measurement REPLICA IDENTITY FULL;

CREATE PUBLICATION apconnect_cdc FOR TABLE measurement;

GRANT CONNECT ON DATABASE apconnect TO cdc;
GRANT CONNECT ON DATABASE apconnect TO ignition;
GRANT USAGE ON SCHEMA public TO cdc, ignition;
GRANT SELECT ON TABLE measurement TO cdc, ignition;
```

`ignition` gets SELECT for pattern 6, not for this pattern. Grant it now so 06 does not need a
second nuke. `cdc` must **not** be able to INSERT — if the observer can write, the demo is lying.

`CREATE PUBLICATION` in a database init script runs as the superuser; that is fine. Do not hand
`cdc` `CREATE` on the database.

### `result_values` contents

The Variant array a Haze 3001 measurement would carry. `QUANTITY` variants expand `value` into
`{numeric, unit, quantity}` — that is the vendor's representation, not ours.

```json
[
  {"id": "Haze/Haze",             "name": "Haze",             "type": "QUANTITY",
   "value": {"numeric": 4.12,   "unit": "EBC", "quantity": "HAZE"}},
  {"id": "Haze/HazeNTU",          "name": "Haze (NTU)",       "type": "QUANTITY",
   "value": {"numeric": 16.48,  "unit": "NTU", "quantity": "HAZE"}},
  {"id": "Haze/S25S0",            "name": "Haze value S25/S0","type": "QUANTITY",
   "value": {"numeric": 0.0132, "unit": "",    "quantity": "-"}},
  {"id": "Haze/S90S0",            "name": "Haze value S90/S0","type": "QUANTITY",
   "value": {"numeric": 0.0087, "unit": "",    "quantity": "-"}},
  {"id": "Haze/AbsorbanceS0",     "name": "Haze absorbance S0","type": "QUANTITY",
   "value": {"numeric": 0.214,  "unit": "",    "quantity": "-"}},
  {"id": "Density/CellTemperature","name": "Cell temperature", "type": "QUANTITY",
   "value": {"numeric": 20.03,  "unit": "°C",  "quantity": "TEMPERATURE"}}
]
```

**`Haze/...` ids are modelled, not transcribed.** The vendor's well-known-values table documents
only the density module (`Density/SetTemperature`, `Density/CellTemperature`, `Density/Density`);
the `Module/Quantity` convention is real, the Haze ids are our extension of it. Say "modelled on
the vendor's convention" if asked, never "this is their id".

1 EBC = 4 NTU exactly, so `Haze/HazeNTU` is derived, never independently randomised.

Unit strings contain non-ASCII (`°C`). Everything from the simulator to the mapper to the broker
must be UTF-8 clean. Set `PYTHONIOENCODING=utf-8` in the containers if anything mangles it.

### `04-cdc.sql` (replace)

```sql
-- Retired 2026-08-23. Pattern 5 tails database `apconnect` (see 05-apconnect.sql).
-- lims.sample_result and mes.batch_event have no CDC consumer.
-- This file stays so initdb numbering does not have a hole, and so a grep for
-- icc26_cdc still finds the explanation.
```

Do not keep `CREATE PUBLICATION icc26_cdc`. A leftover publication with no subscriber is how
somebody "fixes" this back onto the LIMS tables.

### Simulator

House style from `services/sim-valve-mqtt`: `_env` helpers, a `Config` class, stdlib `http.server`,
inline CSS/JS, no CDN, no shared library. Dependency: `psycopg[binary]` only. **No paho. No
FastAPI.**

The name is `sim-apconnect`, not `sim-turbidity`: it simulates the *application*, and the
application is the thing patterns 5 and 6 integrate with. The instrument it simulates measurements
from is `turbidity-01`.

```
services/sim-apconnect/
    app.py          # interval INSERT, config, graceful stop
    webui.py        # stdlib config page
    page.html
    Dockerfile
    requirements.txt
    README.md
```

Config page (host **8087**): EBC setpoint, noise amplitude, insert period, a "measure now" button,
a status override so `CANCELED` / `FAILURE` rows can be produced on demand, and the last
`measurement_no` written. Optional stall checkbox is **not** here — stalling the writer is not the
demo; stalling pattern 6's timer is.

```python
# app.py sketch

class Config:
    def __init__(self):
        self.pghost = _env("PGHOST", "postgres")
        self.pgdatabase = _env("PGDATABASE", "apconnect")
        self.pguser = _env("PGUSER", "apconnect")
        self.pgpassword = _env("PGPASSWORD", "apconnect")
        self.device_id = _env("DEVICE_ID", "turbidity-01")
        self.instrument_serial = _env("INSTRUMENT_SERIAL", "83012345")
        self.instrument_type = _env("INSTRUMENT_TYPE", "DMA 5002")
        self.period_s = _env_float("INSERT_PERIOD_S", 2.0)
        self.ebc_setpoint = _env_float("EBC_SETPOINT", 4.0)
        self.ebc_noise = _env_float("EBC_NOISE", 0.3)
        self.http_port = _env_int("HTTP_PORT", 8080)

EBC_PER_NTU = 4.0  # vendor: 1 EBC = 4 NTU

def build_result_values(ebc, temp_c):
    """The Variant array AP Connect would hold. Vendor shape, modelled ids."""
    def q(vid, name, numeric, unit, quantity):
        return {"id": vid, "name": name, "type": "QUANTITY",
                "value": {"numeric": round(numeric, 4), "unit": unit,
                          "quantity": quantity}}
    return [
        q("Haze/Haze",              "Haze",              ebc,              "EBC", "HAZE"),
        q("Haze/HazeNTU",           "Haze (NTU)",        ebc * EBC_PER_NTU, "NTU", "HAZE"),
        q("Haze/S25S0",             "Haze value S25/S0", ebc * 0.0032,     "",    "-"),
        q("Haze/S90S0",             "Haze value S90/S0", ebc * 0.0021,     "",    "-"),
        q("Haze/AbsorbanceS0",      "Haze absorbance S0", 0.19 + ebc * 0.006, "", "-"),
        q("Density/CellTemperature", "Cell temperature", temp_c,           "°C",  "TEMPERATURE"),
    ]

def insert_one(cfg, rng, status="SUCCESS") -> int:
    ebc = max(0.0, rng.gauss(cfg.ebc_setpoint, cfg.ebc_noise))
    values = build_result_values(ebc, rng.gauss(20.0, 0.05))
    # A canceled or failed measurement is a row with no reading. Absent, not zero.
    if status in ("CANCELED", "FAILURE"):
        values = [v for v in values if not v["id"].startswith("Haze/")]
    ...
```

TFF filtrate, roughly 0.5–3 EBC (2–12 NTU), walking slowly, is enough. Do not simulate 4–20 mA. The
poll problem is the store.

`measurement_completion_no` increments only for rows that reach a completed state — which, in this
simulation, is all of them. Keep it a separate counter from `measurement_no` anyway, so the model
stays faithful and pattern 6 could switch to the REST-style watermark later without a schema
change.

### Debezium

Image: `quay.io/debezium/server:3.0` — **pin a digest** when building (`docker pull` before the
conference). Named volume for offsets and the replication-slot bookkeeping. No internet at runtime.

```properties
# compose/debezium/application.properties

debezium.source.connector.class=io.debezium.connector.postgresql.PostgresConnector
debezium.source.plugin.name=pgoutput
debezium.source.database.hostname=postgres
debezium.source.database.port=5432
debezium.source.database.user=cdc
debezium.source.database.password=cdc
debezium.source.database.dbname=apconnect
debezium.source.topic.prefix=debezium
debezium.source.table.include.list=public.measurement
debezium.source.publication.name=apconnect_cdc
debezium.source.publication.autocreate.mode=disabled
debezium.source.slot.name=debezium_apconnect
debezium.source.tombstones.on.delete=false
debezium.source.decimal.handling.mode=double

debezium.sink.type=mqtt
debezium.sink.mqtt.client.id=debezium
debezium.sink.mqtt.server.uri=tcp://chariot:1883
debezium.sink.mqtt.username=debezium
debezium.sink.mqtt.password=debezium
debezium.sink.mqtt.qos=1
debezium.sink.mqtt.retained=false
# Exact property names for 3.0: confirm against the image's mqtt-sink docs when building.
# Topic should land on debezium.public.measurement (prefix + table), which the mapper subscribes.
```

`decimal.handling.mode=double` matters less now that the numbers live in `jsonb` rather than
`numeric` columns, but leave it — it keeps any future numeric column from arriving base64-encoded.

Offset volume: `debezium-data:/debezium/data`. Slot `debezium_apconnect` is created by Debezium on
first connect; do not pre-create it in SQL.

Compose: `restart: unless-stopped`, `container_name: icc26-debezium`. No `depends_on: chariot` —
reconnect is the sink's job. A healthcheck that only proves the process is up is enough;
`tasks.py health` does not need a new probe for this.

### Mapper

```
services/cdc-mapper/
    app.py
    Dockerfile
    requirements.txt   # paho-mqtt>=2.1,<3
    README.md
```

Subscribe `debezium.public.measurement` (confirm the real topic from a `mosquitto_sub` on
`debezium/#` the first time Debezium fires). Publish the envelope. Filter to **creates** (`op == "c"`
/ `"r"` for snapshot). Updates and deletes are checkpoint 5 — log them, do not put them on the UNS
topic unless you decide as-built that the talk wants them.

The mapper is where the Variant array becomes named fields. That projection is the talk line: CDC
handed you the row exactly as the vendor stores it, and the backbone contract is still yours to
meet.

```python
# app.py sketch

MECHANISM = "cdc"
SOURCE = {"id": "turbidity-01", "type": "turbidity-meter"}
OUT_TOPIC = "icc26/site1/downstream/tff-301/turbidity-01/telemetry"

# Variant id -> envelope key. One place, so swapping in the vendor's real ids
# when someone confirms them against a live AP Connect is a single edit.
VARIANT_MAP = {
    "Haze/Haze":               "haze_ebc",
    "Haze/HazeNTU":            "haze_ntu",
    "Haze/S25S0":              "s25_s0",
    "Haze/S90S0":              "s90_s0",
    "Haze/AbsorbanceS0":       "absorbance_s0",
    "Density/CellTemperature": "cell_temperature_c",
}

def project_values(result_values):
    """Variant array -> flat dict. Absent stays absent; never substitute 0."""
    out = {}
    for v in result_values or []:
        key = VARIANT_MAP.get(v.get("id"))
        if key is None:
            continue
        val = v.get("value")
        if isinstance(val, dict):
            val = val.get("numeric")
        if val is not None:
            out[key] = float(val)
    return out

def project(change):
    payload = change.get("payload") or change
    if payload.get("op") not in ("c", "r"):
        return None
    after = payload.get("after") or {}

    result_values = after.get("result_values")
    if isinstance(result_values, str):          # jsonb may arrive as a string
        result_values = json.loads(result_values)

    values = {
        "measurement_no": int(after["measurement_no"]),
        "measurement_id": after["id"],
        "status": after["status"],
        "sample_name": after.get("sample_name"),
        "instrument_serial": after.get("instrument_serial"),
    }
    # A CANCELED or FAILURE measurement produces no reading. The keys are simply
    # absent -- same absent-vs-zero rule as patterns 3 and 4.
    values.update(project_values(result_values))

    return {
        "ts": _iso(after["completed_ts"]),
        "seq": int(after["measurement_no"]),
        "source": SOURCE,
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso_now(),
            "correlation_id": after["id"],      # the vendor's GUID
        },
        "values": _drop_nones(values),
    }
```

`seq` from `measurement_no` keeps CDC and poll comparable on the firehose. `correlation_id` is the
**GUID**, not the integer, and pattern 6 must use the same field — a subscriber joins the two
colours of one measurement on it.

Debezium renders `timestamptz` as epoch microseconds by default, and `uuid` as a string. Confirm
both against the first real event rather than trusting this sentence; `_iso` must cope with an int,
a float, or an ISO string.

House MQTT style: reconnect-with-backoff, env-config (`BROKER_HOST=chariot`), no LWT required (this
is not a field device). Duplicate the ~20-line envelope helper; no shared lib.

### Compose services (sketch)

```yaml
  sim-apconnect:
    build: ./services/sim-apconnect
    container_name: icc26-sim-apconnect
    restart: unless-stopped
    environment:
      PGHOST: postgres
      PGDATABASE: apconnect
      PGUSER: apconnect
      PGPASSWORD: apconnect
      INSERT_PERIOD_S: ${APCONNECT_INSERT_PERIOD_S:-2}
      EBC_SETPOINT: ${APCONNECT_EBC_SETPOINT:-4.0}
      HTTP_PORT: 8080
      PYTHONIOENCODING: utf-8
      TZ: ${TZ:-America/Chicago}
    ports:
      - "${APCONNECT_UI_PORT:-8087}:8080"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
    networks: [icc26]

  debezium:
    image: quay.io/debezium/server:3.0
    container_name: icc26-debezium
    restart: unless-stopped
    volumes:
      - ./compose/debezium/application.properties:/debezium/conf/application.properties:ro
      - debezium-data:/debezium/data
    networks: [icc26]

  cdc-mapper:
    build: ./services/cdc-mapper
    container_name: icc26-cdc-mapper
    restart: unless-stopped
    environment:
      BROKER_HOST: chariot
      MQTT_USERNAME: cdc-mapper
      MQTT_PASSWORD: cdc-mapper
      IN_TOPIC: ${CDC_MAPPER_IN_TOPIC:-debezium.public.measurement}
      OUT_TOPIC: icc26/site1/downstream/tff-301/turbidity-01/telemetry
      PYTHONIOENCODING: utf-8
    networks: [icc26]
```

Add `debezium-data` to the compose `volumes:` block.

## MQTT users + topics

| User | Subscribe | Publish |
|---|---|---|
| `debezium` | none | `debezium/#` |
| `cdc-mapper` | `debezium/#` | `icc26/site1/downstream/tff-301/turbidity-01/#` |
| `observer` | already covers `icc26/#` | none |

Do not give `debezium` an `icc26/#` publish grant. The raw WAL event must not land on the UNS topic
by accident. `mqtt-users.json` seeds on first run only — nuke, or add the users in the Chariot UI.

UNS topic (mapper output):

```
icc26/site1/downstream/tff-301/turbidity-01/telemetry
```

Internal (Debezium output, not the talk): `debezium.public.measurement` — confirm as-built.

## Envelope

```json
{
  "ts": "2026-08-23T14:03:22.145Z",
  "seq": 41,
  "source": { "id": "turbidity-01", "type": "turbidity-meter" },
  "meta": {
    "mechanism": "cdc",
    "ingest_ts": "2026-08-23T14:03:22.401Z",
    "correlation_id": "6f0a1c2e-9b41-4d38-8a77-1f2b3c4d5e6f"
  },
  "values": {
    "measurement_no": 41,
    "measurement_id": "6f0a1c2e-9b41-4d38-8a77-1f2b3c4d5e6f",
    "status": "SUCCESS",
    "sample_name": "TFF-301 filtrate",
    "instrument_serial": "83012345",
    "haze_ebc": 4.12,
    "haze_ntu": 16.48,
    "s25_s0": 0.0132,
    "s90_s0": 0.0087,
    "absorbance_s0": 0.2147,
    "cell_temperature_c": 20.03
  }
}
```

`ts` is `completed_ts` (when AP Connect finished storing the measurement). `meta.ingest_ts` is when
the mapper published. The lag is small and is not the point — pattern 6's lag is.

A `CANCELED` or `FAILURE` row publishes the same envelope with `status` set accordingly and **no
haze keys at all**. Absent, not zero. Pattern 6 must behave identically.

## Empirical checkpoints

**1 — Schema exists after nuke.** `SHOW wal_level;` → `logical`. `\l` lists `apconnect`.
`\d measurement` in that database. `SELECT * FROM pg_publication;` → `apconnect_cdc` only.

**2 — Simulator writes.** Config page on :8087.
`SELECT measurement_no, id, status, completed_ts, result_values->0 FROM measurement ORDER BY measurement_no DESC LIMIT 5;`
Numbers contiguous, GUIDs distinct, first Variant is `Haze/Haze` in EBC near the setpoint.

**3 — Debezium slot.** `SELECT slot_name, active FROM pg_replication_slots;` → `debezium_apconnect`,
`t`. Chariot client list shows `debezium`.

**4 — Mapper output.** One INSERT → **one** UNS message, `mechanism=cdc`, `values.haze_ebc` matches
the row, `seq == values.measurement_no`, `meta.correlation_id == values.measurement_id`, and
`haze_ntu == 4 × haze_ebc`.

**5 — Updates and deletes.**
`UPDATE measurement SET status = 'SUCCESS_WITH_WARNING' WHERE measurement_no = …` → mapper log shows
`op=u` and `before` populated (`REPLICA IDENTITY FULL`). No UNS message (insert-only decision).
Record as-built if you choose to publish updates.

**6 — A failed measurement carries no reading.** Force a `FAILURE` from the config page. The UNS
message has `status: "FAILURE"` and **no** `haze_ebc` key. Not `0`.

**7 — Catch-up.** Stop Debezium. Insert several rows (or let the sim run). Start Debezium. Those
`measurement_no`s appear on the topic, in order, no gaps. That is the WAL argument.

**8 — Non-ASCII survives.** `cell_temperature_c` exists and the `°C` unit did not mangle anywhere
in the chain. Check the mapper log, not just the final envelope.

**9 — Pattern 6 beside it.** Same rows, second colour, same topic. A subscriber still cannot tell
from the **topic** which is which. That checkpoint lives in spec 06 but needs this path green first.

## Verification (copy-paste)

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' -v
```

Watch `debezium/#` in a second client while bringing Debezium up the first time, to learn the real
internal topic name.

## Closing step

Rewrite [`../05-cdc-turbidity.md`](../05-cdc-turbidity.md) from the draft into the as-built talk
track. Update architecture (Postgres roles, `sim-apconnect` / `debezium` / `cdc-mapper` rows in the
service table), `00-status.md`, `services/README.md`, `compose/chariot/README.md`.

Deviations table, at minimum:

- **Postgres substituted for Microsoft SQL Server**, with the reasoning and the one aside it costs.
- **`Haze/...` Variant ids are modelled**, not transcribed — only the density module's ids are
  documented by the vendor.
- **AP Connect's real table schema is unpublished**; this models the REST data model instead.
- MQTT vs HTTP sink; insert-only vs all ops; the real Debezium topic name; the image digest.
- Whether Debezium rendered `timestamptz` and `uuid` as expected.

If anyone ever gets a look at a real AP Connect database or confirms the Haze Variant ids, update
[`../reference/apconnect-haze3001-model.md`](../reference/apconnect-haze3001-model.md) first, then
this spec.
