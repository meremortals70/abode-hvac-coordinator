"""capabilities.py: what a unit can do (Section 9) [D048]."""

from __future__ import annotations

import unittest
from typing import Any

from tests.pure._load import load

cap = load("capabilities")
models = load("models")

# Attributes as Home Assistant's climate platform publishes them [HA]
UNIT: dict[str, Any] = {
    "hvac_modes": ["off", "heat", "dry", "cool", "fan_only", "heat_cool"],
    "min_temp": 18.0,
    "max_temp": 30.0,
    "target_temp_step": 1.0,
    "fan_modes": ["auto", "quiet", "low", "medium", "high"],
    "swing_modes": ["auto", "swing", "1", "2", "3", "4"],
    "supported_features": 1 | 8 | 32 | 128 | 256,
    "current_temperature": 24.0,
    "temperature": 24.0,
}


class ReadProfile(unittest.TestCase):
    def test_reads_advertised_controls(self) -> None:
        p = cap.read_profile("cool", UNIT)
        self.assertEqual(p.hvac_modes, ("off", "heat", "dry", "cool", "fan_only", "heat_cool"))
        self.assertEqual(p.fan_modes, ("auto", "quiet", "low", "medium", "high"))
        self.assertEqual(p.vertical_vanes, ("auto", "swing", "1", "2", "3", "4"))
        self.assertEqual(p.horizontal_vanes, ())
        self.assertEqual((p.min_setpoint, p.max_setpoint, p.setpoint_step), (18.0, 30.0, 1.0))
        self.assertFalse(p.range_only)

    def test_not_reporting(self) -> None:
        for state in (None, "unavailable", "unknown"):
            self.assertIsNone(cap.read_profile(state, UNIT))
        self.assertIsNone(cap.read_profile("cool", {**UNIT, "min_temp": None}))
        self.assertIsNone(cap.read_profile("cool", {**UNIT, "min_temp": 31.0}))

    def test_no_step_is_tenths(self) -> None:
        attrs = {k: v for k, v in UNIT.items() if k != "target_temp_step"}
        self.assertEqual(cap.read_profile("cool", attrs).setpoint_step, 0.1)

    def test_limits_and_step_stored_in_celsius(self) -> None:
        # a Fahrenheit system: 61-86 F in whole degrees is 16.1-30 C in 5/9 C steps
        p = cap.read_profile("cool", {**UNIT, "min_temp": 61.0, "max_temp": 86.0, "target_temp_step": 1.0}, "°F")
        self.assertAlmostEqual(p.min_setpoint, 16.1111, places=4)
        self.assertAlmostEqual(p.max_setpoint, 30.0, places=9)
        self.assertAlmostEqual(p.setpoint_step, 5.0 / 9.0, places=12)
        k = cap.read_profile("cool", {**UNIT, "min_temp": 290.15, "max_temp": 303.15}, "K")
        self.assertAlmostEqual(k.min_setpoint, 17.0, places=9)
        self.assertIsNone(cap.read_profile("cool", UNIT, "°R"))  # a unit it does not know

    def test_the_step_is_in_the_units_own_unit(self) -> None:
        """Home Assistant converts the limits to the system's unit but
        publishes the step in the entity's own unit [HA]."""
        f_unit = {**UNIT, "min_temp": 16.1, "max_temp": 30.0, "target_temp_step": 1.0}
        p = cap.read_profile("cool", f_unit, "°C", "°F")  # a °F unit on a °C system
        self.assertAlmostEqual(p.setpoint_step, 5.0 / 9.0, places=12)
        p = cap.read_profile("cool", {**f_unit, "min_temp": 61.0, "max_temp": 86.0, "target_temp_step": 0.5}, "°F", "°C")
        self.assertAlmostEqual(p.setpoint_step, 0.5, places=12)  # a °C unit on a °F system
        self.assertAlmostEqual(cap.read_profile("cool", f_unit, "°C", None).setpoint_step, 1.0)  # not known: the system's

    def test_the_units_own_limits_are_used(self) -> None:
        """Home Assistant shows the limits rounded (whole F in a F house) but
        refuses a setpoint outside the unit's own by any amount [HA][D051]."""
        shown = {**UNIT, "min_temp": 64.0, "max_temp": 88.0, "target_temp_step": 0.1}  # 18-31 C shown in whole F
        p = cap.read_profile("cool", shown, "°F", "°C", (18.0, 31.0))
        self.assertEqual((p.min_setpoint, p.max_setpoint), (18.0, 31.0))
        p = cap.read_profile("cool", shown, "°F", "°F", (64.0, 88.0))  # a F unit: its own are the shown ones
        self.assertAlmostEqual(p.min_setpoint, (64.0 - 32.0) / 1.8)

    def test_shown_whole_degree_limits_are_moved_inside_the_real_ones(self) -> None:
        """Without the unit's own limits, whole-degree limits of a unit working
        in another unit are moved in by half a shown degree and onto its step,
        so nothing sent can fall outside them [HA][D051]."""
        for lo_c, hi_c, step in ((18.0, 30.0, 0.1), (16.0, 31.0, 0.5), (17.0, 32.0, None)):
            shown = {**UNIT, "min_temp": round(lo_c * 1.8 + 32.0), "max_temp": round(hi_c * 1.8 + 32.0)}
            shown.pop("target_temp_step", None)
            if step is not None:
                shown["target_temp_step"] = step
            p = cap.read_profile("cool", shown, "°F", "°C")
            self.assertGreaterEqual(p.min_setpoint, lo_c - 1e-9)
            self.assertLessEqual(p.max_setpoint, hi_c + 1e-9)
            grid = step or 0.1
            for v in (p.min_setpoint, p.max_setpoint):
                self.assertAlmostEqual(v / grid, round(v / grid), places=6)  # on the unit's grid
        # limits too close to fit a setpoint once moved inside: not usable
        self.assertIsNone(cap.read_profile("cool", {**UNIT, "min_temp": 64.0, "max_temp": 65.0}, "°F", "°C"))
        same = cap.read_profile("cool", {**UNIT, "min_temp": 16.0, "max_temp": 30.0}, "°C", "°C")
        self.assertEqual((same.min_setpoint, same.max_setpoint), (16.0, 30.0))  # its own unit: as shown

    def test_conversions_are_inverse(self) -> None:
        for unit in (None, "°C", "°F", "K"):
            self.assertAlmostEqual(cap.TO_C[unit](cap.FROM_C[unit](23.4)), 23.4, places=12)

    def test_lists_only_with_their_feature(self) -> None:
        p = cap.read_profile("cool", {**UNIT, "supported_features": 1})
        self.assertEqual((p.fan_modes, p.vertical_vanes), ((), ()))
        p = cap.read_profile("cool", {**UNIT, "supported_features": 1 | 512, "swing_horizontal_modes": ["a", "b"]})
        self.assertEqual(p.horizontal_vanes, ("a", "b"))

    def test_range_only(self) -> None:
        self.assertTrue(cap.read_profile("heat_cool", {**UNIT, "supported_features": 2}).range_only)
        self.assertFalse(cap.read_profile("heat_cool", {**UNIT, "supported_features": 3}).range_only)


def profile(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "hvac_modes": ("off", "cool", "dry", "fan_only"),
        "fan_modes": ("auto", "low", "high"),
        "vertical_vanes": ("auto", "1"),
        "horizontal_vanes": (),
        "min_setpoint": 18.0,
        "max_setpoint": 30.0,
        "setpoint_step": 0.5,
        "range_only": False,
    }
    base.update(kw)
    return models.CapabilityProfile(**base)


class ModeLists(unittest.TestCase):
    def test_a_list_that_is_not_a_list_is_read_as_empty(self) -> None:
        self.assertEqual(cap._strings(5), ())
        self.assertEqual(cap._strings("cool"), ())
        self.assertEqual(cap._strings(["cool", 1]), ("cool", "1"))


class Combine(unittest.TestCase):
    def test_single_head_unchanged(self) -> None:
        p = profile()
        self.assertIs(cap.combine([p]), p)

    def test_only_what_every_head_can_do(self) -> None:
        a = profile()
        b = profile(hvac_modes=("cool", "off", "heat"), fan_modes=("high", "auto"), min_setpoint=16.0, max_setpoint=28.0, setpoint_step=1.0)
        c = cap.combine([a, b])
        self.assertEqual(c.hvac_modes, ("off", "cool"))
        self.assertEqual(c.fan_modes, ("auto", "high"))
        self.assertEqual((c.min_setpoint, c.max_setpoint), (18.0, 28.0))
        self.assertEqual(c.setpoint_step, 1.0)
        self.assertIsNone(c.step_reason)

    def test_steps_not_whole_multiples(self) -> None:
        c = cap.combine([profile(setpoint_step=0.5), profile(setpoint_step=0.3)])
        self.assertIsNone(c.setpoint_step)
        self.assertIn("not whole multiples", c.step_reason)

    def test_any_range_only_head_makes_the_room_range_only(self) -> None:
        self.assertTrue(cap.combine([profile(), profile(range_only=True)]).range_only)

    def test_ranges_that_do_not_overlap_are_reported(self) -> None:
        c = cap.combine([profile(max_setpoint=20.0), profile(min_setpoint=22.0)])
        self.assertIn("do not overlap", c.step_reason)
        self.assertIn("do not overlap", cap.setup_problem(c))
        self.assertIsNone(cap.setup_problem(cap.combine([profile(), profile(min_setpoint=20.0)])))

    def test_a_head_without_a_step_leaves_the_room_unrounded(self) -> None:
        c = cap.combine([profile(setpoint_step=None), profile(setpoint_step=0.5)])
        self.assertIsNone(c.setpoint_step)
        self.assertIn("not rounded", c.step_reason)

    def test_a_range_only_head_with_one_that_cannot_take_a_range_is_refused(self) -> None:
        """Nothing sent could suit both, and Home Assistant refuses a range
        to a unit that does not advertise it [D024]."""
        ranged = cap.read_profile("cool", {**UNIT, "supported_features": 2}, "°C", "°C")
        single = cap.read_profile("cool", {**UNIT, "supported_features": 1}, "°C", "°C")
        both = cap.read_profile("cool", {**UNIT, "supported_features": 3}, "°C", "°C")
        self.assertEqual((ranged.range_only, ranged.takes_range), (True, True))
        self.assertEqual((single.range_only, single.takes_range), (False, False))
        self.assertEqual((both.range_only, both.takes_range), (False, True))
        mixed = cap.combine([ranged, single])
        self.assertFalse(cap.heads_agree_on_setpoints(mixed))
        self.assertIn("cannot take them", cap.setup_problem(mixed))
        for fine in ([ranged, both], [single, both], [single, single]):
            self.assertIsNone(cap.setup_problem(cap.combine(fine)), fine)

    def test_empty_refused(self) -> None:
        with self.assertRaises(ValueError):
            cap.combine([])


class RoundToStep(unittest.TestCase):
    def test_clamps_and_rounds(self) -> None:
        p = profile(setpoint_step=1.0)
        self.assertEqual(cap.round_to_step(22.4, p), 22.0)
        self.assertEqual(cap.round_to_step(22.6, p), 23.0)
        self.assertEqual(cap.round_to_step(10.0, p), 18.0)
        self.assertEqual(cap.round_to_step(40.0, p), 30.0)
        self.assertEqual(cap.round_to_step(22.37, profile(setpoint_step=None)), 22.37)


if __name__ == "__main__":
    unittest.main()
