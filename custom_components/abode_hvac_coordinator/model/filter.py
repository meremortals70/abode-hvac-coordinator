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
    FIGURE_LOG_RANGE,
    FILTER_GATE_NIS,
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
    UNIT_DELAY_RANGE_MIN,
    UNIT_LAG_RANGE_MIN,
)
from ..linalg import (
    Matrix,
    add,
    diagonal,
    is_finite,
    outer,
    sandwich,
    scale,
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
    curve_rate,
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
    # every minute's compressor state was known: an hour counts as resting
    # only when each of its steps was known and stopped (the trust check)
    compressor_known: bool = True


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


def process_noise(compressor_ran: bool, unknown_t: float = 0.0, unknown_m: float = 0.0) -> Matrix:
    """Q per 5 minutes; the air's noise is raised while the unit runs,
    because the unit's rate is itself learned [Prop][NYE]; and by the
    variance of an input the step could not know (unknown_t, unknown_m:
    see unknown_input_variance) [Der]."""
    step_h = STEP_MIN / 60.0
    t_noise = FILTER_NOISE_T_PER_H * step_h + unknown_t
    if compressor_ran:
        t_noise += (FILTER_RUNNING_RATE_NOISE_C_PER_H * step_h) ** 2
    d = [t_noise, FILTER_NOISE_M_PER_H * step_h + unknown_m] + [FILTER_NOISE_FIGURE] * (STATE_SIZE - 4)
    d += [FILTER_NOISE_LN_LAG, FILTER_NOISE_DELAY]
    return diagonal(d)


_LOG_FIGURES = frozenset(
    (I_LN_A, I_LN_B, I_LN_C, I_LN_K, I_LN_S, I_LN_M, *range(I_COOL, I_COOL + CURVE_POINTS),
     *range(I_HEAT, I_HEAT + CURVE_POINTS))
)


def _at_top(j: int, value: float) -> bool:
    """Whether a step up in z[j] would pass the top of its range."""
    if j == I_LN_LAG:
        return value + FILTER_JACOBIAN_DELTA > math.log(UNIT_LAG_RANGE_MIN[1])
    if j == I_DELAY:
        return value + FILTER_JACOBIAN_DELTA > UNIT_DELAY_RANGE_MIN[1]
    if j in _LOG_FIGURES:
        return value + FILTER_JACOBIAN_DELTA > FIGURE_LOG_RANGE[1]
    return False


def jacobian(z: Sequence[float], inputs: StepInputs, base: StepResult) -> Matrix:
    """F: the change in z- for a small change in each element of z."""
    n = len(z)
    f = [[0.0] * n for _ in range(n)]
    for j in range(n):
        moved = list(z)
        # at the top of the lag's or delay's range a step up is clamped away
        # and would show no effect: there the step is taken downward
        delta = -FILTER_JACOBIAN_DELTA if _at_top(j, z[j]) else FILTER_JACOBIAN_DELTA
        moved[j] += delta
        out = step(moved, inputs).z
        for i in range(n):
            f[i][j] = (out[i] - base.z[i]) / delta
    return f


def unknown_input_variance(z: Sequence[float], minutes: Sequence[MinuteSample]) -> tuple[float, float]:
    """The variance a step's unknown inputs add to the air and the mass
    over 5 minutes [Der]. A minute whose compressor state is unknown is run
    at the rate its mode and effort ask, r; the unit may have delivered
    anything from 0 to r, so the air may be out by up to r x 5 min. Sun
    that had to be assumed is taken as full (1); the true sun lies between
    0 and 1, so the air may be out by up to s x 5 min and the mass by
    m x 5 min. Each is taken as the mean square of a miss spread evenly
    from 0 to its largest, one third of the largest squared."""
    fig = figures(z)
    step_h = STEP_MIN / 60.0
    var_t = var_m = 0.0
    unknown = [m for m in minutes if not m.compressor_known]
    if unknown:
        def most(m: MinuteSample) -> float:
            # a mode with a direction: the rate its effort asks; heat_cool or
            # auto: either way, up to the larger curve's top
            if m.mode_direction > 0:
                return curve_rate(curve_efforts(), fig.cool_steps, m.effort)
            if m.mode_direction < 0:
                return abs(curve_rate(curve_efforts(), fig.heat_steps, m.effort))
            top = curve_efforts()[-1]
            return max(curve_rate(curve_efforts(), fig.cool_steps, top),
                       abs(curve_rate(curve_efforts(), fig.heat_steps, top)))
        r = max(most(m) for m in unknown)
        var_t += (r * step_h * len(unknown) / STEP_MIN) ** 2 / 3.0
    if any(m.sun_estimated for m in minutes):
        var_t += (fig.s * step_h) ** 2 / 3.0
        var_m += (fig.m * step_h) ** 2 / 3.0
    return var_t, var_m


def state_only() -> list[bool]:
    """The air and the mass only: a step whose inputs were not all known
    corrects where the room is and teaches no figure [D074]."""
    acts = [False] * STATE_SIZE
    acts[I_T] = acts[I_M] = True
    return acts


def acting(minutes: Sequence[MinuteSample], history: Sequence[tuple[float, int]]) -> list[bool]:
    """Which parts of the state a step may correct: the air, the mass, the
    exchange figures and the heat pattern always; the sun's two figures
    only on a step with sun on the glass; each direction's curve only when
    the unit worked that way in the last 40 minutes, and the delay and lag
    when it worked either way. A figure whose input did not act has nothing
    in the reading to learn from: without this its estimate walks on the
    filter's cross-covariances alone, as the mass's sun gain did in a room
    whose sun was never measured (0.1 to 87 per hour). The humidity filter
    learns the same way [D026][D060][R16]."""
    sun = any(m.sun > 0.0 for m in minutes)
    cool = any(d > 0 for _, d in history)
    heat = any(d < 0 for _, d in history)
    acts = [True] * STATE_SIZE
    acts[I_LN_S] = acts[I_LN_M] = sun
    for j in range(CURVE_POINTS):
        acts[I_COOL + j] = cool
        acts[I_HEAT + j] = heat
    acts[I_LN_LAG] = acts[I_DELAY] = cool or heat
    return acts


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
    _history_known: deque[bool] = field(default_factory=lambda: deque(maxlen=HISTORY_MIN))
    air_noise: float = 0.0  # learned: the room's own unforecast change, C^2 per step
    reading_jitter: float | None = None  # learned: the reading's own noise, C^2
    _prior_avg: float | None = None  # running average of P-[T,T] less air_noise
    _step_readings: deque[float] = field(default_factory=lambda: deque(maxlen=3))
    _seen_minutes: int = 0  # minutes collected since start-up
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
        self._history_known.append(sample.compressor_known)
        self._seen_minutes += 1

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
        known = all(m.compressor_known for m in minutes)
        blocked = self._why_not_at_all(minutes)
        reason = blocked or self._why_not(minutes, complete, bool(outdoors))
        # an unknown input widens the air and mass whether or not there is a
        # reading to correct them [D074][Der]
        unknown_t, unknown_m = unknown_input_variance(self.z, minutes)
        z_before, p_before, u_before = list(self.z), [r[:] for r in self.p], self.u
        try:
            base = step(self.z, inputs)
            f = jacobian(self.z, inputs, base)
        except (ArithmeticError, ValueError):
            # the matrix exponential overflowed or its matrix was singular:
            # nothing is changed, and the step says why [Der]
            failed = FilterStep(now, self.z[I_T], False, "the model's arithmetic failed; step undone", None, inputs,
                                (), ran, known)
            self.recent.append(failed)
            return failed
        p_minus = add(sandwich(f, self.p), process_noise(ran, unknown_t + self.air_noise, unknown_m))
        self.z, self.p, self.u = base.z, symmetrise(p_minus), base.u_end
        predicted = self.z[I_T]
        innovation: float | None = None
        reading = minutes[-1].reading
        if blocked is None and reading is not None:
            # the reading always tells the filter where the room is; the
            # figures learn only from a step whose inputs were all known
            # [D074][Prop]. Without this the air and mass ran open-loop
            # through every step with the unit running and its compressor
            # unknown: a whole night in the living room's history
            learn = reason is None
            acts = acting(minutes, history) if learn else state_only()
            innovation = self._correct(reading, acts, learn=learn)
            if learn:
                self.last_correction = now
        if not (is_finite(self.p) and all(math.isfinite(x) for x in self.z) and math.isfinite(self.u)):
            self.z, self.p, self.u = z_before, p_before, u_before
            result = FilterStep(now, predicted, False, "a figure was not a finite number; step undone", None,
                                inputs, base.u_path, ran, known)
        else:
            result = FilterStep(now, predicted, innovation is not None and reason is None, reason, innovation, inputs,
                                base.u_path, ran, known)
        self.recent.append(result)
        return result

    def _why_not_at_all(self, minutes: Sequence[MinuteSample]) -> str | None:
        """The reason the reading cannot be used at all, or None [D074]."""
        if minutes[-1].reading is None:
            return "the room reading was stood in or missing"
        if self.sensor_step is None or self.noise is None or self.innovation_avg is None:
            return "the sensor's step is not yet known"
        return None

    def _why_not(self, minutes: Sequence[MinuteSample], complete: bool, outdoor_seen: bool) -> str | None:
        """The reason the step's figures learn nothing, or None: the room
        and mass are still corrected [D074][Prop]."""
        if not all(m.compressor_known for m in minutes):
            return "the compressor state, or the way it ran, was unknown"
        if not all(self._history_known):
            # the unit's rate now comes through its delay and lag from the
            # last 40 minutes: a minute among them whose compressor was not
            # known would be learned as running the way its mode asks
            return "the compressor state was unknown in the unit's last 40 minutes"
        if any(m.sun_estimated for m in minutes):
            return "the sun had to be assumed"
        if not complete:
            return "fewer than five minutes were seen"
        if not outdoor_seen:
            return "the outdoor temperature was missing"
        fig = figures(self.z)
        if self._seen_minutes < max(HISTORY_MIN, fig.delay_min + 3.0 * fig.lag_min):
            # the unit's rate is not kept across a restart and starts again
            # at 0: until it has come through the delay and three lags (95 %
            # of the way, e^-3) from minutes actually seen, the filter would
            # take the unit as slower than it is and learn the curve high
            return "the unit's rate is not yet known since start-up"
        return None

    def _correct(self, reading: float, acts: Sequence[bool], *, learn: bool = True) -> float:
        """CORRECT [R6][R17][Prop][Sim].

        What the innovations show is split between the reading and the
        room [R17][R21]. The reading's own noise R is what its second
        differences show, step to step: a sensor's error is new each
        reading, while a room's change carries on, and over 5-minute steps
        a carried-on change hardly shows in a second difference. The rest
        of the innovations' variance, past R and the filter's own
        uncertainty, is the room's unforecast change: it is added to the
        air's noise, so a room that has moved is followed, not taken as a
        noisy sensor. Without the split, R took the whole miss: 2.17 C^2
        on a sensor in 0.1 C steps, and the estimate stayed 1 C or more
        from the room for 12 hours [Sim: replay of the house's history].
        """
        assert self.sensor_step is not None and self.noise is not None and self.innovation_avg is not None
        nu = reading - self.z[I_T]
        p_tt = self.p[I_T][I_T]
        floor = self.sensor_step**2 / 12.0
        self._step_readings.append(reading)
        if len(self._step_readings) == 3:
            y0, y1, y2 = self._step_readings
            d2 = (y2 - 2.0 * y1 + y0) ** 2 / 6.0  # white noise of variance R gives 6 R
            j0 = self.reading_jitter if self.reading_jitter is not None else floor
            self.reading_jitter = j0 + (d2 - j0) * SENSOR_NOISE_WEIGHT
        self.noise = max(floor, self.reading_jitter if self.reading_jitter is not None else floor)
        base = p_tt - self.air_noise
        self._prior_avg = base if self._prior_avg is None else self._prior_avg + (base - self._prior_avg) * SENSOR_NOISE_WEIGHT
        # a reading far outside what the filter expected is weighted down in
        # proportion, never taken as exact: its variance is at least
        # nu^2 / 10.83, so it moves the state as a 3.3-sigma surprise would
        # at most [R20]. The noise is still learned from the whole nu [R17].
        s = max(p_tt + self.noise, nu * nu / FILTER_GATE_NIS)
        r_used = s - p_tt
        self.innovation_avg += (nu * nu - self.innovation_avg) * SENSOR_NOISE_WEIGHT
        if self.corrections >= SENSOR_NOISE_SETTLE_CORRECTIONS or not learn:
            self.air_noise = max(0.0, self.innovation_avg - self._prior_avg - self.noise)
        k = [self.p[i][I_T] / s if acts[i] else 0.0 for i in range(STATE_SIZE)]
        self.z = [zi + ki * nu for zi, ki in zip(self.z, k, strict=True)]
        # Joseph form, P = (I - K H) P (I - K H)^T + K R K^T with H = e_T: it
        # holds for a gain that leaves figures out, where P - K H P does not
        q = subtract(self.p, outer(k, self.p[I_T]))
        q = subtract(q, outer([row[I_T] for row in q], k))
        self.p = symmetrise(add(q, scale(outer(k, k), r_used)))
        self._hold_unit_in_range()
        if learn:
            self.corrections += 1
        return nu

    def _hold_unit_in_range(self) -> None:
        """The delay and lag are held in their ranges in the state itself,
        not only where they are read, so the filter cannot carry either
        beyond its range unseen [R19]; and every figure within the range the
        arithmetic can carry (FIGURE_LOG_RANGE) [Der]."""
        lo, hi = (math.log(v) for v in UNIT_LAG_RANGE_MIN)
        self.z[I_LN_LAG] = min(max(self.z[I_LN_LAG], lo), hi)
        self.z[I_DELAY] = min(max(self.z[I_DELAY], UNIT_DELAY_RANGE_MIN[0]), UNIT_DELAY_RANGE_MIN[1])
        for i in _LOG_FIGURES:
            self.z[i] = min(max(self.z[i], FIGURE_LOG_RANGE[0]), FIGURE_LOG_RANGE[1])

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
            "air_noise": self.air_noise,
            "jitter": self.reading_jitter,
            "prior_avg": self._prior_avg,
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
        lo, hi = (math.log(v) for v in UNIT_LAG_RANGE_MIN)
        if not (lo - 1e-9 <= z[I_LN_LAG] <= hi + 1e-9) or any(
            not (FIGURE_LOG_RANGE[0] - 1e-9 <= z[i] <= FIGURE_LOG_RANGE[1] + 1e-9) for i in _LOG_FIGURES
        ):
            # learned before the lag was held in range: a lag outside it was
            # learned by a filter that could diverge, and is relearned [R19]
            return None
        return cls(
            z=z,
            p=p,
            sensor_step=_finite_or_none(data.get("q")),
            noise=_finite_or_none(data.get("r")),
            innovation_avg=_finite_or_none(data.get("v")),
            corrections=int(data.get("corrections", 0)),
            last_outdoor=_finite_or_none(data.get("last_outdoor")),
            air_noise=max(_finite_or_none(data.get("air_noise")) or 0.0, 0.0),
            reading_jitter=_finite_or_none(data.get("jitter")),
            _prior_avg=_finite_or_none(data.get("prior_avg")),
        )


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    out = float(value)
    return out if math.isfinite(out) else None
