"""Which controller is in charge (Section 4.9, control/arbiter.py) [I7][Prop].

The plan and PID are in charge from the first minute. The learned
controller runs beside them, takes over only once it has proved better on
that room, and hands back at once if it fails: the Simplex pattern
[R10][R11].
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..const import (
    ARBITER_FIDELITY_C,
    ARBITER_HAND_DOWN_DAYS,
    ARBITER_HAND_UP_TRUST_DAYS,
    ARBITER_HAND_UP_WINS,
    ARBITER_WINDOW_DAYS,
)
from ..models import Controller


@dataclass(frozen=True, slots=True)
class DayScore:
    """A controller's day: minutes outside the band while occupied, and J."""

    minutes_outside: float
    cost: float


@dataclass(frozen=True, slots=True)
class DayResult:
    """One day's comparison, judged at midnight.

    in_charge_real is the day as it happened; in_charge_sim the same day
    simulated on the model, and fidelity the average difference between
    the two room temperatures, C; other_sim the other controller's choices
    simulated on the same day (None while it has no policy).
    """

    day: str
    in_charge_real: DayScore
    in_charge_sim: DayScore
    fidelity: float
    other_sim: DayScore | None


def rl_wins(rl: DayScore, plan: DayScore) -> bool:
    """RL wins a day when it has no more minutes outside the band and a
    lower J."""
    return rl.minutes_outside <= plan.minutes_outside and rl.cost < plan.cost


@dataclass(slots=True)
class Arbiter:
    """One room's arbiter."""

    in_charge: Controller = Controller.PLAN_PID
    since: datetime | None = None
    wins: deque[bool | None] = field(default_factory=lambda: deque(maxlen=ARBITER_WINDOW_DAYS))
    plan_streak: int = 0
    last_reason: str = "the plan and PID are in charge from the first minute"
    handed_over: bool = False

    def end_of_day(self, result: DayResult, trusted: bool, six_hour_days: int, now: datetime) -> None:
        """EVERY DAY at midnight: score the day, then hand up or down."""
        self.handed_over = False
        if result.other_sim is None:
            self.wins.append(None)
            self.last_reason = "the learned controller has no policy"
        elif result.fidelity > ARBITER_FIDELITY_C:
            self.wins.append(None)
            self.last_reason = (
                f"the model reproduced the day to {result.fidelity:.2f} C, more than {ARBITER_FIDELITY_C} C: "
                "no comparison counts today"
            )
        else:
            rl_score = result.other_sim if self.in_charge is Controller.PLAN_PID else result.in_charge_sim
            plan_score = result.in_charge_sim if self.in_charge is Controller.PLAN_PID else result.other_sim
            won = rl_wins(rl_score, plan_score)
            self.wins.append(won)
            self.plan_streak = 0 if won else self.plan_streak + 1
            self.last_reason = "the learned controller won the day" if won else "the plan and PID won the day"
        if self.in_charge is Controller.RL:
            if not trusted:
                self._hand(Controller.PLAN_PID, "the model stopped being trusted", now)
            elif self.plan_streak >= ARBITER_HAND_DOWN_DAYS:
                self._hand(Controller.PLAN_PID, "the plan and PID beat it two days running", now)
        else:
            won_days = sum(1 for w in self.wins if w)
            if six_hour_days >= ARBITER_HAND_UP_TRUST_DAYS and won_days >= ARBITER_HAND_UP_WINS:
                self._hand(Controller.RL, f"it won {won_days} of the last {len(self.wins)} days", now)

    def lost_trust(self, now: datetime) -> None:
        """HAND DOWN at once when the model stops being TRUSTED."""
        if self.in_charge is Controller.RL:
            self._hand(Controller.PLAN_PID, "the model stopped being trusted", now)

    def _hand(self, to: Controller, why: str, now: datetime) -> None:
        self.in_charge = to
        self.since = now
        self.plan_streak = 0
        self.handed_over = True
        self.last_reason = f"handed to {to.value}: {why}"

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps: who is in charge and since when, the last 14
        days of scores."""
        return {
            "in_charge": self.in_charge.value,
            "since": self.since.isoformat() if self.since else None,
            "wins": list(self.wins),
            "plan_streak": self.plan_streak,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Arbiter:
        out = cls()
        try:
            out.in_charge = Controller(data.get("in_charge", Controller.PLAN_PID.value))
        except ValueError:
            out.in_charge = Controller.PLAN_PID
        since = data.get("since")
        out.since = datetime.fromisoformat(since) if isinstance(since, str) else None
        out.wins.extend(w if isinstance(w, bool) else None for w in data.get("wins", []))
        streak = data.get("plan_streak", 0)
        out.plan_streak = streak if isinstance(streak, int) else 0
        return out
