"""The figures' range, unknown inputs and the plan's start (eleventh review, 6 November 2026)."""

from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime

from tests.pure._load import load
from tests.pure.test_control import inputs, plan

filt = load("model.filter")
physics = load("model.physics")
forecast = load("model.forecast")
const = load("const")
models = load("models")
SK = models.StepKind

NOW = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)


def filter_with(b: float, c: float) -> object:
    f = filt.HeatFilter.start(25.0)
    f.noise = 0.003
    f.z[physics.I_LN_B] = math.log(b)
    f.z[physics.I_LN_C] = math.log(c)
    return f


class FigureRangeVersusFiveMinuteMatrices(unittest.TestCase):
    """The figure range is what every exact step carries, the plan's and the
    forecast's 5 minutes included: at the top of the range nothing raises,
    and a stored model beyond it is refused [Der]."""

    def test_the_top_of_the_range_is_1000_per_hour(self) -> None:
        self.assertAlmostEqual(math.exp(const.FIGURE_LOG_RANGE[1]), 1e3)
        self.assertIsNone(filt.HeatFilter.from_dict(filter_with(9000.0, 9000.0).as_dict()))

    def test_forecast_and_plan_carry_figures_at_the_top(self) -> None:
        f = filter_with(1000.0, 1000.0)
        for i in (physics.I_LN_A, physics.I_LN_K):
            f.z[i] = const.FIGURE_LOG_RANGE[1]
        step = forecast.ForecastInput(24.0, 50.0, 0.0, 10.0, SK.REST, 0.0)
        forecast.forecast(f, None, [step] * 12, e_now=15.0)
        plan.make_plan(inputs(heat=f))


class UnknownInputVariance(unittest.TestCase):
    def test_running_in_heat_cool_adds_variance(self) -> None:
        """A head running in heat_cool or auto with its compressor unknown
        may move the air either way: the air's noise is widened by the
        larger curve's top [Der]."""
        z = filt.start_state(25.0)
        m = filt.MinuteSample(time=NOW, hour=10.0, outdoor=30.0, outdoor_rh=50.0, sun=0.0, sun_estimated=False,
                              reading=25.0, reading_rh=50.0, effort=0.0, direction=0, mode_direction=0, dry=False,
                              compressor_known=False, compressor_running=True)
        var_t, _ = filt.unknown_input_variance(z, [m] * 5)
        top = physics.curve_rate(const.UNIT_CURVE_EFFORTS_C, z[physics.I_COOL:physics.I_COOL + 6], 4.0)
        self.assertGreater(var_t, 0.0, f"no added variance; the unit may be moving the air at up to {top} C/h")


class PidAfterAMidStepPlan(unittest.TestCase):
    def test_a_plan_made_between_filter_steps_starts_from_the_carried_estimate(self) -> None:
        """A plan made between filter steps (a band change, a new ceiling, a
        user's choice, free-cooling advice) starts its path from the estimate
        carried forward from the filter's last step, the one the PID acts
        on, so a room exactly on its model shows no error [Der]."""
        pid = load("control.pid")
        control = load("control")
        f = filter_with(0.2, 0.3)
        f.z[0] = 26.0  # warm room, cool outdoor: drifting down
        slope0, since0 = -2.0, 4.0 / 60.0
        inp = inputs(heat=f, now=NOW, t_now=pid.estimate_now(f.z[0], slope0, since0))
        g = pid.gains(physics.figures(f.z), f.delay_min, f.lag_min, pid.actuation_minutes(0.1))
        rc = control.RoomControl()
        rc.replan(inp, g)
        slope = -2.0  # C/h, the model's rate now (drift - u)
        since_h = 4.0 / 60.0  # the last filter step was 4 minutes ago
        t_est = pid.estimate_now(f.z[0], slope, since_h)
        t_path, _ = plan.path_now(rc.plan, NOW, rc.plan_t0)
        self.assertAlmostEqual(t_est - t_path, 0.0, places=2,
                               msg=f"error {t_est - t_path:+.3f} C at the moment the plan was made")


class PlanStart(unittest.TestCase):
    def test_the_plan_starts_from_t_now(self) -> None:
        """The plan's first point follows from t_now, not the filter's last
        step, when the two differ [Der]."""
        f = filter_with(0.2, 0.3)
        f.z[0] = 26.0
        from_filter = plan.make_plan(inputs(heat=f, now=NOW))
        from_now = plan.make_plan(inputs(heat=f, now=NOW, t_now=27.0))
        self.assertGreater(from_now.points[0].t - from_filter.points[0].t, 0.5)

if __name__ == "__main__":
    unittest.main()
