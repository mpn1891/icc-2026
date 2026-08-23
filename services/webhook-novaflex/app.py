#!/usr/bin/env python3
"""Pattern 4 -- the same BioProfile FLEX2, imagined with only a callback URL.

READ THIS BEFORE COMPARING WITH services/opcua-novaflex.

`opcua-novaflex` is the FLEX2 as Nova actually ships it: a licensed OPC UA server with ~400
tags. This container is the *other* thing most of the room has actually integrated -- an
instrument whose entire outward surface is "POST the finished sample to a URL somebody typed
into a config screen." Nothing else. No browse, no subscribe, no query, no queue.

It is a SEPARATE CONTAINER rather than a second output bolted onto the OPC UA simulator, and
that was a deliberate choice with a cost attached. See the deviations table in
docs/04-novaflex-webhook.md. Short version:

  * Benefit -- pattern 4 is a self-contained instrument. Stopping, restarting, retoggling or
    misconfiguring it cannot disturb pattern 3, which is live and verified. The two patterns
    are demonstrably independent, which is exactly the claim the topic namespace makes.
  * Cost -- the two simulators run two independent sample lifecycles, so the SAME physical
    sample cannot appear on the topic twice by construction. `meta.correlation_id` still
    carries `sample_id` in both, and the id FORMAT is identical (S-#####), but lining the two
    counters up is a manual act. SAMPLE_ID_PREFIX / SAMPLE_ID_START exist for exactly that.

House style is duplication over a shared library -- the same convention that makes
sim-valve-mqtt and sim-valve-spb byte-for-byte comparable. `_culture()` and the result
synthesis below are COPIED from services/opcua-novaflex/app.py, not imported, and should be
diffed against it when either changes. What is copied is only what the vendor POST body
carries; the OPC-only material (ranges, flow times, consumables, RSM status, QC tag tree,
the 911-node address space) has no analogue in an HTTP callback and is not reproduced.

Standard library only. No asyncua, no requests, no paho -- an instrument that can only POST
does not need any of them, and every wheel not fetched is one fewer thing to go wrong
building this on a conference network.

Three rules this file exists to keep:

  * POST ONLY ON A COMPLETED SAMPLE. A QC run, an aborted run and a dispense timeout all
    produce no HTTP request at all. Same rule as pattern 3's tag trigger.
  * ABSENT IS ABSENT. A missing osmometer omits the `Osmo` key entirely. It never sends 0.
  * ONE ATTEMPT, NO OUTBOX. If Ignition is down the POST fails, is logged, and the sample is
    gone from the backbone's point of view. That failure IS the demo -- it is the reason
    pattern 5 exists. Building a retry queue here would steal pattern 5's line.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import signal
import ssl
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import webui

LOG = logging.getLogger("webhook-novaflex")

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_PATH = os.path.join(HERE, "page.html")

# How long an analysis sits in Running before its result lands. Same 8 s as the OPC UA
# simulator, and fixed for the same reason: a rehearsal should be predictable.
RUN_DURATION_S = 8.0
QC_DURATION_S = 5.0

GAS_PARAMS = ("pH", "pCO2", "pO2")
CHEM_PARAMS = ("Na", "K", "Ca", "NH4", "Gln", "Glu", "Gluc", "Lac")


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


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self) -> None:
        self.analyzer_id = _env("ANALYZER_ID", "FLEX2-01")
        self.device_id = _env("DEVICE_ID", "novaflex-01")
        self.serial_number = _env("SERIAL_NUMBER", "FX2-2026-0119")
        self.software_version = _env("SOFTWARE_VERSION", "4.3.1")
        self.location = _env("LOCATION", "Site 1 / QC Lab")

        # ── the callback ──
        #
        # WEBHOOK_ENABLED is the FACTORY default. The config page overrides it live and the
        # override is deliberately NOT persisted: unlike the valves' commissioned topic, this
        # is a stage control, and a restart should put the instrument back to how it shipped.
        self.webhook_enabled = _env_bool("WEBHOOK_ENABLED", True)
        self.webhook_url = _env(
            "WEBHOOK_URL",
            "https://ignition:8043/system/eventstream/icc-2026/04_webhook/novaflex-result",
        )
        self.webhook_secret = _env("WEBHOOK_SECRET", "icc26-webhook-secret")
        # Mounted read-only by compose. Loaded as an ADDITIONAL trust anchor on a context that
        # still has the system store -- we do not replace the bundle and we never disable
        # verification. See _ssl_context().
        self.webhook_ca_file = _env("WEBHOOK_CA_FILE", "/certs/icc26-ignition.crt")
        self.webhook_timeout_s = _env_float("WEBHOOK_TIMEOUT_S", 10.0)

        # ── sample identity ──
        #
        # The id FORMAT matches services/opcua-novaflex exactly (S-00001, S-00002, ...) so a
        # subscriber cannot tell the two documents apart by their sample_id shape. The
        # SEQUENCES are independent, because these are two independent simulators. Set
        # SAMPLE_ID_START on one of them to line the counters up by hand before a run where
        # you want the same id to appear under both mechanisms. Nothing automates that, and
        # docs/04-novaflex-webhook.md says so plainly.
        self.sample_id_prefix = _env("SAMPLE_ID_PREFIX", "S-")
        self.sample_id_start = _env_int("SAMPLE_ID_START", 1)
        self.sample_id_width = _env_int("SAMPLE_ID_WIDTH", 5)

        self.batch_id = _env("BATCH_ID", "BR-2026-014")
        self.vessel_id = _env("VESSEL_ID", "BRX-2000-A")
        self.cell_type = _env("CELL_TYPE", "CHO-K1")
        self.operator = _env("OPERATOR", "Auto")

        # ── fitted modules ──
        #
        # Osmo absent by default, same as the OPC UA simulator and for the same reason: it
        # keeps absent-vs-zero live on stage rather than hypothetical. Here it shows up as a
        # key that is simply not in the JSON, beside Modules.Osmo = false which IS in the
        # JSON. Absent and "deliberately not measured" are different statements.
        self.gas_installed = _env_bool("GAS_INSTALLED", True)
        self.chem_installed = _env_bool("CHEM_INSTALLED", True)
        self.cdv_installed = _env_bool("CDV_INSTALLED", True)
        self.osmo_installed = _env_bool("OSMO_INSTALLED", False)

        # ── the run cycle ──
        self.first_sample_delay_s = _env_float("FIRST_SAMPLE_DELAY_S", 15.0)
        self.sample_interval_s = _env_float("SAMPLE_INTERVAL_S", 120.0)
        # Every Nth free-running cycle runs an onboard QC instead of a sample. A QC POSTs
        # NOTHING -- checkpoint 5. 0 disables.
        self.qc_every_n = _env_int("QC_EVERY_N", 6)
        # One analyte's sensor errors: the sample still completes and still POSTs, but that
        # analyte's key is absent from the body.
        self.sensor_error_rate = _env_float("SENSOR_ERROR_RATE", 0.0)
        # The whole analysis fails (dispense timeout). No POST at all.
        self.failure_rate = _env_float("FAILURE_RATE", 0.0)

        # ── the culture ──
        self.culture_span_samples = _env_int("CULTURE_SPAN_SAMPLES", 60)
        self.viability_start = _env_float("VIABILITY_START", 98.0)
        self.viability_end = _env_float("VIABILITY_END", 84.0)
        self.density_start = _env_float("DENSITY_START", 0.4)     # 10^6 cells/mL
        self.density_peak = _env_float("DENSITY_PEAK", 12.0)
        self.glucose_start = _env_float("GLUCOSE_START", 6.0)     # g/L
        self.glucose_end = _env_float("GLUCOSE_END", 1.4)
        self.lactate_start = _env_float("LACTATE_START", 0.2)     # g/L
        self.lactate_end = _env_float("LACTATE_END", 2.6)

        self.seed = _env_int("RANDOM_SEED", 0)
        self.http_port = _env_int("HTTP_PORT", 8080)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    """ISO-8601 UTC with milliseconds -- byte-identical in shape to pattern 3's `_iso`."""
    if value is None:
        value = _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _drop_nones(value):
    """Recursively remove None-valued keys, and any sub-object left empty by that.

    This is the absent-vs-zero rule, applied once at the edge instead of remembered at every
    assignment. `False` and `0` survive -- they are measurements. Only None is absence.
    """
    if not isinstance(value, dict):
        return value
    cleaned = {}
    for key, item in value.items():
        item = _drop_nones(item)
        if item is None:
            continue
        if isinstance(item, dict) and not item:
            continue
        cleaned[key] = item
    return cleaned


# ── the instrument ───────────────────────────────────────────────────────────────────────


class Flex2Webhook:
    """The sample lifecycle, the result synthesis, and the one HTTP request it can make."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed or None)

        self.stopping = threading.Event()
        self.trigger = threading.Event()
        self.lock = threading.RLock()

        self.started_at = _now()
        self.sample_no = cfg.sample_id_start - 1
        self.completed = 0
        self.qc_completed = 0
        self.cycle = 0

        # Live, page-editable copies of the three factory settings.
        self.enabled = cfg.webhook_enabled
        self.url = cfg.webhook_url
        self.secret = cfg.webhook_secret

        self.pending_kind = ""        # "sample" | "qc", set by the page's trigger buttons
        self.inject_failure = False

        self.state = "Idle"
        self.posts_ok = 0
        self.posts_failed = 0
        self.last_sample: dict | None = None
        self.last_post: dict | None = None
        self.next_due_at: datetime | None = None

        self.ssl_context = self._ssl_context()

    # ---- TLS

    def _ssl_context(self) -> ssl.SSLContext:
        """Verify the gateway certificate. Never skip verification.

        `seed` restores the machine-local ssl.pfx, whose SAN is `localhost` -- not `ignition`,
        which is the name this container has to dial over the compose network. So the hostname
        check is off and the SIGNATURE check is not. That is a real, narrow concession to a
        self-signed demo certificate, and it is the same one services/lims made.
        """
        context = ssl.create_default_context()
        ca_file = self.cfg.webhook_ca_file
        if ca_file and os.path.isfile(ca_file):
            context.load_verify_locations(ca_file)
            context.check_hostname = False
            LOG.info("trusted gateway certificate %s (hostname check off: SAN is localhost)",
                     ca_file)
        elif self.cfg.webhook_url.startswith("https://"):
            LOG.warning(
                "WEBHOOK_CA_FILE %s is missing; HTTPS posts will fail until it is mounted. "
                "Run `python tasks.py seed` or `enable-ssl` to generate it.", ca_file)
        return context

    # ---- the culture
    #
    # COPIED from services/opcua-novaflex/app.py. Diff the two when either changes.

    def _culture(self) -> dict[str, float]:
        """A fed-batch CHO culture, sampled once per analysis.

        Not a kinetic model and not trying to be -- it exists so the trend charts move the way
        an audience expects: glucose consumed while lactate and ammonia accumulate, viable
        density peaking then falling as viability declines. Deterministic given RANDOM_SEED.
        """
        cfg = self.cfg
        rng = self.rng
        span = max(1, cfg.culture_span_samples)
        t = min(1.0, self.completed / span)

        def lerp(a: float, b: float, frac: float) -> float:
            return a + (b - a) * frac

        peak_at = 0.7
        if t <= peak_at:
            density = lerp(cfg.density_start, cfg.density_peak, math.sin(t / peak_at * math.pi / 2))
        else:
            density = lerp(cfg.density_peak, cfg.density_peak * 0.55,
                           (t - peak_at) / (1 - peak_at))
        viability = lerp(cfg.viability_start, cfg.viability_end, t ** 2)

        return {
            "viable_density": max(0.05, density * rng.gauss(1.0, 0.03)),
            "viability": min(100.0, max(0.0, viability + rng.gauss(0.0, 0.5))),
            "Gluc": max(0.05, lerp(cfg.glucose_start, cfg.glucose_end, t) + rng.gauss(0, 0.08)),
            "Lac": max(0.0, lerp(cfg.lactate_start, cfg.lactate_end, t) + rng.gauss(0, 0.05)),
            "Gln": max(0.0, lerp(4.0, 0.4, t) + rng.gauss(0, 0.08)),
            "Glu": max(0.0, lerp(0.5, 2.4, t) + rng.gauss(0, 0.06)),
            "NH4": max(0.0, lerp(0.9, 5.8, t) + rng.gauss(0, 0.1)),
            "Na": lerp(145.0, 158.0, t) + rng.gauss(0, 0.8),
            "K": lerp(4.4, 6.1, t) + rng.gauss(0, 0.08),
            "Ca": 1.10 + rng.gauss(0, 0.03),
            "pH": lerp(7.16, 6.96, t) + rng.gauss(0, 0.015),
            "pCO2": lerp(45.0, 78.0, t) + rng.gauss(0, 1.5),
            "pO2": lerp(125.0, 62.0, t) + rng.gauss(0, 3.0),
            "Osmo": lerp(298.0, 382.0, t) + rng.gauss(0, 2.5),
            "diameter": lerp(16.6, 14.4, t) + rng.gauss(0, 0.15),
        }

    # ---- one analysis

    def _synthesize(self, ts: datetime, sample_id: str,
                    sample_source: str) -> tuple[dict[str, object], list[str]]:
        """One analysis, as the flat vendor path -> value dict.

        The keys are the FLEX2's own historical-result paths, and they are the same strings
        `services/opcua-novaflex` writes into its address space -- deliberately, so the two
        files can be diffed. A path absent from this dict is absent from the POST body; there
        is no zero anywhere in this function standing in for "did not measure".

        Reproduced from the OPC UA simulator: SampleInformation, Modules, Gas, Chem, Osmo,
        CellDensity, CalculatedResults. Not reproduced, because an HTTP callback has no
        analogue for them: Ranges, FlowTimeData, consumables, RSM status, TimeInTray,
        ErrorStatus text, the ICC26Extensions branch.
        """
        cfg = self.cfg
        rng = self.rng
        culture = self._culture()
        errors: list[str] = []

        values: dict[str, object] = {
            "SampleTime": _iso(ts),
            "StartTags/SampleSource": sample_source,
            "StartTags/Operator": cfg.operator,
            "StartTags/SampleInformation/SampleID": sample_id,
            "StartTags/SampleInformation/BatchID": cfg.batch_id,
            "StartTags/SampleInformation/VesselID": cfg.vessel_id,
            "StartTags/SampleInformation/CellType": cfg.cell_type,
            # The vendor's own absent-vs-zero mechanism: which modules took part in THIS
            # analysis. `false` here is true information and is published; the corresponding
            # Result key is simply not there.
            "StartTags/ModuleInformation/Modules/CDV": cfg.cdv_installed,
            "StartTags/ModuleInformation/Modules/Chemistry": cfg.chem_installed,
            "StartTags/ModuleInformation/Modules/Gas": cfg.gas_installed,
            "StartTags/ModuleInformation/Modules/Osmo": cfg.osmo_installed,
        }

        readings: dict[str, float] = {}
        if cfg.gas_installed:
            readings.update({p: culture[p] for p in GAS_PARAMS})
        if cfg.chem_installed:
            readings.update({p: culture[p] for p in CHEM_PARAMS})
        if cfg.osmo_installed:
            readings["Osmo"] = culture["Osmo"]

        errored = set()
        for param in list(readings):
            if rng.random() < cfg.sensor_error_rate:
                errored.add(param)
                errors.append("%s: sensor out of range" % param)

        digits = {"pH": 2, "Ca": 2, "K": 2, "Gln": 2, "Glu": 2, "Gluc": 2, "Lac": 2, "NH4": 2}
        for param, value in readings.items():
            if param in errored:
                continue
            branch = "Gas" if param in GAS_PARAMS else "Chem" if param in CHEM_PARAMS else None
            prefix = "%s/%s" % (branch, param) if branch else "Osmo"
            values["%s/Result" % prefix] = round(value, digits.get(param, 1))

        # Calculated results need pH and pCO2; if either errored there is nothing to derive.
        if cfg.gas_installed and not ({"pH", "pCO2"} & errored):
            ph = readings["pH"]
            pco2 = readings["pCO2"]
            # Henderson-Hasselbalch, the same relation a blood-gas analyzer uses.
            hco3 = 0.0307 * pco2 * (10 ** (ph - 6.105))
            values["CalculatedResults/HCO3"] = round(hco3, 1)
            total_co2 = hco3 + 0.0307 * pco2
            values["CalculatedResults/CO2Saturation"] = round(hco3 / total_co2 * 100.0, 1)
            if "pO2" not in errored:
                po2 = readings["pO2"]
                # Severinghaus.
                so2 = 100.0 / (23400.0 / (po2 ** 3 + 150.0 * po2) + 1.0)
                values["CalculatedResults/O2Saturation"] = round(so2, 1)

        if cfg.cdv_installed:
            viable = culture["viable_density"]
            viability = culture["viability"]
            values.update({
                "CellDensity/TotalDensity": round(viable / (viability / 100.0), 2),
                "CellDensity/ViableDensity": round(viable, 2),
                "CellDensity/Viability": round(viability, 1),
                "CellDensity/AvgLiveDiameter": round(culture["diameter"], 1),
            })

        return values, errors

    # ---- the POST body

    def _vendor_payload(self, historical: dict) -> dict:
        """What a FLEX2 with only a callback URL would POST. NOT our envelope.

        The instrument does not know about `meta.mechanism`, `seq`, or an ISA-95 topic
        namespace, and pretending otherwise would be the single most dishonest thing this
        simulator could do -- the whole point of pattern 4 is that the wrapping is Ignition's
        job. The Event Stream transform (webhook_event.build_novaflex_result) turns this into
        the pattern-3 envelope.
        """
        body = {
            "SampleID": historical.get("StartTags/SampleInformation/SampleID"),
            "BatchID": historical.get("StartTags/SampleInformation/BatchID"),
            "VesselID": historical.get("StartTags/SampleInformation/VesselID"),
            "CellType": historical.get("StartTags/SampleInformation/CellType"),
            "SampleTime": historical.get("SampleTime"),
            # Pattern 3 publishes values.sample_source off the vendor StartTags/SampleSource
            # tag. Carrying it here too is what keeps the two `values` key sets identical --
            # the claim "a subscriber cannot tell the documents apart" is only as good as the
            # narrowest of the two trees.
            "SampleSource": historical.get("StartTags/SampleSource"),
            "Operator": historical.get("StartTags/Operator"),
            "Gas": {
                "pH": historical.get("Gas/pH/Result"),
                "pCO2": historical.get("Gas/pCO2/Result"),
                "pO2": historical.get("Gas/pO2/Result"),
            },
            "Chem": {
                "Na": historical.get("Chem/Na/Result"),
                "K": historical.get("Chem/K/Result"),
                "Ca": historical.get("Chem/Ca/Result"),
                "NH4": historical.get("Chem/NH4/Result"),
                "Gln": historical.get("Chem/Gln/Result"),
                "Glu": historical.get("Chem/Glu/Result"),
                "Gluc": historical.get("Chem/Gluc/Result"),
                "Lac": historical.get("Chem/Lac/Result"),
            },
            "Osmo": historical.get("Osmo/Result"),   # None when the module is not fitted
            "CellDensity": {
                "TotalDensity": historical.get("CellDensity/TotalDensity"),
                "ViableDensity": historical.get("CellDensity/ViableDensity"),
                "Viability": historical.get("CellDensity/Viability"),
                "AvgLiveDiameter": historical.get("CellDensity/AvgLiveDiameter"),
            },
            "Calculated": {
                "HCO3": historical.get("CalculatedResults/HCO3"),
                "O2Saturation": historical.get("CalculatedResults/O2Saturation"),
                "CO2Saturation": historical.get("CalculatedResults/CO2Saturation"),
            },
            "Modules": {
                "CDV": bool(historical.get("StartTags/ModuleInformation/Modules/CDV")),
                "Chemistry": bool(historical.get("StartTags/ModuleInformation/Modules/Chemistry")),
                "Gas": bool(historical.get("StartTags/ModuleInformation/Modules/Gas")),
                "Osmo": bool(historical.get("StartTags/ModuleInformation/Modules/Osmo")),
            },
        }
        # Absent analyte: omit the key. Do not send 0. `Modules` survives because false is a
        # measurement of intent, not an absence.
        return _drop_nones(body)

    # ---- the one HTTP request this instrument can make

    def _post_result(self, historical: dict) -> None:
        """One attempt. No retry, no queue, no outbox.

        A real instrument might buffer; most do not, and the ones that do buffer badly. The
        failed POST is pattern 4's honest ending and the reason pattern 5 exists, so a retry
        loop here would be building pattern 5 in the wrong container.
        """
        with self.lock:
            enabled, url, secret = self.enabled, self.url, self.secret
        sample_id = historical.get("StartTags/SampleInformation/SampleID")

        if not enabled or not url:
            with self.lock:
                self.last_post = {
                    "ok": None, "at": _iso(), "sample_id": sample_id,
                    "status": None,
                    "detail": "POST disabled on the device page" if not enabled
                              else "no callback URL configured",
                }
            LOG.info("sample %s complete -- POST suppressed (%s)", sample_id,
                     "disabled" if not enabled else "no URL")
            return

        payload = self._vendor_payload(historical)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": secret,
                "User-Agent": "BioProfile-FLEX2/%s" % self.cfg.software_version,
            },
        )
        # Always attach the HTTPS handler explicitly. Ignition redirects :8088 to :8043, and a
        # bare urlopen would follow that redirect with the default trust store and fail on the
        # self-signed certificate for reasons that look nothing like the actual cause.
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ssl_context)
        )
        try:
            with opener.open(request, timeout=self.cfg.webhook_timeout_s) as response:
                status = response.status
                response.read()
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx is a reachable receiver that said no. Worth distinguishing from a
            # connection failure on the page, because on stage they mean different things:
            # one is a wrong secret, the other is a gateway that is down.
            try:
                exc.read()
            except Exception:
                pass
            self._record_post(False, sample_id, exc.code, "HTTP %s" % exc.code)
            LOG.warning("webhook POST for %s rejected: HTTP %s", sample_id, exc.code)
            return
        except Exception as exc:
            self._record_post(False, sample_id, None, str(exc))
            LOG.warning("webhook POST for %s failed: %s", sample_id, exc)
            return
        self._record_post(True, sample_id, status, None)
        LOG.info("webhook POST for %s -> HTTP %s (%s bytes)", sample_id, status, len(body))

    def _record_post(self, ok: bool, sample_id, status, detail) -> None:
        with self.lock:
            if ok:
                self.posts_ok += 1
            else:
                self.posts_failed += 1
            self.last_post = {
                "ok": ok, "at": _iso(), "sample_id": sample_id,
                "status": status, "detail": detail,
            }

    # ---- the run cycle

    def _run_sample(self, sample_source: str = "ESM") -> None:
        cfg = self.cfg
        self.sample_no += 1
        sample_id = "%s%0*d" % (cfg.sample_id_prefix, cfg.sample_id_width, self.sample_no)

        with self.lock:
            self.state = "Running"
        LOG.info("analysis %s running for %.1fs", sample_id, RUN_DURATION_S)
        if self.stopping.wait(RUN_DURATION_S):
            return

        if self.inject_failure or self.rng.random() < cfg.failure_rate:
            self.inject_failure = False
            reason = "Dispense Timeout: sample not received within 25 minutes"
            with self.lock:
                self.state = "Error"
                self.last_sample = {"sample_id": sample_id, "at": _iso(),
                                    "outcome": "failed", "detail": reason}
            LOG.warning("analysis %s FAILED (%s) -- no POST", sample_id, reason)
            return

        ts = _now()
        historical, errors = self._synthesize(ts, sample_id, sample_source)
        self.completed += 1
        with self.lock:
            self.state = "Completed"
            self.last_sample = {
                "sample_id": sample_id,
                "at": _iso(ts),
                "outcome": "completed",
                "detail": "%s sensor error(s)" % len(errors) if errors else "all sensors good",
                "body": self._vendor_payload(historical),
            }
        LOG.info("analysis %s complete: %s -- completed=%s", sample_id,
                 "%s sensor error(s)" % len(errors) if errors else "all sensors good",
                 self.completed)

        # The only call site. Not reachable from _run_qc and not reachable from either of the
        # two early returns above.
        self._post_result(historical)
        with self.lock:
            self.state = "Idle"

    def _run_qc(self) -> None:
        """An onboard QC analysis. POSTs nothing, deliberately -- checkpoint 5.

        The FLEX2 manual's own note at the top of section 9 says HistoricalSampleResults
        excludes QC, and pattern 3 keys off that tree. Pattern 4 must be equally quiet or the
        two mechanisms stop being comparable. There is no code path from here to _post_result.
        """
        with self.lock:
            self.state = "QualityControl"
        LOG.info("QC Level 1 (Chemistry) running for %.1fs -- no POST", QC_DURATION_S)
        if self.stopping.wait(QC_DURATION_S):
            return
        self.qc_completed += 1
        with self.lock:
            self.state = "Idle"
            self.last_sample = {
                "sample_id": None, "at": _iso(), "outcome": "qc",
                "detail": "onboard QC Level 1 -- excluded from the sample stream, no POST",
            }

    def run(self) -> None:
        delay = self.cfg.first_sample_delay_s
        while not self.stopping.is_set():
            with self.lock:
                self.next_due_at = datetime.fromtimestamp(
                    _now().timestamp() + delay, tz=timezone.utc)
            fired = self.trigger.wait(delay)
            self.trigger.clear()
            if self.stopping.is_set():
                return
            self.cycle += 1
            kind = self.pending_kind
            self.pending_kind = ""
            try:
                if kind == "qc":
                    self._run_qc()
                elif kind == "sample":
                    self._run_sample("Manual")
                elif (self.cfg.qc_every_n and not fired
                        and self.cycle % self.cfg.qc_every_n == 0):
                    # Free-running cycles occasionally run QC instead of a sample. An operator
                    # trigger is always what the operator asked for.
                    self._run_qc()
                else:
                    self._run_sample("ESM")
            except Exception:
                LOG.exception("analysis failed unexpectedly")
                with self.lock:
                    self.state = "Error"
            delay = self.cfg.sample_interval_s


# ── the device's configuration page ──────────────────────────────────────────────────────


class WebhookConfigProvider(webui.ConfigProvider):
    """What the embedded page can read from the instrument, and do to it.

    Same shape as the valves' provider (services/sim-valve-mqtt/webui.py), because it is the
    same idea: a small commissioning UI served by the device itself. The controls differ
    because the device differs -- there is no topic here, no QoS and no retained flag. There
    is a URL, a shared secret and a switch. That is the entire integration surface, and the
    contrast with pattern 1's page is worth a screenshot.
    """

    def __init__(self, flex: Flex2Webhook) -> None:
        self.flex = flex

    def state(self) -> dict:
        flex = self.flex
        cfg = flex.cfg
        with flex.lock:
            next_due = flex.next_due_at
            state = {
                "device": {
                    "id": cfg.device_id,
                    "analyzer_id": cfg.analyzer_id,
                    "serial": cfg.serial_number,
                    "software": cfg.software_version,
                    "location": cfg.location,
                },
                "config": {
                    "enabled": flex.enabled,
                    "url": flex.url,
                    "secret": flex.secret,
                },
                "factory": {
                    "enabled": cfg.webhook_enabled,
                    "url": cfg.webhook_url,
                    "ca_file": cfg.webhook_ca_file,
                    "ca_present": bool(cfg.webhook_ca_file
                                       and os.path.isfile(cfg.webhook_ca_file)),
                    "timeout_s": cfg.webhook_timeout_s,
                },
                "modules": {
                    "gas": cfg.gas_installed,
                    "chemistry": cfg.chem_installed,
                    "cdv": cfg.cdv_installed,
                    "osmo": cfg.osmo_installed,
                },
                "runtime": {
                    "state": flex.state,
                    "completed": flex.completed,
                    "qc_completed": flex.qc_completed,
                    "posts_ok": flex.posts_ok,
                    "posts_failed": flex.posts_failed,
                    "next_sample_in_s": max(0, int(next_due.timestamp() - _now().timestamp()))
                                        if next_due else None,
                    "sample_interval_s": cfg.sample_interval_s,
                    "qc_every_n": cfg.qc_every_n,
                    "inject_failure": flex.inject_failure,
                    "last_sample": flex.last_sample,
                    "last_post": flex.last_post,
                },
            }
        return state

    def apply(self, payload: dict):
        flex = self.flex
        url = str(payload.get("url") or "").strip()
        secret = str(payload.get("secret") or "").strip()
        enabled = bool(payload.get("enabled"))
        if enabled and not url:
            return False, "A callback URL is required to enable POST."
        if url and not (url.startswith("https://") or url.startswith("http://")):
            return False, "URL must start with https:// or http://"
        with flex.lock:
            flex.enabled = enabled
            flex.url = url
            flex.secret = secret
        LOG.info("callback reconfigured: enabled=%s url=%s", enabled, url or "(none)")
        if url.startswith("http://"):
            return True, ("Saved. Note: plain HTTP -- Ignition redirects :8088 to :8043, so "
                          "this will still end up on TLS.")
        return True, "Saved. Applies to the next completed sample."

    def trigger_run(self, kind: str) -> dict:
        flex = self.flex
        flex.pending_kind = "qc" if kind == "qc" else "sample"
        flex.trigger.set()
        return {"queued": flex.pending_kind}

    def set_inject_failure(self, on: bool) -> None:
        self.flex.inject_failure = bool(on)
        LOG.info("inject_failure=%s", self.flex.inject_failure)


# ── entry point ──────────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    cfg = Config()
    flex = Flex2Webhook(cfg)
    provider = WebhookConfigProvider(flex)

    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        signal.signal(sig, lambda *_: flex.stopping.set())

    webui.serve(cfg.http_port, PAGE_PATH, provider)
    LOG.info("callback %s -> %s", "ENABLED" if flex.enabled else "DISABLED",
             flex.url or "(no URL)")
    LOG.info("modules: gas %s, chem %s, cdv %s, osmo %s -- sample ids %s%0*d and up",
             "yes" if cfg.gas_installed else "NO",
             "yes" if cfg.chem_installed else "NO",
             "yes" if cfg.cdv_installed else "NO",
             "yes" if cfg.osmo_installed else "NO",
             cfg.sample_id_prefix, cfg.sample_id_width, cfg.sample_id_start)

    worker = threading.Thread(target=flex.run, name="analysis", daemon=True)
    worker.start()
    flex.stopping.wait()
    LOG.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
