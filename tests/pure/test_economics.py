"""economics.py: prices, the power budget, grid checks, demand (Module 4)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load

eco = load("economics")
models = load("models")
PM = models.PowerManagement

T0 = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
H = timedelta(hours=1)


def iv(h0: float, h1: float, price: float, enforced: tuple[str, ...] = (), hints: tuple[str, ...] = ()) -> Any:
    return models.TariffInterval(T0 + h0 * H, T0 + h1 * H, "r", price, enforced, hints)


def item(h0: float, h1: float, price: float, constraints: list[str] | None = None,
         enforceable: list[str] | None = None) -> dict[str, Any]:
    # Abode Power Tariffs' Interval.as_dict (intervals.py, commit 34d71d5)
    return {
        "start_time": (T0 + h0 * H).isoformat(),
        "end_time": (T0 + h1 * H).isoformat(),
        "duration": int((h1 - h0) * 60),
        "per_kwh": price,
        "export_per_kwh": 0.05,
        "rate": "Peak",
        "constraints": constraints or [],
        "enforceable_constraints": enforceable or [],
        "coasting_permitted": False,
        "allowance_kwh": None,
        "fallback_rate": None,
        "fallback_per_kwh": None,
        "day_pattern": "Weekdays",
        "demand_period": False,
        "demand_rate_per_kw_month": 0.0,
        "export_allowance_kwh": None,
        "export_fallback_price": None,
        "forecast": False,
    }


class Tariff(unittest.TestCase):
    def test_reads_the_published_shape(self) -> None:
        r = eco.parse_tariff({"intervals": [item(0.5, 1, 0.42), item(0, 0.5, 0.0, ["no_grid_import",
                                                                                     "precool_opportunity"],
                                                                                    ["no_grid_import"])]})
        self.assertIsNone(r.error)
        first, second = r.intervals
        self.assertEqual((first.start, first.price, first.rate_name), (T0, 0.0, "Peak"))
        self.assertEqual((first.constraints, first.hints), (("no_grid_import",), ("precool_opportunity",)))
        self.assertEqual(second.price, 0.42)
        self.assertEqual(r.unknown, frozenset())

    def test_unknown_reported_never_dropped(self) -> None:
        r = eco.parse_tariff({"intervals": [item(0, 1, 0.3, ["mystery"], ["other_rule"])]})
        self.assertEqual(r.unknown, frozenset({"mystery", "other_rule"}))
        self.assertEqual(r.intervals[0].constraints, ("other_rule",))
        self.assertEqual(r.intervals[0].hints, ("mystery",))

    def test_unreadable(self) -> None:
        self.assertIsNotNone(eco.parse_tariff(None).error)
        self.assertIsNotNone(eco.parse_tariff({}).error)
        bad = item(0, 1, 0.3)
        bad["start_time"] = "2026-10-27T14:00:00"  # no offset
        self.assertIsNotNone(eco.parse_tariff({"intervals": [bad]}).error)
        bad = item(0, 1, 0.3)
        del bad["per_kwh"]
        r = eco.parse_tariff({"intervals": [bad]})
        self.assertEqual((r.intervals, r.error is not None), ((), True))

    def test_prices_ahead(self) -> None:
        tariff = [iv(0, 2, 0.0), iv(2, 4, 0.5), iv(4, 5, 0.2)]
        prices, p_max = eco.prices_ahead(tariff, T0 + 0.25 * H)
        self.assertEqual(p_max, 0.5)
        self.assertEqual(len(prices), 12)
        self.assertEqual(prices[:4], [0.0, 0.0, 0.0, 0.0])  # 14:15, 14:45, 15:15, 15:45
        self.assertEqual(prices[4], 0.5)  # 16:15
        self.assertEqual(prices[8], 0.2)  # 18:15
        self.assertEqual(prices[9:], [0.2, 0.2, 0.2])  # beyond the last interval
        self.assertEqual(eco.prices_ahead([], T0), ([1.0] * 12, 1.0))

    def test_no_import_window(self) -> None:
        tariff = [iv(-1, 0, 0.1), iv(0, 2, 0.0, ("no_grid_import",)), iv(2, 4, 0.5, ("no_grid_import",)),
                  iv(4, 5, 0.2), iv(5, 6, 0.2, ("no_grid_import",))]
        self.assertEqual(eco.no_import_window_end(tariff, T0 + 0.5 * H), T0 + 4 * H)
        self.assertIsNone(eco.no_import_window_end(tariff, T0 - 0.5 * H))
        self.assertEqual(eco.no_import_window_end(tariff, T0 + 5.5 * H), T0 + 6 * H)
        hint_only = [iv(0, 2, 0.0, (), ("no_grid_import",))]
        self.assertIsNone(eco.no_import_window_end(hint_only, T0 + 0.5 * H))

    def test_next_tariff_change(self) -> None:
        # half-hour intervals of one rate are one period; a change of price or rule ends it
        tariff = [iv(0, 0.5, 0.3), iv(0.5, 1, 0.3), iv(1, 1.5, 0.3, ("no_grid_import",)), iv(1.5, 2, 0.2)]
        self.assertEqual(eco.next_tariff_change(tariff, T0 + 0.25 * H), T0 + 1 * H)
        self.assertEqual(eco.next_tariff_change(tariff, T0 + 1.25 * H), T0 + 1.5 * H)
        self.assertIsNone(eco.next_tariff_change(tariff, T0 + 1.75 * H))  # none ahead differs
        self.assertIsNone(eco.next_tariff_change(tariff, T0 - 1 * H))  # none in force
        renamed = [iv(0, 1, 0.3), models.TariffInterval(T0 + H, T0 + 2 * H, "shoulder", 0.3, (), ())]
        self.assertEqual(eco.next_tariff_change(renamed, T0), T0 + 1 * H)


class Loads(unittest.TestCase):
    def test_rest_of_house(self) -> None:
        t = eco.HouseLoadTracker()
        t.add(T0, 1.0, 2.0, frozenset())
        t.add(T0 + timedelta(minutes=1), 2.2, 4.0, frozenset({"ou1"}))
        t.add(T0 + timedelta(minutes=2), 1.2, None, frozenset({"ou2"}))
        self.assertAlmostEqual(t.rest_of_house({"ou1"}, {"ou1": 1.2}), 1.1)
        self.assertAlmostEqual(t.rest_of_house({"ou1", "ou2"}, {}), 1.0)  # neither running
        self.assertAlmostEqual(t.solar(), 3.0)
        t.add(T0 + timedelta(minutes=16), 2.4, 1.0, frozenset({"ou1"}))
        self.assertEqual([s[0] for s in t.samples],  # 15 minutes kept
                         [T0 + timedelta(minutes=m) for m in (1, 2, 16)])

    def test_running_throughout(self) -> None:
        t = eco.HouseLoadTracker()
        t.add(T0, 2.4, 0.0, frozenset({"ou1"}))
        t.add(T0 + timedelta(minutes=1), 2.6, 0.0, frozenset({"ou1"}))
        self.assertAlmostEqual(t.rest_of_house({"ou1"}, {"ou1": 1.3}), 1.2)
        self.assertIsNone(t.rest_of_house({"ou1"}, {"ou1": None}))
        self.assertIsNone(t.rest_of_house({"ou1"}, {}))

    def test_each_minute_less_the_units_running_then(self) -> None:
        """No minute without one: each minute's load less the learned draws
        of the units running then; minutes with one not learned are left out."""
        t = eco.HouseLoadTracker()
        t.add(T0, 2.5, 0.0, frozenset({"ou1"}))
        t.add(T0 + timedelta(minutes=1), 4.0, 0.0, frozenset({"ou1", "ou2"}))
        t.add(T0 + timedelta(minutes=2), 3.0, 0.0, frozenset({"ou1", "ou3"}))
        self.assertAlmostEqual(t.rest_of_house({"ou1", "ou2", "ou3"}, {"ou1": 1.5, "ou2": 1.5}), 1.0)

    def test_another_enforced_unit_is_not_counted_twice(self) -> None:
        """Another enforced unit's draw comes off the allowance once, not
        also through the rest of the house [I10][D067]."""
        def allowance(b_running: bool) -> float:
            pb = eco.PowerBudget()
            for i in range(31):
                pb.loads.add(T0 + timedelta(seconds=30 * i), 1.0 + (1.5 if b_running else 0.0), 0.0,
                             frozenset({"uB"}) if b_running else frozenset())
            now = T0 + timedelta(minutes=15)
            rooms = [models.RoomPower("A", "uA", models.PowerManagement.ENFORCED, False, False, 3.0, 1.0),
                     models.RoomPower("B", "uB", models.PowerManagement.ENFORCED, False, b_running, 3.0, 1.0)]
            out = pb.evaluate(now=now, window_end=now + timedelta(hours=2),
                              house=eco.HousePower(50.0, 10.0, 1.0, None, True, True), rooms=rooms,
                              draw_for_rate={"A": linear, "B": linear},
                              running_draw={("uB", "B"): 1.5} if b_running else {},
                              solar_slices=None, worst_clear_sky=1.0)
            return out["A"][2]
        self.assertAlmostEqual(allowance(False) - allowance(True), 1.5, places=6)


def slices(kwh: list[float], start: datetime = T0) -> list[Any]:
    return [eco.SolarSlice(start + i * H, start + (i + 1) * H, k) for i, k in enumerate(kwh)]


class Allowance(unittest.TestCase):
    def test_battery_only(self) -> None:
        # 5 kWh over 2 hours, rest of house 1 kW: 5/2 - 1 = 1.5 kW
        p = eco.allowance_with_forecast(e_avail=5.0, headroom=0.0, rest=1.0, slices=slices([0, 0]), plant_kw=None)
        self.assertAlmostEqual(p, 1.5, delta=0.005)

    def test_solar_later_does_not_save_an_empty_battery_now(self) -> None:
        # nothing in the battery and nothing in the first hour: no draw fits
        p = eco.allowance_with_forecast(e_avail=0.0, headroom=10.0, rest=1.0, slices=slices([0, 8]), plant_kw=None)
        self.assertEqual(p, 0.0)

    def test_full_battery_spills_solar(self) -> None:
        # battery full (no headroom): the first hour's 4 kWh above need is lost
        p = eco.allowance_with_forecast(e_avail=1.0, headroom=0.0, rest=0.0, slices=slices([4, 0]), plant_kw=None)
        self.assertAlmostEqual(p, 1.0, delta=0.005)
        q = eco.allowance_with_forecast(e_avail=1.0, headroom=10.0, rest=0.0, slices=slices([4, 0]), plant_kw=None)
        self.assertAlmostEqual(q, 2.5, delta=0.005)

    def test_below_the_reserve_starts_from_zero(self) -> None:
        # E0 = max(E_avail, 0): 3 kWh of sun in the first hour carries 2 hours at 1.5 kW
        p = eco.allowance_with_forecast(e_avail=-2.0, headroom=10.0, rest=0.0, slices=slices([3, 0]),
                                        plant_kw=None)
        self.assertAlmostEqual(p, 1.5, delta=0.005)

    def test_plant_limit(self) -> None:
        p = eco.allowance_with_forecast(e_avail=50.0, headroom=0.0, rest=1.0, slices=slices([0, 0]), plant_kw=2.0)
        self.assertEqual(p, 2.0)

    def test_available(self) -> None:
        house = eco.HousePower(80.0, 13.5, 2.0, 5.0, True, True)
        self.assertAlmostEqual(eco.available_kwh(house), 0.8 * 13.5 - 2.0)
        self.assertIsNone(eco.available_kwh(eco.HousePower(None, 13.5, 2.0, None, True, True)))

    def test_without_forecast(self) -> None:
        # solar 3 kW x worst clear sky 0.5 = 1.5 sustained; rest 1 kW -> credit 0.5
        p = eco.allowance_without_forecast(e_avail=4.0, rest=1.0, solar_now=3.0, worst_clear_sky=0.5,
                                           hours_left=2.0, rated_kw=None)
        self.assertAlmostEqual(p, 0.5 + 2.0)
        # rest 2 kW: shortfall 0.5 kW; battery 4/2 - 0.5 = 1.5; rated 1.0 - 0.5 = 0.5 binds
        p = eco.allowance_without_forecast(e_avail=4.0, rest=2.0, solar_now=3.0, worst_clear_sky=0.5,
                                           hours_left=2.0, rated_kw=1.0)
        self.assertAlmostEqual(p, 0.5)
        self.assertEqual(eco.allowance_without_forecast(e_avail=-1.0, rest=2.0, solar_now=0.0, worst_clear_sky=1.0,
                                                        hours_left=2.0, rated_kw=None), 0.0)

    def test_highest_rate(self) -> None:
        def draw(r: float) -> float:
            return 0.3 + 0.25 * r

        self.assertAlmostEqual(eco.highest_rate(draw, 1.0, 6.0), 2.8)
        self.assertIsNone(eco.highest_rate(draw, 0.3, 6.0))
        self.assertEqual(eco.highest_rate(draw, 9.0, 6.0), 6.0)
        self.assertIsNone(eco.highest_rate(lambda r: None, 9.0, 6.0))


HOUSE = eco.HousePower(50.0, 10.0, 1.0, None, True, True)


def room(rid: str = "r1", unit: str = "ou1", mgmt: Any = PM.ENFORCED, precool: bool = False,
         running: bool = True, lowest: float = 1.0) -> Any:
    return models.RoomPower(rid, unit, mgmt, precool, running, 6.0, lowest)


def linear(r: float) -> float:
    return 0.3 + 0.25 * r


class Budget(unittest.TestCase):
    def budget(self, load: float = 1.0) -> Any:
        b = eco.PowerBudget()
        b.loads.add(T0 - timedelta(minutes=1), load, 0.0, frozenset())
        return b

    def go(self, b: Any, now: datetime, rooms: list[Any], *, end: datetime | None = T0 + 2 * H,
            running: dict[tuple[str, str], float | None] | None = None, house: Any = HOUSE) -> Any:
        return b.evaluate(now=now, window_end=end, house=house, rooms=rooms,
                          draw_for_rate={r.room_id: linear for r in rooms}, running_draw=running or {},
                          solar_slices=None, worst_clear_sky=1.0)

    def test_the_forecast_is_cut_to_the_window(self) -> None:
        """The forecast holds every hour it has, today's past ones and
        tomorrow's; only the window from now to its end counts, and time it
        does not cover counts as no solar [D061][HA]."""
        day = datetime(2026, 1, 10, tzinfo=UTC)
        hours = [eco.SolarSlice(d + timedelta(hours=h), d + timedelta(hours=h + 1), 1.5)
                 for d in (day, day + timedelta(days=1)) for h in range(6, 19)]
        now, end = day + timedelta(hours=19, minutes=30), day + timedelta(hours=21)  # after sunset
        cut = eco.window_slices(hours, now, end)
        self.assertEqual([(s.start, s.end, s.kwh) for s in cut], [(now, end, 0.0)])
        part = eco.window_slices(hours, day + timedelta(hours=18, minutes=30), end)
        self.assertEqual([round(s.kwh, 6) for s in part], [0.75, 0.0])  # half of 18-19, then none
        self.assertEqual(part[0].end, day + timedelta(hours=19))
        gap = eco.window_slices([hours[0], hours[2]], day + timedelta(hours=6), day + timedelta(hours=9))
        self.assertEqual([round(s.kwh, 6) for s in gap], [1.5, 0.0, 1.5])  # an hour missing: none
        for e_avail_pct, want in ((50.0, 1.0), (30.0, 0.0)):  # 3 kWh and 1 kWh above the reserve
            house = eco.HousePower(e_avail_pct, 10.0, 2.0, None, True, True)
            b = eco.PowerBudget()
            b.loads.add(now - timedelta(minutes=1), 1.0, 0.0, frozenset())  # rest of the house 1 kW
            r = models.RoomPower("r1", "u1", models.PowerManagement.ENFORCED, False, False, 6.0, 1.0)
            out = b.evaluate(now=now, window_end=end, house=house, rooms=[r], draw_for_rate={"r1": linear},
                             running_draw={}, solar_slices=hours, worst_clear_sky=1.0)
            # the battery alone carries the 1.5 h left: E / 1.5 - 1 kW, never below 0
            self.assertAlmostEqual(out["r1"][2], want, delta=0.01)

    def test_no_window_no_limit(self) -> None:
        out = self.go(self.budget(), T0, [room()], end=None)
        self.assertEqual(out["r1"][:2], (None, False))
        self.assertIn("no no-grid-import window", out["r1"][3])

    def test_missing_figure_means_no_limit(self) -> None:
        house = eco.HousePower(None, 10.0, 1.0, None, True, True)
        out = self.go(self.budget(), T0, [room()], house=house)
        self.assertEqual(out["r1"][0], None)
        self.assertIn("battery charge", out["r1"][3])
        out = self.go(eco.PowerBudget(), T0, [room()])
        self.assertIn("house load", out["r1"][3])

    def test_ceiling(self) -> None:
        # E_avail = 5 - 1 = 4 kWh over 2 h, rest 1 kW, no solar: P* = 1 kW
        out = self.go(self.budget(), T0, [room()])
        self.assertAlmostEqual(out["r1"][2], 1.0)
        self.assertAlmostEqual(out["r1"][0], 2.8)  # 0.3 + 0.25 x 2.8 = 1.0

    def test_precool_and_off_not_rationed(self) -> None:
        out = self.go(self.budget(), T0, [room(precool=True), room("r2", mgmt=PM.OFF)])
        self.assertEqual((out["r1"][0], out["r1"][3]), (None, "precool is not rationed"))
        self.assertEqual(out["r2"][0], None)

    def test_other_enforced_units_share_the_allowance(self) -> None:
        rooms = [room(lowest=0.5), room("r2", "ou2"), room("r3", "ou3", mgmt=PM.GUIDANCE)]
        out = self.go(self.budget(), T0, rooms, running={("ou2", "r2"): 0.5, ("ou3", "r3"): 0.4})
        self.assertAlmostEqual(out["r1"][2], 0.5)  # 1.0 less ou2's 0.5; ou3 is guidance
        self.assertAlmostEqual(out["r1"][0], 0.8)

    def test_rooms_on_one_outdoor_unit_share_its_draw(self) -> None:
        """Two rooms on one outdoor unit are one draw on the battery: each
        room's allowance is less what the other room on the unit draws, whatever
        that room's setting [D097]."""
        rooms = [room(lowest=0.5), room("r2", mgmt=PM.GUIDANCE), room("r3", mgmt=PM.OFF)]
        out = self.go(self.budget(), T0, rooms, running={("ou1", "r2"): 0.3, ("ou1", "r3"): 0.2})
        self.assertAlmostEqual(out["r1"][2], 0.5)  # 1.0 less r2's 0.3 and r3's 0.2
        self.assertAlmostEqual(out["r2"][2], 0.8)  # 1.0 less r3's 0.2; its own draw is not counted
        # a unit running throughout: the rest of the house is the load less every room's draw on it
        b = eco.PowerBudget()
        b.loads.add(T0 - timedelta(minutes=1), 2.0, 0.0, frozenset({"ou1"}))
        out = self.go(b, T0, rooms[:2], running={("ou1", "r1"): 0.5, ("ou1", "r2"): 0.5})
        self.assertAlmostEqual(out["r1"][2], 0.5)  # P* = 4 kWh / 2 h - (2.0 - 1.0) = 1.0, less r2's 0.5
        unknown = self.go(b, T0, rooms[:1], running={("ou1", "r1"): 0.5, ("ou1", "r2"): None})
        self.assertIn("not yet measured", unknown["r1"][3])  # one room's draw unknown: the unit's is too

    def test_drops_at_once_rises_after_ten_minutes_with_margin(self) -> None:
        b = self.budget()
        rooms = [room(lowest=0.5), room("r2", "ou2")]

        def at(minutes: float, running: dict[tuple[str, str], float | None]) -> float:
            now = T0 + timedelta(minutes=minutes)
            return self.go(b, now, rooms, running=running, end=now + 2 * H)["r1"][0]

        self.assertAlmostEqual(at(0, {}), 2.8)  # allowance 1.0 kW
        self.assertAlmostEqual(at(1, {("ou2", "r2"): 0.5}), 0.8)  # drops at once: 0.5 kW left
        self.assertAlmostEqual(at(5, {}), 0.8)  # not yet risen: stood 4 minutes
        self.assertAlmostEqual(at(11, {}), 1.6)  # risen, clearing 1.0 kW by 0.3: 0.3 + 0.25 x 1.6 = 0.7
        self.assertAlmostEqual(at(22, {}), 1.6)  # the margin holds it there

    def test_cools_at_the_lowest_rate_until_it_cannot(self) -> None:
        """A battery that cannot last to the end of the window even at the
        lowest rate keeps the room cooling at its lowest rate until it is
        down to its reserve (decision of 29 October 2026)."""
        b = self.budget(load=3.0)  # P* = 4 kWh / 2 h - 3 kW < 0: nothing fits to the end
        out = self.go(b, T0, [room()])
        self.assertEqual(out["r1"][:2], (1.0, False))  # the lowest rate, not off
        self.assertIn("until the battery reaches its reserve", out["r1"][3])
        later = self.go(b, T0 + timedelta(minutes=30), [room()])
        self.assertEqual(later["r1"][:2], (1.0, False))  # no 10-minute turn-off any more

    def test_a_ceiling_below_the_lowest_rate_is_lifted_to_it(self) -> None:
        out = self.go(self.budget(), T0, [room(lowest=3.0)])  # 2.8 C/h fits; the lowest rate is 3.0
        self.assertEqual(out["r1"][:2], (3.0, False))

    def test_off_once_the_battery_is_at_its_reserve(self) -> None:
        b = self.budget(load=3.0)
        at_reserve = eco.HousePower(10.0, 10.0, 1.0, None, True, True)  # 1 kWh - 1 kWh reserve
        out = self.go(b, T0, [room()], house=at_reserve)
        self.assertEqual(out["r1"][:2], (0.0, True))
        self.assertIn("down to its reserve", out["r1"][3])
        resting = self.go(b, T0, [room(running=False)], house=at_reserve)
        self.assertEqual(resting["r1"][:2], (0.0, False))  # a resting room is left alone
        guided = self.go(b, T0, [room(mgmt=PM.GUIDANCE)], house=at_reserve)
        self.assertFalse(guided["r1"][1])  # guidance never turns off [D063]

    def test_off_when_the_battery_cannot_supply_the_lowest_rate(self) -> None:
        b = self.budget(load=3.0)
        weak = eco.HousePower(50.0, 10.0, 1.0, 3.2, True, True)  # 3.2 kW plant - 3 kW rest: 0.2 kW free
        out = self.go(b, T0, [room()], house=weak)  # the lowest rate draws 0.55 kW
        self.assertEqual(out["r1"][:2], (0.0, True))
        self.assertIn("cannot supply", out["r1"][3])
        strong = eco.HousePower(50.0, 10.0, 1.0, 5.0, True, True)
        self.assertEqual(self.go(b, T0, [room()], house=strong)["r1"][:2], (1.0, False))
        unknown = b.evaluate(now=T0, window_end=T0 + 2 * H, house=HOUSE, rooms=[room()],
                             draw_for_rate={"r1": lambda r: None if r <= 1.0 else 9.0}, running_draw={},
                             solar_slices=None, worst_clear_sky=1.0)
        self.assertTrue(unknown["r1"][1])  # the lowest rate's draw is not known

    def test_no_limit_when_the_top_fits(self) -> None:
        b = self.budget(load=0.0)
        house = eco.HousePower(100.0, 20.0, 0.0, None, True, True)  # 20 kWh over 2 h
        out = self.go(b, T0, [room()], house=house)
        self.assertEqual((out["r1"][0], out["r1"][3]), (None, "no limit"))

    def test_room_economics(self) -> None:
        budget = (2.0, True, 1.0, "ceiling")
        e = eco.room_economics(PM.ENFORCED, [0.1] * 12, 0.5, budget)
        self.assertEqual((e.ceiling, e.guidance_ceiling, e.turn_off, e.p_max), (2.0, None, True, 0.5))
        g = eco.room_economics(PM.GUIDANCE, [0.1] * 12, 0.5, budget)
        self.assertEqual((g.ceiling, g.guidance_ceiling, g.turn_off), (None, 2.0, False))


def plan(actions: list[tuple[Any, float]], made_at: datetime = T0) -> Any:
    acts = tuple(models.PlanAction(k, r, None) for k, r in actions)
    return models.Plan(made_at, acts, (), 0.0, 0.0, 0.0, 0.0, 0, 1.0, 0.0)


class DemandToChange(unittest.TestCase):
    """The demand forecast to the next tariff change (decision of 29 October 2026)."""

    def test_to_the_next_tariff_change(self) -> None:
        tariff = [iv(0, 1, 0.3), iv(1, 2, 0.1)]
        p = plan([(models.StepKind.COOL, 2.0)] * 4)  # 2 hours of 0.8 kW
        f = eco.forecast_to_change([(p, linear)], tariff, T0)
        self.assertEqual((f.until, f.to_change), (T0 + H, True))
        self.assertAlmostEqual(f.kwh, 0.8)  # the first hour only
        self.assertEqual([(iv_.price, round(k, 3)) for iv_, k in f.per_interval], [(0.3, 0.8)])
        self.assertAlmostEqual(f.cost, 0.24)  # priced at 0.30 $/kWh

    def test_the_part_already_past_is_not_demand(self) -> None:
        tariff = [iv(0, 1, 0.3), iv(1, 2, 0.1)]
        p = plan([(models.StepKind.COOL, 2.0)] * 4)
        f = eco.forecast_to_change([(p, linear)], tariff, T0 + 0.25 * H)
        self.assertAlmostEqual(f.kwh, 0.6)  # 45 minutes of 0.8 kW

    def test_a_change_beyond_the_plans_runs_to_their_end(self) -> None:
        tariff = [iv(0, 24, 0.3)]
        p = plan([(models.StepKind.COOL, 2.0), (models.StepKind.REST, 0.0)] * 6)
        f = eco.forecast_to_change([(p, linear)], tariff, T0)
        self.assertEqual((f.until, f.to_change), (T0 + 6 * H, False))
        self.assertAlmostEqual(f.kwh, 6 * 0.4)
        late = [iv(0, 1, 0.3), iv(1, 9, 0.1)]
        f = eco.forecast_to_change([(p, linear)], late[:1] + [iv(1, 8, 0.3), iv(8, 9, 0.1)], T0)
        self.assertEqual((f.until, f.to_change), (T0 + 6 * H, False))  # the change at 8 h is beyond

    def test_heating_is_priced_on_its_own_curve(self) -> None:
        """A heating rate is negative; its sign picks the heating curve [D028]."""
        tariff = [iv(0, 24, 0.3)]
        p = plan([(models.StepKind.HEAT, -2.0)] * 2)  # an hour of heating
        f = eco.forecast_to_change([(p, lambda r: 0.6 if r < 0 else 1.0)], tariff, T0)
        self.assertAlmostEqual(f.kwh, 0.6)

    def test_without_a_tariff_or_a_plan(self) -> None:
        p = plan([(models.StepKind.DRY, 1.0), (models.StepKind.OFF, 0.0)])
        f = eco.forecast_to_change([(p, lambda r: None)], [], T0)
        self.assertEqual((f.until, f.to_change, f.cost, f.per_interval), (T0 + H, False, None, ()))
        self.assertAlmostEqual(f.kwh, 0.6)  # an unlearned draw priced as the plan prices it, 1.2 kW
        self.assertIsNone(eco.forecast_to_change([], [], T0))


class Grid(unittest.TestCase):
    def test_evidence(self) -> None:
        self.assertTrue(eco.grid_evidence(1.0, 0.5, False, 300.0))
        self.assertFalse(eco.grid_evidence(1.0, 0.5, False, -300.0))
        self.assertIsNone(eco.grid_evidence(0.6, 0.5, False, 300.0))  # 0.1 kW net load
        self.assertIsNone(eco.grid_evidence(1.0, 0.5, True, 300.0))  # battery falling
        self.assertIsNone(eco.grid_evidence(1.0, 0.5, False, 5.0))  # within 5 W of zero

    def test_ten_contradictions_raise_a_repair(self) -> None:
        g = eco.GridChecks(positive_is_import=True)
        kw: dict[str, Any] = {"load_kw": 1.0, "solar_kw": 0.0, "charge_pct": 50.0, "in_no_import_window": False}
        for i in range(9):
            g.observe(T0 + i * timedelta(minutes=1), grid_kw=-0.8, **kw)
        self.assertFalse(g.sign_repair)
        g.observe(T0 + 9 * timedelta(minutes=1), grid_kw=0.004, **kw)  # near zero resets
        self.assertEqual(g.contradictions, 0)
        for i in range(10):
            g.observe(T0 + (10 + i) * timedelta(minutes=1), grid_kw=-0.8, **kw)
        self.assertTrue(g.sign_repair)

    def test_import_in_a_window(self) -> None:
        g = eco.GridChecks(positive_is_import=False)
        common: dict[str, Any] = {"load_kw": None, "solar_kw": None, "charge_pct": None}
        g.observe(T0, grid_kw=0.0, in_no_import_window=True, **common)
        g.observe(T0 + timedelta(minutes=30), grid_kw=-0.2, in_no_import_window=True, **common)  # 0.2 kW in
        g.observe(T0 + timedelta(minutes=31), grid_kw=0.0, in_no_import_window=False, **common)
        self.assertAlmostEqual(g.import_repair_kwh, 0.1)
        g.observe(T0 + 2 * H, grid_kw=0.0, in_no_import_window=True, **common)
        g.observe(T0 + 3 * H, grid_kw=0.0, in_no_import_window=False, **common)
        self.assertIsNone(g.import_repair_kwh)

    def test_battery_power(self) -> None:
        self.assertAlmostEqual(eco.battery_power(2.0, 3.0, -0.5), -0.5)


class Demand(unittest.TestCase):
    def test_split_by_overlap(self) -> None:
        intervals = [iv(0, 1, 0.1), iv(1, 2, 0.2)]
        rooms = [[(T0 + 0.5 * H, T0 + 1.5 * H, 1.0)], [(T0, T0 + 0.5 * H, 0.4)]]
        total, per = eco.demand_forecast(rooms, intervals)
        self.assertAlmostEqual(total, 1.4)
        self.assertAlmostEqual(per[0][1], 0.9)
        self.assertAlmostEqual(per[1][1], 0.5)


if __name__ == "__main__":
    unittest.main()
