"""Runs the cycle; decides nothing (Section 8, coordinator.py) [D002].

The coordinator gathers each room's inputs from Home Assistant, calls the
ten modules in the order of Section 5, carries out what they decide, and
publishes the result for the entities. Every decision is in a module.

One cycle every 30 seconds runs every room. The model's minute, 5-minute,
hourly and midnight work runs inside the cycle that first passes each
boundary, so every part of a cycle sees the same moment and nothing else
touches a room's model while it runs; the heavy parts (the filters and the
plan) run in the executor [Prop]. The tariff, the solar forecast and the
weather forecast are read on their own timers, and only replace what the
next cycle reads.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_RESTORED
from homeassistant.core import Context, CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import capabilities
from .act import (
    CommandContext,
    CommandLog,
    RoomAct,
    UserCommand,
    async_send,
    async_user_command,
)
from .comfort import RoomComfort, UserAction, choices_after_user_action
from .const import (
    COIL_REBOUND_S,
    CYCLE_S,
    DOMAIN,
    MODEL_STEP_S,
    RECORDER_KEEP_DAYS_DEFAULT,
    ROOM_SENSOR_GONE_REPAIR_S,
    SOLAR_FORECAST_READ_S,
    TARIFF_READ_S,
    TARIFF_REQUEST_HOURS,
    TARIFF_RESOLUTION_MIN,
    WEATHER_READ_S,
)
from .control import RoomControl
from .control.pid import actuation_minutes, estimate_now, gains
from .control.plan import Conditions, PlanInputs, conditions_ahead
from .delivery import RoomDelivery, outdoor_air_would_cool
from .economics import (
    GridChecks,
    HousePower,
    PowerBudget,
    SolarSlice,
    TariffRead,
    forecast_to_change,
    grid_evidence,
    highest_rate,
    no_import_window_end,
    parse_tariff,
    prices_ahead,
    room_economics,
)
from .evidence import (
    Availability,
    HouseFacts,
    RoomFacts,
    RoomScorecard,
    RoomWarnings,
    house_issues,
    mode_state,
    room_issues,
    trace,
)
from .forms import house_from_data, rooms_from_options
from .hci import outdoor_apparent_temperature, vapour_pressure
from .limits import GuardEntry, HouseGuard, RoomLimits, RoomStep, band_distances
from .memory import (
    HouseState,
    Memory,
    RoomState,
    async_read_history,
    from_data,
    raw_from_state,
    recorder_keep_days,
    replay,
    seed_entities,
    to_data,
)
from .model import RoomModel
from .model.forecast import ForecastInput, drift_now
from .model.physics import Figures, figures
from .model.unit import effort_for_rate, head_effort, top_rate
from .models import (
    CapabilityProfile,
    ComfortResult,
    CommandRecord,
    CompressorState,
    ControlResult,
    DayScore,
    DeliveryResult,
    HeadCommand,
    HouseConfig,
    Issue,
    LimitsResult,
    ModelAnswers,
    RawState,
    RoomConfig,
    RoomEconomics,
    RoomModeState,
    RoomPower,
    Snapshot,
    Speech,
    StepKind,
    TariffInterval,
    ValueTag,
)
from .observe import LoadHistory, RoomObserver
from .psychro import dew_point

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__package__)

SIGNAL_ROOM_ADDED = f"{DOMAIN}_room_added"
SIGNAL_ROOM_REMOVED = f"{DOMAIN}_room_removed"
SIGNAL_ROOM_EDITED = f"{DOMAIN}_room_edited"

TARIFF_DOMAIN = "abode_power_tariffs"
TARIFF_SERVICE = "get_intervals"
WEATHER_DOMAIN = "weather"
WEATHER_SERVICE = "get_forecasts"
TTS_DOMAIN = "tts"
TTS_SERVICE = "speak"

_FIVE = timedelta(seconds=MODEL_STEP_S)
_MINUTE = timedelta(minutes=1)
_HOUR = timedelta(hours=1)
_CHARGE_WINDOW = timedelta(minutes=5)


# --------------------------------------------------------------------------
# What the entities read
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoomView:
    """One room as the last cycle left it, for its entities. ok is False
    when the room failed this cycle; its sensors are then unavailable."""

    ok: bool
    mode: RoomModeState | None
    trace: Mapping[str, Any]
    comfort_index: float | None
    band_low: float | None
    band_high: float | None
    position: str | None
    target_c: float | None
    commanded_c: float | None
    wanted_c: float | None
    dew_point_c: float | None
    outdoor_dew_point_c: float | None
    outdoor_apparent_c: float | None
    free_cooling_advised: bool
    condensation_risk: bool
    in_charge: str | None
    plan: Mapping[str, Any] | None
    learning: float | None
    yesterday: DayScore | None
    rl_score: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HouseView:
    """The house's figures (Section 7, For the house)."""

    demand_kwh: float | None
    demand_per_interval: tuple[Mapping[str, Any], ...]
    demand_until: datetime | None
    demand_to_tariff_change: bool
    tariff_now: float | None
    tariff_rate: str | None
    intervals: tuple[Mapping[str, Any], ...]
    constraints: tuple[str, ...]
    projected_cost: float | None
    outdoor_c: float | None
    outdoor_apparent_c: float | None
    outdoor_dew_point_c: float | None
    forecast_peak_c: float | None
    stale: tuple[str, ...]
    rooms: int


@dataclass(frozen=True, slots=True)
class CycleData:
    """What one cycle publishes."""

    rooms: Mapping[str, RoomView]
    house: HouseView


# --------------------------------------------------------------------------
# One room's parts
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RoomRun:
    """One room's modules, and what they gave this cycle."""

    config: RoomConfig
    state: RoomState
    learning_since: datetime
    observer: RoomObserver = field(init=False)
    comfort: RoomComfort = field(init=False)
    control: RoomControl = field(init=False)
    delivery: RoomDelivery = field(init=False)
    limits: RoomLimits = field(init=False)
    act: RoomAct = field(init=False)
    scorecard: RoomScorecard = field(init=False)
    warnings: RoomWarnings = field(default_factory=RoomWarnings)
    availability: Availability = field(init=False)
    replan: bool = True
    ceiling: float | None = None  # the enforced ceiling the plan in force was made under
    seeding: bool = False
    snapshot: Snapshot | None = None
    answers: ModelAnswers = field(default_factory=lambda: ModelAnswers(learning=False))
    comfort_result: ComfortResult | None = None
    economics: RoomEconomics | None = None
    control_result: ControlResult | None = None
    other_proposal: float | None = None
    delivery_result: DeliveryResult | None = None
    step: RoomStep | None = None
    limits_result: LimitsResult | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    not_delivered: bool = False
    failed: bool = False
    fail_logged: bool = False
    free_cooling_since: datetime | None = None
    commanded_c: float | None = None
    wanted_c: float | None = None
    conditions: list[Conditions] | None = None

    def __post_init__(self) -> None:
        self.build()

    def build(self) -> None:
        """The modules for the room's settings; what the room has learned
        stays in state."""
        room = self.config
        self.observer = RoomObserver(room)
        comfort = RoomComfort(room)
        comfort.occupied = self.state.occupied
        self.comfort = comfort
        self.control = RoomControl(arbiter=self.state.arbiter)
        self.delivery = RoomDelivery(room)
        self.limits = RoomLimits(room)
        self.act = RoomAct(room)
        self.scorecard = RoomScorecard(yesterday=self.state.yesterday)
        self.availability = Availability(room)
        self.replan = True

    def edited(self, room: RoomConfig) -> None:
        """WHEN the room's settings are saved: each module takes the new
        settings and keeps what it remembers between cycles, so an edit
        does not restart a compressor's minimum run or rest, an open
        window's grace, a warning already given, a command still to be
        reported, today's scorecard, the plan or a band's ramp [D031][D098].
        What it remembers of a head or reading the room no longer has is
        dropped."""
        self.config = room
        climates = {h.climate for h in room.heads}
        required = climates | {room.temperature_sensor, room.humidity_sensor}
        self.observer.room = room
        self.comfort.room = room
        self.delivery.room = room
        self.limits.room = room
        self.limits.last_running = tuple(h for h in self.limits.last_running if h.climate in climates)
        self.act.room = room
        self.availability.room = room
        self.availability.keep_only(required)
        self.replan = True

    @property
    def model(self) -> RoomModel:
        return self.state.model

    @property
    def outdoor_units(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(h.outdoor_unit or f"own:{h.climate}" for h in self.config.heads))


def _by_unit(draws: Mapping[tuple[str, str], float | None]) -> dict[str, float | None]:
    """Each outdoor unit's draw now: the sum over the rooms running on it;
    None if any of theirs is not yet learned [D028][D097]."""
    out: dict[str, float | None] = {}
    for (unit, _room), d in draws.items():
        if unit in out:
            prev = out[unit]
            out[unit] = prev + d if prev is not None and d is not None else None
        else:
            out[unit] = d
    return out


def _floor(t: datetime, period: timedelta) -> datetime:
    """t rounded down to a whole period since midnight UTC."""
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return day + ((t - day) // period) * period


CLIMATE_COMPONENT = "climate"
def _note_ceiling(run: RoomRun, ceiling: float | None) -> None:
    """A changed enforced ceiling is planned under at once, not at the next
    5-minute plan [D041][D062]."""
    if ceiling != run.ceiling:
        run.ceiling = ceiling
        run.replan = True


def _ceilings(direction: int, ceiling: float | None, draw: Callable[[float], float | None],
              fig: Figures | None) -> tuple[float | None, float | None]:
    """The enforced ceiling as a cooling rate and as a heating rate. The
    budget finds it in one direction (_budget_direction); the same power is
    a different rate the other way, read on that curve, so a plan that turns
    the other way is held to the same allowance, neither more nor less
    [D028][D041]."""
    if ceiling is None:
        return None, None
    if ceiling <= 0.0:
        other: float | None = 0.0
    else:
        kw = draw(direction * ceiling)
        if kw is None or fig is None:
            other = ceiling  # the draw not learned: the same figure
        else:
            other = highest_rate(_in_direction(draw, -direction), kw, top_rate(fig, -direction)) or 0.0
    return (ceiling, other) if direction > 0 else (other, ceiling)


def _pid_top(top: float, ceilings: tuple[float | None, float | None], direction: int) -> float:
    """The PID never asks past an enforced ceiling either: it is the plan's
    limit and the PID's [D041]. ceilings is (cooling, heating), from
    _ceilings; the one in the direction the PID runs applies."""
    ceiling = ceilings[0] if direction > 0 else ceilings[1]
    return top if ceiling is None else min(top, ceiling)


def _room_power(run: RoomRun, draw: Callable[[float], float | None]) -> tuple[RoomPower, Callable[[float], float | None]]:
    """A room's figures for the power budget, and its draw at a rate: all
    in the direction the room is running, on that direction's curve, floor
    and draw [I10][D028]."""
    assert run.snapshot is not None and run.comfort_result is not None
    fig = figures(run.model.heat.z) if run.model.heat is not None else None
    d = _budget_direction(run)
    power = RoomPower(
        room_id=run.config.room_id,
        outdoor_unit=run.outdoor_units[0],
        management=run.config.power_management,
        precool_on=run.comfort_result.precool_on,
        compressor_running=any(h.compressor is CompressorState.RUNNING for h in run.snapshot.heads),
        top_rate=top_rate(fig, d) if fig is not None else 0.0,
        lowest_rate=run.model.unit.lowest_rate(fig, d) if fig is not None else 0.0,
    )
    return power, _in_direction(draw, d)


def _budget_direction(run: RoomRun) -> int:
    """The direction the power budget works in for a room: the plan's, while
    it heats or cools; while it rests, the way the room must go to reach
    its band's centre, so a resting room that needs heat is budgeted on the
    heating curve and can be offered heat at all [I10][D028]."""
    plan = run.control.plan
    if plan is not None and plan.first.kind is StepKind.HEAT:
        return -1
    if plan is not None and plan.first.kind in (StepKind.COOL, StepKind.DRY):
        return 1
    comfort = run.comfort_result
    if comfort is not None and comfort.room_index is not None and comfort.points:
        return -1 if comfort.room_index < comfort.points[0].centre else 1
    return 1


def _in_direction(draw: Callable[[float], float | None], direction: int) -> Callable[[float], float | None]:
    """A draw function asked with rates as positive numbers, read on the
    curve of one direction: the power budget works in rates without a
    sign, and the sign is what picks the heating curve [D028]."""
    return lambda rate: draw(direction * abs(rate))


def _still_coming(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """A config entry not loaded yet that is on its way: setting up or
    retrying, or not yet set up while Home Assistant is still starting.
    One the user disabled, or one that failed for good, is not: a disabled
    entry is unloaded and never set up again (config_entries.py:2689-2692,
    2026.11.0b0) [HA]."""
    if entry.disabled_by is not None:
        return False
    if entry.state in (ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.SETUP_RETRY):
        return True
    return entry.state is ConfigEntryState.NOT_LOADED and hass.state is not CoreState.running


def native_temperature_unit(hass: HomeAssistant, entity_id: str) -> str | None:
    """A climate entity's own temperature unit, from the entity itself:
    its state shows only the system's unit. native_temperature_unit from
    2026.11, temperature_unit before it (climate/entity.py:267,
    2026.11.0b0) [HA]. None when the entity is not loaded."""
    # the climate entity component, kept under its domain (climate's
    # DATA_COMPONENT is HassKey("climate"), climate/const.py:16 from 2026.10) [HA]
    component = hass.data.get(CLIMATE_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None:
        return None
    unit = getattr(entity, "native_temperature_unit", None) or getattr(entity, "temperature_unit", None)
    return unit if isinstance(unit, str) else None


def native_limits(hass: HomeAssistant, entity_id: str) -> tuple[float, float] | None:
    """A climate entity's own min_temp and max_temp, in its own unit: what
    Home Assistant checks a setpoint against (climate/services.py:150), not
    the shown, rounded figures (climate/entity.py:159-164, 2026.11.0b0)
    [HA]. None when the entity is not loaded."""
    component = hass.data.get(CLIMATE_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None:
        return None
    lo, hi = getattr(entity, "min_temp", None), getattr(entity, "max_temp", None)
    if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int | float) or not isinstance(hi, int | float):
        return None
    return float(lo), float(hi)


def _gone(hass: HomeAssistant, entity_id: str) -> bool:
    """An entity no longer exists [D046]: it has no state, or only the
    stand-in the entity registry writes (restored: true), and nothing is
    about to provide it. The registry writes that stand-in for any
    registered entity not set up, so while the entity's own config entry
    is not loaded (setting up, retrying, reloading) it is only away, not
    gone (helpers/entity_registry.py write_unavailable_state;
    helpers/entity.py, remove, 2026.11.0b0) [HA]."""
    state = hass.states.get(entity_id)
    if state is not None and not state.attributes.get(ATTR_RESTORED):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.config_entry_id is None:
        return True
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None or config_entry.state is ConfigEntryState.LOADED:
        return True  # loaded and not providing it (a platform retrying waits out the grace)
    return not _still_coming(hass, config_entry)


def _local_hour(t: datetime) -> float:
    local = dt_util.as_local(t)
    return local.hour + local.minute / 60.0 + local.second / 3600.0


# --------------------------------------------------------------------------
# The coordinator
# --------------------------------------------------------------------------


class AbodeHvacCoordinator(DataUpdateCoordinator[CycleData]):
    """The cycle, for every room of the house."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=CYCLE_S),
            always_update=True,
        )
        self.house_data = dict(entry.data)
        self.house: HouseConfig = house_from_data(entry.data)
        self.rooms: dict[str, RoomRun] = {}
        self.memory = Memory(hass, self._collect)
        self.house_state = HouseState()
        self._stored_rooms: dict[str, RoomState] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self.commands = CommandLog()
        self._last_command: dict[str, datetime] = {}
        self.load_history = LoadHistory()
        self.budget = PowerBudget()
        self.guard = HouseGuard()
        self.grid: GridChecks | None = None
        self._charges: deque[tuple[datetime, float]] = deque()
        self.tariff = TariffRead((), frozenset())
        self.tariff_read_at: datetime | None = None
        self.forecast: list[dict[str, Any]] = []
        self.forecast_units: tuple[str | None, str | None] = (None, None)
        self.forecast_error: str | None = None
        self.solar: list[SolarSlice] | None = None
        self.solar_error: str | None = None
        self._due: dict[str, datetime] = {}
        self._last_cycle: datetime | None = None
        self._running_since: dict[str, datetime] = {}
        self._stopped_since: dict[str, datetime] = {}
        self._unit_running: dict[str, bool] = {}
        self._issues: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {}
        self._gone_since: dict[str, datetime] = {}  # room sensors seen gone, and since when
        self._words: dict[str, str] = {}
        self._tts_failed = False
        self.version: str | None = None
        self.unseeded: list[str] = []
        self.house_device_id: str | None = None

    # ------------------------------------------------------------------
    # Start-up
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """AT START-UP: Memory restores every room before the first cycle
        [D049][D068]; a file that cannot be read raises, so setup retries
        [R18]. Then the first reads of the tariff and the forecasts, and
        the timers."""
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(self.hass, DOMAIN)
        self.version = str(integration.version) if integration.version is not None else None
        data = await self.memory.async_load()
        self._stored_rooms, self.house_state = from_data(data, has_load_sensor=self.house.house_load is not None)
        if self.house.grid_power and self.house.grid_positive_is_import is not None:
            self.grid = GridChecks(self.house.grid_positive_is_import)
        self._words = await async_get_translations(self.hass, self.hass.config.language, "exceptions", [DOMAIN])
        now = dt_util.utcnow()
        for room in rooms_from_options(self.config_entry.options):
            self._add_room(room, now)
        await self.async_read_tariff()
        await self.async_read_weather()
        await self.async_read_solar()
        entry = self.config_entry
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._tariff_timer, timedelta(seconds=TARIFF_READ_S))
        )
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._weather_timer, timedelta(seconds=WEATHER_READ_S))
        )
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._solar_timer, timedelta(seconds=SOLAR_FORECAST_READ_S))
        )

    def _add_room(self, room: RoomConfig, now: datetime) -> RoomRun:
        stored = self._stored_rooms.pop(room.room_id, None)
        state = stored if stored is not None else RoomState()
        if stored is None or stored.model.heat is None:
            # a new room, or one whose stored heat filter was refused, is
            # seeded from history [Prop][R19]
            self.unseeded.append(room.room_id)
        profile = room.profile or state.profile
        if profile is None:
            profile = self._read_profile(room)  # [D048]
        if profile is not None:
            room = replace(room, profile=profile)
            state.profile = profile
        run = RoomRun(room, state, now)
        self.rooms[room.room_id] = run
        return run

    def _read_profile(self, room: RoomConfig) -> CapabilityProfile | None:
        """A room's capability profile from its live units [D048]; None
        while any head is not reporting."""
        profiles: list[CapabilityProfile] = []
        unit = self.hass.config.units.temperature_unit
        for h in room.heads:
            st = self.hass.states.get(h.climate)
            p = capabilities.read_profile(st.state if st else None, st.attributes if st else {}, unit,
                                          native_temperature_unit(self.hass, h.climate),
                                          native_limits(self.hass, h.climate))
            if p is None:
                return None
            profiles.append(p)
        return capabilities.combine(profiles)

    def _collect(self) -> dict[str, Any]:
        """What Memory writes: every room's learning and choices, and the
        house's learned draw."""
        rooms = {rid: r.state for rid, r in self.rooms.items()}
        for rid, r in self.rooms.items():
            r.state.occupied = r.comfort.occupied
            r.state.yesterday = r.scorecard.yesterday
            r.state.arbiter = r.control.arbiter
            rooms[rid] = r.state
        return to_data(rooms, self.house_state)

    async def async_shutdown_rooms(self) -> None:
        """WHEN the component unloads: stop seeding, write everything
        [D064]."""
        self._closing = True
        async with self._lock:
            await self.memory.async_write_now()

    # ------------------------------------------------------------------
    # Reads on their own timers
    # ------------------------------------------------------------------

    async def _tariff_timer(self, _now: datetime) -> None:
        await self.async_read_tariff()

    async def _weather_timer(self, _now: datetime) -> None:
        await self.async_read_weather()

    async def _solar_timer(self, _now: datetime) -> None:
        await self.async_read_solar()

    async def async_read_tariff(self) -> None:
        """Every 15 minutes: the forward intervals, in one service call to
        Abode Power Tariffs [D004]. A failed read prices every half-hour
        the same and raises a repair; comfort is unchanged."""
        entry_id = self.house.tariff_entry
        if entry_id is None:
            self.tariff = TariffRead((), frozenset())
            return
        try:
            response = await self.hass.services.async_call(
                TARIFF_DOMAIN,
                TARIFF_SERVICE,
                {"config_entry_id": entry_id, "hours": TARIFF_REQUEST_HOURS,
                 "resolution_minutes": TARIFF_RESOLUTION_MIN},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError) as err:
            self.tariff = TariffRead((), frozenset(), str(err) or type(err).__name__)
            return
        self.tariff = parse_tariff(response if isinstance(response, Mapping) else None)
        self.tariff_read_at = dt_util.utcnow()

    async def async_read_weather(self) -> None:
        """Every 30 minutes: the hourly forecast [Set]."""
        entity = self.house.weather
        if entity is None:
            self.forecast, self.forecast_error = [], None
            return
        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN, WEATHER_SERVICE, {ATTR_ENTITY_ID: entity, "type": "hourly"},
                blocking=True, return_response=True,
            )
        except (HomeAssistantError, ValueError) as err:
            self.forecast, self.forecast_error = [], str(err) or type(err).__name__
            return
        found = response.get(entity) if isinstance(response, Mapping) else None
        hours = found.get("forecast") if isinstance(found, Mapping) else None
        self.forecast = [dict(h) for h in hours if isinstance(h, Mapping)] if isinstance(hours, list) else []
        self.forecast_error = None
        st = self.hass.states.get(entity)
        attrs = st.attributes if st is not None else {}
        self.forecast_units = (attrs.get("temperature_unit"), attrs.get("wind_speed_unit"))

    async def async_read_solar(self) -> None:
        """Every 15 minutes: the solar forecast, from the sources chosen,
        or the Energy dashboard's where none is chosen [D061]."""
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.components.energy.websocket_api import (
            async_get_energy_platforms,
        )

        if self.house.solar_power is None:
            self.solar, self.solar_error = None, None
            return
        sources = list(self.house.solar_forecast_sources)
        try:
            if not sources:
                manager = await async_get_manager(self.hass)
                if manager.data is not None:
                    for src in manager.data["energy_sources"]:
                        if src["type"] == "solar":
                            sources += list(src.get("config_entry_solar_forecast") or [])
            if not sources:
                self.solar, self.solar_error = None, None
                return
            platforms = await async_get_energy_platforms(self.hass)
            wh: dict[str, float] = {}
            waiting = False
            for entry_id in dict.fromkeys(sources):
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    continue
                # a source still setting up has no forecast to give yet: its
                # platform would read runtime_data before it is set
                # (forecast_solar/energy.py, 2026.11.0b0), and until it is set
                # up its energy platform is not even registered
                # (helpers/integration_platform.py) [HA]
                if entry.state is not ConfigEntryState.LOADED:
                    waiting = waiting or _still_coming(self.hass, entry)
                    continue
                if entry.domain not in platforms:
                    continue
                result = await platforms[entry.domain](self.hass, entry_id)
                if result is None:
                    continue
                for when, value in result["wh_hours"].items():
                    wh[when] = wh.get(when, 0.0) + float(value)
        except (HomeAssistantError, ValueError, TypeError, KeyError, AttributeError) as err:
            # another integration's forecast code: whatever it raises, the
            # forecast is unreadable and a repair says so [D061]
            self.solar, self.solar_error = None, str(err) or type(err).__name__
            return
        slices: list[SolarSlice] = []
        for when, value in wh.items():
            start = dt_util.parse_datetime(when)
            if start is None or not math.isfinite(value):
                continue
            start = dt_util.as_utc(start)
            slices.append(SolarSlice(start, start + _HOUR, value / 1000.0))
        slices.sort(key=lambda s: s.start)
        if not slices and waiting:
            return  # read again in 15 minutes; nothing is wrong yet
        self.solar = slices or None
        self.solar_error = None if slices else "the solar forecast gave no hours"

    # ------------------------------------------------------------------
    # The cycle
    # ------------------------------------------------------------------

    def _is_due(self, key: str, now: datetime, period: timedelta) -> bool:
        mark = _floor(now, period)
        last = self._due.get(key)
        if last is not None and mark <= last:
            return False
        self._due[key] = mark
        return True

    def _day_due(self, now: datetime) -> str | None:
        """The local day that has just ended, once, at local midnight."""
        today = dt_util.as_local(now).date()
        last = self._due.get("day")
        mark = datetime(today.year, today.month, today.day, tzinfo=dt_util.get_default_time_zone())
        if last is None:
            self._due["day"] = mark
            return None
        if mark <= last:
            return None
        self._due["day"] = mark
        return (today - timedelta(days=1)).isoformat()

    def _states(self) -> dict[str, RawState]:
        """The current state of every entity, in the shape Observe reads
        (D002: each decision in a cycle sees the same moment)."""
        return {s.entity_id: raw_from_state(s) for s in self.hass.states.async_all()}

    async def _async_update_data(self) -> CycleData:
        async with self._lock:
            now = dt_util.utcnow()
            data = await self._cycle(now)
        if self.rooms and all(r.failed for r in self.rooms.values()):
            # no room can run: every entity is unavailable
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="no_room_can_run")
        return data

    async def _cycle(self, now: datetime) -> CycleData:
        """THE CYCLE, for each room, in the order of Section 5 [Prop]."""
        dt_s = (now - self._last_cycle).total_seconds() if self._last_cycle is not None else CYCLE_S
        self._last_cycle = now
        states = self._states()
        minute = self._is_due("minute", now, _MINUTE)
        five = self._is_due("five", now, _FIVE)
        hour = self._is_due("hour", now, _HOUR)
        day_ended = self._day_due(now)
        system_unit = self.hass.config.units.temperature_unit
        location = (self.hass.config.latitude, self.hass.config.longitude)
        head_units = {h.climate: h.outdoor_unit or f"own:{h.climate}" for r in self.rooms.values()
                      for h in r.config.heads}
        draw_now = _by_unit(self._running_draws())
        shared = self._shared_climates()

        # 1-3: Observe, Model, Comfort, per room
        for run in list(self.rooms.values()):
            run.failed = False
            try:
                await self._observe_and_learn(run, states, now, minute, five, hour, location, head_units,
                                              draw_now, system_unit)
            except Exception:  # noqa: BLE001 - one room failing never fails the update [R18]
                self._room_failed(run)
        runs = [r for r in self.rooms.values() if not r.failed and r.snapshot is not None]

        # the house: load, the power budget, grid checks, once per cycle
        first = runs[0].snapshot if runs else None
        budget = self._house(now, runs, first)
        if minute and first is not None:
            heads = [h for r in runs if r.snapshot is not None for h in r.snapshot.heads]
            self.house_state.draw.observe(now, heads, self._last_command, self.load_history)

        # 4-7: Economics, Control, Delivery, Limits, per room
        for run in runs:
            try:
                await self._decide(run, now, budget.get(run.config.room_id), location,
                                   any(c in shared for c in (h.climate for h in run.config.heads)))
            except Exception:  # noqa: BLE001 - one room failing never fails the update [R18]
                self._room_failed(run)
        runs = [r for r in runs if not r.failed and r.step is not None]

        # 7, rule 9: the short-cycle guard, across rooms [D016][D020]
        entries = []
        for run in runs:
            assert run.step is not None and run.snapshot is not None
            observed = {u: any(h.compressor is CompressorState.RUNNING for h in run.snapshot.heads
                               if (h.outdoor_unit or f"own:{h.climate}") == u) for u in run.outdoor_units}
            seen = tuple(HeadCommand(h.climate, h.mode, None, None, None, None, None, None)
                         for h in run.snapshot.heads if h.compressor is CompressorState.RUNNING and h.mode)
            c = run.comfort_result
            outside, from_centre = (band_distances(c.room_index, c.band_low, c.band_high) if c is not None
                                    else (0.0, 0.0))
            entries.append(GuardEntry(run.limits, run.step, run.outdoor_units, observed, seen, outside, from_centre))
        allowed = self.guard.apply(now, entries)

        # 8-9: Act, Evidence, per room
        speech: list[Speech] = []
        for run in runs:
            run.limits_result = allowed.get(run.config.room_id)
            try:
                speech += await self._act_and_record(run, now, states, dt_s, system_unit)
            except Exception:  # noqa: BLE001 - one room failing never fails the update [R18]
                self._room_failed(run)

        if day_ended is not None:
            self._midnight(day_ended)
        for s in speech:
            await self._speak(s)
        self._repairs(now, shared)
        self._log_failures()
        return self._publish()

    async def _observe_and_learn(
        self,
        run: RoomRun,
        states: Mapping[str, RawState],
        now: datetime,
        minute: bool,
        five: bool,
        hour: bool,
        location: tuple[float, float],
        head_units: Mapping[str, str],
        draw_now: Mapping[str, float | None],
        system_unit: str,
    ) -> None:
        room = run.config
        snap = run.observer.build(
            house=self.house,
            states=states,
            now=now,
            choices=run.state.choices,
            location=location,
            forecast=self.forecast,
            forecast_temperature_unit=self.forecast_units[0],
            forecast_wind_unit=self.forecast_units[1],
            tariff=self.tariff.intervals,
            load_history=self.load_history,
            commands=self._last_command,
            draw_kw=draw_now,
            head_outdoor_units=head_units,
            system_temperature_unit=system_unit,
        )
        run.snapshot = snap
        model = run.model
        if minute:
            model.minute(snap, _local_hour(now))
        if five:
            model = await self._in_executor(run, lambda m: m.five_minutes(now))
            run.replan = True
            if model.heat is not None:
                self.memory.learned()
        run.conditions = conditions_ahead(snap, room, location, _local_hour, now,
                                          model.heat.last_outdoor if model.heat is not None else None)
        if hour:
            await self._hour(run, now)
        outdoor = snap.outdoor_temperature.value
        if outdoor is None and model.heat is not None:
            outdoor = model.heat.last_outdoor
        run.answers = model.answers(outdoor, snap.sun.s, _local_hour(now))
        comfort = run.comfort.evaluate(snap, run.state.choices, now)
        choices = run.state.choices
        if comfort.end_precool is not None or comfort.clear_leave:
            choices = replace(
                choices,
                precool_ready_by=None if comfort.end_precool is not None else choices.precool_ready_by,
                leave_pressed_at=None if comfort.clear_leave else choices.leave_pressed_at,
            )
            run.state.choices = choices
            self.memory.choice()
        if comfort.rerun:
            run.replan = True  # the band changed: decided again at once [D092]
        if run.comfort_result is None or run.comfort_result.off != comfort.off:
            run.replan = True
        run.comfort_result = comfort

    async def _hour(self, run: RoomRun, now: datetime) -> None:
        """Every hour: Model's trust check; the arbiter hands down at once
        if trust is lost [I7]."""
        snap = run.snapshot
        assert snap is not None
        heat = run.model.heat
        rest: list[ForecastInput] = []
        conds = run.conditions or []
        tau = snap.sun.transmission
        if conds and all(c.outdoor_rh is not None for c in conds[:12]):
            rest = [ForecastInput(c.outdoor, float(c.outdoor_rh or 0.0),
                                  tau * c.f_clear if c.sun_on_glass else 0.0, c.hour, StepKind.REST, 0.0)
                    for c in conds[:12]]
        # only measured room readings are scored: a unit's own reading
        # standing in is never learned from [D074]
        t = snap.room_temperature.value if snap.room_temperature.tag is ValueTag.MEASURED else None
        rh = snap.room_humidity.value if snap.room_humidity.tag is ValueTag.MEASURED else None
        e_now = vapour_pressure(t, rh) if t is not None and rh is not None else (
            run.model.humidity.z[0] if run.model.humidity is not None else 0.0
        )
        trusted = run.model.trust.trusted
        t_now = t if heat is not None else None
        model = await self._in_executor(run, lambda m: m.hour(now, t_now, rest, e_now))
        if trusted and not model.trust.trusted:
            run.control.arbiter.lost_trust(now)

    async def _in_executor(self, run: RoomRun, step: Callable[[RoomModel], object]) -> RoomModel:
        """A model step too heavy for the event loop, run in the executor on
        a copy that replaces the room's model when it is done. Nothing in
        the event loop (a save, the diagnostics) ever reads a model while it
        is being changed [HA]. The cycle holds the lock throughout, so a
        seeding cannot replace the model in between."""
        work = copy.deepcopy(run.state.model)
        await self.hass.async_add_executor_job(step, work)
        run.state.model = work
        return work

    def _running_draws(self) -> dict[tuple[str, str], float | None]:
        """Each room's learned draw now on each outdoor unit it runs, at the
        mean effort of its running heads on that unit, from the last cycle's
        snapshots: (outdoor unit, room id) to kW [D028][I10]. A unit's draw
        is the sum over the rooms on it."""
        efforts: dict[tuple[str, str], list[float]] = {}
        for run in self.rooms.values():
            if run.snapshot is None:
                continue
            for h in run.snapshot.heads:
                e = head_effort(h)
                if e is not None and h.compressor is CompressorState.RUNNING:
                    efforts.setdefault((h.outdoor_unit, run.config.room_id), []).append(e)
        draw = self.house_state.draw
        return {k: draw.draw_at(k[0], sum(v) / len(v)) for k, v in efforts.items()}

    def _draw_for_rate(self, run: RoomRun) -> Callable[[float], float | None]:
        """The room's learned draw at a rate: each of its outdoor units at
        the effort that rate needs [D028][I10]."""
        heat = run.model.heat
        if heat is None:
            return lambda _rate: None
        fig = figures(heat.z)
        units = run.outdoor_units
        draw = self.house_state.draw

        def at(rate: float) -> float | None:
            effort = effort_for_rate(fig, rate)
            total = 0.0
            for u in units:
                d = draw.draw_at(u, effort)
                if d is None:
                    return None
                total += d
            return total

        return at

    def _shared_climates(self) -> dict[str, tuple[str, ...]]:
        """Every climate entity used by more than one room [D021]."""
        by: dict[str, list[str]] = {}
        for run in self.rooms.values():
            for h in run.config.heads:
                by.setdefault(h.climate, []).append(run.config.name)
        return {c: tuple(n) for c, n in by.items() if len(n) > 1}

    def _house(
        self, now: datetime, runs: Sequence[RoomRun], first: Snapshot | None
    ) -> dict[str, tuple[float | None, bool, float | None, str]]:
        """The house's readings and the one power budget [I10]."""
        if first is None:
            return {}
        load = first.house_load_kw.value
        solar = first.solar_kw.value
        charge = first.battery_charge_pct.value
        self.load_history.add(now, load)
        running = frozenset(
            h.outdoor_unit for r in runs if r.snapshot is not None for h in r.snapshot.heads
            if h.compressor is CompressorState.RUNNING
        )
        self.budget.loads.add(now, load, solar, running)
        for unit in {h.outdoor_unit for r in runs if r.snapshot is not None for h in r.snapshot.heads}:
            on = unit in running
            if self._unit_running.get(unit) is not on:
                if unit in self._unit_running:
                    (self._running_since if on else self._stopped_since)[unit] = now
                self._unit_running[unit] = on
        if charge is not None:
            self._charges.append((now, charge))
        while len(self._charges) > 1 and now - self._charges[1][0] >= _CHARGE_WINDOW:
            self._charges.popleft()
        window_end = no_import_window_end(self.tariff.intervals, now)
        if self.grid is not None:
            self.grid.observe(now, load_kw=load, solar_kw=solar, grid_kw=first.grid_kw.value, charge_pct=charge,
                              in_no_import_window=window_end is not None)
        house = HousePower(
            battery_charge_pct=charge,
            battery_size_kwh=self.house.battery_size_kwh,
            reserve_kwh=self.house.reserve_margin_kwh,
            rated_discharge_kw=self.house.battery_max_discharge_kw,
            solar_configured=self.house.solar_power is not None,
            load_configured=self.house.house_load is not None,
        )
        rooms: list[RoomPower] = []
        draws: dict[str, Callable[[float], float | None]] = {}
        for r in runs:
            power, draw = _room_power(r, self._draw_for_rate(r))
            rooms.append(power)
            draws[r.config.room_id] = draw
        return self.budget.evaluate(
            now=now,
            window_end=window_end,
            house=house,
            rooms=rooms,
            draw_for_rate=draws,
            running_draw=self._running_draws(),
            solar_slices=self.solar,
            worst_clear_sky=self._worst_clear_sky(now, window_end, first),
        )

    @staticmethod
    def _worst_clear_sky(now: datetime, window_end: datetime | None, snap: Snapshot) -> float:
        """The worst clear-sky fraction the forecast gives over the rest of
        the no-import window [D039]; 1 where the forecast gives none, so the
        budget fails toward comfort [D008]."""
        from .observe import clear_sky_fraction

        if window_end is None:
            return 1.0
        values = [
            f for h in snap.forecast
            if h.start + _HOUR > now and h.start < window_end
            and (f := clear_sky_fraction(h.cloud_coverage, h.uv_index)) is not None
        ]
        return min(values) if values else 1.0

    async def _decide(
        self,
        run: RoomRun,
        now: datetime,
        budget: tuple[float | None, bool, float | None, str] | None,
        location: tuple[float, float],
        shared_climate: bool,
    ) -> None:
        """4-7 for one room: Economics, Control, Delivery, Limits."""
        snap, comfort = run.snapshot, run.comfort_result
        assert snap is not None and comfort is not None
        room = run.config
        prices, p_max = prices_ahead(self.tariff.intervals, now)
        econ = room_economics(room.power_management, prices, p_max,
                              budget if budget is not None else (None, False, None, "no limit"))
        run.economics = econ
        _note_ceiling(run, econ.ceiling)
        heat = run.model.heat
        fig = figures(heat.z) if heat is not None else None
        # FREE COOLING [D073]: outdoor air would do better in an occupied room
        outdoor_better = comfort.occupied is True and outdoor_air_would_cool(snap)
        if not outdoor_better:
            run.free_cooling_since = None

        async def replan() -> None:
            assert heat is not None and fig is not None
            inputs = self._plan_inputs(run, now, prices, p_max, econ)
            if inputs is not None:
                step = room.profile.setpoint_step if room.profile is not None else None
                g = gains(fig, heat.delay_min, heat.lag_min, actuation_minutes(step))
                await self.hass.async_add_executor_job(run.control.replan, inputs, g)
                run.replan = False

        if run.replan and heat is not None and fig is not None and not comfort.off:
            await replan()
        if (
            outdoor_better
            and run.free_cooling_since is None
            and run.control.plan is not None
            and run.control.plan.first.kind is StepKind.COOL
            and heat is not None and fig is not None and not comfort.off
        ):
            # the plan is to cool: free cooling is advised from now, and the
            # plan made again holds a start for 10 minutes before it goes out
            run.free_cooling_since = now
            await replan()
        control: ControlResult | None = None
        if heat is not None and fig is not None and run.control.plan is not None:
            direction = 1 if run.control.plan.first.rate >= 0 else -1
            outdoor = snap.outdoor_temperature.value
            if outdoor is None:
                outdoor = heat.last_outdoor
            slope = (drift_now(heat, outdoor, snap.sun.s, _local_hour(now)) - heat.u) if outdoor is not None else 0.0
            last = run.model.last_step
            since_h = (now - last.time).total_seconds() / 3600.0 if last is not None else 0.0
            control = run.control.cycle(
                now=now,
                t_est=estimate_now(heat.z[0], slope, since_h),
                slope_est=slope,
                u_min=run.model.unit.lowest_rate(fig, direction),
                top=_pid_top(top_rate(fig, direction),
                             _ceilings(_budget_direction(run), econ.ceiling, self._draw_for_rate(run), fig),
                             direction),
                delivered=not run.not_delivered,
            )
        run.control_result = control
        run.other_proposal = None  # the learned controller has no policy yet (4.9) [NYE]
        if control is None:
            control = ControlResult(StepKind.NONE, 0.0, None, run.control.arbiter.in_charge, None, None,
                                    "no plan yet: the model has no reading to plan from")
        u_min = run.model.unit.lowest_rate(fig, 1) if fig is not None else 0.0
        delivery = run.delivery.deliver(now=now, control=control, snapshot=snap, comfort=comfort,
                                        choices=run.state.choices, heat=heat, u_min=u_min,
                                        cooling_held=run.free_cooling_since is not None)
        run.delivery_result = delivery
        run.step = run.limits.step(now=now, proposed=delivery, snapshot=snap, comfort=comfort, economics=econ,
                                   choices=run.state.choices, shared_climate=shared_climate,
                                   can_speak=self._engine() is not None)

    def _plan_inputs(
        self, run: RoomRun, now: datetime, prices: Sequence[float], p_max: float, econ: RoomEconomics
    ) -> PlanInputs | None:
        """Everything the plan works within; None without the 72 points
        of weather the plan needs."""
        snap, comfort, heat = run.snapshot, run.comfort_result, run.model.heat
        assert snap is not None and comfort is not None and heat is not None
        conds = run.conditions
        if conds is None or len(comfort.points) < len(conds):
            return None
        room = run.config
        t, rh = snap.room_temperature.value, snap.room_humidity.value
        if t is not None and rh is not None:
            e_now = vapour_pressure(t, rh)
        elif run.model.humidity is not None:
            e_now = run.model.humidity.z[0]
        else:
            return None
        units = run.outdoor_units
        running = any(self._unit_running.get(u, False) for u in units)
        starts = [self._running_since[u] for u in units if u in self._running_since]
        stops = [self._stopped_since[u] for u in units if u in self._stopped_since]
        rebound = 0
        hum = run.model.humidity
        if hum is not None and hum.last_wet_stop is not None:
            left = COIL_REBOUND_S - (now - hum.last_wet_stop).total_seconds()
            rebound = max(math.ceil(left / MODEL_STEP_S), 0)
        profile = room.profile
        last_kind = run.control.plan.first.kind if run.control.plan is not None else StepKind.REST
        cool_cap, heat_cap = _ceilings(_budget_direction(run), econ.ceiling, self._draw_for_rate(run),
                                       figures(heat.z))
        return PlanInputs(
            now=now,
            heat=heat,
            humidity=run.model.humidity,
            e_now=e_now,
            unit=run.model.unit,
            bands=comfort.points,
            conditions=conds,
            prices=prices,
            p_max=p_max,
            draw_for_rate=self._draw_for_rate(run),
            modes=frozenset(profile.hvac_modes) if profile is not None else frozenset(),
            blind_control=room.allow_blind_control and bool(room.blinds),
            transmission_now=snap.sun.transmission,
            ceiling=cool_cap,
            ceiling_heat=heat_cap,
            waking=comfort.waking,
            compressor_running=running,
            running_since=max(starts) if running and starts else None,
            stopped_since=max(stops) if not running and stops else None,
            occupied=comfort.occupied,
            free_cooling_advised_at=run.free_cooling_since,
            rebound_steps=rebound,
            last_kind=last_kind,
        )

    async def _act_and_record(
        self, run: RoomRun, now: datetime, states: Mapping[str, RawState], dt_s: float, system_unit: str
    ) -> list[Speech]:
        """8-9 for one room: send what is allowed, then the trace, the
        scorecard and the warnings."""
        result, snap, comfort = run.limits_result, run.snapshot, run.comfort_result
        assert result is not None and snap is not None and comfort is not None
        run.not_delivered = result.not_delivered  # [D054]
        calls = run.act.calls(now=now, step=result, live=states, system_unit=system_unit, choices=run.state.choices)
        about = CommandContext(run.control_result.rate if run.control_result else None,
                               snap.room_temperature.value, comfort.centre_c)
        records: list[CommandRecord] = []
        if calls:
            records = await async_send(self.hass, calls, room_id=run.config.room_id, log=self.commands, now=now,
                                       about=about)
        for rec in records:
            if rec.ok:
                self._last_command[rec.entity_id] = now  # Act tells Model what was sent [D081]
                if rec.service == "set_temperature":
                    head = next((h for h in result.heads if h.climate == rec.entity_id), None)
                    if head is not None:
                        run.commanded_c = head.setpoint
        wanted = [h.wanted for h in (run.delivery_result.heads if run.delivery_result else ()) if h.wanted is not None]
        if wanted:
            run.wanted_c = wanted[0]
        run.trace = trace(snapshot=snap, answers=run.answers, comfort=comfort, economics=run.economics,
                          control=run.control_result, other_proposal=run.other_proposal,
                          delivery=run.delivery_result, limits=result, choices=run.state.choices)
        running = any(h.compressor is CompressorState.RUNNING for h in snap.heads)
        draws = self._running_draws()
        rid = run.config.room_id
        # the room's own share of each outdoor unit it runs, not the whole unit's draw [D097];
        # a share not yet learned leaves the day unpriced, never priced at 0
        own = [d for (_u, r), d in draws.items() if r == rid]
        learned = [d for d in own if d is not None]
        draw = sum(learned) if running and own and len(learned) == len(own) else None
        band = (comfort.band_low, comfort.band_high) if comfort.band_low is not None and comfort.band_high is not None \
            else None
        run.scorecard.observe(dt_s=dt_s, occupied=comfort.occupied, index=comfort.room_index, band=band,
                              running=running, draw_kw=draw,
                              price=snap.tariff_now.price if snap.tariff_now is not None else None)
        weekly = run.model.trust.weekly_errors
        run.scorecard.learning = weekly[-1][1] if weekly else None
        # a sensor is present only when it was read: a unit's own reading
        # standing in does not hide that it is lost [D074]
        required = {run.config.temperature_sensor: snap.room_temperature.tag is ValueTag.MEASURED,
                    run.config.humidity_sensor: snap.room_humidity.tag is ValueTag.MEASURED}
        required.update({h.climate: h.available for h in snap.heads})
        run.availability.update(required)
        off_sent = any(r.ok and r.service == "set_hvac_mode" and r.data.get("hvac_mode") == "off" for r in records)
        return run.warnings.to_say(
            result.warnings,
            room=run.config,
            engine=self._engine(),
            automatic=run.state.choices.automatic_control,
            waiting_for_unit=any(not h.available for h in snap.heads),
            asleep=comfort.sleeping,
            unit_on_at_start=any(h.available and h.mode not in (None, "off") for h in snap.heads),
            held_off=result.held_off,
            off_sent=off_sent,
        )

    def _midnight(self, day: str) -> None:
        """Every day at midnight: the scorecard and the model's day [Prop].
        The arbiter's daily comparison needs the learned controller's
        policy, which does not exist yet (4.9) [NYE], so it is not run."""
        for run in self.rooms.values():
            run.state.yesterday = run.scorecard.end_of_day()
            run.model.end_of_day(day)
        self.memory.learned()

    # ------------------------------------------------------------------
    # Speech and repairs
    # ------------------------------------------------------------------

    def _engine(self) -> str | None:
        """The speech engine chosen; with none chosen, the only one Home
        Assistant has [D069]."""
        if self.house.tts_engine is not None:
            return self.house.tts_engine
        engines = self.hass.states.async_entity_ids(TTS_DOMAIN)
        return engines[0] if len(engines) == 1 else None

    def words(self, key: str, placeholders: Mapping[str, str]) -> str:
        """The text of a spoken warning, from strings.json."""
        text = self._words.get(f"component.{DOMAIN}.exceptions.spoken_{key}.message", key)
        try:
            return text.format(**placeholders)
        except (KeyError, IndexError, ValueError):
            return text

    async def _speak(self, s: Speech) -> None:
        """tts.speak; a failure is logged once, until speech works again
        [D069]."""
        try:
            await self.hass.services.async_call(
                TTS_DOMAIN, TTS_SERVICE,
                {ATTR_ENTITY_ID: s.engine, "media_player_entity_id": list(s.media_players),
                 "message": self.words(s.key, s.placeholders)},
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            if not self._tts_failed:
                _LOGGER.warning("A spoken warning could not be said through %s: %s", s.engine, err)
            self._tts_failed = True
            return
        if self._tts_failed:
            _LOGGER.info("Spoken warnings work again through %s", s.engine)
        self._tts_failed = False

    def _repairs(self, now: datetime, shared: Mapping[str, tuple[str, ...]]) -> None:
        """Raise each repair while its cause stands; clear it when it goes
        [R18]."""
        facts = HouseFacts(
            tariff_configured=self.house.tariff_entry is not None,
            tariff_error=self.tariff.error,
            unknown_constraints=self.tariff.unknown,
            speech_wanted=any(r.config.announce and r.config.announce_through for r in self.rooms.values()),
            speech_engine=self._engine(),
            speech_engines_available=len(self.hass.states.async_entity_ids(TTS_DOMAIN)),
            solar_forecast_configured=bool(self.house.solar_forecast_sources),
            solar_forecast_error=self.solar_error,
            grid_sign_contradicted=self.grid.sign_repair if self.grid is not None else False,
            grid_import_kwh=self.grid.import_repair_kwh if self.grid is not None else None,
            shared_climates=shared,
        )
        issues: list[Issue] = house_issues(facts)
        # until Home Assistant has started, a sensor whose integration loads
        # later has no state yet and is not gone [HA]
        started = self.hass.state is CoreState.running
        gone_now = {e for run in self.rooms.values()
                    for e in (run.config.temperature_sensor, run.config.humidity_sensor)
                    if started and _gone(self.hass, e)}
        self._gone_since = {e: self._gone_since.get(e, now) for e in gone_now}
        for run in self.rooms.values():
            # gone for a quarter hour, so a platform that is only retrying,
            # which leaves its config entry loaded, raises nothing [D046][Set]
            missing = tuple(e for e in (run.config.temperature_sensor, run.config.humidity_sensor)
                            if e in gone_now and (now - self._gone_since[e]).total_seconds() >= ROOM_SENSOR_GONE_REPAIR_S)
            issues += room_issues(RoomFacts(run.config, missing, run.answers.last_correction, run.learning_since,
                                            run.answers.last_reason), now)
        wanted = {i.issue_id: (i.key, tuple(sorted(i.placeholders.items()))) for i in issues}
        for issue_id in self._issues.keys() - wanted.keys():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        for issue_id, (key, placeholders) in wanted.items():
            if self._issues.get(issue_id) == (key, placeholders):
                continue
            # new, or its wording or figures changed: Home Assistant updates it in place
            # (helpers/issue_registry.py, async_get_or_create, 2026.10.4) [HA]
            ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key=key, translation_placeholders=dict(placeholders))
        self._issues = wanted

    def clear_repairs(self) -> None:
        """When the entry unloads, its repairs go with it."""
        for issue_id in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._issues = {}

    @staticmethod
    def _room_failed(run: RoomRun) -> None:
        """A room's cycle failed: logged once, with the error, until it runs
        again; its sensors are unavailable meanwhile [R18]."""
        run.failed = True
        if not run.fail_logged:
            _LOGGER.exception("%s: the cycle failed; its sensors are unavailable", run.config.name)
            run.fail_logged = True

    def _log_failures(self) -> None:
        for run in self.rooms.values():
            if not run.failed and run.fail_logged:
                _LOGGER.info("%s: the cycle runs again", run.config.name)
                run.fail_logged = False

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def _publish(self) -> CycleData:
        rooms = {rid: self._room_view(r) for rid, r in self.rooms.items()}
        return CycleData(rooms, self._house_view())

    def _room_view(self, run: RoomRun) -> RoomView:
        snap, comfort = run.snapshot, run.comfort_result
        if run.failed or snap is None or comfort is None:
            return RoomView(False, None, {}, None, None, None, None, None, None, None, None, None, None, False, False,
                            None, None, None, run.scorecard.yesterday, {})
        t, rh = snap.room_temperature.value, snap.room_humidity.value
        ot, orh, ow = snap.outdoor_temperature.value, snap.outdoor_humidity.value, snap.outdoor_wind_ms.value
        band = run.trace.get("band") if isinstance(run.trace.get("band"), Mapping) else None
        arbiter = run.control.arbiter
        return RoomView(
            ok=True,
            mode=mode_state(run.limits_result, comfort),
            trace=run.trace,
            comfort_index=comfort.room_index,
            band_low=comfort.band_low,
            band_high=comfort.band_high,
            position=band.get("position") if band else None,
            target_c=comfort.centre_c,
            commanded_c=run.commanded_c,
            wanted_c=run.wanted_c,
            dew_point_c=dew_point(t, rh) if t is not None and rh is not None else None,
            outdoor_dew_point_c=dew_point(ot, orh) if ot is not None and orh is not None else None,
            outdoor_apparent_c=(outdoor_apparent_temperature(ot, orh, ow or 0.0)
                                if ot is not None and orh is not None else None),
            free_cooling_advised=run.delivery_result.free_cooling_advised if run.delivery_result else False,
            condensation_risk=run.delivery_result.condensation_risk if run.delivery_result else False,
            in_charge=arbiter.in_charge.value,
            plan=run.trace.get("plan"),
            learning=run.scorecard.learning,
            yesterday=run.scorecard.yesterday,
            rl_score={"reason": arbiter.last_reason, "wins": [w for w in arbiter.wins],
                      "since": arbiter.since.isoformat() if arbiter.since else None},
        )

    def _house_view(self) -> HouseView:
        now = dt_util.utcnow()
        snaps = [r.snapshot for r in self.rooms.values() if r.snapshot is not None]
        first = snaps[0] if snaps else None
        intervals = self.tariff.intervals
        current: TariffInterval | None = next((i for i in intervals if i.start <= now < i.end), None)
        if current is not None:
            in_force = current.constraints
        else:
            in_force = ()
        ot = first.outdoor_temperature.value if first else None
        orh = first.outdoor_humidity.value if first else None
        ow = first.outdoor_wind_ms.value if first else None
        peak = max((h.temperature for h in first.forecast if h.temperature is not None), default=None) if first \
            else None
        stale = tuple(sorted({s for snap in snaps for s in snap.stale}))
        total, per, cost, until, reaches = self._demand(now)
        return HouseView(
            demand_kwh=total,
            demand_per_interval=per,
            demand_until=until,
            demand_to_tariff_change=reaches,
            tariff_now=current.price if current is not None else None,
            tariff_rate=current.rate_name if current is not None else None,
            intervals=tuple({"start": i.start.isoformat(), "end": i.end.isoformat(), "rate": i.rate_name,
                             "price": i.price, "constraints": list(i.constraints), "hints": list(i.hints)}
                            for i in intervals),
            constraints=in_force,
            projected_cost=cost,
            outdoor_c=ot,
            outdoor_apparent_c=outdoor_apparent_temperature(ot, orh, ow or 0.0) if ot is not None and orh is not None
            else None,
            outdoor_dew_point_c=dew_point(ot, orh) if ot is not None and orh is not None else None,
            forecast_peak_c=peak,
            stale=stale,
            rooms=len(self.rooms),
        )

    def _demand(self, now: datetime) -> tuple[float | None, tuple[Mapping[str, Any], ...], float | None,
                                              datetime | None, bool]:
        """The demand forecast from each room's plan in force, to the next
        tariff change (economics.py) [D005]."""
        plans = [(r.control.plan, self._draw_for_rate(r)) for r in self.rooms.values()
                 if r.control.plan is not None and not r.failed]
        f = forecast_to_change(plans, self.tariff.intervals, now)
        if f is None:
            return None, (), None, None, False
        rows = tuple({"start": iv.start.isoformat(), "end": iv.end.isoformat(), "rate": iv.rate_name,
                      "kwh": round(kwh, 3)} for iv, kwh in f.per_interval)
        return round(f.kwh, 3), rows, round(f.cost, 2) if f.cost is not None else None, f.until, f.to_change

    def grid_sign_suggestion(self, grid_kw: float | None) -> bool | None:
        """Which way a positive grid reading points, from a moment of grid
        evidence now (economics.py); None when there is none [D038]."""
        snaps = [r.snapshot for r in self.rooms.values() if r.snapshot is not None]
        if not snaps or grid_kw is None:
            return None
        load, solar = snaps[0].house_load_kw.value, snaps[0].solar_kw.value
        if load is None or solar is None or not self._charges:
            return None
        fell = self._charges[-1][1] < self._charges[0][1]
        return grid_evidence(load, solar, fell, grid_kw * 1000.0)

    # ------------------------------------------------------------------
    # The user acts [D064]
    # ------------------------------------------------------------------

    async def async_set_choices(self, room_id: str, action: UserAction, **changes: Any) -> None:
        """Record the choice; Memory saves it within 5 seconds; plan again
        and run the cycle now [D064]."""
        run = self.rooms[room_id]
        choices = replace(run.state.choices, **changes)
        run.state.choices = choices_after_user_action(choices, action)
        run.replan = True
        self.memory.choice()
        await self.async_refresh()

    async def async_user_command(self, room_id: str, cmd: UserCommand, context: Context | None = None) -> None:
        """A room control changed by the user: refused with a translated
        reason while the component runs the room, otherwise sent to every
        head [D049]; then the cycle runs now."""
        run = self.rooms[room_id]
        now = dt_util.utcnow()
        records = await async_user_command(self.hass, run.config, run.state.choices, cmd, log=self.commands,
                                           now=now, context=context)
        for rec in records:
            self._last_command[rec.entity_id] = now
        await self.async_refresh()

    def has_room(self, room_id: str) -> bool:
        return room_id in self.rooms

    # ------------------------------------------------------------------
    # The rooms change (options saved; no reload) [R18]
    # ------------------------------------------------------------------

    async def async_rooms_changed(self) -> None:
        """A room added gets its modules and is seeded; a room edited keeps
        what it has learned; a room removed takes its device with it
        [D031]."""
        now = dt_util.utcnow()
        wanted = {r.room_id: r for r in rooms_from_options(self.config_entry.options)}
        added: list[str] = []
        edited: list[str] = []
        async with self._lock:
            for rid in list(self.rooms):
                if rid not in wanted:
                    del self.rooms[rid]
                    self._remove_device(rid)
                    async_dispatcher_send(self.hass, SIGNAL_ROOM_REMOVED, rid)
            for rid, room in wanted.items():
                if rid in self.rooms:
                    run = self.rooms[rid]
                    if run.config != room:
                        if run.config.name != room.name:
                            self._rename_device(rid, room.name)
                        profile = room.profile or run.state.profile or self._read_profile(room)
                        if profile is not None:
                            run.state.profile = profile
                        run.edited(replace(room, profile=profile) if profile is not None else room)
                        edited.append(rid)
                else:
                    self._add_room(room, now)
                    added.append(rid)
        await self.memory.async_write_now()
        await self.async_refresh()
        for rid in added:
            async_dispatcher_send(self.hass, SIGNAL_ROOM_ADDED, rid)
        for rid in edited:
            async_dispatcher_send(self.hass, SIGNAL_ROOM_EDITED, rid)
        self.start_seeding()

    def start_seeding(self) -> None:
        """Seed each new room from history, in the background."""
        for rid in self.unseeded:
            self.config_entry.async_create_background_task(self.hass, self.async_seed(rid), f"{DOMAIN} seed {rid}")
        self.unseeded = []

    def _rename_device(self, room_id: str, name: str) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier((DOMAIN, room_id), self.config_entry.entry_id)
        if device is not None:
            registry.async_update_device(device.id, name=name)

    def _remove_device(self, room_id: str) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier((DOMAIN, room_id), self.config_entry.entry_id)
        if device is not None:
            registry.async_remove_device(device.id)

    # ------------------------------------------------------------------
    # Learning: seed and reset [Prop]
    # ------------------------------------------------------------------

    async def async_seed(self, room_id: str, *, reset: bool = False) -> None:
        """Run the room's filters over the recorder's history. The replay
        runs on a copy, in the executor, and replaces the room's model when
        it is done, so the cycle is never held for it."""
        run = self.rooms.get(room_id)
        if run is None or run.seeding:
            return
        run.seeding = True
        try:
            if self.hass.is_stopping:
                return
            end = dt_util.utcnow()
            days = recorder_keep_days(self.hass, RECORDER_KEEP_DAYS_DEFAULT)
            start = end - timedelta(days=days)
            ids = seed_entities(run.config, self.house)
            try:
                histories = await async_read_history(self.hass, ids, start, end)
            except (HomeAssistantError, KeyError) as err:
                _LOGGER.warning("%s: the recorder's history could not be read: %s", run.config.name, err)
                return
            # always from the starting figures: replaying old readings onto a
            # filter already at now would learn them twice and out of time
            # order, so seeding replaces what the room has learned [Prop]
            model = RoomModel()
            first = min((s.last_changed for h in histories.values() for s in h), default=None)
            if first is None:
                if reset:
                    async with self._lock:
                        run.state.model = model
                        run.learning_since = end
                        run.replan = True
                return
            result = await self.hass.async_add_executor_job(self._replay, model, run.config, histories,
                                                            max(first, start), end)
            if self._closing or room_id not in self.rooms:
                return
            if (model.heat is None or result.corrections == 0) and not reset:
                # the history taught nothing: what the room has learned stands
                _LOGGER.info("%s: seeded from %.1f days of history, which taught nothing; what it has learned "
                             "stands", run.config.name, result.days)
                return
            async with self._lock:
                run.state.model = model
                run.learning_since = end
                run.replan = True
            self.memory.learned()
            _LOGGER.info("%s: seeded from %.1f days of history, %d corrections", run.config.name, result.days,
                         result.corrections)
        finally:
            run.seeding = False

    def _replay(self, model: RoomModel, room: RoomConfig, histories: Mapping[str, Sequence[RawState]],
                start: datetime, end: datetime) -> Any:
        return replay(model, room=room, house=self.house, histories=histories, start=start, end=end,
                      tz=dt_util.get_default_time_zone(),
                      location=(self.hass.config.latitude, self.hass.config.longitude),
                      system_temperature_unit=self.hass.config.units.temperature_unit,
                      stop=lambda: self._closing)

    # ------------------------------------------------------------------
    # Diagnostics [D052]
    # ------------------------------------------------------------------

    @callback
    def diagnostics(self) -> dict[str, Any]:
        from .memory import choices_to_dict, profile_to_dict, room_to_dict

        rooms: dict[str, Any] = {}
        for rid, run in self.rooms.items():
            r = run.config
            rooms[rid] = {
                "settings": settings_dict(r),
                "choices": choices_to_dict(run.state.choices),
                "profile": profile_to_dict(r.profile) if r.profile else None,
                "learned": room_to_dict(run.state),
                "plan": run.trace.get("plan"),
                "in_charge": run.control.arbiter.in_charge.value,
                "arbiter": run.control.arbiter.as_dict(),
                "yesterday": run.scorecard.as_dict(),
                "compressors": run.trace.get("compressors"),
                "trace": run.trace,
                "failed": run.failed,
            }
        return {
            "rooms": rooms,
            "last_command": {e: t.isoformat() for e, t in self._last_command.items()},
            "commands": [
                {"time": c.time.isoformat(), "room_id": c.room_id, "entity_id": c.entity_id, "service": c.service,
                 "data": dict(c.data), "rate": c.rate, "room_temperature": c.room_temperature,
                 "centre_c": c.centre_c, "by_user": c.by_user, "ok": c.ok, "error": c.error}
                for c in self.commands.records
            ],
            "draw": self.house_state.draw.as_dict(),
            "power_budget": {"allowance_kw": self.budget.last_allowance, "reason": self.budget.last_reason},
            "tariff": {"error": self.tariff.error, "unknown": sorted(self.tariff.unknown),
                       "read_at": self.tariff_read_at.isoformat() if self.tariff_read_at else None,
                       "intervals": list(self.data.house.intervals) if self.data else []},
            "forecast": {"hours": len(self.forecast), "error": self.forecast_error,
                         "solar_hours": len(self.solar or []), "solar_error": self.solar_error},
            "demand": {"kwh": self.data.house.demand_kwh if self.data else None,
                       "until": self.data.house.demand_until.isoformat()
                       if self.data and self.data.house.demand_until else None,
                       "per_interval": list(self.data.house.demand_per_interval) if self.data else []},
        }


def settings_dict(r: RoomConfig) -> dict[str, Any]:
    """Every setting of a room, unset ones stated as unset [D048]."""

    def v(x: Any) -> Any:
        if x is None or x == ():
            return "unset"
        if isinstance(x, tuple):
            return list(x)
        return x

    return {
        "name": r.name,
        "air_conditioners": [{"climate": h.climate, "outdoor_unit": v(h.outdoor_unit),
                              "frequency_sensor": v(h.frequency_sensor)} for h in r.heads],
        "temperature_sensor": r.temperature_sensor,
        "humidity_sensor": r.humidity_sensor,
        "presence_sensor": v(r.presence_sensor),
        "sleep_schedule": v(r.sleep_schedule),
        "sun_sensor": v(r.sun_sensor),
        "openings": v(r.openings),
        "blinds": v(r.blinds),
        "allow_blind_control": r.allow_blind_control,
        "window_facing": v(r.window_facing),
        "overhang_projection_m": v(r.overhang_projection_m),
        "overhang_height_m": v(r.overhang_height_m),
        "occupied_after_min": r.occupied_after_s / 60.0,
        "empty_after_min": r.empty_after_s / 60.0,
        "warning_grace_min": r.warning_grace_s / 60.0,
        "announce": r.announce,
        "announce_through": v(r.announce_through),
        "opening_grace_min": r.opening_grace_s / 60.0,
        "lockout_reason": v(r.lockout_reason),
        "occupied_band": list(r.occupied_band),
        "sleep_band": list(r.sleep_band),
        "precool_band": list(r.precool_band),
        "power_management": r.power_management.value,
    }

