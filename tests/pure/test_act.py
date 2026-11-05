"""act.py: what to send, and the record of it (Module 8). Sending itself is
tested through Home Assistant (tests/ha/test_act.py)."""

from __future__ import annotations

import logging
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load

act = load("act")
models = load("models")
SK = models.StepKind

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SEC = timedelta(seconds=1)


def profile(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "hvac_modes": ("off", "cool", "heat", "dry", "fan_only"), "fan_modes": ("auto", "quiet"),
        "vertical_vanes": ("fixed", "swing"), "horizontal_vanes": ("left", "right"), "min_setpoint": 17.0,
        "max_setpoint": 30.0, "setpoint_step": 0.5, "range_only": False,
    }
    base.update(kw)
    return models.CapabilityProfile(**base)


def room(heads: tuple[str, ...] = ("climate.a",), **kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "r1", "name": "Office", "heads": tuple(models.HeadConfig(h) for h in heads),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh",
        "occupied_band": (23.0, 26.0), "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0),
        "profile": profile(),
    }
    base.update(kw)
    return models.RoomConfig(**base)


def raw(entity: str, state: str, **attrs: Any) -> Any:
    return models.RawState(entity, state, attrs, T0, T0)


def head(climate: str = "climate.a", mode: str | None = "cool", setpoint: float | None = 24.0,
         fan: str | None = "auto", vane: str | None = None, low: float | None = None,
         high: float | None = None) -> Any:
    return models.HeadCommand(climate, mode, setpoint, low, high, setpoint, fan, vane)


def allowed(kind: Any = SK.COOL, heads: tuple[Any, ...] | None = None, blinds: tuple[Any, ...] = ()) -> Any:
    if heads is None:
        heads = (head(),) if kind not in (SK.OFF, SK.NONE) else ()
    return models.LimitsResult(kind, heads, blinds, (), (), False, False, False, False, (), (), (), ())


def calls(ra: Any, step: Any, live: dict[str, Any], *, now: datetime = T0, unit: str = "°C",
          vanes: bool = False) -> list[tuple[str, str, dict[str, Any]]]:
    out = ra.calls(now=now, step=step, live=live, system_unit=unit,
                   choices=models.RoomChoices(automatic_vane_control=vanes))
    return [(c.domain, c.service, dict(c.data)) for c in out]


LIVE_AS_WANTED = {"climate.a": raw("climate.a", "cool", temperature=24.0, fan_mode="auto", swing_mode="fixed")}


class Sending(unittest.TestCase):
    def test_none_sends_nothing(self) -> None:
        live = {"climate.a": raw("climate.a", "heat")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(SK.NONE), live), [])
        self.assertEqual(calls(act.RoomAct(room()), allowed(SK.NONE, heads=(head(),)), live), [])  # [D017]

    def test_off_only_where_not_off(self) -> None:
        live = {"climate.a": raw("climate.a", "cool"), "climate.b": raw("climate.b", "off")}
        self.assertEqual(calls(act.RoomAct(room(("climate.a", "climate.b"))), allowed(SK.OFF), live),
                         [("climate", "set_hvac_mode", {"entity_id": "climate.a", "hvac_mode": "off"})])

    def test_only_what_differs(self) -> None:
        self.assertEqual(calls(act.RoomAct(room()), allowed(), LIVE_AS_WANTED), [])
        live = {"climate.a": raw("climate.a", "heat", temperature=26.0, fan_mode="quiet")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(), live), [
            ("climate", "set_hvac_mode", {"entity_id": "climate.a", "hvac_mode": "cool"}),
            ("climate", "set_temperature", {"entity_id": "climate.a", "temperature": 24.0}),
            ("climate", "set_fan_mode", {"entity_id": "climate.a", "fan_mode": "auto"}),
        ])  # [D007]

    def test_setpoint_differs_by_a_step_or_more(self) -> None:
        near = {"climate.a": raw("climate.a", "cool", temperature=24.4, fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(), near), [])
        off_by_step = {"climate.a": raw("climate.a", "cool", temperature=24.5, fan_mode="auto")}
        self.assertEqual(len(calls(act.RoomAct(room()), allowed(), off_by_step)), 1)
        none = {"climate.a": raw("climate.a", "cool", fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(), none)[0][1], "set_temperature")

    def test_range(self) -> None:
        step = allowed(heads=(head(setpoint=None, low=23.0, high=25.0),))
        live = {"climate.a": raw("climate.a", "cool", target_temp_low=23.0, target_temp_high=26.0, fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(room()), step, live), [
            ("climate", "set_temperature", {"entity_id": "climate.a", "target_temp_low": 23.0,
                                            "target_temp_high": 25.0}),
        ])  # [D024]
        live = {"climate.a": raw("climate.a", "cool", target_temp_low=23.0, target_temp_high=25.0, fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(room()), step, live), [])
        half = {"climate.a": raw("climate.a", "cool", target_temp_low=23.0, fan_mode="auto")}
        self.assertEqual(len(calls(act.RoomAct(room()), step, half)), 1)

    def test_vane_only_under_automatic_vane_control(self) -> None:
        step = allowed(heads=(head(vane="swing"),))
        self.assertEqual(calls(act.RoomAct(room()), step, LIVE_AS_WANTED), [])
        self.assertEqual(calls(act.RoomAct(room()), step, LIVE_AS_WANTED, vanes=True),
                         [("climate", "set_swing_mode", {"entity_id": "climate.a", "swing_mode": "swing"})])
        self.assertEqual(calls(act.RoomAct(room()), allowed(heads=(head(vane="fixed"),)), LIVE_AS_WANTED, vanes=True),
                         [])

    def test_a_unit_not_reporting_is_skipped(self) -> None:
        live = {"climate.a": raw("climate.a", "unavailable")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(), live), [])
        self.assertEqual(calls(act.RoomAct(room()), allowed(SK.OFF), live), [])
        self.assertEqual(calls(act.RoomAct(room()), allowed(), {}), [])

    def test_every_head(self) -> None:
        r = room(("climate.a", "climate.b"))
        step = allowed(heads=(head("climate.a"), head("climate.b", setpoint=25.0)))
        live = {"climate.a": raw("climate.a", "off"), "climate.b": raw("climate.b", "off")}
        sent = calls(act.RoomAct(r), step, live)
        self.assertEqual([(c[1], c[2]["entity_id"]) for c in sent],
                         [("set_hvac_mode", "climate.a"), ("set_temperature", "climate.a"),
                          ("set_fan_mode", "climate.a"), ("set_hvac_mode", "climate.b"),
                          ("set_temperature", "climate.b"), ("set_fan_mode", "climate.b")])  # [D020]

    def test_not_sent_again_while_the_unit_has_yet_to_report(self) -> None:
        ra = act.RoomAct(room())
        live = {"climate.a": raw("climate.a", "heat", temperature=24.0, fan_mode="auto")}
        self.assertEqual(len(calls(ra, allowed(), live, now=T0)), 1)
        self.assertEqual(calls(ra, allowed(), live, now=T0 + 29 * SEC), [])
        self.assertEqual(len(calls(ra, allowed(), live, now=T0 + 30 * SEC)), 1)  # re-asserted [D007]
        # a different value is sent at once
        self.assertEqual(len(calls(ra, allowed(kind=SK.HEAT, heads=(head(mode="dry"),)), live, now=T0 + 31 * SEC)), 1)

    def test_blinds(self) -> None:
        blinds = (models.BlindCommand("cover.a", 0.0), models.BlindCommand("cover.b", 0.0),
                  models.BlindCommand("cover.c", 100.0))
        live = {**LIVE_AS_WANTED, "cover.a": raw("cover.a", "open", current_position=50),
                "cover.b": raw("cover.b", "closed", current_position=5), "cover.c": raw("cover.c", "unavailable")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(blinds=blinds), live),
                         [("cover", "set_cover_position", {"entity_id": "cover.a", "position": 0})])  # [Set]
        unknown = {**LIVE_AS_WANTED, "cover.a": raw("cover.a", "open")}
        self.assertEqual(calls(act.RoomAct(room()), allowed(blinds=blinds[:1]), unknown)[0][1], "set_cover_position")


class Units(unittest.TestCase):
    def test_fahrenheit_system(self) -> None:
        # 24 C is 75.2 F; the unit steps in whole F, 5/9 C, so 24.4444 C is 76 F
        r = room(profile=profile(setpoint_step=5.0 / 9.0, min_setpoint=16.1111, max_setpoint=30.0))
        step = allowed(heads=(head(setpoint=(76.0 - 32.0) * 5.0 / 9.0),))
        live = {"climate.a": raw("climate.a", "cool", temperature=74.0, fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(r), step, live, unit="°F")[0][2], {"entity_id": "climate.a",
                                                                                "temperature": 76.0})
        same = {"climate.a": raw("climate.a", "cool", temperature=76.0, fan_mode="auto")}
        self.assertEqual(calls(act.RoomAct(r), step, same, unit="°F"), [])

    def test_to_system(self) -> None:
        self.assertEqual(act.to_system(24.0, "°C", 0.5), 24.0)
        self.assertEqual(act.to_system(24.04, "°C", 0.1), 24.0)
        self.assertEqual(act.to_system(24.0, "°C", None), 24.0)
        self.assertEqual(act.to_system(24.06, "°C", None), 24.1)  # no step advertised: tenths [D051]
        self.assertEqual(act.to_system(24.0, "K", 1.0), 297.15)
        self.assertEqual(act.to_system(24.3, "°C", 0.5), 24.5)
        self.assertEqual(act.to_system(24.0, "°F", 5.0 / 9.0), 75.0)

    def test_what_is_sent_converts_onto_the_units_own_grid_never_below(self) -> None:
        """Home Assistant converts with no rounding (C = (F - 32) / 1.8, F = C
        x 1.8 + 32) [HA]: what lands on a unit that truncates must not be a
        hair under its degree [D051]."""
        for n in range(-40, 160):
            for step in (5.0 / 9.0, 5.0 / 18.0):  # a unit in whole or half F on a C system
                f = act.to_system(n * step, "°C", step) * 1.8 + 32.0
                self.assertGreaterEqual(f, round(32.0 + n * step * 1.8, 6))
                self.assertAlmostEqual(f, 32.0 + n * step * 1.8, places=9)
            for step in (0.1, 0.5, 1.0):  # a unit in C on a F system
                c = (act.to_system(n * step + 20.0, "°F", step) - 32.0) / 1.8
                want = round(round((n * step + 20.0) / step) * step, 6)
                self.assertGreaterEqual(c, want)
                self.assertAlmostEqual(c, want, places=9)


F22 = act.to_system(22.0, "°F", 0.5)  # 22 C for a C unit on a F system: 71.6 F, a float above


class UserCommands(unittest.TestCase):
    def test_refused_while_automatic(self) -> None:
        with self.assertRaises(act.RefusedCommand) as cm:
            act.user_calls(room(), models.RoomChoices(), act.UserCommand(hvac_mode="heat"), "°C")
        self.assertEqual((cm.exception.key, cm.exception.placeholders), ("automatic_control_on", {"room": "Office"}))
        vanes_on = models.RoomChoices(automatic_control=False, automatic_vane_control=True)
        with self.assertRaises(act.RefusedCommand) as cm:
            act.user_calls(room(), vanes_on, act.UserCommand(vertical_vane="swing"), "°C")
        self.assertEqual(cm.exception.key, "automatic_vane_control_on")
        with self.assertRaises(act.RefusedCommand):
            act.user_calls(room(), vanes_on, act.UserCommand(horizontal_vane="left"), "°C")

    def test_vanes_are_the_users_while_vane_control_is_off(self) -> None:
        sent = act.user_calls(room(), models.RoomChoices(), act.UserCommand(horizontal_vane="left"), "°C")
        self.assertEqual([(c.service, dict(c.data)) for c in sent],
                         [("set_swing_horizontal_mode", {"entity_id": "climate.a", "swing_horizontal_mode": "left"})])

    def test_only_what_the_unit_offers(self) -> None:
        off = models.RoomChoices(automatic_control=False)
        for cmd, control in ((act.UserCommand(hvac_mode="auto"), "mode"),
                             (act.UserCommand(fan_mode="turbo"), "fan speed"),
                             (act.UserCommand(vertical_vane="up"), "vertical vane"),
                             (act.UserCommand(horizontal_vane="up"), "horizontal vane"),
                             (act.UserCommand(setpoint=31.0), "setpoint"),
                             (act.UserCommand(setpoint=16.5), "setpoint")):
            with self.assertRaises(act.RefusedCommand) as cm:
                act.user_calls(room(), off, cmd, "°C")
            self.assertEqual((cm.exception.key, cm.exception.placeholders["control"]), ("not_offered", control))
        with self.assertRaises(act.RefusedCommand) as cm:
            act.user_calls(room(profile=None), off, act.UserCommand(fan_mode="auto"), "°C")
        self.assertEqual(cm.exception.key, "profile_not_read")

    def test_sent_to_every_head(self) -> None:
        off = models.RoomChoices(automatic_control=False)
        sent = act.user_calls(room(("climate.a", "climate.b")), off,
                              act.UserCommand(hvac_mode="heat", setpoint=22.0, fan_mode="quiet"), "°F")
        self.assertEqual([(c.service, dict(c.data)) for c in sent], [
            ("set_hvac_mode", {"entity_id": "climate.a", "hvac_mode": "heat"}),
            ("set_temperature", {"entity_id": "climate.a", "temperature": F22}),
            ("set_fan_mode", {"entity_id": "climate.a", "fan_mode": "quiet"}),
            ("set_hvac_mode", {"entity_id": "climate.b", "hvac_mode": "heat"}),
            ("set_temperature", {"entity_id": "climate.b", "temperature": F22}),
            ("set_fan_mode", {"entity_id": "climate.b", "fan_mode": "quiet"}),
        ])  # 22 C, on the unit's 0.5 C step, is 71.6 F
        ranged = act.user_calls(room(profile=profile(range_only=True)), off, act.UserCommand(setpoint=22.0), "°C")
        self.assertEqual(dict(ranged[0].data), {"entity_id": "climate.a", "target_temp_low": 21.0,
                                                "target_temp_high": 23.0})  # [D024]


class Log(unittest.TestCase):
    def record(self, ok: bool = True, user: bool = False) -> Any:
        return models.CommandRecord(T0, "r1", "climate.a", "set_temperature", {"entity_id": "climate.a",
                                    "temperature": 24.0}, 2.0, 25.1, 24.5, user, ok, None if ok else "boom")

    def test_keeps_the_last_200(self) -> None:
        log = act.CommandLog()
        for _ in range(205):
            log.add(self.record())
        self.assertEqual(len(log.records), 200)  # [D052]

    def test_logged_at_info_with_its_context(self) -> None:
        with self.assertLogs("custom_components.abode_hvac_coordinator", logging.INFO) as cm:
            act.CommandLog().add(self.record())
            act.CommandLog().add(self.record(user=True))
            act.CommandLog().add(self.record(ok=False))
        self.assertEqual([r.levelno for r in cm.records], [logging.INFO, logging.INFO, logging.WARNING])
        self.assertIn("set_temperature {'temperature': 24.0}; rate needed 2.00 C/h, room 25.10 C, centre 24.50 C",
                      cm.output[0])
        self.assertIn("(by the user)", cm.output[1])
        self.assertIn("failed: boom", cm.output[2])


if __name__ == "__main__":
    unittest.main()


class TopOfTheRange(unittest.TestCase):
    """At the top of a unit's range what is sent converts to no more than
    its maximum; Home Assistant refuses even a float above [HA][D051]."""

    def test_every_half_degree_maximum_lands_at_or_under_it(self) -> None:
        for half in range(40, 70):  # maximums 20.0 .. 34.5 C, a C unit in a F house
            top = half / 2.0
            sent = act.to_system(top, "°F", 0.5, top)
            self.assertLessEqual((sent - 32.0) / 1.8, top)
            self.assertAlmostEqual((sent - 32.0) / 1.8, top, places=9)
        for f in range(70, 95):  # maximums in whole F, a F unit in a C house
            top = (f - 32.0) / 1.8
            sent = act.to_system(top, "°C", 5.0 / 9.0, top)
            self.assertLessEqual(sent * 1.8 + 32.0, f)

    def test_act_and_the_users_command_pass_the_maximum(self) -> None:
        r = room(profile=profile(max_setpoint=31.0))
        live = {"climate.a": raw("climate.a", "heat", temperature=60.0, fan_mode="auto")}
        sent = [d["temperature"] for _, s_, d in calls(act.RoomAct(r), allowed(SK.HEAT, (head(mode="heat", setpoint=31.0),)),
                                                       live, unit="°F") if s_ == "set_temperature"]
        self.assertTrue(sent and (sent[0] - 32.0) / 1.8 <= 31.0)
        user = act.user_calls(r, models.RoomChoices(automatic_control=False), act.UserCommand(setpoint=31.0), "°F")
        sent = [c.data["temperature"] for c in user if c.service == "set_temperature"]
        self.assertTrue(sent and (sent[0] - 32.0) / 1.8 <= 31.0)


class LastSent(unittest.TestCase):
    def test_a_value_other_than_the_last_sent_is_sent(self) -> None:
        """A unit still showing about the old value, within a step of the new:
        what was last sent says it is not yet as wanted [D007]."""
        ra = act.RoomAct(room())
        live = {"climate.a": raw("climate.a", "cool", temperature=20.0, fan_mode="auto")}
        first = calls(ra, allowed(SK.COOL, (head(setpoint=24.5),)), live)
        self.assertEqual([d["temperature"] for _, s_, d in first if s_ == "set_temperature"], [24.5])
        live = {"climate.a": raw("climate.a", "cool", temperature=24.3, fan_mode="auto")}
        later = calls(ra, allowed(SK.COOL, (head(setpoint=24.0),)), live, now=T0 + 60 * SEC)
        self.assertEqual([d["temperature"] for _, s_, d in later if s_ == "set_temperature"], [24.0])


class NotRunMeansForget(unittest.TestCase):
    def test_what_was_sent_is_forgotten_while_the_room_is_not_run(self) -> None:
        """While the room is not run the units may be changed by hand, so what
        was last sent no longer says what they hold [D007]."""
        ra = act.RoomAct(room())
        live = {"climate.a": raw("climate.a", "cool", temperature=20.0, fan_mode="auto")}
        calls(ra, allowed(SK.COOL, (head(setpoint=24.5),)), live)
        self.assertIn(("climate.a", "temperature"), ra._sent)
        calls(ra, allowed(SK.NONE), live, now=T0 + 60 * SEC)
        self.assertEqual(ra._sent, {})
