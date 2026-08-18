#!/usr/bin/env python3
"""The smart sample valve assembly itself -- badge roster, interlock, state machine.

This module is the *device*, and it knows nothing about MQTT. It hands what happened to a
sink, and the two variants of this assembly (plain MQTT, Sparkplug B) implement that sink
differently: one turns an event into a JSON document on a topic you configured, the other
turns it into metric updates on a namespace the spec configured. Keeping the physics in one
place is what makes the pattern 1 / pattern 2 comparison honest -- the device is identical
and only the way it speaks changes.

This file is duplicated byte-for-byte between services/sim-valve-mqtt/ and
services/sim-valve-spb/. That is the house convention (docs/plans/00-master-plan.md, "no
shared lib, keeps build contexts self-contained") -- fix it in one, copy it to the other,
and `diff` the two before committing.

The valve is PUBLISH-ONLY. Nothing on the backbone can open it, and there is no command
topic: the assembly checks the badge against its own roster because a sample port that
stops working when the network does is not a sample port anyone would install.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone

LOG = logging.getLogger("valve")

# -- states ------------------------------------------------------------------------------
#
# LOCKED --scan--> [authorized && interlock] --> UNLOCKING -> OPEN -> CLOSING -> LOCKED
#                          |
#                          +--> denied: an event, and nothing moves

LOCKED = "locked"
UNLOCKING = "unlocking"
OPEN = "open"
CLOSING = "closing"
OFFLINE = "offline"  # never entered here -- it is what the death certificate carries

# Deny reasons. Every one of these produces an event: a refused sample attempt is exactly
# as audit-relevant as a successful one, which is the whole reason this device publishes.
DENY_UNKNOWN = "badge-unknown"
DENY_ROLE = "badge-not-authorized"
DENY_TRAINING = "training-expired"
DENY_INTERLOCK = "interlock-open"
DENY_BUSY = "valve-busy"

# Roster status values, as they appear in BADGE_ROSTER.
STATUS_AUTHORIZED = "authorized"
STATUS_NOT_AUTHORIZED = "not-authorized"
STATUS_TRAINING_EXPIRED = "training-expired"

_DENY_FOR_STATUS = {
    STATUS_NOT_AUTHORIZED: DENY_ROLE,
    STATUS_TRAINING_EXPIRED: DENY_TRAINING,
}


def iso(epoch: float) -> str:
    """ISO-8601 UTC with milliseconds -- the same shape every other service here emits."""
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class Badge:
    __slots__ = ("badge_id", "holder", "role", "status")

    def __init__(self, badge_id: str, holder: str, role: str, status: str) -> None:
        self.badge_id = badge_id
        self.holder = holder
        self.role = role
        self.status = status

    def deny_reason(self):
        return _DENY_FOR_STATUS.get(self.status)

    def as_dict(self) -> dict:
        return {"badge_id": self.badge_id, "holder": self.holder, "role": self.role,
                "status": self.status}


def parse_roster(spec: str) -> dict:
    """`id:holder:role:status` entries, comma separated.

    The default roster deliberately carries one badge that is refused for its role and one
    whose training lapsed, so both denial paths can be demonstrated without editing config
    on stage.
    """
    roster = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) != 4:
            LOG.warning("ignoring malformed roster entry %r", entry)
            continue
        badge_id, holder, role, status = parts
        if status not in (STATUS_AUTHORIZED, STATUS_NOT_AUTHORIZED, STATUS_TRAINING_EXPIRED):
            LOG.warning("roster entry %s has unknown status %r -- treating as not-authorized",
                        badge_id, status)
            status = STATUS_NOT_AUTHORIZED
        roster[badge_id] = Badge(badge_id, holder, role, status)
    return roster


class Sink:
    """What the assembly does with what happened. Implemented per variant.

    Three calls, and the split matters: an *event* is a discrete thing that must not be
    lost, a *state* is a level that a late subscriber needs to know right now, and
    *telemetry* is a stream nobody will miss one sample of. Pattern 1 spends that
    distinction on QoS and the retained flag; pattern 2 spends it on birth certificates and
    report-by-exception.
    """

    def valve_event(self, event: str, data: dict, ts: float) -> None:
        raise NotImplementedError

    def valve_state(self, snapshot: dict, ts: float) -> None:
        raise NotImplementedError

    def valve_telemetry(self, values: dict, ts: float) -> None:
        raise NotImplementedError


class ValveAssembly:
    """Sanitary diaphragm sample valve + RFID reader + position feedback, on a sample port.

    Stroke times are real: a pneumatic sanitary valve takes over a second to seat, and a
    demo that snaps between open and closed hides the one moment where the state topic is
    genuinely useful.
    """

    def __init__(self, cfg, sink: Sink) -> None:
        self.cfg = cfg
        self.sink = sink
        self.rng = random.Random(0x5A11)
        self.started = time.time()

        self.lock = threading.RLock()
        self.stopping = threading.Event()

        self.state = LOCKED
        self.state_since = self.started
        self.interlock_ok = True
        self.cycle_count = 0

        self.sample_id = None
        self.sample_seq = 0
        self.active_badge = None
        self.opened_at = None

        self.last_scan = None

        self.line_pressure_bar = cfg.line_pressure_bar
        self.line_temperature_c = cfg.line_temperature_c

        self._next_state_at = None
        self._next_telemetry_at = 0.0
        self._next_auto_scan_at = (
            self.started + cfg.scan_interval_s if cfg.scan_interval_s > 0 else None
        )

    # ---- snapshots

    def state_snapshot(self) -> dict:
        """What the retained state message and the Sparkplug valve metrics both read from."""
        with self.lock:
            return {
                "state": self.state,
                "is_open": self.state == OPEN,
                "position_pct": self._position_pct(),
                "interlock_ok": self.interlock_ok,
                "sample_id": self.sample_id,
                "badge_id": self.active_badge.badge_id if self.active_badge else None,
                "cycle_count": self.cycle_count,
                "since": iso(self.state_since),
            }

    def telemetry_values(self) -> dict:
        with self.lock:
            return {
                "line_pressure_bar": round(self.line_pressure_bar, 3),
                "line_temperature_c": round(self.line_temperature_c, 2),
                "valve_cycles_total": self.cycle_count,
                "interlock_ok": self.interlock_ok,
                "uptime_s": round(time.time() - self.started, 1),
            }

    def _position_pct(self) -> float:
        """Interpolated across the stroke, so the position tag moves rather than snapping."""
        now = time.time()
        elapsed = now - self.state_since
        if self.state == OPEN:
            return 100.0
        if self.state == UNLOCKING:
            return round(min(100.0, 100.0 * elapsed / max(self.cfg.stroke_s, 0.001)), 1)
        if self.state == CLOSING:
            return round(max(0.0, 100.0 * (1.0 - elapsed / max(self.cfg.stroke_s, 0.001))), 1)
        return 0.0

    # ---- the reader

    def scan(self, badge_id: str) -> dict:
        """An RFID badge is presented. Always produces exactly one event.

        Returns the scan result so the config page can echo it back to whoever pressed the
        button; nothing downstream depends on the return value.
        """
        now = time.time()
        with self.lock:
            badge = self.cfg.roster.get(badge_id)
            if badge is None:
                # An unknown badge is not an error condition -- it is a contractor at the
                # wrong skid, and QA wants the record of it.
                badge = Badge(badge_id, "unknown", "unknown", STATUS_NOT_AUTHORIZED)
                reason = DENY_UNKNOWN
            else:
                # Who you are is decided before what the valve happens to be doing. An
                # operator whose training lapsed should be told that, not "try again in
                # ten seconds" -- and the reason has to be the same one every time so the
                # audit trail means something.
                reason = badge.deny_reason()
            if reason is None and self.state != LOCKED:
                reason = DENY_BUSY
            if reason is None and not self.interlock_ok:
                reason = DENY_INTERLOCK

            granted = reason is None
            if granted:
                self.sample_seq += 1
                self.sample_id = "S-{}-{:04d}".format(
                    datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d"),
                    self.sample_seq,
                )
                self.active_badge = badge
                self._enter(UNLOCKING, now)

            result = {
                "badge_id": badge.badge_id,
                "badge_holder": badge.holder,
                "badge_role": badge.role,
                "result": "granted" if granted else "denied",
                "deny_reason": reason,
                "valve_state": self.state,
                "sample_id": self.sample_id if granted else None,
            }
            self.last_scan = dict(result, ts=iso(now))

        self.sink.valve_event("badge-scan", result, now)
        if granted:
            self.sink.valve_state(self.state_snapshot(), now)
        LOG.info("badge %s (%s) %s%s", result["badge_id"], result["badge_role"],
                 result["result"], " [%s]" % reason if reason else "")
        return result

    def set_interlock(self, ok: bool) -> None:
        """The process interlock -- CIP in progress, vessel not at sampling conditions.

        Toggleable from the config page because a deny path you cannot trigger on demand is
        a deny path you will not get to show.
        """
        now = time.time()
        with self.lock:
            if self.interlock_ok == ok:
                return
            self.interlock_ok = ok
        LOG.info("interlock %s", "satisfied" if ok else "open")
        self.sink.valve_state(self.state_snapshot(), now)

    # ---- the state machine

    def _enter(self, state: str, now: float) -> None:
        """Caller holds self.lock."""
        self.state = state
        self.state_since = now
        if state == UNLOCKING:
            self._next_state_at = now + self.cfg.stroke_s
        elif state == OPEN:
            self._next_state_at = now + self.cfg.sample_window_s
        elif state == CLOSING:
            self._next_state_at = now + self.cfg.stroke_s
        else:
            self._next_state_at = None

    def _advance(self, now: float) -> None:
        completed = None
        changed = False

        with self.lock:
            if self._next_state_at is None or now < self._next_state_at:
                return
            if self.state == UNLOCKING:
                self.opened_at = now
                self._enter(OPEN, now)
                changed = True
            elif self.state == OPEN:
                self._enter(CLOSING, now)
                changed = True
            elif self.state == CLOSING:
                self.cycle_count += 1
                badge = self.active_badge
                completed = {
                    "sample_id": self.sample_id,
                    "badge_id": badge.badge_id if badge else None,
                    "badge_holder": badge.holder if badge else None,
                    "opened_at": iso(self.opened_at) if self.opened_at else None,
                    "closed_at": iso(now),
                    "open_duration_s": round(now - self.opened_at, 2) if self.opened_at else None,
                    "cycle_count": self.cycle_count,
                }
                self.active_badge = None
                self.opened_at = None
                self._enter(LOCKED, now)
                changed = True

        if changed:
            self.sink.valve_state(self.state_snapshot(), now)
        if completed:
            self.sink.valve_event("sample-complete", completed, now)
            LOG.info("sample %s complete after %ss", completed["sample_id"],
                     completed["open_duration_s"])

    # ---- the line

    def _drift(self) -> None:
        """Sample line pressure and temperature wander; they are not the story.

        They exist so pattern 2 has an analog to apply a deadband to, and so pattern 1 has
        something whose loss nobody would mourn -- which is what makes QoS 0 the right
        choice for the telemetry topic and the wrong one for the event topic.
        """
        with self.lock:
            open_now = self.state == OPEN
            target_p = self.cfg.line_pressure_bar - (0.35 if open_now else 0.0)
            target_t = self.cfg.line_temperature_c + (1.2 if open_now else 0.0)
            self.line_pressure_bar += (target_p - self.line_pressure_bar) * 0.25 \
                + self.rng.gauss(0.0, 0.004)
            self.line_temperature_c += (target_t - self.line_temperature_c) * 0.25 \
                + self.rng.gauss(0.0, 0.03)

    def _auto_scan(self, now: float) -> None:
        """Background badge traffic, so the firehose is not empty between stage triggers.

        One in five is a badge that is not on the roster at all -- the denial paths should
        be visible without anyone pressing anything.
        """
        candidates = list(self.cfg.roster)
        badge_id = (
            self.cfg.unknown_badge_id
            if not candidates or self.rng.random() < 0.2
            else self.rng.choice(candidates)
        )
        self.scan(badge_id)
        self._next_auto_scan_at = now + self.cfg.scan_interval_s

    # ---- lifecycle

    def run(self) -> None:
        """Tick loop. 0.25 s is fine enough for a 1.5 s stroke and costs nothing."""
        while not self.stopping.wait(0.25):
            now = time.time()
            try:
                self._advance(now)
                self._drift()
                if now >= self._next_telemetry_at:
                    self._next_telemetry_at = now + self.cfg.telemetry_interval_s
                    self.sink.valve_telemetry(self.telemetry_values(), now)
                if self._next_auto_scan_at is not None and now >= self._next_auto_scan_at:
                    self._auto_scan(now)
            except Exception:
                LOG.exception("tick failed")

    def stop(self) -> None:
        self.stopping.set()
