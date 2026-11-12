"""Shapes of the data passed between modules. No logic.

Each module's result shape is added here when that module is built: so
far the shared enumerations, the configuration of the house and its rooms,
a unit's capability profile, the user's choices, and Observe's Snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .const import (
    DEFAULT_ALLOW_BLIND_CONTROL,
    DEFAULT_ANNOUNCE,
    DEFAULT_EMPTY_AFTER_MIN,
    DEFAULT_OCCUPIED_AFTER_MIN,
    DEFAULT_OPENING_GRACE_MIN,
    DEFAULT_WARNING_GRACE_MIN,
)


class ComfortMode(StrEnum):
    """The room's Comfort mode [D058]."""

    SCHEDULE = "schedule"
    SLEEP = "sleep"
    AWAKE = "awake"


class StepKind(StrEnum):
    """What a step asks of the unit [D017][Prop].

    OFF commands the unit off. NONE sends nothing and leaves the unit as
    it is.
    """

    OFF = "off"
    NONE = "none"
    REST = "rest"
    DRY = "dry"
    COOL = "cool"
    HEAT = "heat"


class ValueTag(StrEnum):
    """How far a value in a snapshot can be trusted (Module 1)."""

    MEASURED = "measured"
    STOOD_IN = "stood_in"
    ESTIMATED = "estimated"
    MISSING = "missing"
    UNKNOWN = "unknown"


class PowerManagement(StrEnum):
    """A room's power management setting [D041]."""

    OFF = "off"
    GUIDANCE = "guidance"
    ENFORCED = "enforced"


class RoomModeState(StrEnum):
    """The band rule in force, the state of the Mode sensor (Section 7)."""

    LOCKOUT = "lockout"
    AUTOMATIC_OFF = "automatic_off"
    LEFT = "left"
    UNOCCUPIED = "unoccupied"
    PRECOOL = "precool"
    SLEEP = "sleep"
    OCCUPIED = "occupied"


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """What a unit, or every head of a room together, can do [D048].

    setpoint_step is None only for a room whose heads' steps are not
    whole multiples of one another; setpoints are then not rounded and
    step_reason says why [D051]. range_only: the unit, or a head of the
    room, takes only a low and high setpoint; takes_range: the unit, or
    every head of the room, can take one [D024].
    """

    hvac_modes: tuple[str, ...]
    fan_modes: tuple[str, ...]
    vertical_vanes: tuple[str, ...]
    horizontal_vanes: tuple[str, ...]
    min_setpoint: float
    max_setpoint: float
    setpoint_step: float | None
    range_only: bool
    step_reason: str | None = None
    takes_range: bool = True


@dataclass(frozen=True, slots=True)
class HeadConfig:
    """One air conditioner head in a room (Room screen 2) [D020]."""

    climate: str
    outdoor_unit: str | None = None
    frequency_sensor: str | None = None


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """One room's settings (Room screens 1-3)."""

    room_id: str
    name: str
    heads: tuple[HeadConfig, ...]
    temperature_sensor: str
    humidity_sensor: str
    occupied_band: tuple[float, float]
    sleep_band: tuple[float, float]
    precool_band: tuple[float, float]
    power_management: PowerManagement = PowerManagement.OFF
    presence_sensor: str | None = None
    sleep_schedule: str | None = None
    sun_sensor: str | None = None
    openings: tuple[str, ...] = ()
    blinds: tuple[str, ...] = ()
    allow_blind_control: bool = DEFAULT_ALLOW_BLIND_CONTROL
    window_facing: str | None = None
    overhang_projection_m: float | None = None
    overhang_height_m: float | None = None
    occupied_after_s: float = DEFAULT_OCCUPIED_AFTER_MIN * 60.0
    empty_after_s: float = DEFAULT_EMPTY_AFTER_MIN * 60.0
    warning_grace_s: float = DEFAULT_WARNING_GRACE_MIN * 60.0
    announce: bool = DEFAULT_ANNOUNCE
    announce_through: tuple[str, ...] = ()
    opening_grace_s: float = DEFAULT_OPENING_GRACE_MIN * 60.0
    lockout_reason: str | None = None
    profile: CapabilityProfile | None = None


@dataclass(frozen=True, slots=True)
class HouseConfig:
    """The house's settings (House screens): the config entry's data."""

    tariff_entry: str | None = None
    outdoor_temperature: str | None = None
    outdoor_humidity: str | None = None
    outdoor_wind: str | None = None
    weather: str | None = None
    battery_charge: str | None = None
    battery_size_kwh: float | None = None
    battery_max_discharge_kw: float | None = None
    solar_power: str | None = None
    house_load: str | None = None
    reserve_margin_kwh: float | None = None
    grid_power: str | None = None
    grid_positive_is_import: bool | None = None
    solar_forecast_sources: tuple[str, ...] = field(default_factory=tuple)
    tts_engine: str | None = None


@dataclass(frozen=True, slots=True)
class RoomChoices:
    """The user's choices for a room (Section 7) [D049][D058][D088][I8].

    leave_pressed_at is set while Leave stands; precool_ready_by while
    Precool is asked for (now, or the time the room must be ready by).
    """

    automatic_control: bool = True
    automatic_vane_control: bool = False
    comfort_mode: ComfortMode = ComfortMode.SCHEDULE
    leave_pressed_at: datetime | None = None
    precool_ready_by: datetime | None = None


# --------------------------------------------------------------------------
# Module 1, Observe
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawState:
    """One entity's state as the coordinator hands it over [D002]."""

    entity_id: str
    state: str
    attributes: Mapping[str, Any]
    last_changed: datetime
    last_reported: datetime


@dataclass(frozen=True, slots=True)
class TariffInterval:
    """One forward tariff interval from Abode Power Tariffs [D004].

    constraints are the rules the tariff declares enforceable, held
    absolutely [D034]; hints are the rules it declares as information only.
    """

    start: datetime
    end: datetime
    rate_name: str
    price: float  # $/kWh
    constraints: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Reading:
    """A number in a snapshot, with how far it can be trusted."""

    value: float | None
    tag: ValueTag
    entity_id: str | None = None
    age_s: float | None = None


@dataclass(frozen=True, slots=True)
class Flag:
    """An on/off value in a snapshot, with how far it can be trusted."""

    value: bool | None
    tag: ValueTag
    entity_id: str | None = None
    since: datetime | None = None


class CompressorState(StrEnum):
    """Whether a head's compressor is running (Section 4.8) [D025]."""

    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class CompressorSource(StrEnum):
    """Which source answered whether the compressor ran [D006]."""

    FREQUENCY = "frequency"
    HVAC_ACTION = "hvac_action"
    MODE = "mode"
    LOAD_STEP = "load_step"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HeadSnapshot:
    """One head as it stands this cycle."""

    climate: str
    outdoor_unit: str
    available: bool
    mode: str | None
    setpoint: float | None
    hvac_action: str | None
    own_temperature: float | None
    own_humidity: float | None
    fan_mode: str | None
    vertical_vane: str | None
    horizontal_vane: str | None
    frequency: Reading
    compressor: CompressorState
    compressor_source: CompressorSource


@dataclass(frozen=True, slots=True)
class ForecastHour:
    """One hour of the weather forecast, gaps filled from the sensors now."""

    start: datetime
    temperature: float | None
    humidity: float | None
    wind_ms: float | None
    cloud_coverage: float | None
    uv_index: float | None


@dataclass(frozen=True, slots=True)
class SunSnapshot:
    """The sun on the room's glass (Section 4.2) [D023].

    on_glass is None when it cannot be said; s is the share of full sun on
    the glass, the model's input S. tag is ESTIMATED when the clear-sky fraction had to be
    assumed, which stops the filter correcting on that step.
    """

    on_glass: bool | None
    source: str
    f_clear: float
    transmission: float
    s: float
    tag: ValueTag


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One room at one moment, each value tagged by how far to trust it."""

    room_id: str
    time: datetime
    room_temperature: Reading
    room_humidity: Reading
    heads: tuple[HeadSnapshot, ...]
    sun: SunSnapshot
    outdoor_temperature: Reading
    outdoor_humidity: Reading
    outdoor_wind_ms: Reading
    forecast: tuple[ForecastHour, ...]
    presence: Flag | None
    leave_pressed_at: datetime | None
    precool_ready_by: datetime | None
    openings: tuple[Flag, ...]
    blind_positions: tuple[Reading, ...]
    sleep_schedule: Flag | None
    house_load_kw: Reading
    solar_kw: Reading
    battery_charge_pct: Reading
    grid_kw: Reading
    tariff_now: TariffInterval | None
    tariff_ahead: tuple[TariffInterval, ...]
    stale: tuple[str, ...]


# --------------------------------------------------------------------------
# Module 2, Model
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelAnswers:
    """What the room's model says this cycle, for the other modules and
    the trace. learning is False until the room's first reading."""

    learning: bool
    t: float | None = None
    mass: float | None = None
    e: float | None = None
    drift: float | None = None
    figures: Mapping[str, float] = field(default_factory=dict)
    uncertainty: Mapping[str, float] = field(default_factory=dict)
    sensor_noise: float | None = None
    corrections: int = 0
    last_reason: str | None = None
    trusted: bool = False
    six_hour_trusted: bool = False
    one_hour_error: float | None = None
    delay_min: float | None = None
    lag_min: float | None = None
    u_min: float | None = None
    top_cool: float | None = None
    top_heat: float | None = None
    last_correction: datetime | None = None


# --------------------------------------------------------------------------
# Module 3, Comfort
# --------------------------------------------------------------------------


class BandRule(StrEnum):
    """Which rule chose the band (Module 3). The Mode sensor adds lockout
    and automatic off, which Limits decides."""

    LEFT = "left"
    PRECOOL = "precool"
    UNOCCUPIED = "unoccupied"
    SLEEP = "sleep"
    OCCUPIED = "occupied"


@dataclass(frozen=True, slots=True)
class BandPoint:
    """The band in force at one 5-minute point ahead, in comfort index."""

    time: datetime
    low: float
    high: float
    required: bool

    @property
    def centre(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def half_width(self) -> float:
        return (self.high - self.low) / 2.0


@dataclass(frozen=True, slots=True)
class ComfortResult:
    """What Comfort gives the rest of the cycle.

    off is True when the room is to be off (Leave, or empty); points is
    then empty. end_precool and clear_leave ask the coordinator to clear
    those choices; rerun asks for the room to be decided again at once
    [D092].
    """

    rule: BandRule
    off: bool
    reason: str
    occupied: bool
    sleeping: bool
    waking: bool
    precool_on: bool
    points: tuple[BandPoint, ...]
    room_index: float | None
    centre_c: float | None
    end_precool: str | None = None
    clear_leave: bool = False
    rerun: bool = False
    band_low: float | None = None  # the band in force now, for the trace and the scorecard
    band_high: float | None = None


# --------------------------------------------------------------------------
# Module 4, Economics
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoomPower:
    """A room's part in the house's power budget this cycle."""

    room_id: str
    outdoor_unit: str
    management: PowerManagement
    precool_on: bool
    compressor_running: bool
    top_rate: float
    lowest_rate: float


@dataclass(frozen=True, slots=True)
class RoomEconomics:
    """Economics' answer for one room (economics.py GIVE BACK).

    prices hold the price of each half-hour ahead, $/kWh. ceiling is the
    highest rate the room may run at under ENFORCED power management, or
    None; guidance_ceiling the same figure, shown only, under GUIDANCE.
    """

    prices: tuple[float, ...]
    p_max: float
    ceiling: float | None
    guidance_ceiling: float | None
    turn_off: bool
    allowance_kw: float | None
    reason: str


# --------------------------------------------------------------------------
# Module 5, Control
# --------------------------------------------------------------------------


class Controller(StrEnum):
    """Which controller is in charge (4.9)."""

    PLAN_PID = "plan_pid"
    RL = "rl"


@dataclass(frozen=True, slots=True)
class PlanAction:
    """One half-hour step of a plan. rate is C/h, positive cooling,
    negative heating; blinds_closed is None where blinds are not part of
    the plan."""

    kind: StepKind
    rate: float
    blinds_closed: bool | None


@dataclass(frozen=True, slots=True)
class PlanPoint:
    """The forecast at one 5-minute point of a plan."""

    time: datetime
    t: float
    hci: float
    sigma_hci: float
    outside: bool


@dataclass(frozen=True, slots=True)
class Plan:
    """The best plan found (4.4), with what it costs and why [D006]."""

    made_at: datetime
    actions: tuple[PlanAction, ...]
    points: tuple[PlanPoint, ...]
    minutes_outside: float
    comfort_cost: float
    energy_cost: float
    start_cost: float
    starts: int
    w: float
    c_start: float
    alternatives: tuple[tuple[PlanAction, str], ...] = ()
    # free cooling [D073][D105]: the plan with an opening open and the unit
    # resting, against this plan over the same window and by the same cost
    free_cooling_advised: bool = False
    free_cooling_reason: str | None = None

    @property
    def first(self) -> PlanAction:
        return self.actions[0]

    @property
    def total_cost(self) -> float:
        return self.comfort_cost + self.energy_cost + self.start_cost


@dataclass(frozen=True, slots=True)
class PidTerms:
    """The PID's working, for the trace."""

    u: float
    error: float
    p: float
    i: float
    d: float
    kp: float
    ti_h: float
    td_h: float


@dataclass(frozen=True, slots=True)
class ControlResult:
    """What Control decides this cycle: the action and its rate."""

    kind: StepKind
    rate: float
    blinds_closed: bool | None
    in_charge: Controller
    plan: Plan | None
    pid: PidTerms | None
    reason: str


# --------------------------------------------------------------------------
# Module 6, Delivery
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeadCommand:
    """What Delivery asks of one head (delivery.py GIVE BACK).

    setpoint is the value to send, after whole-degree alternation and
    within the unit's range; wanted is the setpoint before alternation, for
    the Commanded setpoint sensor [D051]. A range-only unit gets low and
    high instead of setpoint [D024]. None means leave it as it is.
    """

    climate: str
    mode: str | None
    setpoint: float | None
    setpoint_low: float | None
    setpoint_high: float | None
    wanted: float | None
    fan_mode: str | None
    vertical_vane: str | None


@dataclass(frozen=True, slots=True)
class BlindCommand:
    """A blind to move, to a position in percent open [D022]."""

    cover: str
    position: float


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """The proposed step: what to send, and the reason for each part.

    rest_heads is what resting would send, so Limits can hold a refused
    start at rest without deciding anything itself; empty for a unit with
    no fan-only mode, which rests off [D057]."""

    kind: StepKind
    rate: float
    heads: tuple[HeadCommand, ...]
    blinds: tuple[BlindCommand, ...]
    blinds_left_alone: tuple[str, ...]
    free_cooling_advised: bool
    free_cooling_reason: str | None
    condensation_risk: bool
    reasons: tuple[str, ...]
    rest_heads: tuple[HeadCommand, ...] = ()  # resting, for Limits to fall back on; empty without fan-only


# --------------------------------------------------------------------------
# Module 7, Limits
# --------------------------------------------------------------------------


class LimitRule(StrEnum):
    """The rules of limits.py, in the order they apply [D006]."""

    LOCKOUT = "lockout"
    AUTOMATIC_CONTROL = "automatic_control"
    LEAVE = "leave"
    EMPTY_ROOM = "empty_room"
    OPENING = "opening"
    MISSING_READING = "missing_reading"
    ENFORCED_POWER = "enforced_power"
    UNIT_ABILITIES = "unit_abilities"
    SHORT_CYCLE_GUARD = "short_cycle_guard"
    TARIFF_CONSTRAINTS = "tariff_constraints"


class WarningKind(StrEnum):
    """A spoken warning Limits says is due; Evidence decides whether it is
    said (D071, D077)."""

    EMPTY_ROOM = "empty_room"
    OPENING_FIRST = "opening_first"
    OPENING_FINAL = "opening_final"


@dataclass(frozen=True, slots=True)
class DueWarning:
    """A warning due this cycle, and how long until the unit is stopped."""

    kind: WarningKind
    seconds_to_off: float


@dataclass(frozen=True, slots=True)
class LimitsResult:
    """The allowed step (limits.py GIVE BACK).

    changed lists every rule that changed, held or refused the proposed
    step; not_delivered is then True, so the PID's integral holds [D054].
    held_off is True while the minimum run holds an OFF back [D077].
    """

    kind: StepKind
    heads: tuple[HeadCommand, ...]
    blinds: tuple[BlindCommand, ...]
    warnings: tuple[DueWarning, ...]
    changed: tuple[LimitRule, ...]
    not_delivered: bool
    held_off: bool
    lockout: bool
    automatic_off: bool
    constraints: tuple[str, ...]
    unknown_constraints: tuple[str, ...]
    missing: tuple[str, ...]
    reasons: tuple[str, ...]


# --------------------------------------------------------------------------
# Module 8, Act
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceCall:
    """One Home Assistant service call Act sends: domain, service and data,
    the entity included."""

    domain: str
    service: str
    data: Mapping[str, Any]

    @property
    def entity_id(self) -> str:
        return str(self.data["entity_id"])


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """One command sent, for the log and the last-200 list [D052]."""

    time: datetime
    room_id: str
    entity_id: str
    service: str
    data: Mapping[str, Any]
    rate: float | None
    room_temperature: float | None
    centre_c: float | None
    by_user: bool
    ok: bool
    error: str | None = None


# --------------------------------------------------------------------------
# Module 9, Evidence
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayScore:
    """One day's scorecard for a room (evidence.py SCORECARD). None where
    the day gave nothing to measure: no occupied time, no running time, or
    no price."""

    comfort_in_band: float | None  # % of occupied time inside the band
    near_centre: float | None  # % of occupied time within 0.3 of the centre
    starts_per_hour: float | None  # starts / hours the compressor ran
    cost: float | None  # the house currency


@dataclass(frozen=True, slots=True)
class Speech:
    """A spoken warning to say: tts.speak on engine, to the room's speakers
    [D069]. key and placeholders give the words (strings.json)."""

    engine: str
    media_players: tuple[str, ...]
    key: str
    placeholders: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Issue:
    """A repair: raised while its cause stands, cleared when it goes. key
    is its translation key; it says what to fix [R18]."""

    issue_id: str
    key: str
    placeholders: Mapping[str, str]
