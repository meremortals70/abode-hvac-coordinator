"""Is the model right? (Section 4.3, model/trust.py) [Prop].

Every hour each resting hour's one-hour forecast is compared with what
happened: trusted when the average error over the last 48 is at most
0.3 C, with at least 24. The same test is applied to six-hour forecasts
over 14 days. Trust is published, and decides only whether the learned
controller may take over [NYE: the thresholds are set from replay, M3].
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..const import (
    TRUST_HOURS_KEPT,
    TRUST_HOURS_NEEDED,
    TRUST_ONE_HOUR_C,
    TRUST_SIX_HOUR_DAYS,
)


@dataclass(slots=True)
class Trust:
    """One room's forecast errors and whether the model is trusted."""

    one_hour_errors: deque[float] = field(default_factory=lambda: deque(maxlen=TRUST_HOURS_KEPT))
    six_hour_errors: deque[tuple[datetime, float]] = field(default_factory=deque)
    six_hour_days: deque[tuple[str, bool]] = field(default_factory=lambda: deque(maxlen=TRUST_SIX_HOUR_DAYS))
    weekly_errors: list[tuple[str, float]] = field(default_factory=list)
    _pending: tuple[datetime, float] | None = None
    _week: str | None = None
    _week_errors: list[float] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        """TRUSTED: the average one-hour error <= 0.3 C over at least 24."""
        return len(self.one_hour_errors) >= TRUST_HOURS_NEEDED and (
            statistics.fmean(self.one_hour_errors) <= TRUST_ONE_HOUR_C
        )

    @property
    def one_hour_error(self) -> float | None:
        """The average of the kept one-hour errors."""
        return statistics.fmean(self.one_hour_errors) if self.one_hour_errors else None

    @property
    def six_hour_trusted(self) -> bool:
        """The same test on the six-hour errors of the last 14 days."""
        errors = [e for _, e in self.six_hour_errors]
        return len(errors) >= TRUST_HOURS_NEEDED and statistics.fmean(errors) <= TRUST_ONE_HOUR_C

    def hourly(
        self,
        now: datetime,
        room_now: float | None,
        forecast_next_hour: float | None,
        last_hour_resting: bool,
        six_hour_error: float | None,
    ) -> None:
        """EVERY HOUR: score the forecast made an hour ago if the hour was
        resting, remember this hour's, and keep the six-hour check."""
        if self._pending is not None and room_now is not None and last_hour_resting:
            error = abs(room_now - self._pending[1])
            if math.isfinite(error):
                self.one_hour_errors.append(error)
                self._add_week(now, error)
        self._pending = (now, forecast_next_hour) if forecast_next_hour is not None else None
        if six_hour_error is not None and math.isfinite(six_hour_error):
            self.six_hour_errors.append((now, six_hour_error))
        horizon = now - timedelta(days=TRUST_SIX_HOUR_DAYS)
        while self.six_hour_errors and self.six_hour_errors[0][0] < horizon:
            self.six_hour_errors.popleft()

    def end_of_day(self, day: str) -> None:
        """At midnight: whether the model had six-hour trust that day, for
        the arbiter's 14 days (4.9)."""
        self.six_hour_days.append((day, self.six_hour_trusted))

    def six_hour_trusted_days(self) -> int:
        """How many of the last 14 days ended with six-hour trust."""
        return sum(1 for _, ok in self.six_hour_days if ok)

    def _add_week(self, now: datetime, error: float) -> None:
        """EVERY WEEK: the week's average one-hour error, for the scorecard."""
        year, week, _ = now.isocalendar()
        key = f"{year}-W{week:02d}"
        if self._week is not None and key != self._week and self._week_errors:
            self.weekly_errors.append((self._week, statistics.fmean(self._week_errors)))
            self._week_errors = []
        self._week = key
        self._week_errors.append(error)

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps: the last 48 one-hour errors, 14 days of
        six-hour errors, the weekly errors."""
        return {
            "one_hour": list(self.one_hour_errors),
            "six_hour": [[t.isoformat(), e] for t, e in self.six_hour_errors],
            "days": [[d, ok] for d, ok in self.six_hour_days],
            "weekly": [[w, e] for w, e in self.weekly_errors],
            "week": self._week,
            "week_errors": list(self._week_errors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Trust:
        """Restore; any number that is not finite is discarded [D031]."""
        out = cls()
        out.one_hour_errors.extend(_finite(x) for x in data.get("one_hour", []) if _ok(x))
        for t, e in data.get("six_hour", []):
            if _ok(e):
                out.six_hour_errors.append((datetime.fromisoformat(str(t)), float(e)))
        out.six_hour_days.extend((str(d), bool(ok)) for d, ok in data.get("days", []))
        out.weekly_errors = [(str(w), float(e)) for w, e in data.get("weekly", []) if _ok(e)]
        week = data.get("week")
        out._week = str(week) if week is not None else None
        out._week_errors = [float(x) for x in data.get("week_errors", []) if _ok(x)]
        return out


def _ok(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(float(x))


def _finite(x: Any) -> float:
    return float(x)
