"""How the room's moisture moves (Section 4.2-4.3, model/humidity.py)
[D026][D060][Prop].

Works in vapour pressure e (hPa), which changes only when water moves:

    de/dt = h (e_out - e) + q + occupancy [occupied]
            - drying [dry] - cooling x U [cool]
            + rebound [15 minutes after the compressor stops cooling or drying]

A second Kalman filter of the same kind as filter.py learns e and the
figures h, q, drying, cooling response, rebound and the vapour people add
while the room is occupied [D113]. Each figure moves only
on steps where it acts: its gain is set to zero on the others, and the
uncertainty is then updated in Joseph form, which stays correct for any
gain. Not simulated [NYE].
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..const import (
    COIL_REBOUND_S,
    FIGURE_LOG_RANGE,
    FILTER_GATE_NIS,
    FILTER_JACOBIAN_DELTA,
    HUMIDITY_NOISE_E_PER_H,
    HUMIDITY_NOISE_FIGURE,
    HUMIDITY_OCC_SETTLE_CORRECTIONS,
    HUMIDITY_START_COOLING_HPA_PER_C,
    HUMIDITY_START_DRYING_HPA_PER_H,
    HUMIDITY_START_H_PER_H,
    HUMIDITY_START_OCC_HPA_PER_H,
    HUMIDITY_START_Q_HPA_PER_H,
    HUMIDITY_START_REBOUND_HPA_PER_H,
    HUMIDITY_START_VAR_E,
    HUMIDITY_START_VAR_LOG_FIGURE,
    HUMIDITY_START_VAR_Q,
    SENSOR_NOISE_SETTLE_CORRECTIONS,
    SENSOR_NOISE_WEIGHT,
)
from ..hci import vapour_pressure
from ..linalg import (
    Matrix,
    add,
    diagonal,
    is_finite,
    multiply,
    outer,
    sandwich,
    scale,
    symmetrise,
    transpose,
)

H_E, H_LN_H, H_Q, H_LN_DRY, H_LN_COOL, H_LN_REB, H_LN_OCC = 0, 1, 2, 3, 4, 5, 6
H_SIZE = 7
_MINUTE_H = 1.0 / 60.0
_REBOUND_MIN = COIL_REBOUND_S / 60.0


@dataclass(frozen=True, slots=True)
class MoistureFigures:
    """The humidity model's figures."""

    h: float
    q: float
    drying: float
    cooling: float
    rebound: float
    occ: float  # vapour added per hour while the room is occupied [D113]


def moisture_figures(z: Sequence[float]) -> MoistureFigures:
    return MoistureFigures(
        h=math.exp(z[H_LN_H]),
        q=z[H_Q],
        drying=math.exp(z[H_LN_DRY]),
        cooling=math.exp(z[H_LN_COOL]),
        rebound=math.exp(z[H_LN_REB]),
        occ=math.exp(z[H_LN_OCC]),
    )


@dataclass(frozen=True, slots=True)
class MoistureMinute:
    """One minute of what moves moisture."""

    outdoor_e: float
    dry: bool  # drying with the compressor running
    cooling_rate: float  # U while cooling with the compressor running, else 0
    rebound: bool  # within 15 minutes after the compressor stopped cooling or drying
    occupied: bool = False  # someone is in the room: they add vapour [D113]


def rate_of_change(fig: MoistureFigures, e: float, m: MoistureMinute, *, use_occ: bool = True) -> float:
    """de/dt in hPa per hour. use_occ is False until the room has been seen
    both occupied and empty, when people's vapour cannot yet be told apart from
    the steady source: it is then predicted as absent, but still observed
    [D113]."""
    out = fig.h * (m.outdoor_e - e) + fig.q
    if m.dry:
        out -= fig.drying
    elif m.cooling_rate > 0.0:
        out -= fig.cooling * m.cooling_rate
    if m.occupied and use_occ:
        out += fig.occ
    if m.rebound:
        out += fig.rebound
    return out


_LOG_FIGURES = (H_LN_H, H_LN_DRY, H_LN_COOL, H_LN_REB, H_LN_OCC)


def moisture_step(z: Sequence[float], minutes: Sequence[MoistureMinute],
                  *, use_occ: bool = True) -> list[float]:
    """Five one-minute steps, each by the exact solution with its inputs
    held, as the plan and the forecast step: a straight-line minute
    diverges once h passes 120 per hour [Der]. The figures are unchanged."""
    fig = moisture_figures(z)
    e = z[H_E]
    for m in minutes:
        # de/dt = h (target - e), with target where the room's moisture settles
        target = e + rate_of_change(fig, e, m, use_occ=use_occ) / fig.h
        e = target + (e - target) * math.exp(-fig.h * _MINUTE_H)
    out = list(z)
    out[H_E] = e
    return out


def acting(minutes: Sequence[MoistureMinute]) -> list[bool]:
    """Which figures act on this step: e, h and q always; drying, the
    cooling response and the rebound only where they act [D026][D060]."""
    return [
        True,
        True,
        True,
        any(m.dry for m in minutes),
        any(m.cooling_rate > 0.0 and not m.dry for m in minutes),
        any(m.rebound for m in minutes),
        any(m.occupied for m in minutes),
    ]


@dataclass(slots=True)
class HumidityFilter:
    """The room's moisture filter."""

    z: list[float]
    p: Matrix
    sensor_step: float | None = None
    noise: float | None = None
    innovation_avg: float | None = None
    corrections: int = 0
    last_wet_stop: datetime | None = None
    _last_e: float | None = None
    vapour_noise: float = 0.0  # learned: the room's own unforecast change in e, hPa^2 per step
    reading_jitter: float | None = None  # learned: the reading's own noise, hPa^2
    _prior_avg: float | None = None  # running average of P-[e,e] less vapour_noise
    seen_occupied: bool = False  # the room has been seen with someone in it
    seen_empty: bool = False  # and seen empty
    occ_corrections: int = 0  # corrections taken since the room was identified
    # a source that only acts while someone is there cannot be told apart
    # from the steady one until the room has been seen both ways [D113]
    _step_readings: deque[float] = field(default_factory=lambda: deque(maxlen=3))

    @classmethod
    def start(cls, e0: float) -> HumidityFilter:
        z = [0.0] * H_SIZE
        z[H_E] = e0
        z[H_LN_H] = math.log(HUMIDITY_START_H_PER_H)
        z[H_Q] = HUMIDITY_START_Q_HPA_PER_H
        z[H_LN_DRY] = math.log(HUMIDITY_START_DRYING_HPA_PER_H)
        z[H_LN_COOL] = math.log(HUMIDITY_START_COOLING_HPA_PER_C)
        z[H_LN_REB] = math.log(HUMIDITY_START_REBOUND_HPA_PER_H)
        z[H_LN_OCC] = math.log(HUMIDITY_START_OCC_HPA_PER_H)
        p = diagonal(
            [HUMIDITY_START_VAR_E, HUMIDITY_START_VAR_LOG_FIGURE, HUMIDITY_START_VAR_Q]
            + [HUMIDITY_START_VAR_LOG_FIGURE] * 4
        )
        return cls(z=z, p=p)

    @property
    def occupancy_identified(self) -> bool:
        """Whether people's vapour can be told apart from the steady source
        at all: only once the room has been seen both occupied and empty [D113]."""
        return self.seen_occupied and self.seen_empty

    @property
    def occ_settled(self) -> bool:
        """Whether people's vapour has been corrected enough to be priced:
        one hour of steps after the room was identified. Pricing it at the
        starting guess cost 24 minutes below the band on the first morning of
        the whole-loop simulation [D113]."""
        return self.occ_corrections >= HUMIDITY_OCC_SETTLE_CORRECTIONS

    @property
    def priced_occ(self) -> float:
        """People's vapour as a plan or a forecast should price it: zero until
        the room has been seen both ways and the figure has settled on what it
        has seen [D113]. One accessor, so the filter, the plan and the
        forecast cannot disagree about when it counts."""
        if not (self.occupancy_identified and self.occ_settled):
            return 0.0
        return moisture_figures(self.z).occ

    def observe_e(self, e: float | None) -> None:
        """The smallest non-zero change seen in the room's vapour pressure."""
        if e is None:
            return
        if self._last_e is not None:
            change = abs(e - self._last_e)
            if change > 1e-9 and (self.sensor_step is None or change < self.sensor_step):
                self.sensor_step = change
                if self.noise is None:
                    self.noise = change**2 / 12.0
                if self.innovation_avg is None:
                    self.innovation_avg = self.noise
        self._last_e = e

    def five_minutes(self, minutes: Sequence[MoistureMinute], reading_e: float | None, trusted: bool) -> str | None:
        """Predict, then correct unless the step cannot be trusted
        (trusted is False for a STOOD-IN, MISSING or UNKNOWN value) [D074].
        Gives back the reason it did not correct, or None."""
        if not minutes:
            return "no minutes were seen"
        self.seen_occupied = self.seen_occupied or any(m.occupied for m in minutes)
        self.seen_empty = self.seen_empty or any(not m.occupied for m in minutes)
        z0, p0 = list(self.z), [r[:] for r in self.p]
        use_occ = self.occupancy_identified
        base = moisture_step(self.z, minutes, use_occ=use_occ)
        f = [[0.0] * H_SIZE for _ in range(H_SIZE)]
        for j in range(H_SIZE):
            moved = list(self.z)
            moved[j] += FILTER_JACOBIAN_DELTA
            out = moisture_step(moved, minutes, use_occ=use_occ)
            for i in range(H_SIZE):
                f[i][j] = (out[i] - base[i]) / FILTER_JACOBIAN_DELTA
        q = diagonal([HUMIDITY_NOISE_E_PER_H * len(minutes) / 60.0 + self.vapour_noise]
                     + [HUMIDITY_NOISE_FIGURE] * (H_SIZE - 1))
        self.z, self.p = base, symmetrise(add(sandwich(f, self.p), q))
        reason: str | None = None
        if reading_e is None:
            reason = "a humidity input was stood in, missing or unknown"
        elif self.sensor_step is None or self.noise is None or self.innovation_avg is None:
            reason = "the sensor's step is not yet known"
        elif not trusted:
            # the reading still says where the room's moisture is; the
            # figures learn nothing from a step whose inputs were not known,
            # as in the heat filter [D074]
            reason = "a humidity input was stood in, missing or unknown; the moisture corrected, no figure learned"
            only_e = [False] * H_SIZE
            only_e[H_E] = True
            self._correct(reading_e, only_e, learn=False)
        else:
            acts = acting(minutes)
            if not use_occ:
                # nothing yet separates people's vapour from the steady source:
                # the figure keeps its start until the room is seen both ways
                acts[H_LN_OCC] = False
            self._correct(reading_e, acts)
            if acts[H_LN_OCC]:
                self.occ_corrections += 1
        if not (is_finite(self.p) and all(math.isfinite(x) for x in self.z)):
            self.z, self.p = z0, p0
            reason = "a figure was not a finite number; step undone"
        return reason

    def _correct(self, reading_e: float, acts: Sequence[bool], *, learn: bool = True) -> None:
        """What the innovations show is split between the reading and the
        room, as in the heat filter [R17][R21]: R from the reading's second
        differences, the rest the room's own unforecast change in moisture,
        added to e's noise. Taking the whole miss as sensor noise learned
        6.5 hPa^2 in the house's history and left the estimate 2 hPa or
        more from the room on a quarter of its steps [Sim]."""
        assert self.sensor_step is not None and self.noise is not None and self.innovation_avg is not None
        nu = reading_e - self.z[H_E]
        p_ee = self.p[H_E][H_E]
        floor = self.sensor_step**2 / 12.0
        self._step_readings.append(reading_e)
        if len(self._step_readings) == 3:
            y0, y1, y2 = self._step_readings
            d2 = (y2 - 2.0 * y1 + y0) ** 2 / 6.0
            j0 = self.reading_jitter if self.reading_jitter is not None else floor
            self.reading_jitter = j0 + (d2 - j0) * SENSOR_NOISE_WEIGHT
        self.noise = max(floor, self.reading_jitter if self.reading_jitter is not None else floor)
        base = p_ee - self.vapour_noise
        self._prior_avg = base if self._prior_avg is None else self._prior_avg + (base - self._prior_avg) * SENSOR_NOISE_WEIGHT
        # a reading far outside what the filter expected is weighted down in
        # proportion, as in the heat filter [R20]
        s = max(p_ee + self.noise, nu * nu / FILTER_GATE_NIS)
        r_used = s - p_ee
        self.innovation_avg += (nu * nu - self.innovation_avg) * SENSOR_NOISE_WEIGHT
        if self.corrections >= SENSOR_NOISE_SETTLE_CORRECTIONS or not learn:
            self.vapour_noise = max(0.0, self.innovation_avg - self._prior_avg - self.noise)
        k = [self.p[i][H_E] / s if acts[i] else 0.0 for i in range(H_SIZE)]
        self.z = [zi + ki * nu for zi, ki in zip(self.z, k, strict=True)]
        # Joseph form: P = (I - K H) P (I - K H)^T + K R K^T, H = e_1
        ikh = [[(1.0 if i == j else 0.0) - (k[i] if j == H_E else 0.0) for j in range(H_SIZE)] for i in range(H_SIZE)]
        self.p = symmetrise(add(multiply(multiply(ikh, self.p), transpose(ikh)), scale(outer(k, k), r_used)))
        # the figures' logarithms are held in the range the heat filter's
        # are [Der]
        lo, hi = FIGURE_LOG_RANGE
        for i in _LOG_FIGURES:
            self.z[i] = min(max(self.z[i], lo), hi)
        if learn:
            self.corrections += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "z": list(self.z),
            "p": [list(r) for r in self.p],
            "q": self.sensor_step,
            "r": self.noise,
            "v": self.innovation_avg,
            "corrections": self.corrections,
            "vapour_noise": self.vapour_noise,
            "jitter": self.reading_jitter,
            "prior_avg": self._prior_avg,
            "seen": [self.seen_occupied, self.seen_empty],
            "occ_corrections": self.occ_corrections,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HumidityFilter | None:
        """Restore, or None if not finite: it is then relearned [D031]."""
        try:
            z = [float(x) for x in data["z"]]
            p = [[float(x) for x in r] for r in data["p"]]
        except (KeyError, TypeError, ValueError):
            return None
        if len(z) == H_SIZE - 1 and len(p) == H_SIZE - 1 and all(len(r) == H_SIZE - 1 for r in p):
            # a save from before occupancy added vapour [D113]: the figure
            # starts at its starting value, wide, and everything learned is
            # kept; the column and row it joins start at zero
            z.append(math.log(HUMIDITY_START_OCC_HPA_PER_H))
            p = [r + [0.0] for r in p] + [[0.0] * H_SIZE]
            p[H_LN_OCC][H_LN_OCC] = HUMIDITY_START_VAR_LOG_FIGURE
        if len(z) != H_SIZE or len(p) != H_SIZE or any(len(r) != H_SIZE for r in p):
            return None
        if not all(math.isfinite(x) for x in z) or not is_finite(p):
            return None
        lo, hi = FIGURE_LOG_RANGE
        if any(not lo <= z[i] <= hi for i in _LOG_FIGURES):
            return None  # learned outside the range it is held to: relearned
        out = cls(z=z, p=p, corrections=int(data.get("corrections", 0)))
        for key, attr in (("q", "sensor_step"), ("r", "noise"), ("v", "innovation_avg"), ("jitter", "reading_jitter"),
                          ("prior_avg", "_prior_avg")):
            v = data.get(key)
            if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v)):
                setattr(out, attr, float(v))
        cc = data.get("occ_corrections")
        if isinstance(cc, int) and not isinstance(cc, bool) and cc >= 0:
            out.occ_corrections = cc
        seen = data.get("seen")
        if isinstance(seen, list) and len(seen) == 2:
            out.seen_occupied, out.seen_empty = bool(seen[0]), bool(seen[1])
        vn = data.get("vapour_noise")
        if isinstance(vn, int | float) and not isinstance(vn, bool) and math.isfinite(float(vn)):
            out.vapour_noise = max(float(vn), 0.0)
        return out


def room_e(t: float | None, rh: float | None) -> float | None:
    """The room's vapour pressure from its temperature and humidity."""
    if t is None or rh is None:
        return None
    return vapour_pressure(t, rh)


def in_rebound(last_wet_stop: datetime | None, now: datetime) -> bool:
    """Within 15 minutes after the compressor stopped cooling or drying [D060]."""
    return last_wet_stop is not None and 0.0 <= (now - last_wet_stop).total_seconds() / 60.0 < _REBOUND_MIN

