"""evidence.py: the trace, the scorecard, warnings and repairs (Module 9)."""

from __future__ import annotations

import json
import logging
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

ev = load("evidence")
models = load("models")
SK = models.StepKind
BR = models.BandRule
MS = models.RoomModeState
WK = models.WarningKind

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def room(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "r1", "name": "Office", "heads": (models.HeadConfig("climate.a"),),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh", "occupied_band": (23.0, 26.0),
        "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0), "announce": True,
        "announce_through": ("media_player.office",),
    }
    base.update(kw)
    return models.RoomConfig(**base)


def comfort(rule: Any = BR.OCCUPIED, index: float | None = 24.0, low: float | None = 23.0,
            high: float | None = 26.0) -> Any:
    return models.ComfortResult(rule, False, "the occupied band", True, False, False, False, (), index, 23.4,
                                band_low=low, band_high=high)


def limits(kind: Any = SK.COOL, *, lockout: bool = False, automatic_off: bool = False) -> Any:
    return models.LimitsResult(kind, (), (), (), (models.LimitRule.OPENING,), True, False, lockout, automatic_off,
                               ("no_grid_import",), (), (), ("A door is open.",))


def plan() -> Any:
    act = models.PlanAction(SK.COOL, 2.0, None)
    pts = tuple(models.PlanPoint(T0 + timedelta(minutes=5 * i), 24.0, 24.123456, 0.2, False) for i in range(72))
    return models.Plan(T0, (act,) * 12, pts, 0.0, 0.5, 1.25, 0.6, 1, 0.44, 0.6,
                       ((models.PlanAction(SK.REST, 0.0, None), "leaves the band"),))


def control() -> Any:
    pid = models.PidTerms(2.5, 0.2, 0.4, 0.1, 0.0, 6.5, 1.05, 0.19)
    return models.ControlResult(SK.COOL, 2.5, None, models.Controller.PLAN_PID, plan(), pid, "the plan")


def answers() -> Any:
    return models.ModelAnswers(True, 24.1234, 25.5, 15.0, 0.1, {"a": 0.123456}, {"a": 0.01}, 0.058, 300,
                               "corrected", True, False, 0.05, None, 6.0, 15.0, 1.0, 6.0, 6.0)


def economics(ceiling: float | None = None) -> Any:
    return models.RoomEconomics((1.0,) * 12, 1.0, ceiling, None, False, 2.5 if ceiling else None, "budget")


class Mode(unittest.TestCase):
    def test_state(self) -> None:
        self.assertIs(ev.mode_state(limits(lockout=True, automatic_off=True), comfort()), MS.LOCKOUT)
        self.assertIs(ev.mode_state(limits(automatic_off=True), comfort()), MS.AUTOMATIC_OFF)
        for rule, state in ((BR.LEFT, MS.LEFT), (BR.UNOCCUPIED, MS.UNOCCUPIED), (BR.PRECOOL, MS.PRECOOL),
                            (BR.SLEEP, MS.SLEEP), (BR.OCCUPIED, MS.OCCUPIED)):
            self.assertIs(ev.mode_state(limits(), comfort(rule)), state)
        self.assertIs(ev.mode_state(None, comfort(BR.SLEEP)), MS.SLEEP)


class Trace(unittest.TestCase):
    def build(self, **kw: Any) -> Any:
        base: dict[str, Any] = {
            "snapshot": SimRoom().snapshot(), "answers": answers(), "comfort": comfort(), "economics": economics(),
            "control": control(), "other_proposal": None, "delivery": None, "limits": limits(),
            "choices": models.RoomChoices(),
        }
        base.update(kw)
        return ev.trace(**base)

    def test_every_part_is_there(self) -> None:
        t = self.build()
        self.assertEqual(t["action"], "cool")
        self.assertEqual(t["band"], {"low": 23.0, "high": 26.0, "centre": 24.5, "centre_c": 23.4, "position": "in"})
        self.assertEqual(t["plan"]["index"][0], 24.12)
        self.assertEqual(len(t["plan"]["actions"]), 12)
        self.assertEqual(t["plan"]["not_taken"], [["rest", 0.0, "leaves the band"]])  # [D006]
        self.assertEqual(t["pid"]["ti_min"], 63.0)
        self.assertEqual(t["model"]["figures"], {"a": 0.1235})
        self.assertEqual(t["model"]["room"], 24.12)
        self.assertEqual(t["limits"]["changed"], ["opening"])
        self.assertEqual(t["power"]["ceiling"], "no limit")  # [D096]
        self.assertEqual(t["compressors"]["climate.sim"], {"state": "stopped", "source": "hvac_action"})
        json.dumps(t)  # plain values only

    def test_position_and_ceiling(self) -> None:
        self.assertEqual(self.build(comfort=comfort(index=22.0))["band"]["position"], "below")
        self.assertEqual(self.build(comfort=comfort(index=26.5))["band"]["position"], "above")
        self.assertEqual(self.build(comfort=comfort(index=23.0))["band"]["position"], "in")
        self.assertEqual(self.build(comfort=comfort(index=26.0))["band"]["position"], "in")
        self.assertIsNone(self.build(comfort=comfort(low=None, high=None))["band"])
        self.assertEqual(self.build(economics=economics(3.456))["power"]["ceiling"], 3.46)

    def test_parts_left_out_when_absent(self) -> None:
        t = self.build(control=None, economics=None, limits=None)
        self.assertEqual(t["action"], "none")
        for key in ("plan", "pid", "power", "limits", "in_charge"):
            self.assertNotIn(key, t)

    def test_delivery(self) -> None:
        h = models.HeadCommand("climate.a", "cool", 24.0, None, None, 24.04, "auto", None)
        d = models.DeliveryResult(SK.COOL, 2.0, (h,), (), (), True, "outdoor air is cooler", True, ("why",))
        t = self.build(delivery=d)["delivery"]
        self.assertEqual(t["wanted_setpoints"], {"climate.a": 24.04})
        self.assertEqual((t["free_cooling_advised"], t["condensation_risk"]), (True, True))


class Scorecard(unittest.TestCase):
    def test_a_day(self) -> None:
        s = ev.RoomScorecard()
        band = (23.0, 26.0)
        for _ in range(60):  # 30 min occupied, in band and near the centre, not running
            s.observe(dt_s=30, occupied=True, index=24.6, band=band, running=False, draw_kw=None, price=None)
        for _ in range(60):  # 30 min occupied, out of the band, running at 1 kW at $0.30
            s.observe(dt_s=30, occupied=True, index=26.5, band=band, running=True, draw_kw=1.0, price=0.3)
        for _ in range(120):  # 1 h empty, not running
            s.observe(dt_s=30, occupied=False, index=26.5, band=band, running=False, draw_kw=1.0, price=0.3)
        s.observe(dt_s=30, occupied=False, index=None, band=None, running=True, draw_kw=1.0, price=0.3)
        day = s.end_of_day()
        self.assertAlmostEqual(day.comfort_in_band, 50.0)
        self.assertAlmostEqual(day.near_centre, 50.0)
        self.assertAlmostEqual(day.starts_per_hour, 2 / (1830 / 3600))
        self.assertAlmostEqual(day.cost, 0.3 * 1830 / 3600)
        self.assertEqual(s.yesterday, day)
        self.assertEqual(s.as_dict()["near_centre"], day.near_centre)

    def test_nothing_to_measure_and_a_new_day(self) -> None:
        s = ev.RoomScorecard()
        s.observe(dt_s=30, occupied=False, index=None, band=None, running=True, draw_kw=1.0, price=None)
        self.assertEqual(s.end_of_day(), models.DayScore(None, None, 0.0, None))
        # still running at midnight: not a new start
        s.observe(dt_s=30, occupied=False, index=None, band=None, running=True, draw_kw=1.0, price=0.3)
        day = s.end_of_day()
        self.assertEqual(day.starts_per_hour, 0.0)
        self.assertAlmostEqual(day.cost, 0.3 * 30 / 3600)
        self.assertEqual(ev.RoomScorecard().as_dict(), {})

    def test_the_band_edges_count_as_inside(self) -> None:
        s = ev.RoomScorecard()
        for index in (23.0, 26.0, 26.01, 22.99):
            s.observe(dt_s=30, occupied=True, index=index, band=(23.0, 26.0), running=False, draw_kw=None,
                      price=None)
        self.assertAlmostEqual(s.end_of_day().comfort_in_band, 50.0)

    def test_a_start_just_after_midnight_counts(self) -> None:
        s = ev.RoomScorecard()
        s.observe(dt_s=30, occupied=False, index=None, band=None, running=False, draw_kw=None, price=None)
        s.end_of_day()
        s.observe(dt_s=3600, occupied=False, index=None, band=None, running=True, draw_kw=1.0, price=0.3)
        self.assertEqual(s.end_of_day().starts_per_hour, 1.0)

    def test_near_the_centre_is_within_0_3(self) -> None:
        s = ev.RoomScorecard()
        s.observe(dt_s=30, occupied=True, index=24.8, band=(23.0, 26.0), running=False, draw_kw=None, price=None)
        s.observe(dt_s=30, occupied=True, index=24.81, band=(23.0, 26.0), running=False, draw_kw=None, price=None)
        self.assertAlmostEqual(s.end_of_day().near_centre, 50.0)


class Warnings(unittest.TestCase):
    DUE = (models.DueWarning(WK.EMPTY_ROOM, 180.0),)

    def say(self, w: Any = None, due: Any = DUE, **kw: Any) -> Any:
        base: dict[str, Any] = {"room": room(), "engine": "tts.home", "automatic": True, "waiting_for_unit": False,
                                "asleep": False, "unit_on_at_start": True, "held_off": False, "off_sent": False}
        base.update(kw)
        return (w or ev.RoomWarnings()).to_say(due, **base)

    def test_said(self) -> None:
        s = self.say()
        self.assertEqual(s, [models.Speech("tts.home", ("media_player.office",), "empty_room",
                                           {"room": "Office", "minutes": "3"})])  # [D069]

    def test_dropped(self) -> None:
        for kw in ({"automatic": False}, {"waiting_for_unit": True}, {"asleep": True},  # [D071]
                   {"unit_on_at_start": False},  # [D077]
                   {"engine": None}, {"room": room(announce=False)}, {"room": room(announce_through=())}):
            self.assertEqual(self.say(**kw), [], kw)

    def test_held_off_is_said_with_the_off(self) -> None:
        w = ev.RoomWarnings()
        self.assertEqual(self.say(w, held_off=True), [])
        self.assertEqual(self.say(w, due=(), held_off=True), [])
        said = self.say(w, due=(), off_sent=True)
        self.assertEqual([s.key for s in said], ["empty_room"])  # [D077]
        self.assertEqual(self.say(w, due=(), off_sent=True), [])

    def test_held_warning_dropped_if_the_off_stops_coming(self) -> None:
        w = ev.RoomWarnings()
        self.say(w, held_off=True)
        self.assertEqual(self.say(w, due=()), [])
        self.assertEqual(self.say(w, due=(), off_sent=True), [])
        self.say(w, held_off=True)
        self.assertEqual(self.say(w, due=(), asleep=True), [])
        self.assertEqual(self.say(w, due=(), off_sent=True), [])
        self.say(w, held_off=True)
        self.assertEqual(self.say(w, due=(), engine=None), [])
        self.assertEqual(self.say(w, due=(), off_sent=True), [])


def house(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "tariff_configured": True, "tariff_error": None, "unknown_constraints": frozenset(), "speech_wanted": True,
        "speech_engine": "tts.home", "speech_engines_available": 1, "solar_forecast_configured": True,
        "solar_forecast_error": None, "grid_sign_contradicted": False, "grid_import_kwh": None,
        "shared_climates": {"climate.a": ("Office",)},
    }
    base.update(kw)
    return ev.HouseFacts(**base)


class Repairs(unittest.TestCase):
    def test_none_when_all_is_well(self) -> None:
        self.assertEqual(ev.house_issues(house()), [])

    def test_each_house_repair(self) -> None:
        cases = (
            ({"unknown_constraints": frozenset({"z", "a"})}, "unknown_constraint", {"constraints": "a, z"}),
            ({"tariff_error": "no response"}, "tariff_unreadable", {"error": "no response"}),
            ({"speech_engine": None, "speech_engines_available": 0}, "no_speech_engine", {}),
            ({"speech_engine": None, "speech_engines_available": 2}, "speech_engine_not_chosen", {"count": "2"}),
            ({"solar_forecast_error": "boom"}, "solar_forecast_unreadable", {"error": "boom"}),
            ({"grid_sign_contradicted": True}, "grid_sign_contradicted", {}),
            ({"grid_import_kwh": 0.0712}, "grid_import", {"kwh": "0.07"}),
            ({"shared_climates": {"climate.a": ("Office", "Den")}}, "shared_climate",
             {"entity": "climate.a", "rooms": "Office, Den"}),
        )
        for kw, key, placeholders in cases:
            issues = ev.house_issues(house(**kw))
            self.assertEqual([(i.key, dict(i.placeholders)) for i in issues], [(key, placeholders)], key)

    def test_not_raised_for_what_was_not_set_up(self) -> None:
        self.assertEqual(ev.house_issues(house(tariff_configured=False, tariff_error="no response")), [])
        self.assertEqual(ev.house_issues(house(speech_wanted=False, speech_engine=None,
                                               speech_engines_available=0)), [])
        self.assertEqual(ev.house_issues(house(speech_engine=None, speech_engines_available=1)), [])
        self.assertEqual(ev.house_issues(house(solar_forecast_configured=False, solar_forecast_error="x")), [])

    def test_room_repairs(self) -> None:
        facts = ev.RoomFacts(room(), ("sensor.t",), None, T0, None)
        issues = ev.room_issues(facts, T0 + timedelta(hours=1))
        self.assertEqual([(i.issue_id, i.key) for i in issues], [("room_sensor_missing_r1_sensor.t",
                                                                 "room_sensor_missing")])  # [D046]
        silent = ev.RoomFacts(room(), (), T0, T0 - timedelta(days=3), "the sun had to be assumed")
        self.assertEqual(ev.room_issues(silent, T0 + timedelta(hours=23, minutes=59)), [])
        issues = ev.room_issues(silent, T0 + timedelta(hours=24))
        self.assertEqual([(i.key, i.placeholders["reason"]) for i in issues],
                         [("filter_silent", "the sun had to be assumed")])
        never = ev.RoomFacts(room(), (), None, T0, None)
        self.assertEqual(ev.room_issues(never, T0 + timedelta(hours=23)), [])
        self.assertEqual(ev.room_issues(never, T0 + timedelta(hours=24))[0].placeholders["reason"],
                         "no reading has been usable")
        restarted = ev.RoomFacts(room(), (), T0 - timedelta(days=5), T0, "x")
        self.assertEqual(ev.room_issues(restarted, T0 + timedelta(hours=1)), [])


class LostAndRegained(unittest.TestCase):
    def test_logged_once_each_way(self) -> None:
        a = ev.Availability(room())
        with self.assertLogs("custom_components.abode_hvac_coordinator", logging.INFO) as cm:
            self.assertEqual(a.update({"sensor.t": True, "climate.a": True}), [])
            self.assertEqual(a.update({"sensor.t": False, "climate.a": True}), [("sensor.t", True)])
            self.assertEqual(a.update({"sensor.t": False, "climate.a": True}), [])
            self.assertEqual(a.lost, frozenset({"sensor.t"}))
            self.assertEqual(a.update({"sensor.t": True, "climate.a": True}), [("sensor.t", False)])
            self.assertEqual(a.update({"sensor.t": True, "climate.a": True}), [])
        self.assertEqual(len(cm.records), 2)
        self.assertIn("Office: sensor.t cannot be read", cm.output[0])
        self.assertIn("Office: sensor.t can be read again", cm.output[1])


if __name__ == "__main__":
    unittest.main()
