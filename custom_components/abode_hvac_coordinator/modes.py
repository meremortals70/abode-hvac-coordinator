"""Mode evaluation and actuator selection.

Pure. One function decides the mode, one decides the actuator step, and both
write their reasoning into the trace as they go. Nothing here talks to Home
Assistant, so the whole decision path is testable without a running instance.

PRECEDENCE — architecture proposal v0.3, section 4
--------------------------------------------------
    LOCKOUT        beats everything, always
    PRECONDITION   beats presence, which is the point of it. Entered by a
                   heading-home request, and driven to the occupied band
    PRECOOL        free/cheap window plus forecast heat ahead
    COAST          model says the band holds unaided, and the window permits it
    SLEEP          presence, and the sleep schedule or the room's Comfort
                   mode (DR-058)
    OCCUPIED       presence
    UNOCCUPIED     no presence

COAST is returned as the mode but carries base_mode, so the band that applies
is the one the displaced occupancy mode would have used. Without that, COAST
would have no band and the fallback out of COAST would have nothing to compare
against.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from .hci import (
    SOLVE_MAX_C,
    SOLVE_MIN_C,
    ComfortBand,
    comfort_index,
    dry_bulb_for_index,
    radiant_load,
    relative_humidity_at_constant_vapour_pressure,
)
from .models import (
    BAND_MODES,
    ActuatorStep,
    ComfortMode,
    DecisionTrace,
    Mode,
    RoomConfig,
    RoomInputs,
)
from .psychro import (
    condensation_risk,
    dew_point_c,
    free_cooling,
    weather_works_against_compressor,
)
from .scheduling import ramped_band


def sleeping(comfort_mode: ComfortMode, schedule_active: bool) -> bool:
    """Whether a room counts as sleeping right now (DR-058).

    The one answer to the question, used wherever it is asked: by the mode
    decision below, and by the coordinator wherever it picks a band to project
    the room against. Sleep and Awake are the user's choice and replace the
    schedule; Schedule leaves it to the schedule, as before 0.9.2.
    """
    if comfort_mode is ComfortMode.SLEEP:
        return True
    if comfort_mode is ComfortMode.AWAKE:
        return False
    return schedule_active


def _sleep_source(inputs: RoomInputs) -> str:
    """What made the room sleep, for the trace."""
    if inputs.comfort_mode is ComfortMode.SLEEP:
        return "comfort mode set to sleep"
    return "sleep schedule active"


def _occupancy_mode(inputs: RoomInputs, trace: DecisionTrace) -> Mode:
    """Resolve the presence-driven mode. Never returns LOCKOUT."""
    asleep = sleeping(inputs.comfort_mode, inputs.sleep_schedule_active)
    if (
        inputs.comfort_mode is ComfortMode.AWAKE
        and inputs.sleep_schedule_active
    ):
        trace.reasons.append("comfort mode set to awake over the sleep schedule")

    if inputs.presence is None:
        # Unknown presence is not absence. A failed sensor must not turn the
        # room off, so hold an occupied mode and say so. The sleep schedule
        # still applies: a dead presence sensor at 2am should not put the room
        # on the day band.
        if asleep:
            if inputs.comfort_mode is ComfortMode.SLEEP:
                trace.reasons.append(
                    "presence unknown, holding sleep, comfort mode set to sleep"
                )
            else:
                trace.reasons.append("presence unknown, holding sleep")
            return Mode.SLEEP
        trace.reasons.append("presence unknown, holding occupied")
        return Mode.OCCUPIED

    if not inputs.presence:
        trace.reasons.append("no presence")
        return Mode.UNOCCUPIED

    if asleep:
        trace.reasons.append(f"presence and {_sleep_source(inputs)}")
        return Mode.SLEEP

    trace.reasons.append("presence")
    return Mode.OCCUPIED


def evaluate_mode(
    config: RoomConfig, inputs: RoomInputs, trace: DecisionTrace
) -> tuple[Mode, Mode | None]:
    """Return (mode, base_mode). base_mode is set only when mode is COAST."""
    if config.lockout_reason is not None:
        trace.reasons.append(f"lockout: {config.lockout_reason}")
        return Mode.LOCKOUT, None

    if inputs.heading_home:
        trace.reasons.append("heading home, overrides presence")
        return Mode.PRECONDITION, None

    if inputs.left:
        # 0.9.7, DR-088. The owner said they have left. Nothing runs the room
        # - not presence, precool or a coast - until someone comes back.
        trace.reasons.append("left: the owner has left the room")
        return Mode.UNOCCUPIED, None

    base = _occupancy_mode(inputs, trace)

    # Precool banks thermal mass in the building against a load that is coming
    # later. Present occupancy is beside the point and must not gate it: the
    # free window is typically the middle of the day, when the room is empty,
    # and the load it is banking against arrives in the evening when the room
    # is not. Precooling an occupied room is the case that needs no
    # preparation, because the controller is already holding it.
    if inputs.precool_opportunity:
        if not inputs.forecast_demand_ahead:
            trace.rejected.append("precool: window open but no demand forecast ahead")
        else:
            trace.reasons.append("precool window declared and demand forecast ahead")
            return Mode.PRECOOL, None

    if inputs.predicted_to_hold is True and inputs.coasting_permitted:
        trace.reasons.append("thermal model predicts the band holds unaided")
        return Mode.COAST, base

    if inputs.predicted_to_hold is True and not inputs.coasting_permitted:
        trace.rejected.append(
            "coast: model says it would hold, but this window does not permit "
            "coasting"
        )
    elif inputs.predicted_to_hold is None:
        trace.rejected.append("coast: thermal model has not converged for this room")

    # 0.8.11, finding 12. The band may not hold indefinitely unaided, but it
    # may hold long enough for a strictly cheaper tariff interval to begin —
    # the cheapest way to deliver the same comfort outcome is to wait for
    # it, and this is only ever True when the model can positively say the
    # wait is safe (see coordinator._cheaper_window_imminent).
    if inputs.cheaper_window_imminent and inputs.coasting_permitted:
        trace.reasons.append(
            "a cheaper tariff window begins soon and the band holds until then"
        )
        return Mode.COAST, base
    if inputs.cheaper_window_imminent and not inputs.coasting_permitted:
        trace.rejected.append(
            "coast: a cheaper window is imminent, but this window does not "
            "permit coasting"
        )

    return base, None


def band_in_force(
    config: RoomConfig, mode: Mode, base_mode: Mode | None
) -> ComfortBand | None:
    """The band that applies to this mode.

    There is only ever one comfort definition per room, and it is the band.
    COAST follows back to the occupancy mode it displaced. PRECONDITION uses
    the occupied band: bringing a room back on means bringing it to comfort,
    and there is nothing else to drive toward.
    """
    if mode is Mode.COAST and base_mode is not None:
        return config.band_for(base_mode)
    if mode is Mode.PRECONDITION:
        return config.band_for(Mode.OCCUPIED)
    if mode in BAND_MODES:
        return config.band_for(mode)
    return None


#: How far above the band air movement alone is worth trying, in HCI. Beyond
#: this a fan is just noise. Fixed internal, not a setting.
FAN_MARGIN_HCI = 0.5


def _round_up_minutes(remaining: timedelta) -> int:
    """Whole minutes, rounded up, so the trace never says nought."""
    return max(1, -(-int(remaining.total_seconds()) // 60))


#: How long an opening may stay open before the unit is stopped for it, in
#: minutes, where the room has not set its own (0.9.0, DR-050).
#:
#: Stopping immediately would cost a compressor stop and then the five-minute
#: minimum off time for a door held open while someone carries washing
#: through. Waiting forever was the fault before 0.8.7: refusing to *command*
#: a room does not stop a unit that is already running, so a window left open
#: had the thermostat chasing a setpoint it could never reach, indefinitely.
#:
#: Until 0.9.0 this was a fixed two minutes and was not a setting. It is a
#: setting per room now, five minutes by default, because a door and a window
#: are not the same, and the unit stopped for a door with no notice at all.
#:
#: The interlock itself is not debounced. Nothing may actuate into an open
#: room from the moment the opening is seen; only the decision to stop a unit
#: that is already running waits.
DEFAULT_OPENING_GRACE_MINUTES = 5.0

#: How much longer than the compressor itself would take a room may be left to
#: come back into band on the weather's account, and the figure used where the
#: model cannot estimate the compressor's time. Judgements, not measurements
#: (0.9.0, DR-053).
UNAIDED_RETURN_MARGIN_MINUTES = 10.0
UNAIDED_RETURN_FALLBACK_MINUTES = 15.0

#: Fallback humidity threshold, used only while the thermal model has not
#: converged. Once `k_sensible` and `k_latent` are known the decision is made
#: on the merits and this number is not consulted.
#:
#: It is a poor test and that is the point of replacing it: 65% at 22 C and 65%
#: at 30 C are different loads, because a point of relative humidity moves the
#: comfort index by 0.087 at the first and 0.140 at the second.
DRY_MODE_RH_FALLBACK = 65.0

#: How much faster cooling must be than drying before it is preferred. Dry mode
#: runs the compressor at a lower duty for the same effect, so a tie goes to
#: drying; cooling has to actually win to be chosen.
DRY_MODE_ADVANTAGE = 1.25

#: 0.9.6, DR-076. Once the unit is drying or cooling, the other route has to
#: win by this much more before it changes, in percent of humidity on the
#: fallback threshold and as a factor on the learned comparison. On 13
#: October the living room sat at 64-65% and changed between cool and dry
#: every minute or two. Judgements, not measurements.
DRY_FALLBACK_HYSTERESIS_RH = 3.0
DRY_ROUTE_HYSTERESIS = 1.15

#: 0.9.6, DR-076. While the model has not converged, a room this far above its
#: band, in comfort index, is cooled whatever the humidity. The 65% fallback
#: is a poor test, and a room seven points above its band at 73% was put on
#: dry on 13 October. Judgement, not measurement.
DRY_FALLBACK_MAX_OVERSHOOT_HCI = 2.0

#: 0.9.6, DR-073. How long an occupied room waits, after outdoor air was
#: judged better than its own, before the compressor is started anyway: time
#: to open the windows. Judgement, not measurement.
FREE_COOLING_WAIT = timedelta(minutes=10)

#: 0.9.6, DR-072. Modes in which the unit is already working the room down
#: (or up). A room still outside its band is not switched from one of these
#: to its fan.
WORKING_MODES = frozenset({"cool", "dry", "heat", "heat_cool", "auto"})

#: 0.9.7, DR-090. How long a route, once chosen, is kept: drying or cooling
#: above the band, drying or the fan while coasting. On 15 October the living
#: room went dry, cool, dry, cool in three minutes, and thirteen times between
#: 05:15 and 05:38, because each command moved the next comparison.
ROUTE_HOLD = timedelta(minutes=20)

#: 0.9.7, DR-098. How close to its target, in comfort index, a room the unit
#: is working on must come before it may coast. The PID holds the room at its
#: target; coasting as soon as the room was anywhere inside its band stopped
#: the compressor at the band's edge, and the room was back above it within a
#: quarter of an hour.
COAST_AT_TARGET_HCI = 0.25

#: How near an extreme a cover counts as already there, in percent. A blind at
#: 3% is shut for our purposes; commanding it to 0 achieves nothing, and
#: without this check the ordering picks covers every cycle on an already-shut
#: room and never escalates to the next step.
COVER_TRAVEL_MARGIN = 5.0


def _demand_direction(mode: Mode, band: ComfortBand, hci: float) -> str | None:
    """Which way the room needs to move, or None if it is where it should be.

    PRECOOL is the exception: it is driving to the low bound, not to the
    middle, so it keeps cooling while the room is above that bound even though
    the room is technically inside the band.
    """
    if mode is Mode.PRECOOL:
        return "cool" if hci > band.low else None
    if hci > band.high:
        return "cool"
    if hci < band.low:
        return "heat"
    return None


def _latent_route(
    inputs: RoomInputs, overshoot: float, trace: DecisionTrace
) -> str | None:
    """Whether drying closes the comfort gap faster than cooling would.

    Returns the reason to dry, or None having recorded why not.

    Both routes are projected forward one hour and evaluated as index falls,
    rather than compared through a derivative. A derivative is a claim about
    the index formula that has to be re-derived by hand whenever the formula
    changes — this one already has, once. Evaluating `comfort_index` at each
    end is the same arithmetic, self-checking, and survives a term like wind
    or radiant being added later.

        cooling   comfort_index(T, RH) - comfort_index(T - k_sensible, RH')
        drying    comfort_index(T, RH) - comfort_index(T, RH - k_latent)

    `RH'` — what relative humidity reads after cooling — is the one place
    with a genuine unknown, and it has three states, all named in the trace:

    1. `k_rh_cooling` converged: the room's own measured response, signed and
       not decomposed. RH rises as dry bulb falls at roughly constant vapour
       pressure and falls as the coil condenses moisture; the net is what the
       room feels.
    2. Not converged: constant absolute humidity, crediting cooling with no
       condensation at all. This is a floor, not an answer — the "1.0
       derivative" the review pointed at, expressed without one.
    3. `k_sensible` or `k_latent` themselves unconverged: the review's
       original 65% relative-humidity threshold, unchanged. A model state,
       not a configuration state, and it survives temperature and humidity
       becoming required fields.
    """
    if inputs.temperature_c is None or inputs.relative_humidity is None:
        trace.rejected.append("dry: no reading to judge the load with")
        return None

    sensible_rate = inputs.k_sensible_c_per_hour
    latent_rate = inputs.k_latent_rh_per_hour

    if sensible_rate is None or latent_rate is None:
        # State 3. Not learned yet. The humidity threshold is a poor test and
        # it is what there is until the filter converges. Saying so keeps the
        # fallback visible rather than looking like a decision.
        #
        # 0.9.6, DR-076. A room well above its band is cooled, and the line
        # moves away from whichever route the unit is already on.
        if overshoot > DRY_FALLBACK_MAX_OVERSHOOT_HCI:
            trace.rejected.append(
                f"dry: {overshoot:.1f} HCI above band, too far for drying while "
                "the model has not converged"
            )
            return None
        threshold = DRY_MODE_RH_FALLBACK
        if inputs.unit_mode == "dry":
            threshold -= DRY_FALLBACK_HYSTERESIS_RH
        elif inputs.unit_mode in WORKING_MODES:
            threshold += DRY_FALLBACK_HYSTERESIS_RH
        if inputs.relative_humidity >= threshold:
            return (
                f"dry: {inputs.relative_humidity:.0f}% humidity and the model "
                "has not converged, falling back to the humidity threshold "
                f"({threshold:.0f}%)"
            )
        trace.rejected.append(
            f"dry: {inputs.relative_humidity:.0f}% is below the "
            f"{threshold:.0f}% fallback threshold, and the model has "
            "not converged enough to judge the load properly"
        )
        return None

    temp_c = inputs.temperature_c
    relative_humidity = inputs.relative_humidity
    cooled_temp_c = temp_c - sensible_rate

    if inputs.k_rh_cooling_per_hour is not None:
        # State 1.
        cooled_rh = relative_humidity + inputs.k_rh_cooling_per_hour
        rh_state = "measured humidity response"
    else:
        # State 2.
        cooled_rh = relative_humidity_at_constant_vapour_pressure(
            temp_c, relative_humidity, cooled_temp_c
        )
        rh_state = "condensation not yet measured, assuming none"
    cooled_rh = max(0.0, min(100.0, cooled_rh))
    dried_rh = max(0.0, relative_humidity - latent_rate)

    start_index = comfort_index(temp_c, relative_humidity)
    cooling_per_hour = start_index - comfort_index(cooled_temp_c, cooled_rh)
    drying_per_hour = start_index - comfort_index(temp_c, dried_rh)

    if drying_per_hour <= 0:
        trace.rejected.append("dry: this room has never shown a latent response")
        return None

    advantage = DRY_MODE_ADVANTAGE
    if inputs.unit_mode == "dry":
        advantage *= DRY_ROUTE_HYSTERESIS
    elif inputs.unit_mode in WORKING_MODES:
        advantage /= DRY_ROUTE_HYSTERESIS
    if cooling_per_hour > drying_per_hour * advantage:
        trace.rejected.append(
            f"dry: cooling closes {cooling_per_hour:.2f} HCI per hour "
            f"({rh_state}) against {drying_per_hour:.2f} for drying — the "
            "load is sensible"
        )
        return None

    hours = overshoot / drying_per_hour if drying_per_hour else 0.0
    return (
        f"dry: drying closes {drying_per_hour:.2f} HCI per hour against "
        f"{cooling_per_hour:.2f} for cooling ({rh_state}), about "
        f"{hours * 60:.0f} min — the load is latent"
    )


def _route_kept(inputs: RoomInputs, routes: tuple[str, ...]) -> str | None:
    """The route the unit is on, if it is one of `routes` and still held.

    0.9.7, DR-090. A route chosen less than `ROUTE_HOLD` ago is kept. None
    when the unit is on none of `routes`, or has been for longer, or how
    long is not known.
    """
    if inputs.unit_mode not in routes or inputs.unit_mode_minutes is None:
        return None
    if inputs.unit_mode_minutes >= ROUTE_HOLD.total_seconds() / 60.0:
        return None
    return inputs.unit_mode


def _covers_can_help(demand: str, position: float | None) -> bool:
    """Whether the covers still have travel left in the useful direction.

    **Unknown position means no.** This previously returned True, on the
    reasoning that the covers should be commanded once and the next
    evaluation would see the result. The next evaluation sees the same
    unknown, so a room whose covers never report `current_position` chose
    COVERS on every cycle — and choosing COVERS turns the climate entity off
    for that cycle. A west-facing room with position-less blinds therefore
    had its air conditioning held off from the moment the sun reached the
    glass until it left, with nothing in the trace to say why.

    Refusing is the safe direction: the ladder falls through to the fan, dry
    and compressor steps, and the rejection names the missing reading.
    """
    if position is None:
        return False
    if demand == "cool":
        return position > COVER_TRAVEL_MARGIN
    return position < (100.0 - COVER_TRAVEL_MARGIN)


def select_actuator(
    mode: Mode,
    band: ComfortBand | None,
    hci: float | None,
    inputs: RoomInputs,
    trace: DecisionTrace,
) -> ActuatorStep:
    """Cheapest first: covers, then fan, then dry, then compressor.

    Nothing reaches the compressor until everything above it has been ruled
    out, and every rule-out is written into the trace. Architecture proposal
    v0.3, section 6.
    """
    if mode is Mode.LOCKOUT:
        trace.rejected.append("all actuators: room is in lockout")
        return ActuatorStep.OFF

    if mode is Mode.UNOCCUPIED:
        # Not a wider envelope. Off. A heading-home request or a precool window
        # brings it back on; nothing else does.
        trace.rejected.append("all actuators: room unoccupied, air conditioning off")
        return ActuatorStep.OFF

    if inputs.opening_open:
        # The interlock exists to stop the room being conditioned while it is
        # open to outside. Refusing to *start* is not the same thing: a unit
        # already running keeps running, the thermostat never reaches a
        # setpoint it cannot reach, and nothing bounds it.
        #
        # But a door open for twenty seconds is not a window left open, and
        # stopping for it costs a compressor stop plus the minimum off time.
        # So nothing new is actuated either way, and the stop waits for the
        # room's own grace (DR-050).
        #
        # An opening whose age cannot be read holds the unit. An unknown age
        # is not an old one, and a sensor that cannot be read is not evidence
        # the door has been open long.
        if inputs.free_cooling_advised:
            # 0.9.6, DR-073. Outdoors is better than in and the room has been
            # opened up: that is what was advised, so the unit is stopped
            # without waiting out the grace.
            trace.rejected.append(
                "all actuators: an opening is open and outdoors is better than "
                "in, so the air conditioning is off"
            )
            return ActuatorStep.OFF
        grace = timedelta(minutes=inputs.opening_grace_minutes)
        since = inputs.opening_open_since
        if since is None:
            trace.rejected.append(
                "all actuators: an opening in this room is open and how long "
                "it has been open is not known, holding the unit as it is"
            )
            return ActuatorStep.NONE
        if inputs.now - since < grace:
            remaining = grace - (inputs.now - since)
            trace.rejected.append(
                "all actuators: an opening in this room is open, holding the "
                f"unit as it is for another {_round_up_minutes(remaining)} "
                "minute(s) in case it closes"
            )
            return ActuatorStep.NONE
        trace.rejected.append("all actuators: an opening in this room is open")
        return ActuatorStep.OFF

    if mode is Mode.COAST:
        # Coasting means the compressor is not running and the room is left to
        # the weather. It does not mean the unit is off (DR-057): the unit
        # stays on its fan at the quietest speed so the room's air is kept
        # mixed, and off is kept for the stops that mean off. Leaving the unit
        # cooling would have the thermostat hold the band by running, which is
        # the opposite of coasting.
        return coast_step(inputs, trace, band, hci)

    if mode is Mode.PRECONDITION and not inputs.precondition_ready:
        # The request stands and the room is in PRECONDITION. The model simply
        # says the deadline is far enough out that starting now would cool an
        # empty house for hours to arrive at a moment it can reach from cold.
        trace.rejected.append(
            "all actuators: preconditioning, but the deadline is far enough "
            "out that the pull can wait"
        )
        return ActuatorStep.OFF

    if hci is None or band is None:
        # NONE, not OFF. A room with a dead sensor keeps its air conditioning:
        # comfort is a hard constraint and a reading the controller cannot
        # take is not grounds for withdrawing it. Stopping here would be a
        # guard failing closed against comfort, which is the fault finding 3
        # was spent reversing.
        if hci is None:
            # Named entity, not a generic "no comfort reading". Temperature
            # and humidity are required at setup from 0.8.9, so a room
            # reaching this line is either mid-outage or was configured
            # before the requirement existed — either way this is a fault to
            # go looking at, not a configuration state to shrug off.
            missing = [
                entity_id or kind
                for kind, value, entity_id in (
                    ("temperature sensor", inputs.temperature_c, inputs.temperature_entity_id),
                    ("humidity sensor", inputs.relative_humidity, inputs.humidity_entity_id),
                )
                if value is None
            ]
            trace.rejected.append(
                "all actuators: not reporting — " + ", ".join(missing)
            )
        else:
            trace.rejected.append("all actuators: no band configured for this mode")
        return ActuatorStep.NONE

    demand = _demand_direction(mode, band, hci)
    trace.demand = demand
    if demand is None:
        trace.reasons.append("within band")
        return _in_band_step(inputs, trace, band, hci)

    # --- 1. Covers. Free, and they work in both directions: block gain when
    # the room is too warm, admit it when the room is too cold.
    #
    # The gate is whether the sun is on this room's windows, which is geometry.
    # Indoor light level cannot answer it: a semi-transparent blind reads
    # bright when it is fully closed, so lux would say there is nothing to
    # block at exactly the moment the blind is already blocking.
    if not inputs.has_covers:
        trace.rejected.append("covers: none configured for this room")
    elif not inputs.allow_cover_control:
        trace.rejected.append("covers: control disabled for this room")
    elif inputs.direct_sun is None:
        trace.rejected.append("covers: cannot tell whether the sun is on this room")
    elif not inputs.direct_sun:
        trace.rejected.append("covers: no sun on this room to act on")
    elif inputs.cover_position is None:
        # Distinct from "already where they need to be": nothing is known
        # about where they are, so there is no way to tell whether commanding
        # them would achieve anything. Named separately so a room silently
        # skipping its free step is diagnosable from the trace alone.
        trace.rejected.append(
            "covers: no cover in this room reports its position"
        )
    elif not _covers_can_help(demand, inputs.cover_position):
        # Already where they need to be. Saying so is what lets the ordering
        # move on to the next step instead of choosing covers forever.
        trace.rejected.append(
            "covers: already closed against the gain"
            if demand == "cool"
            else "covers: already open to the gain"
        )
    else:
        trace.reasons.append(
            "covers: blocking solar gain"
            if demand == "cool"
            else "covers: admitting solar gain"
        )
        return ActuatorStep.COVERS

    if demand == "heat" and inputs.waking:
        # 0.9.7, DR-085. A room that has come out of Sleep is below its
        # daytime band because it was cooled for sleep. It is not heated up
        # to it: on 15 October the living room's unit went to heat at 24 C at
        # 05:12 and woke the house. It warms by itself.
        #
        # DR-091. Nor is it left cooling toward the Sleep setpoint: a room
        # below its band is not cooled further. The unit goes to its fan.
        trace.rejected.append(
            "compressor: not heating a room on its way out of Sleep; it warms "
            "by itself"
        )
        if inputs.unit_mode in (None, "off", "fan_only"):
            return ActuatorStep.NONE
        return _fan_or_off(inputs, trace, "below band on its way out of Sleep")

    if demand == "heat":
        # Fan and dry mode do not heat. Once covers are out, the compressor is
        # the only remaining step.
        trace.rejected.append("fan and dry: neither adds heat")
        if not inputs.can_heat:
            trace.rejected.append("compressor: this unit cannot heat")
            return ActuatorStep.OFF
        trace.reasons.append("compressor: heating")
        return ActuatorStep.COMPRESSOR

    # --- 2. Fan. Air movement, no compressor. Worth trying only when the room
    # is marginally out of band.
    overshoot = hci - (band.low if mode is Mode.PRECOOL else band.high)
    if not inputs.can_fan_only:
        trace.rejected.append("fan: this unit has no fan-only mode")
    elif inputs.unit_mode in WORKING_MODES:
        # 0.9.6, DR-072. The unit is already bringing the room down. Moving it
        # to its fan short of the band stopped the compressor, the minimum off
        # time held it there while the room climbed back, and the living room
        # went round that about ten times on 13-14 October without reaching
        # its band once.
        trace.rejected.append(
            f"fan: the unit is already {inputs.unit_mode}, and the room is "
            f"still {overshoot:.1f} HCI above band"
        )
    elif overshoot <= FAN_MARGIN_HCI:
        trace.reasons.append("fan: marginally above band, air movement should carry it")
        return ActuatorStep.FAN
    else:
        trace.rejected.append(
            f"fan: {overshoot:.1f} HCI above band, beyond what air movement carries"
        )

    # --- 3. Dry mode. A latent-dominated load costs far less to shift with dry
    # mode on a low fan than with cooling.
    #
    # Which route is faster is answered from what the room has actually taught
    # the model, not from a humidity number. The comfort excess is the same
    # either way; what differs is how fast each actuator closes it, and that is
    # the learned rate multiplied by how much the index moves per unit of what
    # that actuator changes.
    kept = _route_kept(inputs, ("dry", "cool"))
    if not inputs.can_dry:
        trace.rejected.append("dry: this unit has no dry mode")
    elif kept == "dry":
        trace.reasons.append(
            f"dry: kept, chosen {inputs.unit_mode_minutes:.0f} min ago and held "
            f"for {ROUTE_HOLD.seconds // 60} min"
        )
        return ActuatorStep.DRY
    elif kept == "cool":
        trace.rejected.append(
            f"dry: cooling was chosen {inputs.unit_mode_minutes:.0f} min ago and "
            f"is held for {ROUTE_HOLD.seconds // 60} min"
        )
    else:
        route = _latent_route(inputs, overshoot, trace)
        if route:
            trace.reasons.append(route)
            return ActuatorStep.DRY

    # --- 4. Compressor. Everything cheaper has been ruled out above.
    if not inputs.can_cool:
        trace.rejected.append("compressor: this unit cannot cool")
        return ActuatorStep.OFF
    trace.reasons.append("compressor: cooling")
    return ActuatorStep.COMPRESSOR


def _fan_or_off(inputs: RoomInputs, trace: DecisionTrace, why: str) -> ActuatorStep:
    """The unit to its fan where it has one, otherwise off."""
    if inputs.can_fan_only:
        trace.reasons.append(f"fan: {why}, the unit goes to its fan")
        return ActuatorStep.FAN
    trace.reasons.append(f"off: {why}, and this unit has no fan-only mode")
    return ActuatorStep.OFF


def _in_band_step(
    inputs: RoomInputs, trace: DecisionTrace, band: ComfortBand, hci: float
) -> ActuatorStep:
    """What a room inside its band asks of its unit.

    Normally nothing: the unit keeps its mode and the room loop's setpoint is
    walked onto it (DR-056).

    0.9.7, DR-091. Not a mode whose reason has passed. A unit heating a room
    already at or above the middle of its band goes to its fan; in a
    simulated morning the living room was heated for two hours because
    "nothing" kept it in heat. A unit left
    drying once the room no longer calls for it goes back to cooling at the
    room loop's setpoint; on a humid evening it dried for five hours at 51%.
    """
    if inputs.unit_mode == "heat" and hci >= band.midpoint:
        return _fan_or_off(inputs, trace, "in band and warm enough")
    if (
        inputs.unit_mode == "dry"
        and inputs.can_cool
        and _route_kept(inputs, ("dry",)) is None
    ):
        scratch = DecisionTrace(room_id=trace.room_id, at=trace.at, mode=trace.mode)
        if _latent_route(inputs, 0.0, scratch) is None:
            trace.reasons.append(
                "dry: no longer called for in band; back to cooling at the "
                "room's setpoint"
            )
            return ActuatorStep.COMPRESSOR
    trace.hold_setpoint = True
    return ActuatorStep.NONE


def coast_step(
    inputs: RoomInputs,
    trace: DecisionTrace,
    band: ComfortBand | None = None,
    hci: float | None = None,
) -> ActuatorStep:
    """What a coasting room's unit is asked to do (DR-057, DR-059).

    Coasting stops cooling the room's air. What the unit is left in depends
    on the room's humidity (DR-059):

    - **High humidity: dry.** Leaving the unit on its fan blows over a wet
      coil and puts the water it has just taken out back into the room; in
      the living room the dew point rose 2.5 C in a quarter of an hour, every
      time. "High" is decided by the same test the actuator ladder uses to
      choose drying over cooling (`_latent_route`): the room's learned rates
      once they have converged, and the 65% threshold until then. Going from
      cooling to dry is not a compressor stop, so the short-cycle guard has
      nothing to refuse.
    - **Otherwise: fan only** where the unit offers it, so the room's air
      keeps moving (DR-057).
    - **No fan-only mode: off.**

    A power constraint can still turn a coasting unit off; that is decided
    with the budget in the coordinator (DR-063), not here.
    """
    kept = _route_kept(inputs, ("dry", "fan_only"))
    if kept == "dry" and inputs.can_dry:
        trace.reasons.append(
            f"dry: kept while coasting, chosen {inputs.unit_mode_minutes:.0f} "
            f"min ago and held for {ROUTE_HOLD.seconds // 60} min"
        )
        return ActuatorStep.DRY
    if kept == "fan_only" and inputs.can_fan_only:
        trace.rejected.append(
            f"compressor: coasting on the fan, chosen {inputs.unit_mode_minutes:.0f} "
            f"min ago and held for {ROUTE_HOLD.seconds // 60} min"
        )
        return ActuatorStep.FAN
    if inputs.unit_mode == "heat":
        # 0.9.7, DR-093. After heating the coil is warm and dry: there is no
        # water to blow back into the room, so there is no case for drying.
        # On 15 October the living room was coasted from heat into dry.
        trace.rejected.append("dry: the unit has been heating, its coil is dry")
    elif inputs.can_dry and band is not None and hci is not None:
        overshoot = max(0.0, hci - band.high)
        # Judged at the humidity the room will reach once the coil has given
        # its water back, where the room has learned that (DR-060): stopping
        # the compressor is what raises it, so the reading now understates it.
        judged = inputs
        if (
            inputs.rebound_relative_humidity is not None
            and inputs.relative_humidity is not None
            and inputs.rebound_relative_humidity > inputs.relative_humidity
        ):
            judged = replace(
                inputs, relative_humidity=inputs.rebound_relative_humidity
            )
        scratch = DecisionTrace(room_id=trace.room_id, at=trace.at, mode=trace.mode)
        route = _latent_route(judged, overshoot, scratch)
        for note in scratch.rejected:
            if note not in trace.rejected:
                trace.rejected.append(note)
        if route:
            trace.rejected.append(
                "compressor: coasting, the room's temperature will hold; "
                "humidity is high, so the unit dries rather than blowing over "
                "a wet coil"
            )
            trace.reasons.append(route)
            return ActuatorStep.DRY
    if inputs.can_fan_only:
        trace.rejected.append(
            "compressor: coasting, model predicts the band holds; the unit stays on its fan"
        )
        return ActuatorStep.FAN
    trace.rejected.append(
        "compressor: coasting, model predicts the band holds; this unit has no "
        "fan-only mode, so it is off"
    )
    return ActuatorStep.OFF


def _weather_coast(
    mode: Mode,
    band: ComfortBand | None,
    inputs: RoomInputs,
    trace: DecisionTrace,
) -> str | None:
    """A reason to let the weather do the work instead of the compressor.

    0.9.0, DR-053. Applies only where the compressor has been chosen for a
    room in a normal occupied or sleeping mode: PRECOOL is banking on purpose
    and PRECONDITION has a deadline. Returns the reason to coast, or None.

    With a projection, the question is whether the room's own physics - the
    learned loss and solar terms, the forecast, the sun on the window - bring
    it back into band, for good, within the return limit. Without one, the
    plain outdoor comparison is the floor: no heating while outdoors feels at
    least as warm as the band's lower bound, no cooling while it feels no
    warmer than the upper bound unless the sun is on the glass. A missing
    outdoor reading means no coast at all, never a guess.
    """
    if mode not in (Mode.OCCUPIED, Mode.SLEEP) or band is None:
        return None
    demand = trace.demand
    if demand not in ("cool", "heat"):
        return None

    if inputs.unaided_refused is not None and demand == "cool":
        # DR-060. Not the floor comparison either: the floor ignores the
        # moisture the coil gives back just the same. Cooling only: a room
        # that needs heat is not waiting on moisture from a cold coil.
        trace.rejected.append(f"coast: {inputs.unaided_refused}")
        return None

    if inputs.unaided_available:
        trace.unaided_return_limit_minutes = inputs.unaided_return_limit_minutes
        if inputs.unaided_return_minutes is None:
            trace.rejected.append(
                "coast: the model does not bring the room back into band "
                "unaided, so the compressor works"
            )
            return None
        trace.unaided_return_minutes = inputs.unaided_return_minutes
        if inputs.unaided_return_minutes <= inputs.unaided_return_limit_minutes:
            return (
                "coast: the weather brings the room back into band in about "
                f"{inputs.unaided_return_minutes:.0f} min unaided "
                f"(limit {inputs.unaided_return_limit_minutes:.0f} min)"
            )
        trace.rejected.append(
            "coast: the room would be back in band unaided in about "
            f"{inputs.unaided_return_minutes:.0f} min, longer than the "
            f"{inputs.unaided_return_limit_minutes:.0f} min allowed"
        )
        return None

    return weather_works_against_compressor(
        demand,
        band_low=band.low,
        band_high=band.high,
        outdoor_c=inputs.outdoor_c,
        outdoor_rh=inputs.outdoor_relative_humidity,
        outdoor_wind_ms=inputs.outdoor_wind_ms,
        direct_sun=inputs.direct_sun,
    )


def evaluate_room(
    config: RoomConfig, inputs: RoomInputs
) -> DecisionTrace:
    """Full evaluation for one room. Always returns a trace."""
    trace = DecisionTrace(room_id=config.room_id, at=inputs.now, mode=Mode.LOCKOUT)

    mode, base = evaluate_mode(config, inputs, trace)
    trace.mode = mode
    trace.base_mode = base
    trace.room_c = inputs.temperature_c
    trace.comfort_mode = inputs.comfort_mode.value

    # Sun through glass, still air and equipment heat all change how hot a
    # person is without moving the air temperature much. A wall sensor cannot
    # see any of them, which is why the index carries them explicitly.
    radiant = radiant_load(
        direct_sun=inputs.direct_sun,
        cover_position=inputs.cover_position,
        has_covers=inputs.has_covers,
    )
    trace.radiant_fraction = radiant
    still_air = not inputs.air_moving

    hci: float | None = None
    if inputs.temperature_c is not None and inputs.relative_humidity is not None:
        trace.hci_base = comfort_index(
            inputs.temperature_c, inputs.relative_humidity
        )
        hci = comfort_index(
            inputs.temperature_c,
            inputs.relative_humidity,
            radiant=radiant,
            still_air=still_air,
            heat_load=inputs.heat_load,
        )
        trace.hci = hci
        if hci - trace.hci_base >= 0.5:
            trace.reasons.append(
                f"index raised {hci - trace.hci_base:.1f} by "
                + ", ".join(
                    filter(
                        None,
                        (
                            f"sun ({radiant:.0%} through)" if radiant > 0 else "",
                            "still air" if still_air else "",
                            "heat load in the room" if inputs.heat_load else "",
                        ),
                    )
                )
            )
    else:
        trace.rejected.append("comfort index: temperature or humidity unavailable")

    band = band_in_force(config, mode, base)
    band_mode = base if mode is Mode.COAST and base is not None else mode
    if inputs.previous_mode is not None and inputs.previous_mode is not band_mode:
        band, ramp_reason = ramped_band(
            from_band=band_in_force(config, inputs.previous_mode, None),
            to_band=band,
            changed_at=inputs.mode_changed_at,
            now=inputs.now,
        )
        if ramp_reason:
            trace.reasons.append(ramp_reason)
    if band is not None:
        trace.band_low = band.low
        trace.band_high = band.high
        if hci is not None:
            trace.band_position = band.position(hci)

    # The dry bulb target the AC is actually asked for. The user never sets
    # this: it is derived from the band and the measured humidity, so a humid
    # night produces a different setpoint for the same felt comfort.
    if band is not None and inputs.relative_humidity is not None:
        target_hci = band.low if mode is Mode.PRECOOL else band.midpoint
        # The setpoint must be solved under the same conditions the index was
        # measured under: a sunlit room needs colder air to feel the same.
        target_c = dry_bulb_for_index(
            target_hci,
            inputs.relative_humidity,
            radiant=radiant,
            still_air=still_air,
            heat_load=inputs.heat_load,
        )
        if target_c in (SOLVE_MIN_C, SOLVE_MAX_C):
            # The band and the measured humidity together imply a setpoint
            # outside anything worth asking of the hardware. Clamped, and said
            # so, rather than passed through.
            trace.rejected.append(
                f"target: HCI {target_hci:.1f} at {inputs.relative_humidity:.0f}% "
                f"implies a setpoint outside {SOLVE_MIN_C:.0f}-{SOLVE_MAX_C:.0f} C, "
                f"clamped to {target_c:.0f} C"
            )
        trace.target_dry_bulb_c = target_c

    if (
        mode is Mode.COAST
        and base is not None
        and band is not None
        and hci is not None
        and inputs.unit_mode in WORKING_MODES
    ):
        # 0.9.7, DR-098. A room the unit is working on coasts only once it has
        # reached its target. Until then the room loop keeps the unit working
        # toward it; the band's edge is not the target.
        target_hci = band.low if base is Mode.PRECOOL else band.midpoint
        heating = inputs.unit_mode == "heat"
        short = (
            target_hci - hci if heating else hci - target_hci
        ) - COAST_AT_TARGET_HCI
        if short > 0:
            trace.rejected.append(
                f"coast: not yet, the unit keeps working until the room reaches "
                f"its target (index {hci:.1f} against {target_hci:.1f})"
            )
            mode = base
            trace.mode = base
            trace.base_mode = None

    trace.actuator = select_actuator(mode, band, hci, inputs, trace)

    # The weather may bring the room back by itself. Where it will, the
    # compressor is not started for it: the room coasts. Only the compressor
    # step is replaced - covers and fan were already tried first.
    if trace.actuator is ActuatorStep.COMPRESSOR:
        coast_reason = _weather_coast(mode, band, inputs, trace)
        if coast_reason is not None:
            trace.reasons.append(coast_reason)
            trace.base_mode = mode
            trace.mode = Mode.COAST
            trace.actuator = coast_step(inputs, trace, band, hci)

    # The felt comparison uses the room's own comfort index, corrections and
    # all. A sunlit room feels hotter than its air temperature, which makes
    # opening up more attractive, and the index is where that already lives.
    advice = free_cooling(
        indoor_hci=hci,
        indoor_c=inputs.temperature_c,
        indoor_rh=inputs.relative_humidity,
        outdoor_c=inputs.outdoor_c,
        outdoor_rh=inputs.outdoor_relative_humidity,
        outdoor_wind_ms=inputs.outdoor_wind_ms,
        demand=trace.demand,
    )
    trace.free_cooling_advised = advice.advised
    if (
        advice.advised
        and mode is Mode.OCCUPIED
        and trace.actuator in (ActuatorStep.COMPRESSOR, ActuatorStep.DRY)
        and inputs.unit_mode not in WORKING_MODES
        and not inputs.free_cooling_wait_over
    ):
        # 0.9.6, DR-073. Outdoors is better than in. The compressor is not
        # started while the windows may be opened; on 14 October the Office
        # was started and told to open up in the same second. After
        # `FREE_COOLING_WAIT` it starts anyway: comfort is the constraint.
        trace.actuator = ActuatorStep.NONE
        trace.hold_setpoint = False
        trace.rejected.append(
            "compressor: not started, outdoors is cooler and drier; opening up "
            f"would help, and the unit starts after {FREE_COOLING_WAIT.seconds // 60} "
            "minutes if the room is not opened"
        )
    trace.dew_point_c = advice.indoor_dew_point_c
    trace.outdoor_dew_point_c = advice.outdoor_dew_point_c
    trace.outdoor_apparent_c = advice.outdoor_apparent_c
    if advice.advised:
        trace.reasons.append(advice.reason)
    elif advice.outdoor_dew_point_c is not None:
        trace.rejected.append(advice.reason)

    risk = condensation_risk(
        indoor_c=inputs.temperature_c,
        indoor_rh=inputs.relative_humidity,
        setpoint_c=trace.target_dry_bulb_c,
    )
    trace.condensation_risk = risk.at_risk
    if trace.dew_point_c is None:
        trace.dew_point_c = risk.dew_point_c
    if risk.reason:
        trace.reasons.append(risk.reason)

    # The dew point is a property of the air in the room, not of any decision
    # about it. Both paths above can decline to compute it — free cooling when
    # the room is not asking to be cooled, condensation when there is no
    # setpoint to compare against — which left an unoccupied room publishing
    # nothing despite having both readings.
    #
    # That is exactly backwards: a shut-up room nobody is in is where mould
    # grows. If the readings exist, the dew point is published.
    if (
        trace.dew_point_c is None
        and inputs.temperature_c is not None
        and inputs.relative_humidity is not None
    ):
        trace.dew_point_c = round(
            dew_point_c(inputs.temperature_c, inputs.relative_humidity), 1
        )

    return trace
