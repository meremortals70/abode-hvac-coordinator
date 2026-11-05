"""Module 3, Comfort: occupancy, the band and its centre (Section 6, comfort.py).

Comfort turns how the user says the room should feel into the goal the plan
aims at: the centre of the band in force, in comfort index [D009][I4].
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .const import BAND_RAMP_S, PLAN_HORIZON_H, PRECOOL_ENDS_AFTER_READY_S
from .hci import comfort_index, temperature_for_index
from .models import (
    BandPoint,
    BandRule,
    ComfortMode,
    ComfortResult,
    RoomChoices,
    RoomConfig,
    Snapshot,
)

_POINT = timedelta(minutes=5)
_POINTS = int(PLAN_HORIZON_H * 12)
_RAMP = timedelta(seconds=BAND_RAMP_S)


class UserAction(StrEnum):
    """What the user did to a room, for clearing Leave [D088]."""

    AUTOMATIC_CONTROL = "automatic_control"
    COMFORT_MODE = "comfort_mode"
    PRECOOL = "precool"
    OTHER = "other"


def choices_after_user_action(choices: RoomChoices, action: UserAction) -> RoomChoices:
    """Leave is undone when the user changes Automatic control, Comfort
    mode or asks for Precool [D088]."""
    if choices.leave_pressed_at is not None and action is not UserAction.OTHER:
        return dataclasses.replace(choices, leave_pressed_at=None)
    return choices


def room_index(snapshot: Snapshot) -> float | None:
    """The room's comfort index now, from its measured temperature and
    humidity only [D010][D055]."""
    t, rh = snapshot.room_temperature.value, snapshot.room_humidity.value
    if t is None or rh is None:
        return None
    return comfort_index(t, rh)


def _lerp(a: tuple[float, float], b: tuple[float, float], f: float) -> tuple[float, float]:
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


@dataclass(slots=True)
class RoomComfort:
    """One room's comfort state between cycles."""

    room: RoomConfig
    occupied: bool | None = None
    waking: bool = False
    _target: tuple[BandRule, tuple[float, float] | None] | None = None
    _ramp_from: tuple[float, float] | None = None
    _ramp_at: datetime | None = None
    _leave_saw_empty: bool = False

    def band_at(self, when: datetime, target: tuple[float, float]) -> tuple[float, float]:
        """The band in force at a moment: moving steadily from the band it
        changed from to the new one over the hour after a change [D014][D084]."""
        if self._ramp_from is None or self._ramp_at is None:
            return target
        f = (when - self._ramp_at) / _RAMP
        if f >= 1.0:
            return target
        return _lerp(self._ramp_from, target, max(f, 0.0))

    def evaluate(self, snapshot: Snapshot, choices: RoomChoices, now: datetime) -> ComfortResult:
        """Every cycle: occupancy, Precool, sleeping, the band now, the
        ramp and waking, and the band at each 5-minute point ahead."""
        room = self.room
        has_presence = room.presence_sensor is not None
        clear_leave = self._leave(snapshot, choices)
        leave = choices.leave_pressed_at is not None and not clear_leave

        # OCCUPANCY [D012][D068]
        if leave:
            self.occupied = False
        elif not has_presence:
            self.occupied = True  # [Set]
        else:
            self._occupancy(snapshot, now)
        occupied = bool(self.occupied)

        # PRECOOL, only in a room with a presence sensor [I8][Der]
        precool = has_presence and choices.precool_ready_by is not None
        end_precool: str | None = None
        if precool and choices.precool_ready_by is not None:
            if occupied:
                end_precool = "the room counts as occupied"
            elif leave:
                end_precool = "Leave was pressed"
            elif now >= choices.precool_ready_by + timedelta(seconds=PRECOOL_ENDS_AFTER_READY_S):
                end_precool = "an hour after its ready time"
            if end_precool is not None:
                precool = False

        # SLEEPING [D058][D083]
        if choices.comfort_mode is ComfortMode.SLEEP:
            sleeping = True
        elif choices.comfort_mode is ComfortMode.AWAKE:
            sleeping = False
        else:
            sched = snapshot.sleep_schedule
            sleeping = sched is not None and sched.value is True

        # THE BAND NOW: the first that applies wins
        if leave:
            rule, target = BandRule.LEFT, None
        elif precool:
            rule, target = BandRule.PRECOOL, room.precool_band
        elif not occupied:
            rule, target = BandRule.UNOCCUPIED, None
        elif sleeping:
            rule, target = BandRule.SLEEP, room.sleep_band
        else:
            rule, target = BandRule.OCCUPIED, room.occupied_band

        # RAMP and WAKING
        rerun = self._ramp(rule, target, now)
        index = room_index(snapshot)
        self._waking(rule, index, now)

        if target is None:
            reason = "Leave was pressed" if rule is BandRule.LEFT else "the room is empty"
            return ComfortResult(rule, True, reason, occupied, sleeping, self.waking, False, (), index, None,
                                 end_precool, clear_leave, rerun)
        ready = choices.precool_ready_by if rule is BandRule.PRECOOL else None
        points = []
        for i in range(1, _POINTS + 1):
            when = now + i * _POINT
            lo, hi = self.band_at(when, target)
            points.append(BandPoint(when, lo, hi, ready is None or when >= ready))
        lo_now, hi_now = self.band_at(now, target)
        rh = snapshot.room_humidity.value
        centre_c = (
            temperature_for_index((lo_now + hi_now) / 2.0, rh).t_c if rh is not None else None
        )
        return ComfortResult(rule, False, f"the {rule.value} band", occupied, sleeping, self.waking, precool,
                             tuple(points), index, centre_c, end_precool, clear_leave, rerun, lo_now, hi_now)

    def _leave(self, snapshot: Snapshot, choices: RoomChoices) -> bool:
        """Leave stands until presence reads empty and then occupied again
        [D088]. True when that has just happened."""
        if choices.leave_pressed_at is None:
            self._leave_saw_empty = False
            return False
        p = snapshot.presence
        if p is None or p.value is None:
            return False
        if p.value is False:
            self._leave_saw_empty = True
            return False
        if self._leave_saw_empty and p.since is not None and p.since >= choices.leave_pressed_at:
            self._leave_saw_empty = False
            return True
        return False

    def _occupancy(self, snapshot: Snapshot, now: datetime) -> None:
        """Occupied after presence for the occupied grace, empty after none
        for the empty grace; otherwise the last answer stands [Set]. Before
        any answer the room counts as occupied, failing toward comfort
        [D008]."""
        p = snapshot.presence
        if p is not None and p.value is not None and p.since is not None:
            held = (now - p.since).total_seconds()
            if p.value and held >= self.room.occupied_after_s:
                self.occupied = True
            elif not p.value and held >= self.room.empty_after_s:
                self.occupied = False
        if self.occupied is None:
            self.occupied = True

    def _ramp(self, rule: BandRule, target: tuple[float, float] | None, now: datetime) -> bool:
        """WHEN the band changes, and neither side is OFF: ramp from the
        band in force, and ask for the room to be decided again [D092]."""
        before = self._target
        self._target = (rule, target)
        if before is None:
            return False
        old_rule, old_target = before
        if old_rule is BandRule.SLEEP and rule is not BandRule.SLEEP:
            self.waking = True  # [D085]
        if old_target == target:
            return False
        if old_target is None or target is None:
            self._ramp_from, self._ramp_at = None, None
            return False
        self._ramp_from = self.band_at(now, old_target)
        self._ramp_at = now
        return True

    def _waking(self, rule: BandRule, index: float | None, now: datetime) -> None:
        """A room leaving the SLEEP band is WAKING until the ramp is over
        [D085][D092]. Not until it is also inside its daytime band: a room
        colder than that could then never stop waking, and a waking room
        is never heated (decision of 2 November 2026)."""
        if rule is BandRule.SLEEP:
            self.waking = False
            return
        if not self.waking:
            return
        ramping = self._ramp_at is not None and now - self._ramp_at < _RAMP
        if not ramping:
            self.waking = False

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps: occupancy [D068]."""
        return {"occupied": self.occupied}

    def restore(self, data: Mapping[str, Any]) -> None:
        occ = data.get("occupied")
        self.occupied = occ if isinstance(occ, bool) else None

