"""The cheapest path to the centre (Section 4.4, control/plan.py) [R12][Prop].

Every 5 minutes the room's model is used to find the cheapest way, over the
next six hours, to keep the room near the centre of its band. Plans are
compared first by the time the forecast index spends outside the band, and
only among those with the least time outside by J [D008]:

    J = sum_i w ((HCI_i - HCI*_i) / h_i)^2 delta + sum_j p_j E_j + c_start N_starts

The search keeps the 50 best part-plans at each half-hour step (a beam
search). Waiting for the weather, using the blinds, drying, choosing a
cheaper hour, and not heating a room the weather is already bringing to the
centre all come out of this one calculation; none is a rule [Der].
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from ..const import (
    BLIND_CLOSED_TRANSMISSION,
    COIL_REBOUND_S,
    DRAW_WITHOUT_LOAD_SENSOR_KW,
    FILTER_NOISE_M_PER_H,
    FILTER_NOISE_T_PER_H,
    MIN_REST_S,
    MIN_RUN_S,
    PLAN_BEAM_WIDTH,
    PLAN_MARGIN_SIGMA,
    PLAN_POINTS_PER_STEP,
    PLAN_START_COST_SHARE,
    PLAN_STEP_H,
    PLAN_STEPS,
    REVERSAL_REST_STEPS,
    WINDOW_FACINGS,
)
from ..linalg import add, diagonal, sandwich
from ..model.filter import FilterStep, HeatFilter
from ..model.humidity import H_E, HumidityFilter, moisture_figures
from ..model.physics import I_M, I_T, STEP_H, curve_values, exact_step, figures
from ..model.unit import UnitModel
from ..models import (
    BandPoint,
    Plan,
    PlanAction,
    PlanPoint,
    RoomConfig,
    Snapshot,
    StepKind,
)
from ..observe import clear_sky_fraction
from ..sun import SunOnWindow, sun_on_window, sun_position

_E_COEFF, _OFFSET = 0.33, 4.00  # hci.py, Steadman [R2][D010]
_REBOUND_STEPS = round(COIL_REBOUND_S / 300.0)
_POINT_H = STEP_H


@dataclass(frozen=True, slots=True)
class Conditions:
    """The weather and sun at one 5-minute point ahead."""

    time: datetime
    hour: float
    outdoor: float
    outdoor_rh: float | None  # None: no exchange of moisture with outdoors is assumed
    sun_on_glass: bool
    f_clear: float


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """Everything the plan works within, gathered by the coordinator."""

    now: datetime
    heat: HeatFilter
    humidity: HumidityFilter | None
    e_now: float
    unit: UnitModel
    bands: Sequence[BandPoint]  # 72 points, from Comfort
    conditions: Sequence[Conditions]  # 72 points
    prices: Sequence[float]  # 12 half-hours, from Economics
    p_max: float
    draw_for_rate: Callable[[float], float | None]
    modes: frozenset[str]  # what the unit advertised [D024]
    blind_control: bool  # blinds may be moved and there are blinds [D022][D023]
    transmission_now: float  # the blinds as they are, where not part of the plan
    ceiling: float | None = None  # an enforced power ceiling, C/h, for cooling and dry [D041][D063]
    ceiling_heat: float | None = None  # the same allowance as a heating rate; the ceiling where not given
    waking: bool = False  # [D085]
    compressor_running: bool = False  # the outdoor unit, any room on it
    running_since: datetime | None = None
    stopped_since: datetime | None = None
    occupied: bool = True
    # the share of the room's openings open now: the air's coupling to
    # outdoors is a + v o, so the forecast carries the opening as it is
    # [D105]
    opening: float = 0.0
    # the outdoor air is dry enough to admit: the model has no learned
    # moisture coupling for an opening, so the dew point is a measured
    # constraint on the advice [D105]
    outdoor_air_dry: bool = True
    rebound_steps: int = 0  # coil rebound left from a stop before now [D060]
    last_kind: StepKind = StepKind.REST
    # the way the unit last ran: 1 cooling or drying, -1 heating, 0 not known
    # [D102]
    last_way: int = 0
    # the room estimate now: the filter's, carried forward on the model's
    # rate since its last step (control/pid.py estimate_now), so a plan made
    # between filter steps starts where the PID will measure it [Der]
    t_now: float | None = None


def conditions_ahead(
    snapshot: Snapshot,
    room: RoomConfig,
    location: tuple[float, float] | None,
    local_hour: Callable[[datetime], float],
    now: datetime,
    last_outdoor: float | None = None,
    measured_clear: float | None = None,
) -> list[Conditions] | None:
    """The weather and sun at each 5-minute point of the next six hours.

    Outdoor temperature and humidity from the forecast hour each point falls
    in, or the readings now where the forecast has none [Prop]; the
    clear-sky fraction from that hour's cloud cover or UV index; without
    either, the sky as the house's solar power measures it now; 1 without
    any [Set]; the sun on the glass from the sun's position and the
    window [D023], counted as not on the glass where that cannot be said.
    Without any outdoor temperature, now, forecast or last known (the
    filter's), there is nothing to plan with: None.
    """
    out_now = snapshot.outdoor_temperature.value
    if out_now is None:
        out_now = last_outdoor
    rh_now = snapshot.outdoor_humidity.value
    facing = WINDOW_FACINGS.get(room.window_facing) if room.window_facing else None
    points: list[Conditions] = []
    for i in range(1, PLAN_STEPS * PLAN_POINTS_PER_STEP + 1):
        when = now + timedelta(minutes=5 * i)
        hour = next((h for h in snapshot.forecast if h.start <= when < h.start + timedelta(hours=1)), None)
        t = hour.temperature if hour is not None and hour.temperature is not None else out_now
        rh = hour.humidity if hour is not None and hour.humidity is not None else rh_now
        f = clear_sky_fraction(hour.cloud_coverage, hour.uv_index) if hour is not None else None
        if f is None:
            f = measured_clear  # the sky as the solar power measures it now, carried forward [Prop]
        position = sun_position(location[0], location[1], when) if location is not None else None
        answer = sun_on_window(position, facing, room.overhang_projection_m, room.overhang_height_m)
        if t is None:
            return None
        points.append(
            Conditions(
                time=when,
                hour=local_hour(when),
                outdoor=t,
                outdoor_rh=rh,
                sun_on_glass=answer is SunOnWindow.ON,
                f_clear=f if f is not None else 1.0,
            )
        )
    return points


def drying_needed(inp: PlanInputs) -> bool:
    """Whether the plan can price a dry run at all [D103].

    Dry's benefit is latent, and only the moisture model can price it. With no
    humidity filter there is nothing to price: dry would be cooling at the
    lowest rate with an extra mode change, so it is not offered. Where the
    filter is present the optimiser decides, against the comfort index, the
    energy and the cost of the mode change. No humidity threshold is used.
    """
    return inp.humidity is not None


def candidate_actions(inp: PlanInputs, u_min: float, u_min_heat: float | None = None) -> list[PlanAction]:
    """The actions in each step: rest, dry, cool and heat at each rate on
    the learned curve from U_min to the top; only what the unit
    advertised, and never above an enforced ceiling [D024][D041]. Where
    blinds are part of the plan, each comes with the blinds left alone,
    closed and open; make_plan offers closed and open only in a step with
    the sun on the glass, and left alone only in one without [D023].
    u_min is the cooling floor, which dry also runs at; heating has its
    own floor on its own curve (u_min_heat; u_min where not given) [D100].
    Dry is offered only while the room is humid enough to need it [D103]."""
    fig = figures(inp.heat.z)
    blinds: list[bool | None] = [None, True, False] if inp.blind_control else [None]
    out: list[PlanAction] = []
    kinds: list[tuple[StepKind, float]] = [(StepKind.REST, 0.0)]
    top = math.inf if inp.ceiling is None else inp.ceiling
    if "dry" in inp.modes and u_min <= top and drying_needed(inp):
        kinds.append((StepKind.DRY, u_min))
    # the allowance is power: as a heating rate it is its own figure, read
    # on the heating curve [D028][D041]
    top_heat = top if inp.ceiling_heat is None else inp.ceiling_heat
    for kind, mode, steps, sign, floor, cap in (
        (StepKind.COOL, "cool", fig.cool_steps, 1.0, u_min, top),
        (StepKind.HEAT, "heat", fig.heat_steps, -1.0, u_min if u_min_heat is None else u_min_heat, top_heat),
    ):
        if mode not in inp.modes or (kind is StepKind.HEAT and inp.waking):
            continue
        rates = sorted({floor, *(r for r in curve_values(steps) if r > floor)})
        kinds.extend((kind, sign * r) for r in rates if r <= cap)
    for kind, rate in kinds:
        for b in blinds:
            out.append(PlanAction(kind, rate, b))
    return out


def _runs(kind: StepKind) -> bool:
    return kind in (StepKind.COOL, StepKind.HEAT, StepKind.DRY)


def _way(kind: StepKind) -> int:
    """The way a running kind moves the room: cool and dry 1, heat -1."""
    if kind in (StepKind.COOL, StepKind.DRY):
        return 1
    if kind is StepKind.HEAT:
        return -1
    return 0


def _reverses(running: bool, last_kind: StepKind, kind: StepKind) -> bool:
    """A running unit turned the other way: a stop and a start, not a
    carry-on (decision of 6 November 2026) [D016]."""
    return running and _runs(kind) and _way(last_kind) * _way(kind) < 0


@dataclass(slots=True)
class _Node:
    t: float
    m: float
    e: float
    outside: float
    comfort: float
    energy: float
    starts: int
    running: bool
    last_kind: StepKind
    rebound: int
    c_start: float
    u: float = 0.0  # the unit's rate now, through its delay and lag
    way: int = 0  # the way the unit last ran in this plan, from last_way
    actions: list[PlanAction] = field(default_factory=list)
    points: list[PlanPoint] = field(default_factory=list)

    @property
    def cost(self) -> float:
        """J so far: comfort, energy and starts [Prop]."""
        return self.comfort + self.energy + self.c_start * self.starts

    @property
    def key(self) -> tuple[float, float]:
        """Least time outside the band first, then least J [D008]."""
        return self.outside, self.cost

    def copy(self) -> _Node:
        return replace(self, actions=list(self.actions), points=list(self.points))


@dataclass(frozen=True, slots=True)
class _Fixed:
    """What every plan shares: the model's matrices and the uncertainty,
    which does not depend on the actions."""

    phi: tuple[float, float, float, float]
    gam: tuple[tuple[float, float, float, float], tuple[float, float, float, float]]
    g: tuple[float, ...]
    sigma_t: tuple[float, ...]
    h: float
    q: float
    drying: float
    cooling: float
    rebound: float
    have_moisture: bool
    dry_share: float  # dry's sensible share of the cooling curve [D108]
    u_decay: float  # e^(-5 min / (lag + delay))
    u_mean: float  # the share of the step's change in rate seen on average over it


def _fixed(inp: PlanInputs, opening: float | None = None) -> _Fixed:
    fig = figures(inp.heat.z)
    o = inp.opening if opening is None else opening
    ex = exact_step(fig, opening=o)
    p2 = [[inp.heat.p[0][0], inp.heat.p[0][1]], [inp.heat.p[1][0], inp.heat.p[1][1]]]
    noise = diagonal([FILTER_NOISE_T_PER_H * STEP_H, FILTER_NOISE_M_PER_H * STEP_H])
    # the margin is the room's own uncertainty, not the sensor's: the
    # comfort is the room's [Der]; the room's learned unforecast change is
    # part of it (model/filter.py air_noise)
    noise = add(noise, diagonal([inp.heat.air_noise, 0.0]))
    sigmas: list[float] = []
    for _ in inp.conditions:
        p2 = add(sandwich(ex.phi, p2), noise)
        sigmas.append(math.sqrt(max(p2[0][0], 0.0)))
    # the unit reaches a new rate through its delay and lag; the plan takes
    # them as one first-order lag of their sum (a delay as a lag of its own
    # length, the first-order Pade form) [Der]
    tau = (fig.lag_min + fig.delay_min) / 60.0
    decay = math.exp(-_POINT_H / tau)
    mf = moisture_figures(inp.humidity.z) if inp.humidity is not None else None
    return _Fixed(
        phi=(ex.phi[0][0], ex.phi[0][1], ex.phi[1][0], ex.phi[1][1]),
        gam=(
            (ex.gam[0][0], ex.gam[0][1], ex.gam[0][2], ex.gam[0][3]),
            (ex.gam[1][0], ex.gam[1][1], ex.gam[1][2], ex.gam[1][3]),
        ),
        g=tuple(fig.g(c.hour) for c in inp.conditions),
        sigma_t=tuple(sigmas),
        h=mf.h if mf else 0.0,
        q=mf.q if mf else 0.0,
        drying=mf.drying if mf else 0.0,
        cooling=mf.cooling if mf else 0.0,
        rebound=mf.rebound if mf else 0.0,
        have_moisture=mf is not None,
        dry_share=fig.dry_share,
        u_decay=decay,
        u_mean=tau / _POINT_H * (1.0 - decay),
    )


def _vp(t: float, rh: float) -> float:
    return rh / 100.0 * 6.105 * math.exp(17.27 * t / (237.7 + t))


def _allowed_first(inp: PlanInputs, action: PlanAction, can_carry_on: bool = True) -> bool:
    """What step 0 may not do, from the state now [D016][D073]. Later steps
    are half an hour apart, longer than the shortest run and rest."""
    return _first_bar(inp, action, can_carry_on) is None


def _first_bar(inp: PlanInputs, action: PlanAction, can_carry_on: bool = True) -> str | None:
    """Why step 0 may not be this action, or None when it may [D016][D073].

    The reason is kept with the plan so a night can be read back: a room
    that went above its band while the plan rested needs to say whether it
    rested by choice or because the unit could not start yet.

    can_carry_on: the running kind is among this step's actions. Where it
    is not (heating a waking room [D085], a mode or rate no longer
    offered), the plan may rest: Limits' short-cycle guard still holds the
    stop and keeps the unit on its last running command [D016], and the
    plan never turns a heating room to cooling to stay running."""
    runs = _runs(action.kind)
    # turning a running unit the other way is a stop and a start: it rests
    # first, so a plan that reverses begins by resting [D016][D102]
    if _reverses(inp.compressor_running, inp.last_kind, action.kind):
        return "the unit would turn the other way: it rests first"
    if (
        runs
        and not inp.compressor_running
        and inp.stopped_since is not None
        and (inp.now - inp.stopped_since).total_seconds() < MIN_REST_S
    ):
        left = (MIN_REST_S - (inp.now - inp.stopped_since).total_seconds()) / 60.0
        return f"the unit stopped less than its minimum rest ago: {left:.0f} min left"
    # a stop within the minimum run is not allowed [D016]
    if (
        not runs
        and can_carry_on
        and inp.compressor_running
        and inp.last_kind in (StepKind.COOL, StepKind.HEAT, StepKind.DRY)
        and inp.running_since is not None
        and (inp.now - inp.running_since).total_seconds() < MIN_RUN_S
    ):
        left = (MIN_RUN_S - (inp.now - inp.running_since).total_seconds()) / 60.0
        return f"the unit has not run its minimum run: {left:.0f} min left"
    return None


def _reversal(last_way: int, kind: StepKind) -> bool:
    """A running kind the other way from the way the unit last ran [D102]."""
    way = _way(kind)
    return way != 0 and last_way != 0 and way != last_way


def _gap(index: float, band: BandPoint) -> float:
    """How far an index is outside its band, 0 when inside [D102]."""
    if not band.required:
        return 0.0
    return max(band.low - index, index - band.high, 0.0)


def _observed_gap_change(inp: PlanInputs, band: BandPoint) -> float | None:
    """How the room's outside-band gap changed over the last half hour, from
    what the filter actually read rather than what the model forecasts [D102].

    Negative means the room is heading back into its band. None when there
    are not yet six five-minute steps to compare.
    """
    steps = list(inp.heat.recent)[-6:]
    if len(steps) < 6:
        return None

    def index(step: FilterStep) -> float:
        reading = step.predicted_t + (step.innovation or 0.0)
        return reading + _E_COEFF * inp.e_now - _OFFSET

    return _gap(index(steps[-1]), band) - _gap(index(steps[0]), band)


def _rest_recovers(start: _Node, inp: PlanInputs, fx: _Fixed, w: float) -> bool:
    """Whether the room comes back inside its band with the unit resting,
    within REVERSAL_REST_STEPS half-hours [D102].

    A reversal waits for this rest forecast: an overshoot the room's own
    drift will undo is left to drift back, and the unit is turned the other
    way only when resting does not return the room to its band.

    A room that is inside its band now is never reversed: it has not left
    the band, so there is nothing to answer (Jason, 8 November 2026: the
    office at 27.0 in a 26.0-28.5 band was heated after a cooling run). A
    room that is outside but whose rest forecast narrows the gap is also
    left to rest, even when it does not reach the band inside the hour: it
    is heading back, which is what D102 asks (Jason, 9 November 2026:
    "if after a rest cycle it is not heading back into band then decide").
    """
    index_now = start.t + _E_COEFF * start.e - _OFFSET
    band_now = inp.bands[0]
    if _gap(index_now, band_now) == 0.0:
        return True
    observed = _observed_gap_change(inp, band_now)
    if observed is not None and observed < 0.0:
        # the readings themselves show the room heading back: rest, even if
        # the model's forecast cannot see the recovery [D102]
        return True
    node = start
    for j in range(REVERSAL_REST_STEPS):
        node = _extend(node, PlanAction(StepKind.REST, 0.0, None), j, inp, fx, w, 0.0)
    for j, pt in enumerate(node.points):
        band = inp.bands[j]
        if not band.required or band.low <= pt.hci <= band.high:
            return True
    last = node.points[-1]
    return _gap(last.hci, inp.bands[len(node.points) - 1]) < _gap(index_now, band_now)


def _opening_advice(inp: PlanInputs, start: _Node, best: _Node, fx: _Fixed, w: float,
                    draws: dict[float, float]) -> tuple[bool, str | None]:
    """FREE COOLING [D073][D105]: the plan with an opening open and the unit
    resting, against the plan itself, over the same window and by the same
    cost (comfort, energy and starts).

    Both are carried forward half an hour at a time and compared at every
    half-hour, so the window that suits the opening is the one that speaks.
    The opening must also move the room's index by more than the forecast's
    own uncertainty: a room with no opening sensors, or a shut room whose
    learned coupling is near zero, shows no difference and is never advised
    to open up [D105].
    """
    if inp.opening > 0.0:
        return False, "An opening is already open: the room is being ventilated."
    if not inp.outdoor_air_dry:
        return False, "The outdoor air is too damp to admit: its dew point is not below the room's."
    fx_open = _fixed(inp, opening=1.0)
    rest = PlanAction(StepKind.REST, 0.0, None)
    opened, shut, planned = start, start, start
    best_saving, longest = 0.0, 0
    for j in range(PLAN_STEPS):
        opened = _extend(opened, rest, j, inp, fx_open, w, 0.0)
        shut = _extend(shut, rest, j, inp, fx, w, 0.0)
        action = best.actions[j]
        planned = _extend(planned, action, j, inp, fx, w, draws[action.rate])
        at = j * PLAN_POINTS_PER_STEP + PLAN_POINTS_PER_STEP - 1
        # does the opening itself change the room, beyond the forecast's own
        # uncertainty? a sealed room does not, so it is never advised [D105]
        if shut.points[at].hci - opened.points[at].hci <= shut.points[at].sigma_hci:
            continue
        saving = planned.cost - opened.cost
        if saving > best_saving:
            best_saving, longest = saving, j + 1
    if longest == 0:
        return False, ("Opening up would not beat the plan over the next six hours, by the "
                       "plan's own measure.")
    return True, (
        f"Opening up and resting for {longest * PLAN_STEP_H * 60:.0f} minutes would cost "
        f"{best_saving:.2f} less than the plan over the same time, by the plan's own measure."
    )


def make_plan(inp: PlanInputs) -> Plan:
    """EVERY 5 MINUTES, per room: the best plan [Prop][Sim].

    Gives back the best plan's actions, its forecast at every 5-minute
    point, and its cost terms; for each other first action, the best plan
    that starts with it that survived the search, and why it lost [D006].
    """
    if len(inp.conditions) < PLAN_STEPS * PLAN_POINTS_PER_STEP or len(inp.bands) < len(inp.conditions):
        raise ValueError("the plan needs 72 points of conditions and bands")
    fx = _fixed(inp)
    u_min = inp.unit.lowest_rate(figures(inp.heat.z), 1)
    u_min_heat = inp.unit.lowest_rate(figures(inp.heat.z), -1)
    # the draw a rate takes is read on its own direction's curve: the sign
    # chooses it (unit.py effort_for_rate); a unit that only heats is
    # weighed on heating [D028]
    sign = -1.0 if "cool" not in inp.modes and "heat" in inp.modes else 1.0
    d_min = inp.draw_for_rate(sign * (u_min_heat if sign < 0 else u_min)) or DRAW_WITHOUT_LOAD_SENSOR_KW
    d_one = inp.draw_for_rate(sign * 1.0) or DRAW_WITHOUT_LOAD_SENSOR_KW
    # a tariff priced at or below zero throughout still values comfort and
    # starts: the price of no tariff, 1, stands in [Prop]
    p_ref = inp.p_max if inp.p_max > 0.0 else 1.0
    w = p_ref * d_min  # [I4][Der]
    c_start = PLAN_START_COST_SHARE * p_ref * d_one * 1.0  # [Prop][Sim][NYE]
    actions = candidate_actions(inp, u_min, u_min_heat)
    can_carry_on = any(a.kind is inp.last_kind for a in actions)
    draws = {a.rate: (inp.draw_for_rate(a.rate) or DRAW_WITHOUT_LOAD_SENSOR_KW) for a in actions}
    start = _Node(
        t=inp.t_now if inp.t_now is not None else inp.heat.z[I_T],
        m=inp.heat.z[I_M],
        # the forecast starts from the room the band rule measures: e_now is
        # the measured vapour pressure, the same one comfort.room_index uses.
        # The moisture filter's own estimate is not used here, because it can
        # sit below the reading (a short-cycling unit, a filter still settling)
        # and the plan would then read the room inside its band while the room
        # is above it, and rest [D106][D074][D055]
        e=inp.e_now if inp.e_now is not None else (inp.humidity.z[H_E] if inp.humidity is not None else 0.0),
        outside=0.0,
        comfort=0.0,
        energy=0.0,
        starts=0,
        running=inp.compressor_running,
        last_kind=inp.last_kind,
        rebound=inp.rebound_steps,
        c_start=c_start,
        u=inp.heat.u,
        way=inp.last_way,
    )
    # turn the unit the other way only when resting it would not bring the
    # room back into its band: an overshoot inside the band is left to the
    # room's own drift, never answered with a reversal [D102]. When the
    # unit's observed direction is unknown, the plan's last running kind is
    # the best evidence of the way it ran; otherwise a restart would drop
    # the guard exactly when it is needed.
    last_way = inp.last_way or _way(inp.last_kind)
    reversal_ok = not _rest_recovers(start, inp, fx, w)
    beam = [start]
    best_by_first: dict[PlanAction, _Node] = {}
    # why step 0 could not be each kind it was barred from: one line a kind,
    # so a night can say whether the plan rested by choice [D006]
    blocked: dict[StepKind, str] = {}
    for j in range(PLAN_STEPS):
        grown: list[_Node] = []
        # blinds act only while the sun is on the glass; "cannot say" counts as not [D023]
        sunny = any(c.sun_on_glass for c in inp.conditions[j * PLAN_POINTS_PER_STEP:(j + 1) * PLAN_POINTS_PER_STEP])
        for node in beam:
            for action in actions:
                if j == 0:
                    bar = _first_bar(inp, action, can_carry_on)
                    if bar is None and not reversal_ok and _reversal(last_way, action.kind):
                        bar = "resting brings the room back: the unit is not turned the other way"
                    if bar is not None:
                        blocked.setdefault(action.kind, bar)
                        continue
                if inp.blind_control and (action.blinds_closed is not None) != sunny:
                    continue
                grown.append(_extend(node, action, j, inp, fx, w, draws[action.rate]))
        grown.sort(key=lambda n: n.key)
        beam = grown[:PLAN_BEAM_WIDTH]
        for n in grown if j == 0 else beam:
            first = n.actions[0]
            held = best_by_first.get(first)
            if held is None or len(n.actions) > len(held.actions) or (
                len(n.actions) == len(held.actions) and n.key < held.key
            ):
                best_by_first[first] = n
    best = min(beam, key=lambda n: n.key)
    advised, advice = _opening_advice(inp, start, best, fx, w, draws)
    return _as_plan(inp.now, best, best_by_first, w, c_start, tuple(blocked.items()), advised, advice)


def _extend(
    node: _Node,
    action: PlanAction,
    j: int,
    inp: PlanInputs,
    fx: _Fixed,
    w: float,
    draw_kw: float,
) -> _Node:
    n = node.copy()
    runs = _runs(action.kind)
    if runs and (not n.running or (n.last_kind is not action.kind and _runs(n.last_kind))):
        # a change of running kind is a stop and a start: the unit stops its
        # compressor to change mode, so cool to dry costs one too. A running
        # unit whose kind is not known is carried on, not charged [D016][D103]
        n.starts += 1
    wet_before = n.last_kind in (StepKind.COOL, StepKind.DRY) and n.running
    wet = action.kind in (StepKind.COOL, StepKind.DRY)
    if wet_before and not wet:
        n.rebound = _REBOUND_STEPS
    n.running = runs
    n.last_kind = action.kind
    if runs:
        n.way = _way(action.kind)
    u_target = action.rate if runs else 0.0  # dry removes its share of what cooling does [D108]
    if action.kind is StepKind.DRY:
        # dry is offered at the cooling floor, the rate the unit is asked
        # for; the room only sees its learned share of it [D108]
        u_target *= fx.dry_share
    if action.blinds_closed is None:
        tau = inp.transmission_now
    else:
        tau = BLIND_CLOSED_TRANSMISSION if action.blinds_closed else 1.0
    p0, p1, p2_, p3 = fx.phi
    g0, g1 = fx.gam
    for k in range(PLAN_POINTS_PER_STEP):
        i = j * PLAN_POINTS_PER_STEP + k
        c = inp.conditions[i]
        s = tau * c.f_clear if c.sun_on_glass else 0.0  # S, the model's sun input (4.2)
        g = fx.g[i]
        u = u_target + (n.u - u_target) * fx.u_mean
        n.u = u_target + (n.u - u_target) * fx.u_decay
        t = p0 * n.t + p1 * n.m + g0[0] * c.outdoor + g0[1] * s + g0[2] * g + g0[3] * u
        m = p2_ * n.t + p3 * n.m + g1[0] * c.outdoor + g1[1] * s + g1[2] * g + g1[3] * u
        n.t, n.m = t, m
        if fx.have_moisture:
            removed = fx.drying if action.kind is StepKind.DRY else (
                fx.cooling * u if action.kind is StepKind.COOL else 0.0
            )
            gain = fx.rebound if n.rebound > 0 else 0.0
            e_out = _vp(c.outdoor, c.outdoor_rh) if c.outdoor_rh is not None else n.e
            target = (fx.h * e_out + fx.q - removed + gain) / fx.h
            n.e = target + (n.e - target) * math.exp(-fx.h * _POINT_H)
        if n.rebound > 0:
            n.rebound -= 1
        hci = t + _E_COEFF * n.e - _OFFSET  # the index, T + 0.33 e - 4.00 [D010]
        band = inp.bands[i]
        # e is carried as a vapour pressure, so the index moves one for one
        # with T here; the margin is held to half the band's half-width, so
        # a band always has an inside to aim for [Der][Set]
        sigma = min(PLAN_MARGIN_SIGMA * fx.sigma_t[i], 0.5 * band.half_width)
        outside = False
        if band.required:
            outside = hci > band.high - sigma or hci < band.low + sigma
            if outside:
                n.outside += 5.0
            n.comfort += w * ((hci - band.centre) / band.half_width) ** 2 * _POINT_H
        n.points.append(PlanPoint(c.time, t, hci, sigma, outside))
    if runs:
        n.energy += inp.prices[j] * draw_kw * PLAN_STEP_H
    n.actions.append(action)
    return n


def _as_plan(now: datetime, best: _Node, by_first: dict[PlanAction, _Node], w: float, c_start: float,
             blocked: tuple[tuple[StepKind, str], ...] = (), advised: bool = False,
             advice: str | None = None) -> Plan:
    alternatives: list[tuple[PlanAction, str]] = []
    for first, node in by_first.items():
        if first == best.actions[0]:
            continue
        if len(node.actions) < len(best.actions):
            why = f"dropped from the search after {len(node.actions)} half-hours"
        elif node.outside > best.outside:
            why = f"{node.outside - best.outside:.0f} more minutes outside the band"
        else:
            why = f"costs {node.cost - best.cost:.4f} more"
        alternatives.append((first, why))
    start_cost = best.starts * c_start
    return Plan(
        made_at=now,
        actions=tuple(best.actions),
        points=tuple(best.points),
        minutes_outside=best.outside,
        comfort_cost=best.comfort,
        energy_cost=best.energy,
        start_cost=start_cost,
        starts=best.starts,
        w=w,
        c_start=c_start,
        alternatives=tuple(alternatives),
        first_blocked=blocked,
        free_cooling_advised=advised,
        free_cooling_reason=advice,
    )


def path_now(plan: Plan, when: datetime, t_now: float) -> tuple[float, float]:
    """The plan's room temperature at a moment, and its slope in C/h, by
    straight lines between its 5-minute points, starting from the
    temperature when the plan was made."""
    times = [plan.made_at] + [p.time for p in plan.points]
    temps = [t_now] + [p.t for p in plan.points]
    if when <= times[0]:
        slope = (temps[1] - temps[0]) / _POINT_H
        return temps[0], slope
    for k in range(1, len(times)):
        if when <= times[k]:
            span = (times[k] - times[k - 1]).total_seconds()
            f = (when - times[k - 1]).total_seconds() / span
            slope = (temps[k] - temps[k - 1]) / (span / 3600.0)
            return temps[k - 1] + f * (temps[k] - temps[k - 1]), slope
    return temps[-1], 0.0


def curve_rates(heat: HeatFilter, direction: int) -> list[float]:
    """The learned curve's rates in one direction, C/h."""
    fig = figures(heat.z)
    return curve_values(fig.cool_steps if direction > 0 else fig.heat_steps)

