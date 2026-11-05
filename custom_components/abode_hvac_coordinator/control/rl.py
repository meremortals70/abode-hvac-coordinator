"""A learned policy, in shadow until promoted (Section 4.9, control/rl.py) [I7].

It does nothing until a trained policy exists for the room. Training
happens away from Home Assistant, on a simulator built from the room's own
learned model and recorded weather, and never explores in the live room
[R14]. The learning algorithm is not yet established [NYE]; until it is,
no policy exists and this controller proposes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RlState:
    """The policy's inputs (4.9)."""

    distance_from_centre: float
    slope: float
    drift: float
    outdoor: float
    sun: float
    price_now: float
    price_next_hour: float
    hour: float
    mass: float


@dataclass(slots=True)
class RlController:
    """One room's learned controller."""

    policy: Callable[[RlState], float] | None = None

    def propose(self, state: RlState, allowed: Callable[[float], float]) -> float | None:
        """The policy's rate, within what the plan may do (allowed clamps
        a rate to the plan's NOT ALLOWED list); None without a policy."""
        if self.policy is None:
            return None
        return allowed(self.policy(state))
