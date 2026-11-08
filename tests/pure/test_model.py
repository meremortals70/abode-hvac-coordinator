"""model/: the room's model, its filters, the unit, trust and forecasts (Module 2)."""

from __future__ import annotations

import dataclasses
import itertools
import math
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

physics = load("model.physics")
filt = load("model.filter")
unit = load("model.unit")
hum = load("model.humidity")
trust_mod = load("model.trust")
fc = load("model.forecast")
model = load("model")
models = load("models")
la = load("linalg")
hci = load("hci")
observe = load("observe")
VT = models.ValueTag
CS = models.CompressorState

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EFF = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)


def inputs(outdoor: float = 24.0, sun: float = 0.0, effort: float = 0.0, direction: int = 0,
           u0: float = 0.0) -> Any:
    return physics.StepInputs(outdoor, sun, (12.0,) * 5, ((effort, direction),) * 40, u0, EFF)


def with_unit(z: list[float], lag: float, delay: float) -> list[float]:
    z = list(z)
    z[physics.I_LN_LAG] = math.log(lag)
    z[physics.I_DELAY] = delay
    return z


class Physics(unittest.TestCase):
    def test_curve_rises_and_inverts(self) -> None:
        steps = (0.1, -0.5, 0.3, -2.0, 0.0, 0.2)
        values = physics.curve_values(steps)
        self.assertTrue(all(b > a for a, b in itertools.pairwise(values)))
        for x in (0.5, 0.7, 1.9, 3.5, 4.0):
            self.assertAlmostEqual(physics.curve_effort(EFF, steps, physics.curve_rate(EFF, steps, x)), x, places=9)
        # below the first effort a running unit runs at its lowest rate
        self.assertEqual(physics.curve_rate(EFF, steps, 0.2), values[0])
        self.assertEqual(physics.curve_effort(EFF, steps, values[0] / 2), 0.5)
        self.assertEqual(physics.curve_rate(EFF, steps, 9.0), values[-1])
        self.assertEqual(physics.curve_effort(EFF, steps, values[-1] * 2), 4.0)  # [D056]
        self.assertEqual(physics.curve_rate(EFF, steps, -1.0), 0.0)
        self.assertEqual(physics.curve_rate(EFF, (0.0,) * 6, 2.0), 4.0)  # ln 1 steps: 1, 2, 3, 4 ...

    def test_rest_at_equilibrium_stays(self) -> None:
        z = filt.start_state(24.0)
        z[physics.I_G0] = 0.0
        out = physics.step(z, inputs()).z
        self.assertAlmostEqual(out[physics.I_T], 24.0, places=12)
        self.assertAlmostEqual(out[physics.I_M], 24.0, places=12)

    def test_heat_balance_terms(self) -> None:
        z = filt.start_state(24.0)
        fig = physics.figures(z)
        out = physics.step(z, inputs(outdoor=30.0)).z
        # first minute: dT = a (30 - 24) per hour; five Euler minutes
        self.assertGreater(out[physics.I_T], 24.0)
        self.assertAlmostEqual(out[physics.I_T] - 24.0, fig.a * 6.0 * 5 / 60, delta=0.002)
        self.assertGreater(out[physics.I_M], 24.0)

    def test_step_matches_a_reference_for_every_term(self) -> None:
        z = filt.start_state(22.0)
        z[physics.I_M] = 25.0
        z[physics.I_LN_S] = math.log(0.7)
        z[physics.I_G0], z[physics.I_AL1], z[physics.I_BE1] = 0.2, 0.1, -0.05
        z[physics.I_AL2], z[physics.I_BE2] = 0.07, 0.03
        z = with_unit(z, 12.0, 0.0)
        hours = (14.0, 14.0 + 1 / 60, 14.0 + 2 / 60, 14.0 + 3 / 60, 14.0 + 4 / 60)
        si = physics.StepInputs(31.0, 0.6, hours, ((1.2, 1),) * 5, 0.5, EFF)
        got = physics.step(z, si)
        a, b, c, k, s_, m = 0.2, 0.2, 0.3, 0.1, 0.7, 0.1
        t, mass, u = 22.0, 25.0, 0.5
        # the reference: each minute's inputs held, the heat balance stepped
        # in 6000 straight-line slices, which converges on the exact answer
        n = 6000
        for h in hours:
            w = 2 * math.pi * h / 24
            g = 0.2 + 0.1 * math.cos(w) - 0.05 * math.sin(w) + 0.07 * math.cos(2 * w) + 0.03 * math.sin(2 * w)
            u += (2.4 - u) / 12.0  # ln 1 curve: 2 C/h at 1.0, 3 at 1.5 -> 2.4 at 1.2
            for _ in range(n):
                dt = a * (31.0 - t) + b * (mass - t) + s_ * 0.6 + g - u
                dm = c * (t - mass) + k * (31.0 - mass) + m * 0.6
                t, mass = t + dt / 60 / n, mass + dm / 60 / n
        self.assertAlmostEqual(got.z[physics.I_T], t, places=6)
        self.assertAlmostEqual(got.z[physics.I_M], mass, places=6)
        self.assertAlmostEqual(got.u_end, u, places=12)

    def test_step_stays_bounded_for_any_positive_figures(self) -> None:
        """A figure just taught can be large: the minute step is the heat
        balance's exact solution, which a straight-line minute is not once
        c + k passes 120 per hour (here c = 180, as one correction made it
        in the office's history on 26 October) [Der]."""
        z = filt.start_state(28.0)
        z[physics.I_M] = 26.0
        z[physics.I_LN_C] = math.log(180.0)
        z[physics.I_LN_K] = math.log(3.65)
        z[physics.I_LN_B] = math.log(11.3)
        z = with_unit(z, 12.0, 0.0)
        si = physics.StepInputs(31.0, 0.0, (14.0,) * 5, ((0.0, 0),) * 5, 0.0, EFF)
        for _ in range(12):  # an hour
            z = physics.step(z, si).z
        self.assertTrue(26.0 <= z[physics.I_T] <= 31.0)
        self.assertTrue(26.0 <= z[physics.I_M] <= 31.0)

    def test_lag_held_between_one_and_thirty_minutes(self) -> None:
        """The lag is read and kept within 1 to 30 minutes [R19]."""
        z = filt.start_state(24.0)
        z[physics.I_LN_LAG] = math.log(23829.4)  # the office's stored lag
        self.assertEqual(physics.figures(z).lag_min, 30.0)
        z[physics.I_LN_LAG] = math.log(0.2)
        self.assertEqual(physics.figures(z).lag_min, 1.0)

    def test_unit_lag(self) -> None:
        z = with_unit(filt.start_state(24.0), 10.0, 0.0)
        r = physics.step(z, inputs(effort=2.0, direction=1))
        want = 4.0 * (1 - (1 - 1 / 10) ** 5)
        self.assertAlmostEqual(r.u_end, want, places=12)
        h = physics.step(z, inputs(effort=2.0, direction=-1))
        self.assertAlmostEqual(h.u_end, -want, places=12)
        self.assertLess(r.z[physics.I_T], 24.0)
        self.assertGreater(h.z[physics.I_T], 24.0)

    def test_delay_takes_the_rate_from_earlier_minutes(self) -> None:
        z = with_unit(filt.start_state(24.0), 1.0, 2.5)  # lag 1 min: U is the delayed rate
        hist = tuple([(0.0, 0)] * 10 + [(2.0, 1)] * 5)  # cooling began with this step
        r = physics.step(z, physics.StepInputs(24.0, 0.0, (12.0,) * 5, hist, 0.0, EFF))
        # minute i reads position 10 + i - 2.5: rest, rest, half, full, full
        self.assertEqual(r.u_path, (0.0, 0.0, 2.0, 4.0, 4.0))

    def test_delay_and_lag_are_bounded(self) -> None:
        fig = physics.figures(with_unit(filt.start_state(24.0), 0.2, 45.0))
        self.assertEqual((fig.lag_min, fig.delay_min), (1.0, 30.0))
        fig = physics.figures(with_unit(filt.start_state(24.0), 15.0, -3.0))
        self.assertEqual(fig.delay_min, 0.0)
        self.assertAlmostEqual(fig.lag_min, 15.0)

    def test_before_any_history_the_unit_rested(self) -> None:
        z = with_unit(filt.start_state(24.0), 1.0, 6.0)
        r = physics.step(z, physics.StepInputs(24.0, 0.0, (12.0,) * 5, ((2.0, 1),) * 5, 0.0, EFF))
        self.assertEqual(r.u_path, (0.0,) * 5)

    def test_exact_step_matches_fine_integration(self) -> None:
        fig = physics.figures(filt.start_state(20.0))
        ex = physics.exact_step(fig)
        t, m = 20.0, 22.0
        out, sun, g, u = 30.0, 0.6, 0.3, 1.5
        got = ex.advance((t, m), out, sun, g, u)
        n = 30000
        dt = physics.STEP_H / n
        for _ in range(n):
            dT = fig.a * (out - t) + fig.b * (m - t) + fig.s * sun + g - u
            dM = fig.c * (t - m) + fig.k * (out - m) + fig.m * sun
            t, m = t + dT * dt, m + dM * dt
        self.assertAlmostEqual(got[0], t, places=6)
        self.assertAlmostEqual(got[1], m, places=6)

    def test_steady_gain_is_g_of_zero(self) -> None:
        fig = physics.figures(filt.start_state(20.0))
        a = physics.a_matrix(fig)
        # steady state for a unit rate U = 1: A x = -[-1, 0]
        x = la.mat_vec(la.inverse_2x2(a), [1.0, 0.0])
        self.assertAlmostEqual(abs(x[0]), physics.steady_gain_h(fig), places=12)
        fast, slow = physics.time_constants_h(fig)
        self.assertGreater(slow, fast)
        self.assertGreater(fast, 0.0)


def reading_sample(reading: float | None = 24.0, *, known: bool = True, running: bool = False,
                   sun_est: bool = False, outdoor: float | None = 24.0, t: datetime = NOW) -> Any:
    return filt.MinuteSample(t, 12.0, outdoor, 60.0, 0.0, sun_est, reading, 50.0, 0.0, 0, 0, False, known,
                             running)


def feed(f: Any, samples: list[Any]) -> Any:
    for s in samples:
        f.add_minute(s)
    return f.five_minutes(NOW)


class Filter(unittest.TestCase):
    def ready(self, t0: float = 24.0) -> Any:
        f = filt.HeatFilter.start(t0)
        f.observe_reading(24.0)
        f.observe_reading(24.2)  # q = 0.2
        f.observe_reading(24.0)
        f._history.extend([(0.0, 0)] * physics.HISTORY_MIN)  # the unit known to have rested
        f._history_known.extend([True] * physics.HISTORY_MIN)
        f._seen_minutes = 24 * 60  # running a day since start-up
        return f

    def test_nothing_is_learned_until_the_units_rate_is_known(self) -> None:
        """After a start or a restart the unit's rate starts at 0 and comes
        through its delay and lag: the figures learn nothing until the
        delay and three lags have passed (6 + 3 x 15 = 51 minutes here, more
        than 40), while the room and mass are corrected throughout."""
        f = filt.HeatFilter.from_dict(self.ready().as_dict())
        f.observe_reading(24.0)
        f.observe_reading(24.2)
        f.observe_reading(24.0)
        a0 = f.z[physics.I_LN_A]
        for minute in range(5, 55, 5):  # 5 to 50 minutes seen
            r = feed(f, [reading_sample(24.2)] * 5)
            self.assertFalse(r.corrected, minute)
            self.assertIsNotNone(r.innovation, minute)  # the room was corrected
            self.assertIn("not yet known since start-up", r.reason)
        self.assertEqual(f.z[physics.I_LN_A], a0)
        self.assertTrue(feed(f, [reading_sample(24.2)] * 5).corrected)  # 55 seen

    def test_start(self) -> None:
        f = filt.HeatFilter.start(23.5)
        self.assertEqual((f.z[0], f.z[1]), (23.5, 23.5))
        self.assertAlmostEqual(math.exp(f.z[physics.I_LN_A]), 0.2)
        self.assertAlmostEqual(math.exp(f.z[physics.I_LN_C]), 0.3)
        self.assertEqual([f.p[i][i] for i in range(8)], [0.01, 4.0] + [1.0] * 6)
        self.assertEqual(f.p[physics.I_G0][physics.I_G0], 0.25)
        self.assertEqual(f.p[physics.I_COOL][physics.I_COOL], 0.5)
        self.assertEqual(len(f.z), 27)
        self.assertEqual((f.delay_min, f.lag_min), (6.0, 15.0))
        self.assertEqual((f.p[physics.I_LN_LAG][physics.I_LN_LAG], f.p[physics.I_DELAY][physics.I_DELAY]),
                         (1.0, 25.0))

    def test_process_noise(self) -> None:
        q = filt.process_noise(False)
        self.assertAlmostEqual(q[0][0], 1e-3 * 5 / 60)
        self.assertAlmostEqual(q[1][1], 1e-4 * 5 / 60)
        self.assertEqual(q[2][2], 1e-6)
        self.assertEqual(q[24][24], 1e-6)
        self.assertEqual((q[physics.I_LN_LAG][physics.I_LN_LAG], q[physics.I_DELAY][physics.I_DELAY]), (1e-5, 1e-3))
        self.assertAlmostEqual(filt.process_noise(True)[0][0], 1e-3 * 5 / 60 + (0.5 * 5 / 60) ** 2)

    def test_sensor_step_and_noise_floor(self) -> None:
        f = self.ready()
        self.assertAlmostEqual(f.sensor_step, 0.2)
        self.assertAlmostEqual(f.noise, 0.04 / 12)
        self.assertAlmostEqual(f.innovation_avg, 0.04 / 12)

    def test_correction_pulls_toward_the_reading(self) -> None:
        f = self.ready()
        before = f.p[0][0]
        r = feed(f, [reading_sample(24.2)] * 5)  # one sensor step: inside the gate
        self.assertTrue(r.corrected)
        self.assertGreater(f.z[0], 24.0)
        self.assertLess(f.z[0], 24.2)
        self.assertLess(f.p[0][0], before + 1e-3)
        self.assertEqual(f.corrections, 1)
        self.assertAlmostEqual(f.noise, 0.04 / 12, places=15)  # q^2/12 for the first day
        v0 = 0.04 / 12
        nu = r.innovation
        self.assertAlmostEqual(f.innovation_avg, v0 + (nu * nu - v0) / 288, places=15)

    def test_a_surprise_moves_the_state_no_more_than_a_gated_one(self) -> None:
        """A reading far outside what the filter expected is weighted as a
        3.3-sigma surprise at most: in the office's history one 0.4 C step,
        taken as exact, moved c from 0.3 to 180 per hour [R20]."""
        f = self.ready()
        f.p[physics.I_LN_C][0] = f.p[0][physics.I_LN_C] = 0.05  # correlated with the air
        before = f.z[physics.I_LN_C]
        r = feed(f, [reading_sample(30.0)] * 5)  # 6 C from the forecast
        self.assertTrue(r.corrected)
        nu = r.innovation
        p_tt = f.p[0][0]  # after; the gain used was P_cT / max(S, nu^2 / 10.83)
        moved = abs(f.z[physics.I_LN_C] - before)
        self.assertLess(moved, 0.05 * abs(nu) / (nu * nu / 10.83) + 1e-9)
        self.assertLess(f.z[0], 24.0 + 0.5 * 6.0)  # the air is not dragged to the reading
        self.assertGreater(p_tt, 0.0)

    def test_sun_figures_learn_only_with_sun_on_the_glass(self) -> None:
        """The sun's figures are corrected only on a step with sun on the
        glass; in the living room's history, where it was never measured,
        the mass's sun gain walked from 0.1 to 87 per hour [D026][R16]."""
        f = self.ready()
        f.p[physics.I_LN_M][0] = f.p[0][physics.I_LN_M] = 0.2
        f.p[physics.I_LN_S][0] = f.p[0][physics.I_LN_S] = 0.2
        s0, m0 = f.z[physics.I_LN_S], f.z[physics.I_LN_M]
        r = feed(f, [reading_sample(24.2)] * 5)  # no sun
        self.assertTrue(r.corrected)
        self.assertEqual((f.z[physics.I_LN_S], f.z[physics.I_LN_M]), (s0, m0))
        self.assertNotEqual(f.z[0], 24.0)

    def test_unit_figures_learn_only_when_the_unit_worked(self) -> None:
        f = self.ready()  # the unit rested for the last 40 minutes
        for i in (physics.I_COOL, physics.I_HEAT, physics.I_LN_LAG, physics.I_DELAY):
            f.p[i][0] = f.p[0][i] = 0.1
        before = [f.z[i] for i in (physics.I_COOL, physics.I_HEAT, physics.I_LN_LAG, physics.I_DELAY)]
        self.assertTrue(feed(f, [reading_sample(24.2)] * 5).corrected)
        self.assertEqual([f.z[i] for i in (physics.I_COOL, physics.I_HEAT, physics.I_LN_LAG, physics.I_DELAY)],
                         before)
        acts = filt.acting([reading_sample()] * 5, [(1.0, 1)] + [(0.0, 0)] * 39)
        self.assertTrue(acts[physics.I_COOL] and acts[physics.I_LN_LAG] and acts[physics.I_DELAY])
        self.assertFalse(acts[physics.I_HEAT])

    def test_a_correction_that_leaves_figures_out_keeps_p_as_joseph_gives_it(self) -> None:
        """With a gain that leaves figures out, the uncertainty is the Joseph
        form's, (I - K H) P (I - K H)^T + K R K^T: P - K H P holds only for
        the full gain, and would leave P wrong for the figures left out."""
        f = self.ready()
        f.observe_reading(24.2)
        f.noise, f.innovation_avg = 0.01, 0.01
        for i in range(filt.STATE_SIZE):
            f.p[i][0] = f.p[0][i] = 0.01 * (1 + i % 3)
        f.p[0][0] = 0.05
        p0 = [r[:] for r in f.p]
        acts = filt.acting([reading_sample()] * 5, [(0.0, 0)] * physics.HISTORY_MIN)  # no sun, no unit
        r = f.sensor_step ** 2 / 12.0  # the first day's R
        nu = f._correct(f.z[0] + 0.1, acts)
        n = filt.STATE_SIZE
        s = max(p0[0][0] + r, nu * nu / 10.83)
        k = [p0[i][0] / s if acts[i] else 0.0 for i in range(n)]
        ikh = [[(1.0 if i == j else 0.0) - (k[i] if j == 0 else 0.0) for j in range(n)] for i in range(n)]
        a = [[sum(ikh[i][m] * p0[m][j] for m in range(n)) for j in range(n)] for i in range(n)]
        want = [[sum(a[i][m] * ikh[j][m] for m in range(n)) + k[i] * k[j] * (s - p0[0][0]) for j in range(n)]
                for i in range(n)]
        for i in range(n):
            for j in range(n):
                self.assertAlmostEqual(f.p[i][j], want[i][j], places=12)

    def test_an_unknown_compressor_corrects_the_room_but_teaches_no_figure(self) -> None:
        """A step whose compressor state is unknown still says where the
        room is: the air and mass are corrected and no figure moves [D074].
        In the living room's history the air ran open-loop through a whole
        night of cooling with its compressor unknown."""
        f = self.ready()
        before = list(f.z[2:])
        r = feed(f, [reading_sample(25.0, known=False)] * 5)
        self.assertFalse(r.corrected)
        self.assertIsNotNone(r.innovation)
        self.assertGreater(f.z[0], 24.0)
        self.assertEqual(f.z[2:], before)

    def test_an_unknown_minute_in_the_last_40_blocks_learning(self) -> None:
        """The unit's rate now comes through its delay and lag from the last
        40 minutes; a minute among them whose compressor was unknown would
        be learned as running the way its mode asks."""
        f = self.ready()
        f._history_known[5] = False
        r = feed(f, [reading_sample(24.2)] * 5)
        self.assertFalse(r.corrected)
        self.assertIn("last 40 minutes", r.reason)

    def test_the_rooms_learned_change_widens_the_air_each_step(self) -> None:
        f = self.ready()
        p0 = f.p[0][0]
        feed(f, [reading_sample(None)] * 5)  # no reading: predict only
        quiet = f.p[0][0] - p0
        g = self.ready()
        g.air_noise = 0.5
        feed(g, [reading_sample(None)] * 5)
        self.assertAlmostEqual(g.p[0][0] - p0 - quiet, 0.5, places=6)

    def test_unknown_inputs_widen_the_air_and_mass(self) -> None:
        z = filt.start_state(24.0)
        sunless = filt.unknown_input_variance(z, [reading_sample()] * 5)
        sunny = filt.unknown_input_variance(z, [reading_sample(sun_est=True)] * 5)
        self.assertEqual(sunless, (0.0, 0.0))
        self.assertAlmostEqual(sunny[0], (0.4 * 5 / 60) ** 2 / 3)
        self.assertAlmostEqual(sunny[1], (0.1 * 5 / 60) ** 2 / 3)

    def test_a_lag_at_the_top_of_its_range_can_come_down(self) -> None:
        """At the top of the range a step up is clamped away; the Jacobian
        steps down there, so the filter still sees the lag's effect and can
        move it back (a lag held at 30 against a true 15 for six days)."""
        z = filt.start_state(24.0)
        z[physics.I_LN_LAG] = math.log(30.0)
        si = physics.StepInputs(30.0, 0.0, (12.0,) * 5, ((2.0, 1),) * 40, 1.0, EFF)
        base = physics.step(z, si)
        f = filt.jacobian(z, si, base)
        self.assertNotEqual(f[0][physics.I_LN_LAG], 0.0)

    def test_figures_are_held_within_the_range_the_arithmetic_carries(self) -> None:
        f = self.ready()
        f.z[physics.I_LN_C] = 20.0  # e^20 per hour: the exponential would overflow
        f._hold_unit_in_range()
        self.assertEqual(f.z[physics.I_LN_C], math.log(1e3))
        data = self.ready().as_dict()
        data["z"][physics.I_LN_A] = -40.0
        self.assertIsNone(filt.HeatFilter.from_dict(data))

    def test_a_step_whose_arithmetic_fails_is_undone(self) -> None:
        f = self.ready()
        f.z[physics.I_LN_C] = 20.0
        z0 = list(f.z)
        r = feed(f, [reading_sample(24.0)] * 5)
        self.assertIn("arithmetic failed", r.reason)
        self.assertEqual(f.z, z0)

    def test_lag_kept_in_range_by_a_correction(self) -> None:
        f = self.ready()
        f._history.extend([(2.0, 1)] * physics.HISTORY_MIN)
        f.z[physics.I_LN_LAG] = math.log(29.9)
        f.p[physics.I_LN_LAG][0] = f.p[0][physics.I_LN_LAG] = 0.5
        f.p[physics.I_LN_LAG][physics.I_LN_LAG] = 5.0
        feed(f, [reading_sample(24.6, running=True)] * 5)
        self.assertLessEqual(f.z[physics.I_LN_LAG], math.log(30.0) + 1e-12)
        self.assertGreaterEqual(f.z[physics.I_LN_LAG], 0.0)

    def test_a_stored_lag_outside_the_range_is_relearned(self) -> None:
        """The office's and living room's stored lags (23,829 and 2,120,772
        minutes) were learned by a filter that could diverge [R19]."""
        data = self.ready().as_dict()
        data["z"][physics.I_LN_LAG] = math.log(23829.4)
        self.assertIsNone(filt.HeatFilter.from_dict(data))
        data["z"][physics.I_LN_LAG] = math.log(15.0)
        self.assertIsNotNone(filt.HeatFilter.from_dict(data))

    def test_the_reading_at_the_step_is_the_one_used(self) -> None:
        f = self.ready()
        r = feed(f, [reading_sample(None)] + [reading_sample(24.4)] * 4)
        self.assertTrue(r.corrected)

    def test_no_correction_when_untrusted(self) -> None:
        cases = {
            "stood in or missing": [reading_sample(None)] * 5,
            "unknown": [reading_sample(known=False)] * 5,
            "assumed": [reading_sample(sun_est=True)] * 5,
            "fewer than five": [reading_sample()] * 3,
        }
        for want, samples in cases.items():
            f = self.ready()
            r = feed(f, samples)
            self.assertFalse(r.corrected, want)
            self.assertIn(want, r.reason)
            self.assertEqual(f.corrections, 0)
        f = filt.HeatFilter.start(24.0)  # q never seen
        self.assertIn("step is not yet known", feed(f, [reading_sample()] * 5).reason)

    def test_prediction_runs_without_a_correction(self) -> None:
        f = self.ready()
        p0 = f.p[physics.I_LN_A][physics.I_LN_A]
        feed(f, [reading_sample(None)] * 5)
        # a figure's row of F is the identity, so its variance grows by Q
        self.assertAlmostEqual(f.p[physics.I_LN_A][physics.I_LN_A], p0 + 1e-6, places=9)

    def test_no_outdoor_yet(self) -> None:
        f = self.ready()
        r = feed(f, [reading_sample(outdoor=None)] * 5)
        self.assertIn("no outdoor", r.reason)
        f.last_outdoor = 20.0
        r = feed(f, [reading_sample(outdoor=None)] * 5)
        self.assertIn("outdoor temperature was missing", r.reason)

    def test_the_readings_noise_is_what_its_second_differences_show(self) -> None:
        """R is learned from the reading's second differences, step to step:
        white noise of variance R gives a second difference of variance 6 R
        [R21]."""
        f = self.ready()
        for y in (24.0, 24.4, 24.0):
            feed(f, [reading_sample(y)] * 5)
        floor = 0.04 / 12
        d2 = (24.0 - 2 * 24.4 + 24.0) ** 2 / 6.0
        self.assertAlmostEqual(f.reading_jitter, floor + (d2 - floor) / 288, places=12)
        self.assertAlmostEqual(f.noise, max(floor, f.reading_jitter), places=12)

    def test_a_room_that_moves_is_followed_not_taken_as_a_noisy_sensor(self) -> None:
        """A room warming steadily, which the model did not forecast: the
        miss is the room's own change, added to the air's noise, and the
        estimate follows; the sensor's noise stays at its rounding. Before
        this, R took the whole miss (2.17 C^2 on a sensor in 0.1 C steps)
        and the estimate stayed 1 C or more from the room for 12 hours
        [R21][Sim]."""
        f = self.ready()
        f.corrections = 288
        for _ in range(12):  # an hour as forecast
            feed(f, [reading_sample(round(f.z[0], 1))] * 5)
        y = round(f.z[0], 1) + 3.0  # the room warms 3 C and stays (the sun, a door)
        for _ in range(24):  # two hours
            feed(f, [reading_sample(y)] * 5)
        self.assertLess(f.noise, 0.05)  # the sensor is not taken as noisy
        self.assertGreater(f.air_noise, 0.0)  # the room's own change is
        self.assertLess(abs(f.z[0] - y), 0.5)  # and the estimate has followed

    def test_non_finite_step_undone(self) -> None:
        f = self.ready()
        z0 = list(f.z)
        r = feed(f, [reading_sample(outdoor=1e308)] * 5)
        self.assertIn("not a finite number", r.reason)
        self.assertEqual(f.z, z0)

    def test_round_trip(self) -> None:
        f = self.ready()
        feed(f, [reading_sample(24.4)] * 5)
        g = filt.HeatFilter.from_dict(f.as_dict())
        self.assertEqual((g.z, g.p, g.sensor_step, g.noise, g.corrections), (f.z, f.p, f.sensor_step, f.noise, 1))
        self.assertEqual((g.air_noise, g.reading_jitter, g._prior_avg), (f.air_noise, f.reading_jitter, f._prior_avg))
        bad = f.as_dict()
        bad["z"][3] = float("nan")
        self.assertIsNone(filt.HeatFilter.from_dict(bad))
        self.assertIsNone(filt.HeatFilter.from_dict({"z": [1.0] * 24, "p": []}))


class MinuteSampleTests(unittest.TestCase):
    def snap(self, *heads: Any, t: float | None = 24.0, tag: Any = VT.MEASURED) -> Any:
        return dataclasses.replace(SimRoom().snapshot(), heads=heads, room_temperature=models.Reading(t, tag))

    def head(self, mode: str, comp: Any, setpoint: float = 22.0, own: float = 24.0) -> Any:
        return models.HeadSnapshot("climate.x", "u", True, mode, setpoint, None, own, None, None, None, None,
                                   models.Reading(None, VT.MISSING), comp, models.CompressorSource.MODE)

    def test_cooling_running(self) -> None:
        s = filt.minute_sample(self.snap(self.head("cool", CS.RUNNING)), 12.0)
        self.assertEqual((s.effort, s.direction, s.compressor_known, s.compressor_running), (2.0, 1, True, True))

    def test_effort_is_never_the_frequency(self) -> None:
        # a frequency sensor answers whether the compressor runs (4.8); the
        # effort stays the unit's reading against its setpoint, in C
        h = dataclasses.replace(self.head("cool", CS.RUNNING), frequency=models.Reading(48.0, VT.MEASURED))
        self.assertEqual(unit.head_effort(h), 2.0)
        self.assertEqual(filt.minute_sample(self.snap(h), 12.0).effort, 2.0)

    def test_known_stopped_is_resting(self) -> None:
        s = filt.minute_sample(self.snap(self.head("cool", CS.STOPPED)), 12.0)
        self.assertEqual((s.direction, s.mode_direction), (0, 1))

    def test_heating_effort(self) -> None:
        s = filt.minute_sample(self.snap(self.head("heat", CS.RUNNING, setpoint=23.0, own=21.5)), 12.0)
        self.assertEqual((s.effort, s.direction), (1.5, -1))

    def test_unknown_keeps_the_mode_direction(self) -> None:
        s = filt.minute_sample(self.snap(self.head("cool", CS.UNKNOWN)), 12.0)
        self.assertEqual((s.direction, s.compressor_known), (1, False))

    def test_known_only_when_every_head_is(self) -> None:
        s = filt.minute_sample(self.snap(self.head("cool", CS.RUNNING), self.head("cool", CS.UNKNOWN)), 12.0)
        self.assertFalse(s.compressor_known)
        self.assertTrue(s.compressor_running)

    def test_stood_in_reading_is_not_learned_from(self) -> None:
        self.assertIsNone(filt.minute_sample(self.snap(self.head("cool", CS.STOPPED), t=23.0, tag=VT.STOOD_IN), 12.0).reading)


def run_sim(days: int, pinned: bool, seed: int = 3) -> tuple[Any, list[float]]:
    """Run the Section 11.1 room through the model, scoring one-hour
    forecasts over resting hours after the first day. With pinned the
    unit's delay and lag are held at the true 6 and 15 minutes, as in
    Section 11.2; otherwise the filter learns them."""
    sim = SimRoom(seed=seed)
    rm = model.RoomModel()
    errors: list[float] = []
    pending: list[tuple[int, float, bool]] = []
    with mock.patch.object(filt, "FILTER_NOISE_LN_LAG", 0.0 if pinned else filt.FILTER_NOISE_LN_LAG), \
            mock.patch.object(filt, "FILTER_NOISE_DELAY", 0.0 if pinned else filt.FILTER_NOISE_DELAY):
        for _ in range(days * 1440):
            sim.advance()
            rm.minute(sim.snapshot(), sim.hour)
            if pinned and rm.heat is not None and rm.heat.corrections == 0:
                for i in (physics.I_LN_LAG, physics.I_DELAY):
                    for j in range(physics.STATE_SIZE):
                        rm.heat.p[i][j] = rm.heat.p[j][i] = 0.0
            if sim.minute % 5 == 0:
                rm.five_minutes(sim.now)
            if sim.minute % 60 != 0 or rm.heat is None:
                continue
            # a resting hour: the unit at rest when the forecast was made and an hour later
            for made, value, ran in pending:
                if made == sim.minute - 60 and made >= 1440 and not ran and not sim.running and sim.u < 0.01:
                    errors.append(abs(value - sim.t))
            ins = []
            for k in range(12):
                h = ((sim.minute + 5 * (k + 1)) % 1440) / 60
                out = sim._mean + sim._swing / 2 * math.sin(2 * math.pi * (h - 9) / 24)
                sun = sim._cloud if 7 <= h < 15 else 0.0
                ins.append(fc.ForecastInput(out, 60.0, sun, h, models.StepKind.REST, 0.0))
            ran = sim.running or sim.u > 0.01
            pending.append((sim.minute, fc.forecast(rm.heat, rm.humidity, ins, e_now=20.0)[-1].t, ran))
    return rm, errors


class Learning(unittest.TestCase):
    def test_learns_the_simulated_room_given_the_delay_and_lag(self) -> None:
        rm, errors = run_sim(3, pinned=True)
        fig = physics.figures(rm.heat.z)
        self.assertEqual((fig.delay_min, round(fig.lag_min, 9)), (6.0, 15.0))
        self.assertGreater(len(errors), 30)
        self.assertLess(sum(errors) / len(errors), 0.12)  # 0.10 measured
        self.assertAlmostEqual(physics.curve_rate(EFF, fig.cool_steps, 2.0), 3.0, delta=0.45)
        self.assertGreater(math.sqrt(rm.heat.noise), 0.05)
        self.assertLess(math.sqrt(rm.heat.noise), 0.09)
        self.assertGreater(rm.heat.corrections, 700)

    def test_learns_the_delay_and_lag(self) -> None:
        # true delay 6 and lag 15 minutes; at 0.2 C steps their sum is
        # learned more surely than how it splits between them
        rm, errors = run_sim(8, pinned=False, seed=7)
        fig = physics.figures(rm.heat.z)
        self.assertAlmostEqual(fig.delay_min + fig.lag_min, 21.0, delta=4.0)
        self.assertGreater(fig.lag_min, 5.0)
        self.assertAlmostEqual(physics.curve_rate(EFF, fig.cool_steps, 2.0), 3.0, delta=0.45)
        self.assertLess(sum(errors) / len(errors), 0.12)


class Unit(unittest.TestCase):
    def test_u_min(self) -> None:
        u = unit.UnitModel()
        for _ in range(29):
            u.observe_rate(True, 2.0, 2.0)
        self.assertIsNone(u.u_min)
        u.observe_rate(True, 2.0, 2.0)
        self.assertEqual(u.u_min, 2.0)
        u.observe_rate(False, 0.0, 2.0)
        for _ in range(30):
            u.observe_rate(True, 3.0, 2.0)  # above what was offered: says nothing
        self.assertEqual(u.u_min, 2.0)
        u.observe_rate(False, 0.0, 2.0)
        for _ in range(30):
            u.observe_rate(True, 2.5, 2.5)  # held at the lowest offered, on a curve that has moved up
        self.assertEqual(u.u_min, 2.5)  # replaced, up or down [D100]

    def test_a_run_above_the_lowest_rate_offered_is_not_u_min(self) -> None:
        """A first long run at full rate says nothing about how low the unit
        can go [D100]."""
        u = unit.UnitModel()
        for _ in range(45):
            u.observe_rate(True, 6.0, 1.0)  # pull-down at the top, lowest offered 1.0
        self.assertIsNone(u.u_min)
        u.observe_rate(False, 0.0, 1.0)
        for _ in range(30):
            u.observe_rate(True, 0.9, 1.0)  # held just below what was offered
        self.assertAlmostEqual(u.u_min, 0.9)
        fig = physics.figures(filt.start_state(20.0))
        self.assertEqual(unit.UnitModel().lowest_rate(fig, 1), 1.0)

    def test_minutes_with_no_effort_break_the_run(self) -> None:
        """The room at its setpoint while the compressor runs: no rate is
        asked, so those minutes are no rate held and U_min is never 0 [D100]."""
        u = unit.UnitModel()
        for _ in range(30):
            u.observe_rate(True, 0.0, 1.0)
        self.assertIsNone(u.u_min)
        for _ in range(20):
            u.observe_rate(True, 0.8, 1.0)
        u.observe_rate(True, 0.0, 1.0)  # one minute at the setpoint breaks it
        for _ in range(29):
            u.observe_rate(True, 0.8, 1.0)
        self.assertIsNone(u.u_min)
        u.observe_rate(True, 0.8, 1.0)
        self.assertAlmostEqual(u.u_min, 0.8)
        self.assertIsNone(unit.UnitModel.from_dict({"u_min": 0.0}).u_min)  # a stored 0 is relearned

    def test_heating_runs_are_learned(self) -> None:
        """Heating rates are negative; a heating hold is learned as one [D100]."""
        fig = physics.figures(filt.start_state(20.0))
        u = unit.UnitModel()
        for _ in range(30):
            u.observe_rate(True, unit.rate_for_effort(fig, 0.4, -1), u.lowest_rate(fig, -1))
        self.assertAlmostEqual(u.u_min_heat, abs(unit.rate_for_effort(fig, 0.4, -1)))
        self.assertIsNone(u.u_min)  # heating says nothing of cooling's floor

    def test_heating_and_cooling_each_have_their_own_floor(self) -> None:
        """Each direction has its own curve, so its own lowest rate [D100]."""
        fig = physics.figures(filt.start_state(20.0))
        u = unit.UnitModel(u_min=2.0, u_min_heat=3.0)
        self.assertEqual((u.lowest_rate(fig, 1), u.lowest_rate(fig, -1)), (2.0, 3.0))
        for _ in range(29):
            u.observe_rate(True, 2.0, 2.0)
        u.observe_rate(True, -2.5, 3.0)  # a change of direction starts a new run
        self.assertEqual((u.u_min, u.u_min_heat), (2.0, 3.0))
        v = unit.UnitModel.from_dict(u.as_dict())
        self.assertEqual((v.u_min, v.u_min_heat), (2.0, 3.0))
        self.assertIsNone(unit.UnitModel.from_dict({"u_min": 1.0}).u_min_heat)  # an older file
        self.assertIsNone(unit.UnitModel.from_dict({"u_min": True}).u_min)  # not a number

    def test_an_early_low_u_min_does_not_hold_the_plan_below_the_curve(self) -> None:
        """A U_min learned on an earlier, lower curve: never offered below
        the curve's first point, and replaced by a hold there [D100]."""
        fig = physics.figures(filt.start_state(20.0))
        first = unit.rate_for_effort(fig, unit.curve_efforts()[0], 1)
        u = unit.UnitModel(u_min=0.05)
        self.assertEqual(u.lowest_rate(fig, 1), first)
        for _ in range(30):
            u.observe_rate(True, unit.rate_for_effort(fig, 0.4, 1), u.lowest_rate(fig, 1))
        self.assertAlmostEqual(u.u_min, first)

    def test_round_trip(self) -> None:
        u = unit.UnitModel(u_min=0.8)
        v = unit.UnitModel.from_dict(u.as_dict())
        self.assertEqual(v.u_min, 0.8)
        self.assertIsNone(unit.UnitModel.from_dict({"u_min": float("inf")}).u_min)


def hsnap(climate: str, ou: str, mode: str, comp: Any, setpoint: float = 22.0) -> Any:
    return models.HeadSnapshot(climate, ou, True, mode, setpoint, None, 24.0, None, None, None, None,
                               models.Reading(None, VT.MISSING), comp, models.CompressorSource.MODE)


def noisy_history(level: float, until: Any = None) -> Any:
    """A house load that has moved by about 0.03 kW reading to reading for
    the half-hour before, so its noise is learned (3 sigma about 0.1 kW)."""
    hist = observe.LoadHistory()
    end = until if until is not None else NOW - timedelta(minutes=4)
    wobble = (0.03, -0.02, 0.01, -0.03, 0.02, -0.01)
    for k in range(40):
        hist.add(end - timedelta(seconds=30 * (40 - k)), level + wobble[k % len(wobble)])
    return hist


class Draw(unittest.TestCase):
    def run_event(self, before: list[Any], after: list[Any], load_before: float, load_after: float,
                  commands: dict[str, datetime] | None = None, rise_after_s: float = 30.0) -> Any:
        """The head changes at NOW; the load stands at load_before for the
        3 minutes before and moves to load_after rise_after_s later."""
        d = unit.DrawLearner()
        hist = noisy_history(load_before)
        t = NOW - timedelta(minutes=4)
        while t <= NOW + timedelta(minutes=6):
            hist.add(t, load_before if t < NOW + timedelta(seconds=rise_after_s) else load_after)
            if t == NOW - timedelta(minutes=1):
                d.observe(t, before, {}, hist)
            elif t == NOW:
                d.observe(t, after, commands or {}, hist)
            elif t > NOW and (t - NOW).total_seconds() % 60 == 0:
                d.observe(t, after, {}, hist)
            t += timedelta(seconds=30)
        return d

    def test_start(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.STOPPED)], [hsnap("c1", "ou1", "cool", CS.RUNNING)],
                           0.5, 1.7)
        self.assertAlmostEqual(d.draw_at("ou1", 2.0), 1.2)

    def test_start_from_the_mode_alone(self) -> None:
        """A unit that never reports its compressor: put in cool from off,
        and the load rises 3 minutes later, as the office's did on 7
        November 2026. The released learner learned no start at all."""
        d = self.run_event([hsnap("c1", "ou1", "off", CS.STOPPED)], [hsnap("c1", "ou1", "cool", CS.UNKNOWN)],
                           0.6, 1.1, rise_after_s=190.0)
        self.assertAlmostEqual(d.draw_at("ou1", 2.0), 0.5)

    def test_start_with_no_rise_is_not_learned(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "off", CS.STOPPED)], [hsnap("c1", "ou1", "cool", CS.UNKNOWN)],
                           0.6, 0.65)
        self.assertIsNone(d.draw_at("ou1", 2.0))

    def test_stop_into_fan_only(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.UNKNOWN)], [hsnap("c1", "ou1", "fan_only", CS.STOPPED)],
                           1.9, 0.6)
        self.assertAlmostEqual(d.draw_at("ou1", 2.0), 1.3)

    def test_stop_at_a_whole_degree_setpoint_is_learned(self) -> None:
        """The unit reads its setpoint in whole degrees yet is drawing: the
        office at 18:05 on 7 November 2026 read 24 at a 24 setpoint and the
        load fell 0.23 kW. The released learner refused it."""
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.UNKNOWN, setpoint=24.0)],
                           [hsnap("c1", "ou1", "off", CS.STOPPED, setpoint=24.0)], 0.85, 0.62)
        self.assertAlmostEqual(d.draw_at("ou1", 0.5), 0.23)

    def test_stop_with_no_fall_is_not_learned(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.UNKNOWN, setpoint=24.0)],
                           [hsnap("c1", "ou1", "fan_only", CS.STOPPED)], 0.70, 0.66)
        self.assertIsNone(d.draw_at("ou1", 2.0))

    def test_another_unit_changing_spoils_it(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.STOPPED)], [hsnap("c1", "ou1", "cool", CS.RUNNING)],
                           0.5, 1.7, commands={"c2": NOW + timedelta(seconds=20)})
        self.assertIsNone(d.draw_at("ou1", 2.0))

    def test_negative_step_dropped(self) -> None:
        d = self.run_event([hsnap("c1", "ou1", "cool", CS.STOPPED)], [hsnap("c1", "ou1", "cool", CS.RUNNING)],
                           1.7, 0.5)
        self.assertIsNone(d.draw_at("ou1", 2.0))

    def test_interpolation_and_defaults(self) -> None:
        d = unit.DrawLearner(table={"ou1": {1: (0.6, 1), 3: (1.0, 2)}})
        self.assertEqual(d.draw_at("ou1", 0.2), 0.6)
        self.assertAlmostEqual(d.draw_at("ou1", 1.5), 0.8)
        self.assertEqual(d.draw_at("ou1", 3.5), 1.0)
        self.assertIsNone(d.draw_at("ou2", 1.0))
        self.assertEqual(unit.DrawLearner(has_load_sensor=False).draw_at("ou1", 1.0), 1.2)
        e = unit.DrawLearner.from_dict(d.as_dict(), True)
        self.assertEqual(e.table, d.table)

    def test_nothing_is_learned_before_the_houses_noise_is(self) -> None:
        d = unit.DrawLearner()
        hist = observe.LoadHistory()
        t = NOW - timedelta(minutes=4)
        while t <= NOW + timedelta(minutes=6):
            hist.add(t, 0.5 if t < NOW else 1.7)
            if t >= NOW - timedelta(minutes=1) and (t - NOW).total_seconds() % 60 == 0:
                state = CS.STOPPED if t < NOW else CS.RUNNING
                d.observe(t, [hsnap("c1", "ou1", "cool", state)], {}, hist)
            t += timedelta(seconds=30)
        self.assertIsNone(d.draw_at("ou1", 2.0))
        self.assertIsNone(hist.step_kw())

    def test_the_step_is_three_times_the_learned_noise(self) -> None:
        hist = noisy_history(0.6)
        self.assertIsNotNone(hist.step_kw())
        self.assertGreater(hist.step_kw(), 0.03)
        self.assertLess(hist.step_kw(), 0.2)

    def test_running_average(self) -> None:
        d = unit.DrawLearner()
        d._add("ou1", 2.1, 1.0)
        d._add("ou1", 1.9, 2.0)
        self.assertEqual(d.table["ou1"][3], (1.5, 2))


class Humidity(unittest.TestCase):
    def minute(self, **kw: Any) -> Any:
        base = {"outdoor_e": 20.0, "dry": False, "cooling_rate": 0.0, "rebound": False}
        base.update(kw)
        return hum.MoistureMinute(**base)

    def test_rates(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        fig = hum.moisture_figures(f.z)
        self.assertAlmostEqual(hum.rate_of_change(fig, 20.0, self.minute()), 0.0)
        self.assertAlmostEqual(hum.rate_of_change(fig, 20.0, self.minute(dry=True)), -fig.drying)
        self.assertAlmostEqual(hum.rate_of_change(fig, 20.0, self.minute(cooling_rate=2.0)), -2.0 * fig.cooling)
        self.assertAlmostEqual(hum.rate_of_change(fig, 20.0, self.minute(rebound=True)), fig.rebound)
        self.assertAlmostEqual(hum.rate_of_change(fig, 18.0, self.minute()), fig.h * 2.0)

    def test_acting(self) -> None:
        self.assertEqual(hum.acting([self.minute()]), [True, True, True, False, False, False])
        self.assertEqual(hum.acting([self.minute(dry=True), self.minute(rebound=True)]),
                         [True, True, True, True, False, True])

    def ready(self) -> Any:
        f = hum.HumidityFilter.start(20.0)
        f.observe_e(20.0)
        f.observe_e(20.1)
        return f

    def test_a_surprise_moves_the_humidity_no_more_than_a_gated_one(self) -> None:
        """As in the heat filter, a reading far outside what the filter
        expected moves it as a 3.3-sigma surprise at most [R20]."""
        f = self.ready()
        self.assertIsNone(f.five_minutes([self.minute()] * 5, 30.0, True))  # 10 hPa from the forecast
        self.assertLess(f.z[hum.H_E], 25.0)
        self.assertGreater(f.z[hum.H_E], 20.0)

    def test_figure_moves_only_where_it_acts(self) -> None:
        f = self.ready()
        f.p[hum.H_LN_DRY][hum.H_E] = f.p[hum.H_E][hum.H_LN_DRY] = 0.3  # correlated
        before = f.z[hum.H_LN_DRY]
        self.assertIsNone(f.five_minutes([self.minute()] * 5, 21.0, True))
        self.assertEqual(f.z[hum.H_LN_DRY], before)
        self.assertNotEqual(f.z[hum.H_E], 20.0)
        f.p[hum.H_LN_DRY][hum.H_E] = f.p[hum.H_E][hum.H_LN_DRY] = 0.3
        f.five_minutes([self.minute(dry=True)] * 5, 19.0, True)
        self.assertNotEqual(f.z[hum.H_LN_DRY], before)

    def test_untrusted_corrects_nothing(self) -> None:
        f = self.ready()
        self.assertIn("stood in", f.five_minutes([self.minute()] * 5, 25.0, False))
        self.assertEqual(f.corrections, 0)

    def test_round_trip_and_rebound(self) -> None:
        f = self.ready()
        f.five_minutes([self.minute()] * 5, 20.5, True)
        g = hum.HumidityFilter.from_dict(f.as_dict())
        self.assertEqual((g.z, g.p, g.corrections), (f.z, f.p, 1))
        self.assertTrue(hum.in_rebound(NOW, NOW + timedelta(minutes=14)))
        self.assertFalse(hum.in_rebound(NOW, NOW + timedelta(minutes=15)))
        self.assertFalse(hum.in_rebound(None, NOW))


class HumidityEdges(unittest.TestCase):
    def test_no_minutes_no_step(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        self.assertEqual(f.five_minutes([], 20.0, True), "no minutes were seen")

    def test_a_step_that_is_not_finite_is_undone(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        f.observe_e(20.0)
        f.observe_e(20.1)
        z, p = list(f.z), [r[:] for r in f.p]
        m = hum.MoistureMinute(outdoor_e=float("inf"), dry=False, cooling_rate=0.0, rebound=False)
        self.assertEqual(f.five_minutes([m] * 5, 20.0, True), "a figure was not a finite number; step undone")
        self.assertEqual((f.z, f.p), (z, p))

    def test_a_stored_filter_that_cannot_be_read_is_relearned(self) -> None:
        good = hum.HumidityFilter.start(20.0).as_dict()
        self.assertIsNone(hum.HumidityFilter.from_dict({}))
        self.assertIsNone(hum.HumidityFilter.from_dict({**good, "z": good["z"][:-1]}))
        self.assertIsNone(hum.HumidityFilter.from_dict({**good, "z": [float("nan")] * len(good["z"])}))


class RoomModelSteps(unittest.TestCase):
    def test_nothing_to_step_before_the_first_reading(self) -> None:
        m = model.RoomModel()
        self.assertIsNone(m.five_minutes(NOW))
        m.hour(NOW, None, [], 20.0)  # no filter yet: nothing to check
        self.assertEqual(m.trust.one_hour_errors, type(m.trust.one_hour_errors)())

    def test_the_six_hour_check_replays_the_hours_since(self) -> None:
        sim = SimRoom(seed=3, external=True)
        m = model.RoomModel()
        rest = [fc.ForecastInput(24.0, 50.0, 0.0, 12.0, models.StepKind.REST, 0.0)] * 12
        for i in range(8 * 60):
            sim.advance()
            m.minute(sim.snapshot(), sim.hour)
            if i % 5 == 4:
                m.five_minutes(sim.now)
            if i % 60 == 59:
                m.hour(sim.now, sim.t, rest, 20.0)
        self.assertTrue(m.trust.six_hour_errors)  # [Prop]


class TrustTests(unittest.TestCase):
    def test_one_hour_trust(self) -> None:
        t = trust_mod.Trust()
        for i in range(25):
            t.hourly(NOW + timedelta(hours=i), 24.0, 24.2, True, None)
        self.assertEqual(len(t.one_hour_errors), 24)
        self.assertTrue(t.trusted)
        t2 = trust_mod.Trust()
        for i in range(25):
            t2.hourly(NOW + timedelta(hours=i), 24.0, 24.31, True, None)
        self.assertFalse(t2.trusted)

    def test_needs_24_and_resting_hours(self) -> None:
        t = trust_mod.Trust()
        for i in range(30):
            t.hourly(NOW + timedelta(hours=i), 24.0, 24.0, i % 2 == 0, None)
        self.assertEqual(len(t.one_hour_errors), 14)  # the first hour has no forecast to score
        self.assertFalse(t.trusted)

    def test_keeps_48(self) -> None:
        t = trust_mod.Trust()
        for i in range(60):
            t.hourly(NOW + timedelta(hours=i), 24.0, 24.0, True, None)
        self.assertEqual(len(t.one_hour_errors), 48)

    def test_six_hour_over_14_days(self) -> None:
        t = trust_mod.Trust()
        for i in range(24):
            t.hourly(NOW + timedelta(hours=i), None, None, False, 0.2)
        self.assertTrue(t.six_hour_trusted)
        t.hourly(NOW + timedelta(days=15), None, None, False, 0.5)
        self.assertEqual(len(t.six_hour_errors), 1)
        self.assertFalse(t.six_hour_trusted)
        t.end_of_day("2026-01-16")
        self.assertEqual(t.six_hour_trusted_days(), 0)

    def test_weekly_and_round_trip(self) -> None:
        t = trust_mod.Trust()
        for i in range(24 * 9):
            t.hourly(NOW + timedelta(hours=i), 24.0, 24.1, True, 0.1)
        self.assertEqual(len(t.weekly_errors), 1)
        self.assertAlmostEqual(t.weekly_errors[0][1], 0.1, places=9)
        u = trust_mod.Trust.from_dict(t.as_dict())
        self.assertEqual((list(u.one_hour_errors), list(u.six_hour_errors), u.weekly_errors),
                         (list(t.one_hour_errors), list(t.six_hour_errors), t.weekly_errors))


class Forecasts(unittest.TestCase):
    def rest(self, n: int, outdoor: float = 30.0, kind: Any = None, rate: float = 0.0) -> list[Any]:
        return [fc.ForecastInput(outdoor, 50.0, 0.0, 12.0, kind or models.StepKind.REST, rate)
                for _ in range(n)]

    def test_drift(self) -> None:
        f = filt.HeatFilter.start(24.0)
        f.z[physics.I_M] = 26.0
        fig = physics.figures(f.z)
        self.assertAlmostEqual(fc.drift_now(f, 30.0, 0.5, 12.0), fig.a * 6 + fig.b * 2 + fig.s * 0.5 + fig.g(12.0))

    def test_uncertainty_carried_exactly(self) -> None:
        f = filt.HeatFilter.start(24.0)
        f.noise = 0.003
        ex = physics.exact_step(physics.figures(f.z))
        p2 = [[f.p[0][0], f.p[0][1]], [f.p[1][0], f.p[1][1]]]
        noise = la.diagonal([1e-3 * 5 / 60, 1e-4 * 5 / 60])
        pts = fc.forecast(f, None, self.rest(3), e_now=15.0)
        for p in pts:
            p2 = la.add(la.sandwich(ex.phi, p2), noise)
            self.assertAlmostEqual(p.sigma_t, math.sqrt(p2[0][0] + 0.003), places=12)

    def test_rebound_after_wet_steps_in_the_forecast(self) -> None:
        f = filt.HeatFilter.start(24.0)
        h = hum.HumidityFilter.start(20.0)
        wet = fc.forecast(f, h, self.rest(2, kind=models.StepKind.COOL, rate=0.0) + self.rest(3), e_now=0.0)
        dry = fc.forecast(f, h, self.rest(5), e_now=0.0)
        self.assertEqual(wet[1].e, dry[1].e)
        self.assertGreater(wet[2].e, dry[2].e)

    def test_forecast_and_uncertainty(self) -> None:
        f = filt.HeatFilter.start(24.0)
        f.noise = 0.003
        pts = fc.forecast(f, None, self.rest(72), e_now=15.0)
        self.assertEqual(len(pts), 72)
        self.assertGreater(pts[-1].t, pts[0].t)
        self.assertGreater(pts[-1].sigma_t, pts[0].sigma_t)
        self.assertGreaterEqual(pts[0].sigma_t, math.sqrt(0.003))
        p = pts[10]
        self.assertAlmostEqual(p.hci, hci.comfort_index(p.t, p.rh), places=12)
        self.assertAlmostEqual(p.sigma_hci, p.sigma_t, places=12)  # e held: the index moves with T one for one
        self.assertAlmostEqual(p.e, 15.0)

    def test_forecast_starts_from_the_units_rate_now(self) -> None:
        """The unit reaches a new rate through its delay and lag, taken as
        one lag of their sum: a forecast from a resting unit asked for 3 C/h
        cools less in its first 5 minutes than one already at 3 C/h."""
        f = filt.HeatFilter.start(24.0)
        cold = fc.forecast(f, None, self.rest(1, kind=models.StepKind.COOL, rate=3.0), e_now=15.0)[0]
        f.u = 3.0
        warm = fc.forecast(f, None, self.rest(1, kind=models.StepKind.COOL, rate=3.0), e_now=15.0)[0]
        self.assertLess(warm.t, cold.t)
        fig = physics.figures(f.z)
        tau = (fig.lag_min + fig.delay_min) / 60.0
        mean = 3.0 * (1.0 - tau / (5 / 60) * (1.0 - math.exp(-(5 / 60) / tau)))
        ex = physics.exact_step(fig)
        want = ex.advance((24.0, 24.0), 30.0, 0.0, fig.g(12.0), mean)
        self.assertAlmostEqual(cold.t, want[0], places=12)

    def test_forecast_matches_exact_step(self) -> None:
        f = filt.HeatFilter.start(24.0)
        f.u = 1.0  # already at the step's rate
        ex = physics.exact_step(physics.figures(f.z))
        want = ex.advance((24.0, 24.0), 30.0, 0.0, physics.figures(f.z).g(12.0), 1.0)
        got = fc.forecast(f, None, self.rest(1, kind=models.StepKind.COOL, rate=1.0), e_now=15.0)[0]
        self.assertAlmostEqual(got.t, want[0], places=12)

    def test_humidity_in_forecast(self) -> None:
        f = filt.HeatFilter.start(24.0)
        h = hum.HumidityFilter.start(20.0)
        e_out = hci.vapour_pressure(30.0, 50.0)
        rest = fc.forecast(f, h, self.rest(6), e_now=0.0)
        dry = fc.forecast(f, h, self.rest(6, kind=models.StepKind.DRY), e_now=0.0)
        self.assertLess(dry[-1].e, rest[-1].e)
        self.assertLess(abs(rest[-1].e - e_out), abs(20.0 - e_out))
        rebound = fc.forecast(f, h, self.rest(2, kind=models.StepKind.COOL, rate=2.0) + self.rest(3), e_now=0.0)
        plain = fc.forecast(f, h, self.rest(2, kind=models.StepKind.COOL, rate=2.0) + self.rest(3), e_now=0.0,
                            rebound_steps=0)
        self.assertEqual(rebound[-1].e, plain[-1].e)
        fig = hum.moisture_figures(h.z)
        after = fc.forecast(f, h, self.rest(1), e_now=0.0, rebound_steps=1)
        before = fc.forecast(f, h, self.rest(1), e_now=0.0)
        self.assertAlmostEqual(after[0].e - before[0].e,
                               fig.rebound / fig.h * (1 - math.exp(-fig.h * physics.STEP_H)), places=9)

    def test_replay_and_setpoint(self) -> None:
        f = filt.HeatFilter.start(24.0)
        steps = [inputs(outdoor=30.0)] * 3
        z = f.z
        for s in steps:
            z = physics.step(z, s).z
        self.assertEqual(fc.replay(f.z, steps), z[physics.I_T])
        self.assertAlmostEqual(fc.setpoint_for_rate(f, 24.0, 4.0), 22.0)  # ln 1 curve: 4 C/h at effort 2
        self.assertAlmostEqual(fc.setpoint_for_rate(f, 20.0, -2.0), 21.0)


class DrawWithASharedUnit(unittest.TestCase):
    def test_a_head_joining_a_running_outdoor_unit_is_not_its_start(self) -> None:
        """B runs the outdoor unit; A joins it, adding a little load. With one
        frequency sensor for both, A goes from stopped to running, but the
        unit never started, so nothing is learned of its draw [D028]."""
        observe = load("observe")
        models_ = load("models")
        obs = observe.RoomObserver.__new__(observe.RoomObserver)
        obs._load_steps = {}
        learner = unit.DrawLearner(has_load_sensor=True)
        hist = observe.LoadHistory()
        t0 = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
        units = {"climate.a": "outdoor1", "climate.b": "outdoor1"}
        for i in range(6):
            now = t0 + timedelta(minutes=i)
            hist.add(now, 1.5 if i < 2 else 1.65)

            def st(eid: str, state: str, attrs: dict[str, Any], now: datetime = now) -> Any:
                return models_.RawState(eid, state, attrs, now - timedelta(seconds=5), now - timedelta(seconds=5))

            states = {
                "sensor.freq": st("sensor.freq", "40", {"unit_of_measurement": "Hz"}),
                "climate.a": st("climate.a", "off" if i < 2 else "cool", {"temperature": 22, "current_temperature": 25}),
                "climate.b": st("climate.b", "cool", {"temperature": 22, "current_temperature": 25}),
            }
            reader = observe._Reader(states, now, "°C")
            heads = [obs._head(reader, c, "outdoor1", "sensor.freq", hist, {}, {}, units) for c in units]
            learner.observe(now, heads, {}, hist)
        self.assertEqual(learner.table, {})


class UndirectedMode(unittest.TestCase):
    def test_a_compressor_running_in_auto_is_not_learned_as_rest(self) -> None:
        """heat_cool or auto running: cooling or heating, unknown which, so
        nothing is learned from the minute, as with an unknown compressor."""
        models_ = load("models")
        base = SimRoom().snapshot()
        for mode in ("heat_cool", "auto"):
            head = dataclasses.replace(base.heads[0], mode=mode, hvac_action="cooling", setpoint=21.0,
                                       own_temperature=25.0, compressor=models_.CompressorState.RUNNING)
            s = filt.minute_sample(dataclasses.replace(base, heads=(head,)), 12.0)
            self.assertFalse(s.compressor_known, mode)
            f = filt.HeatFilter.start(25.0)
            f.sensor_step = f.noise = f.innovation_avg = 0.01
            self.assertIn("compressor", f._why_not([s] * 5, True, True) or "")
        # the same head in cool is known, and stopped in auto is known too
        cool = dataclasses.replace(base.heads[0], mode="cool", compressor=models_.CompressorState.RUNNING)
        self.assertTrue(filt.minute_sample(dataclasses.replace(base, heads=(cool,)), 12.0).compressor_known)
        idle = dataclasses.replace(base.heads[0], mode="auto", compressor=models_.CompressorState.STOPPED)
        self.assertTrue(filt.minute_sample(dataclasses.replace(base, heads=(idle,)), 12.0).compressor_known)


class DrawSwap(unittest.TestCase):
    def test_a_stop_and_a_start_on_one_unit_in_the_same_minute_are_not_learned(self) -> None:
        """A stops as B starts on the same outdoor unit: it may never have
        stopped, so neither change is learned [D028]."""
        models_ = load("models")
        t0 = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
        base = dataclasses.replace(SimRoom().snapshot().heads[0], outdoor_unit="u1",
                                   setpoint=22.0, own_temperature=25.0)
        self.assertIsNotNone(unit.head_effort(dataclasses.replace(base, mode="cool")))

        def heads(a_on: bool, b_on: bool) -> list[Any]:
            run, stop = models_.CompressorState.RUNNING, models_.CompressorState.STOPPED
            return [dataclasses.replace(base, climate="climate.a", mode="cool" if a_on else "off",
                                        compressor=run if a_on else stop),
                    dataclasses.replace(base, climate="climate.b", mode="cool" if b_on else "off",
                                        compressor=run if b_on else stop)]

        def run(a_on: Any, b_on: Any) -> dict[str, Any]:
            learner = unit.DrawLearner(has_load_sensor=True)
            hist = noisy_history(1.5, until=t0)
            for i in range(12):
                now = t0 + timedelta(minutes=i)
                # the house load rises 0.6 kW at minute 2 in every case
                hist.add(now, 1.5 + (0.6 if i >= 2 else 0.0))
                learner.observe(now, heads(a_on(i), b_on(i)), {}, hist)
            return learner.table

        # a start on its own is learned from the same load rise...
        self.assertEqual(run(lambda i: False, lambda i: i >= 2)["u1"][4][1], 1)
        # ...but not when another head on the unit stopped in the same minute
        self.assertEqual(run(lambda i: i < 2, lambda i: i >= 2), {})


class RoomModelTests(unittest.TestCase):
    def test_lowest_rate_is_the_rate_held_not_the_lag(self) -> None:
        # cooling for 40 minutes from rest at the curve's first effort, the
        # lowest rate offered before anything is learned: the rate held is
        # that point's rate, though the lagged rate ramps up from 0 over the
        # first quarter hour [D100]
        sim = SimRoom(external=True)
        rm = model.RoomModel()
        first = unit.curve_efforts()[0]
        sim.command_direction, sim.command_effort = 1, first
        for _ in range(40):
            sim.advance()
            rm.minute(sim.snapshot(), sim.hour)
        assert rm.heat is not None
        fig = physics.figures(rm.heat.z)
        self.assertAlmostEqual(rm.unit.u_min, unit.rate_for_effort(fig, first, 1), places=6)

    def test_a_pull_down_at_full_rate_is_not_the_lowest_rate(self) -> None:
        # 40 minutes at the curve's top effort: held, but above the lowest
        # rate offered, so it says nothing about how low the unit goes [D100]
        sim = SimRoom(external=True)
        rm = model.RoomModel()
        sim.command_direction, sim.command_effort = 1, unit.curve_efforts()[-1]
        for _ in range(40):
            sim.advance()
            rm.minute(sim.snapshot(), sim.hour)
        self.assertIsNone(rm.unit.u_min)


    def test_answers(self) -> None:
        rm = model.RoomModel()
        self.assertFalse(rm.answers(24.0, 0.0, 12.0).learning)
        sim = SimRoom()
        for _ in range(10):
            sim.advance()
            rm.minute(sim.snapshot(), sim.hour)
        rm.five_minutes(sim.now)
        a = rm.answers(sim.outdoor(), 0.0, sim.hour)
        self.assertTrue(a.learning)
        self.assertAlmostEqual(a.figures["a"], math.exp(rm.heat.z[physics.I_LN_A]))
        self.assertEqual((a.delay_min, a.lag_min), (6.0, 15.0))
        rm.heat.z[physics.I_DELAY] = 9.0
        rm.heat.z[physics.I_LN_LAG] = math.log(12.0)
        b = rm.answers(sim.outdoor(), 0.0, sim.hour)
        self.assertEqual(b.delay_min, 9.0)
        self.assertAlmostEqual(b.lag_min, 12.0)
        self.assertAlmostEqual(a.top_cool, 6.0)
        self.assertIsNotNone(a.drift)
        self.assertEqual(a.last_correction, rm.heat.last_correction)
        rm.heat.last_correction = sim.now
        self.assertEqual(rm.answers(sim.outdoor(), 0.0, sim.hour).last_correction, sim.now)


if __name__ == "__main__":
    unittest.main()
