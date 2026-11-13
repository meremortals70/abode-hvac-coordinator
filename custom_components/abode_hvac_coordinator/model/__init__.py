"""Module 2, Model: one room's model (Section 6).

RoomModel holds one room's parts together. Its methods only call the parts
in the order the coordinator's timers need (every minute, every 5 minutes,
every hour, at midnight); every calculation is in the part it belongs to.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import ModelAnswers, Snapshot
from .filter import FilterStep, HeatFilter, MinuteSample, minute_sample
from .forecast import ForecastInput, drift_now, forecast, replay
from .humidity import HumidityFilter, MoistureMinute, in_rebound, room_e
from .physics import I_DELAY, I_LN_LAG, I_M, I_T, figures
from .trust import Trust
from .unit import UnitModel, rate_for_effort, top_rate

_FIGURE_NAMES = ("a", "b", "c", "k", "s", "m")


@dataclass(slots=True)
class RoomModel:
    """One room's model: the heat and moisture filters, the unit, trust."""

    heat: HeatFilter | None = None
    humidity: HumidityFilter | None = None
    unit: UnitModel = field(default_factory=UnitModel)
    trust: Trust = field(default_factory=Trust)
    last_step: FilterStep | None = None
    last_humidity_reason: str | None = None
    _moisture: list[MoistureMinute] = field(default_factory=list)
    _moisture_trusted: bool = True
    _last_minute: MinuteSample | None = None
    _hourly_z: deque[tuple[datetime, list[float]]] = field(default_factory=lambda: deque(maxlen=7))

    def minute(self, snapshot: Snapshot, local_hour: float) -> MinuteSample:
        """Every minute: the step averages and the unit's rate."""
        sample = minute_sample(snapshot, local_hour)
        if self.heat is None and sample.reading is not None:
            self.heat = HeatFilter.start(sample.reading)
        if self.heat is not None:
            self.heat.add_minute(sample)
            # the steady rate asked of the unit, not its lagged rate: U_min is a rate held [Prop]
            fig = figures(self.heat.z)
            if sample.dry and sample.compressor_running:
                # a dry run is not a cooling run: it says nothing about the
                # cooling floor, and what it does remove is dry's own share
                # [D108]
                self.unit.break_run()
            else:
                steady = rate_for_effort(fig, sample.effort, sample.direction)
                self.unit.observe_rate(sample.compressor_running and sample.compressor_known, steady,
                                       self.unit.lowest_rate(fig, sample.direction))
        self._moisture_minute(sample)
        self._last_minute = sample
        return sample

    def _moisture_minute(self, sample: MinuteSample) -> None:
        prev = self._last_minute
        wet_now = sample.compressor_running and sample.direction > 0
        if prev is not None and prev.compressor_running and prev.direction > 0 and not wet_now and self.humidity:
            self.humidity.last_wet_stop = sample.time
        e = room_e(sample.reading, sample.reading_rh)
        if self.humidity is None and e is not None:
            self.humidity = HumidityFilter.start(e)
        if self.humidity is None:
            return
        self.humidity.observe_e(e)
        e_out = room_e(sample.outdoor, sample.outdoor_rh)
        if e_out is None or e is None or not sample.compressor_known:
            self._moisture_trusted = False
        u = self.heat.u if self.heat is not None else 0.0
        self._moisture.append(
            MoistureMinute(
                outdoor_e=e_out if e_out is not None else self.humidity.z[0],
                dry=wet_now and sample.dry,
                cooling_rate=max(u, 0.0) if wet_now and not sample.dry else 0.0,
                rebound=in_rebound(self.humidity.last_wet_stop, sample.time),
            )
        )

    def five_minutes(self, now: datetime) -> FilterStep | None:
        """Every 5 minutes: both filters predict and correct."""
        result = self.heat.five_minutes(now) if self.heat is not None else None
        if result is not None:
            self.last_step = result
        if self.humidity is not None:
            last = self._last_minute
            e = room_e(last.reading, last.reading_rh) if last is not None else None
            self.last_humidity_reason = self.humidity.five_minutes(self._moisture[-5:], e, self._moisture_trusted)
        self._moisture = []
        self._moisture_trusted = True
        return result

    def hour(self, now: datetime, room_now: float | None, rest_inputs: Sequence[ForecastInput], e_now: float) -> None:
        """Every hour: the trust check."""
        if self.heat is None:
            return
        steps = [s for s in self.heat.recent if s.time > now - timedelta(hours=1)]
        # resting only when every step's compressor was known and stopped: an
        # hour cooling unseen is not scored as a rest forecast [Prop]
        resting = len(steps) >= 12 and all(s.compressor_known and not s.compressor_ran for s in steps)
        ahead = forecast(self.heat, self.humidity, rest_inputs[:12], e_now=e_now)
        next_hour = ahead[-1].t if len(ahead) == 12 else None
        six_error: float | None = None
        then = next(((t, z) for t, z in self._hourly_z if abs((now - t).total_seconds() - 6 * 3600) < 900), None)
        if then is not None and room_now is not None:
            since = [s.inputs for s in self.heat.recent if s.inputs is not None and then[0] < s.time <= now]
            if len(since) >= 70:
                six_error = abs(room_now - replay(then[1], since))
        self.trust.hourly(now, room_now, next_hour, resting, six_error)
        self._hourly_z.append((now, list(self.heat.z)))

    def end_of_day(self, day: str) -> None:
        """At midnight."""
        self.trust.end_of_day(day)

    def answers(self, outdoor: float | None, sun: float, hour: float) -> ModelAnswers:
        """What the model says now."""
        if self.heat is None:
            return ModelAnswers(learning=False)
        fig = figures(self.heat.z)
        values = (fig.a, fig.b, fig.c, fig.k, fig.s, fig.m)
        figs = dict(zip(_FIGURE_NAMES, values, strict=True))
        figs.update(g0=fig.g0, al1=fig.al1, be1=fig.be1, al2=fig.al2, be2=fig.be2)
        unc = {"T": self.heat.p[I_T][I_T], "M": self.heat.p[I_M][I_M]}
        unc.update({n: self.heat.p[2 + i][2 + i] for i, n in enumerate(_FIGURE_NAMES)})
        unc.update(delay=self.heat.p[I_DELAY][I_DELAY], ln_lag=self.heat.p[I_LN_LAG][I_LN_LAG])
        return ModelAnswers(
            learning=True,
            t=self.heat.z[I_T],
            mass=self.heat.z[I_M],
            e=self.humidity.z[0] if self.humidity is not None else None,
            drift=drift_now(self.heat, outdoor, sun, hour) if outdoor is not None else None,
            figures=figs,
            uncertainty={k: math.sqrt(max(v, 0.0)) for k, v in unc.items()},
            sensor_noise=math.sqrt(self.heat.noise) if self.heat.noise is not None else None,
            corrections=self.heat.corrections,
            last_reason=self.last_step.reason if self.last_step is not None else None,
            trusted=self.trust.trusted,
            six_hour_trusted=self.trust.six_hour_trusted,
            one_hour_error=self.trust.one_hour_error,
            delay_min=fig.delay_min,
            lag_min=fig.lag_min,
            u_min=self.unit.lowest_rate(fig, 1),
            top_cool=top_rate(fig, 1),
            top_heat=top_rate(fig, -1),
            last_correction=self.heat.last_correction,
        )
