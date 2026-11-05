"""limits.py: what may reach the unit (Module 7)."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

limits = load("limits")
models = load("models")
SK = models.StepKind
VT = models.ValueTag
LR = models.LimitRule
WK = models.WarningKind
BR = models.BandRule
CS = models.CompressorState

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def profile(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "hvac_modes": ("off", "cool", "heat", "dry", "fan_only"), "fan_modes": ("auto", "quiet"),
        "vertical_vanes": ("fixed",), "horizontal_vanes": (), "min_setpoint": 17.0, "max_setpoint": 30.0,
        "setpoint_step": 0.1, "range_only": False,
    }
    base.update(kw)
    return models.CapabilityProfile(**base)


def room(room_id: str = "r1", outdoor: str | None = None, **kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": room_id, "name": room_id, "heads": (models.HeadConfig(f"climate.{room_id}", outdoor),),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh",
        "occupied_band": (23.0, 26.0), "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0),
        "profile": profile(), "opening_grace_s": 300.0, "warning_grace_s": 180.0,
    }
    base.update(kw)
    return models.RoomConfig(**base)


def cmd(mode: str = "cool", setpoint: float | None = 24.0, fan: str | None = "auto", climate: str = "climate.r1",
        vane: str | None = None) -> Any:
    return models.HeadCommand(climate, mode, setpoint, None, None, setpoint, fan, vane)


REST = (cmd("fan_only", None, "quiet"),)


def proposed(kind: Any = SK.COOL, heads: tuple[Any, ...] | None = None, advised: bool = False,
             rest: tuple[Any, ...] = REST, blinds: tuple[Any, ...] = ()) -> Any:
    if heads is None:
        heads = (cmd(),) if kind in (SK.COOL, SK.HEAT, SK.DRY) else (REST if kind is SK.REST else ())
    return models.DeliveryResult(kind, 2.0, heads, blinds, (), advised, None, False, ("the plan",), rest)


def snap(*, mode: str = "cool", comp: Any = CS.STOPPED, openings: tuple[Any, ...] = (),
         t_tag: Any = VT.MEASURED, tariff: Any = None) -> Any:
    base = SimRoom().snapshot()
    h = dataclasses.replace(base.heads[0], climate="climate.r1", mode=mode, compressor=comp)
    return dataclasses.replace(base, heads=(h,), openings=openings, tariff_now=tariff,
                               room_temperature=models.Reading(25.0 if t_tag is VT.MEASURED else None, t_tag,
                                                               "sensor.t"))


def comfort(rule: Any = BR.OCCUPIED, off: bool = False, index: float | None = 26.0, sleeping: bool = False,
            points: bool = True) -> Any:
    pts = (models.BandPoint(T0, 23.0, 26.0, True),) if points and not off else ()
    return models.ComfortResult(rule, off, "why", rule is not BR.UNOCCUPIED, sleeping, False, False, pts, index,
                                None)


def econ(turn_off: bool = False) -> Any:
    return models.RoomEconomics((1.0,) * 12, 1.0, None, None, turn_off, None, "the budget")


def step(rl: Any, *, now: datetime = T0, p: Any = None, s: Any = None, c: Any = None, e: Any = None,
         choices: Any = None, shared: bool = False, can_speak: bool = True) -> Any:
    return rl.step(now=now, proposed=p or proposed(), snapshot=s or snap(), comfort=c or comfort(), economics=e,
                   choices=choices or models.RoomChoices(), shared_climate=shared, can_speak=can_speak)


HAND = (models.HeadCommand("climate.a", "cool", None, None, None, None, None, None),)  # seen running as it is


def allowed(rl: Any, guard: Any = None, *, outdoor: str = "u1", running: bool = False, **kw: Any) -> Any:
    guard = guard or limits.HouseGuard()
    st = step(rl, **kw)
    seen = HAND if running else ()
    return guard.apply(kw.get("now", T0), [limits.GuardEntry(rl, st, (outdoor,), {outdoor: running}, seen)])[
        st.room_id]


def opening(since: datetime | None, open_: bool = True) -> Any:
    return models.Flag(open_, VT.MEASURED, "binary_sensor.door", since)


class FirstRuleWins(unittest.TestCase):
    def test_nothing_applies(self) -> None:
        r = allowed(limits.RoomLimits(room()))
        self.assertEqual((r.kind, r.heads, r.changed, r.not_delivered), (SK.COOL, (cmd(),), (), False))

    def test_lockout_by_reason_or_shared_entity(self) -> None:
        r = allowed(limits.RoomLimits(room(lockout_reason="Under renovation")))
        self.assertEqual((r.kind, r.lockout, r.changed), (SK.OFF, True, (LR.LOCKOUT,)))
        self.assertIn("Under renovation", r.reasons[0])
        r = allowed(limits.RoomLimits(room()), shared=True)
        self.assertEqual((r.kind, r.lockout), (SK.OFF, True))  # [D021]
        r = allowed(limits.RoomLimits(room(lockout_reason="x")), choices=models.RoomChoices(automatic_control=False))
        self.assertEqual(r.changed, (LR.LOCKOUT,))

    def test_automatic_control_off_once_then_nothing(self) -> None:
        rl = limits.RoomLimits(room())
        allowed(rl)
        off = models.RoomChoices(automatic_control=False)
        r = allowed(rl, choices=off)
        self.assertEqual((r.kind, r.automatic_off), (SK.OFF, True))  # [D089]
        r = allowed(rl, choices=off)
        self.assertEqual((r.kind, r.heads), (SK.NONE, ()))  # [D049]
        # restored with it off: nothing is sent, not even once
        self.assertIs(allowed(limits.RoomLimits(room()), choices=off).kind, SK.NONE)

    def test_leave_is_off(self) -> None:
        r = allowed(limits.RoomLimits(room()), p=proposed(SK.OFF), c=comfort(BR.LEFT, off=True))
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.LEAVE,)))  # [D088]

    def test_missing_reading_sends_nothing(self) -> None:
        r = allowed(limits.RoomLimits(room()), s=snap(t_tag=VT.MISSING), c=comfort(index=None))
        self.assertEqual((r.kind, r.missing), (SK.NONE, ("sensor.t",)))  # [D018]
        self.assertIn("sensor.t", r.reasons[0])
        r = allowed(limits.RoomLimits(room()), c=comfort(points=False))
        self.assertIs(r.kind, SK.NONE)
        self.assertIn("the band", r.reasons[0])

    def test_enforced_power(self) -> None:
        r = allowed(limits.RoomLimits(room()), e=econ(turn_off=True))
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.ENFORCED_POWER,)))  # [D063]
        self.assertIs(allowed(limits.RoomLimits(room()), e=econ()).kind, SK.COOL)


class EmptyRoom(unittest.TestCase):
    def setUp(self) -> None:
        self.rl = limits.RoomLimits(room(announce=True, announce_through=("media_player.r1",)))
        self.empty = comfort(BR.UNOCCUPIED, off=True)

    def test_warned_then_off_one_grace_later(self) -> None:
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty)
        self.assertEqual((r.kind, [w.kind for w in r.warnings]), (SK.NONE, [WK.EMPTY_ROOM]))  # [D082]
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, now=T0 + 2 * MIN)
        self.assertEqual((r.kind, r.warnings), (SK.NONE, ()))
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, now=T0 + 3 * MIN - timedelta(seconds=0.4))
        self.assertIn("off in 1 s", r.reasons[-1])  # a countdown never says 0 s while it still waits
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, now=T0 + 3 * MIN)
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.EMPTY_ROOM,)))

    def test_off_at_once_with_no_speech_engine(self) -> None:
        """Speakers set, but no engine to say the warning: off at once
        [D069][D082]."""
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, can_speak=False)
        self.assertEqual((r.kind, r.warnings), (SK.OFF, ()))

    def test_off_at_once_where_no_one_can_hear(self) -> None:
        for rl, c, s in (
            (limits.RoomLimits(room()), self.empty, snap()),  # announcements off
            (limits.RoomLimits(room(announce=True)), self.empty, snap()),  # no speakers
            (self.rl, comfort(BR.UNOCCUPIED, off=True, sleeping=True), snap()),  # asleep [D071]
            (self.rl, self.empty, snap(mode="off")),  # unit already off
        ):
            r = allowed(rl, p=proposed(SK.OFF), c=c, s=s)
            self.assertEqual((r.kind, r.warnings), (SK.OFF, ()))

    def test_leave_ends_an_absence(self) -> None:
        allowed(self.rl, p=proposed(SK.OFF), c=self.empty)
        allowed(self.rl, p=proposed(SK.OFF), c=comfort(BR.LEFT, off=True), now=T0 + MIN)
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, now=T0 + 10 * MIN)
        self.assertEqual((r.kind, [w.kind for w in r.warnings]), (SK.NONE, [WK.EMPTY_ROOM]))

    def test_off_and_none_move_no_blinds(self) -> None:
        moves = (models.BlindCommand("cover.a", 0.0),)
        r = allowed(limits.RoomLimits(room()), p=proposed(blinds=moves), shared=True)
        self.assertEqual((r.kind, r.blinds), (SK.OFF, ()))
        r = allowed(limits.RoomLimits(room()), p=proposed(blinds=moves), c=comfort(index=None))
        self.assertEqual((r.kind, r.blinds), (SK.NONE, ()))
        self.assertEqual(allowed(limits.RoomLimits(room()), p=proposed(blinds=moves)).blinds, moves)

    def test_a_new_absence_warns_again(self) -> None:
        allowed(self.rl, p=proposed(SK.OFF), c=self.empty)
        allowed(self.rl, c=comfort())
        r = allowed(self.rl, p=proposed(SK.OFF), c=self.empty, now=T0 + 10 * MIN)
        self.assertEqual([w.kind for w in r.warnings], [WK.EMPTY_ROOM])


class Openings(unittest.TestCase):
    def test_nothing_new_is_started(self) -> None:
        r = allowed(limits.RoomLimits(room()), s=snap(openings=(opening(T0),)))
        self.assertEqual((r.kind, r.heads, r.changed), (SK.REST, REST, (LR.OPENING,)))  # [D050]
        self.assertTrue(r.not_delivered)
        r = allowed(limits.RoomLimits(room()), s=snap(openings=(opening(T0),)), p=proposed(rest=()))
        self.assertIs(r.kind, SK.OFF)

    def test_a_running_unit_keeps_running_until_the_grace_ends(self) -> None:
        rl = limits.RoomLimits(room())
        running = snap(comp=CS.RUNNING, openings=(opening(T0),))
        r = allowed(rl, s=running)
        self.assertEqual((r.kind, r.changed), (SK.COOL, ()))
        self.assertEqual(allowed(rl, s=running, now=T0 + 1.5 * MIN).warnings, ())
        r = allowed(rl, s=running, now=T0 + 2 * MIN)
        self.assertEqual([w.kind for w in r.warnings], [WK.OPENING_FIRST])
        self.assertAlmostEqual(r.warnings[0].seconds_to_off, 180.0)
        r = allowed(rl, s=running, now=T0 + 3 * MIN)
        self.assertEqual(r.warnings, ())
        self.assertEqual(allowed(rl, s=running, now=T0 + 4 * MIN).warnings, ())
        r = allowed(rl, s=running, now=T0 + 4.5 * MIN)
        self.assertEqual([w.kind for w in r.warnings], [WK.OPENING_FINAL])
        r = allowed(rl, s=running, now=T0 + 5 * MIN, running=True)
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.OPENING,)))

    def test_earliest_opening_counts_and_closing_resets(self) -> None:
        rl = limits.RoomLimits(room())
        two = snap(openings=(opening(T0 + 4 * MIN), opening(T0)))
        self.assertIs(allowed(rl, s=two, now=T0 + 5 * MIN).kind, SK.OFF)
        allowed(rl, s=snap(openings=(opening(None, open_=False),)), now=T0 + 6 * MIN)
        r = allowed(rl, s=snap(comp=CS.RUNNING, openings=(opening(T0 + 6 * MIN),)), now=T0 + 8 * MIN)
        self.assertEqual([w.kind for w in r.warnings], [WK.OPENING_FIRST])

    def test_a_later_opening_left_open_warns_again(self) -> None:
        rl = limits.RoomLimits(room())
        first = opening(T0)
        later = dataclasses.replace(opening(T0 + 3 * MIN), entity_id="binary_sensor.window")
        allowed(rl, s=snap(comp=CS.RUNNING, openings=(first,)), now=T0 + 2 * MIN)  # first warning
        shut_first = dataclasses.replace(first, value=False)
        r = allowed(rl, s=snap(comp=CS.RUNNING, openings=(shut_first, later)), now=T0 + 5 * MIN)
        self.assertEqual([w.kind for w in r.warnings], [WK.OPENING_FIRST])

    def test_when_it_opened_cannot_be_read(self) -> None:
        r = allowed(limits.RoomLimits(room()), s=snap(openings=(opening(None),)))
        self.assertEqual((r.kind, r.heads), (SK.NONE, ()))

    def test_opened_on_free_cooling_advice_stops_at_once(self) -> None:
        rl = limits.RoomLimits(room())
        allowed(rl, p=proposed(advised=True))
        r = allowed(rl, p=proposed(advised=True), s=snap(comp=CS.RUNNING, openings=(opening(T0 + 0.5 * MIN),)),
                    now=T0 + MIN)
        self.assertEqual((r.kind, r.warnings), (SK.OFF, ()))  # [D073]
        # opened before the advice: the ordinary grace
        rl = limits.RoomLimits(room())
        allowed(rl, p=proposed(advised=True), now=T0)
        r = allowed(rl, p=proposed(advised=True), s=snap(comp=CS.RUNNING, openings=(opening(T0 - MIN),)),
                    now=T0 + MIN)
        self.assertIs(r.kind, SK.COOL)

    def test_advice_ends_a_cycle_after_it_was_last_given(self) -> None:
        rl = limits.RoomLimits(room())
        allowed(rl, p=proposed(advised=True), now=T0)
        allowed(rl, now=T0 + 0.5 * MIN)
        allowed(rl, now=T0 + 2 * MIN)
        r = allowed(rl, s=snap(comp=CS.RUNNING, openings=(opening(T0 + 2.5 * MIN),)), now=T0 + 3 * MIN)
        self.assertIs(r.kind, SK.COOL)


class Abilities(unittest.TestCase):
    def test_only_what_the_unit_offers(self) -> None:
        p = proposed(heads=(cmd(fan="turbo", vane="swing"),))
        r = allowed(limits.RoomLimits(room()), p=p)
        self.assertEqual((r.heads[0].fan_mode, r.heads[0].vertical_vane), (None, None))  # [D024][D048]
        self.assertIn(LR.UNIT_ABILITIES, r.changed)
        p = proposed(heads=(cmd(mode="auto"),))
        self.assertIsNone(allowed(limits.RoomLimits(room()), p=p).heads[0].mode)

    def test_range_and_bounds(self) -> None:
        rl = limits.RoomLimits(room(profile=profile(range_only=True)))
        h = allowed(rl).heads[0]
        self.assertEqual((h.setpoint, h.setpoint_low, h.setpoint_high), (None, 23.0, 25.0))  # [D024]
        h = allowed(limits.RoomLimits(room()), p=proposed(heads=(cmd(setpoint=35.0),))).heads[0]
        self.assertEqual(h.setpoint, 30.0)  # [D051]
        self.assertIn("within the unit's range", " ".join(allowed(limits.RoomLimits(room()),
                      p=proposed(heads=(cmd(setpoint=10.0),))).reasons))


class Tariff(unittest.TestCase):
    def interval(self, *c: str) -> Any:
        return models.TariffInterval(T0, T0 + 30 * MIN, "peak", 0.5, c)

    def test_known_constraints_recorded(self) -> None:
        r = allowed(limits.RoomLimits(room()), s=snap(tariff=self.interval("no_grid_import")))
        self.assertEqual((r.kind, r.constraints, r.unknown_constraints, r.not_delivered),
                         (SK.COOL, ("no_grid_import",), (), False))

    def test_unknown_constraint_reported_never_dropped(self) -> None:
        r = allowed(limits.RoomLimits(room()), s=snap(tariff=self.interval("no_grid_import", "demand_cap")))
        self.assertEqual((r.constraints, r.unknown_constraints), (("no_grid_import", "demand_cap"), ("demand_cap",)))
        self.assertIn(LR.TARIFF_CONSTRAINTS, r.changed)
        self.assertIn("demand_cap", r.reasons[-1])  # [D034]
        self.assertFalse(r.not_delivered)


class Guard(unittest.TestCase):
    def setUp(self) -> None:
        self.g = limits.HouseGuard()
        self.rl = limits.RoomLimits(room())

    def run_(self, minute: float, kind: Any, rl: Any = None, **kw: Any) -> Any:
        return allowed(rl or self.rl, self.g, p=proposed(kind), now=T0 + minute * MIN, **kw)

    def test_start_held_within_five_minutes_of_a_stop(self) -> None:
        self.assertIs(self.run_(0, SK.COOL).kind, SK.COOL)
        self.assertIs(self.run_(11, SK.REST).kind, SK.REST)
        r = self.run_(14, SK.COOL)
        self.assertEqual((r.kind, r.heads, r.changed, r.not_delivered),
                         (SK.REST, REST, (LR.SHORT_CYCLE_GUARD,), True))  # [D016]
        self.assertIs(self.run_(16, SK.COOL).kind, SK.COOL)

    def test_stop_held_within_ten_minutes_of_a_start(self) -> None:
        self.assertIs(self.run_(0, SK.COOL).kind, SK.COOL)
        r = self.run_(6, SK.REST)
        self.assertEqual((r.kind, r.changed, r.held_off), (SK.COOL, (LR.SHORT_CYCLE_GUARD,), False))
        self.assertEqual(r.heads, (cmd(fan="quiet"),))  # fan choice stands
        r = self.run_(8, SK.OFF)
        self.assertEqual((r.kind, r.held_off), (SK.COOL, True))  # [D077]
        self.assertIs(self.run_(10, SK.REST).kind, SK.REST)

    def test_dry_counts_as_running(self) -> None:
        allowed(self.rl, self.g, p=proposed(SK.DRY, heads=(cmd("dry", 24.5),)), now=T0)
        r = self.run_(5, SK.REST)
        self.assertIs(r.kind, SK.DRY)

    def test_held_on_with_heat_keeps_heat(self) -> None:
        allowed(self.rl, self.g, p=proposed(SK.HEAT, heads=(cmd("heat", 26.0),)), now=T0)
        self.assertIs(self.run_(3, SK.REST).kind, SK.HEAT)

    def test_user_stops_are_not_held(self) -> None:
        self.run_(0, SK.COOL)
        r = self.run_(3, SK.OFF, c=comfort(BR.LEFT, off=True))
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.LEAVE,)))  # [D088]
        self.run_(20, SK.COOL)
        r = self.run_(23, SK.COOL, choices=models.RoomChoices(automatic_control=False))
        self.assertIs(r.kind, SK.OFF)  # [D089]
        g = limits.HouseGuard()
        rl = limits.RoomLimits(room())
        allowed(rl, g, now=T0)
        r = allowed(rl, g, now=T0 + MIN, shared=True)
        self.assertEqual((r.kind, r.changed), (SK.OFF, (LR.LOCKOUT,)))  # [D021]

    def test_empty_room_and_power_stops_are_held(self) -> None:
        self.run_(0, SK.COOL)
        r = self.run_(5, SK.OFF, c=comfort(BR.UNOCCUPIED, off=True))
        self.assertEqual((r.kind, r.held_off), (SK.COOL, True))
        g = limits.HouseGuard()
        rl = limits.RoomLimits(room())
        allowed(rl, g, now=T0)
        self.assertIs(allowed(rl, g, now=T0 + 5 * MIN, e=econ(turn_off=True)).kind, SK.COOL)

    def test_a_start_made_by_hand_counts_when_control_comes_back(self) -> None:
        manual = models.RoomChoices(automatic_control=False)
        self.assertIs(self.run_(0, SK.COOL).kind, SK.COOL)
        self.assertIs(self.run_(20, SK.COOL, choices=manual).kind, SK.OFF)  # sent off once [D089]
        self.assertIs(self.run_(21, SK.COOL, choices=manual, running=True).kind, SK.NONE)  # the user starts it
        r = self.run_(22, SK.COOL, running=True)  # control comes back with the compressor running
        self.assertEqual((r.kind, r.changed), (SK.COOL, ()))  # not held off as a fresh start
        r = self.run_(25, SK.REST, running=True)
        self.assertEqual((r.kind, r.changed), (SK.COOL, (LR.SHORT_CYCLE_GUARD,)))  # ran 4 minutes since the start [D016]
        self.assertIs(self.run_(31, SK.REST, running=True).kind, SK.REST)

    def test_the_stop_of_a_start_made_by_hand_is_held(self) -> None:
        """Started by hand while Automatic control was off, and control
        comes back to a plan that rests: the stop is held, the unit kept as
        it runs, until it has run 10 minutes [D016][D089]."""
        manual = models.RoomChoices(automatic_control=False)
        self.run_(-30, SK.REST, choices=manual)
        self.run_(-29, SK.REST, choices=manual)
        self.assertIs(self.run_(0, SK.REST, choices=manual, running=True).kind, SK.NONE)  # the user starts it
        r = self.run_(2, SK.REST, running=True)
        self.assertEqual((r.kind, r.heads, r.changed), (SK.COOL, HAND, (LR.SHORT_CYCLE_GUARD,)))
        self.assertIs(self.run_(10, SK.REST, running=True).kind, SK.REST)

    def test_a_stop_made_by_hand_counts_when_control_comes_back(self) -> None:
        manual = models.RoomChoices(automatic_control=False)
        self.run_(0, SK.COOL)
        self.run_(1, SK.COOL, choices=manual)  # off once, within the minimum run: a user stop, not held
        self.run_(15, SK.COOL, choices=manual, running=True)  # the user starts it
        self.run_(16, SK.COOL, choices=manual, running=False)  # and stops it
        r = self.run_(18, SK.COOL)
        self.assertEqual((r.kind, r.changed), (SK.REST, (LR.SHORT_CYCLE_GUARD,)))  # rested 2 minutes [D016]

    def test_not_known_since_start_up_is_not_held(self) -> None:
        self.assertIs(self.run_(0, SK.REST, running=True).kind, SK.REST)
        self.assertIs(limits.HouseGuard().apply(T0, [limits.GuardEntry(self.rl, step(self.rl), ("u1",),
                                                                        {"u1": False})])["r1"].kind, SK.COOL)

    def test_a_unit_run_by_hand_in_one_room_lets_another_room_rest(self) -> None:
        """Rooms a and b share an outdoor unit. a is run by hand and its
        compressor is seen running, so b's stop does not stop the unit and
        is not held [D016][D020]."""
        a, b = limits.RoomLimits(room("a", "shared")), limits.RoomLimits(room("b", "shared"))
        manual = models.RoomChoices(automatic_control=False)

        def both(minute: float, kb: Any, running: bool) -> Any:
            now = T0 + minute * MIN
            ea = limits.GuardEntry(a, step(a, p=proposed(SK.COOL, heads=(cmd(climate="climate.a"),)), now=now,
                                           choices=manual), ("shared",), {"shared": running})
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=(cmd(climate="climate.b"),) if kb is SK.COOL
                                                         else None), now=now), ("shared",), {"shared": running})
            return self.g.apply(now, [ea, eb])

        both(-30, SK.REST, False)
        both(0, SK.REST, True)  # a started by hand
        both(1, SK.COOL, True)
        r = both(3, SK.REST, True)
        self.assertEqual((r["b"].kind, r["b"].changed), (SK.REST, ()))
        r = both(4, SK.COOL, True)  # and b may start again: the unit never stopped
        self.assertEqual((r["b"].kind, r["b"].changed), (SK.COOL, ()))

    def test_an_outdoor_unit_runs_if_any_room_on_it_wants_it(self) -> None:
        a, b = limits.RoomLimits(room("a", "shared")), limits.RoomLimits(room("b", "shared"))

        def both(minute: float, ka: Any, kb: Any) -> Any:
            now = T0 + minute * MIN
            ea = limits.GuardEntry(a, step(a, p=proposed(ka, heads=(cmd(climate="climate.a"),) if ka is SK.COOL
                                                         else None), now=now), ("shared",), {})
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=(cmd(climate="climate.b"),) if kb is SK.COOL
                                                         else None), now=now), ("shared",), {})
            return self.g.apply(now, [ea, eb])

        both(0, SK.COOL, SK.REST)
        r = both(4, SK.REST, SK.COOL)
        # a's stop: b keeps the outdoor unit running, so a rests and nothing is held
        self.assertEqual((r["a"].kind, r["b"].kind, r["a"].changed), (SK.REST, SK.COOL, ()))
        r = both(6, SK.REST, SK.REST)
        # the unit started 6 minutes ago: b ran last, so b is held on; a rests
        self.assertEqual((r["a"].kind, r["b"].kind), (SK.REST, SK.COOL))
        r = both(10, SK.REST, SK.REST)
        self.assertEqual((r["a"].kind, r["b"].kind), (SK.REST, SK.REST))
        r = both(12, SK.COOL, SK.REST)
        self.assertEqual((r["a"].kind, r["a"].changed), (SK.REST, (LR.SHORT_CYCLE_GUARD,)))


if __name__ == "__main__":
    unittest.main()
