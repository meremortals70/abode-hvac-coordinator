"""Data model for the coordinator.

Pure. No Home Assistant imports, so every one of these can be built and
inspected in a plain Python session or a unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from .grace import GraceSettings
from .hci import ComfortBand


class Mode(StrEnum):
    """Room modes. Mutually exclusive. Architecture proposal v0.3, section 4."""

    LOCKOUT = "lockout"
    UNOCCUPIED = "unoccupied"
    OCCUPIED = "occupied"
    SLEEP = "sleep"
    PRECONDITION = "precondition"
    PRECOOL = "precool"
    COAST = "coast"


#: Modes that carry a comfort band of their own.
#:
#: UNOCCUPIED is not among them. An unoccupied room is off, not held to a wider
#: envelope, and the only thing that brings it back on is a heading-home
#: request. COAST inherits the band of the occupancy mode it displaced;
#: PRECONDITION drives to an explicitly supplied target.
BAND_MODES: Final = (Mode.OCCUPIED, Mode.SLEEP, Mode.PRECOOL)


class ActuatorStep(StrEnum):
    """Cheapest first. Architecture proposal v0.3, section 6.

    Nothing may reach for a step until every step above it is exhausted.

    NONE and OFF are both "no actuator is being used", and they are not the
    same instruction. NONE leaves the unit exactly as it was last commanded,
    which is correct for a room sitting inside its band: the unit holds the
    trimmed setpoint against its own sensor between our thirty-second
    decisions. OFF stops it.

    They were one value until 0.8.7, and two places downstream had to guess
    which was meant — the actuator guessed from the mode and caught two of the
    five stop paths, and the short-cycle guard guessed "not running" and so
    recorded a stop every time a room reached its band.
    """

    NONE = "none"
    OFF = "off"
    COVERS = "covers"
    FAN = "fan"
    DRY = "dry"
    COMPRESSOR = "compressor"


#: Ordering used by the selector. Index position is the cost rank.
ACTUATOR_ORDER: Final = (
    ActuatorStep.COVERS,
    ActuatorStep.FAN,
    ActuatorStep.DRY,
    ActuatorStep.COMPRESSOR,
)


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """Static configuration for one room.

    Every room is seeded with identical defaults. There is no global setting
    and no inheritance. Rooms that genuinely differ get adjusted individually.
    Architecture proposal v0.3, section 5.
    """

    room_id: str
    name: str
    climate_entity_id: str
    bands: dict[Mode, ComfortBand]
    temperature_entity_id: str | None = None
    humidity_entity_id: str | None = None
    presence_entity_id: str | None = None
    #: A schedule, input_boolean or binary_sensor that is on while this room
    #: is in its sleeping hours. Without one, SLEEP is never entered.
    sleep_schedule_entity_id: str | None = None
    #: A binary sensor that is on while something in the room is generating
    #: heat — a workstation, a server, a dryer. Radiant and convective heat a
    #: wall sensor barely sees but a person sitting next to it certainly does.
    heat_load_entity_id: str | None = None
    #: A fan, or anything else indicating the room's air is moving. Without
    #: movement both convective and evaporative loss are worse than the
    #: comfort formula assumes, so still air is a penalty.
    air_movement_entity_id: str | None = None
    #: Optional override: a binary sensor that is on while the sun is on this
    #: room's windows. Only needed for a room too complicated for a single
    #: compass direction. Normally the controller works it out itself.
    direct_sun_entity_id: str | None = None
    #: Which way this room's windows face. With it, the controller works out
    #: sun-on-glass from the sun's position and needs no sensor at all.
    window_direction: str | None = None
    #: How far an eave, soffit, balcony or verandah projects from the wall
    #: above this window, in metres. Zero or unset means no overhang.
    overhang_projection_m: float | None = None
    #: How far that overhang sits above the bottom of the glass, in metres.
    overhang_height_m: float | None = None
    opening_entity_ids: tuple[str, ...] = ()
    cover_entity_ids: tuple[str, ...] = ()
    #: Whether this integration may move this room's covers at all. True by
    #: default. A room whose blinds are set for privacy or glare rather than
    #: solar gain can be excluded without removing the cover entities.
    allow_cover_control: bool = True
    #: How long presence must hold before starting, how long vacancy must
    #: hold before stopping, and whether to announce first. Seeded with
    #: defaults so a room works without touching them.
    grace: GraceSettings = field(default_factory=GraceSettings)
    #: Media players to announce through. Empty means no announcement, however
    #: the grace settings are configured.
    announce_target_entity_ids: tuple[str, ...] = ()
    #: Set for rooms that must never actuate. Carries the reason string that
    #: appears in the trace, e.g. "upstairs renovation".
    lockout_reason: str | None = None

    def band_for(self, mode: Mode) -> ComfortBand | None:
        return self.bands.get(mode)


@dataclass(frozen=True, slots=True)
class RoomInputs:
    """Everything the mode machine is allowed to look at, at one instant.

    Assembled from Home Assistant state by the coordinator and handed to the
    pure evaluator. Any value the coordinator could not read is None, and the
    evaluator must treat None as unknown rather than as a number.
    """

    now: datetime
    temperature_c: float | None = None
    relative_humidity: float | None = None
    presence: bool | None = None
    #: True while any opening in this room is open.
    opening_open: bool = False
    #: When the longest-open opening in this room was last seen to open. Used
    #: to hold off stopping the unit for a door that is about to close again;
    #: None where no opening is open or the time is not known.
    opening_open_since: datetime | None = None
    #: Sleep schedule is active for this room right now.
    sleep_schedule_active: bool = False
    #: When a heading-home request needs the room to be at comfort by. The
    #: target itself is never supplied: it is always the room's comfort band.
    precondition_deadline: datetime | None = None
    #: Declared on the active tariff window, not inferred from price.
    precool_opportunity: bool = False
    no_grid_import: bool = False
    #: Learned rates, from the thermal model. None until that coefficient has
    #: converged. Together with the index sensitivities these decide dry mode
    #: against cooling on the merits, rather than on a humidity threshold.
    k_sensible_c_per_hour: float | None = None
    k_latent_rh_per_hour: float | None = None
    #: Thermal model verdict. None until the model has converged for this room,
    #: in which case COAST is never entered and the fallback holds the band.
    predicted_to_hold: bool | None = None
    #: False in windows where coasting is the wrong call even if the room would
    #: hold, e.g. the cheap overnight window where energy is cheap and the
    #: battery should arrive at 06:00 full. Architecture proposal v0.3, s7.
    coasting_permitted: bool = True
    #: True when the forecast says the room will need cooling later today, the
    #: precondition for banking thermal mass in a free window.
    forecast_demand_ahead: bool = False
    #: Set by the heading-home request. The only thing that brings an
    #: unoccupied room back on.
    heading_home: bool = False
    #: Whether the sun is currently on this room's windows. Geometry, not light
    #: level — sun position against the window aspect. None when unknown.
    direct_sun: bool | None = None
    #: Something in the room is generating heat.
    heat_load: bool = False
    #: The room's air is moving. False means still, which is a comfort penalty.
    air_moving: bool = False
    #: Outdoor conditions, for the free-cooling advisory. Temperature is also
    #: read by the thermal model, but the model gets it from the coordinator
    #: directly; these are here because the evaluator needs them to answer
    #: whether opening a window would help.
    outdoor_c: float | None = None
    outdoor_relative_humidity: float | None = None
    #: Outdoor wind, in metres per second, converted from whatever unit the
    #: entity reports. None means no wind feed, which is treated as still air
    #: rather than guessed at.
    outdoor_wind_ms: float | None = None
    #: False while a heading-home request has a deadline far enough out that
    #: the model says the pull can wait. The room is still in PRECONDITION —
    #: the request stands — but nothing actuates yet.
    precondition_ready: bool = True
    #: The mode the room was in before the current one, and when it changed,
    #: so the band can be ramped across a sleep transition rather than stepped.
    previous_mode: Mode | None = None
    mode_changed_at: datetime | None = None
    #: Whether this room has any covers under the controller's direction.
    has_covers: bool = False
    #: What the unit itself can do, read from the climate entity. The decision
    #: has to know: choosing dry on a unit with no dry mode would leave the
    #: actuator with a rejection and nothing to fall back to.
    can_cool: bool = True
    can_heat: bool = True
    can_dry: bool = True
    can_fan_only: bool = True
    #: Mean cover position across the room, 0 closed to 100 open, or None when
    #: no cover reports one. Covers are only worth commanding when they still
    #: have somewhere to go: without this the selector picks covers every cycle
    #: on an already-shut room and never escalates.
    cover_position: float | None = None
    #: Whether this room's covers are under this integration's direction at
    #: all. Distinct from `has_covers`: a room can have covers configured
    #: (for their position or for other automations) while telling this
    #: integration not to move them.
    allow_cover_control: bool = True
    #: Whether the compressor may draw power right now. True unless the
    #: tariff currently forbids grid import for this interval *and* the
    #: house has battery/solar readings configured *and* neither covers this
    #: room's projected need until the window lifts or solar catches up.
    #: True (unaffected) whenever power-aware operation is not configured —
    #: this is an added constraint, never a substitute for the ordinary
    #: capability checks.
    power_available: bool = True


@dataclass(slots=True)
class DecisionTrace:
    """Why a room is doing what it is doing.

    Non-negotiable. Every evaluation produces one of these, whether or not
    anything changed. Architecture proposal v0.3, section 10.
    """

    room_id: str
    at: datetime
    mode: Mode
    #: The occupancy mode COAST displaced, so the band in force is visible.
    base_mode: Mode | None = None
    hci: float | None = None
    #: What the corrections contributed, so a surprising index can be read
    #: rather than argued with.
    hci_base: float | None = None
    radiant_fraction: float | None = None
    band_low: float | None = None
    band_high: float | None = None
    band_position: str | None = None
    target_dry_bulb_c: float | None = None
    actuator: ActuatorStep = ActuatorStep.NONE
    reasons: list[str] = field(default_factory=list)
    #: Steps that were considered and rejected, with why. This is what makes
    #: "cheapest first" auditable rather than asserted.
    rejected: list[str] = field(default_factory=list)
    #: Which way the room needs to move to reach its band: "cool", "heat" or
    #: None when it is already inside.
    demand: str | None = None
    #: The setpoint actually sent to the unit: the solved target plus the
    #: outer loop's accumulated trim. Different from target_dry_bulb_c
    #: whenever Layer 2 has learned that the unit's own sensor disagrees with
    #: the room's, which is almost always.
    commanded_dry_bulb_c: float | None = None
    #: How much of that difference the outer loop is responsible for.
    regulation_trim_c: float | None = None
    #: Dew points, and whether outdoor air would help. Advisory: this
    #: controller owns the air conditioner and the covers, not the windows.
    dew_point_c: float | None = None
    outdoor_dew_point_c: float | None = None
    #: What outdoors feels like, on the same scale as `hci`, with the damped
    #: wind term applied. This is the number the free-cooling advice turns on.
    outdoor_apparent_c: float | None = None
    free_cooling_advised: bool = False
    #: True when the commanded setpoint sits near enough the room's dew point
    #: that surfaces may sweat. Reported, never used to refuse an actuation.
    condensation_risk: bool = False
    #: Set when the short-cycle guard refused to let the compressor stop. The
    #: decision stands — a cover or fan step is still carried out — but the
    #: unit is left running rather than being commanded off or into a mode
    #: that would stop it. Without this the guard, which exists to protect the
    #: compressor, silently replaced cover and fan decisions with COMPRESSOR.
    hold_compressor: bool = False
    #: Feeds that answered with a value too old to act on, and how old. A room
    #: holding because its sensor died must say so, not simply hold.
    stale_feeds: list[str] = field(default_factory=list)
    #: The room's learned thermal coefficients and how converged they are.
    #: Published so a decision that depended on the model can be checked
    #: against what the model actually believed at the time.
    model: dict[str, Any] = field(default_factory=dict)

    def as_attributes(self) -> dict[str, Any]:
        """Flatten for publication as entity attributes."""
        return {
            "room_id": self.room_id,
            "evaluated_at": self.at.isoformat(),
            "mode": str(self.mode),
            "base_mode": str(self.base_mode) if self.base_mode else None,
            "hci": None if self.hci is None else round(self.hci, 2),
            "hci_air_only": (
                None if self.hci_base is None else round(self.hci_base, 2)
            ),
            "radiant_fraction": (
                None
                if self.radiant_fraction is None
                else round(self.radiant_fraction, 2)
            ),
            "band_low": self.band_low,
            "band_high": self.band_high,
            "band_position": self.band_position,
            "target_dry_bulb_c": (
                None
                if self.target_dry_bulb_c is None
                else round(self.target_dry_bulb_c, 1)
            ),
            "commanded_dry_bulb_c": (
                None
                if self.commanded_dry_bulb_c is None
                else round(self.commanded_dry_bulb_c, 1)
            ),
            "regulation_trim_c": (
                None
                if self.regulation_trim_c is None
                else round(self.regulation_trim_c, 2)
            ),
            "dew_point_c": self.dew_point_c,
            "outdoor_dew_point_c": self.outdoor_dew_point_c,
            "outdoor_apparent_c": self.outdoor_apparent_c,
            "free_cooling_advised": self.free_cooling_advised,
            "condensation_risk": self.condensation_risk,
            "stale_feeds": list(self.stale_feeds),
            "demand": self.demand,
            "actuator": str(self.actuator),
            "hold_compressor": self.hold_compressor,
            "reasons": list(self.reasons),
            "rejected": list(self.rejected),
            "model": dict(self.model),
        }
