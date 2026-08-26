#!/usr/bin/env python3
"""The instrument's own sample-login touchscreen.

Every bench analyzer has one. On a real FLEX2 an operator stands in front of it,
scans or types the sample id off the paperwork, and presses Run. That step is
the reason this file exists: **the sample id is minted by the sample valve on
BR-201 and gets into this instrument by a human retyping it.** Pattern 1's GxP
claim is "the record originates at the point of action -- no transcription, no
intermediary", and this screen is the intermediary, with a keyboard.

Which means it can be typed wrong, and the demo depends on that being possible.
A mistyped id produces a result the LIMS cannot attach to any sample: the valve's
entry sits open with no analysis, the analysis sits parked with no entry, and
both are visible on one screen. That is not a defect in this page.

Two rules the page follows, and neither is negotiable:

  * **It goes through the vendor's own contract.** Run writes the sample metadata
    into `OPCSystemCommands/ESMScheduleAnalysis/SampleInformation/*` and then sets
    the `ESMScheduleAnalysis` bit -- the same nodes, in the same order, that
    Ignition's `bioanalyzer` UDT drives from `command/sample_id` and
    `command/esm_schedule_analysis`. It does not call `_run_sample()` behind the
    address space's back. The whole of pattern 3's argument is that this
    instrument ships 104 writable bits and zero methods; a page that shortcuts
    around that stops demonstrating it.

  * **It runs on the HTTP thread and the instrument runs on asyncio.** Every call
    into the analyzer is marshalled onto its event loop with
    `run_coroutine_threadsafe`. Touching asyncua nodes from this thread directly
    would be a data race against the OPC UA services.

One asymmetry worth noticing on stage: this page can say "an analysis is already
running" and refuse. An OPC UA client writing the same bit cannot -- a tag write
has no return value, and the instrument documents no rejection path, so all a
client gets is that nothing happened. The page knows because it is inside the
instrument.

`http.server` from the standard library, one HTML file with its CSS and JS
inline, no external assets of any kind -- the demo has to run with networking
disabled (docs/00-architecture.md). Same convention as the two valve pages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("webui")

# How long a page request will wait on the instrument's event loop. Generous
# enough for a handful of node writes, short enough that a wedged loop shows as
# an error on the screen rather than a browser that hangs forever.
CALL_TIMEOUT_S = 10.0

# Where the sample metadata lives. The ESM scheduler, because that is the sampling
# path BR-201 feeds; the autosampler and OLS schedulers have their own copies of
# the same fields, which is the vendor's design, not a duplication to tidy up.
SCHEDULER = "ESMScheduleAnalysis"
INFO = SCHEDULER + "/SampleInformation"

# form field -> vendor tag under INFO. `sample_id` is deliberately first: it is
# the one that matters and the one a person types.
INFO_FIELDS = (
    ("sample_id", "SampleID"),
    ("batch_id", "BatchID"),
    ("vessel_id", "VesselID"),
    ("cell_type", "CellType"),
)


class Console:
    """What the sample-login page can ask the instrument for, and do to it.

    Holds the `Novaflex` and the event loop it lives on. Everything public here is
    called from an HTTP thread and returns plain JSON-able data.
    """

    def __init__(self, flex, loop: asyncio.AbstractEventLoop) -> None:
        self.flex = flex
        self.loop = loop

    # ---- plumbing

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(CALL_TIMEOUT_S)

    async def _read(self, leaf, default=None):
        """Read a node, tolerating Bad quality and a missing leaf.

        The result tree is Bad_NoData until the first analysis, by design -- a
        client that cannot tell that apart from a tree of zeros is meant to find
        out. This page is such a client, so it has to cope.
        """
        if leaf is None:
            return default
        try:
            value = await leaf.node.read_value()
        except Exception:
            return default
        return default if value is None else value

    # ---- reads

    def state(self) -> dict:
        return self._call(self._state())

    async def _state(self) -> dict:
        import app  # deferred: app imports this module at its own module level

        names = {code: name for name, code in app.UNIT_STATE.items()}
        flex, cfg = self.flex, self.flex.cfg
        leaves = flex.command_leaves
        historical = flex.historical_branch.leaves if flex.historical_branch else {}

        state_code = await self._read(flex.ext.get("State"), -1)
        last_time = await self._read(historical.get("SampleTime"))
        return {
            "analyzer_id": cfg.analyzer_id,
            "state": names.get(state_code, "Unknown"),
            "running": state_code == app.UNIT_STATE["Running"],
            "sample_no": flex.sample_no,
            # 0 means the instrument only runs when somebody presses Run, which is
            # what the demo wants: a free-running analyzer invents sample ids
            # nobody transcribed, and every one of them lands unmatched.
            "free_running": cfg.sample_interval_s > 0,
            "interval_s": cfg.sample_interval_s,
            "sample_types": cfg.sample_types,
            "loaded": {
                key: await self._read(leaves.get("%s/%s" % (INFO, tag)), "")
                for key, tag in INFO_FIELDS
            },
            "sample_type": await self._read(leaves.get(SCHEDULER + "/SampleType"), ""),
            "operator": await self._read(leaves.get(SCHEDULER + "/Operator"), ""),
            "last_result": {
                "sample_id": await self._read(
                    historical.get("StartTags/SampleInformation/SampleID"), ""),
                "sample_time": last_time.isoformat() if hasattr(last_time, "isoformat")
                               else (last_time or ""),
            },
        }

    # ---- actions

    def run_sample(self, payload: dict):
        """Returns (ok: bool, message: str)."""
        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            return False, "Sample ID is required. Scan or type the id from the sample valve."
        return self._call(self._run_sample(sample_id, payload))

    async def _run_sample(self, sample_id: str, payload: dict):
        import app

        state = await self._read(self.flex.ext.get("State"), -1)
        if state == app.UNIT_STATE["Running"]:
            return False, "An analysis is already running."

        ts = app._now()
        leaves = self.flex.command_leaves
        # Arguments first, trigger last, exactly as the vendor's contract requires
        # and exactly as an Ignition tag write would have to. Nothing here is
        # atomic -- that is the contract, not an oversight in this page.
        await leaves["%s/SampleID" % INFO].write(sample_id, ts)
        for key, tag in INFO_FIELDS[1:]:
            value = str(payload.get(key) or "").strip()
            if value:
                await leaves["%s/%s" % (INFO, tag)].write(value, ts)
        for key, path in (("sample_type", SCHEDULER + "/SampleType"),
                          ("operator", SCHEDULER + "/Operator")):
            value = str(payload.get(key) or "").strip()
            if value:
                await leaves[path].write(value, ts)

        await leaves["%s/%s" % (SCHEDULER, SCHEDULER)].write(True, ts)
        LOG.info("sample login: %s queued by %s",
                 sample_id, str(payload.get("operator") or "Auto"))
        return True, "Analysis started for %s." % sample_id

    def run_qc(self, level: str):
        return self._call(self._run_qc("2" if str(level).strip() == "2" else "1"))

    async def _run_qc(self, level: str):
        """Run an onboard QC.

        Kept as a button because QC otherwise only fires on free-running cycles
        (`QC_EVERY_N`), and this instrument no longer free-runs. Losing it would
        lose the manual's own section 9 warning made visible: QC writes QCResults
        and increments QcCompleteCounter, and never touches the sample tree.
        """
        import app

        state = await self._read(self.flex.ext.get("State"), -1)
        if state == app.UNIT_STATE["Running"]:
            return False, "An analysis is already running."
        command = "ChemistryQcLevel" + level
        await self.flex.command_leaves["%s/%s" % (command, command)].write(
            True, app._now())
        LOG.info("sample login: %s requested", command)
        return True, "%s started -- no sample result, and no sample counter." % command


class _Handler(BaseHTTPRequestHandler):
    server_version = "BioProfileFLEX2/1.0"
    console: Console = None   # set on the server instance below
    page: bytes = b""

    # BaseHTTPRequestHandler logs every request to stderr in its own format. Route
    # it into the same logger as everything else so `docker logs` reads as one
    # stream. Same trick as the valve pages.
    def log_message(self, fmt, *args):
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ---- helpers

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, document: dict) -> None:
        self._send(status, json.dumps(document).encode("utf-8"), "application/json")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routes

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")
        if path == "/api/state":
            return self._send_json(200, self.console.state())
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError):
            return self._send_json(400, {"ok": False, "message": "body was not JSON"})

        try:
            if path == "/api/run":
                ok, message = self.console.run_sample(payload)
                return self._send_json(200 if ok else 409,
                                       {"ok": ok, "message": message,
                                        "state": self.console.state()})
            if path == "/api/qc":
                ok, message = self.console.run_qc(payload.get("level") or "1")
                return self._send_json(200 if ok else 409,
                                       {"ok": ok, "message": message,
                                        "state": self.console.state()})
        except Exception as exc:  # a touchscreen must never take the instrument down
            LOG.exception("request to %s failed", path)
            return self._send_json(500, {"ok": False, "message": str(exc)})

        self._send_json(404, {"ok": False, "message": "not found"})


def serve(port: int, page_path: str, console: Console) -> ThreadingHTTPServer:
    """Start the sample-login server on a daemon thread and return it.

    The page is read once at startup rather than per request: it is an
    instrument's firmware UI, not a template, and editing it means rebuilding the
    image anyway. Same as the valve pages.
    """
    with open(page_path, "rb") as handle:
        page = handle.read()

    handler = type("Handler", (_Handler,), {"console": console, "page": page})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, name="sample-login", daemon=True).start()
    LOG.info("sample login page on http://0.0.0.0:%s", port)
    return httpd
