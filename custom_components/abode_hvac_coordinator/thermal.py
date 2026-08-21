"""Thermal model.

Pure. No Home Assistant imports.

WHAT IT LEARNS
--------------
Per room, from observation, four coefficients:

    k_loss      how fast the room drifts toward outdoor conditions, per hour
    k_solar     how much the sun raises the room when it is on the glass
    k_sensible  how fast the compressor moves dry-bulb temperature, per hour
    k_latent    how fast dry mode moves humidity, per hour

**Sensible and latent are learned separately, and that is the whole point.**
A model built for a heating climate learns heat loss, heating power and solar
gain — all sensible terms — because northern-hemisphere heating has no latent
component worth modelling. A humid subtropical climate does. Rain is the case
that separates them: dry bulb falls while humidity climbs toward saturation, so
sensible load drops as latent load rises, and a filter fitting one coefficient
to both is wrong on exactly those days.

HOW IT LEARNS
-------------
A scalar Kalman update per coefficient. Each observation is an interval: what
the room did, against what the model predicted it would do. The residual is
attributed to whichever coefficient was driving over that interval, weighted by
how strongly it was driving.

Full matrix estimation is not used. The coefficients are near-independent over
short intervals — heat loss acts when the compressor is off, compressor gain
acts when it is on — so the cross terms a matrix filter would estimate are
mostly noise, and a scalar filter per coefficient is both easier to reason
about and easier to test.

CONVERGENCE
-----------
Each coefficient carries its own variance and sample count. A coefficient is
converged when it has enough samples and its variance has fallen far enough.
Until every coefficient the caller needs has converged, predictions are refused
and the caller falls back to hysteresis.

**The system works on day one and improves**, rather than needing a training
period before it does anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: Samples before a coefficient is trusted, however tight its variance looks.
#: A handful of agreeing observations can be a coincidence.
MIN_SAMPLES = 20

#: Variance below which a coefficient is considered settled.
CONVERGED_VARIANCE = 0.05

#: Starting values. Deliberately wide: the filter should be led by observation,
#: not by a prior. These are order-of-magnitude only.
INITIAL_VARIANCE = 1.0

#: Observation noise. Room sensors are noisy and intervals are short, so a
#: single observation should move a settled coefficient very little.
OBSERVATION_VARIANCE = 0.5

#: Process noise per hour. Small and non-zero: a house changes slowly — new
#: curtains, a door left open, a season — and a filter with none eventually
#: stops listening.
PROCESS_VARIANCE_PER_HOUR = 0.001

#: Intervals shorter or longer than these carry no information worth having.
#: Too short and sensor quantisation dominates; too long and something else
#: changed inside the interval.
MIN_INTERVAL_HOURS = 1.0 / 60.0
MAX_INTERVAL_HOURS = 1.0

#: Below this, whatever was driving was barely driving, and dividing by it
#: turns sensor noise into an enormous residual.
MIN_DRIVE = 0.05


@dataclass(slots=True)
class Coefficient:
    """One learned number, with how sure the filter is of it."""

    value: float
    variance: float = INITIAL_VARIANCE
    samples: int = 0

    @property
    def converged(self) -> bool:
        """Whether this coefficient can be relied on."""
        return self.samples >= MIN_SAMPLES and self.variance <= CONVERGED_VARIANCE

    def update(self, observed: float, elapsed_hours: float) -> None:
        """Fold one observation in, by scalar Kalman update."""
        # Let the estimate drift a little with time, so the filter keeps
        # listening rather than locking onto an early answer forever.
        prior_variance = self.variance + PROCESS_VARIANCE_PER_HOUR * elapsed_hours
        gain = prior_variance / (prior_variance + OBSERVATION_VARIANCE)
        self.value += gain * (observed - self.value)
        self.variance = (1.0 - gain) * prior_variance
        self.samples += 1

    def as_dict(self) -> dict[str, float | int]:
        """For persistence."""
        return {
            "value": self.value,
            "variance": self.variance,
            "samples": self.samples,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], default: float) -> Coefficient:
        """Restore from persistence, tolerating a partial or absent record."""
        if not isinstance(data, dict):
            return cls(value=default)
        try:
            return cls(
                value=float(data.get("value", default)),
                variance=float(data.get("variance", INITIAL_VARIANCE)),
                samples=int(data.get("samples", 0)),
            )
        except (TypeError, ValueError):
            return cls(value=default)


@dataclass(frozen=True, slots=True)
class Observation:
    """One interval of what the room actually did.

    Everything is measured at both ends of the interval; nothing here is
    inferred. `elapsed_hours` is the wall time between them.
    """

    elapsed_hours: float
    indoor_start_c: float
    indoor_end_c: float
    humidity_start: float
    humidity_end: float
    outdoor_c: float | None
    #: Whether the sun was on the room's glass over the interval.
    direct_sun: bool
    #: Whether the compressor was moving sensible heat, and in which direction.
    #: +1 heating, -1 cooling, 0 idle.
    compressor: int
    #: Whether dry mode was running.
    drying: bool

    @property
    def usable(self) -> bool:
        """Whether this interval carries information worth learning from."""
        return MIN_INTERVAL_HOURS <= self.elapsed_hours <= MAX_INTERVAL_HOURS


@dataclass(slots=True)
class ThermalModel:
    """Per-room learned thermal behaviour."""

    #: Degrees per hour per degree of indoor-outdoor difference.
    k_loss: Coefficient = field(default_factory=lambda: Coefficient(0.15))
    #: Degrees per hour while the sun is on the glass.
    k_solar: Coefficient = field(default_factory=lambda: Coefficient(1.0))
    #: Degrees per hour the compressor moves dry bulb.
    k_sensible: Coefficient = field(default_factory=lambda: Coefficient(2.0))
    #: Percentage points of relative humidity per hour in dry mode.
    k_latent: Coefficient = field(default_factory=lambda: Coefficient(8.0))

    # ---- learning -----------------------------------------------------

    def observe(self, obs: Observation) -> None:
        """Learn from one interval.

        Each coefficient is updated only from intervals where it was actually
        the thing driving. An interval with the compressor running teaches
        nothing reliable about passive heat loss, because the compressor
        swamps it.
        """
        if not obs.usable:
            return

        self._observe_sensible(obs)
        self._observe_latent(obs)

        # Passive means nothing was driving the room. Dry mode energises the
        # compressor and moves both dry bulb and humidity, so an interval
        # spent drying teaches nothing about how the room drifts on its own —
        # it was previously folded into `k_loss` and `k_solar` because
        # `compressor` reports direction and dry mode has none.
        if obs.compressor == 0 and not obs.drying:
            self._observe_passive(obs)

    def _observe_sensible(self, obs: Observation) -> None:
        """Compressor authority over dry bulb, from intervals where it ran."""
        if obs.compressor == 0:
            return
        rate = (obs.indoor_end_c - obs.indoor_start_c) / obs.elapsed_hours
        # Expressed as magnitude in the direction the compressor was driving,
        # so heating and cooling contribute to the same coefficient.
        observed = rate * obs.compressor
        if observed <= 0:
            # The room moved against the compressor. Something else dominated
            # the interval — a door open, a heat load — and this teaches
            # nothing about the unit.
            return
        self.k_sensible.update(observed, obs.elapsed_hours)

    def _observe_latent(self, obs: Observation) -> None:
        """Dry-mode authority over humidity, learned in its own right.

        This is the term a heating-climate model does not have and this
        climate cannot do without.
        """
        if not obs.drying:
            return
        rate = (obs.humidity_start - obs.humidity_end) / obs.elapsed_hours
        if rate <= 0:
            return
        self.k_latent.update(rate, obs.elapsed_hours)

    def _observe_passive(self, obs: Observation) -> None:
        """Heat loss and solar gain, from intervals with no compressor."""
        if obs.outdoor_c is None:
            return
        rate = (obs.indoor_end_c - obs.indoor_start_c) / obs.elapsed_hours
        difference = obs.outdoor_c - obs.indoor_start_c

        if obs.direct_sun:
            # Attribute what the difference does not explain to the sun.
            explained = self.k_loss.value * difference
            self.k_solar.update(rate - explained, obs.elapsed_hours)
            return

        if abs(difference) < MIN_DRIVE:
            # Indoors and outdoors are level. Nothing is driving, so the
            # residual is noise divided by nearly zero.
            return
        self.k_loss.update(rate / difference, obs.elapsed_hours)

    # ---- prediction ---------------------------------------------------

    @property
    def converged(self) -> bool:
        """Whether the passive terms can be relied on for prediction."""
        return self.k_loss.converged

    def drift_rate(
        self, indoor_c: float, outdoor_c: float | None, *, direct_sun: bool
    ) -> float | None:
        """Degrees per hour the room moves unaided, or None if not yet known.

        **A sunlit room with an unconverged solar term returns None, not a
        number missing its largest contribution.** This previously added the
        solar term only when `k_solar` had converged but returned a rate
        either way, so a west-facing room in the afternoon produced a drift
        estimate built from heat loss alone. `holds_through` then reported
        that the band would hold and the room entered COAST with the sun full
        on the glass.

        Absence of a converged coefficient is not evidence that the term is
        zero. The only honest answer is that the model cannot say, which every
        caller already handles.
        """
        if not self.k_loss.converged or outdoor_c is None:
            return None
        if direct_sun and not self.k_solar.converged:
            return None
        rate = self.k_loss.value * (outdoor_c - indoor_c)
        if direct_sun:
            rate += self.k_solar.value
        return rate

    def holds_through(
        self,
        indoor_c: float,
        outdoor_c: float | None,
        *,
        direct_sun: bool,
        hours: float,
        lower_c: float,
        upper_c: float,
    ) -> bool | None:
        """Whether the room stays inside the bounds unaided over a horizon.

        None means the model cannot say, which the caller must treat as "do not
        coast" rather than as "yes".
        """
        rate = self.drift_rate(indoor_c, outdoor_c, direct_sun=direct_sun)
        if rate is None:
            return None
        projected = indoor_c + rate * hours
        return lower_c <= projected <= upper_c

    def hours_to_reach(
        self,
        indoor_c: float,
        target_c: float,
        outdoor_c: float | None,
        *,
        direct_sun: bool,
    ) -> float | None:
        """How long the compressor needs to reach a target, or None.

        Accounts for the room drifting while the compressor works: on a hot day
        the unit is fighting the drift, so it takes longer than the compressor
        rate alone suggests.
        """
        if not self.k_sensible.converged:
            return None
        gap = target_c - indoor_c
        if abs(gap) < MIN_DRIVE:
            return 0.0

        direction = 1.0 if gap > 0 else -1.0
        net = self.k_sensible.value * direction
        drift = self.drift_rate(indoor_c, outdoor_c, direct_sun=direct_sun)
        if drift is not None:
            net += drift

        # The compressor is losing. No finite answer.
        if net * direction <= 0:
            return None
        return abs(gap / net)

    def energy_for(
        self,
        indoor_c: float,
        target_c: float,
        outdoor_c: float | None,
        *,
        direct_sun: bool,
        hours: float,
        rated_kw: float,
        can_heat: bool = True,
        can_cool: bool = True,
    ) -> float | None:
        """Projected energy over a horizon, in kWh, or None if unknown.

        Two parts: pulling the room to target, then holding it there against
        the drift for the rest of the horizon. Deliberately simple — it feeds a
        forecast that another system turns into a reserve, not a billing model.

        `can_heat`/`can_cool` are what `select_actuator` already checks before
        it will ever command the compressor in that direction. A unit that
        cannot correct a direction simply will not run for it — the room
        contributes nothing to the projection there, not a full-horizon draw
        pretending a correction is happening that never will.
        """
        if not self.k_sensible.converged:
            return None

        gap = target_c - indoor_c
        pull_possible = abs(gap) < MIN_DRIVE or (can_heat if gap > 0 else can_cool)

        pull_hours: float
        if not pull_possible:
            pull_hours = 0.0
            remaining = hours
        else:
            solved_hours = self.hours_to_reach(
                indoor_c, target_c, outdoor_c, direct_sun=direct_sun
            )
            if solved_hours is None:
                # Cannot reach it; assume it runs the whole horizon trying.
                return round(rated_kw * hours, 3)
            pull_hours = min(solved_hours, hours)
            remaining = max(hours - pull_hours, 0.0)

        hold_fraction = 0.0
        drift = self.drift_rate(target_c, outdoor_c, direct_sun=direct_sun)
        if drift is not None and self.k_sensible.value > 0:
            # Duty cycle needed to cancel the drift — only if the unit can
            # actually correct the direction the room is drifting in.
            drift_correctable = (drift > 0 and can_cool) or (
                drift < 0 and can_heat
            )
            if drift_correctable:
                hold_fraction = min(abs(drift) / self.k_sensible.value, 1.0)

        return round(rated_kw * (pull_hours + remaining * hold_fraction), 3)

    # ---- persistence --------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """For the store."""
        return {
            "k_loss": self.k_loss.as_dict(),
            "k_solar": self.k_solar.as_dict(),
            "k_sensible": self.k_sensible.as_dict(),
            "k_latent": self.k_latent.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ThermalModel:
        """Restore from the store. Anything unreadable starts fresh.

        Losing this costs convergence time, not correctness: the caller falls
        back to hysteresis until the filter has learned again.
        """
        model = cls()
        if not isinstance(data, dict):
            return model
        model.k_loss = Coefficient.from_dict(data.get("k_loss", {}), 0.15)
        model.k_solar = Coefficient.from_dict(data.get("k_solar", {}), 1.0)
        model.k_sensible = Coefficient.from_dict(data.get("k_sensible", {}), 2.0)
        model.k_latent = Coefficient.from_dict(data.get("k_latent", {}), 8.0)
        return model

    def diagnostics(self) -> dict[str, Any]:
        """Human-readable state, for the decision trace and diagnostics."""
        return {
            name: {
                "value": round(coefficient.value, 4),
                "variance": round(coefficient.variance, 4),
                "samples": coefficient.samples,
                "converged": coefficient.converged,
            }
            for name, coefficient in (
                ("k_loss", self.k_loss),
                ("k_solar", self.k_solar),
                ("k_sensible", self.k_sensible),
                ("k_latent", self.k_latent),
            )
        }


def is_finite(value: float | None) -> bool:
    """Whether a value is a usable number."""
    return value is not None and math.isfinite(value)
