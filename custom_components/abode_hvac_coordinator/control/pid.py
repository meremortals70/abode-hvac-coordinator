"""The PID: holding the room on the plan (Section 4.5, control/pid.py) [R7][Prop].

    u = u_plan + Kp [e + (1/Ti) integral(e dt) + Td (dT/dt - dT_path/dt)],  e = T - T_path

T is the filter's estimate of the room, not the raw reading [Prop][Sim].
The gains are worked out with every plan from the room's learned model,
SIMC's rules with tau_c = theta (a self-tuning regulator) [R7][R8].
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..const import CYCLE_S, PID_FINE_STEP_C, SETPOINT_MIN_HOLD_S
from ..model.physics import Figures, steady_gain_h
from ..models import PidTerms


@dataclass(frozen=True, slots=True)
class Gains:
    """Parallel-form gains: Kp per hour, Ti and Td in hours."""

    kp: float
    ti: float
    td: float


def actuation_minutes(setpoint_step: float | None) -> float:
    """How often the setpoint the unit holds can actually change: every
    cycle for a unit that steps in tenths or finer, otherwise once per
    shortest hold of the whole-degree alternation (4.6) [Prop]."""
    if setpoint_step is None or setpoint_step <= PID_FINE_STEP_C:
        return CYCLE_S / 60.0
    return SETPOINT_MIN_HOLD_S / 60.0


def gains(fig: Figures, delay_min: float, lag_min: float, actuation_min: float) -> Gains:
    """SIMC gains from the model (4.5) [R7][Der].

    tau1 = |G(0)| = (c + k) / (a (c + k) + b k); theta = the delay, plus
    half the actuation interval (decision of 27 October 2026); tau_c =
    theta; Kc = 1 / (tau_c + theta); tau_I = min(tau1, 4 (tau_c + theta));
    tau_D = the lag. Series to parallel: Kp = Kc (1 + tau_D / tau_I),
    Ti = tau_I + tau_D, Td = tau_I tau_D / (tau_I + tau_D).
    """
    tau1 = steady_gain_h(fig)
    theta = (delay_min + actuation_min / 2.0) / 60.0
    tc = theta
    kc = 1.0 / (tc + theta)
    t_i = min(tau1, 4.0 * (tc + theta))
    t_d = lag_min / 60.0
    return Gains(kp=kc * (1.0 + t_d / t_i), ti=t_i + t_d, td=t_i * t_d / (t_i + t_d))


@dataclass(slots=True)
class Pid:
    """One room's PID. Its integral is not kept across a restart [D031]."""

    integral: float = 0.0
    running: bool = False
    gains: Gains | None = None
    last_u: float | None = None
    last_time: datetime | None = None
    last_free: bool = False  # the last output was not at a limit

    def rest(self) -> None:
        """When the plan rests, dries or is off the PID does nothing."""
        self.running = False
        self.last_u = None
        self.last_time = None

    def bumpless(self, g: Gains, u_now: float, u_plan: float, e: float, d_term: float) -> None:
        """Set the integral so the output does not jump: the law solved
        for the integral at the output now [Der]. Used when the gains
        change and on a handover (4.9)."""
        self.integral = g.ti * ((u_now - u_plan) / g.kp - e - d_term)

    def step(
        self,
        *,
        now: datetime,
        g: Gains,
        u_plan: float,
        t_est: float,
        t_path: float,
        slope_est: float,
        slope_path: float,
        u_min: float,
        top: float,
        delivered: bool,
    ) -> PidTerms:
        """EVERY CYCLE while the plan cools or heats at u_plan (positive
        cools). delivered says whether the last cycle's output reached the
        unit as asked (Limits did not hold it) [D054]."""
        e = t_est - t_path
        d_term = g.td * (slope_est - slope_path)
        if not self.running:
            self.integral = 0.0  # starts again at zero each time the unit starts [Prop][Sim]
        elif self.gains is not None and g != self.gains and self.last_u is not None:
            self.bumpless(g, self.last_u, u_plan, e, d_term)
        elif self.last_time is not None and delivered and self.last_free:
            # no wind-up: only while the output is delivered as asked [D054][R9]
            self.integral += e * (now - self.last_time).total_seconds() / 3600.0
        raw = u_plan + g.kp * (e + self.integral / g.ti + d_term)
        sign = 1.0 if u_plan >= 0.0 else -1.0
        magnitude = raw * sign
        lo, hi = u_min, max(top, u_min)
        clamped = min(max(magnitude, lo), hi)
        u = sign * clamped
        self.last_free = lo < magnitude < hi
        self.running = True
        self.gains = g
        self.last_u = u
        self.last_time = now
        return PidTerms(
            u=u,
            error=e,
            p=g.kp * e,
            i=g.kp * self.integral / g.ti,
            d=g.kp * d_term,
            kp=g.kp,
            ti_h=g.ti,
            td_h=g.td,
        )
