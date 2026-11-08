"""Module 5, Control: one room's controllers and the arbiter (Section 6).

RoomControl holds the plan in force, the PID, the learned controller and
the arbiter together. Its methods only call them in the order the cycle
needs; every decision is in the part it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..models import ControlResult, Plan, StepKind
from .arbiter import Arbiter
from .pid import Gains, Pid
from .plan import PlanInputs, make_plan, path_now
from .rl import RlController


@dataclass(slots=True)
class RoomControl:
    """One room's control."""

    plan: Plan | None = None
    plan_t0: float | None = None
    pid: Pid = field(default_factory=Pid)
    rl: RlController = field(default_factory=RlController)
    arbiter: Arbiter = field(default_factory=Arbiter)
    gains: Gains | None = None
    last_kind: StepKind | None = None  # the kind the PID last held

    def replan(self, inputs: PlanInputs, gains: Gains) -> Plan:
        """Every 5 minutes: the plan, and the PID's gains with it."""
        self.plan = make_plan(inputs)
        self.plan_t0 = inputs.t_now if inputs.t_now is not None else inputs.heat.z[0]
        self.gains = gains
        return self.plan

    def cycle(
        self,
        *,
        now: datetime,
        t_est: float,
        slope_est: float,
        u_min: float,
        top: float,
        delivered: bool,
    ) -> ControlResult | None:
        """Every cycle: the plan's first step, held on its path by the PID.
        None before the first plan."""
        plan = self.plan
        if plan is None or self.gains is None or self.plan_t0 is None:
            return None
        first = plan.first
        in_charge = self.arbiter.in_charge
        if first.kind not in (StepKind.COOL, StepKind.HEAT):
            self.pid.rest()
            self.last_kind = None
            return ControlResult(first.kind, first.rate, first.blinds_closed, in_charge, plan, None,
                                 f"the plan's first step: {first.kind.value}")
        if self.last_kind is not None and self.last_kind is not first.kind:
            # cooling turned to heating, or back: a stop and a start, so the
            # integral starts again from zero [D016][Prop]
            self.pid.rest()
        self.last_kind = first.kind
        t_path, slope_path = path_now(plan, now, self.plan_t0)
        terms = self.pid.step(
            now=now,
            g=self.gains,
            u_plan=first.rate,
            t_est=t_est,
            t_path=t_path,
            slope_est=slope_est,
            slope_path=slope_path,
            u_min=u_min,
            top=top,
            delivered=delivered,
        )
        return ControlResult(first.kind, terms.u, first.blinds_closed, in_charge, plan, terms,
                             f"the plan's first step: {first.kind.value} at {first.rate:.2f} C/h, held by the PID")
