"""The hours a room is usually occupied, learned from its own presence [D114].

A plan looks six hours ahead, and occupancy changes inside that window:
someone comes home, someone goes to bed. Taking the room as it is now over the
whole horizon makes the plan blind to both — it cannot anticipate the arrival
that adds vapour, and it keeps pricing people who have gone to bed. This is the
smallest thing that remembers: a fraction per hour of the day, learned from the
room's own occupancy, with no schedule to configure and no rule about clocks.

An hour nobody has seen yet is None, and the plan falls back to the room as it
is now, which is what it did before this.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

HOURS = 24
# how fast an hour forgets a day: an eighth of the way each time, so a week of
# days settles it and a change of routine shows within a few days [Set]
WEIGHT = 1.0 / 8.0


@dataclass(slots=True)
class OccupancyProfile:
    """The fraction of days each hour of the day has been occupied."""

    hours: list[float | None] = field(default_factory=lambda: [None] * HOURS)

    def observe(self, hour: float, occupied: bool) -> None:
        """One hour's answer, once an hour: a running share, not a schedule."""
        i = int(hour) % HOURS
        was = self.hours[i]
        answer = 1.0 if occupied else 0.0
        self.hours[i] = answer if was is None else was + (answer - was) * WEIGHT

    def at(self, hour: float) -> float | None:
        """The usual occupancy at that hour; None where no hour has been seen."""
        return self.hours[int(hour) % HOURS]

    def as_dict(self) -> dict[str, Any]:
        return {"hours": [None if h is None else round(h, 4) for h in self.hours]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OccupancyProfile:
        hours: list[float | None] = [None] * HOURS
        raw = data.get("hours")
        if isinstance(raw, list) and len(raw) == HOURS:
            for i, value in enumerate(raw):
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                number = float(value)
                if math.isfinite(number):
                    hours[i] = min(max(number, 0.0), 1.0)
        return cls(hours=hours)
