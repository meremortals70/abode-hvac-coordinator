"""delivery.py: what to ask of the blinds and the unit (Module 6, Section 4.6)."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

delivery = load("delivery")
models = load("models")
filt = load("model.filter")
psychro = load("psychro")
SK = models.StepKind
VT = models.ValueTag

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def profile(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "hvac_modes": ("off", "cool", "heat", "dry", "fan_only"),
        "fan_modes": ("Low", "Auto", "High", "Quiet"),
        "vertical_vanes": ("Swing", "Fixed"),
        "horizontal_vanes": (),
        "min_setpoint": 17.0,
        "max_setpoint": 30.0,
        "setpoint_step": 0.1,
        "range_only": False,
    }
    base.update(kw)
    return models.CapabilityProfile(**base)


def room(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "r1", "name": "Office", "heads": (models.HeadConfig("climate.a"),),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh",
        "occupied_band": (23.0, 26.0), "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0),
        "profile": profile(),
    }
    base.update(kw)
    return models.RoomConfig(**base)


def head(climate: str = "climate.a", own: float | None = 25.0, available: bool = True) -> Any:
    return models.HeadSnapshot(climate, climate, available, "cool", 24.0, None, own, None, None, None, None,
                               models.Reading(None, VT.MISSING), models.CompressorState.RUNNING,
                               models.CompressorSource.MODE)


def snap(heads: tuple[Any, ...] | None = None, *, t: float = 25.0, rh: float = 50.0, out_t: float = 30.0,
         out_rh: float = 50.0, blinds: tuple[Any, ...] = ()) -> Any:
    return dataclasses.replace(
        SimRoom().snapshot(),
        heads=heads if heads is not None else (head(),),
        room_temperature=models.Reading(t, VT.MEASURED),
        room_humidity=models.Reading(rh, VT.MEASURED),
        outdoor_temperature=models.Reading(out_t, VT.MEASURED),
        outdoor_humidity=models.Reading(out_rh, VT.MEASURED),
        outdoor_wind_ms=models.Reading(0.0, VT.MEASURED),
        blind_positions=blinds,
    )


def ctl(kind: Any, rate: float = 0.0, blinds: bool | None = None) -> Any:
    return models.ControlResult(kind, rate, blinds, models.Controller.PLAN_PID, None, None, "the plan")


def comf(off: bool = False, occupied: bool = True) -> Any:
    return models.ComfortResult(models.BandRule.OCCUPIED, off, "the occupied band" if not off else "the room is empty",
                                occupied, False, False, False, (), None, None)


def go(control: Any, *, rd: Any = None, s: Any = None, c: Any = None, vanes: bool = False,
       heat: Any = "default", u_min: float = 1.0, now: datetime = T0) -> Any:
    rd = rd or delivery.RoomDelivery(room())
    hf = filt.HeatFilter.start(25.0) if heat == "default" else heat
    # the starting curve: every step ln 1, so the rate at efforts
    # 0.5, 1, 1.5, 2, 3, 4 C is 1, 2, 3, 4, 5, 6 C/h
    return rd.deliver(now=now, control=control, snapshot=s or snap(), comfort=c or comf(),
                      choices=models.RoomChoices(automatic_vane_control=vanes), heat=hf, u_min=u_min)


class Passing(unittest.TestCase):
    def test_comfort_off_is_off(self) -> None:
        r = go(ctl(SK.COOL, 2.0), c=comf(off=True))
        self.assertEqual((r.kind, r.heads), (SK.OFF, ()))
        self.assertIn("empty", r.reasons[0])

    def test_off_and_none_pass_through(self) -> None:
        self.assertIs(go(ctl(SK.OFF)).kind, SK.OFF)
        self.assertIs(go(ctl(SK.NONE)).kind, SK.NONE)

    def test_no_profile_sends_nothing(self) -> None:
        r = go(ctl(SK.COOL, 2.0), rd=delivery.RoomDelivery(room(profile=None)))
        self.assertIs(r.kind, SK.NONE)


class RateToSetpoint(unittest.TestCase):
    def test_cool(self) -> None:
        # 2 C/h is effort 1 C: setpoint = the unit's reading - 1 [D095]
        r = go(ctl(SK.COOL, 2.0))
        h = r.heads[0]
        self.assertEqual((r.kind, h.mode, h.setpoint, h.wanted), (SK.COOL, "cool", 24.0, 24.0))
        self.assertEqual(h.fan_mode, "Auto")  # the unit's own spelling
        self.assertIsNone(h.vertical_vane)

    def test_heat(self) -> None:
        h = go(ctl(SK.HEAT, -2.0)).heads[0]
        self.assertEqual((h.mode, h.setpoint), ("heat", 26.0))  # reading + effort 1 C [D095]

    def test_between_curve_points(self) -> None:
        # 2.5 C/h lies halfway between efforts 1 and 1.5 C
        self.assertAlmostEqual(go(ctl(SK.COOL, 2.5)).heads[0].setpoint, 23.8)

    def test_dry_has_the_setpoint_cool_would_have_at_u_min(self) -> None:
        h = go(ctl(SK.DRY, 0.0), u_min=2.0).heads[0]
        self.assertEqual((h.mode, h.setpoint), ("dry", 24.0))  # [D078]

    def test_beyond_the_curve_asks_for_the_top(self) -> None:
        h = go(ctl(SK.COOL, 50.0), s=snap((head(own=26.0),))).heads[0]
        self.assertEqual(h.setpoint, 22.0)  # top effort 4 C [D056]

    def test_kept_within_the_range(self) -> None:
        h = go(ctl(SK.COOL, 50.0), s=snap((head(own=20.0),))).heads[0]
        self.assertEqual((h.wanted, h.setpoint), (17.0, 17.0))  # [D051]
        h = go(ctl(SK.HEAT, -50.0), s=snap((head(own=28.0),))).heads[0]
        self.assertEqual(h.setpoint, 30.0)

    def test_each_head_from_its_own_reading(self) -> None:
        r = go(ctl(SK.COOL, 2.0), s=snap((head("climate.a", 25.0), head("climate.b", 26.0))))
        self.assertEqual([(h.climate, h.setpoint) for h in r.heads], [("climate.a", 24.0), ("climate.b", 25.0)])

    def test_range_only_unit(self) -> None:
        rd = delivery.RoomDelivery(room(profile=profile(range_only=True)))
        h = go(ctl(SK.COOL, 2.0), rd=rd).heads[0]
        self.assertEqual((h.setpoint, h.setpoint_low, h.setpoint_high), (None, 23.0, 25.0))  # [D024]

    def test_what_cannot_be_worked_out_is_left_alone(self) -> None:
        self.assertIs(go(ctl(SK.COOL, 2.0), heat=None).kind, SK.NONE)
        r = go(ctl(SK.COOL, 2.0), s=snap((head(own=None),)))
        self.assertIs(r.kind, SK.NONE)
        self.assertIn("own reading is missing", r.reasons[0])
        self.assertIs(go(ctl(SK.COOL, 2.0), s=snap((head(available=False),))).kind, SK.NONE)
        rd = delivery.RoomDelivery(room(profile=profile(hvac_modes=("off", "heat", "fan_only"))))
        r = go(ctl(SK.COOL, 2.0), rd=rd)
        self.assertEqual((r.kind, r.reasons[0]), (SK.NONE, "The unit does not offer cool."))  # [D024]


class Rest(unittest.TestCase):
    def test_fan_only_at_the_quietest_fan(self) -> None:
        h = go(ctl(SK.REST)).heads[0]
        self.assertEqual((h.mode, h.setpoint, h.fan_mode), ("fan_only", None, "Quiet"))  # [D057]

    def test_rest_is_offered_for_limits_to_fall_back_on(self) -> None:
        r = go(ctl(SK.COOL, 2.0))
        self.assertEqual([(h.mode, h.fan_mode, h.setpoint) for h in r.rest_heads], [("fan_only", "Quiet", None)])
        rd = delivery.RoomDelivery(room(profile=profile(hvac_modes=("off", "cool"))))
        self.assertEqual(go(ctl(SK.COOL, 2.0), rd=rd).rest_heads, ())

    def test_no_fan_only_mode_rests_off(self) -> None:
        rd = delivery.RoomDelivery(room(profile=profile(hvac_modes=("off", "cool"))))
        r = go(ctl(SK.REST), rd=rd)
        self.assertEqual((r.kind, r.heads), (SK.OFF, ()))
        self.assertIn("no fan-only", r.reasons[0])


class FansAndVanes(unittest.TestCase):
    def test_pick_is_first_preferred_offered(self) -> None:
        self.assertEqual(delivery.pick(("High", "MEDIUM", "auto"), ("auto", "medium")), "auto")
        self.assertEqual(delivery.pick(("High", "MEDIUM"), ("auto", "medium")), "MEDIUM")
        self.assertIsNone(delivery.pick(("Turbo",), ("auto",)))

    def test_vane_only_under_automatic_vane_control(self) -> None:
        self.assertIsNone(go(ctl(SK.COOL, 2.0)).heads[0].vertical_vane)
        self.assertEqual(go(ctl(SK.COOL, 2.0), vanes=True).heads[0].vertical_vane, "Fixed")  # [D049]
        self.assertEqual(go(ctl(SK.REST), vanes=True).heads[0].vertical_vane, "Fixed")
        self.assertIsNone(go(ctl(SK.REST)).heads[0].vertical_vane)
        self.assertIsNone(go(ctl(SK.COOL, 2.0)).rest_heads[0].vertical_vane)


class WholeDegrees(unittest.TestCase):
    def setUp(self) -> None:
        self.rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=1.0)))

    def at(self, minutes: float, own: float) -> Any:
        # rate 2 C/h: effort 1 C, so wanted = own - 1
        return go(ctl(SK.COOL, 2.0), rd=self.rd, s=snap((head(own=own),)), now=T0 + minutes * MIN).heads[0]

    def test_average_is_the_wanted_setpoint(self) -> None:
        # f = 0.5: high for 15 minutes of each 30, low for the rest
        sent = [self.at(m, 25.5).setpoint for m in range(60)]
        self.assertEqual(sent[:15], [25.0] * 15)
        self.assertEqual(sent[15:30], [24.0] * 15)
        self.assertEqual(sent[30:45], [25.0] * 15)
        self.assertAlmostEqual(sum(sent) / 60, 24.5)
        self.assertEqual(self.at(60, 25.5).wanted, 24.5)

    def test_short_shares_hold_the_nearer_value(self) -> None:
        self.assertEqual({self.at(m, 25.2).setpoint for m in range(30)}, {24.0})  # f x 30 = 6 min
        rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=1.0)))
        self.rd = rd
        self.assertEqual({self.at(m, 25.8).setpoint for m in range(30)}, {25.0})  # (1 - f) x 30 = 6 min

    def test_each_value_held_ten_minutes(self) -> None:
        # f = 0.4: high to 12 minutes, then low
        self.assertEqual(self.at(0, 25.4).setpoint, 25.0)
        self.assertEqual(self.at(12, 25.4).setpoint, 24.0)
        # the wanted setpoint moves so high is due now; low has stood 8 minutes
        self.assertEqual(self.at(20, 25.8).setpoint, 24.0)
        self.assertEqual(self.at(22, 25.8).setpoint, 25.0)

    def test_a_new_pair_is_held_too(self) -> None:
        """Every value sent stands 10 minutes, whichever pair the wanted
        setpoint moves to. The released hold covered only the same pair, so
        the living room went 20, 21, 19 within two minutes (8 November
        2026)."""
        self.assertEqual(self.at(0, 25.4).setpoint, 25.0)
        self.assertEqual(self.at(3, 27.5).setpoint, 25.0)
        self.assertEqual(self.at(9, 21.2).setpoint, 25.0)
        self.assertNotEqual(self.at(10, 27.5).setpoint, 25.0)

    def test_a_rest_starts_a_new_hold(self) -> None:
        """No setpoint goes out while the unit rests, so the next run sends
        its own value at once."""
        self.assertEqual(self.at(0, 25.4).setpoint, 25.0)
        go(ctl(SK.REST, 0.0), rd=self.rd, s=snap((head(own=27.0),)), now=T0 + timedelta(minutes=2))
        self.assertEqual(self.at(4, 23.0).setpoint, 22.0)

    def test_never_outside_the_range(self) -> None:
        rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=1.0, max_setpoint=29.0)))
        self.rd = rd
        h = go(ctl(SK.HEAT, -1.5), rd=rd, s=snap((head(own=29.5),)), now=T0).heads[0]
        self.assertEqual((h.wanted, h.setpoint), (29.0, 29.0))  # [D051]

    def test_a_high_above_the_top_is_never_sent(self) -> None:
        # top 28.5 C: the pair is 28 and 29, and 29 is above the top [D051]
        self.rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=1.0, max_setpoint=28.5)))
        sent = {go(ctl(SK.HEAT, -1.5), rd=self.rd, s=snap((head(own=27.65),)), now=T0 + m * MIN).heads[0].setpoint
                for m in range(30)}
        self.assertTrue(all(17.0 <= v <= 28.5 for v in sent), sent)

    def test_any_coarse_step_alternates_and_tenths_are_rounded(self) -> None:
        self.rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=0.5)))
        sent = [self.at(m, 25.25).setpoint for m in range(30)]  # wanted 24.25
        self.assertEqual(sent[:15], [24.5] * 15)
        self.assertEqual(sent[15:], [24.0] * 15)
        self.rd = delivery.RoomDelivery(room(profile=profile(setpoint_step=0.1)))
        self.assertAlmostEqual(self.at(0, 25.47).setpoint, 24.5)
        self.assertAlmostEqual(self.at(1, 25.43).setpoint, 24.4)


class Blinds(unittest.TestCase):
    def blinds(self) -> tuple[Any, ...]:
        return (models.Reading(0.5, VT.MEASURED, "cover.a"), models.Reading(0.03, VT.MEASURED, "cover.b"),
                models.Reading(None, VT.MISSING, "cover.c"))

    def test_moved_to_the_plans_position(self) -> None:
        r = go(ctl(SK.REST, blinds=True), s=snap(blinds=self.blinds()))
        self.assertEqual(r.blinds, (models.BlindCommand("cover.a", 0.0),))  # cover.b within 5 points
        self.assertEqual(r.blinds_left_alone, ("cover.c",))  # [D022]
        r = go(ctl(SK.REST, blinds=False), s=snap(blinds=self.blinds()))
        self.assertEqual(r.blinds, (models.BlindCommand("cover.a", 100.0), models.BlindCommand("cover.b", 100.0)))

    def test_five_points_exactly_is_not_moved(self) -> None:
        r = go(ctl(SK.REST, blinds=True), s=snap(blinds=(models.Reading(0.05, VT.MEASURED, "cover.a"),)))
        self.assertEqual(r.blinds, ())

    def test_left_alone_outside_the_plan_or_when_not_allowed(self) -> None:
        self.assertEqual(go(ctl(SK.REST, blinds=None), s=snap(blinds=self.blinds())).blinds, ())
        rd = delivery.RoomDelivery(room(allow_blind_control=False))
        self.assertEqual(go(ctl(SK.REST, blinds=True), rd=rd, s=snap(blinds=self.blinds())).blinds, ())


class Advice(unittest.TestCase):
    def test_free_cooling(self) -> None:
        cool_outside = snap(t=27.0, rh=60.0, out_t=18.0, out_rh=50.0)
        r = go(ctl(SK.COOL, 2.0), s=cool_outside)
        self.assertTrue(r.free_cooling_advised)  # [D073]
        r = go(ctl(SK.COOL, 2.0), s=cool_outside, c=comf(occupied=False))
        self.assertFalse(r.free_cooling_advised)
        self.assertIn("empty", r.free_cooling_reason)
        self.assertFalse(go(ctl(SK.REST), s=cool_outside).free_cooling_advised)
        self.assertFalse(go(ctl(SK.COOL, 2.0), s=snap(t=27.0, rh=60.0, out_t=32.0)).free_cooling_advised)

    def test_condensation(self) -> None:
        humid = snap(t=25.0, rh=90.0)  # dew point 23.2 C
        self.assertLess(24.0 - psychro.dew_point(25.0, 90.0), 2.5)
        r = go(ctl(SK.COOL, 2.0), s=humid)
        self.assertTrue(r.condensation_risk)
        self.assertIn("dew point", r.reasons[0])
        self.assertFalse(go(ctl(SK.COOL, 2.0), s=snap(t=25.0, rh=50.0)).condensation_risk)
        self.assertFalse(go(ctl(SK.REST), s=humid).condensation_risk)
        # heating is never at risk, even with its setpoint near the dew point
        self.assertFalse(go(ctl(SK.HEAT, -1.0), s=humid).condensation_risk)  # setpoint 25.5

    def test_condensation_uses_the_lowest_setpoint_sent(self) -> None:
        # dew point 22.3 C at 85 %: 24 is 1.7 C above it, 25 would be 2.7
        two = snap((head("climate.a", 25.0), head("climate.b", 26.0)), t=25.0, rh=85.0)
        self.assertTrue(go(ctl(SK.COOL, 2.0), s=two).condensation_risk)
        self.assertFalse(go(ctl(SK.COOL, 2.0), s=snap((head(own=26.0),), t=25.0, rh=85.0)).condensation_risk)

    def test_condensation_for_a_range_only_unit(self) -> None:
        rd = delivery.RoomDelivery(room(profile=profile(range_only=True)))
        self.assertTrue(go(ctl(SK.COOL, 2.0), rd=rd, s=snap(t=25.0, rh=90.0)).condensation_risk)


if __name__ == "__main__":
    unittest.main()
