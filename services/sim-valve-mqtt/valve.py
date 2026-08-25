#!/usr/bin/env python3
"""The smart sample valve assembly itself -- badge roster, state machine, stroke faults.

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

Authorization is that roster and nothing else (2026-08-25). The process interlock that used
to sit beside it is gone, along with the lapsed-training badge -- two more ways to say no,
neither of which the demo could show without the audience taking the device's word for it.
What is left is one question with one answer, and a fault path that is physical instead:
starve the pneumatic actuator of air and the valve does not seat.
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
# LOCKED --scan--> [on the roster, right role, not busy] --> UNLOCKING -> OPEN -> CLOSING
#                          |                                                        |
#                          +--> denied: an event, and nothing moves                  |
#                                                                                    v
#                                                             sample-complete, back to LOCKED

LOCKED = "locked"
UNLOCKING = "unlocking"
OPEN = "open"
CLOSING = "closing"

# Neither of these is a valve state, and neither is ever entered by the state machine. They
# are the two values of pattern 1's retained `status` topic -- `online` published by the
# device as its first message after CONNACK, `offline` by the Last Will -- and they live
# here so both firmwares spell them the same way.
ONLINE = "online"
OFFLINE = "offline"

# Deny reasons, checked in exactly this order. Every one of these produces an event: a
# refused sample attempt is exactly as audit-relevant as a successful one, which is the
# whole reason this device publishes. There used to be two more -- `training-expired` and
# `interlock-open` -- and both were cut on 2026-08-25.
DENY_UNKNOWN = "badge-unknown"
DENY_ROLE = "badge-not-authorized"
DENY_BUSY = "valve-busy"

# Roster status values, as they appear in BADGE_ROSTER.
STATUS_AUTHORIZED = "authorized"
STATUS_NOT_AUTHORIZED = "not-authorized"

_DENY_FOR_STATUS = {
    STATUS_NOT_AUTHORIZED: DENY_ROLE,
}

# How a sample ended. Reported on pattern 1's event/sample-complete and mirrored by pattern
# 2's Sample/LastCycleResult metric, which is the same fact in the two vocabularies.
CYCLE_NORMAL = "normal"
CYCLE_FAILED_TO_SEAT = "failed-to-seat"
CYCLE_STROKE_TIMEOUT = "stroke-timeout"

# The actuator is pneumatic, so the air supply is the physical cause of both faults, and
# these two thresholds are what turn one analog reading into two different failures:
#
#   below SEAT   -- enough air to stroke open, not enough to drive it shut against the
#                   diaphragm, so the position feedback stops short of zero
#   below STROKE -- the actuator cannot complete the opening stroke at all
#
# The shipped sag (AIR_SUPPLY_SAG_BAR, 3.2) sits deliberately between the two, so the config
# page's one button produces a failed-to-seat -- the demo path. Sag it further from the
# environment and the same button produces a stroke timeout instead.
AIR_SUPPLY_SEAT_BAR = 4.5
AIR_SUPPLY_STROKE_BAR = 2.5

# Where the position feedback comes to rest when the valve fails to seat, and it stays there
# until a cycle with enough air behind it puts it back. This number IS the fault: nothing in
# this assembly can see the seat, it can only see that the feedback never reached zero.
RESIDUAL_POSITION_PCT = 12.0


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

    The default roster ships two badges -- one authorized, one refused for its role -- so
    both roster outcomes can be demonstrated without editing config on stage. The third
    denial, `badge-unknown`, needs no roster entry by definition; the config page offers a
    button for a badge that is on no roster at all.
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
        if status not in (STATUS_AUTHORIZED, STATUS_NOT_AUTHORIZED):
            LOG.warning("roster entry %s has unknown status %r -- treating as not-authorized",
                        badge_id, status)
            status = STATUS_NOT_AUTHORIZED
        roster[badge_id] = Badge(badge_id, holder, role, status)
    return roster


class Sink:
    """What the assembly does with what happened. Implemented per variant.

    Three calls, and the split matters: an *event* is a discrete thing that must not be
    lost, a *state* is a level that a late subscriber needs to know right now, and
    *telemetry* is a stream nobody will miss one sample of. Pattern 2 spends that
    distinction on birth certificates, deadbands and report-by-exception.

    Pattern 1 spends it on QoS and the retained flag -- and since 2026-08-25 it does not
    spend it on state at all: its `valve_state` does nothing, because valve position is on
    no topic. The device still has the level. That firmware has no way to say so.
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
    demo that snaps between open and closed hides the one moment where a failed seat is
    visible in the position feedback.
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
        self.cycle_count = 0

        self.sample_id = None
        self.sample_seq = 0
        self.active_badge = None
        self.sample_start = None

        self.last_scan = None
        self.last_cycle_result = None

        # What the assembly measures about itself. The air supply is not scenery: it is the
        # cause of every stroke fault below, which is the whole reason telemetry was
        # re-pointed here from a line pressure / line temperature pair.
        self.air_sagged = False
        self.air_supply_bar = cfg.air_supply_bar
        self.enclosure_temperature_c = cfg.enclosure_temperature_c

        # Where the position feedback is resting. Zero unless the last close failed to seat,
        # and then it stays wrong until a cycle with enough air behind it puts it right --
        # which is how a real one behaves, and why the fault survives the message that
        # reported it.
        self.rest_position_pct = 0.0
        self._pending_result = None

        self._next_state_at = None
        self._next_telemetry_at = 0.0
        self._next_auto_scan_at = (
            self.started + cfg.scan_interval_s if cfg.scan_interval_s > 0 else None
        )

    # ---- snapshots

    def state_snapshot(self) -> dict:
        """Where the valve is.

        Pattern 2 reads four of these into Valve/State, Valve/IsOpen, Valve/PositionPct and
        Sample/CycleCount. Pattern 1 reads it for its own config page and publishes none of
        it -- the `state` topic was cut on 2026-08-25, and the asymmetry that leaves is
        tracked as open item 7 in docs/plans/01-native-mqtt.md.
        """
        with self.lock:
            return {
                "state": self.state,
                "is_open": self.state == OPEN,
                "position_pct": self._position_pct(),
                "sample_id": self.sample_id,
                "badge_id": self.active_badge.badge_id if self.active_badge else None,
                "cycle_count": self.cycle_count,
                "since": iso(self.state_since),
            }

    def telemetry_values(self) -> dict:
        """The assembly's own condition -- not the process's.

        Re-pointed 2026-08-23 from a line pressure / line temperature pair: a shut sample
        valve has nothing moving through it, so those were either a dead-leg reading or a
        restatement of the vessel's own instruments. `interlock_ok` left with the interlock
        on 2026-08-25. Four keys, and `air_supply_bar` is the one that predicts a fault.
        """
        with self.lock:
            return {
                "air_supply_bar": round(self.air_supply_bar, 3),
                "enclosure_temperature_c": round(self.enclosure_temperature_c, 2),
                "valve_cycles_total": self.cycle_count,
                "uptime_s": round(time.time() - self.started, 1),
            }

    def _position_pct(self) -> float:
        """Interpolated across the stroke, so the position tag moves rather than snapping.

        Both strokes run between `rest_position_pct` and 100 rather than between 0 and 100:
        a valve that failed to seat is resting off its seat, and the next stroke starts from
        wherever it actually is.
        """
        now = time.time()
        elapsed = now - self.state_since
        fraction = min(1.0, max(0.0, elapsed / max(self.cfg.stroke_s, 0.001)))
        span = 100.0 - self.rest_position_pct
        if self.state == OPEN:
            return 100.0
        if self.state == UNLOCKING:
            return round(self.rest_position_pct + span * fraction, 1)
        if self.state == CLOSING:
            return round(self.rest_position_pct + span * (1.0 - fraction), 1)
        return round(self.rest_position_pct, 1)

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
                # operator refused for their role should be told that, not "try again in
                # ten seconds" -- and the reason has to be the same one every time so the
                # audit trail means something.
                reason = badge.deny_reason()
            if reason is None and self.state != LOCKED:
                reason = DENY_BUSY

            granted = reason is None
            if granted:
                self.sample_seq += 1
                self.sample_id = "S-{}-{:04d}".format(
                    datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d"),
                    self.sample_seq,
                )
                self.active_badge = badge
                self._pending_result = None
                self._enter(UNLOCKING, now)

            result = {
                "badge_id": badge.badge_id,
                "badge_holder": badge.holder,
                "badge_role": badge.role,
                "result": "granted" if granted else "denied",
                "deny_reason": reason,
                # The instant the badge was read, which is not the instant the document is
                # published and is nowhere near the instant the sample finishes. The record
                # of what followed is event/sample-complete, fifteen seconds later.
                "scan_time": iso(now),
                # A denial belongs to no sample, so this is JSON null -- and a JSON null
                # produces no tag at all on the Ignition side. See the ingest notes in
                # docs/plans/01-native-mqtt.md.
                "sample_id": self.sample_id if granted else None,
            }
            self.last_scan = dict(result, ts=iso(now))

        self.sink.valve_event("badge-scan", result, now)
        if granted:
            self.sink.valve_state(self.state_snapshot(), now)
        LOG.info("badge %s (%s) %s%s", result["badge_id"], result["badge_role"],
                 result["result"], " [%s]" % reason if reason else "")
        return result

    def set_air_supply(self, sagged: bool) -> None:
        """Sag the actuator's air supply, or restore it.

        Toggleable from the config page because a fault you cannot trigger on demand is a
        fault you will not get to show. This is the *cause*: the supply drops, telemetry
        reports it, and the next sample comes back `failed-to-seat` a minute later. Two
        facts about one starved actuator -- and only one of the two firmwares gives a
        consumer any reason to read them as the same story.

        Nothing is published from here. Air pressure is an analog that takes a few seconds
        to fall, so the drift below carries it and the next telemetry message reports it,
        which is what would actually happen.
        """
        with self.lock:
            if self.air_sagged == sagged:
                return
            self.air_sagged = sagged
        LOG.info("air supply %s", "sagging" if sagged else "restored")

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

    def _begin_closing(self, now: float) -> None:
        """Caller holds self.lock. Decide, as the close stroke starts, where it will end.

        Whether the valve seats is settled by the air available while it strokes shut, so it
        is decided here -- and the position feedback then walks down to whatever was decided
        rather than snapping there afterwards.

        Nothing in this assembly can see the seat. All it can see is that the feedback
        stopped short, and `failed-to-seat` is that observation rather than a diagnosis. The
        first fault of a cycle wins: a stroke timeout has already been recorded by the time
        we get here, and there is no point telling QA about the same starved actuator twice.
        """
        if self.air_supply_bar < AIR_SUPPLY_SEAT_BAR:
            self.rest_position_pct = RESIDUAL_POSITION_PCT
            if self._pending_result is None:
                self._pending_result = CYCLE_FAILED_TO_SEAT
                LOG.warning("failed to seat at %.2f bar -- feedback resting at %s%%",
                            self.air_supply_bar, RESIDUAL_POSITION_PCT)
        else:
            self.rest_position_pct = 0.0
        self._enter(CLOSING, now)

    def _advance(self, now: float) -> None:
        completed = None
        changed = False

        with self.lock:
            if self._next_state_at is None or now < self._next_state_at:
                return
            if self.state == UNLOCKING:
                self.sample_start = now
                if self.air_supply_bar < AIR_SUPPLY_STROKE_BAR:
                    # Not enough air to finish the opening stroke. No sample is taken, but a
                    # cycle still happened and an operator still stood there, so it gets a
                    # completion record like any other -- carrying the reason it is short.
                    self._pending_result = CYCLE_STROKE_TIMEOUT
                    LOG.warning("stroke timeout at %.2f bar -- aborting sample %s",
                                self.air_supply_bar, self.sample_id)
                    self._begin_closing(now)
                else:
                    self._enter(OPEN, now)
                changed = True
            elif self.state == OPEN:
                self._begin_closing(now)
                changed = True
            elif self.state == CLOSING:
                self.cycle_count += 1
                badge = self.active_badge
                self.last_cycle_result = self._pending_result or CYCLE_NORMAL
                completed = {
                    "sample_id": self.sample_id,
                    "badge_id": badge.badge_id if badge else None,
                    "badge_holder": badge.holder if badge else None,
                    "sample_start": iso(self.sample_start) if self.sample_start else None,
                    "sample_completion": iso(now),
                    # Close-finish minus open-finish, so about 13.5 s against a 12 s sample
                    # window: the valve is still passing material while it seats, and the
                    # honest duration is the one that says so.
                    "open_duration_s":
                        round(now - self.sample_start, 2) if self.sample_start else None,
                    "cycle_result": self.last_cycle_result,
                    "cycle_count": self.cycle_count,
                }
                self.active_badge = None
                self.sample_start = None
                self._pending_result = None
                self._enter(LOCKED, now)
                changed = True

        if changed:
            self.sink.valve_state(self.state_snapshot(), now)
        if completed:
            self.sink.valve_event("sample-complete", completed, now)
            LOG.info("sample %s complete after %ss [%s]", completed["sample_id"],
                     completed["open_duration_s"], completed["cycle_result"])

    # ---- the assembly's own condition

    def _drift(self) -> None:
        """Actuator air supply and enclosure temperature wander.

        The temperature is the disposable one: it exists so pattern 2 has an analog to put a
        0.2 degree deadband on and so pattern 1 has something whose loss nobody would mourn,
        which is what makes QoS 0 right for telemetry and wrong for an event.

        The air supply is not disposable at all, and that is new. It dips while the actuator
        is stroking, because it does; and when the config page sags it, it walks down to the
        sagged figure over a few seconds and stays there until somebody restores it. From
        that moment the next sample is going to fail, and the only thing on the wire that
        said so in advance is this reading.
        """
        with self.lock:
            stroking = self.state in (UNLOCKING, CLOSING)
            supply = self.cfg.air_supply_sag_bar if self.air_sagged else self.cfg.air_supply_bar
            target_p = supply - (0.25 if stroking else 0.0)
            target_t = self.cfg.enclosure_temperature_c + (0.8 if self.state == OPEN else 0.0)
            self.air_supply_bar += (target_p - self.air_supply_bar) * 0.25 \
                + self.rng.gauss(0.0, 0.004)
            self.enclosure_temperature_c += (target_t - self.enclosure_temperature_c) * 0.25 \
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
