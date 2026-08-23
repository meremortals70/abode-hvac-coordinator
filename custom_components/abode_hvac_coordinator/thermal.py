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
from typing import Any, Final

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

#: Operating-point bins, by the gap between the commanded setpoint and the
#: room, in degrees C ("approach"). A room spends most of its life near
#: setpoint, where an inverter runs at a fraction of the rate it manages at
#: full tilt; one pooled coefficient describes neither. 0.8.9, finding 9.
#:
#: Physically motivated and unmeasured — a guess, same standing as the other
#: constants in this file. A week of real running data shows where the rate
#: actually breaks, and they move to match.
BIN_NAMES: Final[tuple[str, ...]] = ("at_setpoint", "close", "working", "pulldown")
APPROACH_AT_SETPOINT_C: Final = 0.5
APPROACH_CLOSE_C: Final = 1.5
APPROACH_WORKING_C: Final = 3.0


def approach_bin(approach_c: float) -> int:
    """Which of the four operating-point bins an approach magnitude falls in.

    Index into `BIN_NAMES` and into `ThermalModel.k_sensible_bins`.
    """
    magnitude = abs(approach_c)
    if magnitude < APPROACH_AT_SETPOINT_C:
        return 0
    if magnitude < APPROACH_CLOSE_C:
        return 1
    if magnitude < APPROACH_WORKING_C:
        return 2
    return 3


def _mean_approach(obs: Observation) -> float:
    """Mean magnitude of the gap to the commanded setpoint across an interval.

    Chosen from the mean of both ends rather than re-evaluated as the room
    moves, and the bin is picked once per interval. Re-evaluating it every
    cycle and resetting the anchor when it changes is the 0.8.3 fault exactly:
    an anchor replaced faster than it can mature, and no coefficient ever
    gaining a sample.
    """
    assert obs.commanded_setpoint_c is not None
    start_gap = abs(obs.commanded_setpoint_c - obs.indoor_start_c)
    end_gap = abs(obs.commanded_setpoint_c - obs.indoor_end_c)
    return (start_gap + end_gap) / 2.0


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

    def update(
        self, observed: float, elapsed_hours: float, *, variance_scale: float = 1.0
    ) -> None:
        """Fold one observation in, by scalar Kalman update.

        `variance_scale` widens the observation noise for a candidate that is
        trusted less than a clean reading — used by `DrawModel` (0.8.9,
        finding 14) to fold in a noisy house-load observation without
        rejecting it outright. 1.0 for every existing caller: nothing here
        changes for `ThermalModel`.
        """
        # Let the estimate drift a little with time, so the filter keeps
        # listening rather than locking onto an early answer forever.
        prior_variance = self.variance + PROCESS_VARIANCE_PER_HOUR * elapsed_hours
        gain = prior_variance / (
            prior_variance + OBSERVATION_VARIANCE * variance_scale
        )
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
    #: The setpoint commanded for this interval, if known. Feeds the approach
    #: bin (0.8.9, finding 9): the gap between this and the room is what an
    #: inverter modulates against. None for an interval with no commanded
    #: setpoint recorded, which updates the pooled coefficients only.
    commanded_setpoint_c: float | None = None

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
    #: Degrees per hour the compressor moves dry bulb. Pooled across every
    #: operating point; updated from every usable interval regardless of
    #: bin, and the fallback for a bin that has not converged.
    k_sensible: Coefficient = field(default_factory=lambda: Coefficient(2.0))
    #: Percentage points of relative humidity per hour in dry mode.
    k_latent: Coefficient = field(default_factory=lambda: Coefficient(8.0))
    #: `k_sensible`, binned by approach — see `BIN_NAMES`/`approach_bin`.
    #: 0.8.9, finding 9. A bin that has not converged falls back to the
    #: pooled `k_sensible` above.
    k_sensible_bins: list[Coefficient] = field(
        default_factory=lambda: [Coefficient(2.0) for _ in BIN_NAMES]
    )
    #: Net RH change per hour while the compressor drives sensible cooling —
    #: the humidity rise from falling dry bulb and the fall from condensation,
    #: netted together and signed. 0.8.9, finding 17. Pooled rather than
    #: binned: the dry-versus-cool comparison only ever asks whether this has
    #: converged, not which operating point it is at.
    k_rh_cooling: Coefficient = field(default_factory=lambda: Coefficient(0.0))

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
        self._observe_rh_cooling(obs)

        # Passive means nothing was driving the room. Dry mode energises the
        # compressor and moves both dry bulb and humidity, so an interval
        # spent drying teaches nothing about how the room drifts on its own —
        # it was previously folded into `k_loss` and `k_solar` because
        # `compressor` reports direction and dry mode has none.
        if obs.compressor == 0 and not obs.drying:
            self._observe_passive(obs)

    def _observe_sensible(self, obs: Observation) -> None:
        """Compressor authority over dry bulb, from intervals where it ran.

        **The negative tail is no longer discarded (0.8.9, finding 9).** A
        room that moved against the compressor at a given approach is real
        information about that approach on that day — the load beat the unit
        — and dropping it truncated the noise distribution and biased what
        remained high. A bin can now settle at or below zero, which
        `hours_to_reach` already treats as unreachable.
        """
        if obs.compressor == 0:
            return
        rate = (obs.indoor_end_c - obs.indoor_start_c) / obs.elapsed_hours
        # Expressed as magnitude in the direction the compressor was driving,
        # so heating and cooling contribute to the same coefficient.
        observed = rate * obs.compressor
        self.k_sensible.update(observed, obs.elapsed_hours)
        if obs.commanded_setpoint_c is not None:
            bin_index = approach_bin(_mean_approach(obs))
            self.k_sensible_bins[bin_index].update(observed, obs.elapsed_hours)

    def _observe_rh_cooling(self, obs: Observation) -> None:
        """Humidity response while the compressor drives sensible cooling.

        0.8.9, finding 17. `Observation` already carries both humidity
        readings; before this they were measured every interval and thrown
        away unless the unit was drying.

        Signed and deliberately not decomposed: RH rises because dry bulb is
        falling at roughly constant vapour pressure, and RH falls because a
        coil below dew point is condensing. The net is what the room sensor
        sees and what the index cares about — separating the two would need a
        coil temperature this project does not have, for no gain the
        decision could use.
        """
        if obs.compressor != -1:
            # Only cooling. A heating interval says nothing about a coil
            # condensing moisture, and a drying interval is `_observe_latent`'s
            # question, not this one.
            return
        rate = (obs.humidity_end - obs.humidity_start) / obs.elapsed_hours
        self.k_rh_cooling.update(rate, obs.elapsed_hours)

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

    def sensible_rate_at(self, approach_c: float) -> float | None:
        """The best available `k_sensible` for one approach.

        0.8.10. Fixes a gap disclosed at the 0.8.9 handover: the
        dry-versus-cool comparison (`modes._latent_route`) was still reading
        the pooled coefficient regardless of the bin an interval belongs to,
        even though `hours_to_reach` and `energy_for` both went bin-aware in
        0.8.9. The pooled figure describes full tilt on average; a room
        deciding between drying and cooling is usually close to its band,
        where the true rate is a fraction of that.

        Same fallback shape as `DrawModel.draw_kw`: this bin if converged,
        else the pooled coefficient, else `None` — which tells the caller
        nothing has converged at all, and it should fall back to the
        humidity threshold rather than trust a number the filter has not
        settled on. Never the raw seed.
        """
        coefficient = self.k_sensible_bins[approach_bin(approach_c)]
        if coefficient.converged:
            return coefficient.value
        if self.k_sensible.converged:
            return self.k_sensible.value
        return None

    def _sensible_rate(self, bin_index: int) -> float:
        """The compressor's rate at one operating point, bin or pooled.

        A bin that has not converged falls back to the pooled coefficient,
        which is itself only ever consulted once `k_sensible.converged` has
        already gated the caller.
        """
        coefficient = self.k_sensible_bins[bin_index]
        if coefficient.converged:
            return coefficient.value
        return self.k_sensible.value

    def hours_to_reach(
        self,
        indoor_c: float,
        target_c: float,
        outdoor_c: float | None,
        *,
        direct_sun: bool,
    ) -> float | None:
        """How long the compressor needs to reach a target, or None.

        **Piecewise across the approach bins (0.8.9, finding 9).** A pulldown
        from 30 C to 23 C crosses all four operating points and was
        previously estimated at one averaged rate; it is now walked segment
        by segment, each crossed at its own bin's rate, so a room that is
        mostly close to setpoint is not estimated at the pulldown rate for
        the whole horizon.

        Accounts for the room drifting while the compressor works, throughout
        rather than per segment: on a hot day the unit is fighting the drift
        the whole way, and re-deriving the drift term per segment would cost
        a great deal of complexity for a correction this small.
        """
        if not self.k_sensible.converged:
            return None
        gap = target_c - indoor_c
        if abs(gap) < MIN_DRIVE:
            return 0.0

        direction = 1.0 if gap > 0 else -1.0
        drift = self.drift_rate(indoor_c, outdoor_c, direct_sun=direct_sun)

        # Segments walked from the largest approach down to zero. Each entry
        # is (upper bound, lower bound, bin index); only the portion of the
        # starting gap that actually falls in a segment is charged against it.
        segments = (
            (math.inf, APPROACH_WORKING_C, 3),
            (APPROACH_WORKING_C, APPROACH_CLOSE_C, 2),
            (APPROACH_CLOSE_C, APPROACH_AT_SETPOINT_C, 1),
            (APPROACH_AT_SETPOINT_C, 0.0, 0),
        )
        remaining = abs(gap)
        total_hours = 0.0
        for upper, lower, bin_index in segments:
            if remaining <= lower:
                continue
            length = min(remaining, upper) - lower
            if length <= 0:
                continue
            net = self._sensible_rate(bin_index) * direction
            if drift is not None:
                net += drift
            # The compressor is losing at this operating point. No finite
            # answer for the whole pull.
            if net * direction <= 0:
                return None
            total_hours += length / abs(net)
            remaining = lower
        return total_hours

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
        # The at-setpoint bin's rate, not the pooled figure (0.8.9, finding
        # 9). Holding at target is exactly what that bin describes, and using
        # it is what stops `hold_fraction` saturating toward full duty near
        # setpoint on a pooled rate dominated by pulldown intervals.
        hold_rate = self._sensible_rate(0)
        if drift is not None and hold_rate > 0:
            # Duty cycle needed to cancel the drift — only if the unit can
            # actually correct the direction the room is drifting in.
            drift_correctable = (drift > 0 and can_cool) or (
                drift < 0 and can_heat
            )
            if drift_correctable:
                hold_fraction = min(abs(drift) / hold_rate, 1.0)

        return round(rated_kw * (pull_hours + remaining * hold_fraction), 3)

    # ---- persistence --------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """For the store."""
        return {
            "k_loss": self.k_loss.as_dict(),
            "k_solar": self.k_solar.as_dict(),
            "k_sensible": self.k_sensible.as_dict(),
            "k_latent": self.k_latent.as_dict(),
            "k_sensible_bins": [c.as_dict() for c in self.k_sensible_bins],
            "k_rh_cooling": self.k_rh_cooling.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ThermalModel:
        """Restore from the store. Anything unreadable starts fresh.

        Losing this costs convergence time, not correctness: the caller falls
        back to hysteresis until the filter has learned again.

        **A pre-0.8.9 record has no `k_sensible_bins` or `k_rh_cooling`.**
        Both start fresh and unconverged, which is exactly the fallback
        state, so a store written by 0.8.8 loads with every prediction
        unchanged: `_sensible_rate` falls straight through to the pooled
        `k_sensible` it already restored correctly, and the dry-versus-cool
        comparison drops to state 2 until `k_rh_cooling` converges again.
        """
        model = cls()
        if not isinstance(data, dict):
            return model
        model.k_loss = Coefficient.from_dict(data.get("k_loss", {}), 0.15)
        model.k_solar = Coefficient.from_dict(data.get("k_solar", {}), 1.0)
        model.k_sensible = Coefficient.from_dict(data.get("k_sensible", {}), 2.0)
        model.k_latent = Coefficient.from_dict(data.get("k_latent", {}), 8.0)
        raw_bins = data.get("k_sensible_bins")
        if isinstance(raw_bins, list) and len(raw_bins) == len(BIN_NAMES):
            model.k_sensible_bins = [
                Coefficient.from_dict(entry, 2.0) for entry in raw_bins
            ]
        model.k_rh_cooling = Coefficient.from_dict(data.get("k_rh_cooling", {}), 0.0)
        return model

    def diagnostics(self) -> dict[str, Any]:
        """Human-readable state, for the decision trace and diagnostics."""
        pooled: dict[str, Any] = {
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
                ("k_rh_cooling", self.k_rh_cooling),
            )
        }
        pooled["k_sensible_bins"] = {
            name: {
                "value": round(coefficient.value, 4),
                "variance": round(coefficient.variance, 4),
                "samples": coefficient.samples,
                "converged": coefficient.converged,
            }
            for name, coefficient in zip(BIN_NAMES, self.k_sensible_bins)
        }
        return pooled


#: Seed draw, in kW, for a fresh `DrawModel` bin — the same order-of-magnitude
#: placeholder `forecast.ASSUMED_UNIT_KW` uses. Only ever read before any bin
#: or the pooled figure has converged, at which point `draw_kw`'s caller-
#: supplied fallback is used instead, so this value itself is never returned.
_DRAW_SEED_KW = 1.2

#: Floor on the quality score a draw observation can enter with. A wide-open
#: floor rather than zero: dividing by zero quality would be an infinite
#: variance, well-formed but pointless to compute, and the point of scoring
#: rather than gating is that even a messy observation still moves the
#: estimate a little (0.8.9, finding 14).
MIN_DRAW_QUALITY = 0.02


@dataclass(slots=True)
class DrawModel:
    """Learned electrical draw of one compressor, in kW, binned by approach.

    Pure. One instance per outdoor unit group (0.8.9, finding 14; the unit of
    account finding 13 delivered in 0.8.8). Nothing here knows what a
    compressor is, how its state is detected, or where the kW candidate came
    from — the coordinator hands over a bin, an observed kW figure and a
    quality score in (0, 1], and this class runs the same scalar Kalman
    update `Coefficient` already provides, scaled by how much the observation
    is trusted.

    **Nothing is rejected.** A noisy candidate still updates the filter, just
    barely: quality scales the observation variance, so a clean reading moves
    the estimate and a messy one is folded in wide enough to leave it almost
    where it was. A rejecting gate cannot tell a kettle from a compressor
    whose draw has genuinely changed; a quality-weighted filter does not need
    to, and it does not starve the pulldown bin, where a clean event is rare.
    """

    bins: list[Coefficient] = field(
        default_factory=lambda: [Coefficient(_DRAW_SEED_KW) for _ in BIN_NAMES]
    )
    pooled: Coefficient = field(default_factory=lambda: Coefficient(_DRAW_SEED_KW))

    def observe(self, bin_index: int, draw_kw: float, quality: float) -> None:
        """Fold one candidate observation in, weighted by its quality."""
        variance_scale = 1.0 / max(quality, MIN_DRAW_QUALITY)
        # A draw observation has no natural "elapsed hours" the way a thermal
        # interval does — candidates arrive irregularly, on a compressor
        # state change. A nominal hour of process noise per accepted
        # observation keeps the filter listening across a house that changes
        # slowly (a re-gassed unit, a compressor ageing) without inventing an
        # elapsed time that means nothing. Recorded as a guess, alongside the
        # confidence curve this quality score itself stands in for.
        self.bins[bin_index].update(draw_kw, 1.0, variance_scale=variance_scale)
        self.pooled.update(draw_kw, 1.0, variance_scale=variance_scale)

    def draw_kw(self, bin_index: int, fallback_kw: float) -> float:
        """Three-level fallback: this bin, then the pooled figure, then the
        caller's constant — `forecast.ASSUMED_UNIT_KW` for every caller today.
        """
        if self.bins[bin_index].converged:
            return self.bins[bin_index].value
        if self.pooled.converged:
            return self.pooled.value
        return fallback_kw

    def as_dict(self) -> dict[str, Any]:
        """For the store."""
        return {
            "bins": [c.as_dict() for c in self.bins],
            "pooled": self.pooled.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DrawModel:
        """Restore from the store. Anything unreadable starts fresh."""
        model = cls()
        if not isinstance(data, dict):
            return model
        raw_bins = data.get("bins")
        if isinstance(raw_bins, list) and len(raw_bins) == len(BIN_NAMES):
            model.bins = [
                Coefficient.from_dict(entry, _DRAW_SEED_KW) for entry in raw_bins
            ]
        model.pooled = Coefficient.from_dict(data.get("pooled", {}), _DRAW_SEED_KW)
        return model

    def diagnostics(self) -> dict[str, Any]:
        """Human-readable state, for diagnostics."""

        def _entry(coefficient: Coefficient) -> dict[str, Any]:
            return {
                "value": round(coefficient.value, 3),
                "variance": round(coefficient.variance, 4),
                "samples": coefficient.samples,
                "converged": coefficient.converged,
            }

        return {
            "bins": {
                name: _entry(coefficient)
                for name, coefficient in zip(BIN_NAMES, self.bins)
            },
            "pooled": _entry(self.pooled),
        }


def is_finite(value: float | None) -> bool:
    """Whether a value is a usable number."""
    return value is not None and math.isfinite(value)
