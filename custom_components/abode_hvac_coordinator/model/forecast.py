"""The questions every other module asks (Section 6, model/forecast.py).

No other module keeps its own idea of the room [Prop]: drift, forecasts
with their uncertainty, and the setpoint for a rate all come from here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..const import COIL_REBOUND_S, FILTER_NOISE_M_PER_H, FILTER_NOISE_T_PER_H
from ..hci import comfort_index, rh_at_temperature
from ..hci import vapour_pressure as _vp
from ..linalg import add, diagonal, sandwich
from ..models import StepKind
from .filter import HeatFilter
from .humidity import H_E, HumidityFilter, moisture_figures
from .physics import I_M, I_T, STEP_H, ExactStep, StepInputs, exact_step, figures, step
from .unit import effort_for_rate

_REBOUND_STEPS = round(COIL_REBOUND_S / 300.0)


def drift_now(heat: HeatFilter, outdoor: float, sun: float, hour: float,
              opening: float = 0.0) -> float:
    """DRIFT NOW, unit at rest: (a + v o) (out - T) + b (M - T) + s S +
    g(hour) [Der][D105]. opening is 1 while an opening is open, 0 while it
    is shut: a shut room is left with a alone."""
    fig = figures(heat.z)
    t, m = heat.z[I_T], heat.z[I_M]
    air = fig.a + fig.vent * opening
    return air * (outdoor - t) + fig.b * (m - t) + fig.s * sun + fig.g(hour)


@dataclass(frozen=True, slots=True)
class ForecastInput:
    """One 5-minute step of a forecast: the weather, the blinds' sun, and
    what the unit does. rate is positive cooling, negative heating.
    opening is 1 while an opening is open, 0 while it is shut [D105]."""

    outdoor: float
    outdoor_rh: float
    sun: float
    hour: float
    kind: StepKind
    rate: float
    opening: float = 0.0


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """The room at the end of one 5-minute step."""

    t: float
    rh: float
    e: float
    hci: float
    sigma_t: float
    sigma_hci: float


def forecast(
    heat: HeatFilter,
    humidity: HumidityFilter | None,
    inputs: Sequence[ForecastInput],
    *,
    e_now: float,
    rebound_steps: int = 0,
) -> list[ForecastPoint]:
    """FORECAST: from T, M and e now, five minutes at a time with Phi and
    Gam, with the uncertainty carried forward [Der].

    e_now is used where there is no humidity filter yet. rebound_steps is
    how many 5-minute steps of coil rebound remain from a stop before now;
    a stop within the forecast starts 15 minutes more [D060].
    """
    fig = figures(heat.z)
    # Phi and Gam depend on the opening only through the air's coupling
    # a + v o, so each opening used is worked out once [D105]
    steps: dict[float, ExactStep] = {}

    def exact(opening: float) -> ExactStep:
        if opening not in steps:
            steps[opening] = exact_step(fig, opening=opening)
        return steps[opening]

    tm = (heat.z[I_T], heat.z[I_M])
    p2 = [[heat.p[0][0], heat.p[0][1]], [heat.p[1][0], heat.p[1][1]]]
    noise = diagonal([FILTER_NOISE_T_PER_H * STEP_H, FILTER_NOISE_M_PER_H * STEP_H])
    r = heat.noise or 0.0
    mfig = moisture_figures(humidity.z) if humidity is not None else None
    e = humidity.z[H_E] if humidity is not None else e_now
    rebound = rebound_steps
    wet_before = False
    # the unit's rate starts where the filter has it and reaches each
    # step's rate through its delay and lag, taken as one lag of their sum,
    # as the plan takes it [Der]
    u = heat.u
    tau_h = (fig.lag_min + fig.delay_min) / 60.0
    decay = math.exp(-STEP_H / tau_h)
    mean = tau_h / STEP_H * (1.0 - decay)
    out: list[ForecastPoint] = []
    for step_in in inputs:
        wet = step_in.kind in (StepKind.COOL, StepKind.DRY)
        if wet_before and not wet:
            rebound = _REBOUND_STEPS
        wet_before = wet
        # dry's sensible rate is its share of what it is asked for [D108]
        rate = step_in.rate * fig.dry_share if step_in.kind is StepKind.DRY else step_in.rate
        u_step = rate + (u - rate) * mean
        u = rate + (u - rate) * decay
        ex = exact(step_in.opening)
        tm = ex.advance(tm, step_in.outdoor, step_in.sun, fig.g(step_in.hour), u_step)
        p2 = add(sandwich(ex.phi, p2), noise)
        if mfig is not None:
            removed = 0.0
            if step_in.kind is StepKind.DRY:
                removed = mfig.drying
            elif step_in.kind is StepKind.COOL and step_in.rate > 0.0:
                removed = mfig.cooling * step_in.rate
            gain = mfig.rebound if rebound > 0 else 0.0
            e_out = _vp(step_in.outdoor, step_in.outdoor_rh)
            target = (mfig.h * e_out + mfig.q - removed + gain) / mfig.h
            e = target + (e - target) * math.exp(-mfig.h * STEP_H)
        if rebound > 0:
            rebound -= 1
        t = tm[0]
        rh = rh_at_temperature(e, t)
        sigma_t = math.sqrt(max(p2[0][0], 0.0) + r)
        out.append(
            ForecastPoint(
                t=t,
                rh=rh,
                e=e,
                hci=comfort_index(t, rh),
                sigma_t=sigma_t,
                sigma_hci=sigma_t,  # e is held as a vapour pressure: the index moves one for one with T
            )
        )
    return out


def replay(z_then: Sequence[float], steps: Sequence[StepInputs]) -> float:
    """The room temperature now, forecast from an earlier state with the
    weather and unit rates that actually happened (the six-hour check)."""
    z = list(z_then)
    for s in steps:
        z = step(z, s).z
    return z[I_T]


def setpoint_for_rate(heat: HeatFilter, own_reading: float, rate: float) -> float:
    """SETPOINT FOR A RATE [Prop][D095]: the effort for the rate, from the
    unit's reading. Cooling: reading - effort; heating: reading + effort."""
    effort = effort_for_rate(figures(heat.z), rate)
    return own_reading - effort if rate >= 0.0 else own_reading + effort
