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
    SLEEP          sleep schedule and presence
    OCCUPIED       presence
    UNOCCUPIED     no presence

COAST is returned as the mode but carries base_mode, so the band that applies
is the one the displaced occupancy mode would have used. Without that, COAST
would have no band and the fallback out of COAST would have nothing to compare
against.
"""

from __future__ import annotations

from .hci import (
    SOLVE_MAX_C,
    SOLVE_MIN_C,
    ComfortBand,
    comfort_index,
    dry_bulb_for_index,
    radiant_load,
    sensitivity_to_humidity,
    sensitivity_to_temperature,
)
from .models import (
    BAND_MODES,
    ActuatorStep,
    DecisionTrace,
    Mode,
    RoomConfig,
    RoomInputs,
)
from .psychro import condensation_risk, dew_point_c, free_cooling
from .scheduling import ramped_band


def _occupancy_mode(inputs: RoomInputs, trace: DecisionTrace) -> Mode:
    """Resolve the presence-driven mode. Never returns LOCKOUT."""
    if inputs.presence is None:
        # Unknown presence is not absence. A failed sensor must not turn the
        # room off, so hold an occupied mode and say so. The sleep schedule
        # still applies: a dead presence sensor at 2am should not put the room
        # on the day band.
        if inputs.sleep_schedule_active:
            trace.reasons.append("presence unknown, holding sleep")
            return Mode.SLEEP
        trace.reasons.append("presence unknown, holding occupied")
        return Mode.OCCUPIED

    if not inputs.presence:
        trace.reasons.append("no presence")
        return Mode.UNOCCUPIED

    if inputs.sleep_schedule_active:
        trace.reasons.append("presence and sleep schedule active")
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

    Both routes are measured in hours to close the same excess:

        cooling   excess / (k_sensible  x  dHCI/dT)
        drying    excess / (k_latent    x  dHCI/dRH)

    `k_sensible` is degrees per hour and `k_latent` is humidity points per
    hour; the sensitivities convert each into index units per hour so the two
    are comparable. Nothing here is a threshold.
    """
    if inputs.temperature_c is None or inputs.relative_humidity is None:
        trace.rejected.append("dry: no reading to judge the load with")
        return None

    sensible_rate = inputs.k_sensible_c_per_hour
    latent_rate = inputs.k_latent_rh_per_hour

    if sensible_rate is None or latent_rate is None:
        # Not learned yet. The humidity threshold is a poor test and it is what
        # there is until the filter converges. Saying so keeps the fallback
        # visible rather than looking like a decision.
        if inputs.relative_humidity >= DRY_MODE_RH_FALLBACK:
            return (
                f"dry: {inputs.relative_humidity:.0f}% humidity and the model "
                "has not converged, falling back to the humidity threshold"
            )
        trace.rejected.append(
            f"dry: {inputs.relative_humidity:.0f}% is below the "
            f"{DRY_MODE_RH_FALLBACK:.0f}% fallback threshold, and the model has "
            "not converged enough to judge the load properly"
        )
        return None

    cooling_per_hour = sensible_rate * sensitivity_to_temperature(
        inputs.temperature_c, inputs.relative_humidity
    )
    drying_per_hour = latent_rate * sensitivity_to_humidity(inputs.temperature_c)

    if drying_per_hour <= 0:
        trace.rejected.append("dry: this room has never shown a latent response")
        return None

    if cooling_per_hour > drying_per_hour * DRY_MODE_ADVANTAGE:
        trace.rejected.append(
            f"dry: cooling closes {cooling_per_hour:.2f} HCI per hour against "
            f"{drying_per_hour:.2f} for drying — the load is sensible"
        )
        return None

    hours = overshoot / drying_per_hour if drying_per_hour else 0.0
    return (
        f"dry: drying closes {drying_per_hour:.2f} HCI per hour against "
        f"{cooling_per_hour:.2f} for cooling, about {hours * 60:.0f} min — "
        "the load is latent"
    )


def _covers_can_help(demand: str, position: float | None) -> bool:
    """Whether the covers still have travel left in the useful direction."""
    if position is None:
        # No position reported. Command them once and let the next evaluation
        # see the result, rather than assuming either way.
        return True
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
        return ActuatorStep.NONE

    if mode is Mode.UNOCCUPIED:
        # Not a wider envelope. Off. A heading-home request or a precool window
        # brings it back on; nothing else does.
        trace.rejected.append("all actuators: room unoccupied, air conditioning off")
        return ActuatorStep.NONE

    if inputs.opening_open:
        trace.rejected.append("all actuators: an opening in this room is open")
        return ActuatorStep.NONE

    if mode is Mode.COAST:
        trace.rejected.append("compressor: coasting, model predicts the band holds")
        return ActuatorStep.NONE

    if mode is Mode.PRECONDITION and not inputs.precondition_ready:
        # The request stands and the room is in PRECONDITION. The model simply
        # says the deadline is far enough out that starting now would cool an
        # empty house for hours to arrive at a moment it can reach from cold.
        trace.rejected.append(
            "all actuators: preconditioning, but the deadline is far enough "
            "out that the pull can wait"
        )
        return ActuatorStep.NONE

    if hci is None or band is None:
        # No reading, or no band configured for this mode. A room with no bands
        # never actuates: there is no default to fall back on, and inventing
        # one would be worse.
        trace.rejected.append("all actuators: no comfort reading or no band in force")
        return ActuatorStep.NONE

    demand = _demand_direction(mode, band, hci)
    trace.demand = demand
    if demand is None:
        trace.reasons.append("within band")
        return ActuatorStep.NONE

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

    if demand == "heat":
        # Fan and dry mode do not heat. Once covers are out, the compressor is
        # the only remaining step.
        trace.rejected.append("fan and dry: neither adds heat")
        if not inputs.can_heat:
            trace.rejected.append("compressor: this unit cannot heat")
            return ActuatorStep.NONE
        if not inputs.power_available:
            trace.rejected.append(
                "compressor: no grid import permitted, and battery/solar "
                "cannot cover this room's projected need"
            )
            return ActuatorStep.NONE
        trace.reasons.append("compressor: heating")
        return ActuatorStep.COMPRESSOR

    # --- 2. Fan. Air movement, no compressor. Worth trying only when the room
    # is marginally out of band.
    overshoot = hci - (band.low if mode is Mode.PRECOOL else band.high)
    if not inputs.can_fan_only:
        trace.rejected.append("fan: this unit has no fan-only mode")
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
    if not inputs.can_dry:
        trace.rejected.append("dry: this unit has no dry mode")
    else:
        route = _latent_route(inputs, overshoot, trace)
        if route:
            trace.reasons.append(route)
            return ActuatorStep.DRY

    # --- 4. Compressor. Everything cheaper has been ruled out above.
    if not inputs.can_cool:
        trace.rejected.append("compressor: this unit cannot cool")
        return ActuatorStep.NONE
    if not inputs.power_available:
        trace.rejected.append(
            "compressor: no grid import permitted, and battery/solar "
            "cannot cover this room's projected need"
        )
        return ActuatorStep.NONE
    trace.reasons.append("compressor: cooling")
    return ActuatorStep.COMPRESSOR


def evaluate_room(
    config: RoomConfig, inputs: RoomInputs
) -> DecisionTrace:
    """Full evaluation for one room. Always returns a trace."""
    trace = DecisionTrace(room_id=config.room_id, at=inputs.now, mode=Mode.LOCKOUT)

    mode, base = evaluate_mode(config, inputs, trace)
    trace.mode = mode
    trace.base_mode = base

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
    if inputs.previous_mode is not None and inputs.previous_mode is not mode:
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

    trace.actuator = select_actuator(mode, band, hci, inputs, trace)

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
