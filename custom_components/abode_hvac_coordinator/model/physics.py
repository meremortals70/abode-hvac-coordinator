"""The room's heat balance (Section 4.2) [R1][Prop].

    dT/dt = a (T_out - T) + b (M - T) + s S + g(t) - U
    dM/dt = c (T - M) + k (T_out - M) + m S

Rates are per hour, temperatures in C, time in hours. U is the rate the
unit removes (+, cooling) or adds (-, heating), in C/h. The unit reaches
r(x(t - delay)) through a first-order lag; the delay and the lag are
carried in the state with the figures (decision of 27 October 2026).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..const import UNIT_DELAY_RANGE_MIN, UNIT_LAG_MIN_MIN
from ..linalg import (
    Matrix,
    eigenvalues_2x2,
    expm_2x2,
    identity,
    inverse_2x2,
    mat_vec,
    multiply,
    subtract,
)

# Positions in the filter's state z (Section 4.3)
I_T = 0
I_M = 1
I_LN_A, I_LN_B, I_LN_C, I_LN_K, I_LN_S, I_LN_M = 2, 3, 4, 5, 6, 7
I_G0, I_AL1, I_BE1, I_AL2, I_BE2 = 8, 9, 10, 11, 12
I_COOL = 13  # rho_cool 1..6 at 13..18
I_HEAT = 19  # rho_heat 1..6 at 19..24
I_LN_LAG = 25  # the unit's lag, minutes, as a logarithm
I_DELAY = 26  # the unit's delay, minutes
CURVE_POINTS = 6
STATE_SIZE = 27

MINUTE_H = 1.0 / 60.0
STEP_H = 5.0 / 60.0


@dataclass(frozen=True, slots=True)
class Figures:
    """The model's figures, read from the state z."""

    a: float
    b: float
    c: float
    k: float
    s: float
    m: float
    g0: float
    al1: float
    be1: float
    al2: float
    be2: float
    cool_steps: tuple[float, ...]  # rho_cool 1..6 (logarithms)
    heat_steps: tuple[float, ...]  # rho_heat 1..6 (logarithms)
    lag_min: float
    delay_min: float

    def g(self, hour: float) -> float:
        """Heat from people and appliances at hour h, C/h [Prop]."""
        w = 2.0 * math.pi * hour / 24.0
        return (
            self.g0
            + self.al1 * math.cos(w)
            + self.be1 * math.sin(w)
            + self.al2 * math.cos(2.0 * w)
            + self.be2 * math.sin(2.0 * w)
        )


def figures(z: Sequence[float]) -> Figures:
    """a b c k s m = e^(their logs); the heat pattern as it is."""
    return Figures(
        a=math.exp(z[I_LN_A]),
        b=math.exp(z[I_LN_B]),
        c=math.exp(z[I_LN_C]),
        k=math.exp(z[I_LN_K]),
        s=math.exp(z[I_LN_S]),
        m=math.exp(z[I_LN_M]),
        g0=z[I_G0],
        al1=z[I_AL1],
        be1=z[I_BE1],
        al2=z[I_AL2],
        be2=z[I_BE2],
        cool_steps=tuple(z[I_COOL : I_COOL + CURVE_POINTS]),
        heat_steps=tuple(z[I_HEAT : I_HEAT + CURVE_POINTS]),
        lag_min=max(math.exp(z[I_LN_LAG]), UNIT_LAG_MIN_MIN),
        delay_min=min(max(z[I_DELAY], UNIT_DELAY_RANGE_MIN[0]), UNIT_DELAY_RANGE_MIN[1]),
    )


def curve_values(steps: Sequence[float]) -> list[float]:
    """r(x_i) = sum over j <= i of e^(rho_j): always rising [Der]."""
    out: list[float] = []
    total = 0.0
    for rho in steps:
        total += math.exp(rho)
        out.append(total)
    return out


def curve_rate(efforts: Sequence[float], steps: Sequence[float], x: float) -> float:
    """The steady rate at effort x, C/h.

    Straight lines between the curve's efforts, the top value beyond. Below
    the first effort the first effort's rate: a unit that runs at all runs
    at least at its lowest rate, as an inverter does. An effort at or below
    zero asks for nothing, and gives no rate.
    """
    values = curve_values(steps)
    if x <= 0.0:
        return 0.0
    if x <= efforts[0]:
        return values[0]
    for i in range(1, len(efforts)):
        if x <= efforts[i]:
            f = (x - efforts[i - 1]) / (efforts[i] - efforts[i - 1])
            return values[i - 1] + f * (values[i] - values[i - 1])
    return values[-1]


def curve_effort(efforts: Sequence[float], steps: Sequence[float], rate: float) -> float:
    """The effort for a rate: the curve read backwards. A rate above the
    curve's top asks for the top effort [D056]; one at or below the first
    effort's rate asks for the first effort."""
    values = curve_values(steps)
    if rate <= 0.0:
        return 0.0
    if rate <= values[0]:
        return efforts[0]
    for i in range(1, len(values)):
        if rate <= values[i]:
            f = (rate - values[i - 1]) / (values[i] - values[i - 1])
            return efforts[i - 1] + f * (efforts[i] - efforts[i - 1])
    return efforts[-1]


HISTORY_MIN = 40  # minutes of effort kept: the longest delay and a step


@dataclass(frozen=True, slots=True)
class StepInputs:
    """Five minutes of inputs to STEP.

    outdoor and sun are the step's averages; hours gives the hour of day at
    the start of each of the five minutes. history holds the room's effort
    and direction (+1 cooling, -1 heating, 0 resting) for each minute,
    oldest first, the last five being this step's; u_start is the unit's
    rate when the step began.
    """

    outdoor: float
    sun: float
    hours: tuple[float, float, float, float, float]
    history: tuple[tuple[float, int], ...]
    u_start: float
    curve_efforts: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class StepResult:
    """The state after STEP, and the unit's rate along the way."""

    z: list[float]
    u_path: tuple[float, ...]
    u_end: float


def unit_rate(fig: Figures, curve_efforts: Sequence[float], effort: float, direction: int) -> float:
    """r(effort) in the direction asked: positive cools, negative heats."""
    if direction > 0:
        return curve_rate(curve_efforts, fig.cool_steps, effort)
    if direction < 0:
        return -curve_rate(curve_efforts, fig.heat_steps, effort)
    return 0.0


def delayed_rate(fig: Figures, inputs: StepInputs, minute: int) -> float:
    """r(x(t - delay)) for one minute of the step.

    The delayed moment falls between two recorded minutes; the rate is
    taken in proportion between them, so the delay can be learned as a
    continuous figure. Before the first recorded minute the unit rested.
    """
    hist = inputs.history
    n = len(hist)
    pos = n - 5 + minute - fig.delay_min
    lo = math.floor(pos)
    frac = pos - lo

    def at(j: int) -> float:
        if j < 0 or n == 0:
            return 0.0
        effort, direction = hist[min(j, n - 1)]
        return unit_rate(fig, inputs.curve_efforts, effort, direction)

    return at(lo) + frac * (at(lo + 1) - at(lo))


def step(z: Sequence[float], inputs: StepInputs) -> StepResult:
    """STEP (z, 5 minutes of inputs) -> z after 5 minutes [Der].

    Five one-minute steps. The unit's rate follows r(effort) after its
    delay, through its first-order lag, U = U + (r - U) x 1 min / lag, each
    minute. The figures, delay and lag are unchanged by STEP.
    """
    fig = figures(z)
    t, mass = z[I_T], z[I_M]
    u = inputs.u_start
    path: list[float] = []
    for i in range(5):
        r = delayed_rate(fig, inputs, i)
        u = u + (r - u) * 1.0 / fig.lag_min
        path.append(u)
        g = fig.g(inputs.hours[i])
        dt = fig.a * (inputs.outdoor - t) + fig.b * (mass - t) + fig.s * inputs.sun + g - u
        dm = fig.c * (t - mass) + fig.k * (inputs.outdoor - mass) + fig.m * inputs.sun
        t = t + dt * MINUTE_H
        mass = mass + dm * MINUTE_H
    out = list(z)
    out[I_T], out[I_M] = t, mass
    return StepResult(out, tuple(path), u)


def a_matrix(fig: Figures) -> Matrix:
    """A = [[-(a+b), b], [c, -(c+k)]] (Section 4.2)."""
    return [[-(fig.a + fig.b), fig.b], [fig.c, -(fig.c + fig.k)]]


def b_matrix(fig: Figures) -> Matrix:
    """B = [[a, s, 1, -1], [k, m, 0, 0]] for inputs (outdoor, S, g, U)."""
    return [[fig.a, fig.s, 1.0, -1.0], [fig.k, fig.m, 0.0, 0.0]]


@dataclass(frozen=True, slots=True)
class ExactStep:
    """The exact 5-minute matrices: next (T, M) = Phi (T, M) + Gam u."""

    phi: Matrix
    gam: Matrix

    def advance(self, tm: tuple[float, float], outdoor: float, sun: float, g: float, u: float) -> tuple[float, float]:
        """(T, M) five minutes on, inputs held for the step."""
        x = mat_vec(self.phi, [tm[0], tm[1]])
        w = mat_vec(self.gam, [outdoor, sun, g, u])
        return x[0] + w[0], x[1] + w[1]


def exact_step(fig: Figures, dt_h: float = STEP_H) -> ExactStep:
    """Phi = e^(A dt); Gam = A^-1 (Phi - I) B [Der]. det A > 0 for every
    room (Section 4.2), so A^-1 exists."""
    a = a_matrix(fig)
    phi = expm_2x2(a, dt_h)
    gam = multiply(multiply(inverse_2x2(a), subtract(phi, identity(2))), b_matrix(fig))
    return ExactStep(phi, gam)


def time_constants_h(fig: Figures) -> tuple[float, float]:
    """The room's fast and slow time constants, hours: the reciprocals of
    A's eigenvalues, which are real, distinct and negative (4.2)."""
    l1, l2 = eigenvalues_2x2(a_matrix(fig))
    return -1.0 / l2, -1.0 / l1


def steady_gain_h(fig: Figures) -> float:
    """|G(0)| = (c + k) / (a (c + k) + b k), hours (Section 4.5)."""
    return (fig.c + fig.k) / (fig.a * (fig.c + fig.k) + fig.b * fig.k)
