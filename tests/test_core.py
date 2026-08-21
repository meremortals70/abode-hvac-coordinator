"""Tests for the pure modules. No Home Assistant required.

python3 -m unittest discover -s tests -p "test_core.py"
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load

_pkg = load()
allowance = _pkg.allowance
intervals = _pkg.intervals
serialise = _pkg.serialise
strip = _pkg.strip

DayPattern = _pkg.plan.DayPattern
Plan = _pkg.plan.Plan
PlanError = _pkg.plan.PlanError
Rate = _pkg.plan.Rate
ExportRate = _pkg.plan.ExportRate
Period = _pkg.plan.Period
day_token = _pkg.plan.day_token
format_time = _pkg.plan.format_time
parse_month_day = _pkg.plan.parse_month_day
parse_time = _pkg.plan.parse_time

is_valid = _pkg.validate.is_valid
validate_day_coverage = _pkg.validate.validate_day_coverage
validate_plan = _pkg.validate.validate_plan
validate_periods = _pkg.validate.validate_periods

BRISBANE = ZoneInfo("Australia/Brisbane")
SYDNEY = ZoneInfo("Australia/Sydney")

ALL_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun", "holiday"})


def never_holiday(_: date) -> bool:
    return False


def sample_plan() -> Plan:
    """A six-period, four-rate plan on one day set."""
    rates = (
        Rate(
            "cheap",
            0.198,
            constraints=frozenset({"grid_charge_battery"}),
        ),
        Rate("standard", 0.321),
        Rate("free", 0.0, constraints=frozenset({"precool_opportunity"})),
        Rate("peak", 0.584, constraints=frozenset({"no_grid_import"})),
    )
    periods = (
        Period(0, 360, "cheap"),
        Period(360, 660, "standard"),
        Period(660, 840, "free"),
        Period(840, 960, "standard"),
        Period(960, 1260, "peak"),
        Period(1260, 1440, "standard"),
    )
    return Plan(
        name="Ovo",
        rates=rates,
        day_patterns=(DayPattern("Every day", ALL_DAYS, periods),),
        daily_supply_charge=1.25,
    )


class TestTimeParsing(unittest.TestCase):
    def test_round_trip(self) -> None:
        for text in ("00:00", "06:30", "23:59", "24:00"):
            self.assertEqual(format_time(parse_time(text)), text)

    def test_end_of_day(self) -> None:
        self.assertEqual(parse_time("24:00"), 1440)

    def test_rejects_rubbish(self) -> None:
        for text in ("", "25:00", "12:70", "noon", "12"):
            with self.assertRaises(PlanError):
                parse_time(text)

    def test_month_day(self) -> None:
        self.assertEqual(parse_month_day("11-01"), (11, 1))
        with self.assertRaises(PlanError):
            parse_month_day("13-01")


class TestDayToken(unittest.TestCase):
    def test_weekday(self) -> None:
        self.assertEqual(day_token(date(2026, 8, 14), False), "fri")
        self.assertEqual(day_token(date(2026, 8, 16), False), "sun")

    def test_holiday_beats_weekday(self) -> None:
        self.assertEqual(day_token(date(2026, 8, 14), True), "holiday")


class TestResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = sample_plan()

    def test_each_window(self) -> None:
        day = date(2026, 8, 14)
        cases = {
            0: "cheap",
            359: "cheap",
            360: "standard",
            660: "free",
            839: "free",
            840: "standard",
            960: "peak",
            1259: "peak",
            1260: "standard",
            1439: "standard",
        }
        for minutes, expected in cases.items():
            resolved = self.plan.resolve(day, minutes, False)
            self.assertIsNotNone(resolved, minutes)
            assert resolved is not None
            self.assertEqual(resolved.rate.name, expected, minutes)

    def test_start_inclusive_end_exclusive(self) -> None:
        day = date(2026, 8, 14)
        at_start = self.plan.resolve(day, 660, False)
        at_end = self.plan.resolve(day, 840, False)
        assert at_start is not None and at_end is not None
        self.assertEqual(at_start.rate.name, "free")
        self.assertEqual(at_end.rate.name, "standard")

    def test_constraints_reach_the_resolution(self) -> None:
        resolved = self.plan.resolve(date(2026, 8, 14), 1000, False)
        assert resolved is not None
        self.assertIn("no_grid_import", resolved.rate.constraints)

    def test_declared_constraints(self) -> None:
        self.assertEqual(
            self.plan.constraints,
            ("grid_charge_battery", "no_grid_import", "precool_opportunity"),
        )

    def test_unknown_rate_resolves_to_nothing(self) -> None:
        broken = Plan(
            name="Broken",
            rates=(Rate("a", 0.1),),
            day_patterns=(
                DayPattern("Every day", ALL_DAYS, (Period(0, 1440, "missing"),)),
            ),
        )
        self.assertIsNone(broken.resolve(date(2026, 8, 14), 60, False))


class TestDayPatterns(unittest.TestCase):
    def test_weekday_and_weekend(self) -> None:
        weekday = DayPattern(
            "Weekday",
            frozenset({"mon", "tue", "wed", "thu", "fri"}),
            (Period(0, 1440, "a"),),
        )
        weekend = DayPattern(
            "Weekend", frozenset({"sat", "sun", "holiday"}), (Period(0, 1440, "b"),)
        )
        plan = Plan("P", (Rate("a", 0.1), Rate("b", 0.2)), (weekday, weekend))
        friday = plan.resolve(date(2026, 8, 14), 600, False)
        saturday = plan.resolve(date(2026, 8, 15), 600, False)
        holiday = plan.resolve(date(2026, 8, 14), 600, True)
        assert friday is not None and saturday is not None and holiday is not None
        self.assertEqual(friday.rate.name, "a")
        self.assertEqual(saturday.rate.name, "b")
        self.assertEqual(holiday.rate.name, "b")

    def test_season_wraps_new_year(self) -> None:
        summer = DayPattern(
            "Summer", ALL_DAYS, (Period(0, 1440, "a"),), (11, 1), (3, 31)
        )
        self.assertTrue(summer.covers_date(date(2026, 12, 25)))
        self.assertTrue(summer.covers_date(date(2026, 1, 5)))
        self.assertFalse(summer.covers_date(date(2026, 7, 1)))

    def test_seasonal_beats_year_round(self) -> None:
        summer = DayPattern(
            "Summer", ALL_DAYS, (Period(0, 1440, "a"),), (11, 1), (3, 31)
        )
        year_round = DayPattern("Rest", ALL_DAYS, (Period(0, 1440, "b"),))
        plan = Plan("P", (Rate("a", 0.1), Rate("b", 0.2)), (summer, year_round))
        in_summer = plan.resolve(date(2026, 12, 25), 600, False)
        out_of_summer = plan.resolve(date(2026, 7, 1), 600, False)
        assert in_summer is not None and out_of_summer is not None
        self.assertEqual(in_summer.rate.name, "a")
        self.assertEqual(out_of_summer.rate.name, "b")


class TestValidation(unittest.TestCase):
    def test_sample_plan_is_valid(self) -> None:
        self.assertTrue(
            is_valid(sample_plan()), [str(p) for p in validate_plan(sample_plan())]
        )

    def test_gap_is_reported(self) -> None:
        day_pattern = DayPattern(
            "D", ALL_DAYS, (Period(0, 360, "a"), Period(420, 1440, "a"))
        )
        problems = validate_periods(day_pattern)
        self.assertTrue(
            any("06:00" in p.message and "07:00" in p.message for p in problems)
        )

    def test_overlap_is_reported(self) -> None:
        day_pattern = DayPattern(
            "D", ALL_DAYS, (Period(0, 400, "a"), Period(360, 1440, "a"))
        )
        problems = validate_periods(day_pattern)
        self.assertTrue(any("overlaps" in p.message for p in problems))

    def test_tail_gap_is_reported(self) -> None:
        day_pattern = DayPattern("D", ALL_DAYS, (Period(0, 1380, "a"),))
        problems = validate_periods(day_pattern)
        self.assertTrue(any("24:00" in p.message for p in problems))

    def test_backwards_window(self) -> None:
        day_pattern = DayPattern("D", ALL_DAYS, (Period(600, 300, "a"),))
        self.assertTrue(
            any("before it starts" in p.message for p in validate_periods(day_pattern))
        )

    def test_uncovered_day_type(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (DayPattern("Weekday", frozenset({"mon"}), (Period(0, 1440, "a"),)),),
        )
        problems = validate_day_coverage(plan)
        self.assertTrue(any("sun" in p.message for p in problems))

    def test_day_type_claimed_twice(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (
                DayPattern("One", ALL_DAYS, (Period(0, 1440, "a"),)),
                DayPattern("Two", ALL_DAYS, (Period(0, 1440, "a"),)),
            ),
        )
        self.assertTrue(
            any("more than one" in p.message for p in validate_day_coverage(plan))
        )

    def test_missing_rate_reference(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "nope"),)),),
        )
        self.assertTrue(any("does not exist" in p.message for p in validate_plan(plan)))

    def test_allowance_needs_a_fallback(self) -> None:
        plan = Plan(
            "P",
            (Rate("free", 0.0, rate_allowance_kwh=24.0),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "free"),)),),
        )
        self.assertTrue(any("fallback" in p.message for p in validate_plan(plan)))

    def test_fallback_cannot_itself_have_an_allowance(self) -> None:
        plan = Plan(
            "P",
            (
                Rate("free", 0.0, rate_allowance_kwh=24.0, fallback_rate="also"),
                Rate("also", 0.3, rate_allowance_kwh=10.0, fallback_rate="free"),
            ),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "free"),)),),
        )
        self.assertTrue(
            any("itself has an allowance" in p.message for p in validate_plan(plan))
        )

    def test_validity_backwards(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "a"),)),),
            valid_from=date(2026, 7, 1),
            valid_to=date(2026, 1, 1),
        )
        self.assertTrue(
            any("ends before it starts" in p.message for p in validate_plan(plan))
        )

    def test_seasonal_pair_covering_the_year_is_valid(self) -> None:
        summer = DayPattern(
            "Summer", ALL_DAYS, (Period(0, 1440, "a"),), (11, 1), (3, 31)
        )
        winter = DayPattern(
            "Winter", ALL_DAYS, (Period(0, 1440, "b"),), (4, 1), (10, 31)
        )
        plan = Plan("P", (Rate("a", 0.1), Rate("b", 0.2)), (summer, winter))
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])


class TestIntervals(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = sample_plan()

    def test_count_and_alignment(self) -> None:
        start = datetime(2026, 8, 14, 9, 7, tzinfo=BRISBANE)
        series = intervals.generate(self.plan, start, BRISBANE, never_holiday, hours=8)
        self.assertEqual(series[0].start.hour, 9)
        self.assertEqual(series[0].start.minute, 0)
        for earlier, later in pairwise(series):
            self.assertEqual(earlier.end, later.start)

    def test_prices_follow_the_window(self) -> None:
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        series = intervals.generate(self.plan, start, BRISBANE, never_holiday, hours=6)
        by_hour = {i.start.astimezone(BRISBANE).hour: i.rate for i in series}
        self.assertEqual(by_hour[10], "standard")
        self.assertEqual(by_hour[11], "free")
        self.assertEqual(by_hour[13], "free")
        self.assertEqual(by_hour[14], "standard")

    def test_service_shape(self) -> None:
        start = datetime(2026, 8, 14, 16, 0, tzinfo=BRISBANE)
        first = intervals.generate(self.plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ]
        payload = first.as_dict()
        for key in (
            "start_time",
            "end_time",
            "duration",
            "per_kwh",
            "export_per_kwh",
            "rate",
            "constraints",
            "coasting_permitted",
            "forecast",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["duration"], 30)
        self.assertEqual(payload["rate"], "peak")
        self.assertFalse(payload["forecast"])

    def test_evcc_shape_is_local(self) -> None:
        """Local time with the offset, like everything else this publishes."""
        start = datetime(2026, 8, 14, 16, 0, tzinfo=BRISBANE)
        entry = intervals.generate(self.plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ]
        payload = entry.as_evcc_entry()
        self.assertEqual(set(payload), {"start", "end", "value"})
        self.assertFalse(payload["start"].endswith("Z"))
        self.assertEqual(payload["start"], "2026-08-14T16:00:00+10:00")

    def test_rejects_bad_arguments(self) -> None:
        start = datetime(2026, 8, 14, 16, 0, tzinfo=BRISBANE)
        with self.assertRaises(ValueError):
            intervals.generate(
                self.plan, start, BRISBANE, never_holiday, resolution_minutes=0
            )
        with self.assertRaises(ValueError):
            intervals.generate(self.plan, start, BRISBANE, never_holiday, hours=0)


class TestDaylightSaving(unittest.TestCase):
    """Brisbane has no daylight saving, so these run against Sydney.

    Every assertion here normalises to UTC before comparing. Two datetimes that
    carry the same tzinfo are compared and subtracted on the wall clock, so an
    assertion written the obvious way measures what the plan says rather than
    what actually happened, and cannot fail on the two days that matter.
    """

    # Sydney springs forward 2026-10-04, 02:00 becomes 03:00, so that day is 23
    # hours long. It falls back 2026-04-05, 03:00 becomes 02:00, 25 hours long.
    SHORT_DAY = datetime(2026, 10, 3, 20, 0, tzinfo=SYDNEY)
    LONG_DAY = datetime(2026, 4, 4, 20, 0, tzinfo=SYDNEY)
    ORDINARY_DAY = datetime(2026, 6, 10, 20, 0, tzinfo=SYDNEY)

    def setUp(self) -> None:
        self.plan = sample_plan()

    def _series(self, start: datetime) -> list:
        return intervals.generate(self.plan, start, SYDNEY, never_holiday, hours=24)

    @staticmethod
    def _real_hours(series: list) -> float:
        span = series[-1].end.astimezone(UTC) - series[0].start.astimezone(UTC)
        return span.total_seconds() / 3600

    def test_the_horizon_is_wall_clock(self) -> None:
        """Twenty-four hours from 20:00 is 20:00 tomorrow, whatever it costs."""
        for start in (self.SHORT_DAY, self.LONG_DAY, self.ORDINARY_DAY):
            series = self._series(start)
            first = series[0].start.astimezone(SYDNEY)
            last = series[-1].end.astimezone(SYDNEY)
            self.assertEqual((first.hour, first.minute), (20, 0), start)
            self.assertEqual((last.hour, last.minute), (20, 0), start)
            self.assertEqual(last.date(), first.date() + timedelta(days=1), start)

    def test_a_short_day_is_twenty_three_hours(self) -> None:
        series = self._series(self.SHORT_DAY)
        self.assertEqual(self._real_hours(series), 23.0)
        self.assertEqual(len(series), 46)

    def test_a_long_day_is_twenty_five_hours(self) -> None:
        series = self._series(self.LONG_DAY)
        self.assertEqual(self._real_hours(series), 25.0)
        self.assertEqual(len(series), 50)

    def test_an_ordinary_day_is_unaffected(self) -> None:
        series = self._series(self.ORDINARY_DAY)
        self.assertEqual(self._real_hours(series), 24.0)
        self.assertEqual(len(series), 48)

    def test_every_interval_is_the_resolution_it_claims(self) -> None:
        """Including the one straddling the transition, which is the bug."""
        for start in (self.SHORT_DAY, self.LONG_DAY, self.ORDINARY_DAY):
            for interval in self._series(start):
                real = (
                    interval.end.astimezone(UTC) - interval.start.astimezone(UTC)
                ).total_seconds() / 60
                self.assertEqual(real, 30, f"{start}: {interval.start.isoformat()}")
                self.assertEqual(interval.duration_minutes, 30)

    def test_no_gaps_overlaps_or_repeats_in_real_time(self) -> None:
        for start in (self.SHORT_DAY, self.LONG_DAY, self.ORDINARY_DAY):
            series = self._series(start)
            instants = [i.start.astimezone(UTC) for i in series]
            self.assertEqual(len(instants), len(set(instants)), start)
            for earlier, later in pairwise(series):
                self.assertEqual(
                    earlier.end.astimezone(UTC), later.start.astimezone(UTC), start
                )
                self.assertLess(
                    earlier.start.astimezone(UTC), later.start.astimezone(UTC), start
                )

    def test_the_missing_hour_is_not_emitted(self) -> None:
        """02:00 to 03:00 does not exist on the short day, so nothing claims it."""
        local = [i.start.astimezone(SYDNEY) for i in self._series(self.SHORT_DAY)]
        on_the_day = [t for t in local if t.date() == date(2026, 10, 4)]
        self.assertNotIn((2, 0), [(t.hour, t.minute) for t in on_the_day])
        self.assertNotIn((2, 30), [(t.hour, t.minute) for t in on_the_day])

    def test_the_repeated_hour_is_emitted_twice(self) -> None:
        """02:00 to 03:00 happens twice on the long day, with different offsets."""
        local = [i.start.astimezone(SYDNEY) for i in self._series(self.LONG_DAY)]
        at_two = [t for t in local if t.date() == date(2026, 4, 5) and t.hour == 2]
        self.assertEqual(len(at_two), 4)
        self.assertEqual(
            {t.utcoffset() for t in at_two}, {timedelta(hours=11), timedelta(hours=10)}
        )

    def test_a_rate_gets_less_of_a_short_day_and_more_of_a_long_one(self) -> None:
        """The plan is unchanged. The day is what changed."""
        for start, expected in (
            (self.ORDINARY_DAY, 6.0),
            (self.SHORT_DAY, 5.0),
            (self.LONG_DAY, 7.0),
        ):
            # 'cheap' runs 00:00 to 06:00 and contains the transition.
            real = (
                sum(
                    (i.end.astimezone(UTC) - i.start.astimezone(UTC)).total_seconds()
                    for i in self._series(start)
                    if i.rate == "cheap"
                )
                / 3600
            )
            self.assertEqual(real, expected, start)

    def test_wall_clock_window_holds_across_the_transition(self) -> None:
        after = datetime(2026, 10, 4, 17, 0, tzinfo=SYDNEY)
        resolved = intervals.resolve_at(self.plan, after, SYDNEY, never_holiday)
        assert resolved is not None
        self.assertEqual(resolved.rate.name, "peak")

    def test_the_second_pass_is_not_silently_taken_as_the_first(self) -> None:
        """P25: the series must start at the instant asked for, not an hour before it.

        The first cursor is built by combining a naive datetime with a
        timedelta and only then attaching ``tzinfo``, which always resolves
        ``fold=0`` — the first pass through the repeated hour. A call placed
        in the second pass got a cursor an hour into the past, and the whole
        series shifted with it. This is the same defect P9 fixed in
        ``next_boundary``; ``instants_at`` was never wired into ``generate``.
        """
        start = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY, fold=1)
        series = self._series(start)
        self.assertEqual(series[0].start.astimezone(UTC), start.astimezone(UTC))

    def test_the_first_pass_is_unaffected_by_the_fix(self) -> None:
        """The companion case: fold=0 at the same wall-clock time must still work."""
        start = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY, fold=0)
        series = self._series(start)
        self.assertEqual(series[0].start.astimezone(UTC), start.astimezone(UTC))


class TestBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = sample_plan()

    def test_next_boundary_within_the_day(self) -> None:
        now = datetime(2026, 8, 14, 9, 30, tzinfo=BRISBANE)
        nxt = intervals.next_boundary(self.plan, now, BRISBANE, never_holiday)
        assert nxt is not None
        local = nxt.astimezone(BRISBANE)
        self.assertEqual((local.hour, local.minute), (11, 0))

    def test_next_boundary_rolls_to_tomorrow(self) -> None:
        now = datetime(2026, 8, 14, 23, 30, tzinfo=BRISBANE)
        nxt = intervals.next_boundary(self.plan, now, BRISBANE, never_holiday)
        assert nxt is not None
        local = nxt.astimezone(BRISBANE)
        self.assertEqual(local.date(), date(2026, 8, 15))
        self.assertEqual((local.hour, local.minute), (0, 0))

    def test_boundary_is_strictly_in_the_future(self) -> None:
        now = datetime(2026, 8, 14, 11, 0, tzinfo=BRISBANE)
        nxt = intervals.next_boundary(self.plan, now, BRISBANE, never_holiday)
        assert nxt is not None
        self.assertGreater(nxt, now)


class TestBoundariesAcrossTheFallBack(unittest.TestCase):
    """A boundary inside the hour that happens twice.

    Sydney falls back 2026-04-05: 03:00 becomes 02:00, so every wall-clock
    time between 02:00 and 03:00 names two real instants an hour apart. A
    boundary is stored as minutes past midnight, which alone does not say
    which.

    Every assertion here is about elapsed time. An assertion that writes down
    an expected wall-clock time passes against the broken code, because the
    expected value is built the same wrong way the code builds it.
    """

    def _plan(self) -> Plan:
        # Boundaries at 02:00 and 02:30, both inside the repeated hour.
        return Plan(
            "P",
            (Rate("A", 0.10), Rate("B", 0.20), Rate("C", 0.30)),
            (
                DayPattern(
                    "D",
                    ALL_DAYS,
                    (
                        Period(0, 120, "A"),
                        Period(120, 150, "B"),
                        Period(150, 1440, "C"),
                    ),
                ),
            ),
        )

    def _gap(self, now: datetime) -> timedelta:
        nxt = intervals.next_boundary(self._plan(), now, SYDNEY, never_holiday)
        assert nxt is not None
        return nxt.astimezone(UTC) - now.astimezone(UTC)

    def test_the_next_boundary_is_ahead_in_the_second_pass(self) -> None:
        """The whole fault in one assertion: it used to return a past instant."""
        now = datetime(2026, 4, 5, 2, 15, tzinfo=SYDNEY, fold=1)
        self.assertEqual(self._gap(now), timedelta(minutes=15))

    def test_the_second_pass_of_a_boundary_is_scheduled(self) -> None:
        """Standing in the first pass, the next change is the second 02:00."""
        now = datetime(2026, 4, 5, 1, 45, tzinfo=SYDNEY, fold=0)
        self.assertEqual(self._gap(now), timedelta(minutes=15))

    def test_it_does_not_sleep_through_the_repeated_hour(self) -> None:
        """It used to fall through to tomorrow midnight and sleep 22 hours."""
        now = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY, fold=0)
        self.assertLess(self._gap(now), timedelta(hours=2))

    def test_an_instant_is_built_for_each_pass(self) -> None:
        both = intervals.instants_at(date(2026, 4, 5), 150, SYDNEY)
        self.assertEqual(len(both), 2)
        self.assertEqual(
            both[1].astimezone(UTC) - both[0].astimezone(UTC), timedelta(hours=1)
        )

    def test_an_ordinary_day_names_one_instant(self) -> None:
        self.assertEqual(len(intervals.instants_at(date(2026, 6, 10), 150, SYDNEY)), 1)

    def test_the_spring_forward_day_is_unaffected(self) -> None:
        """Twenty-three hours long. Nothing repeats, so nothing changes."""
        now = datetime(2026, 10, 4, 1, 45, tzinfo=SYDNEY)
        self.assertEqual(self._gap(now), timedelta(minutes=15))


class TestTheCapIsDeclaredEvenWhenNothingCountsIt(unittest.TestCase):
    """The plan says what the cap is and what is paid past it.

    Counting usage against it is a separate, opt-in thing. A consumer that
    wants to apply the rule itself has everything it needs from the interval.
    """

    def _plan(self) -> Plan:
        return Plan(
            "P",
            (
                Rate("Free", 0.0, rate_allowance_kwh=24.0, fallback_rate="Peak"),
                Rate("Peak", 0.5688),
            ),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "Free"),)),),
        )

    def test_the_interval_carries_the_cap_and_the_fallback(self) -> None:
        plan = self._plan()
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertEqual(payload["allowance_kwh"], 24.0)
        self.assertEqual(payload["fallback_rate"], "Peak")
        self.assertAlmostEqual(payload["fallback_per_kwh"], 0.5688)

    def test_an_uncapped_rate_carries_neither(self) -> None:
        plan = Plan(
            "P",
            (Rate("Peak", 0.5688),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "Peak"),)),),
        )
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertIsNone(payload["allowance_kwh"])
        self.assertIsNone(payload["fallback_rate"])
        self.assertIsNone(payload["fallback_per_kwh"])


class TestTheIntervalCarriesTheDemandCharge(unittest.TestCase):
    """P27: the real cost of a demand-priced rate is declared, not hidden.

    The demand rate belongs to whichever rate it is attached to, not the
    plan, so two different rates can carry two different demand rates — or
    none at all.
    """

    def _plan(self) -> Plan:
        return Plan(
            "P",
            (
                Rate(
                    "Peak",
                    0.5688,
                    demand_period=True,
                    demand_rate_per_kw_month=18.40,
                ),
                Rate("Off Peak", 0.198),
            ),
            (
                DayPattern(
                    "D",
                    ALL_DAYS,
                    (Period(0, 960, "Off Peak"), Period(960, 1440, "Peak")),
                ),
            ),
        )

    def test_a_demand_priced_interval_declares_it(self) -> None:
        plan = self._plan()
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        start = datetime(2026, 8, 14, 17, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertEqual(payload["rate"], "Peak")
        self.assertTrue(payload["demand_period"])
        self.assertAlmostEqual(payload["demand_rate_per_kw_month"], 18.40)

    def test_a_rate_with_no_demand_charge_declares_none(self) -> None:
        plan = self._plan()
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertEqual(payload["rate"], "Off Peak")
        self.assertFalse(payload["demand_period"])
        self.assertEqual(payload["demand_rate_per_kw_month"], 0.0)


class TestTheIntervalCarriesTheExportAllowance(unittest.TestCase):
    """P32: the export allowance sits with the export price it caps.

    Published, never blended into export_price, and never counted. It used
    to be declared on the import rate, which is a different flow with its
    own periods, so the cap could not say which export price it capped.
    """

    def _timed(self) -> Plan:
        """Feed-in priced by period: the declaration is on the export rate."""
        return Plan(
            "P",
            (Rate("Peak", 0.30),),
            (
                DayPattern(
                    "D",
                    ALL_DAYS,
                    (Period(0, 1440, "Peak"),),
                    export_periods=(
                        Period(0, 720, "Morning"),
                        Period(720, 1440, "Afternoon"),
                    ),
                    export_same_all_day=False,
                ),
            ),
            export_rates=(
                ExportRate("Morning", 0.08, allowance_kwh=10.0, fallback_price=0.02),
                ExportRate("Afternoon", 0.05),
            ),
        )

    def _all_day(self) -> Plan:
        """One feed-in price all day: the declaration is on the timetable."""
        return Plan(
            "P",
            (Rate("Peak", 0.30),),
            (
                DayPattern(
                    "D",
                    ALL_DAYS,
                    (Period(0, 1440, "Peak"),),
                    export_flat_price=0.08,
                    export_allowance_kwh=10.0,
                    export_fallback_price=0.02,
                ),
            ),
        )

    def _payload(self, plan: Plan, hour: int) -> dict[str, Any]:
        start = datetime(2026, 8, 14, hour, 0, tzinfo=BRISBANE)
        result: dict[str, Any] = intervals.generate(
            plan, start, BRISBANE, never_holiday, hours=1
        )[0].as_dict()
        return result

    def test_a_timed_export_rate_carries_its_own_cap(self) -> None:
        plan = self._timed()
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        payload = self._payload(plan, 10)
        self.assertEqual(payload["export_allowance_kwh"], 10.0)
        self.assertAlmostEqual(payload["export_fallback_price"], 0.02)

    def test_the_cap_belongs_to_that_export_rate_and_not_the_next(self) -> None:
        """The import rate is the same all day; only the export rate changes."""
        payload = self._payload(self._timed(), 14)
        self.assertIsNone(payload["export_allowance_kwh"])
        self.assertIsNone(payload["export_fallback_price"])

    def test_an_all_day_export_price_can_be_capped(self) -> None:
        plan = self._all_day()
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        payload = self._payload(plan, 10)
        self.assertAlmostEqual(payload["export_per_kwh"], 0.08)
        self.assertEqual(payload["export_allowance_kwh"], 10.0)
        self.assertAlmostEqual(payload["export_fallback_price"], 0.02)

    def test_an_uncapped_export_declares_none(self) -> None:
        plan = Plan(
            "P",
            (Rate("Peak", 0.5688),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "Peak"),)),),
        )
        payload = self._payload(plan, 10)
        self.assertIsNone(payload["export_allowance_kwh"])
        self.assertIsNone(payload["export_fallback_price"])

    def test_the_import_rate_no_longer_carries_an_export_cap(self) -> None:
        """Import and export are separate flows; neither reaches into the other."""
        self.assertNotIn("export_allowance_kwh", Rate.__dataclass_fields__)
        self.assertNotIn("export_fallback_price", Rate.__dataclass_fields__)


class TestTheMidnightWarning(unittest.TestCase):
    """A capped stretch running through midnight gets its cap twice.

    A period cannot cross midnight here, so 22:00 to 02:00 is entered as two
    periods naming the same rate. The count resets at midnight between them.
    Legitimate configuration, but only the user knows how their retailer
    counts it, so it warns rather than refusing to save.
    """

    def _plan(self, *, capped: bool, wraps: bool) -> Plan:
        night = Rate(
            "Night",
            0.0,
            rate_allowance_kwh=24.0 if capped else None,
            fallback_rate="Day" if capped else None,
        )
        periods = (
            (
                Period(0, 120, "Night"),
                Period(120, 1320, "Day"),
                Period(1320, 1440, "Night"),
            )
            if wraps
            else (Period(0, 240, "Night"), Period(240, 1440, "Day"))
        )
        return Plan(
            "P", (night, Rate("Day", 0.33)), (DayPattern("D", ALL_DAYS, periods),)
        )

    def test_a_capped_rate_through_midnight_warns(self) -> None:
        plan = self._plan(capped=True, wraps=True)
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        warnings = _pkg.validate.plan_warnings(plan)
        self.assertEqual(len(warnings), 1)
        self.assertIn("either side of", warnings[0].message)
        # The allowance is the slot's. The old wording said the count reset at
        # midnight on a 24-hour clock, which P13 removed.
        self.assertNotIn("24-hour clock", warnings[0].message)
        self.assertEqual(warnings[0].scope, "Night")

    def test_it_is_a_warning_and_not_a_refusal(self) -> None:
        plan = self._plan(capped=True, wraps=True)
        self.assertEqual(validate_plan(plan), [])

    def test_an_uncapped_rate_through_midnight_says_nothing(self) -> None:
        plan = self._plan(capped=False, wraps=True)
        self.assertEqual(_pkg.validate.plan_warnings(plan), [])

    def test_a_capped_rate_that_does_not_wrap_says_nothing(self) -> None:
        plan = self._plan(capped=True, wraps=False)
        self.assertEqual(_pkg.validate.plan_warnings(plan), [])


class TestRateScoping(unittest.TestCase):
    """A rate is identified by its timetable and its name together.

    A weekday Peak and a weekend Peak are two rates, both called Peak, at
    different prices. The timetable used to be pushed onto the front of the
    name to keep it unique, which made the name the user typed into something
    they never typed and put it on the rate sensor.
    """

    WEEKDAYS = frozenset({"mon", "tue", "wed", "thu", "fri"})
    WEEKEND = frozenset({"sat", "sun", "holiday"})

    def _plan(self) -> Plan:
        return Plan(
            "P",
            (
                Rate("Peak", 0.5688, timetable="Weekday"),
                Rate("Off Peak", 0.198, timetable="Weekday"),
                Rate("Peak", 0.30, timetable="Weekend"),
            ),
            (
                DayPattern(
                    "Weekday",
                    self.WEEKDAYS,
                    (Period(0, 960, "Off Peak"), Period(960, 1440, "Peak")),
                ),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )

    def test_the_plan_is_valid(self) -> None:
        plan = self._plan()
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])

    def test_the_name_is_what_the_user_typed(self) -> None:
        self.assertEqual(self._plan().rate_names, ("Peak", "Off Peak", "Peak"))

    def test_the_identifier_is_qualified(self) -> None:
        self.assertEqual(
            self._plan().qualified_rate_names,
            ("weekday.peak", "weekday.off_peak", "weekend.peak"),
        )

    def test_each_timetable_resolves_to_its_own_rate(self) -> None:
        plan = self._plan()
        friday = plan.resolve(date(2026, 8, 14), 1000, False)
        saturday = plan.resolve(date(2026, 8, 15), 1000, False)
        assert friday is not None and saturday is not None
        self.assertEqual(friday.rate.name, "Peak")
        self.assertEqual(saturday.rate.name, "Peak")
        self.assertAlmostEqual(friday.rate.import_price, 0.5688)
        self.assertAlmostEqual(saturday.rate.import_price, 0.30)

    def test_a_holiday_takes_the_weekend_rate(self) -> None:
        plan = self._plan()
        holiday = plan.resolve(date(2026, 8, 14), 1000, True)
        assert holiday is not None
        self.assertAlmostEqual(holiday.rate.import_price, 0.30)

    def test_lookup_prefers_the_timetable_asked_for(self) -> None:
        plan = self._plan()
        weekday = plan.rate_by_name("Peak", "Weekday")
        weekend = plan.rate_by_name("Peak", "Weekend")
        assert weekday is not None and weekend is not None
        self.assertAlmostEqual(weekday.import_price, 0.5688)
        self.assertAlmostEqual(weekend.import_price, 0.30)

    def test_the_same_name_twice_in_one_timetable_is_refused(self) -> None:
        plan = Plan(
            "P",
            (
                Rate("Peak", 0.5, timetable="Weekday"),
                Rate("Peak", 0.6, timetable="Weekday"),
            ),
            (DayPattern("Weekday", ALL_DAYS, (Period(0, 1440, "Peak"),)),),
        )
        self.assertTrue(any("share a name" in p.message for p in validate_plan(plan)))

    def test_two_names_that_reduce_to_one_id_are_refused(self) -> None:
        """'Off Peak' and 'off-peak' both come out as off_peak."""
        plan = Plan(
            "P",
            (
                Rate("Off Peak", 0.2, timetable="Weekday"),
                Rate("off-peak", 0.3, timetable="Weekday"),
            ),
            (DayPattern("Weekday", ALL_DAYS, (Period(0, 1440, "Off Peak"),)),),
        )
        self.assertTrue(any("the same id" in p.message for p in validate_plan(plan)))

    def test_a_period_naming_another_timetables_rate_is_refused(self) -> None:
        plan = Plan(
            "P",
            (Rate("Peak", 0.5, timetable="Weekday"),),
            (
                DayPattern("Weekday", self.WEEKDAYS, (Period(0, 1440, "Peak"),)),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )
        self.assertTrue(any("does not exist" in p.message for p in validate_plan(plan)))

    def test_a_fallback_resolves_within_its_own_timetable(self) -> None:
        plan = Plan(
            "P",
            (
                Rate(
                    "Free",
                    0.0,
                    timetable="Weekday",
                    rate_allowance_kwh=24.0,
                    fallback_rate="Peak",
                ),
                Rate("Peak", 0.5688, timetable="Weekday"),
                Rate("Peak", 0.30, timetable="Weekend"),
            ),
            (
                DayPattern("Weekday", self.WEEKDAYS, (Period(0, 1440, "Free"),)),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        spent = allowance.apply(plan, plan.rates[0], 30.0)
        self.assertTrue(spent.exhausted)
        self.assertEqual(spent.rate.qualified_name, "weekday.peak")

    def test_an_older_plan_keeps_the_names_and_ids_it_had(self) -> None:
        """No timetable field, prefixed names, already unique. Nothing moves."""
        plan = Plan(
            "P",
            (Rate("Every day Peak", 0.5688),),
            (DayPattern("Every day", ALL_DAYS, (Period(0, 1440, "Every day Peak"),)),),
        )
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        self.assertEqual(plan.qualified_rate_names, ("Every day Peak",))
        resolved = plan.resolve(date(2026, 8, 14), 600, False)
        assert resolved is not None
        self.assertEqual(resolved.rate.qualified_name, "Every day Peak")

    def test_the_timetable_survives_storage(self) -> None:
        original = Rate("Peak", 0.5688, timetable="Weekday")
        rebuilt = Rate.from_dict(original.as_dict())
        self.assertEqual(rebuilt.timetable, "Weekday")
        self.assertEqual(rebuilt.qualified_name, "weekday.peak")

    def test_slugging(self) -> None:
        for raw, expected in (
            ("Weekday", "weekday"),
            ("Every day", "every_day"),
            ("Off Peak", "off_peak"),
            ("Weekend & public holidays", "weekend_public_holidays"),
        ):
            self.assertEqual(_pkg.plan.slug(raw), expected)


class TestEnforceableRules(unittest.TestCase):
    """A rule the user declared part of the meaning of the rate.

    A declaration, not an instruction. This component enforces nothing; it
    says which rules the user meant as rules, and the consumer decides.
    """

    def _rate(self, **kwargs: object) -> Rate:
        return Rate("peak", 0.584, **kwargs)  # type: ignore[arg-type]

    def test_nothing_is_enforceable_unless_it_is_said_to_be(self) -> None:
        rate = self._rate(constraints=frozenset({"no_grid_import"}))
        self.assertEqual(rate.enforceable_constraints, frozenset())
        self.assertEqual(rate.informational_constraints, frozenset({"no_grid_import"}))

    def test_the_roles_stay_apart(self) -> None:
        rate = self._rate(
            constraints=frozenset({"no_grid_import", "precool_opportunity"}),
            enforceable_constraints=frozenset({"no_grid_import"}),
        )
        self.assertEqual(rate.enforceable_constraints, frozenset({"no_grid_import"}))
        self.assertEqual(
            rate.informational_constraints, frozenset({"precool_opportunity"})
        )

    def test_a_stored_plan_without_the_key_loads_as_information_only(self) -> None:
        """Existing rules keep their meaning rather than being promoted."""
        rate = Rate.from_dict(
            {"name": "peak", "import_cents": 58.4, "constraints": ["no_grid_import"]}
        )
        self.assertEqual(rate.constraints, frozenset({"no_grid_import"}))
        self.assertEqual(rate.enforceable_constraints, frozenset())

    def test_the_roles_survive_storage(self) -> None:
        original = self._rate(
            constraints=frozenset({"no_grid_import", "precool_opportunity"}),
            enforceable_constraints=frozenset({"no_grid_import"}),
        )
        rebuilt = Rate.from_dict(original.as_dict())
        self.assertEqual(rebuilt.constraints, original.constraints)
        self.assertEqual(
            rebuilt.enforceable_constraints, original.enforceable_constraints
        )

    def test_enforceable_must_be_a_rule_the_rate_carries(self) -> None:
        plan = Plan(
            "P",
            (self._rate(enforceable_constraints=frozenset({"no_grid_import"})),),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "peak"),)),),
        )
        self.assertTrue(any("does not carry" in p.message for p in validate_plan(plan)))

    def test_the_interval_publishes_both(self) -> None:
        plan = Plan(
            "P",
            (
                self._rate(
                    constraints=frozenset({"no_grid_import", "precool_opportunity"}),
                    enforceable_constraints=frozenset({"no_grid_import"}),
                ),
            ),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "peak"),)),),
        )
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        # The flat list is unchanged for anything that already reads it.
        self.assertEqual(
            payload["constraints"], ["no_grid_import", "precool_opportunity"]
        )
        self.assertEqual(payload["enforceable_constraints"], ["no_grid_import"])


class TestExportBoundaries(unittest.TestCase):
    """A flat import rate with a feed-in schedule still has to wake up.

    This was the bug: only the import periods were ever consulted, so a plan
    whose import price never moved scheduled nothing between midnights and the
    export price sensor held its midnight value for the whole day.
    """

    def _plan(self, *, timed: bool) -> Plan:
        ExportRate = _pkg.plan.ExportRate
        pattern = DayPattern(
            "Every day",
            ALL_DAYS,
            (Period(0, 1440, "flat"),),
            None,
            None,
            (
                Period(0, 540, "night"),
                Period(540, 960, "day"),
                Period(960, 1440, "night"),
            ),
            not timed,
            0.05,
        )
        return Plan(
            "P",
            (Rate("flat", 0.30),),
            (pattern,),
            export_rates=(ExportRate("night", 0.0), ExportRate("day", 0.05)),
        )

    def test_the_plan_is_valid_either_way(self) -> None:
        for timed in (True, False):
            plan = self._plan(timed=timed)
            self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])

    def test_import_boundaries_ignore_the_feed_in_schedule(self) -> None:
        plan = self._plan(timed=True)
        self.assertEqual(plan.boundaries_for(date(2026, 8, 14), False), (0, 1440))

    def test_export_boundaries_are_the_feed_in_schedule(self) -> None:
        plan = self._plan(timed=True)
        self.assertEqual(
            plan.export_boundaries_for(date(2026, 8, 14), False), (0, 540, 960, 1440)
        )

    def test_a_flat_feed_in_price_has_no_boundaries(self) -> None:
        plan = self._plan(timed=False)
        self.assertEqual(plan.export_boundaries_for(date(2026, 8, 14), False), ())

    def test_has_export_periods(self) -> None:
        self.assertTrue(self._plan(timed=True).has_export_periods)
        self.assertFalse(self._plan(timed=False).has_export_periods)

    def test_the_price_really_does_change_at_that_boundary(self) -> None:
        plan = self._plan(timed=True)
        day = date(2026, 8, 14)
        self.assertAlmostEqual(plan.export_price_at(day, 539, False), 0.0)
        self.assertAlmostEqual(plan.export_price_at(day, 541, False), 0.05)

    def test_the_next_feed_in_change_is_found(self) -> None:
        plan = self._plan(timed=True)
        now = datetime(2026, 8, 14, 8, 0, tzinfo=BRISBANE)
        nxt = intervals.next_boundary(plan, now, BRISBANE, never_holiday, export=True)
        assert nxt is not None
        local = nxt.astimezone(BRISBANE)
        self.assertEqual((local.hour, local.minute), (9, 0))

    def test_the_import_rate_meanwhile_holds_until_midnight(self) -> None:
        plan = self._plan(timed=True)
        now = datetime(2026, 8, 14, 8, 0, tzinfo=BRISBANE)
        nxt = intervals.next_boundary(plan, now, BRISBANE, never_holiday)
        assert nxt is not None
        self.assertEqual(nxt.astimezone(BRISBANE).date(), date(2026, 8, 15))

    def test_a_flat_feed_in_price_schedules_nothing(self) -> None:
        plan = self._plan(timed=False)
        now = datetime(2026, 8, 14, 8, 0, tzinfo=BRISBANE)
        self.assertIsNone(
            intervals.next_boundary(plan, now, BRISBANE, never_holiday, export=True)
        )


class TestAllowance(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = Plan(
            "P",
            (
                Rate("free", 0.0, rate_allowance_kwh=24.0, fallback_rate="standard"),
                Rate("standard", 0.321),
            ),
            (DayPattern("D", ALL_DAYS, (Period(0, 1440, "free"),)),),
        )

    def test_within_allowance(self) -> None:
        state = allowance.apply(self.plan, self.plan.rates[0], 10.0)
        self.assertEqual(state.rate.name, "free")
        self.assertFalse(state.exhausted)
        self.assertAlmostEqual(state.remaining_kwh or 0.0, 14.0)

    def test_beyond_allowance_falls_back(self) -> None:
        state = allowance.apply(self.plan, self.plan.rates[0], 30.0)
        self.assertEqual(state.rate.name, "standard")
        self.assertTrue(state.exhausted)

    def test_exactly_at_the_allowance_falls_back(self) -> None:
        state = allowance.apply(self.plan, self.plan.rates[0], 24.0)
        self.assertTrue(state.exhausted)

    def test_rate_without_allowance(self) -> None:
        state = allowance.apply(self.plan, self.plan.rates[1], 100.0)
        self.assertIsNone(state.remaining_kwh)
        self.assertFalse(state.exhausted)

    def test_accumulate_ignores_a_meter_reset(self) -> None:
        self.assertEqual(allowance.accumulate(100.0, 5.0, 12.0), 12.0)

    def test_accumulate_ignores_missing_readings(self) -> None:
        self.assertEqual(allowance.accumulate(None, 5.0, 12.0), 12.0)
        self.assertEqual(allowance.accumulate(5.0, None, 12.0), 12.0)

    def test_accumulate_adds_the_delta(self) -> None:
        self.assertAlmostEqual(allowance.accumulate(5.0, 7.5, 12.0), 14.5)


class TestSerialise(unittest.TestCase):
    def test_windows_csv_has_a_row_per_window(self) -> None:
        text = serialise.periods_to_csv(sample_plan())
        self.assertEqual(len(text.strip().splitlines()), 7)

    def test_rates_csv_has_a_row_per_rate(self) -> None:
        text = serialise.rates_to_csv(sample_plan())
        self.assertEqual(len(text.strip().splitlines()), 5)


class TestStorageRoundTrip(unittest.TestCase):
    def test_plan_survives_a_round_trip(self) -> None:
        original = sample_plan()
        rebuilt = Plan.from_dict(
            {
                **original.as_dict(),
                "rates": [rate.as_dict() for rate in original.rates],
                "day_patterns": [ds.as_dict() for ds in original.day_patterns],
            }
        )
        self.assertEqual(rebuilt.rate_names, original.rate_names)
        self.assertEqual(rebuilt.day_pattern_names, original.day_pattern_names)
        self.assertAlmostEqual(
            rebuilt.rate_by_name("peak").import_price,  # type: ignore[union-attr]
            0.584,
        )
        self.assertAlmostEqual(rebuilt.daily_supply_charge, 1.25)
        self.assertTrue(is_valid(rebuilt))

    def test_rate_without_a_name_is_rejected(self) -> None:
        with self.assertRaises(PlanError):
            Rate.from_dict({"import_cents": 30})

    def test_unknown_day_token_is_rejected(self) -> None:
        with self.assertRaises(PlanError):
            DayPattern.from_dict({"name": "D", "days": ["funday"], "periods": []})


class TestValidity(unittest.TestCase):
    def test_active_range(self) -> None:
        plan = Plan(
            "P",
            valid_from=date(2026, 7, 1),
            valid_to=date(2027, 6, 30),
        )
        self.assertFalse(plan.is_active_on(date(2026, 6, 30)))
        self.assertTrue(plan.is_active_on(date(2026, 7, 1)))
        self.assertTrue(plan.is_active_on(date(2027, 6, 30)))
        self.assertFalse(plan.is_active_on(date(2027, 7, 1)))

    def test_open_ended(self) -> None:
        self.assertTrue(Plan("P").is_active_on(date(2030, 1, 1)))


if __name__ == "__main__":
    unittest.main()


class TestExportSide(unittest.TestCase):
    def _plan(self, flat: bool) -> Plan:
        ExportRate = _pkg.plan.ExportRate
        pattern = DayPattern(
            "Every day",
            ALL_DAYS,
            (Period(0, 1440, "standard"),),
            None,
            None,
            (Period(0, 960, "daytime"), Period(960, 1440, "evening")),
            flat,
            0.05,
        )
        return Plan(
            "P",
            (Rate("standard", 0.32),),
            (pattern,),
            export_rates=(ExportRate("daytime", 0.027), ExportRate("evening", 0.12)),
        )

    def test_flat_export_ignores_periods(self) -> None:
        plan = self._plan(flat=True)
        self.assertAlmostEqual(
            plan.export_price_at(date(2026, 8, 14), 1200, False), 0.05
        )
        self.assertTrue(is_valid(plan))

    def test_export_periods_are_independent_of_import(self) -> None:
        plan = self._plan(flat=False)
        self.assertAlmostEqual(
            plan.export_price_at(date(2026, 8, 14), 600, False), 0.027
        )
        self.assertAlmostEqual(
            plan.export_price_at(date(2026, 8, 14), 1200, False), 0.12
        )
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])

    def test_mode_is_per_timetable(self) -> None:
        ExportRate = _pkg.plan.ExportRate
        weekday = DayPattern(
            "Weekday",
            frozenset({"mon", "tue", "wed", "thu", "fri"}),
            (Period(0, 1440, "standard"),),
            None,
            None,
            (),
            True,
            0.05,
        )
        weekend = DayPattern(
            "Weekend",
            frozenset({"sat", "sun", "holiday"}),
            (Period(0, 1440, "standard"),),
            None,
            None,
            (Period(0, 1440, "evening"),),
            False,
            0.0,
        )
        plan = Plan(
            "P",
            (Rate("standard", 0.32),),
            (weekday, weekend),
            export_rates=(ExportRate("evening", 0.12),),
        )
        self.assertTrue(is_valid(plan), [str(p) for p in validate_plan(plan)])
        self.assertAlmostEqual(
            plan.export_price_at(date(2026, 8, 14), 600, False), 0.05
        )
        self.assertAlmostEqual(
            plan.export_price_at(date(2026, 8, 15), 600, False), 0.12
        )

    def test_incomplete_export_day_is_rejected(self) -> None:
        ExportRate = _pkg.plan.ExportRate
        broken = Plan(
            "P",
            (Rate("standard", 0.32),),
            (
                DayPattern(
                    "Every day",
                    ALL_DAYS,
                    (Period(0, 1440, "standard"),),
                    None,
                    None,
                    (Period(0, 600, "daytime"),),
                    False,
                    0.0,
                ),
            ),
            export_rates=(ExportRate("daytime", 0.027),),
        )
        self.assertFalse(is_valid(broken))

    def test_demand_rate_round_trips(self) -> None:
        """P27: the demand rate belongs to the rate, not the plan."""
        plan = Plan(
            "P", (Rate("a", 0.1, demand_period=True, demand_rate_per_kw_month=12.5),)
        )
        rebuilt = Plan.from_dict(
            {
                **plan.as_dict(),
                "rates": [rate.as_dict() for rate in plan.rates],
                "day_patterns": [],
            }
        )
        self.assertAlmostEqual(rebuilt.rates[0].demand_rate_per_kw_month, 12.5)
        self.assertTrue(rebuilt.rates[0].demand_period)

    def test_an_old_plan_wide_demand_rate_is_not_read(self) -> None:
        """No migration, pre-release: the old key is silently ignored on load."""
        rebuilt = Plan.from_dict(
            {
                "name": "P",
                "rates": [Rate("a", 0.1).as_dict()],
                "day_patterns": [],
                "demand_rate_per_kw_month": 12.5,
            }
        )
        self.assertEqual(rebuilt.rates[0].demand_rate_per_kw_month, 0.0)

    def _round_trip(self, plan: Plan) -> Plan:
        return Plan.from_dict(
            {
                **plan.as_dict(),
                "rates": [rate.as_dict() for rate in plan.rates],
                "export_rates": [rate.as_dict() for rate in plan.export_rates],
                "day_patterns": [pattern.as_dict() for pattern in plan.day_patterns],
            }
        )

    def test_an_export_rates_cap_round_trips(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            export_rates=(
                ExportRate("Morning", 0.08, allowance_kwh=10.0, fallback_price=0.02),
            ),
        )
        rebuilt = self._round_trip(plan)
        self.assertEqual(rebuilt.export_rates[0].allowance_kwh, 10.0)
        self.assertAlmostEqual(rebuilt.export_rates[0].fallback_price, 0.02)

    def test_no_export_cap_round_trips_as_none(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),), export_rates=(ExportRate("M", 0.08),))
        rebuilt = self._round_trip(plan)
        self.assertIsNone(rebuilt.export_rates[0].allowance_kwh)
        self.assertIsNone(rebuilt.export_rates[0].fallback_price)

    def test_an_all_day_cap_round_trips_on_the_timetable(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (
                DayPattern(
                    "D",
                    ALL_DAYS,
                    (Period(0, 1440, "a"),),
                    export_flat_price=0.08,
                    export_allowance_kwh=10.0,
                    export_fallback_price=0.02,
                ),
            ),
        )
        rebuilt = self._round_trip(plan)
        self.assertEqual(rebuilt.day_patterns[0].export_allowance_kwh, 10.0)
        self.assertAlmostEqual(rebuilt.day_patterns[0].export_fallback_price, 0.02)


class TestPlanText(unittest.TestCase):
    """The plan is rendered as text, exact everywhere.

    The coloured bar that used to be here could not be aligned: the ruler was
    plain characters, the bar was emoji, and their widths differ by font, so it
    drifted against the clock by hours. The picture is Home Assistant's job now
    — the rate sensor is an enum, so a history-graph card draws it natively.
    """

    WEEKDAYS = frozenset({"mon", "tue", "wed", "thu", "fri"})
    WEEKEND = frozenset({"sat", "sun", "holiday"})

    def test_each_period_shows_its_hours_rate_and_price(self) -> None:
        text = strip.render_day_pattern(sample_plan(), sample_plan().day_patterns[0])
        self.assertIn("00:00-06:00", text)
        self.assertIn("cheap", text)
        self.assertIn("19.80 c/kWh", text)
        self.assertIn("16:00-21:00", text)
        self.assertIn("58.40 c/kWh", text)

    def test_complete_coverage_says_so(self) -> None:
        text = strip.render_plan(sample_plan())
        self.assertIn("Coverage: complete", text)
        self.assertIn("Every day", text)

    def test_a_gap_is_named(self) -> None:
        plan = Plan(
            "P", (Rate("a", 0.1),), (DayPattern("D", ALL_DAYS, (Period(0, 360, "a"),)),)
        )
        self.assertIn("nothing covers", strip.render_plan(plan))

    def test_an_overlap_is_named(self) -> None:
        plan = Plan(
            "P",
            (Rate("a", 0.1), Rate("b", 0.2)),
            (DayPattern("D", ALL_DAYS, (Period(0, 800, "a"), Period(600, 1440, "b"))),),
        )
        self.assertIn("overlaps", strip.render_plan(plan))

    def test_a_missing_rate_is_named(self) -> None:
        plan = Plan(
            "P",
            (Rate("peak", 0.5),),
            (DayPattern("Every day", ALL_DAYS, (Period(0, 1440, "missing"),)),),
        )
        self.assertIn(
            "rate missing", strip.render_day_pattern(plan, plan.day_patterns[0])
        )

    def test_the_price_column_lines_up_whatever_the_names_are(self) -> None:
        """A fixed pad bends the column the moment a name outgrows it."""
        plan = Plan(
            "P",
            (
                Rate("Every day Super Off Peak", 0.0),
                Rate("Every day EV Charging", 0.08),
                Rate("Every day Peak", 0.3685),
            ),
            (
                DayPattern(
                    "Every day",
                    ALL_DAYS,
                    (
                        Period(0, 360, "Every day EV Charging"),
                        Period(360, 660, "Every day Super Off Peak"),
                        Period(660, 1440, "Every day Peak"),
                    ),
                ),
            ),
        )
        text = strip.render_plan(plan)
        # Table rows end at the price column. The flat feed-in line is a
        # sentence ("2.90 c/kWh all day"), not a row, and is not in the table.
        columns = {
            line.index("c/kWh") for line in text.splitlines() if line.endswith("c/kWh")
        }
        self.assertEqual(len(columns), 1, text)

    def test_the_plan_is_one_table_with_no_second_listing(self) -> None:
        """The identifier is a column, not a block underneath restating it."""
        text = strip.render_plan(sample_plan())
        self.assertNotIn("Rates\n", text)
        # Every price appears against the period that charges it, once.
        peak = [line for line in text.splitlines() if "peak" in line]
        self.assertTrue(peak)
        for line in peak:
            self.assertRegex(line.strip(), r"^\d\d:\d\d-\d\d:\d\d")

    def test_every_row_carries_the_published_identifier(self) -> None:
        """The second block existed to show this; it is a column now."""
        plan = Plan(
            "P",
            (Rate("Peak", 0.5, timetable="Every day"),),
            (DayPattern("Every day", ALL_DAYS, (Period(0, 1440, "Peak"),)),),
        )
        row = strip.render_day_pattern(plan, plan.day_patterns[0]).splitlines()[1]
        self.assertIn("00:00-24:00", row)
        self.assertIn("Peak", row)
        self.assertIn("every_day.peak", row)
        self.assertIn("50.00 c/kWh", row)

    def test_the_columns_line_up_across_the_whole_plan(self) -> None:
        """One width per column, measured once, or the table bends."""
        plan = Plan(
            "P",
            (
                Rate("Super Off Peak", 0.0, timetable="Every day"),
                Rate("EV", 0.08, timetable="Every day"),
            ),
            (
                DayPattern(
                    "Every day",
                    ALL_DAYS,
                    (Period(0, 360, "EV"), Period(360, 1440, "Super Off Peak")),
                ),
            ),
        )
        rows = [
            line
            for line in strip.render_plan(plan).splitlines()
            if line.strip().startswith("0") or line.strip().startswith("1")
        ]
        columns = {line.index("c/kWh") for line in rows if "c/kWh" in line}
        self.assertEqual(len(columns), 1, rows)

    def test_nothing_entered_yet(self) -> None:
        self.assertIn("No rates", strip.render_plan(Plan("P")))
        self.assertIn(
            "No day patterns", strip.render_plan(Plan("P", (Rate("a", 0.1),)))
        )

    def test_a_season_is_named(self) -> None:
        pattern = DayPattern(
            "Summer", ALL_DAYS, (Period(0, 1440, "peak"),), (11, 1), (3, 31)
        )
        plan = Plan("P", (Rate("peak", 0.5),), (pattern,))
        self.assertIn("01/11", strip.render_day_pattern(plan, pattern))

    def test_a_flat_feed_in_states_the_price(self) -> None:
        pattern = DayPattern(
            "Every day", ALL_DAYS, (Period(0, 1440, "a"),), None, None, (), True, 0.05
        )
        plan = Plan("P", (Rate("a", 0.1),), (pattern,))
        self.assertIn("Feed-in: 5.00 c/kWh all day", strip.render_plan(plan))

    def test_a_timed_feed_in_lists_its_periods(self) -> None:
        ExportRate = _pkg.plan.ExportRate
        pattern = DayPattern(
            "Every day",
            ALL_DAYS,
            (Period(0, 1440, "a"),),
            None,
            None,
            (Period(0, 960, "daytime"), Period(960, 1440, "evening")),
            False,
            0.0,
        )
        plan = Plan(
            "P",
            (Rate("a", 0.1),),
            (pattern,),
            export_rates=(ExportRate("daytime", 0.027), ExportRate("evening", 0.12)),
        )
        text = strip.render_plan(plan)
        self.assertIn("16:00-24:00", text)
        self.assertIn("evening", text)
        self.assertIn("12.00 c/kWh", text)

    def test_two_timetables_with_the_same_rate_name_stay_apart(self) -> None:
        plan = Plan(
            "P",
            (
                Rate("Peak", 0.5688, timetable="Weekday"),
                Rate("Peak", 0.30, timetable="Weekend"),
            ),
            (
                DayPattern("Weekday", self.WEEKDAYS, (Period(0, 1440, "Peak"),)),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )
        weekday, weekend = (
            strip.render_day_pattern(plan, pattern) for pattern in plan.day_patterns
        )
        self.assertIn("56.88 c/kWh", weekday)
        self.assertIn("30.00 c/kWh", weekend)
        self.assertNotIn("rate missing", weekday + weekend)
        # The identifier is on the row, so the two Peaks are told apart in
        # place rather than in a separate listing.
        self.assertIn("weekday.peak", weekday)
        self.assertIn("weekend.peak", weekend)
        self.assertNotIn("weekend.peak", weekday)

    def test_the_card_resolves_every_period(self) -> None:
        plan = Plan(
            "P",
            (
                Rate("Peak", 0.5688, timetable="Weekday"),
                Rate("Peak", 0.30, timetable="Weekend"),
            ),
            (
                DayPattern("Weekday", self.WEEKDAYS, (Period(0, 1440, "Peak"),)),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )
        card = strip.render_rate_plan_card(plan)
        self.assertNotIn("rate missing", card)
        self.assertIn("buy 0.5688/kWh", card)
        self.assertIn("buy 0.3000/kWh", card)

    def test_the_card_shows_tax_and_charges(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),), daily_supply_charge=1.166)
        card = strip.render_rate_plan_card(plan)
        self.assertIn("Prices include GST", card)
        self.assertIn("Daily supply charge: 116.60 c/day", card)

    def test_the_card_shows_a_monthly_charge_only_when_there_is_one(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),))
        self.assertNotIn("Monthly charge", strip.render_rate_plan_card(plan))
        with_fee = Plan("P", (Rate("a", 0.1),), monthly_charge=19.0)
        self.assertIn("Monthly charge: 19.00", strip.render_rate_plan_card(with_fee))

    def test_the_card_says_when_prices_exclude_tax(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),), prices_include_gst=False)
        self.assertIn("exclude tax", strip.render_rate_plan_card(plan))


class TestTheCsvNamesRatesUnambiguously(unittest.TestCase):
    """A bare rate name is not unique: two timetables can each have a Peak."""

    WEEKDAYS = frozenset(("mon", "tue", "wed", "thu", "fri"))
    WEEKEND = frozenset(("sat", "sun"))

    def _plan(self) -> Plan:
        return Plan(
            "P",
            (
                Rate("Peak", 0.5688, timetable="Weekday"),
                Rate("Peak", 0.30, timetable="Weekend"),
            ),
            (
                DayPattern("Weekday", self.WEEKDAYS, (Period(0, 1440, "Peak"),)),
                DayPattern("Weekend", self.WEEKEND, (Period(0, 1440, "Peak"),)),
            ),
        )

    def test_the_rates_csv_carries_the_identifier(self) -> None:
        text = _pkg.serialise.rates_to_csv(self._plan())
        self.assertIn("rate_id", text.splitlines()[0])
        self.assertIn("weekday.peak", text)
        self.assertIn("weekend.peak", text)

    def test_the_periods_csv_carries_the_identifier(self) -> None:
        text = _pkg.serialise.periods_to_csv(self._plan())
        self.assertIn("rate_id", text.splitlines()[0])
        self.assertIn("weekday.peak", text)
        self.assertIn("weekend.peak", text)


class TestTheCardNamesTheBillingDay(unittest.TestCase):
    def test_the_day_is_stated_when_there_is_one(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),), billing_cycle_day=12)
        self.assertIn("day 12", strip.render_rate_plan_card(plan))

    def test_nothing_is_said_when_there_is_none(self) -> None:
        plan = Plan("P", (Rate("a", 0.1),))
        self.assertNotIn("Billing cycle", strip.render_rate_plan_card(plan))


class TestTheFallbackIsAskedOfTheRate(unittest.TestCase):
    """P31 change three: a rate says which timetable it belongs to.

    ``validate_rates`` and ``allowance.apply`` both look a fallback up in the
    rate's own timetable. ``intervals.generate`` used the day set it happened
    to resolve through, which is a different question, so the published series
    could name a fallback validation never approved.

    A rate stored before rates were scoped belongs to no timetable and
    resolves in any. Its fallback belongs to no timetable either, so it is the
    unscoped rate of that name — not whichever scoped one shares it.
    """

    def _plan(self) -> Plan:
        return Plan(
            "P",
            (
                # Unscoped: what a plan written before the scoping looks like.
                Rate("Capped", 0.0, rate_allowance_kwh=24.0, fallback_rate="Cheap"),
                Rate("Cheap", 0.10),
                # Same name, but belonging to the day set being resolved.
                Rate("Cheap", 0.99, timetable="Weekday"),
            ),
            (DayPattern("Weekday", ALL_DAYS, (Period(0, 1440, "Capped"),)),),
        )

    def test_the_series_names_the_fallback_validation_approved(self) -> None:
        plan = self._plan()
        approved = plan.rate_by_name("Cheap", plan.rates[0].timetable)
        assert approved is not None
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertAlmostEqual(payload["fallback_per_kwh"], approved.import_price)
        self.assertAlmostEqual(payload["fallback_per_kwh"], 0.10)

    def test_the_allowance_module_agrees_with_the_series(self) -> None:
        plan = self._plan()
        spent = allowance.apply(plan, plan.rates[0], 30.0)
        start = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        payload = intervals.generate(plan, start, BRISBANE, never_holiday, hours=1)[
            0
        ].as_dict()
        self.assertAlmostEqual(payload["fallback_per_kwh"], spent.rate.import_price)
