"""comfort.py: occupancy, the band and its centre (Module 3)."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

comfort = load("comfort")
models = load("models")
hci = load("hci")
evidence = load("evidence")
VT = models.ValueTag
BR = models.BandRule
CM = models.ComfortMode

T0 = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)


def room(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "r1",
        "name": "Living",
        "heads": (models.HeadConfig("climate.living"),),
        "temperature_sensor": "sensor.t",
        "humidity_sensor": "sensor.rh",
        "occupied_band": (24.0, 27.0),
        "sleep_band": (21.0, 24.0),
        "precool_band": (23.0, 26.0),
        "presence_sensor": "binary_sensor.p",
        "sleep_schedule": "schedule.sleep",
    }
    base.update(kw)
    return models.RoomConfig(**base)


def snap(presence: bool | None = True, since: datetime | None = None, *, t: float = 25.0, rh: float = 60.0,
         schedule: bool | None = None) -> Any:
    base = SimRoom().snapshot()
    pres = models.Flag(presence, VT.MEASURED if presence is not None else VT.MISSING, "binary_sensor.p",
                       since or T0 - timedelta(hours=1))
    def flag(v: bool | None, e: str) -> Any:
        return None if v is None else models.Flag(v, VT.MEASURED, e, T0)

    return dataclasses.replace(
        base,
        presence=pres,
        room_temperature=models.Reading(t, VT.MEASURED),
        room_humidity=models.Reading(rh, VT.MEASURED),
        sleep_schedule=flag(schedule, "schedule.sleep"),
    )


CH = models.RoomChoices()


class Occupancy(unittest.TestCase):
    def test_no_presence_sensor_counts_as_occupied(self) -> None:
        rc = comfort.RoomComfort(room(presence_sensor=None))
        r = rc.evaluate(snap(None), CH, T0)
        self.assertEqual((r.rule, r.occupied, r.off), (BR.OCCUPIED, True, False))

    def test_graces(self) -> None:
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(False, T0 - timedelta(minutes=10)), CH, T0)
        self.assertEqual((r.rule, r.off), (BR.UNOCCUPIED, True))
        r = rc.evaluate(snap(True, T0 - timedelta(seconds=119)), CH, T0)
        self.assertFalse(r.occupied)  # inside the occupied grace
        self.assertEqual(r.grace, "occupied")
        r = rc.evaluate(snap(True, T0 - timedelta(seconds=120)), CH, T0)
        self.assertTrue(r.occupied)
        self.assertIsNone(r.grace)
        r = rc.evaluate(snap(False, T0 - timedelta(minutes=9, seconds=59)), CH, T0)
        self.assertTrue(r.occupied)  # inside the empty grace
        self.assertEqual(r.grace, "empty")
        r = rc.evaluate(snap(None), CH, T0)
        self.assertTrue(r.occupied)  # unknown keeps the last answer

    def test_the_mode_sensor_names_the_grace(self) -> None:
        """D107: the mode sensor says which grace the room is in, not the old
        answer the grace is holding. Before this the office read `unoccupied`
        for two minutes after someone walked in, and the user set it to
        manual each time."""
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(False, T0 - timedelta(minutes=10)), CH, T0)
        self.assertEqual(evidence.mode_state(None, r).value, "unoccupied")
        r = rc.evaluate(snap(True, T0 - timedelta(seconds=119)), CH, T0)
        self.assertEqual(evidence.mode_state(None, r).value, "occupied_grace")
        r = rc.evaluate(snap(True, T0 - timedelta(seconds=120)), CH, T0)
        self.assertEqual(evidence.mode_state(None, r).value, "occupied")
        r = rc.evaluate(snap(False, T0 - timedelta(minutes=9, seconds=59)), CH, T0)
        self.assertEqual(evidence.mode_state(None, r).value, "empty_grace")

    def test_a_window_open_shows_the_opening_grace(self) -> None:
        """D107: a door or window open and the unit still inside its opening
        grace is named, not left as the band rule."""
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(True, T0 - timedelta(minutes=5)), CH, T0)  # held: occupied
        self.assertEqual(evidence.mode_state(None, r).value, "occupied")
        limits = models.LimitsResult(
            kind=models.StepKind.REST, heads=(), blinds=(), warnings=(), changed=(),
            not_delivered=False, held_off=False, lockout=False, automatic_off=False,
            constraints=(), unknown_constraints=(), missing=(), reasons=(), opening_grace=True,
        )
        self.assertEqual(evidence.mode_state(limits, r).value, "opening_grace")

    def test_first_answer_fails_toward_comfort(self) -> None:
        rc = comfort.RoomComfort(room())
        self.assertTrue(rc.evaluate(snap(None), CH, T0).occupied)

    def test_memory(self) -> None:
        rc = comfort.RoomComfort(room())
        rc.evaluate(snap(False, T0 - timedelta(minutes=30)), CH, T0)
        again = comfort.RoomComfort(room())
        again.restore(rc.as_dict())
        self.assertFalse(again.evaluate(snap(None), CH, T0).occupied)


class Leave(unittest.TestCase):
    def test_leave_turns_the_room_off_until_someone_comes_back(self) -> None:
        rc = comfort.RoomComfort(room())
        ch = models.RoomChoices(leave_pressed_at=T0)
        r = rc.evaluate(snap(True, T0 - timedelta(hours=2)), ch, T0)
        self.assertEqual((r.rule, r.off, r.clear_leave), (BR.LEFT, True, False))
        r = rc.evaluate(snap(False, T0 + timedelta(minutes=1)), ch, T0 + timedelta(minutes=2))
        self.assertEqual(r.rule, BR.LEFT)
        r = rc.evaluate(snap(True, T0 + timedelta(minutes=30)), ch, T0 + timedelta(minutes=31))
        self.assertTrue(r.clear_leave)
        self.assertNotEqual(r.rule, BR.LEFT)

    def test_presence_from_before_leave_does_not_undo_it(self) -> None:
        rc = comfort.RoomComfort(room())
        ch = models.RoomChoices(leave_pressed_at=T0)
        rc.evaluate(snap(False, T0 - timedelta(minutes=5)), ch, T0)
        r = rc.evaluate(snap(True, T0 - timedelta(minutes=1)), ch, T0 + timedelta(minutes=1))
        self.assertFalse(r.clear_leave)

    def test_undone_only_after_an_empty_reading(self) -> None:
        rc = comfort.RoomComfort(room())
        ch = models.RoomChoices(leave_pressed_at=T0)
        rc.evaluate(snap(None), ch, T0)
        r = rc.evaluate(snap(True, T0 + timedelta(minutes=5)), ch, T0 + timedelta(minutes=6))
        self.assertFalse(r.clear_leave)

    def test_user_actions_undo_leave(self) -> None:
        ch = models.RoomChoices(leave_pressed_at=T0)
        for action in (comfort.UserAction.AUTOMATIC_CONTROL, comfort.UserAction.COMFORT_MODE,
                       comfort.UserAction.PRECOOL):
            self.assertIsNone(comfort.choices_after_user_action(ch, action).leave_pressed_at)
        self.assertEqual(comfort.choices_after_user_action(ch, comfort.UserAction.OTHER), ch)


class Precool(unittest.TestCase):
    def test_runs_an_empty_room_to_its_precool_band(self) -> None:
        rc = comfort.RoomComfort(room())
        ready = T0 + timedelta(hours=2)
        r = rc.evaluate(snap(False, T0 - timedelta(hours=1)), models.RoomChoices(precool_ready_by=ready), T0)
        self.assertEqual((r.rule, r.off, r.precool_on), (BR.PRECOOL, False, True))
        self.assertEqual((r.points[0].low, r.points[0].high), (23.0, 26.0))
        self.assertFalse(r.points[22].required)  # T0 + 115 minutes
        self.assertTrue(r.points[23].required)  # T0 + 120 minutes
        self.assertEqual(len(r.points), 72)

    def test_ends(self) -> None:
        ready = T0 - timedelta(minutes=10)
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(True, T0 - timedelta(hours=1)), models.RoomChoices(precool_ready_by=ready), T0)
        self.assertEqual((r.end_precool, r.rule), ("the room counts as occupied", BR.OCCUPIED))
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(False), models.RoomChoices(precool_ready_by=ready, leave_pressed_at=T0), T0)
        self.assertEqual(r.end_precool, "Leave was pressed")
        rc = comfort.RoomComfort(room())
        late = T0 - timedelta(hours=1)
        r = rc.evaluate(snap(False), models.RoomChoices(precool_ready_by=late), T0)
        self.assertEqual((r.end_precool, r.rule), ("an hour after its ready time", BR.UNOCCUPIED))
        r = rc.evaluate(snap(False), models.RoomChoices(precool_ready_by=late + timedelta(seconds=1)), T0)
        self.assertIsNone(r.end_precool)

    def test_only_with_a_presence_sensor(self) -> None:
        rc = comfort.RoomComfort(room(presence_sensor=None))
        r = rc.evaluate(snap(None), models.RoomChoices(precool_ready_by=T0), T0)
        self.assertEqual((r.rule, r.precool_on, r.end_precool), (BR.OCCUPIED, False, None))


class Sleep(unittest.TestCase):
    def test_modes(self) -> None:
        cases = [(CM.SLEEP, None, BR.SLEEP), (CM.AWAKE, True, BR.OCCUPIED), (CM.SCHEDULE, True, BR.SLEEP),
                 (CM.SCHEDULE, False, BR.OCCUPIED), (CM.SCHEDULE, None, BR.OCCUPIED)]
        for mode, sched, want in cases:
            rc = comfort.RoomComfort(room())
            r = rc.evaluate(snap(True, schedule=sched), models.RoomChoices(comfort_mode=mode), T0)
            self.assertEqual(r.rule, want, (mode, sched))


class Ramp(unittest.TestCase):
    def test_sleep_to_schedule_decided_at_once_and_ramped(self) -> None:
        # 23 October 2026: a change from Sleep to Schedule re-evaluates at once
        # against the day band, which then moves up over the hour [D014][D092]
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.SLEEP), T0)
        self.assertEqual((r.rule, r.rerun), (BR.SLEEP, False))
        r = rc.evaluate(snap(True, schedule=False), models.RoomChoices(comfort_mode=CM.SCHEDULE), T0)
        self.assertEqual((r.rule, r.rerun, r.waking), (BR.OCCUPIED, True, True))
        half = r.points[5]  # T0 + 30 minutes
        self.assertAlmostEqual(half.low, 22.5)
        self.assertAlmostEqual(half.high, 25.5)
        self.assertGreater(r.points[0].centre, 22.5)
        self.assertEqual((r.points[11].low, r.points[11].high), (24.0, 27.0))
        self.assertEqual((r.points[12].low, r.points[12].high), (24.0, 27.0))
        again = rc.evaluate(snap(True, schedule=False), models.RoomChoices(comfort_mode=CM.SCHEDULE),
                            T0 + timedelta(minutes=1))
        self.assertFalse(again.rerun)

    def test_a_change_mid_ramp_starts_from_the_band_in_force(self) -> None:
        rc = comfort.RoomComfort(room())
        rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.SLEEP), T0)
        rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.AWAKE), T0)
        r = rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.SLEEP), T0 + timedelta(minutes=30))
        self.assertTrue(r.rerun)
        self.assertAlmostEqual(rc.band_at(T0 + timedelta(minutes=30), (21.0, 24.0))[0], 22.5)

    def test_no_ramp_from_or_to_off(self) -> None:
        rc = comfort.RoomComfort(room())
        rc.evaluate(snap(False, T0 - timedelta(hours=1)), CH, T0)
        r = rc.evaluate(snap(True, T0 - timedelta(minutes=5)), CH, T0 + timedelta(minutes=1))
        self.assertEqual((r.rule, r.rerun), (BR.OCCUPIED, False))
        self.assertEqual((r.points[0].low, r.points[0].high), (24.0, 27.0))

    def test_waking_ends_when_the_ramp_is_over(self) -> None:
        rc = comfort.RoomComfort(room())
        rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.SLEEP), T0)
        aw = models.RoomChoices(comfort_mode=CM.AWAKE)
        self.assertTrue(rc.evaluate(snap(True, t=24.0), aw, T0 + timedelta(minutes=1)).waking)
        self.assertTrue(rc.evaluate(snap(True, t=24.0), aw, T0 + timedelta(minutes=59)).waking)
        # a room colder than its day band stops waking too, so it can be heated
        cold = rc.evaluate(snap(True, t=17.0, rh=50.0), aw, T0 + timedelta(minutes=61))
        self.assertFalse(cold.waking)
        self.assertFalse(rc.evaluate(snap(True, t=17.0, rh=50.0), aw, T0 + timedelta(hours=12)).waking)

    def test_sleep_to_off_is_not_left_waking(self) -> None:
        """No ramp to an OFF band, so nothing to wake through: a cold room
        is not held from heating when someone comes back [D085][D092]."""
        rc = comfort.RoomComfort(room())
        rc.evaluate(snap(True), models.RoomChoices(comfort_mode=CM.SLEEP), T0)
        r = rc.evaluate(snap(True, t=19.0, rh=50.0), models.RoomChoices(comfort_mode=CM.SLEEP, leave_pressed_at=T0),
                        T0)
        self.assertEqual((r.rule, r.waking), (BR.LEFT, False))


class Index(unittest.TestCase):
    def test_index_and_centre(self) -> None:
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(True, t=25.0, rh=60.0), CH, T0)
        self.assertAlmostEqual(r.room_index, 25.0 + 0.33 * hci.vapour_pressure(25.0, 60.0) - 4.0, places=12)
        # sun on the glass does not change the index of the same air [D010]
        sunny = dataclasses.replace(snap(True, t=25.0, rh=60.0),
                                    sun=models.SunSnapshot(True, "sensor", 1.0, 1.0, 1.0, VT.MEASURED))
        self.assertAlmostEqual(comfort.room_index(sunny), r.room_index, places=12)
        self.assertEqual((r.band_low, r.band_high), (24.0, 27.0))
        want = hci.temperature_for_index(25.5, 60.0).t_c
        self.assertAlmostEqual(r.centre_c, want)

    def test_off_gives_no_points(self) -> None:
        rc = comfort.RoomComfort(room())
        r = rc.evaluate(snap(False, T0 - timedelta(hours=1)), CH, T0)
        self.assertEqual((r.points, r.reason), ((), "the room is empty"))


if __name__ == "__main__":
    unittest.main()
