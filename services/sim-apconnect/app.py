#!/usr/bin/env python3
"""Patterns 5 and 6 -- Anton Paar AP Connect, simulated.

This service simulates the *application*, not the instrument. That distinction is the whole
reason patterns 5 and 6 exist: a Haze 3001 turbidity module is not on the network. It plugs
into a host instrument (a DMA 4002/5002/6002 density meter or an Xsample 3200 sample
changer), the host files completed measurements into AP Connect, and AP Connect is a Windows
service with a REST API and a SQL database behind it. Nothing in that chain speaks MQTT.

So the only ways in are the store and the API, and this container is the store's writer:

    operator presses Start ──POST /measure──▶ this service ──INSERT──▶ apconnect.measurement
                                                                              │
                                        pattern 5: Debezium tails the WAL ────┤
                                        pattern 6: Ignition polls over JDBC ──┘

Four things here are deliberate and easy to mistake for oversights:

  * There is NO MQTT client in this service, and there never will be. If the simulated
    vendor application could publish, patterns 5 and 6 would both be pointless. The whole
    argument is that the application will not call you.

  * It does not free-run. `INSERT_PERIOD_S` defaults to 0. A Haze 3001 does not measure on a
    timer -- a person loads a sample and presses Start, and one completed measurement is
    filed. Every row on stage therefore exists because somebody caused it, which makes the
    catch-up demo countable: you know exactly how many measurements should arrive.

  * `result_values` is stored as the vendor's generic Variant array rather than as columns.
    AP Connect hands you key/value pairs, not a schema. Flattening them into named fields is
    the consumer's job -- services/cdc-mapper does it for pattern 5 and the Ignition script
    does it for pattern 6 -- and that projection is a talk line, not an implementation
    detail.

  * A CANCELED or FAILURE measurement writes a row with NO haze Variants at all. Absent, not
    zero. Same rule as patterns 3 and 4.

Two substitutions are recorded in compose/postgres/initdb/05-apconnect.sql and in the
deviations table of docs/05-cdc-turbidity.md: AP Connect really runs on Microsoft SQL Server,
and its real table schema is unpublished, so this models the REST API's data model instead.
The `Haze/...` Variant ids are modelled on the vendor's `Module/Quantity` convention, not
transcribed -- only the density module's ids are documented.

Standard library plus psycopg, nothing more. No paho. No FastAPI.
"""

from __future__ import annotations

import logging
import os
import random
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

import webui

# WellKnownMeasurementStatus, transcribed from the AP Connect REST manual. The last two are
# the "no reading" cases.
STATUSES = ("SUCCESS", "SUCCESS_WITH_WARNING", "SUCCESS_WITH_ERROR", "CANCELED", "FAILURE")
NO_READING = ("CANCELED", "FAILURE")

# The vendor's own conversion, from the Haze 3001 manual: 1 EBC = 4 NTU exactly. HazeNTU is
# therefore DERIVED and never independently randomised -- a consumer that finds the two
# disagreeing has found a bug in the chain, not in the instrument.
NTU_PER_EBC = 4.0


# ── config ───────────────────────────────────────────────────────────────────────────────


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logging.warning("%s is not a number, using %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


class Config:
    """Environment for what the site's AP Connect install was configured with; the config
    page for the handful of knobs a demo operator wants to turn on stage.

    Nothing here is persisted. There is no named volume on this service, deliberately -- the
    interesting state is the `measurement` table, which IS persisted, and a restart returning
    the simulator to its compose defaults is one less thing to explain when the numbers on
    the page do not match the numbers in .env.
    """

    # What the config page may change at runtime.
    ADJUSTABLE = ("ebc_setpoint", "ebc_noise", "period_s", "status_override", "sample_name")

    def __init__(self) -> None:
        self.pghost = _env("PGHOST", "postgres")
        self.pgport = _env_int("PGPORT", 5432)
        self.pgdatabase = _env("PGDATABASE", "apconnect")
        self.pguser = _env("PGUSER", "apconnect")
        self.pgpassword = _env("PGPASSWORD", "apconnect")

        self.device_id = _env("DEVICE_ID", "turbidity-01")
        self.cell = _env("CELL", "tff-301")

        # The HOST instrument, not the module. A Haze 3001 has no serial number of its own on
        # the network; AP Connect records the density meter it is fitted to.
        self.instrument_type = _env("INSTRUMENT_TYPE", "DMA 5002")
        self.instrument_serial = _env("INSTRUMENT_SERIAL", "83012345")
        self.instrument_alias = _env("INSTRUMENT_ALIAS", "TFF-301 filtrate")

        self.operator_login = _env("OPERATOR_LOGIN", "jreyes")
        self.operator_name = _env("OPERATOR_NAME", "Jordan Reyes")

        self.sample_name = _env("SAMPLE_NAME", "TFF-301 filtrate")
        self.product = _env("PRODUCT", "mAb-7 clinical")
        self.method = _env("METHOD", "Haze EBC")

        # 0 DISABLES free-running, and 0 is the default. The trigger is POST /measure, or the
        # button on the config page, or -- once spec 06 is built -- an Ignition Boolean tag
        # whose tag-change script POSTs to the same endpoint. See the module docstring.
        self.period_s = _env_float("INSERT_PERIOD_S", 0.0)

        self.ebc_setpoint = _env_float("EBC_SETPOINT", 4.0)
        self.ebc_noise = _env_float("EBC_NOISE", 0.3)
        self.cell_temperature_c = _env_float("CELL_TEMPERATURE_C", 20.0)

        # "" means the simulator files a normal SUCCESS. Set to one of STATUSES from the
        # config page to force every subsequent measurement -- that is how a CANCELED or
        # FAILURE row gets produced on demand for checkpoint 6.
        self.status_override = _env("STATUS_OVERRIDE", "")

        # How long a measurement is pretended to take, i.e. completed_ts - started_ts. AP
        # Connect carries both because metadata.timestamp is the start and the result
        # metadata's timestamp is the end.
        self.duration_s = _env_float("MEASUREMENT_DURATION_S", 22.0)

        self.http_port = _env_int("HTTP_PORT", 8080)


# ── the vendor's Variant array ───────────────────────────────────────────────────────────


def build_result_values(ebc: float, temp_c: float) -> list:
    """The `MeasurementResult.values[]` array AP Connect would hold for one haze measurement.

    The vendor's shape, not ours: every measured value is a `Variant`, and a number with a
    unit has `type: "QUANTITY"` and a `value` object of {numeric, unit, quantity} rather than
    a bare float. `numeric` is always a 64-bit float; `unit` may be "" and `quantity` may be
    "-" for a dimensionless ratio.

    The ids follow the vendor's `Module/Quantity` convention. `Density/CellTemperature` IS
    documented in the REST manual's well-known-values table. The `Haze/...` ids are NOT --
    only the density module is documented -- so they are MODELLED on that convention. Say
    "modelled on the vendor's convention" if anyone asks; never "this is their id".
    """
    def q(vid: str, name: str, numeric: float, unit: str, quantity: str) -> dict:
        return {"id": vid, "name": name, "type": "QUANTITY",
                "value": {"numeric": round(numeric, 4), "unit": unit, "quantity": quantity}}

    return [
        q("Haze/Haze",               "Haze",               ebc,                 "EBC", "HAZE"),
        q("Haze/HazeNTU",            "Haze (NTU)",         ebc * NTU_PER_EBC,   "NTU", "HAZE"),
        q("Haze/S25S0",              "Haze value S25/S0",  ebc * 0.0032,        "",    "-"),
        q("Haze/S90S0",              "Haze value S90/S0",  ebc * 0.0021,        "",    "-"),
        q("Haze/AbsorbanceS0",       "Haze absorbance S0", 0.19 + ebc * 0.006,  "",    "-"),
        # Non-ASCII on purpose. If a degree sign is mangled anywhere between here and the
        # broker, checkpoint 8 catches it -- and it is exactly the kind of thing that only
        # shows up once real vendor units are in play.
        q("Density/CellTemperature", "Cell temperature",   temp_c,              "°C", "TEMPERATURE"),
    ]


# ── the application's store ──────────────────────────────────────────────────────────────


INSERT_SQL = """
INSERT INTO measurement (
    id, measurement_completion_no, measurement_name, status, assessment,
    started_ts, completed_ts, type_id, sample_name, product, method,
    instrument_type, instrument_serial, instrument_alias,
    operator_login, operator_name, result_values
) VALUES (
    %(id)s,
    -- apc_measurementCompletionNo increments only for measurements that reach a completed
    -- state -- which, in this simulation, is all of them, including CANCELED and FAILURE.
    -- Derived in SQL rather than held in memory so it survives a container restart without
    -- this service needing a volume. Single writer, so no race to worry about.
    (SELECT COALESCE(MAX(measurement_completion_no), 0) + 1 FROM measurement),
    %(measurement_name)s, %(status)s, %(assessment)s,
    %(started_ts)s, %(completed_ts)s, %(type_id)s, %(sample_name)s, %(product)s, %(method)s,
    %(instrument_type)s, %(instrument_serial)s, %(instrument_alias)s,
    %(operator_login)s, %(operator_name)s, %(result_values)s
)
RETURNING measurement_no, id, measurement_completion_no, status, completed_ts
"""


class Store:
    """One connection per insert, opened and closed.

    A pool would be tidier and would hide the failure this demo wants visible: Postgres going
    away is the application's problem to absorb, and the next trigger should just work.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("store")

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(
            host=self.cfg.pghost,
            port=self.cfg.pgport,
            dbname=self.cfg.pgdatabase,
            user=self.cfg.pguser,
            password=self.cfg.pgpassword,
            connect_timeout=5,
        )

    def reachable(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as exc:
            self.log.warning("postgres unreachable: %s", exc)
            return False

    def insert(self, row: dict) -> dict:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, row)
                no, guid, completion_no, status, completed = cur.fetchone()
        return {
            "measurement_no": int(no),
            "id": str(guid),
            "measurement_completion_no": int(completion_no),
            "status": status,
            "completed_ts": completed.astimezone(timezone.utc)
                                     .isoformat(timespec="milliseconds")
                                     .replace("+00:00", "Z"),
        }

    def summary(self) -> dict:
        """What the config page shows about the store. Never raises."""
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(measurement_no), 0), COUNT(*) FROM measurement"
                ).fetchone()
            return {"reachable": True, "last_measurement_no": int(row[0]),
                    "row_count": int(row[1])}
        except Exception as exc:
            return {"reachable": False, "last_measurement_no": None, "row_count": None,
                    "error": str(exc)}


# ── the instrument ───────────────────────────────────────────────────────────────────────


class Instrument:
    """The Haze 3001 and its host, as far as AP Connect is concerned.

    Holds the walk state so consecutive measurements look like a filtrate stream rather than
    independent draws, and does the actual filing.
    """

    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logging.getLogger("instrument")
        self.rng = random.Random()
        self.lock = threading.Lock()
        self.ebc = cfg.ebc_setpoint
        self.filed = 0
        self.last = None          # the last row filed, for the config page
        self.last_error = None
        self._stop = threading.Event()

    # ---- the process value

    def next_ebc(self) -> float:
        """A slow walk pulled back toward the setpoint.

        TFF filtrate sits in a band and wanders inside it; white noise around a constant
        would look like a random number generator, which on a firehose is exactly what a
        sceptical audience will call it.
        """
        self.ebc += self.rng.gauss(0.0, self.cfg.ebc_noise)
        self.ebc += (self.cfg.ebc_setpoint - self.ebc) * 0.3
        return max(0.0, self.ebc)

    # ---- filing a measurement

    def measure(self, status: str = None) -> dict:
        """File one completed measurement, exactly as an operator pressing Start would.

        `status` overrides the config page's own override, which in turn overrides the
        default of SUCCESS. Nothing here randomises the status: every failure on stage should
        be one somebody asked for.
        """
        cfg = self.cfg
        status = (status or cfg.status_override or "SUCCESS").strip().upper()
        if status not in STATUSES:
            raise ValueError("status must be one of %s" % ", ".join(STATUSES))

        with self.lock:
            ebc = self.next_ebc()
            temp = self.rng.gauss(cfg.cell_temperature_c, 0.05)
            values = build_result_values(ebc, temp)

            # A canceled or failed measurement is a row with no reading. The haze Variants
            # are simply absent -- not zero, not null, not present-with-a-flag. The cell
            # temperature stays, because the cell really was at a temperature; what did not
            # happen is the turbidity measurement.
            if status in NO_READING:
                values = [v for v in values if not v["id"].startswith("Haze/")]

            completed = datetime.now(timezone.utc)
            started = completed - timedelta(seconds=cfg.duration_s)

            row = {
                "id": str(uuid.uuid4()),
                # AP Connect auto-generates a name when the operator does not type one. The
                # completion time is a reasonable stand-in and keeps names unique.
                "measurement_name": "Haze %s" % completed.strftime("%Y-%m-%d %H:%M:%S"),
                "status": status,
                # WellKnownMeasurementAssessment uses the same five strings. A measurement
                # that never produced a value has nothing to assess.
                "assessment": None if status in NO_READING else status,
                "started_ts": started,
                "completed_ts": completed,
                "type_id": "SingleMeasurement",
                "sample_name": cfg.sample_name,
                "product": cfg.product,
                "method": cfg.method,
                "instrument_type": cfg.instrument_type,
                "instrument_serial": cfg.instrument_serial,
                "instrument_alias": cfg.instrument_alias,
                "operator_login": cfg.operator_login,
                "operator_name": cfg.operator_name,
                "result_values": Jsonb(values),
            }

            try:
                filed = self.store.insert(row)
            except Exception as exc:
                self.last_error = str(exc)
                self.log.error("could not file a measurement: %s", exc)
                raise

            self.last_error = None
            self.filed += 1
            self.last = filed
            self.log.info(
                "filed measurement_no=%s id=%s status=%s%s",
                filed["measurement_no"], filed["id"], filed["status"],
                "" if status in NO_READING else " haze=%.3f EBC (%.2f NTU)"
                                                % (ebc, ebc * NTU_PER_EBC),
            )
            return filed

    # ---- the soak-test timer

    def run(self) -> None:
        """Free-running mode, for soak tests only. Off unless INSERT_PERIOD_S > 0.

        Do not turn this on for the talk. The demo is better when every row exists because
        somebody caused it -- and the catch-up checkpoint depends on knowing how many rows
        should have arrived.
        """
        if self.cfg.period_s > 0:
            self.log.warning(
                "free-running every %.1fs -- INSERT_PERIOD_S is a soak-test setting, not the "
                "talk. Set it to 0 before the demo.", self.cfg.period_s)
        else:
            self.log.info("trigger-only: POST /measure, or the button on the config page. "
                          "Nothing will be written until something asks.")

        while not self._stop.is_set():
            period = self.cfg.period_s
            if period <= 0:
                # Re-check often enough that turning the interval on from the page takes
                # effect promptly, without spinning.
                self._stop.wait(1.0)
                continue
            self._stop.wait(period)
            if self._stop.is_set():
                break
            try:
                self.measure()
            except Exception:
                # A failed insert must not kill the timer -- Postgres may simply be
                # restarting, and the next tick should just work.
                pass

    def stop(self) -> None:
        self._stop.set()


# ── the config page's view of the application ────────────────────────────────────────────


class Provider(webui.ConfigProvider):
    def __init__(self, cfg: Config, store: Store, instrument: Instrument) -> None:
        self.cfg = cfg
        self.store = store
        self.instrument = instrument

    def state(self) -> dict:
        cfg = self.cfg
        store = self.store.summary()
        return {
            "application": {
                "name": "AP Connect",
                "vendor": "Anton Paar",
                "version": "4.0 (simulated)",
                "engine": "PostgreSQL (substituted; the real product uses MS SQL Server)",
                "database": cfg.pgdatabase,
                "host": "%s:%s" % (cfg.pghost, cfg.pgport),
                "user": cfg.pguser,
            },
            "instrument": {
                "module": "Haze 3001",
                "host_type": cfg.instrument_type,
                "serial": cfg.instrument_serial,
                "alias": cfg.instrument_alias,
                "device_id": cfg.device_id,
                "cell": cfg.cell,
                "operator": "%s (%s)" % (cfg.operator_name, cfg.operator_login),
            },
            "config": {
                "ebc_setpoint": round(cfg.ebc_setpoint, 4),
                "ebc_noise": round(cfg.ebc_noise, 4),
                "period_s": round(cfg.period_s, 2),
                "status_override": cfg.status_override,
                "sample_name": cfg.sample_name,
            },
            "statuses": list(STATUSES),
            "store": store,
            "runtime": {
                "filed_this_session": self.instrument.filed,
                "last": self.instrument.last,
                "last_error": self.instrument.last_error,
            },
        }

    def apply(self, payload: dict):
        cfg = self.cfg

        def number(key, current, low, high):
            try:
                value = float(payload.get(key, current))
            except (TypeError, ValueError):
                return None, "%s must be a number" % key
            if not (low <= value <= high):
                return None, "%s must be between %s and %s" % (key, low, high)
            return value, None

        setpoint, err = number("ebc_setpoint", cfg.ebc_setpoint, 0.0, 200.0)
        if err:
            return False, err
        noise, err = number("ebc_noise", cfg.ebc_noise, 0.0, 50.0)
        if err:
            return False, err
        period, err = number("period_s", cfg.period_s, 0.0, 3600.0)
        if err:
            return False, err

        override = str(payload.get("status_override", cfg.status_override) or "").strip().upper()
        if override and override not in STATUSES:
            return False, "status must be blank or one of %s" % ", ".join(STATUSES)

        sample_name = str(payload.get("sample_name", cfg.sample_name) or "").strip()
        if not sample_name:
            return False, "sample name cannot be empty"

        cfg.ebc_setpoint = setpoint
        cfg.ebc_noise = noise
        cfg.period_s = period
        cfg.status_override = override
        cfg.sample_name = sample_name

        message = "applied -- setpoint %.2f EBC (%.2f NTU), noise %.2f" % (
            setpoint, setpoint * NTU_PER_EBC, noise)
        if period > 0:
            message += ". FREE-RUNNING every %.0fs -- that is a soak-test setting, not the " \
                       "talk." % period
        else:
            message += ". Trigger-only."
        if override:
            message += " Every measurement will be filed as %s until this is cleared." % override
        return True, message

    def measure(self, payload: dict) -> dict:
        status = payload.get("status") if isinstance(payload, dict) else None
        return self.instrument.measure(str(status).strip().upper() if status else None)


# ── main ─────────────────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    cfg = Config()
    store = Store(cfg)
    instrument = Instrument(cfg, store)

    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
    webui.serve(cfg.http_port, page, Provider(cfg, store, instrument))

    logging.info("AP Connect (simulated) up: catalog %s on %s:%s as %s, instrument %s s/n %s",
                 cfg.pgdatabase, cfg.pghost, cfg.pgport, cfg.pguser,
                 cfg.instrument_type, cfg.instrument_serial)

    # Not fatal. The healthcheck only asks whether the config page answers, and an operator
    # standing at the laptop should see the reason on the page rather than in `docker logs`.
    if not store.reachable():
        logging.warning("postgres is not answering yet -- triggers will fail until it does")

    signal.signal(signal.SIGTERM, lambda *_: instrument.stop())
    signal.signal(signal.SIGINT, lambda *_: instrument.stop())
    try:
        instrument.run()
    finally:
        logging.info("shutdown complete -- %s measurement(s) filed this session",
                     instrument.filed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
