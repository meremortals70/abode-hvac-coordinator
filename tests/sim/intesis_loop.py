"""Closed loop: the component's plan, PID and whole-degree delivery driving
a simulated room through a unit that behaves as the house's Intesis units
do: whole-degree reading and setpoint, its own thermostat (start at 1 C
above setpoint, stop at 1 C below), the component sees the compressor only
as the build under test lets it. Counts compressor starts a day.

Run from the repository root:

    py -3 tests/sim/intesis_loop.py . unknown 3
    py -3 tests/sim/intesis_loop.py . known 3

The first argument is the tree to test (so two trees can be compared); the
second is ``known`` when the compressor state is reported directly and
``unknown`` when the component must infer it (D101); the third is the number
of simulated days. tests/sim/test_intesis_loop.py uses run() as a regression.
"""

from __future__ import annotations

import dataclasses
import importlib
import math
import sys
from pathlib import Path
from typing import Any


def _modules(repo: str) -> Any:
    """The test-loop module from the tree under test."""
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return importlib.import_module("tests.sim.test_loop")


def run(days_n: int = 3, known: bool = False, repo: str | None = None) -> list[dict[str, float]]:
    """Drive the whole-degree room for days_n days; one dict a day."""
    tl = _modules(repo or str(Path(__file__).resolve().parents[2]))
    from tests.pure.sim_room import SimRoom

    delivery = tl.load("delivery")
    M = tl.models
    CS, SRC = M.CompressorState, M.CompressorSource
    RH = 60.0
    sim = SimRoom(seed=3, external=True)
    band = (tl.hci.comfort_index(22.5, RH), tl.hci.comfort_index(24.5, RH))
    room = M.RoomConfig("sim", "Sim", (M.HeadConfig("climate.sim"),), "s.t", "s.rh", band, band, band)
    rm, rc, ctl = tl.model.RoomModel(), tl.comfort.RoomComfort(room), tl.control.RoomControl()
    prof = M.CapabilityProfile(
        hvac_modes=("cool", "heat", "dry", "fan_only", "off"), fan_modes=("auto",),
        vertical_vanes=(), horizontal_vanes=(), min_setpoint=18.0, max_setpoint=30.0,
        setpoint_step=1.0, range_only=False, step_reason=None, takes_range=False,
    ) if hasattr(M, "CapabilityProfile") else None
    alt = delivery._Alternation(sim.now)
    fields = {f.name for f in dataclasses.fields(tl.plan.PlanInputs)}
    comp_on, mode, sent = False, "fan_only", None
    since, last_way = {}, 0
    out, day = [], {"n": 0, "inside": 0, "starts": 0, "heat": 0, "modechanges": 0}
    for _ in range(days_n * 1440):
        sim.advance()
        own = float(round(sim.t))
        snap = sim.snapshot()
        h = snap.heads[0]
        comp_state = (CS.RUNNING if comp_on else CS.STOPPED) if (known or mode in ("off", "fan_only")) else CS.UNKNOWN
        h = dataclasses.replace(
            h, mode=mode, setpoint=sent, own_temperature=own, compressor=comp_state,
            compressor_source=SRC.LOAD_STEP if comp_state is not CS.UNKNOWN and mode not in ("off", "fan_only")
            else (SRC.MODE if mode in ("off", "fan_only") else SRC.UNKNOWN),
        )
        snap = dataclasses.replace(snap, heads=(h,))
        rm.minute(snap, sim.hour)
        if sim.minute % 5 == 0 and rm.heat is not None:
            rm.five_minutes(sim.now)
            bands = rc.evaluate(snap, M.RoomChoices(), sim.now).points
            conds = []
            for i in range(1, 73):
                hr = ((sim.minute + 5 * i) % 1440) / 60
                out_t = sim._mean + sim._swing / 2 * math.sin(2 * math.pi * (hr - 9) / 24)
                conds.append(tl.plan.Conditions(sim.now + tl.timedelta(minutes=5 * i), hr, out_t, 60.0,
                                                7 <= hr < 15, sim._cloud))
            kw: dict[str, Any] = {
                "now": sim.now, "heat": rm.heat, "humidity": rm.humidity,
                "e_now": tl.hci.vapour_pressure(23.0, RH), "unit": rm.unit, "bands": bands,
                "conditions": conds, "prices": [1.0] * 12, "p_max": 1.0,
                "draw_for_rate": lambda r: 0.45, "modes": frozenset({"cool", "heat", "dry", "fan_only"}),
                "blind_control": False, "transmission_now": 1.0,
                "compressor_running": mode in ("cool", "heat", "dry"), "running_since": since.get("run"),
                "stopped_since": since.get("stop"),
                "last_kind": ctl.plan.first.kind if ctl.plan else M.StepKind.REST,
            }
            if "last_way" in fields:
                kw["last_way"] = last_way
            fig = tl.physics.figures(rm.heat.z)
            ctl.replan(tl.plan.PlanInputs(**kw),
                       tl.pid.gains(fig, rm.heat.delay_min, rm.heat.lag_min, tl.pid.actuation_minutes(1.0)))
        new_mode = mode
        if ctl.plan is not None:
            fig = tl.physics.figures(rm.heat.z)
            slope = tl.forecast.drift_now(rm.heat, sim.outdoor(), sim.sun(), sim.hour) - rm.heat.u
            top = tl.unit.top_rate(fig, 1 if ctl.plan.first.rate >= 0 else -1)
            res = ctl.cycle(now=sim.now, t_est=rm.heat.z[0], slope_est=slope,
                            u_min=rm.unit.lowest_rate(fig, 1), top=top, delivered=True)
            if res.kind in (M.StepKind.COOL, M.StepKind.HEAT):
                d = 1 if res.kind is M.StepKind.COOL else -1
                wanted = own - d * tl.unit.effort_for_rate(fig, res.rate)
                sent = delivery.whole_degree_value(wanted, 1.0, prof, sim.now, alt)
                new_mode = "cool" if d > 0 else "heat"
            elif res.kind is M.StepKind.DRY:
                new_mode = "dry"
            else:
                new_mode = "fan_only"
                alt = (delivery._Alternation(sim.now)
                       if "last" in [f.name for f in dataclasses.fields(delivery._Alternation)] else alt)
        if new_mode != mode:
            day["modechanges"] += 1
            if new_mode in ("cool", "heat", "dry"):
                since = {"run": sim.now}
            else:
                since = {"stop": sim.now}
        mode = new_mode
        # the unit's own thermostat, on its whole-degree reading
        d = {"cool": 1, "dry": 1, "heat": -1}.get(mode, 0)
        if d == 0 or sent is None:
            want_on = False
        else:
            effort_u = d * (own - sent)
            want_on = effort_u >= 1 if not comp_on else effort_u > -1
            if mode == "dry":
                want_on = want_on and effort_u >= 1
        if want_on and not comp_on:
            day["starts"] += 1
        if comp_on and d != 0:
            last_way = d
        comp_on = want_on
        sim.command_direction = d if comp_on else 0
        sim.command_effort = max(d * (own - sent), 0.5) if comp_on else 0.0
        felt = tl.hci.comfort_index(sim.t, RH)
        day["n"] += 1
        day["inside"] += band[0] <= felt <= band[1]
        day["heat"] += mode == "heat"
        if sim.minute % 1440 == 0:
            out.append(day)
            day = {"n": 0, "inside": 0, "starts": 0, "heat": 0, "modechanges": 0}
    return out


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
    known = len(sys.argv) > 2 and sys.argv[2] == "known"
    days_n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    for dd in run(days_n, known, repo):
        print(("known" if known else "unknown"), "compressor starts", dd["starts"], "mode changes", dd["modechanges"],
              "inside", round(dd["inside"] / dd["n"], 3), "heat minutes", dd["heat"])


if __name__ == "__main__":
    main()
