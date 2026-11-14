"""The whole loop from day one on the simulated room (cf. specification 11.4).

The filter learns the room from its first reading; every 5 minutes Comfort
gives the band and the plan is made on the model as it stands; every minute
the PID holds the room on the plan's path, and the rate is sent through the
learned curve as an effort. The room is tests/pure/sim_room.py, run under
external control: a unit with a 6-minute delay, a 15-minute lag, and 2 C/h
per C of effort from 1 up to 6 C/h. About 25 seconds per simulated day, so
this runs on its own: python3 -m unittest discover -s tests/sim -t .
"""

from __future__ import annotations

import math
import unittest
from datetime import timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

model = load("model")
forecast = load("model.forecast")
physics = load("model.physics")
unit = load("model.unit")
hci = load("hci")
models = load("models")
control = load("control")
plan = load("control.plan")
pid = load("control.pid")
comfort = load("comfort")

RH = 55.0  # the simulated room's sensor reads 55 % throughout


def run(days: int, seed: int = 3) -> list[dict[str, float]]:
    sim = SimRoom(seed=seed, external=True)
    band = (hci.comfort_index(21.5, RH), hci.comfort_index(24.5, RH))
    room = models.RoomConfig("sim", "Sim", (models.HeadConfig("climate.sim"),), "s.t", "s.rh", band, band, band)
    rm, rc, ctl = model.RoomModel(), comfort.RoomComfort(room), control.RoomControl()
    since: dict[str, Any] = {}
    out: list[dict[str, float]] = []
    day: dict[str, float] = {"n": 0, "inside": 0, "starts": 0, "heating": 0}
    running = False
    for _ in range(days * 1440):
        sim.advance()
        snap = sim.snapshot()
        rm.minute(snap, sim.hour)
        if sim.minute % 5 == 0 and rm.heat is not None:
            rm.five_minutes(sim.now)
            bands = rc.evaluate(snap, models.RoomChoices(), sim.now).points
            conds = []
            for i in range(1, 73):
                h = ((sim.minute + 5 * i) % 1440) / 60
                out_t = sim._mean + sim._swing / 2 * math.sin(2 * math.pi * (h - 9) / 24)
                conds.append(plan.Conditions(sim.now + timedelta(minutes=5 * i), h, out_t, 60.0, 7 <= h < 15,
                                             sim._cloud))
            inp = plan.PlanInputs(
                now=sim.now, heat=rm.heat, humidity=rm.humidity, e_now=hci.vapour_pressure(23.0, RH), unit=rm.unit,
                bands=bands, conditions=conds, prices=[1.0] * 12, p_max=1.0, draw_for_rate=lambda r: 0.3 * abs(r),  # a draw is never negative; the sign picks the curve
                modes=frozenset({"cool", "heat", "dry", "fan_only"}), blind_control=False, transmission_now=1.0,
                compressor_running=running,
                running_since=since.get("run"), stopped_since=since.get("stop"),
                last_kind=ctl.plan.first.kind if ctl.plan else models.StepKind.REST,
            )
            fig = physics.figures(rm.heat.z)
            ctl.replan(inp, pid.gains(fig, rm.heat.delay_min, rm.heat.lag_min, pid.actuation_minutes(0.1)))
        if ctl.plan is not None:
            fig = physics.figures(rm.heat.z)
            slope = forecast.drift_now(rm.heat, sim.outdoor(), sim.sun(), sim.hour) - rm.heat.u
            top = unit.top_rate(fig, 1 if ctl.plan.first.rate >= 0 else -1)
            res = ctl.cycle(now=sim.now, t_est=rm.heat.z[0], slope_est=slope, u_min=rm.unit.lowest_rate(fig, 1),
                            top=top, delivered=True)
            if res.kind in (models.StepKind.COOL, models.StepKind.HEAT, models.StepKind.DRY):
                sim.command_direction = 1 if res.rate >= 0 else -1
                sim.command_kind = res.kind.value  # the plant knows dry from cool [D109]
                # a dry rate is read on dry's own curve, as delivery does [D109]
                sim.command_effort = unit.effort_for_rate(
                    fig, res.rate, dry=res.kind is models.StepKind.DRY)
            else:
                sim.command_direction, sim.command_effort = 0, 0.0
        now_running = sim.command_direction != 0
        if now_running and not running:
            day["starts"] += 1
            since = {"run": sim.now}
        elif running and not now_running:
            since = {"stop": sim.now}
        running = now_running
        felt = hci.comfort_index(sim.t, RH)
        day["n"] += 1
        day["inside"] += band[0] <= felt <= band[1]
        day["heating"] += sim.command_direction < 0
        if sim.minute % 1440 == 0:
            out.append(day)
            day = {"n": 0, "inside": 0, "starts": 0, "heating": 0}
    return out


class WholeLoop(unittest.TestCase):
    def test_comfortable_with_few_starts_from_day_one(self) -> None:
        days = run(3)
        for d in days:
            self.assertGreaterEqual(d["inside"] / d["n"], 0.99)
            self.assertLessEqual(d["heating"] / d["n"], 0.02)
        for d in days[1:]:
            self.assertLessEqual(d["starts"], 3)


if __name__ == "__main__":
    unittest.main()
