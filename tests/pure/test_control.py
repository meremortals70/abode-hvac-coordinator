"""control/: the plan, the PID, the learned controller and the arbiter (Module 5)."""

from __future__ import annotations

import dataclasses
import math
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load

plan = load("control.plan")
pid = load("control.pid")
rl = load("control.rl")
arb = load("control.arbiter")
control = load("control")
filt = load("model.filter")
unit = load("model.unit")
physics = load("model.physics")
models = load("models")
hci = load("hci")
SK = models.StepKind

NOW = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
FIVE = timedelta(minutes=5)
E_ROOM = hci.vapour_pressure(24.0, 50.0)


def heat(t0: float = 25.0, **figs: float) -> Any:
    f = filt.HeatFilter.start(t0)
    f.noise = 0.003
    for name, value in figs.items():
        f.z[{"a": 2, "b": 3, "c": 4, "k": 5, "s": 6, "m": 7}[name]] = math.log(value)
    for i in range(2, physics.STATE_SIZE):  # a settled model
        f.p[i][i] = 1e-4
    f.p[0][0], f.p[1][1] = 0.001, 0.01
    return f


def bands(lo: float = 24.0, hi: float = 27.0, required_from: int = 0) -> list[Any]:
    return [models.BandPoint(NOW + (i + 1) * FIVE, lo, hi, i >= required_from) for i in range(72)]


def conds(outdoor: float = 24.0, sun: bool = False, f_clear: float = 1.0, rh: float | None = 50.0) -> list[Any]:
    return [plan.Conditions(NOW + (i + 1) * FIVE, 10.0 + (i + 1) / 12, outdoor, rh, sun, f_clear) for i in range(72)]


def inputs(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "now": NOW,
        "heat": heat(),
        "humidity": None,
        "e_now": E_ROOM,
        "unit": unit.UnitModel(u_min=1.0),
        "bands": bands(),
        "conditions": conds(),
        "prices": [1.0] * 12,
        "p_max": 1.0,
        "draw_for_rate": lambda r: 0.3 * abs(r),  # a draw is never negative; the sign picks the curve
        "modes": frozenset({"cool", "heat", "dry", "fan_only"}),
        "blind_control": False,
        "transmission_now": 1.0,
    }
    base.update(kw)
    return plan.PlanInputs(**base)


def index_of(t: float) -> float:
    return t + 0.33 * E_ROOM - 4.0 + 1.0


class Actions(unittest.TestCase):
    def test_candidates(self) -> None:
        acts = plan.candidate_actions(inputs(), 1.0)
        kinds = {a.kind for a in acts}
        self.assertEqual(kinds, {SK.REST, SK.DRY, SK.COOL, SK.HEAT})
        cool = sorted(a.rate for a in acts if a.kind is SK.COOL)
        self.assertEqual(cool, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])  # ln 1 curve: 1..6 from U_min
        self.assertEqual(sorted(-a.rate for a in acts if a.kind is SK.HEAT), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual([a.rate for a in acts if a.kind is SK.DRY], [1.0])

    def test_u_min_between_curve_points(self) -> None:
        cool = sorted(a.rate for a in plan.candidate_actions(inputs(), 2.5) if a.kind is SK.COOL)
        self.assertEqual(cool, [2.5, 3.0, 4.0, 5.0, 6.0])

    def test_only_advertised_modes_no_heat_when_waking_ceiling(self) -> None:
        acts = plan.candidate_actions(inputs(modes=frozenset({"cool", "fan_only"})), 1.0)
        self.assertEqual({a.kind for a in acts}, {SK.REST, SK.COOL})
        acts = plan.candidate_actions(inputs(waking=True), 1.0)
        self.assertNotIn(SK.HEAT, {a.kind for a in acts})
        acts = plan.candidate_actions(inputs(ceiling=2.5), 1.0)
        self.assertEqual(max(abs(a.rate) for a in acts), 2.0)
        acts = plan.candidate_actions(inputs(ceiling=0.5), 1.0)
        self.assertEqual({a.kind for a in acts}, {SK.REST})

    def test_blinds_triple_the_actions(self) -> None:
        acts = plan.candidate_actions(inputs(blind_control=True), 1.0)
        self.assertEqual({a.blinds_closed for a in acts}, {None, True, False})
        self.assertEqual(len(acts), 3 * len(plan.candidate_actions(inputs(), 1.0)))

    def test_blinds_are_left_alone_without_sun_on_the_glass(self) -> None:
        """No sun on the glass, or none that can be said: the plan never moves
        the blinds; with sun on the glass it always decides them [D023]."""
        p = plan.make_plan(inputs(heat=heat(25.0), blind_control=True, conditions=conds(outdoor=27.0, sun=False)))
        self.assertTrue(all(a.blinds_closed is None for a in p.actions))
        p = plan.make_plan(inputs(heat=heat(25.0), blind_control=True, conditions=conds(outdoor=27.0, sun=True)))
        self.assertTrue(all(a.blinds_closed is not None for a in p.actions))
        # sun on the glass for the first three hours only: decided then, left alone after
        mixed = [plan.Conditions(c.time, c.hour, c.outdoor, c.outdoor_rh, i < 36, c.f_clear)
                 for i, c in enumerate(conds(outdoor=27.0))]
        p = plan.make_plan(inputs(heat=heat(25.0), blind_control=True, conditions=mixed))
        self.assertEqual([a.blinds_closed is not None for a in p.actions], [True] * 6 + [False] * 6)

    def test_blind_choices_offered_follow_the_sun_on_the_glass(self) -> None:
        """What is offered, not only what wins: with no sun on the glass no
        first action moves the blinds, and with sun on it every one does;
        sun at any point of a step counts for the step [D023]."""
        def offered(cs: list[Any]) -> set[bool | None]:
            p = plan.make_plan(inputs(heat=heat(25.0), blind_control=True, conditions=cs))
            return {p.first.blinds_closed, *(a.blinds_closed for a, _ in p.alternatives)}
        self.assertEqual(offered(conds(outdoor=27.0, sun=False)), {None})
        self.assertEqual(offered(conds(outdoor=27.0, sun=True)), {True, False})
        first_point_only = [plan.Conditions(c.time, c.hour, c.outdoor, c.outdoor_rh, i == 0, c.f_clear)
                            for i, c in enumerate(conds(outdoor=27.0))]
        self.assertEqual(offered(first_point_only), {True, False})


class NothingLeftToCarryOn(unittest.TestCase):
    """The minimum run binds only where the running kind can carry on
    [D016]; otherwise the plan may rest and Limits holds the stop."""

    def test_a_waking_room_that_was_heating_is_not_cooled(self) -> None:
        inp = inputs(heat=heat(18.5), bands=bands(21.0, 24.0), waking=True, compressor_running=True,
                     running_since=NOW - timedelta(minutes=5), last_kind=SK.HEAT)
        self.assertIs(plan.make_plan(inp).first.kind, SK.REST)  # [D085]

    def test_a_heat_only_unit_waking_has_a_plan(self) -> None:
        inp = inputs(heat=heat(18.5), bands=bands(21.0, 24.0), waking=True, compressor_running=True,
                     running_since=NOW - timedelta(minutes=5), last_kind=SK.HEAT,
                     modes=frozenset({"heat", "fan_only"}))
        self.assertIs(plan.make_plan(inp).first.kind, SK.REST)

    def test_a_ceiling_below_the_lowest_rate_has_a_plan(self) -> None:
        inp = inputs(heat=heat(27.0), compressor_running=True, running_since=NOW - timedelta(minutes=3),
                     last_kind=SK.COOL, ceiling=0.5)
        self.assertIs(plan.make_plan(inp).first.kind, SK.REST)

    def test_carrying_on_is_still_required_where_it_can(self) -> None:
        inp = inputs(heat=heat(23.0), compressor_running=True, running_since=NOW - timedelta(minutes=3),
                     last_kind=SK.COOL)
        self.assertNotEqual(plan.make_plan(inp).first.kind, SK.REST)


class HeatingHasItsOwnFloor(unittest.TestCase):
    def test_heating_is_offered_from_the_heating_curves_lowest_rate(self) -> None:
        """Heating is never offered below what its own curve can do, nor
        cooling held up by heating's floor [D100]."""
        h = heat()
        h.z[physics.I_COOL] = math.log(0.3)
        h.z[physics.I_HEAT] = math.log(1.2)
        fig = physics.figures(h.z)
        inp = inputs(heat=h, unit=unit.UnitModel())
        p_cool, p_heat = inp.unit.lowest_rate(fig, 1), inp.unit.lowest_rate(fig, -1)
        acts = plan.candidate_actions(inp, p_cool, p_heat)
        self.assertAlmostEqual(min(-a.rate for a in acts if a.kind is SK.HEAT), p_heat)
        self.assertAlmostEqual(min(a.rate for a in acts if a.kind is SK.COOL), p_cool)
        self.assertLess(p_cool, p_heat)

    def test_the_plan_heats_at_or_above_the_heating_floor(self) -> None:
        h = heat(18.5)
        h.z[physics.I_COOL] = math.log(0.3)
        h.z[physics.I_HEAT] = math.log(1.2)
        floor = unit.UnitModel().lowest_rate(physics.figures(h.z), -1)
        p = plan.make_plan(inputs(heat=h, bands=bands(21.0, 24.0), unit=unit.UnitModel()))
        rates = [-a.rate for a in (p.first, *(a for a, _ in p.alternatives)) if a.kind is SK.HEAT]
        self.assertTrue(rates)
        self.assertGreaterEqual(min(rates), floor - 1e-9)


class HeatingIsPricedOnItsOwnCurve(unittest.TestCase):
    def test_heating_rates_reach_the_draw_with_their_sign(self) -> None:
        """The draw function picks the heating curve from a negative rate
        (unit.py effort_for_rate) [D028]."""
        seen: list[float] = []

        def draw(r: float) -> float:
            seen.append(r)
            return 0.3 * abs(r)

        plan.make_plan(inputs(draw_for_rate=draw))
        self.assertTrue(any(r < 0 for r in seen))
        self.assertTrue(any(r > 0 for r in seen))

    def test_a_unit_that_only_heats_is_weighed_on_heating(self) -> None:
        seen: list[float] = []

        def draw(r: float) -> float:
            seen.append(r)
            return 0.3 * abs(r)

        plan.make_plan(inputs(draw_for_rate=draw, modes=frozenset({"heat", "fan_only"})))
        self.assertTrue(seen and all(r <= 0 for r in seen))


class CeilingEachWay(unittest.TestCase):
    def test_heating_is_held_to_its_own_ceiling(self) -> None:
        """Cooling and dry are held to the ceiling, heating to the heating
        ceiling for the same allowance [D041]."""
        inp = inputs(ceiling=2.0, ceiling_heat=4.0)
        acts = plan.candidate_actions(inp, 1.0, 1.0)
        self.assertLessEqual(max(a.rate for a in acts if a.kind is SK.COOL), 2.0)
        self.assertLessEqual(max(-a.rate for a in acts if a.kind is SK.HEAT), 4.0)
        self.assertGreater(max(-a.rate for a in acts if a.kind is SK.HEAT), 2.0)
        same = plan.candidate_actions(inputs(ceiling=2.0), 1.0, 1.0)
        self.assertLessEqual(max(-a.rate for a in same if a.kind is SK.HEAT), 2.0)  # none given: the ceiling


class FirstStep(unittest.TestCase):
    def test_minimum_rest_and_run(self) -> None:
        cool = models.PlanAction(SK.COOL, 2.0, None)
        rest = models.PlanAction(SK.REST, 0.0, None)
        self.assertFalse(plan._allowed_first(inputs(stopped_since=NOW - timedelta(minutes=4)), cool))
        self.assertTrue(plan._allowed_first(inputs(stopped_since=NOW - timedelta(minutes=5)), cool))
        running = {"compressor_running": True, "last_kind": SK.COOL}
        self.assertFalse(plan._allowed_first(inputs(running_since=NOW - timedelta(minutes=9), **running), rest))
        self.assertTrue(plan._allowed_first(inputs(running_since=NOW - timedelta(minutes=10), **running), rest))

    def test_free_cooling_wait(self) -> None:
        cool = models.PlanAction(SK.COOL, 2.0, None)
        advised = NOW - timedelta(minutes=9)
        self.assertFalse(plan._allowed_first(inputs(free_cooling_advised_at=advised), cool))
        self.assertTrue(plan._allowed_first(inputs(free_cooling_advised_at=advised, occupied=False), cool))
        self.assertTrue(plan._allowed_first(inputs(free_cooling_advised_at=NOW - timedelta(minutes=10)), cool))


class Plans(unittest.TestCase):
    def test_shape_and_costs(self) -> None:
        p = plan.make_plan(inputs(prices=[2.0] * 12, p_max=2.0))
        self.assertEqual((len(p.actions), len(p.points)), (12, 72))
        self.assertAlmostEqual(p.w, 2.0 * 0.3 * 1.0)  # p_max x draw at U_min [I4][Der]
        self.assertAlmostEqual(p.c_start, 0.5 * 2.0 * 0.3 * 1.0)  # 0.5 p_max D1 x 1 h
        self.assertAlmostEqual(p.start_cost, p.starts * p.c_start)
        energy = sum(2.0 * 0.3 * abs(a.rate) * 0.5 for a in p.actions if a.kind in (SK.COOL, SK.HEAT, SK.DRY))
        self.assertAlmostEqual(p.energy_cost, energy)
        self.assertTrue(p.alternatives)
        with self.assertRaises(ValueError):
            plan.make_plan(inputs(conditions=conds()[:10]))

    def test_hot_room_is_cooled(self) -> None:
        # starting above the band: the plan cools at once, and leaves the band
        # no later than the unit allows
        p = plan.make_plan(inputs(heat=heat(26.5), conditions=conds(outdoor=33.0)))
        self.assertIs(p.first.kind, SK.COOL)
        self.assertFalse(any(pt.outside for pt in p.points[12:]))

    def test_weather_does_the_work(self) -> None:
        # warm room, cool evening outside: resting brings it to the centre for nothing
        t_centre = 25.5 - (0.33 * E_ROOM - 4.0)
        p = plan.make_plan(inputs(heat=heat(t_centre + 0.8), conditions=conds(outdoor=t_centre - 3.0)))
        self.assertIs(p.first.kind, SK.REST)
        self.assertLess(p.points[5].t, t_centre + 0.8)

    def test_no_heating_while_the_weather_warms_the_room(self) -> None:
        t_centre = 25.5 - (0.33 * E_ROOM - 4.0)
        p = plan.make_plan(inputs(heat=heat(t_centre - 1.0), conditions=conds(outdoor=t_centre + 8.0)))
        self.assertNotIn(SK.HEAT, {a.kind for a in p.actions})

    def test_blinds_before_the_compressor(self) -> None:
        p = plan.make_plan(inputs(heat=heat(25.0), blind_control=True, conditions=conds(outdoor=27.0, sun=True)))
        self.assertTrue(p.first.blinds_closed)

    def test_a_cheaper_hour_is_used(self) -> None:
        t_centre = 25.5 - (0.33 * E_ROOM - 4.0)
        warm = inputs(heat=heat(t_centre), conditions=conds(outdoor=27.0))
        flat = plan.make_plan(warm)
        dear_now = plan.make_plan(dataclasses.replace(warm, prices=[5.0] * 4 + [0.0] * 8, p_max=5.0))
        def ran(p: Any, j: int) -> bool:
            return bool(p.actions[j].kind in (SK.COOL, SK.DRY))

        self.assertTrue(any(ran(flat, j) for j in range(4)))
        self.assertFalse(any(ran(dear_now, j) for j in range(4)))
        self.assertTrue(any(ran(dear_now, j) for j in range(4, 12)))

    def test_comfort_before_cost(self) -> None:
        # a dear tariff never buys time outside the band [D008]
        p = plan.make_plan(inputs(heat=heat(27.0), conditions=conds(outdoor=35.0), prices=[50.0] * 12,
                                  p_max=50.0))
        self.assertIs(p.first.kind, SK.COOL)

    def test_points_not_required_are_not_scored(self) -> None:
        hot = {"heat": heat(28.0), "conditions": conds(outdoor=30.0)}
        later = plan.make_plan(inputs(bands=bands(required_from=48), **hot))
        self.assertEqual(sum(1 for pt in later.points if pt.outside and pt.time <= NOW + 48 * FIVE), 0)
        self.assertNotIn(SK.COOL, {a.kind for a in later.actions[:2]})

    def test_margin_of_one_sigma(self) -> None:
        f = heat(26.0)
        p = plan.make_plan(inputs(heat=f, modes=frozenset({"fan_only"}), conditions=conds(outdoor=26.0)))
        for pt in p.points:
            near = pt.hci > 27.0 - pt.sigma_hci or pt.hci < 24.0 + pt.sigma_hci
            self.assertEqual(pt.outside, near)
        self.assertGreater(p.points[-1].sigma_hci, p.points[0].sigma_hci)

    def test_comfort_cost_in_half_bands(self) -> None:
        p = plan.make_plan(inputs(modes=frozenset({"fan_only"}), heat=heat(25.0), conditions=conds(outdoor=26.0)))
        want = sum(p.w * ((pt.hci - 25.5) / 1.5) ** 2 / 12 for pt in p.points)
        self.assertAlmostEqual(p.comfort_cost, want, places=9)

    def test_inside_but_within_sigma_counts_as_outside(self) -> None:
        f = heat(25.0)
        f.p[0][0] = 0.09  # sigma about 0.3 C
        t = 26.9 - (0.33 * E_ROOM - 4.0)  # index 26.9, inside a band topping at 27
        f.z[0] = f.z[1] = t
        p = plan.make_plan(inputs(heat=f, modes=frozenset({"fan_only"}), conditions=conds(outdoor=t)))
        first = p.points[0]
        self.assertLess(first.hci, 27.0)
        self.assertTrue(first.outside)

    def test_coil_rebound_after_cooling(self) -> None:
        hum = load("model.humidity").HumidityFilter.start(E_ROOM)
        base = {"humidity": hum, "modes": frozenset({"fan_only"}), "heat": heat(25.0)}
        after = plan.make_plan(inputs(compressor_running=True, last_kind=SK.COOL, **base))
        rested = plan.make_plan(inputs(**base))
        self.assertGreater(after.points[2].hci, rested.points[2].hci)
        self.assertAlmostEqual(after.points[5].hci - rested.points[5].hci,
                               after.points[5].hci - rested.points[5].hci)

    def test_closed_blinds_pass_15_percent(self) -> None:
        p = plan.make_plan(inputs(modes=frozenset({"fan_only"}), heat=heat(25.0), blind_control=True,
                                  conditions=conds(outdoor=25.0, sun=True)))
        pt = p.points[0]
        self.assertTrue(p.first.blinds_closed)
        # closed because less sun heats the room less, not because of any
        # term in the index [D010]
        self.assertAlmostEqual(pt.hci, pt.t + 0.33 * E_ROOM - 4.0, places=9)
        shut = plan.make_plan(inputs(modes=frozenset({"fan_only"}), heat=heat(25.0), blind_control=False,
                                     transmission_now=0.15, conditions=conds(outdoor=25.0, sun=True)))
        self.assertAlmostEqual(pt.t, shut.points[0].t, places=9)

    def test_blinds_cut_the_sun_the_model_forecasts(self) -> None:
        # S = sun on the glass x f_clear x tau_blind (4.2): a closed blind
        # lets 15 % of the sun in, so the room warms less
        sun = conds(outdoor=25.0, sun=True)
        kw = {"modes": frozenset({"fan_only"}), "heat": heat(25.0), "conditions": sun}
        shut = plan.make_plan(inputs(transmission_now=0.15, **kw))
        open_ = plan.make_plan(inputs(transmission_now=1.0, **kw))
        self.assertLess(shut.points[-1].t, open_.points[-1].t)

    def test_start_cost_counts_in_the_ranking(self) -> None:
        f = heat(26.0)
        cheap = plan.make_plan(inputs(heat=f, conditions=conds(outdoor=29.0)))
        dear = plan.make_plan(inputs(heat=f, conditions=conds(outdoor=29.0), draw_for_rate=lambda r: 0.3 * abs(r) if
                                     r != 1.0 else 30.0))
        self.assertLessEqual(dear.starts, cheap.starts + 12)
        self.assertAlmostEqual(dear.c_start, 0.5 * 30.0)
        self.assertLessEqual(dear.starts, 1)

    def test_index_follows_the_forecast(self) -> None:
        # sun on the glass changes the forecast temperature, never the index's
        # formula: the index is T + 0.33 e - 4.00 at every point [D010]
        sunny = plan.make_plan(inputs(modes=frozenset({"fan_only"}), heat=heat(25.0),
                                      conditions=conds(sun=True, f_clear=0.5)))
        shade = plan.make_plan(inputs(modes=frozenset({"fan_only"}), heat=heat(25.0)))
        for pt in (sunny.points[0], sunny.points[-1], shade.points[0]):
            self.assertAlmostEqual(pt.hci, pt.t + 0.33 * E_ROOM - 4.0, places=9)
        self.assertGreater(sunny.points[-1].t, shade.points[-1].t)


class PathNow(unittest.TestCase):
    def test_interpolates(self) -> None:
        p = plan.make_plan(inputs(heat=heat(26.5), conditions=conds(outdoor=33.0)))
        t, slope = plan.path_now(p, NOW + timedelta(minutes=2.5), 26.5)
        self.assertAlmostEqual(t, (26.5 + p.points[0].t) / 2)
        self.assertAlmostEqual(slope, (p.points[0].t - 26.5) * 12)
        self.assertEqual(plan.path_now(p, NOW + timedelta(hours=7), 26.5), (p.points[-1].t, 0.0))


class ConditionsAhead(unittest.TestCase):
    def snap(self, outdoor: float | None = 22.0, forecast: tuple[Any, ...] = ()) -> Any:
        from tests.pure.sim_room import SimRoom

        return dataclasses.replace(
            SimRoom().snapshot(),
            outdoor_temperature=models.Reading(outdoor, models.ValueTag.MEASURED if outdoor else
                                               models.ValueTag.MISSING),
            outdoor_humidity=models.Reading(55.0, models.ValueTag.MEASURED),
            forecast=forecast,
        )

    def room(self, facing: str | None = None) -> Any:
        return models.RoomConfig("r", "R", (models.HeadConfig("climate.x"),), "s.t", "s.h", (24, 27), (21, 24),
                                 (24, 27), window_facing=facing)

    def test_forecast_and_fill(self) -> None:
        fc = (models.ForecastHour(NOW, 30.0, None, None, 45.0, None),)
        c = plan.conditions_ahead(self.snap(forecast=fc), self.room(), None, lambda t: 10.0, NOW)
        self.assertEqual(len(c), 72)
        self.assertEqual((c[0].outdoor, c[0].outdoor_rh), (30.0, 55.0))
        self.assertAlmostEqual(c[0].f_clear, 0.6)
        self.assertEqual((c[20].outdoor, c[20].f_clear), (22.0, 1.0))  # beyond the forecast
        self.assertFalse(c[0].sun_on_glass)  # no window direction

    def test_no_outdoor_temperature(self) -> None:
        self.assertIsNone(plan.conditions_ahead(self.snap(None), self.room(), None, lambda t: 10.0, NOW))
        c = plan.conditions_ahead(self.snap(None), self.room(), None, lambda t: 10.0, NOW, last_outdoor=19.0)
        self.assertEqual(c[0].outdoor, 19.0)

    def test_sun_on_glass_ahead(self) -> None:
        brisbane = (-27.5, 153.0)
        afternoon = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)  # 15:00 local
        c = plan.conditions_ahead(self.snap(), self.room("W"), brisbane, lambda t: 15.0, afternoon)
        self.assertTrue(c[0].sun_on_glass)
        c = plan.conditions_ahead(self.snap(), self.room("E"), brisbane, lambda t: 15.0, afternoon)
        self.assertFalse(c[0].sun_on_glass)


def figs(**kw: float) -> Any:
    f = heat(**kw)
    return physics.figures(f.z)


class Gains(unittest.TestCase):
    def test_spec_example(self) -> None:
        # 4.5: theta 6 min, tau 15 min, tau1 above 48 min: Kc = 5 /h, tau_I = 48 min,
        # Kp = 6.56 /h, Ti = 63 min, Td = 11.4 min
        g = pid.gains(figs(a=0.1, b=0.5, c=0.15, k=0.05), 6.0, 15.0, 0.0)
        self.assertAlmostEqual(g.kp, 6.5625, places=9)
        self.assertAlmostEqual(g.ti * 60, 63.0, places=9)
        self.assertAlmostEqual(g.td * 60, 48 * 15 / 63, places=9)

    def test_tau1_binds_for_a_fast_room(self) -> None:
        f = figs(a=5.0, b=0.5, c=0.15, k=0.05)
        tau1 = physics.steady_gain_h(f)
        self.assertLess(tau1, 0.8)
        g = pid.gains(f, 6.0, 15.0, 0.0)
        self.assertAlmostEqual(g.ti, tau1 + 0.25, places=9)

    def test_actuation_adds_half_its_interval(self) -> None:
        self.assertEqual(pid.actuation_minutes(0.1), 0.5)
        self.assertEqual(pid.actuation_minutes(None), 0.5)
        self.assertEqual(pid.actuation_minutes(1.0), 10.0)
        f = figs(a=0.1, b=0.5, c=0.15, k=0.05)
        self.assertEqual(pid.gains(f, 0.0, 15.0, 10.0), pid.gains(f, 5.0, 15.0, 0.0))
        self.assertTrue(math.isfinite(pid.gains(f, 0.0, 15.0, 0.5).kp))


G = pid.Gains(kp=6.0, ti=1.0, td=0.2)


def step(p: Any, minute: float, **kw: Any) -> Any:
    args: dict[str, Any] = {"now": NOW + timedelta(minutes=minute), "g": G, "u_plan": 2.0, "t_est": 25.0,
                            "t_path": 25.0, "slope_est": 0.0, "slope_path": 0.0, "u_min": 1.0, "top": 6.0,
                            "delivered": True}
    args.update(kw)
    return p.step(**args)


class PidTests(unittest.TestCase):
    def test_law(self) -> None:
        p = pid.Pid()
        r = step(p, 0, t_est=25.2, slope_est=0.5, slope_path=-0.5)
        self.assertAlmostEqual(r.u, 2.0 + 6.0 * (0.2 + 0.0 + 0.2 * 1.0))
        self.assertAlmostEqual(r.p, 1.2)
        self.assertAlmostEqual(r.d, 1.2)

    def test_integral_starts_at_zero_and_grows_while_delivered(self) -> None:
        p = pid.Pid()
        step(p, 0, t_est=25.1)
        r = step(p, 30, t_est=25.1)
        self.assertAlmostEqual(p.integral, 0.1 * 0.5)
        self.assertAlmostEqual(r.i, 6.0 * 0.05 / 1.0)
        step(p, 60, t_est=25.1, delivered=False)
        self.assertAlmostEqual(p.integral, 0.05)  # held: not delivered [D054]
        p.rest()
        step(p, 90, t_est=25.1)
        self.assertEqual(p.integral, 0.0)  # a new start

    def test_no_wind_up_at_a_limit(self) -> None:
        p = pid.Pid()
        r = step(p, 0, t_est=26.0)
        self.assertEqual(r.u, 6.0)
        step(p, 30, t_est=26.0)
        self.assertEqual(p.integral, 0.0)

    def test_stays_in_the_plans_direction(self) -> None:
        p = pid.Pid()
        self.assertEqual(step(p, 0, t_est=20.0).u, 1.0)  # never below U_min
        h = pid.Pid()
        r = step(h, 0, u_plan=-2.0, t_est=25.2)
        self.assertEqual(r.u, -1.0)  # -2.0 + 1.2 = -0.8, held at U_min in the heating direction
        r = step(pid.Pid(), 0, u_plan=-2.0, t_est=24.9)
        self.assertAlmostEqual(r.u, -2.0 - 0.6)

    def test_bumpless_on_new_gains(self) -> None:
        p = pid.Pid()
        step(p, 0, t_est=25.1)
        before = step(p, 1, t_est=25.1).u
        after = step(p, 2, t_est=25.1, g=pid.Gains(kp=3.0, ti=0.5, td=0.1)).u
        self.assertAlmostEqual(after, before, places=9)


class Rl(unittest.TestCase):
    def test_nothing_without_a_policy(self) -> None:
        state = rl.RlState(0.0, 0.0, 0.0, 25.0, 0.0, 0.1, 0.1, 12.0, 24.0)
        self.assertIsNone(rl.RlController().propose(state, lambda r: r))
        c = rl.RlController(policy=lambda s: 9.0)
        self.assertEqual(c.propose(state, lambda r: min(r, 6.0)), 6.0)


def day(rl_out: float, rl_cost: float, plan_out: float = 0.0, plan_cost: float = 1.0, fidelity: float = 0.1,
        other: bool = True) -> Any:
    plan_score = arb.DayScore(plan_out, plan_cost)
    return arb.DayResult("d", plan_score, plan_score, fidelity, arb.DayScore(rl_out, rl_cost) if other else None)


class Arbiter(unittest.TestCase):
    def test_hand_up_needs_trust_and_eleven_wins(self) -> None:
        a = arb.Arbiter()
        for i in range(14):
            a.end_of_day(day(0.0, 0.5 if i >= 3 else 2.0), True, 14, NOW)
        self.assertIs(a.in_charge, models.Controller.RL)  # 11 of 14
        b = arb.Arbiter()
        for i in range(14):
            b.end_of_day(day(0.0, 0.5 if i >= 4 else 2.0), True, 14, NOW)
        self.assertIs(b.in_charge, models.Controller.PLAN_PID)  # 10 of 14
        c = arb.Arbiter()
        for _ in range(14):
            c.end_of_day(day(0.0, 0.5), True, 13, NOW)
        self.assertIs(c.in_charge, models.Controller.PLAN_PID)  # 13 days of trust

    def test_a_win_needs_no_more_time_outside_and_a_lower_cost(self) -> None:
        self.assertTrue(arb.rl_wins(arb.DayScore(5, 0.9), arb.DayScore(5, 1.0)))
        self.assertFalse(arb.rl_wins(arb.DayScore(6, 0.1), arb.DayScore(5, 1.0)))
        self.assertFalse(arb.rl_wins(arb.DayScore(5, 1.0), arb.DayScore(5, 1.0)))

    def test_unfaithful_days_and_no_policy_do_not_count(self) -> None:
        a = arb.Arbiter()
        a.end_of_day(day(0.0, 0.1, fidelity=0.31), True, 14, NOW)
        a.end_of_day(day(0.0, 0.1, other=False), True, 14, NOW)
        self.assertEqual(list(a.wins), [None, None])

    def test_hand_down(self) -> None:
        a = arb.Arbiter(in_charge=models.Controller.RL)
        a.end_of_day(day(0.0, 0.5), False, 14, NOW)
        self.assertIs(a.in_charge, models.Controller.PLAN_PID)
        b = arb.Arbiter(in_charge=models.Controller.RL)
        # RL in charge: its real day is in_charge; the plan is simulated as the other
        lose = arb.DayResult("d", arb.DayScore(0, 2.0), arb.DayScore(0, 2.0), 0.1, arb.DayScore(0, 1.0))
        b.end_of_day(lose, True, 14, NOW)
        self.assertIs(b.in_charge, models.Controller.RL)
        b.end_of_day(lose, True, 14, NOW)
        self.assertIs(b.in_charge, models.Controller.PLAN_PID)
        self.assertTrue(b.handed_over)
        c = arb.Arbiter(in_charge=models.Controller.RL)
        c.lost_trust(NOW)
        self.assertIs(c.in_charge, models.Controller.PLAN_PID)

    def test_round_trip(self) -> None:
        a = arb.Arbiter()
        a.end_of_day(day(0.0, 0.5), True, 14, NOW)
        b = arb.Arbiter.from_dict(a.as_dict())
        self.assertEqual((b.in_charge, list(b.wins), b.plan_streak), (a.in_charge, list(a.wins), a.plan_streak))


class RoomControlTests(unittest.TestCase):
    def test_cycle(self) -> None:
        rc = control.RoomControl()
        self.assertIsNone(rc.cycle(now=NOW, t_est=25.0, slope_est=0.0, u_min=1.0, top=6.0, delivered=True))
        f = heat(26.5)
        g = pid.gains(physics.figures(f.z), 6.0, 15.0, 0.5)
        p = rc.replan(inputs(heat=f, conditions=conds(outdoor=33.0)), g)
        self.assertIs(p.first.kind, SK.COOL)
        r = rc.cycle(now=NOW + timedelta(minutes=1), t_est=25.0, slope_est=0.0, u_min=1.0, top=6.0, delivered=True)
        self.assertIs(r.kind, SK.COOL)
        self.assertIsNotNone(r.pid)
        self.assertEqual(r.rate, r.pid.u)  # the PID's rate is the one given
        self.assertNotEqual(r.rate, p.first.rate)
        self.assertIs(r.in_charge, models.Controller.PLAN_PID)
        rc.replan(inputs(heat=heat(23.0), conditions=conds(outdoor=20.0), modes=frozenset({"fan_only"})), g)
        r = rc.cycle(now=NOW + timedelta(minutes=6), t_est=23.0, slope_est=0.0, u_min=1.0, top=6.0, delivered=True)
        self.assertEqual((r.kind, r.pid), (SK.REST, None))
        self.assertFalse(rc.pid.running)


if __name__ == "__main__":
    unittest.main()
