"""One Kalman filter learns the room (Section 4.3, model/filter.py) [R6][R16][Prop].

Its state z is the room air, the building mass, every figure of the model
and the unit's delay and lag, 27 numbers, with their 27 x 27 uncertainty P
(25 in the specification; delay and lag added by decision of 27 October
2026). Every 5 minutes it
predicts, then corrects from the room's reading. Nothing else is stored.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..const import (
    FILTER_JACOBIAN_DELTA,
    FILTER_NOISE_DELAY,
    FILTER_NOISE_FIGURE,
    FILTER_NOISE_LN_LAG,
    FILTER_NOISE_M_PER_H,
    FILTER_NOISE_T_PER_H,
    FILTER_RUNNING_RATE_NOISE_C_PER_H,
    FILTER_START_CURVE_STEP_LOG,
    FILTER_START_DELAY_MIN,
    FILTER_START_FIGURES,
    FILTER_START_LAG_MIN,
    FILTER_START_VAR_CURVE_STEP,
    FILTER_START_VAR_DELAY,
    FILTER_START_VAR_HEAT_PATTERN,
    FILTER_START_VAR_LN_LAG,
    FILTER_START_VAR_LOG_FIGURE,
    FILTER_START_VAR_M,
    FILTER_START_VAR_T,
    SENSOR_NOISE_SETTLE_CORRECTIONS,
    SENSOR_NOISE_WEIGHT,
)
from ..linalg import (
    Matrix,
    add,
    diagonal,
    is_finite,
    outer,
    sandwich,
    subtract,
    symmetrise,
)
from ..models import CompressorState, Snapshot, ValueTag
from .physics import (
    CURVE_POINTS,
    HISTORY_MIN,
    I_COOL,
    I_DELAY,
    I_HEAT,
    I_LN_A,
    I_LN_B,
    I_LN_C,
    I_LN_K,
    I_LN_LAG,
    I_LN_M,
    I_LN_S,
    I_M,
    I_T,
    STATE_SIZE,
    StepInputs,
    StepResult,
    figures,
    step,
)
from .unit import curve_efforts, head_direction, head_effort

STEP_MIN = 5


@dataclass(frozen=True, slots=True)
class MinuteSample:
    """One minute of a room, as the filter's step needs it."""

    time: datetime
    hour: float  # local hour of day, with the minutes as a fraction
    outdoor: float | None
    outdoor_rh: float | None
    sun: float
    sun_estimated: bool
    reading: float | None  # the room's temperature, MEASURED only
    reading_rh: float | None  # the room's humidity, MEASURED only
    effort: float
    direction: int  # +1 cooling, -1 heating, 0 resting (compressor not running)
    mode_direction: int  # the direction the heads' modes ask for
    dry: bool
    compressor_known: bool
    compressor_running: bool


def minute_sample(snapshot: Snapshot, local_hour: float) -> MinuteSample:
    """The room's minute from its snapshot.

    The room's effort is the mean effort of the heads working in the
    direction their modes ask for; the compressor is known only when every
    head's is, and running when any head's is.
    """
    working = [h for h in snapshot.heads if h.available and head_direction(h.mode) != 0]
    mode_direction = head_direction(working[0].mode) if working else 0
    same = [h for h in working if head_direction(h.mode) == mode_direction]
    efforts = [e for e in (head_effort(h) for h in same) if e is not None]
    effort = max(sum(efforts) / len(efforts), 0.0) if efforts else 0.0
    running = any(h.compressor is CompressorState.RUNNING for h in snapshot.heads)
    # a head running in a mode that does not say cooling or heating
    # (heat_cool, auto) is working one way or the other, unknown which:
    # nothing is learned from the minute, as if its compressor were unknown
    undirected = any(h.compressor is CompressorState.RUNNING and head_direction(h.mode) == 0
                     for h in snapshot.heads)
    known = not undirected and all(
        h.available and h.compressor is not CompressorState.UNKNOWN for h in snapshot.heads)
    if known and not running:
        direction = 0
    else:
        direction = mode_direction
    t = snapshot.room_temperature
    rh = snapshot.room_humidity
    return MinuteSample(
        time=snapshot.time,
        hour=local_hour,
        outdoor=snapshot.outdoor_temperature.value,
        outdoor_rh=snapshot.outdoor_humidity.value,
        sun=snapshot.sun.s,
        sun_estimated=snapshot.sun.tag is ValueTag.ESTIMATED,
        reading=t.value if t.tag is ValueTag.MEASURED else None,
        reading_rh=rh.value if rh.tag is ValueTag.MEASURED else None,
        effort=effort,
        direction=direction,
        mode_direction=mode_direction,
        dry=any(h.mode == "dry" for h in same),
        compressor_known=known,
        compressor_running=running,
    )


@dataclass(frozen=True, slots=True)
class FilterStep:
    """What one 5-minute step did, for the trace and the other parts."""

    time: datetime
    predicted_t: float
    corrected: bool
    reason: str | None
    innovation: float | None
    inputs: StepInputs | None
    u_path: tuple[float, ...]
    compressor_ran: bool


def start_state(t0: float) -> list[float]:
    """The starting state: the room and mass at the first reading, the
    starting figures, no heat pattern, an untried curve [Prop][Sim][NYE]."""
    z = [0.0] * STATE_SIZE
    z[I_T] = t0
    z[I_M] = t0
    for i, name in zip((I_LN_A, I_LN_B, I_LN_C, I_LN_K, I_LN_S, I_LN_M), "abcksm", strict=True):
        z[i] = math.log(FILTER_START_FIGURES[name])
    for j in range(CURVE_POINTS):
        z[I_COOL + j] = FILTER_START_CURVE_STEP_LOG
        z[I_HEAT + j] = FILTER_START_CURVE_STEP_LOG
    z[I_LN_LAG] = math.log(FILTER_START_LAG_MIN)
    z[I_DELAY] = FILTER_START_DELAY_MIN
    return z


def start_uncertainty() -> Matrix:
    """Wide starting uncertainty [Prop][Sim][NYE]."""
    d = [FILTER_START_VAR_T, FILTER_START_VAR_M]
    d += [FILTER_START_VAR_LOG_FIGURE] * 6
    d += [FILTER_START_VAR_HEAT_PATTERN] * 5
    d += [FILTER_START_VAR_CURVE_STEP] * (2 * CURVE_POINTS)
    d += [FILTER_START_VAR_LN_LAG, FILTER_START_VAR_DELAY]
    return diagonal(d)


def process_noise(compressor_ran: bool) -> Matrix:
    """Q per 5 minutes; the air's noise is raised while the unit runs,
    because the unit's rate is itself learned [Prop][NYE]."""
    step_h = STEP_MIN / 60.0
    t_noise = FILTER_NOISE_T_PER_H * step_h
    if compressor_ran:
        t_noise += (FILTER_RUNNING_RATE_NOISE_C_PER_H * step_h) ** 2
    d = [t_noise, FILTER_NOISE_M_PER_H * step_h] + [FILTER_NOISE_FIGURE] * (STATE_SIZE - 4)
    d += [FILTER_NOISE_LN_LAG, FILTER_NOISE_DELAY]
    return diagonal(d)


def jacobian(z: Sequence[float], inputs: StepInputs, base: StepResult) -> Matrix:
    """F: the change in z- for a small change in each element of z."""
    n = len(z)
    f = [[0.0] * n for _ in range(n)]
    for j in range(n):
        moved = list(z)
        moved[j] += FILTER_JACOBIAN_DELTA
        out = step(moved, inputs).z
        for i in range(n):
            f[i][j] = (out[i] - base.z[i]) / FILTER_JACOBIAN_DELTA
    return f


@dataclass(slots=True)
class HeatFilter:
    """The room's heat filter, with the sensor's learned noise."""

    z: list[float]
    p: Matrix
    u: float = 0.0  # the unit's rate now, carried through its lag
    sensor_step: float | None = None  # q
    noise: float | None = None  # R
    innovation_avg: float | None = None  # v
    corrections: int = 0
    last_correction: datetime | None = None
    last_outdoor: float | None = None
    _last_reading: float | None = None
    _minutes: list[MinuteSample] = field(default_factory=list)
    _history: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=HISTORY_MIN))
    recent: deque[FilterStep] = field(default_factory=lambda: deque(maxlen=72))

    @classmethod
    def start(cls, t0: float) -> HeatFilter:
        """A new room's filter, from its first reading."""
        return cls(z=start_state(t0), p=start_uncertainty())

    @property
    def efforts(self) -> tuple[float, ...]:
        return curve_efforts()

    def observe_reading(self, value: float | None) -> None:
        """q: the smallest non-zero change seen in the room's readings."""
        if value is None:
            return
        if self._last_reading is not None:
            change = abs(value - self._last_reading)
            if change > 1e-9 and (self.sensor_step is None or change < self.sensor_step):
                self.sensor_step = change
                floor = change**2 / 12.0
                if self.noise is None or self.corrections < SENSOR_NOISE_SETTLE_CORRECTIONS:
                    self.noise = floor
                if self.innovation_avg is None:
                    self.innovation_avg = floor
        self._last_reading = value

    def add_minute(self, sample: MinuteSample) -> None:
        """Collect one minute towards the next step."""
        self.observe_reading(sample.reading)
        self._minutes.append(sample)
        self._history.append((sample.effort, sample.direction))

    @property
    def delay_min(self) -> float:
        """The unit's learned delay, minutes."""
        return figures(self.z).delay_min

    @property
    def lag_min(self) -> float:
        """The unit's learned lag, minutes."""
        return figures(self.z).lag_min

    def five_minutes(self, now: datetime) -> FilterStep | None:
        """EVERY 5 MINUTES: predict, then correct unless the step cannot be
        trusted. Nothing is done before any minute has been collected."""
        minutes = self._minutes[-STEP_MIN:]
        self._minutes = []
        if not minutes:
            return None
        complete = len(minutes) == STEP_MIN
        while len(minutes) < STEP_MIN:
            minutes.append(minutes[-1])
        outdoors = [m.outdoor for m in minutes if m.outdoor is not None]
        if outdoors:
            outdoor = sum(outdoors) / len(outdoors)
            self.last_outdoor = outdoor
        elif self.last_outdoor is not None:
            outdoor = self.last_outdoor
        else:
            return FilterStep(now, self.z[I_T], False, "no outdoor temperature yet", None, None, (), False)
        history = list(self._history)
        while len(history) < STEP_MIN:
            history.append(history[-1] if history else (0.0, 0))
        inputs = StepInputs(
            outdoor=outdoor,
            sun=sum(m.sun for m in minutes) / STEP_MIN,
            hours=(minutes[0].hour, minutes[1].hour, minutes[2].hour, minutes[3].hour, minutes[4].hour),
            history=tuple(history),
            u_start=self.u,
            curve_efforts=self.efforts,
        )
        ran = any(m.compressor_running for m in minutes)
        reason = self._why_not(minutes, complete, bool(outdoors))
        z_before, p_before, u_before = list(self.z), [r[:] for r in self.p], self.u
        base = step(self.z, inputs)
        f = jacobian(self.z, inputs, base)
        p_minus = add(sandwich(f, self.p), process_noise(ran))
        self.z, self.p, self.u = base.z, symmetrise(p_minus), base.u_end
        predicted = self.z[I_T]
        innovation: float | None = None
        reading = minutes[-1].reading
        if reason is None and reading is not None:
            innovation = self._correct(reading)
            self.last_correction = now
        if not (is_finite(self.p) and all(math.isfinite(x) for x in self.z) and math.isfinite(self.u)):
            self.z, self.p, self.u = z_before, p_before, u_before
            result = FilterStep(now, predicted, False, "a figure was not a finite number; step undone", None,
                                inputs, base.u_path, ran)
        else:
            result = FilterStep(now, predicted, innovation is not None, reason, innovation, inputs, base.u_path, ran)
        self.recent.append(result)
        return result

    def _why_not(self, minutes: Sequence[MinuteSample], complete: bool, outdoor_seen: bool) -> str | None:
        """The reason the step corrects nothing, or None [D074][Prop]."""
        if minutes[-1].reading is None:
            return "the room reading was stood in or missing"
        if not all(m.compressor_known for m in minutes):
            return "the compressor state, or the way it ran, was unknown"
        if any(m.sun_estimated for m in minutes):
            return "the sun had to be assumed"
        if not complete:
            return "fewer than five minutes were seen"
        if not outdoor_seen:
            return "the outdoor temperature was missing"
        if self.sensor_step is None or self.noise is None or self.innovation_avg is None:
            return "the sensor's step is not yet known"
        if len(self._history) < HISTORY_MIN:
            # since start-up the unit's rate through its delay and lag is not
            # known: the filter would take the unit as having rested, and
            # learn the delay, lag and curve from that
            return "the unit's last 40 minutes are not yet known since start-up"
        return None

    def _correct(self, reading: float) -> float:
        """CORRECT [R6][R17][Prop][Sim]."""
        assert self.sensor_step is not None and self.noise is not None and self.innovation_avg is not None
        nu = reading - self.z[I_T]
        p_tt = self.p[I_T][I_T]
        floor = self.sensor_step**2 / 12.0
        if self.corrections >= SENSOR_NOISE_SETTLE_CORRECTIONS:
            self.noise = max(floor, self.innovation_avg - p_tt)
        else:
            self.noise = floor
        s = p_tt + self.noise
        self.innovation_avg += (nu * nu - self.innovation_avg) * SENSOR_NOISE_WEIGHT
        k = [self.p[i][I_T] / s for i in range(STATE_SIZE)]
        self.z = [zi + ki * nu for zi, ki in zip(self.z, k, strict=True)]
        self.p = symmetrise(subtract(self.p, outer(k, self.p[I_T])))
        self.corrections += 1
        return nu

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps: 27 numbers, 27 x 27, q, R and v."""
        return {
            "z": list(self.z),
            "p": [list(r) for r in self.p],
            "q": self.sensor_step,
            "r": self.noise,
            "v": self.innovation_avg,
            "corrections": self.corrections,
            "last_outdoor": self.last_outdoor,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HeatFilter | None:
        """Restore, or None if the state is not 27 finite numbers with a
        finite 27 x 27 uncertainty: it is then relearned [D031]."""
        try:
            z = [float(x) for x in data["z"]]
            p = [[float(x) for x in r] for r in data["p"]]
        except (KeyError, TypeError, ValueError):
            return None
        if len(z) != STATE_SIZE or len(p) != STATE_SIZE or any(len(r) != STATE_SIZE for r in p):
            return None
        if not all(math.isfinite(x) for x in z) or not is_finite(p):
            return None
        return cls(
            z=z,
            p=p,
            sensor_step=_finite_or_none(data.get("q")),
            noise=_finite_or_none(data.get("r")),
            innovation_avg=_finite_or_none(data.get("v")),
            corrections=int(data.get("corrections", 0)),
            last_outdoor=_finite_or_none(data.get("last_outdoor")),
        )


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    out = float(value)
    return out if math.isfinite(out) else None
