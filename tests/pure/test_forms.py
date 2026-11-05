"""forms.py: form answers to stored settings and back (Section 8)."""

from __future__ import annotations

import unittest
from typing import Any

from tests.pure._load import load

forms = load("forms")
models = load("models")
const = load("const")

PROFILE = models.CapabilityProfile(("off", "cool"), ("auto",), (), (), 16.0, 30.0, 0.5, False)


def stored(**kw: Any) -> dict[str, Any]:
    room: dict[str, Any] = {"room_id": "r1", "name": "Office", "heads": [{"climate": "climate.a"}],
                            "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh"}
    room.update(kw)
    return room


class House(unittest.TestCase):
    def test_house_from_data_and_defaults(self) -> None:
        h = forms.house_from_data({"tariff_entry": " t1 ", "battery_size": 10, "grid_positive_is_import": False,
                                   "solar_forecast_sources": ["a", "a", " ", "b"], "reserve_margin": float("nan")})
        self.assertEqual(h.tariff_entry, "t1")
        self.assertEqual(h.battery_size_kwh, 10.0)
        self.assertIs(h.grid_positive_is_import, False)
        self.assertEqual(h.solar_forecast_sources, ("a", "b"))
        self.assertIsNone(h.reserve_margin_kwh)  # not a finite number
        self.assertIsNone(forms.house_from_data({"grid_positive_is_import": "yes"}).grid_positive_is_import)
        self.assertIsNone(forms.house_from_data({"battery_size": True}).battery_size_kwh)

    def test_clean_house_keeps_only_answers(self) -> None:
        self.assertEqual(forms.clean_house({"weather": "weather.home", "outdoor_wind": "", "other": 1,
                                            "solar_forecast_sources": []}), {"weather": "weather.home"})

    def test_grid_direction_is_asked_only_for_a_new_or_changed_sensor(self) -> None:
        self.assertTrue(forms.grid_sensor_changed({}, {"grid_power": "sensor.g"}))
        self.assertFalse(forms.grid_sensor_changed({"grid_power": "sensor.g", "grid_positive_is_import": True},
                                                   {"grid_power": "sensor.g"}))
        self.assertTrue(forms.grid_sensor_changed({"grid_power": "sensor.g"}, {"grid_power": "sensor.g"}))
        self.assertTrue(forms.grid_sensor_changed({"grid_power": "sensor.g", "grid_positive_is_import": True},
                                                  {"grid_power": "sensor.h"}))
        self.assertFalse(forms.grid_sensor_changed({"grid_power": "sensor.g"}, {}))

    def test_power_kw(self) -> None:
        self.assertEqual(forms.power_kw("1500", "W"), 1.5)
        self.assertEqual(forms.power_kw("2", "kW"), 2.0)
        self.assertEqual(forms.power_kw("2", None), 2.0)
        self.assertIsNone(forms.power_kw("unavailable", "W"))
        self.assertIsNone(forms.power_kw("1", "BTU/h"))
        self.assertIsNone(forms.power_kw("inf", "W"))


class Rooms(unittest.TestCase):
    def test_a_room_with_its_defaults(self) -> None:
        r = forms.room_from_options(stored())
        self.assertEqual((r.occupied_band, r.sleep_band, r.precool_band), ((23.0, 26.0), (20.0, 23.0), (23.0, 26.0)))
        self.assertEqual((r.occupied_after_s, r.empty_after_s, r.warning_grace_s, r.opening_grace_s),
                         (120.0, 600.0, 180.0, 300.0))
        self.assertTrue(r.allow_blind_control)
        self.assertIs(r.power_management, models.PowerManagement.OFF)
        self.assertIsNone(r.lockout_reason)
        self.assertIsNone(r.profile)

    def test_a_room_with_every_setting(self) -> None:
        r = forms.room_from_options(stored(
            heads=[{"climate": "climate.a", "outdoor_unit": "Shed", "frequency_sensor": "sensor.hz"}, {"x": 1}, "bad"],
            occupied_low=22.0, occupied_high=25.5, power_management="enforced", window_facing="nw",
            lockout_reason="Painting", occupied_after=4, empty_after=-1, openings="binary_sensor.w",
            profile=forms.profile_to_dict(PROFILE)))
        self.assertEqual(r.heads, (models.HeadConfig("climate.a", "Shed", "sensor.hz"),))
        self.assertEqual(r.occupied_band, (22.0, 25.5))
        self.assertIs(r.power_management, models.PowerManagement.ENFORCED)
        self.assertEqual(r.window_facing, "NW")
        self.assertEqual(r.lockout_reason, "Painting")
        self.assertEqual(r.occupied_after_s, 240.0)
        self.assertEqual(r.empty_after_s, 600.0)  # a negative wait takes the default
        self.assertEqual(r.openings, ("binary_sensor.w",))
        self.assertEqual(r.profile, PROFILE)

    def test_values_that_cannot_be_read_take_their_defaults(self) -> None:
        r = forms.room_from_options(stored(power_management="maximum", window_facing="up",
                                           lockout_reason="Not locked out", profile="no"))
        self.assertIs(r.power_management, models.PowerManagement.OFF)
        self.assertIsNone(r.window_facing)
        self.assertIsNone(r.lockout_reason)
        self.assertIsNone(r.profile)

    def test_a_room_that_cannot_run_is_refused(self) -> None:
        for bad in (stored(room_id=""), stored(name=None), stored(heads=[]), stored(heads="x"),
                    stored(temperature_sensor=None), stored(humidity_sensor=" ")):
            with self.assertRaises(ValueError):
                forms.room_from_options(bad)

    def test_rooms_from_options_skips_what_cannot_be_read(self) -> None:
        self.assertEqual(forms.rooms_from_options({}), [])
        self.assertEqual(forms.rooms_from_options({"rooms": "x"}), [])
        rooms = forms.rooms_from_options({"rooms": [stored(), "x", stored(name=None)]})
        self.assertEqual([r.name for r in rooms], ["Office"])

    def test_room_answers_for_edit(self) -> None:
        answers = forms.room_answers(stored(heads=[{"climate": "climate.a"}, {"climate": "climate.b"}, {"x": 1}],
                                            profile={}))
        self.assertEqual(answers["climate"], ["climate.a", "climate.b"])
        self.assertNotIn("heads", answers)
        self.assertNotIn("profile", answers)
        self.assertEqual(answers["occupied_low"], 23.0)
        self.assertEqual(forms.room_answers(stored(heads="x"))["climate"], [])

    def test_build_room(self) -> None:
        room = forms.build_room("r9", {"name": "Den", "climate": ["climate.a"], "blinds": [], "presence_sensor": "",
                                       "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh"},
                                [{"climate": "climate.a", "outdoor_unit": "", "frequency_sensor": "sensor.hz",
                                  "other": 1}], {"occupied_low": 22.0}, PROFILE)
        self.assertEqual(room["room_id"], "r9")
        self.assertNotIn("climate", room)
        self.assertNotIn("blinds", room)
        self.assertNotIn("presence_sensor", room)
        self.assertEqual(room["heads"], [{"climate": "climate.a", "frequency_sensor": "sensor.hz"}])
        self.assertEqual(room["occupied_low"], 22.0)
        self.assertEqual(room["occupied_high"], 26.0)
        self.assertEqual(room["lockout_reason"], "Not locked out")
        self.assertEqual(forms.room_from_options(room).profile, PROFILE)

    def test_band_inverted(self) -> None:
        self.assertIsNone(forms.band_inverted({}))
        self.assertEqual(forms.band_inverted({"occupied_low": 26.0, "occupied_high": 26.0}), "occupied")
        self.assertEqual(forms.band_inverted({"precool_low": 27.0}), "precool")

    def test_climate_used_elsewhere(self) -> None:
        rooms = [stored(), stored(room_id="r2", name="Den", heads=[{"climate": "climate.b"}]), stored(room_id="r3",
                                                                                                       heads="x")]
        self.assertEqual(forms.climate_used_elsewhere(rooms, ["climate.b"], None), ("climate.b", "Den"))
        self.assertIsNone(forms.climate_used_elsewhere(rooms, ["climate.b"], "r2"))  # its own room
        self.assertIsNone(forms.climate_used_elsewhere(rooms, ["climate.z"], None))

    def test_replace_and_remove(self) -> None:
        options = forms.replace_room({}, stored(heads=[{"climate": "climate.a", "outdoor_unit": "Shed"}],
                                                lockout_reason="Painting"))
        self.assertEqual(options["outdoor_unit_names"], ["Shed"])
        self.assertEqual(options["lockout_reasons"], ["Painting"])
        options = forms.replace_room(options, stored(name="Study", lockout_reason="Under renovation",
                                                     heads=[{"climate": "climate.a", "outdoor_unit": "Shed"}]))
        self.assertEqual([r["name"] for r in options["rooms"]], ["Study"])  # replaced, not added
        self.assertEqual(options["outdoor_unit_names"], ["Shed"])
        self.assertEqual(options["lockout_reasons"], ["Painting"])  # a default reason is not added
        options = forms.replace_room(options, stored(room_id="r2"))
        self.assertEqual(len(options["rooms"]), 2)
        self.assertEqual([r["room_id"] for r in forms.remove_room(options, "r1")["rooms"]], ["r2"])

    def test_dropdowns(self) -> None:
        reasons = forms.lockout_choices({"lockout_reasons": ["Painting", 3]})
        self.assertEqual(reasons[0], "Not locked out")
        self.assertEqual(reasons[-1], "Painting")
        self.assertEqual(len(reasons), 8)
        self.assertEqual(forms.outdoor_unit_choices({"outdoor_unit_names": ["Shed", None]}), ["Shed"])

    def test_migration_keeps_everything(self) -> None:
        self.assertEqual(forms.migrate_entry(1, 0, {"a": 1}, {"rooms": []}), ({"a": 1}, {"rooms": []}))


class Summary(unittest.TestCase):
    def test_summary(self) -> None:
        house = forms.house_from_data({"tariff_entry": "t", "weather": "weather.home", "battery_charge": "sensor.b",
                                       "grid_power": "sensor.g"})
        rooms = [forms.room_from_options(stored(lockout_reason="Painting")),
                 forms.room_from_options(stored(room_id="r2", name="Den",
                                                heads=[{"climate": "climate.b"}, {"climate": "climate.c"}]))]
        text = forms.summary(house, rooms)
        self.assertIn("Tariff: from Abode Power Tariffs.", text)
        self.assertIn("Forecast: weather.home.", text)
        self.assertIn("Power sensors: battery, grid.", text)
        self.assertIn("Office: 1 air conditioner, comfort 23 to 26, power management off. Locked out: Painting.", text)
        self.assertIn("Den: 2 air conditioners", text)
        empty = forms.summary(forms.house_from_data({}), [])
        self.assertIn("Tariff: none; every hour priced the same.", empty)
        self.assertIn("Power sensors: none.", empty)
        self.assertIn("No rooms yet.", empty)


if __name__ == "__main__":
    unittest.main()
