"""Demand forecast.

Pure. No Home Assistant imports.

WHAT THIS IS FOR
----------------
The controller publishes what it expects the air conditioning to draw over a
horizon, broken down by tariff window. **It never writes battery actuators.**

Two reasons, the second stronger than the first.

Two writers on one actuator fail silently: if this controller sets a battery
reserve and another automation overwrites it four minutes later, nothing errors
— the battery simply behaves oddly.

And battery control is vendor-specific. Different manufacturers expose
different primitives with incompatible semantics. Coding one in would tie this
project to one brand.

So the output carries **no vendor concepts at all**: projected kWh over a
horizon, plus a per-window breakdown and the constraints in force. Whoever owns
the battery translates that into their own primitives.

WHAT IT IS NOT
--------------
Not a billing model and not an optimiser. It answers one question — roughly how
much energy is the air conditioning going to want, and when — accurately enough
for another system to decide what to hold in reserve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from .thermal import ThermalModel

#: How far ahead to project. Long enough to cover an evening peak from its
#: start, short enough that the weather forecast still means something.
DEFAULT_HORIZON_HOURS = 8

#: Assumed draw of an indoor unit while running, in kW, when the room has no
#: power measurement of its own. Deliberately a stated assumption rather than a
#: setting: a better number comes from measuring the unit, not from asking.
ASSUMED_UNIT_KW = 1.2


@dataclass(frozen=True, slots=True)
class RoomProjection:
    """What one room is expected to draw, and why."""

    room_id: str
    kwh: float
    #: None when the model has not converged and the figure is an assumption.
    modelled: bool
    reason: str


@dataclass(slots=True)
class WindowProjection:
    """Projected draw within one tariff window."""

    start: time
    end: time
    rate: str
    constraints: frozenset[str]
    kwh: float = 0.0
    hours: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """For publication as an entity attribute."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "rate": self.rate,
            "constraints": sorted(self.constraints),
            "hours": round(self.hours, 2),
            "kwh": round(self.kwh, 3),
        }


@dataclass(slots=True)
class DemandForecast:
    """The published contract. No vendor concepts appear in it."""

    horizon_hours: float
    total_kwh: float
    windows: list[WindowProjection] = field(default_factory=list)
    rooms: list[RoomProjection] = field(default_factory=list)
    #: True when every contributing room used a converged model. False means
    #: at least one figure is an assumption, and the consumer should treat the
    #: total as indicative.
    fully_modelled: bool = False

    def as_attributes(self) -> dict[str, Any]:
        """Everything an automation might want, alongside the total."""
        return {
            "horizon_hours": self.horizon_hours,
            "fully_modelled": self.fully_modelled,
            "windows": [window.as_dict() for window in self.windows],
            "rooms": [
                {
                    "room_id": room.room_id,
                    "kwh": room.kwh,
                    "modelled": room.modelled,
                    "reason": room.reason,
                }
                for room in self.rooms
            ],
        }


def _hours_in_window(
    start_at: datetime, horizon_hours: float, window_start: time, window_end: time
) -> float:
    """How many of the next `horizon_hours` fall inside a daily window.

    Walked minute by minute rather than solved analytically: windows wrap past
    midnight, a horizon can span more than one day, and the arithmetic for
    those cases is where this kind of function usually goes wrong.
    """
    minutes = round(horizon_hours * 60)
    inside = 0
    for offset in range(minutes):
        at = (start_at + timedelta(minutes=offset)).time()
        if window_start == window_end:
            inside += 1
        elif window_start < window_end:
            if window_start <= at < window_end:
                inside += 1
        elif at >= window_start or at < window_end:
            inside += 1
    return inside / 60.0


@dataclass(frozen=True, slots=True)
class RoomForecastInput:
    """Everything the forecast needs about one room."""

    room_id: str
    model: ThermalModel
    indoor_c: float | None
    target_c: float | None
    outdoor_c: float | None
    direct_sun: bool
    #: False for a room that will not run at all — locked out, or unoccupied
    #: with no heading-home request. Those rooms contribute nothing.
    will_run: bool
    rated_kw: float = ASSUMED_UNIT_KW
    #: What the unit itself can do, read from the climate entity — the same
    #: capability the actuator decision already checks. Without these the
    #: projection could count energy for correcting a direction the unit will
    #: never actually be commanded to correct.
    can_heat: bool = True
    can_cool: bool = True


def project_room(
    room: RoomForecastInput, horizon_hours: float
) -> RoomProjection:
    """Project one room's energy over the horizon."""
    if not room.will_run:
        return RoomProjection(
            room_id=room.room_id,
            kwh=0.0,
            modelled=True,
            reason="room will not run over this horizon",
        )

    if room.indoor_c is None or room.target_c is None:
        return RoomProjection(
            room_id=room.room_id,
            kwh=0.0,
            modelled=False,
            reason="no reading or no target, nothing to project",
        )

    kwh = room.model.energy_for(
        room.indoor_c,
        room.target_c,
        room.outdoor_c,
        direct_sun=room.direct_sun,
        hours=horizon_hours,
        rated_kw=room.rated_kw,
        can_heat=room.can_heat,
        can_cool=room.can_cool,
    )
    if kwh is None:
        # Not yet learned. Say so rather than publishing a confident number,
        # and assume a modest duty cycle so the figure is not simply zero.
        return RoomProjection(
            room_id=room.room_id,
            kwh=round(room.rated_kw * horizon_hours * 0.3, 3),
            modelled=False,
            reason="thermal model has not converged, assuming 30% duty",
        )

    return RoomProjection(
        room_id=room.room_id, kwh=kwh, modelled=True, reason="modelled"
    )


def build_forecast(
    now: datetime,
    rooms: list[RoomForecastInput],
    windows: list[tuple[time, time, str, frozenset[str]]],
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
) -> DemandForecast:
    """Project total and per-window energy over the horizon.

    Energy is spread across windows in proportion to how much of the horizon
    each window occupies. That is deliberately crude: it assumes the room draws
    evenly, which is wrong in detail and right enough for a reserve decision.
    Anything better needs a per-window thermal projection, which is a great
    deal of machinery for a number that only has to be roughly correct.
    """
    projections = [project_room(room, horizon_hours) for room in rooms]
    total = round(sum(projection.kwh for projection in projections), 3)

    window_projections: list[WindowProjection] = []
    for start, end, rate, constraints in windows:
        hours = _hours_in_window(now, horizon_hours, start, end)
        if hours <= 0:
            continue
        window_projections.append(
            WindowProjection(
                start=start,
                end=end,
                rate=rate,
                constraints=constraints,
                hours=hours,
                kwh=round(total * (hours / horizon_hours), 3) if horizon_hours else 0.0,
            )
        )

    contributing = [
        projection
        for projection in projections
        if projection.kwh > 0 or projection.reason == "modelled"
    ]
    fully_modelled = bool(contributing) and all(
        projection.modelled for projection in contributing
    )

    return DemandForecast(
        horizon_hours=horizon_hours,
        total_kwh=total,
        windows=window_projections,
        rooms=projections,
        fully_modelled=fully_modelled,
    )
