"""A simulated room for the model's tests (specification Section 11.1).

"Two-store" has exactly the structure of Section 4.2, with true figures
a = 0.10, b = 0.50, c = 0.15, k = 0.05, s = 0.80, m = 0.30, and heat from
people of 0.15 C/h by day and 0.60 C/h from 17:00 to 23:00. Weather: a
daily mean on a random walk between 16 and 32 C with a daily swing of 2-8
C; sun on the glass 07:00-15:00, scaled by a daily cloud factor. A plain
thermostat cools at 3 C/h above 25.0 C and stops below 23.5 C, 07:00-23:00,
through a 6-minute delay and a 15-minute lag. The sensor reports in
0.2 C steps every minute. It is a test fixture, not the house.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load

models = load("models")
VT = models.ValueTag

TRUE = {"a": 0.10, "b": 0.50, "c": 0.15, "k": 0.05, "s": 0.80, "m": 0.30}
START = datetime(2026, 1, 1, tzinfo=UTC)


def _reading(v: float | None, tag: Any = None) -> Any:
    return models.Reading(v, tag or (VT.MEASURED if v is not None else VT.MISSING))


@dataclass
class SimRoom:
    seed: int = 1
    sensor_step: float = 0.2
    noise_sd: float = 0.0
    rate: float = 3.0
    effort_when_running: float = 2.0
    t: float = 24.0
    mass: float = 24.0
    u: float = 0.0
    running: bool = False
    minute: int = 0
    rng: random.Random = field(init=False)
    _mean: float = 24.0
    _swing: float = 5.0
    _cloud: float = 1.0
    _cmd: deque[bool] = field(default_factory=lambda: deque([False] * 6, maxlen=6))
    # external control: the effort the unit is asked for (C of its reading
    # against its setpoint), its direction (+1 cool, -1 heat, 0 rest); the
    # true unit delivers 2 C/h per C of effort, from 1 C/h up to 6 C/h
    external: bool = False
    command_effort: float = 0.0
    command_direction: int = 0
    _ext: deque[float] = field(default_factory=lambda: deque([0.0] * 6, maxlen=6))

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @property
    def now(self) -> datetime:
        return START + timedelta(minutes=self.minute)

    @property
    def hour(self) -> float:
        return (self.minute % 1440) / 60.0

    def outdoor(self) -> float:
        return self._mean + self._swing / 2 * math.sin(2 * math.pi * (self.hour - 9.0) / 24.0)

    def sun(self) -> float:
        return self._cloud if 7.0 <= self.hour < 15.0 else 0.0

    def g(self) -> float:
        return 0.60 if 17.0 <= self.hour < 23.0 else 0.15

    def advance(self) -> None:
        """One minute of the true room."""
        if self.minute % 1440 == 0:
            self._mean = min(32.0, max(16.0, self._mean + self.rng.gauss(0, 1.5)))
            self._swing = self.rng.uniform(2, 8)
            self._cloud = self.rng.uniform(0.3, 1.0)
        if self.external:
            self.running = self.command_direction != 0
            # the true unit: 2 C/h per C of effort, at least 1 C/h while it runs, at most 6
            rate = min(max(2.0 * max(self.command_effort, 0.0), 1.0), 6.0) if self.command_direction else 0.0
            self._ext.append(self.command_direction * rate)
            target = self._ext[0]  # 6-minute delay
            self._cmd.append(self.running)
        else:
            day = 7.0 <= self.hour < 23.0
            if day and self.t > 25.0:
                self.running = True
            elif not day or self.t < 23.5:
                self.running = False
            self._cmd.append(self.running)
            target = self.rate if self._cmd[0] else 0.0  # 6-minute delay
        self.u += (target - self.u) / 15.0  # 15-minute lag
        out, s = self.outdoor(), self.sun()
        dt = TRUE["a"] * (out - self.t) + TRUE["b"] * (self.mass - self.t) + TRUE["s"] * s + self.g() - self.u
        dm = TRUE["c"] * (self.t - self.mass) + TRUE["k"] * (out - self.mass) + TRUE["m"] * s
        self.t += dt / 60.0 + self.rng.gauss(0, 0.002)
        self.mass += dm / 60.0
        self.minute += 1

    def sensor(self) -> float:
        v = self.t + (self.rng.gauss(0, self.noise_sd) if self.noise_sd else 0.0)
        return round(v / self.sensor_step) * self.sensor_step

    def snapshot(self) -> Any:
        if self.external:
            mode = {1: "cool", -1: "heat"}.get(self.command_direction, "fan_only")
            own = self.t
            setpoint = own - self.command_direction * self.command_effort if self.command_direction else None
        else:
            mode = "cool" if self.running else "fan_only"
            own = 24.0
            setpoint = 24.0 - self.effort_when_running if self.running else None
        comp = models.CompressorState.RUNNING if self._cmd[-1] else models.CompressorState.STOPPED
        head = models.HeadSnapshot(
            "climate.sim", "own:climate.sim", True, mode, setpoint, None, own, 50.0, "auto", None, None,
            models.Reading(None, VT.MISSING), comp, models.CompressorSource.HVAC_ACTION,
        )
        sun = models.SunSnapshot(self.sun() > 0, "geometry", 1.0, 1.0, self.sun(), VT.MEASURED)
        return models.Snapshot(
            room_id="sim", time=self.now, room_temperature=_reading(self.sensor()),
            room_humidity=_reading(55.0), heads=(head,), sun=sun,
            outdoor_temperature=_reading(self.outdoor()), outdoor_humidity=_reading(60.0),
            outdoor_wind_ms=_reading(0.0), forecast=(), presence=None, leave_pressed_at=None,
            precool_ready_by=None, openings=(), blind_positions=(),
            sleep_schedule=None, house_load_kw=_reading(None), solar_kw=_reading(None),
            battery_charge_pct=_reading(None), grid_kw=_reading(None), tariff_now=None, tariff_ahead=(), stale=(),
        )
