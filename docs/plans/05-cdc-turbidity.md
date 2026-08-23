# 05 — CDC of a turbidity meter's local database

> **Supersedes the pattern-5 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> Written **2026-08-23.** Odoo is not the source. `mes.batch_event` is not the source. The
> LIMS is not the source.
>
> **Vendor API and manuals are coming later.** The schema below is a placeholder so Debezium,
> the simulator, and pattern 6 can be designed against one shape. Replace columns to match
> the real instrument when the docs arrive; do not change the story: **the meter only writes
> a local database.**
>
> Talk track (draft): [`../05-cdc-turbidity.md`](../05-cdc-turbidity.md).
>
> **Build with [`06-poll-turbidity.md`](06-poll-turbidity.md).** This file owns the shared
> foundation (database + simulator). Pattern 6 is the Ignition poll on top. Do not start 06
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

A process turbidity meter on the TFF skid stores readings in **its own Postgres database**.
It does not speak MQTT, does not POST, does not expose OPC UA. Debezium Server tails that
database and publishes onto the backbone with `meta.mechanism = "cdc"`.

## Talk point

**CDC's real use case is an application you do not own and cannot make emit events.** A
workstation next to the skid, vendor software, a database that is the product. You are not
going to get a webhook. You tail the WAL as an out-of-band observer, under a login the
application does not know about.

That is the `cdc` role, and it is why it is not `icc26`.

Pattern 6 polls **the same database** with a watermark on the identity column. Two mechanisms,
one source, one topic — and this time the shared source is honest: when an instrument only
has a local DB, CDC vs poll *is* the choice. It is not the 2026-08-19 LIMS triple (webhook +
CDC + poll of one table). Pattern 4 is a different instrument.

The catch-up is the failure that pattern 4 cannot do: stop Debezium, let rows land, start
Debezium, no gaps. Pattern 6, stalled beside it, is late or missing.

## The chain

```
sim-turbidity ──INSERT──▶ database `turbidity`  (the instrument knows only this)
                              │
                              │  pgoutput, user `cdc`, publication `turbidity_cdc`
                              ▼
                        Debezium Server
                              │  native change events on an internal topic
                              ▼
                        cdc-mapper  (thin envelope projector)
                              │
                              ▼
            icc26/site1/downstream/tff-301/turbidity-01/telemetry   (mechanism: cdc)
```

**Expected sink:** Debezium Server MQTT onto Chariot, then a ~40-line Python mapper that
turns a WAL event into our envelope. Debezium JSON is not our envelope; pretending an SMT
will emit `meta.mechanism` is how this spec would slip. The mapper is also a talk line:
CDC gives you the change, you still project it into the backbone contract.

Ignition is not in this path. That is the observer argument.

**Fallback:** Debezium HTTP sink → Event Stream `05_cdc/turbidity-reading` → Transmission,
same topic. Take this only if the MQTT sink will not stay connected to Chariot. Note it
as-built; it puts Ignition back in the publish path and slightly weakens "nobody asked us."

Do not publish Debezium's native JSON on the UNS topic. The firehose colours by
`meta.mechanism`; a Debezium payload has none.

## Why a separate database

Not a schema in `icc26`. A catalog named `turbidity`, owned by an instrument role.

- Logical replication publications are **per-database**. Debezium Server runs **one source
  connector per process**. Pointing Debezium at `turbidity` means no CDC on `icc26`, which is
  fine — nothing there has a consumer.
- A second catalog is what "we did not design this application" looks like on stage.
- Pattern 6's JDBC datasource points at `turbidity` too, not at `icc26`.

`wal_level=logical` is already on. It stays.

`04-cdc.sql`'s publication on `lims.sample_result` and `mes.batch_event` **retires with this
build.** Those tables have no CDC consumer.

## Placeholder schema

Replace when the vendor docs arrive. Load-bearing: identity column (pattern 6's watermark)
and `REPLICA IDENTITY FULL` (complete `before` images).

`ntu` is the measured value. `status` is the meter's own health word (`ok` / `hold` /
`fault`) so the payload is not a naked float.

Physical address: downstream TFF skid `tff-301`, meter `turbidity-01`. First user of
`downstream`. Seed `plant.equipment` with both ids, **lowercase**, matching the topic
tokens (`BR-201` in the current seed is an existing wart; do not add another).

## Build order

1. **Nuke** (empty Postgres volume). `initdb/` will not re-run otherwise.
2. Role + database + table + publication + grants.
3. `sim-turbidity` inserting on an interval. Prove with `psql` before Debezium exists.
4. MQTT users `debezium` + `cdc-mapper`.
5. Debezium Server + mapper. Prove catch-up.
6. Pattern 6's JDBC poll (separate spec, same volume).

## Files to create

| Path | What |
|---|---|
| `compose/postgres/initdb/01-databases.sql` | `turbidity` role + database |
| `compose/postgres/initdb/05-turbidity.sql` | **new.** Table, replica identity, publication, grants. `\connect turbidity` |
| `compose/postgres/initdb/04-cdc.sql` | stub: the `icc26` publication is retired |
| `compose/postgres/initdb/03-seed.sql` | `tff-301`, `turbidity-01` |
| `services/sim-turbidity/` | writer + config page. No MQTT |
| `services/cdc-mapper/` | WAL event → envelope. paho-mqtt 2.x |
| `compose/debezium/application.properties` | pgoutput source, MQTT sink, slot, publication name |
| `docker-compose.yml` | `sim-turbidity`, `debezium`, `cdc-mapper` |
| `compose/chariot/mqtt-users.json` | `debezium`, `cdc-mapper` |

Ignition: **nothing** on the expected path.

### `01-databases.sql` (additions)

```sql
-- Instrument catalog for patterns 5 and 6. A database the meter owns, not a
-- schema in icc26. Debezium tails this catalog; Ignition polls it.
CREATE ROLE turbidity WITH LOGIN PASSWORD 'turbidity';
CREATE DATABASE turbidity OWNER turbidity;
```

The `cdc` role already exists with `REPLICATION`. Do not create a second CDC user.

### `05-turbidity.sql` (new)

```sql
\connect turbidity

CREATE TABLE reading (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts      timestamptz NOT NULL,
    ntu     numeric(12,4) NOT NULL,
    status  text NOT NULL DEFAULT 'ok'
);

ALTER TABLE reading REPLICA IDENTITY FULL;

CREATE PUBLICATION turbidity_cdc FOR TABLE reading;

GRANT CONNECT ON DATABASE turbidity TO cdc;
GRANT CONNECT ON DATABASE turbidity TO ignition;
GRANT USAGE ON SCHEMA public TO cdc, ignition;
GRANT SELECT ON TABLE reading TO cdc, ignition;
```

`ignition` gets SELECT for pattern 6, not for this pattern. Grant it now so 06 does not
need a second nuke. `cdc` must **not** be able to INSERT — if the observer can write, the
demo is lying.

`CREATE PUBLICATION` in a database init script runs as the superuser; that is fine. Do not
hand `cdc` `CREATE` on the database.

### `04-cdc.sql` (replace)

```sql
-- Retired 2026-08-23. Pattern 5 tails database `turbidity` (see 05-turbidity.sql).
-- lims.sample_result and mes.batch_event have no CDC consumer.
-- This file stays so initdb numbering does not have a hole, and so a grep for
-- icc26_cdc still finds the explanation.
```

Do not keep `CREATE PUBLICATION icc26_cdc`. A leftover publication with no subscriber is
how somebody "fixes" this back onto the LIMS tables.

### Simulator

House style from `services/sim-valve-mqtt`: `_env` helpers, a `Config` class, stdlib
`http.server`, inline CSS/JS, no CDN, no shared library. Dependency: `psycopg[binary]`
only. **No paho. No FastAPI.**

```
services/sim-turbidity/
    app.py          # interval INSERT, config, graceful stop
    webui.py        # stdlib config page
    page.html
    Dockerfile
    requirements.txt
    README.md
```

Config page (host **8087**): NTU setpoint, noise amplitude, insert period, a "insert now"
button, last `id` written. Optional stall checkbox is **not** here — stalling the writer
is not the demo; stalling pattern 6's timer is.

```python
# app.py sketch

class Config:
    def __init__(self):
        self.pghost = _env("PGHOST", "postgres")
        self.pgdatabase = _env("PGDATABASE", "turbidity")
        self.pguser = _env("PGUSER", "turbidity")
        self.pgpassword = _env("PGPASSWORD", "turbidity")
        self.device_id = _env("DEVICE_ID", "turbidity-01")
        self.period_s = _env_float("INSERT_PERIOD_S", 2.0)
        self.ntu_setpoint = _env_float("NTU_SETPOINT", 4.0)
        self.ntu_noise = _env_float("NTU_NOISE", 0.3)
        self.http_port = _env_int("HTTP_PORT", 8080)

def insert_one(cfg, rng) -> int:
    ntu = max(0.0, rng.gauss(cfg.ntu_setpoint, cfg.ntu_noise))
    status = "ok"
    with psycopg.connect(...) as conn:
        row = conn.execute(
            "INSERT INTO reading (ts, ntu, status) VALUES (now(), %s, %s) RETURNING id",
            (ntu, status),
        ).fetchone()
        conn.commit()
        return row[0]
```

TFF filtrate, roughly 1–10 NTU, walking slowly, is enough. Do not simulate 4–20 mA. The
poll problem is the store.

### Debezium

Image: `quay.io/debezium/server:3.0` — **pin a digest** when building (`docker pull` before
the conference). Named volume for offsets and the replication-slot bookkeeping. No internet
at runtime.

```properties
# compose/debezium/application.properties

debezium.source.connector.class=io.debezium.connector.postgresql.PostgresConnector
debezium.source.plugin.name=pgoutput
debezium.source.database.hostname=postgres
debezium.source.database.port=5432
debezium.source.database.user=cdc
debezium.source.database.password=cdc
debezium.source.database.dbname=turbidity
debezium.source.topic.prefix=debezium
debezium.source.table.include.list=public.reading
debezium.source.publication.name=turbidity_cdc
debezium.source.publication.autocreate.mode=disabled
debezium.source.slot.name=debezium_turbidity
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
# Topic should land on debezium.public.reading (prefix + table), which the mapper subscribes.
```

Offset volume: `debezium-data:/debezium/data`. Slot `debezium_turbidity` is created by
Debezium on first connect; do not pre-create it in SQL.

Compose: `restart: unless-stopped`, `container_name: icc26-debezium`. No `depends_on:
chariot` — reconnect is the sink's job. A healthcheck that only proves the process is up
is enough; `tasks.py health` does not need a new probe for this.

### Mapper

```
services/cdc-mapper/
    app.py
    Dockerfile
    requirements.txt   # paho-mqtt>=2.1,<3
    README.md
```

Subscribe `debezium.public.reading` (confirm the real topic from a `mosquitto_sub` on
`debezium/#` the first time Debezium fires). Publish the envelope. Filter to **creates**
(`op == "c"` / `"r"` for snapshot). Updates and deletes are checkpoint 2 — log them, do
not put them on the UNS topic unless you decide as-built that the talk wants them.

```python
# app.py sketch

MECHANISM = "cdc"
SOURCE = {"id": "turbidity-01", "type": "turbidity-meter"}
OUT_TOPIC = "icc26/site1/downstream/tff-301/turbidity-01/telemetry"

def project(change: dict):
    payload = change.get("payload") or change
    op = payload.get("op")
    if op not in ("c", "r"):
        return None
    after = payload.get("after") or {}
    ts = after.get("ts")
    return {
        "ts": ts,
        "seq": int(after["id"]),
        "source": SOURCE,
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso_now(),
            "correlation_id": str(after["id"]),
        },
        "values": {
            "id": int(after["id"]),
            "ntu": float(after["ntu"]),
            "status": after.get("status") or "ok",
        },
    }
```

`seq` from `id` keeps CDC and poll comparable on the firehose. `correlation_id` is the
same string so a subscriber can join the two colours of one row.

House MQTT style: reconnect-with-backoff, env-config (`BROKER_HOST=chariot`), no LWT
required (this is not a field device). Duplicate the ~20-line envelope helper; no shared
lib.

### Compose services (sketch)

```yaml
  sim-turbidity:
    build: ./services/sim-turbidity
    container_name: icc26-sim-turbidity
    restart: unless-stopped
    environment:
      PGHOST: postgres
      PGDATABASE: turbidity
      PGUSER: turbidity
      PGPASSWORD: turbidity
      INSERT_PERIOD_S: ${TURBIDITY_INSERT_PERIOD_S:-2}
      NTU_SETPOINT: ${TURBIDITY_NTU_SETPOINT:-4.0}
      HTTP_PORT: 8080
      TZ: ${TZ:-America/Chicago}
    ports:
      - "${TURBIDITY_UI_PORT:-8087}:8080"
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
      IN_TOPIC: ${CDC_MAPPER_IN_TOPIC:-debezium.public.reading}
      OUT_TOPIC: icc26/site1/downstream/tff-301/turbidity-01/telemetry
    networks: [icc26]
```

Add `debezium-data` to the compose `volumes:` block.

## MQTT users + topics

| User | Subscribe | Publish |
|---|---|---|
| `debezium` | none | `debezium/#` |
| `cdc-mapper` | `debezium/#` | `icc26/site1/downstream/tff-301/turbidity-01/#` |
| `observer` | already covers `icc26/#` | none |

Do not give `debezium` an `icc26/#` publish grant. The raw WAL event must not land on the
UNS topic by accident. `mqtt-users.json` seeds on first run only — nuke, or add the users
in the Chariot UI.

UNS topic (mapper output):

```
icc26/site1/downstream/tff-301/turbidity-01/telemetry
```

Internal (Debezium output, not the talk): `debezium.public.reading` — confirm as-built.

## Envelope

```json
{
  "ts": "2026-08-23T14:03:22.145Z",
  "seq": 41,
  "source": { "id": "turbidity-01", "type": "turbidity-meter" },
  "meta": {
    "mechanism": "cdc",
    "ingest_ts": "2026-08-23T14:03:22.401Z",
    "correlation_id": "41"
  },
  "values": {
    "id": 41,
    "ntu": 4.12,
    "status": "ok"
  }
}
```

`ts` is the row's `ts` (when the meter stored it). `meta.ingest_ts` is when the mapper
published. The lag is small and is not the point — pattern 6's lag is.

## Empirical checkpoints

**1 — Schema exists after nuke.** `SHOW wal_level;` → `logical`. `\l` lists `turbidity`.
`\d reading` in that database. `SELECT * FROM pg_publication;` → `turbidity_cdc` only.

**2 — Simulator writes.** Config page on :8087. `SELECT id, ts, ntu FROM reading ORDER BY id DESC LIMIT 5;`
ids contiguous, ntu near the setpoint.

**3 — Debezium slot.** `SELECT slot_name, active FROM pg_replication_slots;` →
`debezium_turbidity`, `t`. Chariot client list shows `debezium`.

**4 — Mapper output.** One INSERT → **one** UNS message, `mechanism=cdc`, `values.ntu`
matches the row, `seq == values.id`.

**5 — Updates and deletes.** `UPDATE reading SET ntu = ntu + 1 WHERE id = …` → mapper log
shows `op=u` and `before` populated (`REPLICA IDENTITY FULL`). No UNS message (insert-only
decision). Record as-built if you choose to publish updates.

**6 — Catch-up.** Stop Debezium. Insert several rows (or let the sim run). Start Debezium.
Those ids appear on the topic, in order, no gaps. That is the WAL argument.

**7 — Pattern 6 beside it.** Same rows, second colour, same topic. A subscriber still cannot
tell from the **topic** which is which. That checkpoint lives in spec 06 but needs this
path green first.

## Verification (copy-paste)

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/downstream/tff-301/turbidity-01/telemetry' -v
```

Watch `debezium/#` in a second client while bringing Debezium up the first time, to learn
the real internal topic name.

## Closing step

Rewrite [`../05-cdc-turbidity.md`](../05-cdc-turbidity.md) from the draft into the as-built
talk track. Update architecture (Postgres roles, `sim-turbidity` / `debezium` rows in the
service table), `00-status.md`, `services/README.md`, `compose/chariot/README.md`. Swap the
placeholder schema for the vendor's when the manuals arrive — keep `id` and `REPLICA
IDENTITY FULL`. Deviations table: MQTT vs HTTP sink, insert-only vs all ops, real Debezium
topic name, image digest.
