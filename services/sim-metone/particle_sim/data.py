"""The instrument: a rolling sample buffer, a sampling run, and a dirty room.

Three things in here are load-bearing for pattern 6 and none of them is an
accident.

**The cursor is a keyset marker, not an offset.** ``eyJpZCI6MX0=`` is base64 of
``{"id": 1}``. ``get_samples(cursor, limit)`` returns records whose
``sequenceNumber`` is strictly greater than that id, oldest first. Because new
analyses append ever-increasing ids, *"everything after bookmark 42"* and
*"everything new since I last looked"* are the same sentence -- the vendor
shipped a change feed and documented it as pagination. Nothing in this file was
added to make that work; it falls out of keyset paging.

**The buffer does not survive a restart, and that is the trap.** Sequence numbers
start at 1 again while a poller's stored bookmark still says 45, so the server
answers "nothing after 45" -- correctly -- and the poll goes silent with every
health check green. Persisting the buffer would remove the best failure demo in
pattern 6. What *does* persist, in ``/config``, is the operator's sample point,
the room condition and whether a run was going.

**The simulator does not know what a cleanroom limit is.** It has a clean
distribution and a dirty one, and no notion of a threshold. The excursion rule
is Ignition's, lives once on the UDT, and is applied by ``metone_poll`` at
ingest. See docs/00-architecture.md § *Derived flags travel with the fact that
produced them*.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger("particle_sim.data")

# Counts per 28.3 L (1 CFM for one minute) in a clean room, by channel size.
# These are the vendor sample response's own numbers -- 1523 / 842 / 215 / 42 /
# 8 / 1 -- so a 10 s sample at 4.717 L scales them by ~1/6 and lands on the
# 254 / 140 / 36 / 7 / 1 / 0 in the pattern-6 payload contract.
CLEAN_PER_28L = {
    0.3: 1523.0,
    0.5: 842.0,
    1.0: 215.0,
    3.0: 42.0,
    5.0: 8.0,
    10.0: 1.0,
}

# Any channel size not in the table above falls back to a power law through it.
_FALLBACK_EXPONENT = -2.6

STATE_FILE = "state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    """'2026-08-29T14:03:22.145Z' -- milliseconds and a Z, like everything else."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + "%03dZ" % (when.microsecond // 1000)


def encode_cursor(sequence_number: int) -> str:
    return base64.b64encode(
        json.dumps({"id": int(sequence_number)}).encode("utf-8")).decode("ascii")


def decode_cursor(cursor) -> int:
    """The bookmark id inside an opaque cursor, or 0 for 'from the beginning'.

    A cursor that will not decode is treated as 0 rather than raising. The
    vendor calls it opaque, and an opaque token a client has mangled is not
    something the client can be asked to interpret -- so the server starts over
    rather than erroring. It also means a poller that stored a truncated tag
    value replays instead of stalling, which is the kinder of the two failures.
    """
    if not cursor:
        return 0
    try:
        raw = base64.b64decode(str(cursor).encode("ascii"), validate=False)
        return int(json.loads(raw.decode("utf-8")).get("id") or 0)
    except Exception:
        LOG.warning("undecodable cursor %r -- starting from the beginning", cursor)
        return 0


class Instrument:
    """Everything the API and the touchscreen both talk to.

    Single-threaded by construction: the sampling loop, the GraphQL app and the
    panel all run on one asyncio event loop, so there are no locks in here and
    there should not be.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.samples = []            # oldest first; sequenceNumber ascending
        self._sequence = 0
        self.running = False
        self.room = "clean"          # clean | dirty -- the operator's switch
        self.sample_point = cfg.device_name
        self.next_sample_at = None   # epoch seconds, or None when stopped
        self.started_run_at = None
        self.evicted = 0             # how many records the cap has dropped
        self._load_state()
        if cfg.seed_samples > 0:
            self._seed(cfg.seed_samples)

    # ---- persistence ------------------------------------------------------
    #
    # The sample point is the operator's, and losing it on a container restart
    # would mean re-typing the room on stage. The buffer is deliberately NOT
    # here: see the module docstring.

    @property
    def _state_path(self) -> str:
        return os.path.join(self.cfg.config_dir, STATE_FILE)

    def _load_state(self) -> None:
        try:
            with open(self._state_path, "r") as handle:
                state = json.load(handle)
        except Exception:
            return
        self.sample_point = str(state.get("sample_point") or self.cfg.device_name)
        self.room = "dirty" if state.get("room") == "dirty" else "clean"
        if state.get("running"):
            # A run that was going before the restart resumes -- but from
            # sequence number 1, because the buffer did not come back. That
            # combination is exactly the stale-cursor demo.
            self.start()
        LOG.info("restored state: sample_point=%r room=%s running=%s",
                 self.sample_point, self.room, self.running)

    def _save_state(self) -> None:
        state = {
            "sample_point": self.sample_point,
            "room": self.room,
            "running": self.running,
        }
        try:
            os.makedirs(self.cfg.config_dir, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(state, handle)
            os.replace(tmp, self._state_path)
        except Exception as exc:
            LOG.warning("could not persist state to %s: %s", self._state_path, exc)

    # ---- the touchscreen's four controls ----------------------------------

    def start(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.started_run_at = time.time()
        self.next_sample_at = time.time() + self.cfg.duration
        self._save_state()
        LOG.info("sampling started: %ss per analysis, continuous", self.cfg.duration)
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self.running = False
        self.next_sample_at = None
        self._save_state()
        LOG.info("sampling stopped after %d analyses", self._sequence)
        return True

    def set_sample_point(self, value: str) -> str:
        self.sample_point = str(value or "").strip() or self.cfg.device_name
        self._save_state()
        LOG.info("sample point set to %r", self.sample_point)
        return self.sample_point

    def set_room(self, value: str) -> str:
        self.room = "dirty" if str(value).lower().startswith("d") else "clean"
        self._save_state()
        LOG.info("room condition set to %s", self.room)
        return self.room

    def clear(self) -> bool:
        """`clearSamples`. Empties the buffer, keeps the sequence counter.

        Ignition never calls this -- `startSampling`, `stopSampling` and
        `clearSamples` are a vendor control surface we deliberately do not
        touch, the same change-control boundary pattern 3 keeps with the analyzer's
        104 writable bits.
        """
        dropped = len(self.samples)
        self.samples = []
        LOG.info("sample buffer cleared (%d records)", dropped)
        return True

    # ---- generation -------------------------------------------------------

    def _counts(self, size_um: float, volume_l: float) -> int:
        """Particles seen on one channel in `volume_l` litres.

        Poisson-ish: a mean scaled from the per-28.3 L table, then +/-25%
        jitter. Decreasing with size, as the vendor doc says, and with enough
        spread that consecutive analyses do not look canned.
        """
        base = CLEAN_PER_28L.get(size_um)
        if base is None:
            base = CLEAN_PER_28L[0.5] * (size_um / 0.5) ** _FALLBACK_EXPONENT
        mean = base * (volume_l / 28.3)
        if self.room == "dirty":
            mean *= self.cfg.dirty_multiplier
        jitter = random.uniform(0.75, 1.25)
        return max(0, int(round(mean * jitter)))

    def _make_sample(self, started_at: datetime, completed_at: datetime) -> dict:
        self._sequence += 1
        # Flow varies a little, and the volume follows the flow rather than the
        # nameplate -- so `total_volume_l` on the wire is the volume actually
        # drawn, which is what a consumer would have to normalise by.
        flow = round(random.gauss(self.cfg.flow_rate_lpm, 0.05), 2)
        volume = round(flow * self.cfg.duration / 60.0, 3)
        return {
            "id": str(uuid.uuid4()),
            "deviceId": self.cfg.device_id,
            # The location overload, named as one in the spec: the vendor record
            # has no location field and `deviceName` is its only free text. A
            # portable counter labelled with its sampling point is what happens
            # in the field, so it is defensible -- and it is still us putting our
            # meaning in the vendor's box.
            "deviceName": self.sample_point,
            "sequenceNumber": self._sequence,
            "startedAt": _iso(started_at),
            "completedAt": _iso(completed_at),
            "status": "COMPLETED",
            "config": {
                "mode": "TIMED",
                "durationSeconds": self.cfg.duration,
                "repeatCount": 0,
                "volume": {"units": "L", "value": volume},
            },
            "results": {
                "channels": [
                    {"sizeUm": size, "particleCount": self._counts(size, volume)}
                    for size in self.cfg.channels
                ],
                "totalVolume": {"units": "L", "value": volume},
                "environment": {
                    "flowRate": {"average": {"units": "LPM", "value": flow}},
                    "temperature": {"average": {
                        "units": "C", "value": round(random.uniform(21.5, 23.2), 1)}},
                    "humidity": {"average": {
                        "units": "%RH", "value": round(random.uniform(41.0, 51.0), 1)}},
                },
            },
            "operator": {
                "name": self.cfg.operator_name,
                "username": self.cfg.username,
                "role": self.cfg.operator_role,
            },
        }

    def _append(self, sample: dict) -> None:
        self.samples.append(sample)
        # Rolling buffer. Two hours at 10 s is 720 analyses, so an uncapped list
        # grows without bound on a stack that runs all day. Eviction is safe for
        # the cursor -- keyset paging on "greater than N" does not care that
        # earlier records left -- but a poller that fell far enough behind would
        # silently skip records, so say so in the log.
        overflow = len(self.samples) - self.cfg.buffer_max
        if overflow > 0:
            self.samples = self.samples[overflow:]
            self.evicted += overflow
            LOG.warning("buffer at cap (%d); dropped %d oldest record(s), %d total",
                        self.cfg.buffer_max, overflow, self.evicted)

    def _seed(self, count: int) -> None:
        """Pre-generate `count` historical records, spaced by the duration.

        `SEED_SAMPLES` is 0 in this stack's compose file, so this never runs
        here. It stays because the vendor documents a default of 50 and the
        quickstart in the reference has to behave the way the reference says.
        """
        end = _now()
        step = timedelta(seconds=self.cfg.duration)
        first = end - step * count
        for index in range(count):
            started = first + step * index
            self._append(self._make_sample(started, started + step))
        LOG.info("seeded %d historical samples", count)

    def tick(self) -> dict:
        """Produce an analysis if one is due. Returns it, or None.

        Called once a second by the sampling loop rather than sleeping for the
        duration, so Start and Stop are responsive on the touchscreen.
        """
        if not self.running or self.next_sample_at is None:
            return None
        now = time.time()
        if now < self.next_sample_at:
            return None
        completed = _now()
        started = completed - timedelta(seconds=self.cfg.duration)
        sample = self._make_sample(started, completed)
        self._append(sample)
        self.next_sample_at = now + self.cfg.duration
        LOG.info("analysis %d complete: %s room, %s",
                 sample["sequenceNumber"], self.room,
                 ", ".join("%.1fum=%d" % (c["sizeUm"], c["particleCount"])
                           for c in sample["results"]["channels"]))
        return sample

    # ---- the query --------------------------------------------------------

    def get_samples(self, cursor=None, limit=None) -> dict:
        """`getSamples(cursor, limit)`: records after the bookmark, oldest first.

        `limit` defaults to all, per the vendor's variable table. `hasMore` means
        "the server truncated at limit" -- so a client drains a backlog by
        calling again with the cursor it was just handed, until it is false.
        An empty page hands back the cursor it was given, unchanged.
        """
        after = decode_cursor(cursor)
        pending = [s for s in self.samples if s["sequenceNumber"] > after]
        has_more = False
        if limit is not None and limit > 0 and len(pending) > limit:
            pending = pending[:limit]
            has_more = True
        next_cursor = encode_cursor(pending[-1]["sequenceNumber"]) if pending \
            else (cursor or None)
        return {
            "samples": pending,
            "pagination": {"nextCursor": next_cursor, "hasMore": has_more},
        }

    # ---- what the touchscreen shows ---------------------------------------

    def panel_state(self) -> dict:
        last = self.samples[-1] if self.samples else None
        seconds_to_next = None
        if self.running and self.next_sample_at is not None:
            seconds_to_next = max(0, int(round(self.next_sample_at - time.time())))
        return {
            "device_id": self.cfg.device_id,
            "sample_point": self.sample_point,
            "room": self.room,
            "running": self.running,
            "duration_s": self.cfg.duration,
            "flow_rate_lpm": self.cfg.flow_rate_lpm,
            "sample_volume_l": round(self.cfg.sample_volume_l, 3),
            "channels": self.cfg.channels,
            "buffer_count": len(self.samples),
            "buffer_max": self.cfg.buffer_max,
            "evicted": self.evicted,
            "sequence": self._sequence,
            "seconds_to_next": seconds_to_next,
            "last": None if last is None else {
                "sequence_number": last["sequenceNumber"],
                "completed_at": last["completedAt"],
                "total_volume_l": last["results"]["totalVolume"]["value"],
                "channels": last["results"]["channels"],
                "temperature_c": last["results"]["environment"]
                                     ["temperature"]["average"]["value"],
                "humidity_pct": last["results"]["environment"]
                                    ["humidity"]["average"]["value"],
                "flow_rate_lpm": last["results"]["environment"]
                                     ["flowRate"]["average"]["value"],
            },
        }
