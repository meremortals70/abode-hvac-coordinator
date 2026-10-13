"""The coordinator.

Reads Home Assistant state, hands it to the pure evaluator in `modes.py`, and
publishes the resulting traces. Every decision is made in the pure modules; this
file gathers inputs and manages lifecycle only.

Actuation itself lives in `actuator.py`. This file gathers inputs, runs the
evaluator and hands the resulting decision on.
"""

from __future__ import annotations

import functools
import statistics
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from math import ceil
from typing import TYPE_CHECKING, Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfSpeed,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryError,
    HomeAssistantError,
    ServiceNotFound,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import SpeedConverter

from .actuator import (
    Actuator,
    mean_cover_position,
    profile_from_states,
    supported_hvac_modes,
)
from .capabilities import RoomCapabilities
from .const import (
    COAST_HORIZON_HOURS,
    CONF_ALLOW_COMFORT_REDUCTION,
    CONF_ALLOW_COVER_CONTROL,
    CONF_CAPABILITIES,
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_DISCHARGE_KW,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CLIMATE_ENTITIES,
    CONF_CLIMATE_ENTITY,
    CONF_COVER_ENTITIES,
    CONF_DIRECT_SUN_ENTITY,
    CONF_FAN_ENTITY,
    CONF_GRID_ENTITY,
    CONF_GRID_SIGN,
    CONF_HEAD_GROUPS,
    CONF_HEAT_LOAD_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OPENING_GRACE,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_OUTDOOR_WIND_ENTITY,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_PRESENCE_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_SOLAR_FORECAST_ENTRIES,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TARIFF_ENTRY_ID,
    CONF_TEMPERATURE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_TTS_ENGINE,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_DIRECTION,
    DOMAIN,
    EVALUATION_INTERVAL,
    ISSUE_ANNOUNCEMENTS_UNAVAILABLE,
    ISSUE_FORECAST_UNAVAILABLE,
    ISSUE_GRID_REQUIRED,
    ISSUE_GRID_SIGN_CONTRADICTED,
    ISSUE_MISSING_COMFORT_INPUTS,
    ISSUE_NO_BANDS,
    ISSUE_POWER_SHORTFALL,
    ISSUE_SHARED_CLIMATE_ENTITY,
    ISSUE_SOLAR_FORECAST_UNAVAILABLE,
    ISSUE_TARIFF_UNAVAILABLE,
    ISSUE_UNRECOGNISED_CONSTRAINT,
    LOGGER,
    POWER_MANAGEMENT_ENFORCED,
    POWER_MANAGEMENT_GUIDANCE,
    POWER_MANAGEMENT_OFF,
    PRECOOL_DEMAND_MARGIN_C,
    STARTUP_FETCH_ATTEMPTS,
    STARTUP_FETCH_DELAY,
    PROFILE_UNREAD_REASON,
    SOLAR_FORECAST_REFRESH_INTERVAL,
    SWITCHED_OFF_REASON,
    TARIFF_DOMAIN,
    TARIFF_HORIZON_HOURS,
    TARIFF_REFRESH_INTERVAL,
    TARIFF_RESOLUTION_MINUTES,
    TARIFF_SERVICE_GET_INTERVALS,
    WEATHER_DOMAIN,
    WEATHER_REFRESH_INTERVAL,
    WEATHER_SERVICE_GET_FORECASTS,
)
from .forecast import (
    ASSUMED_UNIT_KW,
    DEFAULT_HORIZON_HOURS,
    DemandForecast,
    RoomForecastInput,
    build_forecast,
)
from .forms import power_management_from_raw
from .grace import (
    Announcement,
    GraceSettings,
    GraceState,
    OpeningWarningState,
    evaluate_grace,
    evaluate_opening_warnings,
)
from .hci import (
    ComfortBand,
    apparent_temperature,
    comfort_index,
    dry_bulb_for_index,
    radiant_load,
)
from .models import (
    ActuatorStep,
    ComfortMode,
    DecisionTrace,
    Mode,
    RoomConfig,
    RoomInputs,
)
from .modes import (
    DEFAULT_OPENING_GRACE_MINUTES,
    FREE_COOLING_WAIT,
    UNAIDED_RETURN_FALLBACK_MINUTES,
    UNAIDED_RETURN_MARGIN_MINUTES,
    WORKING_MODES,
    comfort_mode_after,
    comfort_mode_refused,
    evaluate_room,
    room_reads_empty,
    sleeping,
)
from .power import (
    GRID_SIGN_IMPORTING,
    OFF_HOLD,
    READING_AVERAGE_WINDOW,
    battery_available_kwh,
    budget_allowance_kw,
    ceiling_is_binding,
    derive_battery_w,
    energy_dashboard_solar_forecasts,
    forecast_allowance_kw,
    forecast_slices,
    held_ceiling_bin,
    held_setpoint,
    hold_before_off,
    implied_sign,
    mean_recent,
    normalise_grid_import_w,
    plant_allowance_kw,
    setpoint_direction,
    solar_points_from_forecast,
)
from .psychro import dew_point_c
from .regulate import (
    CompressorState,
    RegulatorState,
    PidSetpoint,
    SetpointLimits,
    arbitrate_cycling,
    commanded_setpoint,
    effective_deadband,
    integrate,
    note_transition,
    pid_setpoint,
    quantise_setpoint,
    track_room_rate,
    wants_running,
)
from .scheduling import HEADING_HOME_EXPIRY, PreconditionPlan, plan_precondition
from .staleness import (
    CONTACT_TOLERANCE,
    INDOOR_TOLERANCE,
    OUTDOOR_TOLERANCE,
    POWER_TOLERANCE,
    PRESENCE_TOLERANCE,
    assess,
)
from .store import ModelStore
from .sun import azimuth_for_direction, solar_position, sun_on_window
from .tariff import (
    CONSTRAINT_NO_GRID_IMPORT,
    CONSTRAINT_PRECOOL_OPPORTUNITY,
    Interval,
    TariffPayloadError,
    TariffSeries,
    hours_until_cheaper_interval,
)
from .thermal import (
    APPROACH_AT_SETPOINT_C,
    APPROACH_CLOSE_C,
    APPROACH_WORKING_C,
    BIN_NAMES,
    MIN_DRAW_QUALITY,
    DrawModel,
    Observation,
    PROJECTION_STEP_HOURS,
    REBOUND_HOURS,
    ThermalModel,
    approach_bin,
    unaided_outlook,
)
from .thermal import (
    MAX_INTERVAL_HOURS as MAX_LEARNING_INTERVAL_HOURS,
)
from .thermal import (
    MIN_INTERVAL_HOURS as MIN_LEARNING_INTERVAL_HOURS,
)
from .weather import (
    DEMAND_LOOKAHEAD,
    ForecastPayloadError,
    WeatherTrajectory,
    demand_ahead,
)

if TYPE_CHECKING:
    from . import HvacConfigEntry

#: State strings that carry no reading, as distinct from a number.
_NON_NUMERIC = frozenset({"unknown", "unavailable", "none", ""})

#: 0.8.9, finding 14. How much house-load history to keep, so a compressor
#: state change can be bracketed on both sides even at a slow evaluation
#: cadence. Trimmed by age, not by count.
DRAW_HISTORY_WINDOW = timedelta(minutes=10)
#: How far back before a state change to average the settled "before" period.
DRAW_PRE_WINDOW = timedelta(minutes=2)
#: An inverter ramps rather than steps. This is how long after the change is
#: noticed before the "after" period is considered settled enough to sample.
DRAW_RAMP_ALLOWANCE = timedelta(minutes=2)
#: How long after the ramp allowance to keep averaging the "after" period.
DRAW_POST_WINDOW = timedelta(minutes=3)
#: Fewer house-load readings than this in a window scores it low confidence
#: rather than discarding it — nothing is rejected, only trusted less.
DRAW_MIN_SAMPLES = 2
#: A candidate older than this without settling is dropped rather than kept
#: waiting indefinitely for a house that never settles.
DRAW_CANDIDATE_MAX_AGE = timedelta(minutes=8)
#: House-load noise, in watts, against which a window's own spread is scored
#: for quietness. Not a threshold — it scales the confidence curve.
DRAW_NOISE_REFERENCE_W = 150.0
#: Readings in a window at or above this count are scored full confidence for
#: how many landed, rather than confidence climbing forever with more.
DRAW_TARGET_SAMPLES = 6

#: 0.8.10, finding 11. Consecutive evaluations the live grid reading may
#: disagree with the stored sign convention before it is named as a repair
#: issue. A single odd reading — a momentary spike, a noisy sensor — is not
#: a contradiction; a run of them across several minutes is.
_GRID_SIGN_DISAGREEMENT_THRESHOLD = 10


@dataclass(slots=True)
class _DrawCandidate:
    """One outdoor unit mid-transition, waiting to be scored.

    0.8.9, finding 14. Opened when exactly one group's compressor changes
    state and closed once the post-change window has settled, or dropped if
    it goes stale first. `started` is when the transition was noticed —
    `_house_load_window` for the pre-change period looks back from it.
    """

    group: str
    started: datetime
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class _PowerContext:
    """The shared readings the power budget runs against.

    Read once per evaluation cycle. `engaged` is False whenever any of the
    five house-level fields is missing — the feature is opt-in, and with any
    one of them absent no room is throttled.
    """

    engaged: bool
    battery_soc_percent: float | None = None
    battery_capacity_kwh: float | None = None
    solar_w: float | None = None
    house_load_w: float | None = None
    reserve_margin_kwh: float | None = None
    #: 0.8.10, finding 11. None whenever no grid entity is configured, or the
    #: reading is missing or stale — every consumer treats that exactly like
    #: an unconfigured feature: the budget falls back to the energy figure
    #: alone, and no breach is measured.
    grid_import_w: float | None = None
    #: Derived, not read: `house_load - solar - grid_import`. Positive means
    #: the battery is discharging.
    battery_w: float | None = None


def _optional_float(value: object) -> float | None:
    """A config-entry number field, tolerating None and an empty string.

    The number selector round-trips through JSON storage, so this may arrive
    as an int, a float, or a string depending on how it was last saved.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class HvacCoordinator(DataUpdateCoordinator[dict[str, DecisionTrace]]):
    """Evaluates every room and publishes its decision trace."""

    config_entry: HvacConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HvacConfigEntry,
        store: ModelStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=EVALUATION_INTERVAL,
        )
        self.store = store
        self.actuator = Actuator(hass, self)
        self.outdoor_entity_id: str | None = config_entry.options.get(
            CONF_OUTDOOR_TEMPERATURE_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
        )
        self.outdoor_humidity_entity_id: str | None = config_entry.options.get(
            CONF_OUTDOOR_HUMIDITY_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_HUMIDITY_ENTITY),
        )
        self.outdoor_wind_entity_id: str | None = config_entry.options.get(
            CONF_OUTDOOR_WIND_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_WIND_ENTITY),
        )
        #: Layer 2 state, one per room. Not persisted: a trim is only valid
        #: for the conditions that produced it.
        self._regulators: dict[str, RegulatorState] = {}
        #: Cycling state per outdoor unit, not per room. See CompressorState.
        self._compressors: dict[str, CompressorState] = {}
        #: What each room asked of its compressor last cycle, so a room
        #: reaching its band does not read as the whole outdoor unit
        #: stopping while another room on it is still calling.
        self._room_wants: dict[str, bool] = {}
        #: The mode each room was in last evaluation, and when it changed, so
        #: the band can ramp across a sleep transition instead of stepping.
        self._previous_mode: dict[str, Mode] = {}
        self._mode_changed_at: dict[str, datetime] = {}
        #: Feeds reported stale this evaluation, per room, for the trace.
        self._stale: dict[str, list[str]] = {}
        #: Why a heading-home room is not actuating yet, for the trace.
        self._precondition_reason: dict[str, str] = {}
        #: Which weather entity supplies the hourly forecast, and the
        #: trajectory read from it. None means precool falls back to comparing
        #: current conditions.
        self.weather_entity_id: str | None = config_entry.options.get(
            CONF_WEATHER_ENTITY, config_entry.data.get(CONF_WEATHER_ENTITY)
        )
        #: 0.9.5, DR-069. The chosen text-to-speech engine, or None to use the
        #: only one Home Assistant has.
        self.tts_engine_entity_id: str | None = config_entry.options.get(
            CONF_TTS_ENGINE, config_entry.data.get(CONF_TTS_ENGINE)
        )
        #: Each distinct announcement failure, so it is logged once.
        self._announce_failures: set[str] = set()
        self.trajectory: WeatherTrajectory | None = None
        #: Power-aware operation. All five optional and read the same way as
        #: every other house-level feed; the decision only engages once every
        #: one of them is set — see `_compute_power_context`.
        self.battery_soc_entity_id: str | None = config_entry.options.get(
            CONF_BATTERY_SOC_ENTITY, config_entry.data.get(CONF_BATTERY_SOC_ENTITY)
        )
        self.battery_capacity_kwh: float | None = _optional_float(
            config_entry.options.get(
                CONF_BATTERY_CAPACITY_KWH,
                config_entry.data.get(CONF_BATTERY_CAPACITY_KWH),
            )
        )
        self.solar_entity_id: str | None = config_entry.options.get(
            CONF_SOLAR_POWER_ENTITY, config_entry.data.get(CONF_SOLAR_POWER_ENTITY)
        )
        self.house_load_entity_id: str | None = config_entry.options.get(
            CONF_HOUSE_LOAD_ENTITY, config_entry.data.get(CONF_HOUSE_LOAD_ENTITY)
        )
        self.reserve_margin_kwh: float | None = _optional_float(
            config_entry.options.get(
                CONF_RESERVE_MARGIN_KWH,
                config_entry.data.get(CONF_RESERVE_MARGIN_KWH),
            )
        )
        #: The battery's rated maximum discharge power, in kW. Entered at
        #: setup, not learned — it's a nameplate spec (5 kW for a Powerwall
        #: 2), not a quantity that varies or needs tracking over time.
        self.battery_max_discharge_kw: float | None = _optional_float(
            config_entry.options.get(
                CONF_BATTERY_MAX_DISCHARGE_KW,
                config_entry.data.get(CONF_BATTERY_MAX_DISCHARGE_KW),
            )
        )
        #: 0.8.10, finding 11. Optional, and joins the five above. Without
        #: it the budget still runs on the energy figure alone — see
        #: `allowable_draw_kw` — it just cannot measure a breach afterward.
        self.grid_entity_id: str | None = config_entry.options.get(
            CONF_GRID_ENTITY, config_entry.data.get(CONF_GRID_ENTITY)
        )
        self.grid_sign: str | None = config_entry.options.get(
            CONF_GRID_SIGN, config_entry.data.get(CONF_GRID_SIGN)
        )
        #: 0.9.3, DR-061. Which solar forecasts the budget reads. None means
        #: whatever the Energy dashboard uses; an empty list means none.
        raw_forecasts = config_entry.options.get(
            CONF_SOLAR_FORECAST_ENTRIES,
            config_entry.data.get(CONF_SOLAR_FORECAST_ENTRIES),
        )
        self.solar_forecast_entry_ids: list[str] | None = (
            None if raw_forecasts is None else [str(item) for item in raw_forecasts]
        )
        #: The forecast read from them: (period start, Wh), summed across the
        #: sources. None until read, or where nothing is selected.
        self._solar_points: list[tuple[datetime, float]] | None = None
        #: The sources actually read last time, for diagnostics.
        self._solar_sources: list[str] = []
        #: Each room's ceiling bin and when it last changed, for the
        #: hysteresis in `power.held_ceiling_bin` (DR-062).
        self._ceiling_bins: dict[str, tuple[int | None, datetime]] = {}
        #: 0.9.4, DR-067. When each enforced room's budget first fell short,
        #: so it is turned off only once the shortfall has lasted `OFF_HOLD`.
        self._shortfall_since: dict[str, datetime] = {}
        #: 0.9.4, DR-067. The house load and solar readings of the last
        #: `READING_AVERAGE_WINDOW`, so the budget runs on averages.
        self._budget_house_samples: deque[tuple[datetime, float]] = deque()
        self._budget_solar_samples: deque[tuple[datetime, float]] = deque()
        #: 0.9.4, DR-067. Per room, the house load measured while that room's
        #: unit was not running: the rest of the house, as measured. Not added
        #: to while the room runs, so the room's own draw never enters it.
        self._base_load_samples: dict[str, deque[tuple[datetime, float]]] = {}
        #: How many consecutive evaluations the live grid reading has
        #: disagreed with the stored sign convention, and the repair issue
        #: this raises once persistent enough. Reset the moment the evidence
        #: agrees again — a single odd reading is not a contradiction.
        self._grid_sign_disagreements = 0
        #: The `no_grid_import` window currently being measured, its start
        #: time as the key, and the kWh of import integrated into it so far.
        self._breach_window_start: datetime | None = None
        self._breach_kwh = 0.0
        #: When grid import was last sampled inside the window, so each
        #: sample is weighted by the time it actually covers.
        self._breach_sampled_at: datetime | None = None
        self._last_breach_reported_kwh: float | None = None
        #: Why each room is or is not precooling, for the trace.
        self._demand_reason: dict[str, str] = {}
        #: Learned thermal behaviour, one per room, restored from the store.
        self.models: dict[str, ThermalModel] = {}
        #: The last reading of each room, so the next evaluation can measure
        #: what actually happened over the interval and learn from it. The
        #: sixth element is the commanded setpoint in force at the anchor
        #: (0.8.9, finding 9) — a change to it resets the anchor the same way
        #: a compressor direction change already does, because the approach
        #: bin an interval is attributed to is meaningless once what the room
        #: was being asked to reach has moved.
        self._previous: dict[
            str, tuple[datetime, float, float, int, bool, float | None]
        ] = {}
        #: When each room's compressor last stopped cooling or drying, and
        #: whether it was cooling or drying at the last evaluation. None while
        #: it runs. 0.9.3, DR-060: the room takes heat and water back from the
        #: coil for a while after a stop.
        self._stopped_at: dict[str, datetime | None] = {}
        self._was_cooling: dict[str, bool] = {}
        #: Learned per-outdoor-unit draw (0.8.9, finding 14), keyed by group —
        #: two rooms sharing a compressor share one model.
        self._draw: dict[str, DrawModel] = {}
        #: A short rolling window of house-load readings, for bracketing a
        #: compressor state change on both sides.
        self._house_load_samples: deque[tuple[datetime, float]] = deque()
        #: Outdoor units currently mid-transition, waiting to be scored.
        self._draw_pending: dict[str, _DrawCandidate] = {}
        #: The published demand forecast. Never contains vendor concepts.
        self.forecast: DemandForecast | None = None
        #: Occupancy grace, one per room. Raw presence is the wrong signal for
        #: a compressor: someone dropping a laptop off should not start it, and
        #: someone signing for a delivery should not stop it.
        self._grace: dict[str, GraceState] = {}
        #: Why each room is considered occupied or not, for the trace.
        self._grace_reason: dict[str, str] = {}
        #: Whether free cooling was advised last cycle, per room. Announced
        #: only on the rising edge — the moment it becomes true — not every
        #: cycle it stays true, the same principle the grace warnings use.
        self._free_cooling_advised: dict[str, bool] = {}
        #: 0.9.6, DR-073. When outdoor air was first judged better, per room,
        #: for as long as it stays better.
        self._free_cooling_since: dict[str, datetime] = {}
        #: 0.9.6, DR-077. A final warning ("turning ... off") waiting for the
        #: stop it announces to actually be sent.
        self._deferred_final: dict[str, Announcement] = {}
        #: 0.9.6, DR-077. The mode each room's unit was in at the start of the
        #: evaluation, before anything was sent: a warning about a running
        #: unit is only true of one that was on.
        self._unit_mode_seen: dict[str, str | None] = {}
        #: 0.9.6, DR-074. Why a room is reading its unit's own sensor.
        self._fallback_reason: dict[str, str] = {}
        #: 0.9.6, DR-079. Whether the sun was up at the last evaluation, per
        #: room, so a chosen Sleep ends at sunrise in a room with no schedule.
        self._sun_was_up: dict[str, bool | None] = {}
        #: 0.9.6, DR-080. Why a heading-home request ended, for the trace.
        self._heading_home_reason: dict[str, str] = {}
        #: Announcements raised this evaluation, dispatched after it.
        self._pending_announcements: list[tuple[RoomConfig, Announcement]] = []
        #: Entities currently logged as unavailable, so each transition is
        #: recorded once rather than on every evaluation.
        self._unavailable: set[str] = set()
        self.rooms: dict[str, RoomConfig] = _rooms_from_entry(config_entry)
        #: Which Abode Power Tariffs entry supplies the plan. None means no
        #: tariff: the controller holds comfort and nothing window-driven.
        self.tariff_entry_id: str | None = config_entry.options.get(
            CONF_TARIFF_ENTRY_ID,
            config_entry.data.get(CONF_TARIFF_ENTRY_ID),
        )
        #: The forward interval series, fetched from that entry. The plan
        #: itself is not held here and is never configured here.
        self.tariff: TariffSeries | None = None
        #: Rooms that have a device in the registry, for stale removal. Seeded
        #: from the registry rather than from the previous evaluation: the
        #: coordinator is rebuilt on every options change, so anything held
        #: only in memory would forget the room that was just deleted and its
        #: device would be orphaned.
        self.previous_rooms: set[str] = set()
        #: Whether the startup fetch sequence is still trying for each feed.
        #: Set when the sequence starts; an unconfigured feed is never pending.
        self._pending_tariff = False
        self._pending_forecast = False
        self._pending_solar = False
        #: Heading-home requests, per room, with the deadline if one was given.
        #: There is no target: a heading-home room is driven to its comfort band.
        self._heading_home: dict[str, datetime | None] = {}
        #: Rooms the user has switched off. Held off: the unit is commanded
        #: off every cycle and nothing in the room's decision can start it.
        #: Read from the store, not from entity state, so it is already known
        #: when the first evaluation runs. DR-047.
        self._switched_off: set[str] = store.switched_off() & set(self.rooms)
        #: Rooms whose Automatic vane control the user has switched off: the
        #: coordinator never sends a vane command to them. Read from the store
        #: for the same reason as the line above. DR-049.
        self._manual_vanes: set[str] = store.manual_vanes() & set(self.rooms)
        #: Each room's Comfort mode where it is not Schedule (DR-058). Read
        #: from the store for the same reason as the two lines above: a room
        #: put on Sleep must already be on Sleep in the first evaluation.
        self._comfort_modes: dict[str, ComfortMode] = _comfort_modes_from_store(
            store.comfort_modes(), set(self.rooms)
        )
        #: Whether each room's sleep schedule was on at the last evaluation,
        #: so Awake ends when the schedule goes off rather than whenever it
        #: happens to be off. Unknown until a room has been read once.
        self._schedule_was_on: dict[str, bool] = {}
        #: Whether each room read empty at the last evaluation, so a Comfort
        #: mode chosen for an empty room can be refused.
        self._room_empty: dict[str, bool] = {}
        #: The raw presence reading behind each room's graced presence, read
        #: once per evaluation by `_graced_presence`.
        self._raw_presence: dict[str, bool | None] = {}
        #: Why a room's Comfort mode has just gone back to Schedule, for the
        #: trace of the evaluation in which it happened.
        self._comfort_mode_reason: dict[str, str] = {}
        #: Opening warnings already spoken for each room's current opening.
        #: DR-050.
        self._opening_warnings: dict[str, OpeningWarningState] = {}
        #: The last mode each room was evaluated in, for the controls.
        self._last_modes: dict[str, Mode] = {}
        #: Rooms whose units had nothing to read the first time they were
        #: tried, named once in the log rather than every cycle.
        self._profile_unread: set[str] = set()
        #: Each room's last-solved dry-bulb target, one cycle behind. The
        #: power-aware compressor check needs a target to project energy need
        #: against, but the target itself is solved inside evaluate_room —
        #: after the actuator decision that needs it. A target thirty seconds
        #: stale is a fine input to a battery-sufficiency check; it is not
        #: used for anything that needs to be current.
        self._last_target: dict[str, float] = {}
        #: The shared battery/solar/house-load readings, read once per cycle
        #: rather than once per room — every room's power check in a given
        #: pass sees the same figures.
        self._power_context: _PowerContext | None = None
        #: Which attribute answered the compressor-running question for each
        #: room last cycle: "hvac_action" or, where the entity publishes none,
        #: "hvac_mode". Diagnostics only.
        self._action_source: dict[str, str] = {}

    async def async_prepare(self) -> None:
        """Subscribe to entities, raise any issues, and start the first fetch.

        The tariff and the forecast are *not* fetched inline here. Both call a
        service belonging to another integration, and at boot that integration
        may not have registered it yet. Awaiting a retry sequence on the setup
        path would hold the config entry open for as long as the retries take
        and produce a slow-setup warning, so the sequence runs as a background
        task and setup returns immediately.

        The controller starts with no tariff and no trajectory. Both are
        supported states, not degraded ones: rooms are still held to their
        bands, and precool falls back to comparing current conditions.
        """
        if self.weather_entity_id:
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._async_forecast_tick,
                    WEATHER_REFRESH_INTERVAL,
                )
            )
        if self.tariff_entry_id:
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._async_tariff_tick,
                    TARIFF_REFRESH_INTERVAL,
                )
            )
        if self._power_configured():
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._async_solar_tick,
                    SOLAR_FORECAST_REFRESH_INTERVAL,
                )
            )
        self._async_check_configuration()
        self._load_models()
        self.previous_rooms = self._async_rooms_in_registry()

        # The climate entities are watched too: their capabilities and
        # availability feed the decision, not just their state.
        if watched := sorted(_watched_entities(self.rooms)):
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, watched, self._handle_state_change
                )
            )

        self._pending_tariff = self.tariff_entry_id is not None
        self._pending_forecast = self.weather_entity_id is not None
        self._pending_solar = self._power_configured()
        if self._pending_tariff or self._pending_forecast or self._pending_solar:
            self._async_schedule_startup_fetch(1)

    @callback
    def _async_schedule_startup_fetch(self, attempt: int) -> None:
        """Book one startup fetch attempt, one delay from now.

        `async_call_later` rather than a sleeping task: it is cancelled with
        the config entry, and it moves with the test clock.
        """
        self.config_entry.async_on_unload(
            async_call_later(
                self.hass,
                STARTUP_FETCH_DELAY,
                functools.partial(self._async_startup_fetch, attempt),
            )
        )

    async def _async_startup_fetch(self, attempt: int, _now: datetime) -> None:
        """Fetch the tariff and the forecast, retrying while boot finishes.

        Neither feed is required, and an unconfigured one is never pending.

        Every attempt but the last is quiet. A failure during boot is almost
        always the other integration not having registered its service yet,
        and reporting a race as a fault trains the user to ignore the report.
        When the last attempt fails, the warning and the repair issue are
        raised exactly as they were before.
        """
        final = attempt >= STARTUP_FETCH_ATTEMPTS
        gained = False

        if self._pending_tariff and await self._async_refresh_tariff(quiet=not final):
            self._pending_tariff = False
            gained = True
            # The constraint check reads the series, so it can only say
            # anything once there is one.
            self._async_check_configuration()

        if self._pending_forecast and await self._async_refresh_forecast(
            quiet=not final
        ):
            self._pending_forecast = False
            gained = True

        if self._pending_solar and await self._async_refresh_solar_forecast(
            quiet=not final
        ):
            self._pending_solar = False
            gained = True

        if gained:
            await self.async_request_refresh()

        if not final and (
            self._pending_tariff or self._pending_forecast or self._pending_solar
        ):
            self._async_schedule_startup_fetch(attempt + 1)

    async def _async_tariff_tick(self, now: datetime) -> None:
        """Refetch the series on the interval timer, then re-evaluate."""
        await self._async_refresh_tariff()
        self._async_check_configuration()
        await self.async_request_refresh()

    async def _async_refresh_tariff(self, *, quiet: bool = False) -> bool:
        """Fetch the forward interval series from Abode Power Tariffs.

        A failure holds the series already in hand rather than discarding it.
        The alternative — dropping to no tariff on one bad call — would turn a
        momentary reload of the tariff integration into a room losing its
        constraints, which is a worse outcome than a series a few minutes old.

        `quiet` downgrades a failure to a debug line, for the startup attempts
        that are allowed to lose the race. Returns whether a series was read.
        """
        if not self.tariff_entry_id:
            self.tariff = None
            return True

        try:
            response = await self.hass.services.async_call(
                TARIFF_DOMAIN,
                TARIFF_SERVICE_GET_INTERVALS,
                {
                    "config_entry_id": self.tariff_entry_id,
                    "hours": TARIFF_HORIZON_HOURS,
                    "resolution_minutes": TARIFF_RESOLUTION_MINUTES,
                },
                blocking=True,
                return_response=True,
            )
        except ServiceNotFound:
            self._async_tariff_issue(
                "Abode Power Tariffs is not installed, or is not loaded", quiet
            )
            return False
        except HomeAssistantError as err:
            self._async_tariff_issue(str(err), quiet)
            return False

        try:
            self.tariff = TariffSeries.from_response(
                dict(response) if response else None,
                dt_util.utcnow(),
                dt_util.DEFAULT_TIME_ZONE,
            )
        except TariffPayloadError as err:
            self._async_tariff_issue(
                f"the interval series could not be read: {err}", quiet
            )
            return False

        LOGGER.debug(
            "Tariff series refreshed: %d intervals, covering to %s",
            len(self.tariff.intervals),
            self.tariff.covers_until,
        )
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_TARIFF_UNAVAILABLE)
        return True

    @callback
    def _async_tariff_issue(self, reason: str, quiet: bool = False) -> None:
        """Raise a repair issue naming why the tariff could not be read."""
        if quiet:
            LOGGER.debug("Tariff not available yet: %s", reason)
            return
        LOGGER.warning("Tariff could not be read: %s", reason)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_TARIFF_UNAVAILABLE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_TARIFF_UNAVAILABLE,
            translation_placeholders={"reason": reason},
        )

    async def _async_forecast_tick(self, now: datetime) -> None:
        """Refetch the forecast on the interval timer, then re-evaluate."""
        await self._async_refresh_forecast()
        await self.async_request_refresh()

    async def _async_refresh_forecast(self, *, quiet: bool = False) -> bool:
        """Fetch the hourly forecast from the configured weather entity.

        A failure holds the trajectory already in hand, for the same reason a
        failed tariff fetch does: a momentary reload of a weather integration
        must not turn into every room losing its precool decision.

        `quiet` downgrades a failure to a debug line, for the startup attempts
        that are allowed to lose the race. Returns whether a forecast was read.
        """
        if not self.weather_entity_id:
            self.trajectory = None
            return True

        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN,
                WEATHER_SERVICE_GET_FORECASTS,
                {ATTR_ENTITY_ID: self.weather_entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except (ServiceNotFound, HomeAssistantError) as err:
            self._async_forecast_issue(str(err), quiet)
            return False

        try:
            self.trajectory = WeatherTrajectory.from_response(
                dict(response) if response else None,
                dt_util.utcnow(),
                dt_util.DEFAULT_TIME_ZONE,
            )
        except ForecastPayloadError as err:
            self._async_forecast_issue(
                f"the forecast could not be read: {err}", quiet
            )
            return False

        LOGGER.debug(
            "Forecast refreshed: %d hours, covering to %s",
            len(self.trajectory.points),
            self.trajectory.covers_until,
        )
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_FORECAST_UNAVAILABLE)
        return True

    @callback
    def _async_forecast_issue(self, reason: str, quiet: bool = False) -> None:
        """Raise a repair issue naming why the forecast could not be read."""
        if quiet:
            LOGGER.debug("Weather forecast not available yet: %s", reason)
            return
        LOGGER.warning("Weather forecast could not be read: %s", reason)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_FORECAST_UNAVAILABLE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_FORECAST_UNAVAILABLE,
            translation_placeholders={"reason": reason},
        )

    def _power_configured(self) -> bool:
        """Whether the power budget can engage at all (all five fields set)."""
        return None not in (
            self.battery_soc_entity_id,
            self.battery_capacity_kwh,
            self.solar_entity_id,
            self.house_load_entity_id,
            self.reserve_margin_kwh,
        )

    async def _async_solar_tick(self, now: datetime) -> None:
        """Re-read the solar forecast on its timer."""
        await self._async_refresh_solar_forecast()

    async def _async_solar_forecast_entries(self) -> list[str]:
        """The solar forecast config entries to read (DR-061).

        The ones chosen in the power settings, or, where nothing has been
        chosen there, the ones the Energy dashboard uses. Home Assistant's
        energy manager is read for that; it is not a documented API, so a
        failure to read it means no default rather than an error.
        """
        if self.solar_forecast_entry_ids is not None:
            return list(self.solar_forecast_entry_ids)
        try:
            from homeassistant.components.energy.data import (  # noqa: PLC0415
                async_get_manager,
            )

            manager = await async_get_manager(self.hass)
        except (ImportError, HomeAssistantError, KeyError) as err:
            LOGGER.debug("Energy dashboard settings could not be read: %s", err)
            return []
        return energy_dashboard_solar_forecasts(manager.data)

    async def _async_refresh_solar_forecast(self, *, quiet: bool = False) -> bool:
        """Read the selected solar forecasts and sum them, hour by hour.

        0.9.3, DR-061. Through the same call the Energy dashboard uses: each
        forecast integration's energy platform, `async_get_solar_forecast`,
        which returns Wh per period. A source that cannot be read is named in
        a repair issue and the rest are used; with none read, the budget falls
        back to the solar being produced now, as before 0.9.3. The last
        forecast read is kept until a read succeeds, and dropped once the
        forecast no longer reaches the present.

        Returns whether the read is settled, so the startup sequence stops
        retrying.
        """
        entries = await self._async_solar_forecast_entries()
        if not entries:
            self._solar_points = None
            self._solar_sources = []
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
            return True
        try:
            from homeassistant.components.energy.websocket_api import (  # noqa: PLC0415
                async_get_energy_platforms,
            )

            platforms = await async_get_energy_platforms(self.hass)
        except (ImportError, HomeAssistantError) as err:
            self._async_solar_issue(
                f"Home Assistant's energy platforms could not be read: {err}", quiet
            )
            return False

        totals: dict[datetime, float] = {}
        read: list[str] = []
        failed: list[str] = []
        for entry_id in entries:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain not in platforms:
                failed.append(entry.title if entry is not None else entry_id)
                continue
            try:
                forecast = await platforms[entry.domain](self.hass, entry_id)
            except HomeAssistantError as err:
                LOGGER.debug("Solar forecast %s failed: %s", entry.title, err)
                forecast = None
            points = solar_points_from_forecast(forecast, dt_util.DEFAULT_TIME_ZONE)
            if not points:
                failed.append(entry.title)
                continue
            read.append(entry.title)
            for start, wh in points:
                totals[start] = totals.get(start, 0.0) + wh

        if read:
            self._solar_points = sorted(totals.items())
            self._solar_sources = read
        if failed:
            self._async_solar_issue(
                "these could not be read: " + ", ".join(failed), quiet
            )
            return bool(read) and not quiet
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
        return True

    @callback
    def _async_solar_issue(self, reason: str, quiet: bool = False) -> None:
        """Raise a repair issue naming the solar forecast that failed."""
        if quiet:
            LOGGER.debug("Solar forecast not available yet: %s", reason)
            return
        LOGGER.warning("Solar forecast could not be read: %s", reason)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_SOLAR_FORECAST_UNAVAILABLE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SOLAR_FORECAST_UNAVAILABLE,
            translation_placeholders={"reason": reason},
        )

    def _solar_forecast_slices(
        self, now: datetime, hours: float
    ) -> list[tuple[float, float]] | None:
        """Forecast solar from now to the end of the window, or None.

        None where no forecast is held, or the one held no longer reaches the
        present: a forecast that has gone stale says nothing about the sun.
        """
        points = self._solar_points
        if not points:
            return None
        # Hours before the first point carry no energy (forecast_slices), which
        # is right before sunrise: some sources only publish daylight hours.
        if points[-1][0] + timedelta(hours=1) <= now:
            return None
        return forecast_slices(points, now, now + timedelta(hours=hours))

    def _load_models(self) -> None:
        """Restore each room's learned behaviour from the store."""
        for room_id in self.rooms:
            self.models[room_id] = ThermalModel.from_dict(
                self.store.room(room_id).get("thermal")
            )

    def model_for(self, room_id: str) -> ThermalModel:
        """The thermal model for a room, created on first use."""
        if room_id not in self.models:
            self.models[room_id] = ThermalModel.from_dict(
                self.store.room(room_id).get("thermal")
            )
        return self.models[room_id]

    @callback
    def _async_check_configuration(self) -> None:
        """Surface configuration problems as repair issues, not log noise."""
        unrecognised = (
            self.tariff.unrecognised_constraints() if self.tariff else frozenset()
        )
        if unrecognised:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_UNRECOGNISED_CONSTRAINT,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_UNRECOGNISED_CONSTRAINT,
                translation_placeholders={
                    "constraints": ", ".join(sorted(unrecognised))
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNRECOGNISED_CONSTRAINT)

        # Two rooms pointed at one climate entity write different setpoints to
        # it on every cycle, and each one's dedupe cache sees only its own
        # writes, so neither errors and neither wins. The configuration is
        # reachable today and produces no log line at all. Naming it and
        # refusing to actuate those rooms is the only safe reading; choosing a
        # winner would be inventing an answer the user has not given.
        if shared := self._shared_climate_entities():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_SHARED_CLIMATE_ENTITY,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_SHARED_CLIMATE_ENTITY,
                translation_placeholders={
                    "conflicts": "; ".join(
                        f"{entity_id}: {', '.join(names)}"
                        for entity_id, names in sorted(shared.items())
                    )
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SHARED_CLIMATE_ENTITY)

        unbanded = sorted(
            room.name
            for room in self.rooms.values()
            if not room.bands and room.lockout_reason is None
        )
        if unbanded:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_NO_BANDS,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_BANDS,
                translation_placeholders={"rooms": ", ".join(unbanded)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_NO_BANDS)

        # 0.8.9. Required at setup, but a room saved before that or edited
        # since can still be missing one. Not silently fixed — there is
        # nothing to fix it to — and not a ConfigEntryError, which would take
        # correctly configured rooms down with it.
        if missing := self._rooms_missing_comfort_inputs():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_MISSING_COMFORT_INPUTS,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MISSING_COMFORT_INPUTS,
                translation_placeholders={
                    "rooms": "; ".join(
                        f"{name}: {', '.join(kinds)}"
                        for name, kinds in sorted(missing.items())
                    )
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_MISSING_COMFORT_INPUTS)

        # 0.8.10, finding 11. `no_grid_import` can arrive from Abode Power
        # Tariffs whether or not this integration has power management
        # configured at all — the plan is asserting a rule about grid flow,
        # and without a grid sensor this component can neither honour nor
        # verify it.
        interval = self._interval_at(dt_util.utcnow()) if self.tariff else None
        if (
            interval is not None
            and CONSTRAINT_NO_GRID_IMPORT in interval.constraints
            and self.grid_entity_id is None
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_GRID_REQUIRED,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_GRID_REQUIRED,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_GRID_REQUIRED)

        if self._grid_sign_disagreements >= _GRID_SIGN_DISAGREEMENT_THRESHOLD:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_GRID_SIGN_CONTRADICTED,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_GRID_SIGN_CONTRADICTED,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_GRID_SIGN_CONTRADICTED)

    def _rooms_missing_comfort_inputs(self) -> dict[str, list[str]]:
        """Rooms with no temperature sensor, no humidity sensor, or neither."""
        missing: dict[str, list[str]] = {}
        for room in self.rooms.values():
            absent = [
                kind
                for kind, entity_id in (
                    ("temperature", room.temperature_entity_id),
                    ("humidity", room.humidity_entity_id),
                )
                if entity_id is None
            ]
            if absent:
                missing[room.name] = absent
        return missing

    def _shared_climate_entities(self) -> dict[str, list[str]]:
        """Climate entities claimed by more than one room, and by which.

        A ducted system genuinely has one indoor unit serving several rooms,
        which is why the configuration looks reasonable to enter. This
        controller cannot drive one: its whole output is a dry-bulb target per
        room, and a ducted system has one setpoint and a damper per zone. Until
        that control law exists, the configuration is refused rather than
        half-honoured.
        """
        by_entity: dict[str, list[str]] = {}
        for room in self.rooms.values():
            for entity_id in room.climate_entity_ids:
                by_entity.setdefault(entity_id, []).append(room.name)
        return {
            entity_id: sorted(names)
            for entity_id, names in by_entity.items()
            if len(names) > 1
        }

    @callback
    def async_request_heading_home(
        self, room_id: str, deadline: datetime | None = None
    ) -> None:
        """Bring a room to its comfort band ahead of arrival.

        A deadline with no time zone is taken as local time, which is what
        Home Assistant's date-time picker sends. Until 0.9.3 it was kept
        naive, and every evaluation then failed comparing it with the clock.
        """
        self._heading_home[room_id] = (
            None if deadline is None else dt_util.as_utc(deadline)
        )

    def is_room_switched_off(self, room_id: str) -> bool:
        """Whether the user has switched this room's automatic control off."""
        return room_id in self._switched_off

    async def async_set_room_switched_off(self, room_id: str, off: bool) -> None:
        """Switch a room's automatic control off or on, and act on it now."""
        if off:
            self._switched_off.add(room_id)
        else:
            self._switched_off.discard(room_id)
        self.store.set_switched_off(room_id, off)
        await self.async_request_refresh()

    def is_room_vanes_manual(self, room_id: str) -> bool:
        """Whether the user has taken the vanes in this room into their own hands."""
        return room_id in self._manual_vanes

    async def async_set_room_vanes_manual(self, room_id: str, manual: bool) -> None:
        """Hand a room's vanes to the user, or back to the coordinator."""
        if manual:
            self._manual_vanes.add(room_id)
        else:
            self._manual_vanes.discard(room_id)
        self.store.set_manual_vanes(room_id, manual)
        await self.async_request_refresh()

    def comfort_mode(self, room_id: str) -> ComfortMode:
        """The room's Comfort mode: Schedule unless the user chose otherwise."""
        return self._comfort_modes.get(room_id, ComfortMode.SCHEDULE)

    def comfort_mode_refused(self, room_id: str, mode: ComfortMode) -> bool:
        """Whether choosing this Comfort mode now is refused (DR-058)."""
        return comfort_mode_refused(mode, self._room_empty.get(room_id, False))

    async def async_set_comfort_mode(self, room_id: str, mode: ComfortMode) -> None:
        """Set a room's Comfort mode, keep it, and act on it now (DR-058)."""
        self._remember_comfort_mode(room_id, mode)
        self._comfort_mode_reason.pop(room_id, None)
        await self.async_request_refresh()

    def _remember_comfort_mode(self, room_id: str, mode: ComfortMode) -> None:
        """Hold a room's Comfort mode in memory and in the store."""
        if mode is ComfortMode.SCHEDULE:
            self._comfort_modes.pop(room_id, None)
            self.store.set_comfort_mode(room_id, None)
        else:
            self._comfort_modes[room_id] = mode
            self.store.set_comfort_mode(room_id, mode.value)

    def _update_comfort_mode(self, room: RoomConfig, presence: bool | None) -> None:
        """End a chosen Comfort mode whose reason has gone (DR-058).

        Run once per evaluation, after presence is read and before anything
        asks whether the room is sleeping, so every answer in the cycle is the
        same one. The decision is `modes.comfort_mode_after`.
        """
        room_id = room.room_id
        schedule_active = self._bool(room.sleep_schedule_entity_id) is True
        current = self.comfort_mode(room_id)
        empty = room_reads_empty(presence, self._raw_presence.get(room_id))
        # 0.9.6, DR-079. Sunrise, as the rising edge of `sun.sun`, for a room
        # with no sleep schedule.
        sun = self.hass.states.get("sun.sun")
        sun_up = None if sun is None else sun.state == "above_horizon"
        sun_was_up = self._sun_was_up.get(room_id)
        self._sun_was_up[room_id] = sun_up
        after, reason = comfort_mode_after(
            current,
            empty=empty,
            schedule_active=schedule_active,
            schedule_was_active=self._schedule_was_on.get(room_id),
            has_schedule=room.sleep_schedule_entity_id is not None,
            sunrise=sun_up is True and sun_was_up is False,
        )
        self._schedule_was_on[room_id] = schedule_active
        self._room_empty[room_id] = empty
        if after is not current:
            self._remember_comfort_mode(room_id, after)
            if reason is not None:
                self._comfort_mode_reason[room_id] = reason
                LOGGER.info("%s: %s", room.name, reason)

    def last_mode(self, room_id: str) -> Mode:
        """The mode a room was last evaluated in."""
        return self._last_modes.get(room_id, Mode.LOCKOUT)

    def controls_writable(self, room_id: str, *, vane: bool = False) -> bool:
        """Whether the user may change a room's controls by hand right now.

        DR-049. Everything is writable while Automatic control is off. The
        vanes are also writable while Automatic vane control is off, and stay
        so with Automatic control on.
        """
        if room_id in self._switched_off:
            return True
        return vane and room_id in self._manual_vanes

    def _end_heading_home(
        self, room: RoomConfig, presence: bool | None, now: datetime
    ) -> None:
        """End a heading-home request whose purpose has passed (DR-080).

        It ends once the room is occupied, and an hour after its deadline if
        nobody has arrived. Until 0.9.6 only the clear-override action ended
        one, and until then the room stayed in Precondition, overriding
        presence and sleep: it was never turned off when it emptied again.
        """
        if room.room_id not in self._heading_home:
            return
        deadline = self._heading_home[room.room_id]
        reason: str | None = None
        if presence is True:
            reason = "heading home ended: the room is occupied"
        elif deadline is not None and now >= deadline + HEADING_HOME_EXPIRY:
            reason = (
                "heading home ended: an hour past its deadline and nobody has "
                "arrived"
            )
        if reason is not None:
            self._heading_home.pop(room.room_id, None)
            self._heading_home_reason[room.room_id] = reason
            LOGGER.info("%s: %s", room.name, reason)

    @callback
    def async_clear_override(self, room_id: str) -> None:
        """Drop any heading-home request for a room."""
        self._heading_home.pop(room_id, None)

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Re-evaluate when a watched entity changes.

        Actuation is awaited, so this schedules a refresh rather than
        evaluating inline.
        """
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), "abode_hvac_coordinator state change"
        )

    async def _async_update_data(self) -> dict[str, DecisionTrace]:
        """Evaluate every room."""
        return await self._async_evaluate(dt_util.utcnow())

    async def _async_evaluate(self, now: datetime) -> dict[str, DecisionTrace]:
        """Learn from the last interval, evaluate every room, then act."""
        traces: dict[str, DecisionTrace] = {}
        self._pending_announcements.clear()
        self._stale.clear()
        self._record_house_load_sample(now)
        self._power_context = self._compute_power_context(now)
        self._note_power_samples(now, self._power_context)
        self._track_grid(now, self._power_context)
        self._async_ensure_profiles()
        for room in self.rooms.values():
            inputs = self._inputs_for(room, now)
            self._unit_mode_seen[room.room_id] = inputs.unit_mode
            capabilities = self._capabilities(room)
            self._learn(room, inputs, now)
            # A room is evaluated, and its decision published, whether or not
            # anything is sent to it. Two things stop the sending: the user's
            # Automatic control switch (DR-049) and a room whose units have
            # not yet reported what they can do (DR-048). Either way the unit
            # is left exactly as it is.
            inactive_reason = (
                SWITCHED_OFF_REASON
                if room.room_id in self._switched_off
                else PROFILE_UNREAD_REASON
                if room.capabilities is None
                else None
            )
            trace = evaluate_room(
                replace(room, lockout_reason=inactive_reason)
                if inactive_reason is not None
                else room,
                inputs,
            )
            self._announce_free_cooling(room, trace)
            trace.model = self.model_for(room.room_id).diagnostics()
            if trace.target_dry_bulb_c is not None:
                self._last_target[room.room_id] = trace.target_dry_bulb_c
            if reason := self._grace_reason.get(room.room_id):
                trace.reasons.append(reason)
            if reason := self._fallback_reason.get(room.room_id):
                trace.reasons.append(reason)
            if reason := self._heading_home_reason.pop(room.room_id, None):
                trace.reasons.append(reason)
            if reason := self._comfort_mode_reason.pop(room.room_id, None):
                trace.reasons.append(reason)
            if reason := self._precondition_reason.get(room.room_id):
                trace.rejected.append(reason)
            if reason := self._demand_reason.pop(room.room_id, None):
                if trace.mode is Mode.PRECOOL:
                    trace.reasons.append(reason)
                elif "precool" in reason:
                    trace.rejected.append(reason)
            trace.stale_feeds = self._stale.get(room.room_id, [])
            if inactive_reason is not None:
                self._track_mode(room.room_id, trace.mode, now)
                self._last_modes[room.room_id] = trace.mode
                traces[room.room_id] = trace
                continue
            self._power_ceiling(room, inputs, trace, now, capabilities)
            self._warn_about_opening(room, inputs, now)
            # Guard first. The regulator's anti-windup gate has to see the
            # step that will actually be carried out, not the one that was
            # wanted before the short-cycle guard had its say.
            self._guard_cycling(room, trace, now)
            self._regulate(room, inputs, trace, now)
            self._track_mode(room.room_id, trace.mode, now)
            self._last_modes[room.room_id] = trace.mode
            traces[room.room_id] = trace
            LOGGER.debug(
                "%s: mode=%s actuator=%s hci=%s target=%s",
                room.room_id,
                trace.mode,
                trace.actuator,
                trace.hci,
                trace.target_dry_bulb_c,
            )
            await self.actuator.async_apply(room, trace)

        await self._async_announce(traces)
        self._async_remove_stale_devices(set(traces))
        self._process_draw_candidates(now)
        self.forecast = self._build_forecast(now, traces)
        self._persist_models()
        return traces

    def _regulate(
        self,
        room: RoomConfig,
        inputs: RoomInputs,
        trace: DecisionTrace,
        now: datetime,
    ) -> None:
        """Layer 2. The room loop: a PID that uses what the room has learned.

        The unit's own thermostat regulates against its return-air sensor,
        which is not the room. This closes the outer loop around it, and it is
        the only place in the project allowed to command a temperature other
        than the one the comfort index solved for (DR-054).

        Built from the learned drift and the learned rate against approach
        where they have converged, and from the solved target plus the trim
        where they have not. The trim is kept per direction. The result is
        held to the power ceiling where that is enforced, and last rounded to
        what the unit can actually hold and held inside its range (DR-051).

        The power ceiling only actually clamps `commanded_dry_bulb_c` — and
        only counts as binding for anti-windup — when the room's
        `power_management` is `POWER_MANAGEMENT_ENFORCED`. Under
        `POWER_MANAGEMENT_GUIDANCE`, `_power_ceiling` still populates
        `trace.power_ceiling_c`/`power_bin`/`power_budget_kw` and the trace
        reasons still report what the ceiling would be, but neither this
        method's clamp nor its anti-windup gate ever fire against it: the
        commanded setpoint is left exactly where comfort alone would put it.
        """
        state = self._regulators.setdefault(room.room_id, RegulatorState())
        limits, limits_note = self._setpoint_limits(room)
        trace.setpoint_step_c = limits.step
        if limits_note is not None:
            trace.rejected.append(limits_note)

        track_room_rate(state, inputs.temperature_c, now)

        # Direction comes from the target against the room reading, not from
        # `trace.demand` — the ceiling's primary case is a room *inside* its
        # band (demand is None there), where the solved target still exists
        # and still says which way the compressor is being asked to work.
        direction = setpoint_direction(trace.target_dry_bulb_c, inputs.temperature_c)
        trim_direction = direction or "cool"

        # 0.8.10. Whether the power ceiling is actually binding this cycle —
        # would the uncapped commanded setpoint go further than the ceiling
        # allows. Computed before `integrate` runs so anti-windup can see it:
        # winding the trim further against a bound the ceiling is enforcing
        # is the same fault finding 7 fixed for the short-cycle guard, in a
        # second place.
        uncapped, _ = self._room_loop_setpoint(
            room, state, inputs, trace, direction, trim_direction
        )
        binding = False
        if (
            room.power_management == POWER_MANAGEMENT_ENFORCED
            and trace.power_ceiling_c is not None
            and inputs.temperature_c is not None
            and direction is not None
            and uncapped is not None
        ):
            binding = ceiling_is_binding(
                uncapped, inputs.temperature_c, direction, trace.power_ceiling_c
            )

        integrate(
            state,
            target_c=trace.target_dry_bulb_c,
            room_c=inputs.temperature_c,
            now=now,
            # The trim is only meaningful while the unit is working toward the
            # solved target. That is true of a commanded compressor step and
            # equally true of one the guard is holding on; it is not true of a
            # start the guard has just refused, or of a cycle the power
            # ceiling is actively capping, both of which used to wind against
            # a bound the loop was not free to correct.
            regulating=(
                (trace.actuator is ActuatorStep.COMPRESSOR or trace.hold_compressor)
                and not binding
            ),
            direction=trim_direction,
            deadband_c=effective_deadband(limits),
        )
        trace.reasons.extend(state.notes)
        trace.regulation_trim_c = state.trim_for(trim_direction)
        commanded, pid = self._room_loop_setpoint(
            room, state, inputs, trace, direction, trim_direction
        )
        if pid is not None:
            trace.approach_c = pid.approach_c
            trace.feed_forward_c = pid.feed_forward_c
            trace.wanted_rate_c_per_hour = pid.wanted_rate_c_per_hour
            trace.reasons.append(pid.reason)

        capped = False
        if (
            room.power_management == POWER_MANAGEMENT_ENFORCED
            and trace.power_ceiling_c is not None
            and commanded is not None
            and inputs.temperature_c is not None
            and direction is not None
        ):
            held = held_setpoint(
                commanded, inputs.temperature_c, direction, trace.power_ceiling_c
            )
            if held != commanded:
                commanded = held
                capped = True
                trace.reasons.append(
                    f"power budget: commanded held at {commanded:.1f} C, "
                    f"{trace.power_ceiling_c:.1f} C ceiling"
                )

        if commanded is not None:
            # Last step, and the one that matters to the unit: what it is sent
            # has to be a value it can hold (DR-051). Where the power ceiling
            # has just bound, the rounding goes the way that stays inside it.
            # Otherwise it goes the way the room is being driven (DR-065):
            # cooling rounds down and heating rounds up, so a unit with whole
            # degree steps is never sent a setpoint at or past the room it is
            # meant to be moving. Rounding to the nearest sent 26 C to a 25.9 C
            # room the loop wanted 0.2 C below, and the unit did nothing.
            # 0.9.6, DR-075. Where the room loop answered, its own way of
            # working decides the rounding, not the room against its target:
            # the two disagree for a room a tenth of a degree past its target.
            way = pid.direction if pid is not None else direction
            toward = None
            if capped:
                toward = "up" if direction == "cool" else "down"
            elif way == "cool":
                toward = "down"
            elif way == "heat":
                toward = "up"
            rounded = quantise_setpoint(commanded, limits, toward=toward)
            if abs(rounded - commanded) >= 0.05:
                trace.reasons.append(
                    f"setpoint {commanded:.1f} rounded to {rounded:.1f} C, "
                    f"the unit holds {limits.rounding_step:g} C steps"
                )
            commanded = rounded
        trace.commanded_dry_bulb_c = commanded

    def _room_loop_setpoint(
        self,
        room: RoomConfig,
        state: RegulatorState,
        inputs: RoomInputs,
        trace: DecisionTrace,
        direction: str | None,
        trim_direction: str,
    ) -> tuple[float | None, PidSetpoint | None]:
        """The room loop's setpoint before the power ceiling and the rounding.

        The PID where the drift and the learned rate against approach are both
        known; otherwise the solved target plus the trim for this direction,
        which is what the loop was before 0.9.0 (DR-054).
        """
        target = trace.target_dry_bulb_c
        if target is None:
            return None, None
        if inputs.temperature_c is not None:
            model = self.model_for(room.room_id)
            drift = model.drift_rate(
                inputs.temperature_c,
                self.outdoor_reading(),
                direct_sun=inputs.direct_sun is True,
            )
            pid_direction = direction or ("cool" if (drift or 0.0) >= 0 else "heat")
            pid = pid_setpoint(
                state,
                target_c=target,
                room_c=inputs.temperature_c,
                direction=pid_direction,
                drift_c_per_hour=drift,
                approach_for_rate=model.approach_for_rate,
            )
            if pid is not None:
                return pid.setpoint_c, pid
        return commanded_setpoint(state, target, trim_direction), None

    def _setpoint_limits(self, room: RoomConfig) -> tuple[SetpointLimits, str | None]:
        """What the room's units can be set to, from the stored profile.

        DR-051. No profile means no limits known, and the setpoint is rounded
        to tenths as it always was.
        """
        profile = room.capabilities
        if profile is None:
            return SetpointLimits(), None
        return SetpointLimits(
            profile.target_temp_step, profile.min_temp, profile.max_temp
        ), None

    def _guard_cycling(
        self,
        room: RoomConfig,
        trace: DecisionTrace,
        now: datetime,
    ) -> None:
        """Refuse a compressor transition inside its minimum on or off time.

        Short cycling is the most damaging thing a controller can do to a
        split system, and nothing else in the stack prevents it: the unit's own
        protection is against its own thermostat, not against a coordinator
        commanding hvac_mode from outside.

        A refusal downgrades the step and is written into the trace, because a
        room that appears to ignore its own decision with no explanation is
        the exact fault this project refuses to ship.
        """
        # Dry mode energises the compressor. Treating it as a stop made the
        # guard block a cool-to-dry change for ten minutes as though the
        # compressor were shutting down, then record it as stopped while it
        # was in fact running — after which the minimum *off* time blocked the
        # return to cool, for a stop that never happened.
        # NONE is not a stop. It means the unit keeps what it was last given,
        # so this room asks for whatever it asked for last cycle and there is
        # no transition to record. Until 0.8.7 `wants` was derived from the
        # step alone and resolved NONE as "not running", so every time a room
        # reached its band the guard logged a stop that never happened — the
        # most common state the controller is in.
        # The decisions themselves are `regulate.wants_running` and
        # `regulate.arbitrate_cycling`; what is left here is gathering their
        # inputs and carrying out the verdict.
        groups = room.groups
        compressors = {
            group: self._compressors.setdefault(group, CompressorState())
            for group in groups
        }
        wants = wants_running(
            trace.actuator,
            previous_want=self._room_wants.get(room.room_id),
            running_now=any(state.running for state in compressors.values()),
        )
        verdict = arbitrate_cycling(
            compressors,
            wants=wants,
            neighbours_want={
                group: any(
                    self._room_wants.get(other.room_id, False)
                    for other in self.rooms.values()
                    if other.room_id != room.room_id and group in other.groups
                )
                for group in compressors
            },
            now=now,
        )
        for group, running in verdict.record:
            compressor = compressors[group]
            was_running = compressor.running
            note_transition(compressor, running=running, now=now)
            if compressor.running != was_running:
                # 0.8.9, finding 14. The signal this learns draw from: one
                # group's compressor actually changed state.
                self._note_compressor_transition(
                    group, started=compressor.running, now=now
                )
        refusal = verdict.refusal
        holding = verdict.holding

        self._room_wants[room.room_id] = wants

        if refusal is not None:
            trace.rejected.append(refusal)
            if holding:
                # A stop was refused. Leave the decision alone and hold the
                # compressor: replacing the step with COMPRESSOR cancelled
                # cover and fan actions outright, which is a guard that exists
                # to protect the compressor silently overruling decisions that
                # have nothing to do with it.
                trace.hold_compressor = True
            else:
                # A start was refused. The compressor is not running and may
                # not start yet. Until 0.9.3 the unit was commanded off here,
                # which turned a coasting unit off for a minute or two in the
                # middle of its fan-only coast (DR-059). It is now left on its
                # fan where it has one, which is what it was already doing,
                # and off where it has none. Not NONE: a unit that was moved
                # into a running mode by something else would be left running.
                trace.actuator = (
                    ActuatorStep.FAN
                    if self._capabilities(room)["can_fan_only"]
                    else ActuatorStep.OFF
                )
                trace.hold_setpoint = False
            return

    def _track_mode(self, room_id: str, mode: Mode, now: datetime) -> None:
        """Remember the mode and when it last changed, for the band ramp."""
        if self._previous_mode.get(room_id) is not mode:
            if room_id in self._previous_mode:
                self._mode_changed_at[room_id] = now
            self._previous_mode[room_id] = mode

    def _learn(self, room: RoomConfig, inputs: RoomInputs, now: datetime) -> None:
        """Fold the interval since the last usable anchor into the room's model.

        What the room actually did is measured at both ends; nothing here is
        inferred from what was commanded.

        **The anchor is held, not replaced every cycle.** Evaluation runs every
        thirty seconds and an observation needs at least sixty to carry any
        information over sensor quantisation. Replacing the anchor each time
        made every interval exactly one evaluation period long, so every
        observation was discarded as too short and no coefficient ever gained a
        sample. Nothing reported it: the model simply stayed unconverged
        forever, and coast, the dry-versus-cool split, precool sizing and the
        heading-home estimate were all permanently unavailable.

        So the anchor advances only when it has been used, or when holding it
        any longer would make it useless.

        **0.8.9, finding 9.** The commanded setpoint joins the anchor tuple.
        Read the same one cycle lagged as `compressor` and `drying` already
        are — `_regulate` has not run yet this cycle, so this is last cycle's
        commanded setpoint, matching what actually drove the interval just
        completed. A change to it resets the anchor the same way a
        compressor-direction change already does: the approach bin an
        interval gets attributed to is meaningless once what the room was
        being asked to reach has moved.
        """
        # 0.9.6, DR-074. The unit's own sensor is not the room; nothing is
        # learned from it.
        temperature = None if inputs.temperature_from_unit else inputs.temperature_c
        humidity = inputs.relative_humidity
        compressor = self._compressor_direction(room)
        drying = self._is_drying(room)
        self._track_stop(room.room_id, cooling=compressor == -1 or drying, now=now)
        # 0.9.6, DR-081. The setpoint the unit itself reports, which is what
        # drove it, whoever set it. Until 0.9.6 this was the solved target
        # plus the trim, a form the room loop has not sent since 0.9.0, so
        # every cooling interval was filed under an approach the unit was
        # never asked for.
        commanded = self._unit_setpoint(room)

        if temperature is None or humidity is None:
            # No reading at this end of the interval. Drop the anchor rather
            # than learning across a gap.
            self._previous.pop(room.room_id, None)
            return

        previous = self._previous.get(room.room_id)
        if previous is None:
            self._previous[room.room_id] = (
                now, temperature, humidity, compressor, drying, commanded
            )
            return

        started, start_c, start_rh, start_compressor, start_drying, start_setpoint = (
            previous
        )
        if start_c is None or start_rh is None:
            self._previous[room.room_id] = (
                now, temperature, humidity, compressor, drying, commanded
            )
            return

        if (
            compressor != start_compressor
            or drying != start_drying
            or commanded != start_setpoint
        ):
            # What was driving the room changed inside the interval, so it
            # teaches nothing reliable about either state. Start again from
            # here rather than attributing the whole interval to one of them.
            self._previous[room.room_id] = (
                now, temperature, humidity, compressor, drying, commanded
            )
            return

        elapsed = (now - started).total_seconds() / 3600.0
        if elapsed < MIN_LEARNING_INTERVAL_HOURS:
            # Too short to mean anything yet. Hold the anchor and let it grow.
            return

        if elapsed > MAX_LEARNING_INTERVAL_HOURS:
            # Stale — Home Assistant was asleep, or a sensor was out. Something
            # almost certainly changed inside it.
            self._previous[room.room_id] = (
                now, temperature, humidity, compressor, drying, commanded
            )
            return

        self.model_for(room.room_id).observe(
            Observation(
                elapsed_hours=elapsed,
                indoor_start_c=start_c,
                indoor_end_c=temperature,
                humidity_start=start_rh,
                humidity_end=humidity,
                outdoor_c=self._number(self.outdoor_entity_id, OUTDOOR_TOLERANCE),
                direct_sun=inputs.direct_sun is True,
                compressor=start_compressor,
                drying=start_drying,
                commanded_setpoint_c=start_setpoint,
                since_stop_hours=self._hours_since_stop(room.room_id, started),
            )
        )
        self._previous[room.room_id] = (
            now, temperature, humidity, compressor, drying, commanded
        )

    def _track_stop(self, room_id: str, *, cooling: bool, now: datetime) -> None:
        """Note when the compressor stops cooling or drying (DR-060)."""
        was = self._was_cooling.get(room_id)
        self._was_cooling[room_id] = cooling
        if cooling:
            self._stopped_at[room_id] = None
        elif was:
            self._stopped_at[room_id] = now

    def _hours_since_stop(self, room_id: str, at: datetime) -> float | None:
        """Hours from the last cooling or drying stop to `at`, or None."""
        stopped = self._stopped_at.get(room_id)
        if stopped is None or at < stopped:
            return None
        return (at - stopped).total_seconds() / 3600.0

    @staticmethod
    def _predicted_to_hold(unaided: Mapping[str, Any]) -> bool | None:
        """Whether the room is in band now and stays there unaided.

        0.9.0, DR-053. Read from the same stepped projection the weather coast
        uses, not from a straight line to the point an hour ahead. That check
        called a room back in band whenever the end of the hour landed inside
        it, whatever the room was doing in the meantime and however far out of
        band it was now, and it coasted such a room with no limit at all.

        True only for a room already inside its band and staying inside it.
        A room that is out of band is the weather coast's question, which has
        a limit. None means the model cannot say, and the evaluator treats
        that as \"do not coast\", never as \"yes\".
        """
        if not unaided["unaided_available"]:
            return None
        return bool(unaided["unaided_return_minutes"] == 0.0)

    def _cheaper_window_imminent(self, room: RoomConfig, now: datetime) -> bool:
        """Whether a strictly cheaper tariff interval begins soon enough that
        the band is predicted to hold unaided until it starts.

        0.8.11, finding 12. The room-level half of the pool's finding 19b —
        `per_kwh` was parsed and reached no decision — folded into a general
        principle rather than built as its own separate mechanism: every
        decision point evaluates the cheapest way to deliver the comfort
        band, and for a room approaching the edge of its band that means
        checking whether waiting a short while costs nothing.

        Bounded at `COAST_HORIZON_HOURS`, the same horizon `_predicted_to_
        hold` already trusts for "the band holds unaided" — never further
        out than that, and never when the model cannot positively say the
        wait is safe.
        """
        if self.tariff is None:
            return False
        hours = hours_until_cheaper_interval(
            self.tariff, now, timedelta(hours=COAST_HORIZON_HOURS)
        )
        if hours is None:
            return False

        model = self.model_for(room.room_id)
        indoor = self._number(room.temperature_entity_id)
        humidity = self._number(room.humidity_entity_id)
        if indoor is None or humidity is None:
            return False
        band = room.band_for(Mode.SLEEP if self._sleeping(room) else Mode.OCCUPIED)
        if band is None:
            return False
        lower_c = dry_bulb_for_index(band.low, humidity)
        upper_c = dry_bulb_for_index(band.high, humidity)
        return (
            model.holds_through(
                indoor,
                self._number(self.outdoor_entity_id, OUTDOOR_TOLERANCE),
                direct_sun=self._direct_sun(room) is True,
                hours=hours,
                lower_c=lower_c,
                upper_c=upper_c,
            )
            is True
        )

    def _demand_ahead(self, room: RoomConfig, now: datetime) -> bool:
        """Whether this room is forecast to need cooling later today.

        Precool banks thermal mass against a load that is coming. The question
        is not whether it is hot now — at 11:00 in the free window it usually
        is not — but whether the afternoon is going to arrive. Answering it
        from current conditions is what left the free window unused on exactly
        the days it was worth the most.
        """
        indoor = self._number(
            room.temperature_entity_id, INDOOR_TOLERANCE, room.room_id
        )
        try:
            verdict = demand_ahead(self.trajectory, now=now, indoor_c=indoor)
        except TypeError as err:
            # A forecast this cannot compare against must cost the room its
            # precool decision, not the whole evaluation. The first version of
            # this raised straight out of the update loop and took every room
            # down over one integration's timestamp format.
            LOGGER.warning(
                "The forecast could not be compared against the clock (%s); "
                "precool is falling back to current conditions",
                err,
            )
            self.trajectory = None
            self._async_forecast_issue(
                f"its timestamps could not be compared against the clock: {err}"
            )
            verdict = demand_ahead(None, now=now, indoor_c=indoor)
        if self.trajectory is not None:
            self._demand_reason[room.room_id] = verdict.reason
            return verdict.demand_ahead

        # No forecast configured. Fall back to what the controller did before
        # it had one, and say in the trace that it is a fallback.
        self._demand_reason[room.room_id] = verdict.reason
        model = self.model_for(room.room_id)
        outdoor = self.outdoor_reading()
        if indoor is None or outdoor is None:
            return False
        drift = model.drift_rate(
            indoor, outdoor, direct_sun=self._direct_sun(room) is True
        )
        if drift is None:
            return outdoor > indoor + PRECOOL_DEMAND_MARGIN_C
        return drift > 0

    def _announce_free_cooling(self, room: RoomConfig, trace: DecisionTrace) -> None:
        """Speak the room's own free-cooling advice, once, on the rising edge.

        `free_cooling_advised` is a level, recomputed fresh every cycle
        (`modes.py`), not an event — so raising it every time it reads True
        would announce it every 30 seconds for as long as the window stays
        open. Only the transition from not-advised to advised is queued.
        """
        was_advised = self._free_cooling_advised.get(room.room_id, False)
        self._free_cooling_advised[room.room_id] = trace.free_cooling_advised
        if trace.free_cooling_advised:
            self._free_cooling_since.setdefault(room.room_id, trace.at)
        else:
            self._free_cooling_since.pop(room.room_id, None)
        if trace.free_cooling_advised and not was_advised:
            self._pending_announcements.append((room, Announcement.FREE_COOLING))

    def _graced_presence(self, room: RoomConfig, now: datetime) -> bool | None:
        """Presence after the grace periods, not the raw sensor.

        Announcements raised here are dispatched by the caller, so that this
        stays a pure read of state.
        """
        if room.presence_entity_id is None:
            self._raw_presence.pop(room.room_id, None)
            return None

        state = self._grace.get(room.room_id)
        if state is None:
            # 0.9.4, DR-068. Start from the occupancy last known, which the
            # store keeps across a restart or reload. Until 0.9.4 every room
            # started unoccupied, so a presence sensor still coming up after a
            # restart turned an occupied room off.
            state = GraceState(
                occupied=self.store.occupancy().get(room.room_id, False)
            )
            self._grace[room.room_id] = state
        raw = self._bool(room.presence_entity_id, PRESENCE_TOLERANCE, room.room_id)
        self._raw_presence[room.room_id] = raw
        # 0.9.6, DR-082. The wait for the warnings only where a warning can
        # be said: the room has speakers, is run and awake, and its unit is
        # on. Otherwise the room is turned off when the vacancy period ends,
        # not three minutes after a warning nobody heard.
        speaks = (
            room.grace.announce
            and bool(room.announce_target_entity_ids)
            and not self._silenced(room)
            and self._unit_mode(room) not in (None, "off")
        )
        result = evaluate_grace(
            state,
            raw,
            now,
            room.grace if speaks == room.grace.announce else replace(room.grace, announce=speaks),
        )
        self.store.set_occupancy(room.room_id, state.occupied)
        self._grace_reason[room.room_id] = result.reason
        if result.announcement is not Announcement.NONE:
            self._pending_announcements.append((room, result.announcement))
        return result.occupied

    def outdoor_reading(self) -> float | None:
        """The outdoor temperature, for the house-wide sensor."""
        return self._number(self.outdoor_entity_id, OUTDOOR_TOLERANCE)

    def compressor_state(self) -> dict[str, CompressorState]:
        """Cycling state per outdoor unit, for diagnostics.

        Keyed by outdoor unit group, where a head that shares with nothing is
        keyed by its own entity id. Two rooms with a head each on one outdoor
        unit appear once here, which is the point: `MIN_RUN` and `MIN_OFF`
        protect a compressor, not a room.
        """
        return dict(self._compressors)

    def draw_for(self, group: str) -> DrawModel:
        """The learned draw model for one outdoor unit group, on first use."""
        if group not in self._draw:
            self._draw[group] = DrawModel.from_dict(
                self.store.group(group).get("draw")
            )
        return self._draw[group]

    def draw_models(self) -> dict[str, DrawModel]:
        """Learned draw per outdoor unit group, for diagnostics.

        Only groups actually seen this session — a group with no entry has
        not had a rated-kW figure asked of it yet and is still on
        `ASSUMED_UNIT_KW` everywhere it is used.
        """
        return dict(self._draw)

    def _rated_kw_for(self, room: RoomConfig) -> float:
        """The room's projected draw, summed across its compressors.

        Each group's own pulldown-bin estimate, three-level fallback (bin,
        pooled, `ASSUMED_UNIT_KW`) via `DrawModel.draw_kw`. Pulldown rather
        than at-setpoint: this feeds a headroom or energy figure for a unit
        that is currently off or about to be asked to pull toward target,
        which is the conservative case to size against.
        """
        return sum(
            self.draw_for(group).draw_kw(3, ASSUMED_UNIT_KW)
            for group in room.groups
        ) or ASSUMED_UNIT_KW

    def regulation_state(self) -> dict[str, RegulatorState]:
        """Layer 2 state per room, for diagnostics.

        Exposed deliberately. A commanded setpoint that differs from the solved
        target is the single most confusing thing this controller does, and a
        diagnostics file that cannot show why is a diagnostics file that costs
        an evening.
        """
        return dict(self._regulators)

    def power_state(self) -> dict[str, Any]:
        """House-level power figures, for diagnostics. 0.8.10.

        Per-room budget, ceiling and bin already ride on each room's trace
        (`power_ceiling_c`, `power_budget_kw`, `power_bin`,
        `comfort_reduction_active`); this is the shared, house-wide half.
        """
        context = self._power_context
        return {
            "grid_entity_id": self.grid_entity_id,
            "grid_sign": self.grid_sign,
            "grid_import_w": context.grid_import_w if context else None,
            "battery_w": context.battery_w if context else None,
            "battery_max_discharge_kw": self.battery_max_discharge_kw,
            "grid_sign_disagreements": self._grid_sign_disagreements,
            "last_breach_reported_kwh": self._last_breach_reported_kwh,
            "current_breach_window_kwh": (
                round(self._breach_kwh, 3) if self._breach_window_start else None
            ),
        }

    def forecast_peak(self) -> float | None:
        """The hottest hour the forecast can see, for the house-wide sensor."""
        if self.trajectory is None:
            return None
        now = dt_util.utcnow()
        peak = self.trajectory.peak_between(now, now + DEMAND_LOOKAHEAD)
        return None if peak is None else round(peak[0], 1)

    def forecast_peak_at(self) -> str | None:
        """When that peak falls."""
        if self.trajectory is None:
            return None
        now = dt_util.utcnow()
        peak = self.trajectory.peak_between(now, now + DEMAND_LOOKAHEAD)
        return None if peak is None else peak[1].isoformat()

    def outdoor_wind_ms(self) -> float | None:
        """Outdoor wind in metres per second, whatever unit the entity uses.

        The unit is read from the entity rather than assumed. Steadman's
        formula wants m/s and most Australian weather feeds publish km/h, so
        assuming would make the apparent temperature wrong by a factor of 3.6
        — and wrong in the direction that advises opening the windows on an
        evening you should not.
        """
        if self.outdoor_wind_entity_id is None:
            return None
        raw = self._number(self.outdoor_wind_entity_id, OUTDOOR_TOLERANCE)
        if raw is None:
            return None
        state = self.hass.states.get(self.outdoor_wind_entity_id)
        unit = (
            state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        )
        if unit in (None, UnitOfSpeed.METERS_PER_SECOND):
            return raw
        try:
            return SpeedConverter.convert(
                raw, unit, UnitOfSpeed.METERS_PER_SECOND
            )
        except (HomeAssistantError, ValueError) as err:
            LOGGER.warning(
                "%s reports wind in %s, which cannot be converted to m/s (%s); "
                "still air assumed",
                self.outdoor_wind_entity_id,
                unit,
                err,
            )
            return None

    def outdoor_apparent_temperature(self) -> float | None:
        """What outdoors feels like, on the comfort index scale."""
        temp = self.outdoor_reading()
        humidity = self._number(self.outdoor_humidity_entity_id, OUTDOOR_TOLERANCE)
        if temp is None or humidity is None:
            return None
        return round(
            apparent_temperature(temp, humidity, self.outdoor_wind_ms() or 0.0), 1
        )

    def outdoor_dew_point(self) -> float | None:
        """Outdoor dew point, where both outdoor feeds are configured."""
        temp = self.outdoor_reading()
        humidity = self._number(self.outdoor_humidity_entity_id, OUTDOOR_TOLERANCE)
        if temp is None or humidity is None:
            return None
        return round(dew_point_c(temp, humidity), 1)

    def _precondition_plan(
        self, room: RoomConfig, now: datetime
    ) -> PreconditionPlan | None:
        """When a heading-home request should actually start the compressor.

        The model already answers how long the pull takes. Using that answer is
        the difference between a deadline four hours out costing four hours of
        compressor and costing nothing until it is forty minutes out.
        """
        if room.room_id not in self._heading_home:
            return None

        indoor = self._number(
            room.temperature_entity_id, INDOOR_TOLERANCE, room.room_id
        )
        humidity = self._number(
            room.humidity_entity_id, INDOOR_TOLERANCE, room.room_id
        )
        band = room.band_for(Mode.OCCUPIED)
        hours: float | None = None
        if indoor is not None and humidity is not None and band is not None:
            hours = self.model_for(room.room_id).hours_to_reach(
                indoor,
                dry_bulb_for_index(band.midpoint, humidity),
                self.outdoor_reading(),
                direct_sun=self._direct_sun(room) is True,
            )
        return plan_precondition(
            now=now,
            deadline=self._heading_home.get(room.room_id),
            hours_needed=hours,
        )

    def _air_moving(self, room: RoomConfig) -> bool:
        """Whether the room's air is moving, from a fan the user configured.

        DR-055. Only an air-movement entity the user chose counts, a ceiling
        fan for instance. Until 0.9.1 the air conditioner's own state counted
        when none was configured: any mode other than off moved air. That
        made the comfort index depend on whether the controller's own actuator
        was running. Running the unit dropped the index by a full point, the
        room looked too cool, the unit was stopped, the index rose by a point,
        and the loop repeated. The index is a measurement of the room, taken
        where the sensor is, and the component must not change it by acting.
        With nothing configured the air is assumed still, which fails toward
        comfort.
        """
        if room.air_movement_entity_id and (
            moving := self._bool(room.air_movement_entity_id)
        ) is not None:
            return moving
        return False

    def _sleeping(self, room: RoomConfig) -> bool:
        """Whether the room counts as sleeping: its Comfort mode, or where that
        is Schedule, its sleep schedule (DR-058).

        Every place that picks a band by whether the room is asleep asks here,
        so a room put on Sleep by hand is projected against the same band its
        mode decision uses.
        """
        return sleeping(
            self.comfort_mode(room.room_id),
            self._bool(room.sleep_schedule_entity_id) is True,
        )

    def _tts_engine(self) -> tuple[str | None, str | None]:
        """The text-to-speech engine to speak through, or why there is none.

        0.9.5, DR-069. The one chosen under House configuration where it
        exists; otherwise the only engine Home Assistant has. Returns
        (engine, None) or (None, reason).
        """
        engines = sorted(
            state.entity_id for state in self.hass.states.async_all("tts")
        )
        if self.tts_engine_entity_id:
            if self.tts_engine_entity_id in engines:
                return self.tts_engine_entity_id, None
            return None, (
                f"the chosen text-to-speech engine {self.tts_engine_entity_id} "
                "does not exist"
            )
        if len(engines) == 1:
            return engines[0], None
        if not engines:
            return None, "Home Assistant has no text-to-speech engine"
        return None, (
            "no text-to-speech engine is chosen and there are "
            f"{len(engines)}: {', '.join(engines)}"
        )

    async def _async_announce(
        self, traces: Mapping[str, DecisionTrace] | None = None
    ) -> None:
        """Speak any warnings raised this evaluation.

        0.9.5, DR-069. Through `tts.speak`, which targets a text-to-speech
        engine and takes the speakers as `media_player_entity_id`. Until 0.9.5
        the speakers were sent as the target, Home Assistant rejected every
        call, no warning was ever spoken, and the rejection stopped the rest
        of the evaluation. A warning that cannot be given is now logged once
        and never stops anything else.

        0.9.6, DR-070. A room speaks only while the coordinator is running it
        and the room is awake. Nothing is spoken for a room whose Automatic
        control is off, a room still waiting for its air conditioner to
        report, or a room that is asleep, by its Comfort mode or its sleep
        schedule. Until 0.9.6 the empty-room and free-cooling warnings were
        queued before the Automatic control check, so a room the coordinator
        was not running still said it was turning its air conditioning off.
        """
        pending = [
            (room, announcement)
            for room, announcement in self._timed_announcements(traces or {})
            if room.announce_target_entity_ids and not self._silenced(room)
        ]
        self._pending_announcements.clear()
        if not pending:
            return
        engine, reason = self._tts_engine()
        if engine is None:
            self._note_announce_failure(f"no engine: {reason}", str(reason))
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_ANNOUNCEMENTS_UNAVAILABLE,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ANNOUNCEMENTS_UNAVAILABLE,
                translation_placeholders={"reason": str(reason)},
            )
            return
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ANNOUNCEMENTS_UNAVAILABLE)
        for room, announcement in pending:
            try:
                await self.hass.services.async_call(
                    "tts",
                    "speak",
                    {
                        ATTR_ENTITY_ID: engine,
                        "media_player_entity_id": list(
                            room.announce_target_entity_ids
                        ),
                        "message": _announcement_text(room, announcement),
                    },
                    blocking=False,
                )
            except Exception as err:  # noqa: BLE001 - a spoken warning must never stop the evaluation
                self._note_announce_failure(
                    f"{room.room_id}: {type(err).__name__}: {err}",
                    f"announcement for {room.room_id} failed: {err}",
                )

    def _timed_announcements(
        self, traces: Mapping[str, DecisionTrace]
    ) -> list[tuple[RoomConfig, Announcement]]:
        """The warnings that are true now (DR-077).

        A warning that the air conditioning is running, or will be turned
        off, is said only where a unit is on. A warning that it is being
        turned off is said only in the evaluation the off command is actually
        sent: until 0.9.6 it was said when the stop was decided, and on 14
        October the minimum run then held the Office's unit on for another
        four and a half minutes after it said so. Held until then, and dropped
        if the room no longer stops.
        """
        timed: list[tuple[RoomConfig, Announcement]] = []
        queued = list(self._pending_announcements)
        for room_id, announcement in list(self._deferred_final.items()):
            room = self.rooms.get(room_id)
            if room is not None:
                queued.append((room, announcement))
        self._deferred_final.clear()
        for room, announcement in queued:
            trace = traces.get(room.room_id)
            if announcement is not Announcement.FREE_COOLING and (
                self._unit_mode_seen.get(room.room_id) in (None, "off")
            ):
                continue
            if announcement in (
                Announcement.FINAL_WARNING,
                Announcement.OPENING_FINAL_WARNING,
            ):
                if trace is None or trace.actuator is not ActuatorStep.OFF:
                    continue
                if not trace.stopped:
                    if trace.hold_compressor:
                        self._deferred_final[room.room_id] = announcement
                    continue
            timed.append((room, announcement))
        return timed

    def _silenced(self, room: RoomConfig) -> bool:
        """Whether this room must not speak now (DR-070).

        The room's current configuration is read, not the copy the warning
        was queued with, so a room whose unit has just reported is judged
        on that.
        """
        current = self.rooms.get(room.room_id, room)
        return (
            room.room_id in self._switched_off
            or current.capabilities is None
            or self._sleeping(current)
        )

    def _note_announce_failure(self, key: str, message: str) -> None:
        """Log an announcement failure the first time it happens."""
        if key in self._announce_failures:
            return
        self._announce_failures.add(key)
        LOGGER.warning("Spoken warning not given: %s", message)

    def _compressor_direction(self, room: RoomConfig) -> int:
        """Whether the unit is moving sensible heat, and which way.

        **`hvac_action` first, for every mode.** The mode string says what the
        unit was asked to do; `hvac_action` says what it is doing. A head
        sitting at setpoint in `cool` reports `cool` with the compressor idle,
        and reading the mode counted all of that idle time as an interval the
        compressor was driving. Every sensible observation was diluted by it,
        which is why `k_sensible` describes neither running nor idling.

        The mode is used only where the entity publishes no `hvac_action` at
        all. Which source answered is recorded, so a unit that never publishes
        the attribute is visible in diagnostics rather than quietly degrading
        the model.
        """
        states = [
            state
            for entity_id in room.climate_entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
        ]
        if not states:
            self._action_source.pop(room.room_id, None)
            return 0

        # Any head driving counts. A room with two heads has one comfort
        # target and one thermal response, so what is learned is the room's
        # rate with whatever was running — the split between heads is not
        # observable from a single room sensor.
        actions = [
            state.attributes.get("hvac_action")
            for state in states
            if state.attributes.get("hvac_action") is not None
        ]
        if actions:
            self._action_source[room.room_id] = "hvac_action"
            if "cooling" in actions:
                return -1
            if "heating" in actions:
                return 1
            # idle, off, drying, fan, preheating, defrosting: the compressor
            # is not moving sensible heat in a direction this can learn from.
            return 0

        if self._action_source.get(room.room_id) != "hvac_mode":
            # Once per room. Diagnostics answers this too, but only for
            # someone who already suspected the question — and this is the
            # reading that decides whether every learned sensible coefficient
            # is diluted by idle time. It belongs in the log the install is
            # read from.
            LOGGER.info(
                "%s: %s publishes no hvac_action, so the compressor state is "
                "being read from the mode string. A head idling at setpoint "
                "reports its mode, so the learned sensible coefficient will "
                "be diluted by idle time",
                room.room_id,
                ", ".join(room.climate_entity_ids),
            )
        self._action_source[room.room_id] = "hvac_mode"
        modes = {state.state for state in states}
        if "cool" in modes:
            return -1
        if "heat" in modes:
            return 1
        return 0

    def action_sources(self) -> dict[str, str]:
        """Which attribute answered the compressor question, per room.

        For diagnostics. A room reporting `hvac_mode` here is learning from
        mode rather than action, and its sensible coefficient will be diluted
        by idle time.
        """
        return dict(self._action_source)

    def _unit_mode(self, room: RoomConfig) -> str | None:
        """The mode the room's unit is in, or None where none reports one.

        0.9.6, DR-072. A working mode on any head wins: a room with one head
        cooling is being cooled.
        """
        modes = [
            state.state
            for entity_id in room.climate_entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
            and state.state not in _NON_NUMERIC
        ]
        if not modes:
            return None
        for mode in modes:
            if mode in WORKING_MODES:
                return mode
        return modes[0]

    def _unit_setpoint(self, room: RoomConfig) -> float | None:
        """The setpoint the room's units report, or None (DR-081).

        A unit holding a range reports its middle. The mean across heads.
        """
        values: list[float] = []
        for entity_id in room.climate_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in _NON_NUMERIC:
                continue
            single = self._attribute(state, ATTR_TEMPERATURE)
            if single is not None:
                values.append(single)
                continue
            low = self._attribute(state, "target_temp_low")
            high = self._attribute(state, "target_temp_high")
            if low is not None and high is not None:
                values.append((low + high) / 2.0)
        return round(sum(values) / len(values), 1) if values else None

    def _unit_reading(self, room: RoomConfig, attribute: str) -> float | None:
        """The mean of a numeric attribute across the room's units, or None."""
        values = [
            value
            for entity_id in room.climate_entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
            and state.state not in _NON_NUMERIC
            and (value := self._attribute(state, attribute)) is not None
        ]
        return sum(values) / len(values) if values else None

    def _is_drying(self, room: RoomConfig) -> bool:
        """Whether the unit is in dry mode."""
        return any(
            (state := self.hass.states.get(entity_id)) is not None
            and state.state == "dry"
            for entity_id in room.climate_entity_ids
        )

    def _record_house_load_sample(self, now: datetime) -> None:
        """Keep a short rolling window of house-load readings.

        The signal finding 14 learns from: house load is total draw and does
        not net off solar, so solar cannot confound a step in it. Kept only
        long enough to bracket a compressor state change on both sides —
        trimmed by age because the evaluation cadence is not guaranteed
        constant.
        """
        if self.house_load_entity_id is None:
            return
        reading = self._number(self.house_load_entity_id, POWER_TOLERANCE)
        if reading is not None:
            self._house_load_samples.append((now, reading))
        cutoff = now - DRAW_HISTORY_WINDOW
        while self._house_load_samples and self._house_load_samples[0][0] < cutoff:
            self._house_load_samples.popleft()

    def _house_load_window(
        self, start: datetime, end: datetime
    ) -> list[float]:
        """House-load readings falling in `[start, end)`."""
        return [
            reading
            for stamp, reading in self._house_load_samples
            if start <= stamp < end
        ]

    def _note_compressor_transition(
        self, group: str, *, started: bool, now: datetime
    ) -> None:
        """Open a draw candidate for a group whose compressor just flipped.

        0.8.9, finding 14. Re-opens on a state change before the previous
        candidate settled — the newer transition is the one whose draw can
        actually be measured, so it replaces rather than queues behind it.
        """
        self._draw_pending[group] = _DrawCandidate(
            group=group, started=now, changed_at=now
        )

    def _draw_bin_for_group(self, group: str) -> int:
        """The approach bin to attribute a draw observation to.

        The rooms sharing this outdoor unit, at their last known commanded
        setpoint against their last known reading — the same regressor
        finding 9 bins the sensible rate by. No room in the group with both
        readings available falls back to pulldown, the conservative bin: a
        transition with nothing to measure approach from is exactly the
        cold-start case pulldown exists to describe.
        """
        gaps = [
            abs(target - reading)
            for room in self.rooms.values()
            if group in room.groups
            for target in (self._last_target.get(room.room_id),)
            for reading in (self._number(room.temperature_entity_id),)
            if target is not None and reading is not None
        ]
        if not gaps:
            return 3
        return approach_bin(statistics.mean(gaps))

    def _process_draw_candidates(self, now: datetime) -> None:
        """Settle any draw candidate whose post-change window has matured.

        0.8.9, finding 14. Bracket the transition on both sides — a settled
        period before it and, after the ramp allowance an inverter needs, a
        settled period after — and fold the difference into the group's
        `DrawModel`, weighted by how much the observation is trusted rather
        than gated on it.

        **Simultaneous changes are skipped, not downweighted.** The design
        called for scoring a candidate lower when another group changed near
        its edges; this settles for the simpler rule of dropping a candidate
        outright if another group is also pending when it matures, since an
        apportioned residual cannot be checked against anything and solo
        observations are sufficient at a house's compressor count and
        evaluation cadence.
        """
        stale_cutoff = now - DRAW_CANDIDATE_MAX_AGE
        mature_at = DRAW_RAMP_ALLOWANCE + DRAW_POST_WINDOW
        settled: list[str] = []
        for group, candidate in list(self._draw_pending.items()):
            if candidate.changed_at < stale_cutoff:
                settled.append(group)
                continue
            if now - candidate.changed_at < mature_at:
                continue
            settled.append(group)
            if len(self._draw_pending) > 1:
                # Another group was mid-transition at the same time. Neither
                # side of the step can be attributed cleanly.
                continue

            pre = self._house_load_window(
                candidate.changed_at - DRAW_PRE_WINDOW, candidate.changed_at
            )
            post = self._house_load_window(
                candidate.changed_at + DRAW_RAMP_ALLOWANCE,
                candidate.changed_at + mature_at,
            )
            if not pre or not post:
                continue

            compressor = self._compressors.get(group)
            started = compressor.running if compressor else True
            diff_w = statistics.mean(post) - statistics.mean(pre)
            draw_kw = diff_w / 1000.0 if started else -diff_w / 1000.0

            richness = min(len(pre), len(post), DRAW_TARGET_SAMPLES) / (
                DRAW_TARGET_SAMPLES
            )
            spread = max(
                statistics.pstdev(pre) if len(pre) > 1 else 0.0,
                statistics.pstdev(post) if len(post) > 1 else 0.0,
            )
            quietness = DRAW_NOISE_REFERENCE_W / (DRAW_NOISE_REFERENCE_W + spread)
            quality = max(
                richness * quietness,
                MIN_DRAW_QUALITY if len(pre) >= DRAW_MIN_SAMPLES
                and len(post) >= DRAW_MIN_SAMPLES
                else MIN_DRAW_QUALITY / 2,
            )

            self.draw_for(group).observe(
                self._draw_bin_for_group(group), draw_kw, quality
            )

        for group in settled:
            self._draw_pending.pop(group, None)

    def _persist_models(self) -> None:
        """Write learned state back to the store, on its own delay."""
        for room_id, model in self.models.items():
            record = dict(self.store.room(room_id))
            record["thermal"] = model.as_dict()
            self.store.update_room(room_id, record)
        for group, draw in self._draw.items():
            record = dict(self.store.group(group))
            record["draw"] = draw.as_dict()
            self.store.update_group(group, record)

    def _build_forecast(
        self, now: datetime, traces: dict[str, DecisionTrace]
    ) -> DemandForecast:
        """Project HVAC energy over the horizon. No vendor concepts in it.

        The horizon needs the outdoor temperature across the whole horizon,
        not at this instant — a mild reading right now says nothing about a
        heatwave arriving at hour three. Where a weather forecast is
        configured, the mean forecast temperature over the horizon window
        replaces the instantaneous reading; without one, the instantaneous
        reading is used unchanged, exactly as before this fix.
        """
        outdoor = self._number(self.outdoor_entity_id, OUTDOOR_TOLERANCE)
        if self.trajectory is not None:
            forecast_mean = self.trajectory.mean_between(
                now, now + timedelta(hours=DEFAULT_HORIZON_HOURS)
            )
            if forecast_mean is not None:
                outdoor = forecast_mean
        inputs: list[RoomForecastInput] = []
        for room_id, room in self.rooms.items():
            trace = traces.get(room_id)
            capabilities = self._capabilities(room)
            inputs.append(
                RoomForecastInput(
                    room_id=room_id,
                    model=self.model_for(room_id),
                    indoor_c=self._number(room.temperature_entity_id),
                    target_c=trace.target_dry_bulb_c if trace else None,
                    outdoor_c=outdoor,
                    direct_sun=self._direct_sun(room) is True,
                    # The actuator verdict, not the mode. Checking the mode
                    # was finding 20's hole in a second place: a room stopped
                    # for an open window, for coasting, or by the power
                    # refusal is in none of those modes and projected a full
                    # horizon of draw it was never going to take.
                    will_run=trace is not None
                    and trace.actuator is not ActuatorStep.OFF
                    and trace.mode is not Mode.COAST,
                    can_heat=capabilities["can_heat"],
                    can_cool=capabilities["can_cool"],
                    rated_kw=self._rated_kw_for(room),
                )
            )

        windows: list[tuple[time, time, str, frozenset[str]]] = []
        if self.tariff is not None:
            windows = [
                (window.start, window.end, window.rate, window.constraints)
                for window in self.tariff.windows()
            ]

        return build_forecast(
            dt_util.as_local(now), inputs, windows, DEFAULT_HORIZON_HOURS
        )

    @callback
    def _async_rooms_in_registry(self) -> set[str]:
        """Room ids that currently have a device registered to this entry."""
        registry = dr.async_get(self.hass)
        return {
            identifier[1]
            for device in dr.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            )
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }

    @callback
    def _async_remove_stale_devices(self, current: set[str]) -> None:
        """Drop devices for rooms that are no longer configured."""
        if stale := self.previous_rooms - current:
            registry = dr.async_get(self.hass)
            in_use = {
                entity_id
                for room in self.rooms.values()
                for entity_id in room.climate_entity_ids
            }
            for room_id in stale:
                self.actuator.forget(room_id, in_use)
                self.models.pop(room_id, None)
                self._grace.pop(room_id, None)
                self._base_load_samples.pop(room_id, None)
                self._shortfall_since.pop(room_id, None)
                self._grace_reason.pop(room_id, None)
                self._free_cooling_advised.pop(room_id, None)
                self._free_cooling_since.pop(room_id, None)
                self._deferred_final.pop(room_id, None)
                self._fallback_reason.pop(room_id, None)
                self._unit_mode_seen.pop(room_id, None)
                self._sun_was_up.pop(room_id, None)
                self._heading_home_reason.pop(room_id, None)
                self._heading_home.pop(room_id, None)
                self._regulators.pop(room_id, None)
                self._previous_mode.pop(room_id, None)
                self._mode_changed_at.pop(room_id, None)
                self._precondition_reason.pop(room_id, None)
                self._demand_reason.pop(room_id, None)
                self._previous.pop(room_id, None)
                self._comfort_modes.pop(room_id, None)
                self._schedule_was_on.pop(room_id, None)
                self._room_empty.pop(room_id, None)
                self._raw_presence.pop(room_id, None)
                self._comfort_mode_reason.pop(room_id, None)
                self.store.forget_room(room_id)
                # 0.9.6. A device belongs to one config entry; removing the
                # room removes its device. The lookup and the removal used
                # until 0.9.6 stop working in Home Assistant 2027.8.
                if device := registry.async_get_device_by_identifier(
                    (DOMAIN, room_id), self.config_entry.entry_id
                ):
                    registry.async_remove_device(device.id)
        self.previous_rooms = current

    def _inputs_for(self, room: RoomConfig, now: datetime) -> RoomInputs:
        """Assemble everything the evaluator is allowed to see."""
        interval = self._interval_at(now)
        constraints = interval.constraints if interval else frozenset()

        # Presence first, and the Comfort mode settled against it, before
        # anything below asks whether the room is sleeping (DR-058).
        presence = self._graced_presence(room, now)
        self._update_comfort_mode(room, presence)
        self._end_heading_home(room, presence, now)

        plan = self._precondition_plan(room, now)
        if plan is not None and not plan.start_now:
            self._precondition_reason[room.room_id] = plan.reason
        else:
            self._precondition_reason.pop(room.room_id, None)

        capabilities = self._capabilities(room)
        unaided = self._unaided_inputs(room, now)
        temperature_c = self._number(
            room.temperature_entity_id, INDOOR_TOLERANCE, room.room_id
        )
        relative_humidity = self._number(
            room.humidity_entity_id, INDOOR_TOLERANCE, room.room_id
        )
        temperature_from_unit = False
        self._fallback_reason.pop(room.room_id, None)
        if temperature_c is None:
            # 0.9.6, DR-074. The room's sensor is unavailable. The unit's own
            # reading is used rather than leaving an off unit off for as long
            # as the sensor is down (living room, 13 October), and the trace
            # says so. Its sensor sits in the unit, not the room.
            unit_c = self._unit_reading(room, "current_temperature")
            if unit_c is not None:
                temperature_c = unit_c
                temperature_from_unit = True
                self._fallback_reason[room.room_id] = (
                    f"room temperature from the air conditioner's own sensor, "
                    f"{unit_c:.1f} C: {room.temperature_entity_id} is not reporting"
                )
        if relative_humidity is None:
            unit_rh = self._unit_reading(room, "current_humidity")
            if unit_rh is not None:
                relative_humidity = unit_rh
                temperature_from_unit = True
                note = self._fallback_reason.get(room.room_id)
                self._fallback_reason[room.room_id] = (
                    (note + "; " if note else "")
                    + f"room humidity from the air conditioner's own sensor, "
                    f"{unit_rh:.0f}%: {room.humidity_entity_id} is not reporting"
                )
        since = self._free_cooling_since.get(room.room_id)

        return RoomInputs(
            now=now,
            temperature_c=temperature_c,
            relative_humidity=relative_humidity,
            temperature_from_unit=temperature_from_unit,
            unit_mode=self._unit_mode(room),
            free_cooling_advised=self._free_cooling_advised.get(room.room_id, False),
            free_cooling_wait_over=since is not None
            and now - since >= FREE_COOLING_WAIT,
            temperature_entity_id=room.temperature_entity_id,
            humidity_entity_id=room.humidity_entity_id,
            k_rh_cooling_per_hour=self._learned(room.room_id, "k_rh_cooling"),
            presence=presence,
            direct_sun=self._direct_sun(room),
            heat_load=self._bool(room.heat_load_entity_id) is True,
            air_moving=self._air_moving(room),
            has_covers=bool(room.cover_entity_ids),
            allow_cover_control=room.allow_cover_control,
            cover_position=mean_cover_position(self.hass, room.cover_entity_ids),
            can_cool=capabilities["can_cool"],
            can_heat=capabilities["can_heat"],
            can_dry=capabilities["can_dry"],
            can_fan_only=capabilities["can_fan_only"],
            opening_open=any(
                self._bool(entity_id, CONTACT_TOLERANCE, room.room_id) is True
                for entity_id in room.opening_entity_ids
            ),
            opening_open_since=self._opening_open_since(room),
            opening_grace_minutes=room.opening_grace_minutes,
            precool_opportunity=CONSTRAINT_PRECOOL_OPPORTUNITY in constraints,
            no_grid_import=CONSTRAINT_NO_GRID_IMPORT in constraints,
            coasting_permitted=interval.coasting_permitted if interval else True,
            outdoor_c=self.outdoor_reading(),
            outdoor_relative_humidity=self._number(
                self.outdoor_humidity_entity_id, OUTDOOR_TOLERANCE
            ),
            outdoor_wind_ms=self.outdoor_wind_ms(),
            precondition_ready=plan is None or plan.start_now,
            previous_mode=self._previous_mode.get(room.room_id),
            mode_changed_at=self._mode_changed_at.get(room.room_id),
            heading_home=room.room_id in self._heading_home,
            precondition_deadline=self._heading_home.get(room.room_id),
            k_sensible_c_per_hour=self._sensible_rate_for(room, now),
            k_latent_rh_per_hour=self._learned(room.room_id, "k_latent"),
            predicted_to_hold=self._predicted_to_hold(unaided),
            **unaided,
            cheaper_window_imminent=self._cheaper_window_imminent(room, now),
            forecast_demand_ahead=self._demand_ahead(room, now),
            sleep_schedule_active=self._bool(room.sleep_schedule_entity_id)
            is True,
            comfort_mode=self.comfort_mode(room.room_id),
        )

    def _async_ensure_profiles(self) -> None:
        """Read, once, what each room's units can do where that is not known.

        DR-048. A room set up from 0.9.0 carries its profile in its own
        configuration. A room configured before that has none, and is read
        here from its units' live state and the result kept in the store, so
        a restart with a unit down does not lose it. Until a unit reports, the
        room has no profile and nothing is sent to it.
        """
        for room_id, room in list(self.rooms.items()):
            if room.capabilities is not None:
                continue
            stored = RoomCapabilities.from_dict(self.store.profile(room_id))
            if stored is not None:
                self.rooms[room_id] = replace(room, capabilities=stored)
                continue
            profile = self._read_profile(room)
            if profile is None:
                if room_id not in self._profile_unread:
                    self._profile_unread.add(room_id)
                    LOGGER.info(
                        "%s: its air conditioner has not reported what it can "
                        "do yet; nothing is sent to the room until it does",
                        room.name,
                    )
                continue
            self.store.set_profile(room_id, profile.to_dict())
            self._profile_unread.discard(room_id)
            self.rooms[room_id] = replace(room, capabilities=profile)
            LOGGER.info(
                "%s: read what its air conditioner can do (%d modes, %d fan "
                "speeds, %d vane positions)",
                room.name,
                len(profile.hvac_modes),
                len(profile.fan_modes),
                len(profile.swing_modes),
            )

    def _read_profile(self, room: RoomConfig) -> RoomCapabilities | None:
        """What a room's units offer right now, or None if any is not reporting."""
        profile, note = profile_from_states(
            [self.hass.states.get(entity_id) for entity_id in room.climate_entity_ids]
        )
        if note is not None:
            LOGGER.warning("%s: %s", room.name, note)
        return profile

    def _unaided_inputs(self, room: RoomConfig, now: datetime) -> dict[str, Any]:
        """Whether, and when, the weather brings the room back into band.

        DR-053. Projects the room's own temperature forward with nothing
        running, from the learned loss and solar terms, the forecast outdoor
        temperature, and the sun on this room's window at each step ahead.
        Returns the inputs the evaluator uses to decide whether to coast.
        """
        none: dict[str, Any] = {
            "unaided_available": False,
            "unaided_return_minutes": None,
            "unaided_return_limit_minutes": UNAIDED_RETURN_FALLBACK_MINUTES,
            "unaided_refused": None,
            "rebound_relative_humidity": None,
        }
        indoor = self._number(room.temperature_entity_id)
        humidity = self._number(room.humidity_entity_id)
        if indoor is None or humidity is None:
            return none
        band = room.band_for(Mode.SLEEP if self._sleeping(room) else Mode.OCCUPIED)
        if band is None:
            return none

        # The band is in comfort index and the model works in dry bulb. The
        # bounds are converted under the same corrections the index was
        # measured under - sun on the glass, still air, heat in the room - so
        # a room that is warm only because of the sun is not mistaken for one
        # that is already inside its band.
        direct_sun = self._direct_sun(room)
        radiant = radiant_load(
            direct_sun=direct_sun,
            cover_position=mean_cover_position(self.hass, room.cover_entity_ids),
            has_covers=bool(room.cover_entity_ids),
        )
        still_air = not self._air_moving(room)
        heat_load = self._bool(room.heat_load_entity_id) is True

        def to_dry_bulb(index: float) -> float:
            return dry_bulb_for_index(
                index,
                humidity,
                radiant=radiant,
                still_air=still_air,
                heat_load=heat_load,
            )

        middle_c = to_dry_bulb(band.midpoint)

        # 0.9.3, DR-060. The water the coil gives back once the compressor
        # stops. While it is running, or for `REBOUND_HOURS` after it stops
        # cooling or drying, the room's humidity is projected to rise at its
        # learned rebound rate. Where the rebound has not been learned, the
        # weather coast is refused; the in-band hold keeps the humidity flat,
        # as it always did, so that stops still happen and the rebound can be
        # learned.
        cooling_now = self._was_cooling.get(room.room_id, False)
        since_stop = None if cooling_now else self._hours_since_stop(room.room_id, now)
        rebound_rate = 0.0
        rebound_end = 0.0
        refused: str | None = None
        rebound_rh: float | None = None
        if cooling_now or (since_stop is not None and since_stop < REBOUND_HOURS):
            model_for_rise = self.model_for(room.room_id)
            rise = model_for_rise.rebound_rh_rise(since_stop)
            if rise is None:
                refused = (
                    "the unit has just been cooling and this room has not yet "
                    "learned how much moisture comes back off the coil when it "
                    "stops, so the weather is not relied on to bring it back"
                )
            else:
                rebound_end = REBOUND_HOURS - (since_stop or 0.0)
                rebound_rate = rise / rebound_end if rebound_end > 0 else 0.0
                rebound_rh = min(100.0, humidity + rise)

        def humidity_at(hours_ahead: float) -> float:
            return min(100.0, humidity + rebound_rate * min(hours_ahead, rebound_end))

        outdoor_now = self.outdoor_reading()
        trajectory = self.trajectory

        def outdoor_at(hours_ahead: float) -> float | None:
            if trajectory is not None:
                forecast = trajectory.temperature_at(now + timedelta(hours=hours_ahead))
                if forecast is not None:
                    return forecast
            return outdoor_now

        window_azimuth = azimuth_for_direction(room.window_direction)

        def sun_at(hours_ahead: float) -> float | None:
            if room.direct_sun_entity_id or window_azimuth is None:
                # A sensor override, or no window direction: the sun is held
                # at whatever it is now, as the coast prediction always did.
                return 1.0 if direct_sun is True else 0.0
            when = now + timedelta(hours=hours_ahead)
            azimuth, elevation = solar_position(
                self.hass.config.latitude, self.hass.config.longitude, when
            )
            on = sun_on_window(
                azimuth,
                elevation,
                window_azimuth,
                overhang_projection_m=room.overhang_projection_m,
                overhang_height_m=room.overhang_height_m,
            )
            if on is not True:
                return 0.0
            clear = (
                trajectory.solar_fraction_at(when) if trajectory is not None else None
            )
            return 1.0 if clear is None else clear

        model = self.model_for(room.room_id)
        points = model.project_unaided(
            indoor,
            outdoor_at=outdoor_at,
            sun_at=sun_at,
            hours=COAST_HORIZON_HOURS,
            step_hours=PROJECTION_STEP_HOURS,
        )
        if points is None:
            return {**none, "unaided_refused": refused}
        # Judged on the comfort index, step by step, so the humidity the room
        # is heading for counts as much as its temperature.
        indices = [
            comfort_index(
                temperature,
                humidity_at(index * PROJECTION_STEP_HOURS),
                radiant=radiant,
                still_air=still_air,
                heat_load=heat_load,
            )
            for index, temperature in enumerate(points)
        ]
        outlook = unaided_outlook(indices, lower_c=band.low, upper_c=band.high)

        compressor_hours = model.hours_to_reach(
            indoor, middle_c, outdoor_now, direct_sun=direct_sun is True
        )
        limit = (
            UNAIDED_RETURN_FALLBACK_MINUTES
            if compressor_hours is None
            else compressor_hours * 60.0 + UNAIDED_RETURN_MARGIN_MINUTES
        )
        return {
            "unaided_available": True,
            "unaided_return_minutes": outlook.return_minutes,
            "unaided_return_limit_minutes": limit,
            "unaided_refused": refused,
            "rebound_relative_humidity": rebound_rh,
        }

    def _sensible_rate_for(self, room: RoomConfig, now: datetime) -> float | None:
        """The `k_sensible` figure the dry-versus-cool decision should use.

        0.8.10. Closes the gap disclosed at the 0.8.9 handover:
        `hours_to_reach` and `energy_for` both went bin-aware in 0.8.9, but
        this call site kept reading the pooled coefficient regardless of how
        close the room actually is to its target.

        Approach here is last cycle's commanded setpoint against the current
        reading — the same pairing `_learn` anchors an interval on, since
        `RoomInputs` is assembled before `evaluate_room` has produced this
        cycle's own target. Falls back to the pooled-only figure when either
        half is not yet known, which is every room's first cycle.
        """
        commanded = self._unit_setpoint(room)  # DR-081
        reading = self._number(room.temperature_entity_id, INDOOR_TOLERANCE)
        if commanded is None or reading is None:
            return self._learned(room.room_id, "k_sensible")
        return self.model_for(room.room_id).sensible_rate_at(abs(commanded - reading))

    def _learned(self, room_id: str, name: str) -> float | None:
        """A learned coefficient, or None until it has converged.

        None is what tells the evaluator to fall back rather than to trust a
        number the filter has not settled on. Handing over an unconverged
        coefficient would look like knowledge and behave like noise.
        """
        coefficient = getattr(self.model_for(room_id), name)
        return coefficient.value if coefficient.converged else None

    def _direct_sun(self, room: RoomConfig) -> bool | None:
        """Whether the sun is on this room's windows.

        Worked out from geometry: the sun's position, which Home Assistant
        already publishes as `sun.sun`, against the direction the room's
        windows face. **No extra entity or integration is needed.**

        Indoor light level is deliberately not used. A semi-transparent blind
        reads bright when it is fully closed, so lux would report nothing to
        block at exactly the moment the blind is already blocking.

        A per-room sensor can be configured to override this, for a room whose
        exposure is more complicated than one compass direction — a corner
        room, or one shaded by a tree.

        None means the question cannot be answered, which the evaluator treats
        as "do not move the covers" rather than as either answer.
        """
        if room.direct_sun_entity_id:
            return self._bool(room.direct_sun_entity_id)

        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return None
        return sun_on_window(
            self._attribute(sun, "azimuth"),
            self._attribute(sun, "elevation"),
            azimuth_for_direction(room.window_direction),
            overhang_projection_m=room.overhang_projection_m,
            overhang_height_m=room.overhang_height_m,
        )

    @staticmethod
    def _attribute(state: State, name: str) -> float | None:
        """A numeric attribute, or None when it is missing or not a number."""
        value = state.attributes.get(name)
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _capabilities(self, room: RoomConfig) -> dict[str, bool]:
        """What the unit can do, so the decision never picks an absent mode.

        A missing entity reports everything as unavailable, which stops the
        selector choosing a step that could not be carried out.
        """
        states = [
            state
            for entity_id in room.climate_entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
        ]
        if len(states) != len(room.climate_entity_ids) or not states:
            return {
                "can_cool": False,
                "can_heat": False,
                "can_dry": False,
                "can_fan_only": False,
            }
        # The intersection across the room's heads. A room can only do what
        # all of them can: claiming dry mode because one of two heads has it
        # produces a decision the actuator cannot carry out on the other.
        modes = (
            set(room.capabilities.hvac_modes)
            if room.capabilities is not None
            else set.intersection(*(supported_hvac_modes(state) for state in states))
        )
        return {
            "can_cool": bool(modes & {"cool", "heat_cool", "auto"}),
            "can_heat": bool(modes & {"heat", "heat_cool", "auto"}),
            "can_dry": "dry" in modes,
            "can_fan_only": "fan_only" in modes,
        }

    def _compute_power_context(self, now: datetime) -> _PowerContext:
        """Read the house-level power inputs once per evaluation cycle.

        Every room's power budget runs against this same snapshot
        rather than each re-reading the entities itself — the room assesses
        its own need, but against readings that do not change mid-cycle
        depending on evaluation order.

        `engaged` is False whenever any of the five fields is unconfigured.
        Power management is opt-in: with any one of them absent, no room is
        throttled and nothing about this feature runs.
        """
        if (
            self.battery_soc_entity_id is None
            or self.battery_capacity_kwh is None
            or self.solar_entity_id is None
            or self.house_load_entity_id is None
            or self.reserve_margin_kwh is None
        ):
            return _PowerContext(engaged=False)

        solar_w = self._number(self.solar_entity_id, POWER_TOLERANCE)
        house_load_w = self._number(self.house_load_entity_id, POWER_TOLERANCE)

        grid_import_w: float | None = None
        battery_w: float | None = None
        if self.grid_entity_id is not None and self.grid_sign is not None:
            grid_raw_w = self._number(self.grid_entity_id, POWER_TOLERANCE)
            if grid_raw_w is not None:
                grid_import_w = normalise_grid_import_w(grid_raw_w, self.grid_sign)
                if solar_w is not None and house_load_w is not None:
                    battery_w = derive_battery_w(house_load_w, solar_w, grid_import_w)

        return _PowerContext(
            engaged=True,
            battery_soc_percent=self._number(
                self.battery_soc_entity_id, POWER_TOLERANCE
            ),
            battery_capacity_kwh=self.battery_capacity_kwh,
            solar_w=solar_w,
            house_load_w=house_load_w,
            reserve_margin_kwh=self.reserve_margin_kwh,
            grid_import_w=grid_import_w,
            battery_w=battery_w,
        )

    #: Approach, in °C, at which each bin's ceiling caps `commanded_dry_bulb_c`.
    #: The upper edge of the bin's own range — `at_setpoint`'s ceiling is
    #: where `close` begins, and so on. `pulldown` has no meaningful upper
    #: edge, so its ceiling is large enough to never bind in practice: the
    #: bin fitting the budget at all already means nothing tighter is needed.
    _BIN_CEILING_C = (
        APPROACH_AT_SETPOINT_C,
        APPROACH_CLOSE_C,
        APPROACH_WORKING_C,
        99.0,
    )

    def _track_grid(self, now: datetime, context: _PowerContext) -> None:
        """Check the grid sign convention, and integrate measured import
        across any active `no_grid_import` window.

        0.8.10, finding 11. Runs once per evaluation regardless of whether
        any room is under a power constraint this cycle — the breach record
        is a house-level fact, not a per-room question.

        0.8.13, DR-043. The window is the unbroken run of intervals carrying
        the constraint, not one tariff interval: the series arrives in
        30-minute slices, and keying on each slice's start closed and
        re-judged the window every half hour. Each sample is weighted by the
        time since the previous one, not by a fixed evaluation period:
        evaluations also run on every watched state change, so a fixed period
        counted the same half-minute several times over. A gap longer than
        one evaluation period is credited as one period — the reading says
        nothing about a stretch in which nothing was evaluated.
        """
        self._check_grid_sign(context)

        interval = self._interval_at(now) if self.tariff is not None else None
        if interval is None or CONSTRAINT_NO_GRID_IMPORT not in interval.constraints:
            self._close_breach_window(reported=self._breach_kwh > 0.0)
            return

        if self._breach_window_start is None:
            self._breach_window_start = interval.start
            self._breach_kwh = 0.0
            self._breach_sampled_at = now
            return

        previous = self._breach_sampled_at
        self._breach_sampled_at = now
        if previous is None or now <= previous:
            return
        elapsed = min(now - previous, EVALUATION_INTERVAL)
        if context.grid_import_w is not None and context.grid_import_w > 0:
            self._breach_kwh += (
                context.grid_import_w * elapsed.total_seconds() / 3600.0 / 1000.0
            )

    def _check_grid_sign(self, context: _PowerContext) -> None:
        """Compare the stored sign convention against live evidence.

        0.8.10, finding 11. A contradiction check, not the mechanism: this
        never corrects the stored convention, only counts how often live
        readings disagree with it, so a persistent disagreement can be named
        as a repair issue rather than silently mis-deriving the battery
        figure forever.

        0.8.11. An inconclusive cycle — no real flow to read a sign off, or
        not enough house draw to be decisive — resets the counter exactly
        like an agreeing one. Only a run of genuine, sizeable disagreements
        can ever raise the issue; a night of correctly-inconclusive zero
        readings must not leave the counter primed to trip on the first
        reading the next morning.
        """
        if self.grid_entity_id is None or self.grid_sign is None:
            self._grid_sign_disagreements = 0
            return
        if (
            context.house_load_w is None
            or context.solar_w is None
            or context.grid_import_w is None
        ):
            return
        raw_reading_w = (
            context.grid_import_w
            if self.grid_sign == GRID_SIGN_IMPORTING
            else -context.grid_import_w
        )
        implied = implied_sign(context.house_load_w, context.solar_w, raw_reading_w)
        if implied is None or implied == self.grid_sign:
            self._grid_sign_disagreements = 0
        else:
            self._grid_sign_disagreements += 1

    def _close_breach_window(self, *, reported: bool) -> None:
        """Raise or clear the shortfall issue for the window just ended.

        0.8.10, findings 15/16. A trivial amount of import — a kettle
        overlapping the boundary by one cycle — is not a shortfall worth a
        repair issue, so anything under 0.05 kWh is treated as noise.

        0.8.13, DR-043. A window that closes clean clears an issue left by an
        earlier one, so the issue always describes the most recent window.
        Called every evaluation outside a window as well; only a window that
        was actually open can clear anything.
        """
        window_was_open = self._breach_window_start is not None
        if reported and self._breach_kwh >= 0.05:
            self._last_breach_reported_kwh = round(self._breach_kwh, 2)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_POWER_SHORTFALL,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_POWER_SHORTFALL,
                translation_placeholders={
                    "kwh": f"{self._last_breach_reported_kwh:.2f}"
                },
            )
        elif window_was_open:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_POWER_SHORTFALL)
        self._breach_window_start = None
        self._breach_kwh = 0.0
        self._breach_sampled_at = None

    def _sustained_solar_kw(
        self, now: datetime, hours_until_clear: float, current_solar_kw: float
    ) -> float:
        """How much of today's solar this room may count on for the rest of
        the constrained window.

        0.8.11, finding 18. Extends the instantaneous solar offset with the
        weather forecast's clear-sky fraction — the existing, previously
        unused `solar_fraction_at` — so a window that runs past sunset, or
        into cloud the forecast already expects, is not credited with
        today's current solar persisting for hours it will not. The worst
        (lowest) fraction ratio across the remaining hours derates the
        whole window's credit; nothing here forecasts house load, only
        solar, since only solar has a forecast to read.

        Without a forecast configured, or with no solar right now, the
        instantaneous reading is used unchanged — exactly as before this
        finding, so an install without a weather entity loses nothing.
        """
        if self.trajectory is None or current_solar_kw <= 0:
            return current_solar_kw
        now_fraction = self.trajectory.solar_fraction_at(now)
        if not now_fraction:
            return current_solar_kw
        worst_ratio = 1.0
        hours = max(1, ceil(hours_until_clear))
        for hour in range(1, hours + 1):
            later_fraction = self.trajectory.solar_fraction_at(
                now + timedelta(hours=hour)
            )
            if later_fraction is None:
                continue
            worst_ratio = min(worst_ratio, later_fraction / now_fraction)
        return current_solar_kw * worst_ratio

    def _is_running(self, room: RoomConfig) -> bool:
        """Whether the room's unit is cooling, heating or drying now."""
        return self._compressor_direction(room) != 0 or self._is_drying(room)

    def _note_power_samples(self, now: datetime, context: _PowerContext) -> None:
        """Keep the last fifteen minutes of house load and solar (DR-067).

        Also keeps, for each room whose unit is not running, the house load
        as the rest of the house it measures.
        """
        cutoff = now - READING_AVERAGE_WINDOW
        for samples, reading in (
            (self._budget_house_samples, context.house_load_w),
            (self._budget_solar_samples, context.solar_w),
        ):
            if reading is not None:
                samples.append((now, reading))
            while samples and samples[0][0] < cutoff:
                samples.popleft()
        if context.house_load_w is None:
            return
        for room in self.rooms.values():
            if self._is_running(room):
                continue
            own = self._base_load_samples.setdefault(room.room_id, deque())
            own.append((now, context.house_load_w))
            while own and own[0][0] < cutoff:
                own.popleft()

    def _average_solar_kw(self, context: _PowerContext) -> float:
        """Solar over the last fifteen minutes, in kW (DR-067)."""
        mean = mean_recent(self._budget_solar_samples, READING_AVERAGE_WINDOW)
        if mean is None:
            mean = context.solar_w or 0.0
        return max(mean, 0.0) / 1000.0

    def _rest_of_house_kw(
        self, room: RoomConfig, *, running: bool, own_draw_kw: float
    ) -> float | None:
        """What the rest of the house draws, in kW, for this room's budget.

        0.9.4, DR-067. The house load measured while this room's unit was not
        running, averaged: the room's own draw is never in it. Before the room
        has been seen off — it has run since Home Assistant started — the
        averaged house load less the room's estimated draw, as before 0.9.4.
        None where there is no house-load reading at all.
        """
        base = mean_recent(
            self._base_load_samples.get(room.room_id, ()), READING_AVERAGE_WINDOW
        )
        if base is not None:
            return max(base, 0.0) / 1000.0
        mean = mean_recent(self._budget_house_samples, READING_AVERAGE_WINDOW)
        if mean is None:
            return None
        return max(mean / 1000.0 - (own_draw_kw if running else 0.0), 0.0)

    def _power_ceiling(
        self,
        room: RoomConfig,
        inputs: RoomInputs,
        trace: DecisionTrace,
        now: datetime,
        capabilities: dict[str, bool],
    ) -> None:
        """The setpoint ceiling a tight power budget places on this room.

        0.8.10, findings 10 and 15. Replaces the pre-0.8.10 boolean veto.
        Extended to three states in 0.8.12.

        **`power_management` is the single gate for the whole mechanism.**
        "off" (the default): power management does not touch this room at
        all. "guidance": the ceiling is computed and reported — the trace and
        reasons show what the budget would do — but `_regulate` never clamps
        against it and nothing is stopped. "enforced": the ceiling applies
        whether the room is inside its band or calling for correction, and
        where the budget cannot afford even holding the room, the unit is
        turned off until it can (0.9.3, DR-063; until then enforcement never
        stopped the compressor). A coasting room is not throttled — it has no
        setpoint to throttle — but under enforced it is turned off too when
        nothing can be afforded, since dry mode runs the compressor and the
        fan draws power.

        This was corrected after being built the other way around: the
        first version applied a gentle within-band ceiling regardless of the
        setting, treating it as harmless because comfort was never at risk.
        That was Claude's own invention, not what was asked for. The
        setting is the user's choice between comfort and power management
        being the more important thing for this room, full stop — not a
        magnitude knob on top of an always-on baseline.

        **Solar forecast (0.9.3, DR-061).** Where a solar forecast is held,
        the allowance is the largest steady draw that keeps the battery above
        its reserve until the window clears, counting the forecast generation
        hour by hour (`power.forecast_allowance_kw`). Without one, the solar
        being produced now is used, derated by the weather forecast's
        clear-sky fraction, as before.

        **Hysteresis (0.9.3, DR-062).** The bin moves down at once and up only
        when the allowance clears the next bin by a margin and has held for a
        while (`power.held_ceiling_bin`).
        """
        if room.power_management == POWER_MANAGEMENT_OFF:
            # Power management does not touch this room. Comfort wins
            # unconditionally; nothing below this line applies.
            return
        if trace.actuator in (ActuatorStep.OFF, ActuatorStep.COVERS):
            # Lockout, unoccupied, an opening past its grace, or already
            # stopped for another reason. Nothing running to throttle.
            return
        if trace.mode is Mode.PRECOOL:
            # 0.8.13, DR-037. Precool runs in a window chosen because energy
            # is free, and drives to the bottom of its band, not the middle.
            # Rationing it would throttle the one mode that exists to spend
            # free energy ahead of a load.
            trace.reasons.append("power budget: precool is not rationed")
            return
        context = self._power_context
        if context is None or not context.engaged:
            return
        if self.tariff is None:
            return
        interval = self.tariff.interval_at(now)
        if interval is None or CONSTRAINT_NO_GRID_IMPORT not in interval.constraints:
            self._ceiling_bins.pop(room.room_id, None)
            self._shortfall_since.pop(room.room_id, None)
            return

        if (
            context.battery_soc_percent is None
            or context.battery_capacity_kwh is None
            or context.reserve_margin_kwh is None
        ):
            return

        hours_until_clear = self.tariff.hours_until_clear(
            CONSTRAINT_NO_GRID_IMPORT, now
        )
        if hours_until_clear is None or hours_until_clear <= 0:
            return

        available_kwh = battery_available_kwh(
            context.battery_soc_percent,
            context.battery_capacity_kwh,
            context.reserve_margin_kwh,
        )

        running = self._is_running(room)
        own_draw_kw = self._rated_kw_for(room) if running else 0.0
        # 0.9.4, DR-067. Averages over the last fifteen minutes, and the rest
        # of the house as measured while this room was not running. Until
        # 0.9.4 one reading was projected for the whole window, and this
        # room's own draw beyond its estimate was counted as the house's: the
        # Office turned itself off the minute after every start.
        current_solar_kw = self._average_solar_kw(context)
        rest_kw = self._rest_of_house_kw(
            room, running=running, own_draw_kw=own_draw_kw
        )

        slices = self._solar_forecast_slices(now, hours_until_clear)
        if slices is not None:
            other_house_kw = rest_kw or 0.0
            headroom_kwh = context.battery_capacity_kwh * max(
                0.0, 1.0 - context.battery_soc_percent / 100.0
            )
            allowance_kw = forecast_allowance_kw(
                available_kwh=available_kwh,
                headroom_kwh=headroom_kwh,
                slices=slices,
                other_house_kw=other_house_kw,
                plant_kw=plant_allowance_kw(
                    self.battery_max_discharge_kw, current_solar_kw, other_house_kw
                ),
            )
            trace.reasons.append(
                "power budget: "
                f"{sum(kwh for _, kwh in slices):.1f} kWh of forecast solar "
                f"counted over the {hours_until_clear:.1f} h until import is "
                "allowed again"
            )
        else:
            # 0.8.11, finding 18. Solar checked first, as a direct offset —
            # the battery only binds where solar is insufficient. The
            # arithmetic is in `power.budget_allowance_kw`.
            sustained_solar_kw = self._sustained_solar_kw(
                now, hours_until_clear, current_solar_kw
            )
            allowance_kw = budget_allowance_kw(
                available_kwh=available_kwh,
                hours_until_clear=hours_until_clear,
                max_discharge_kw=self.battery_max_discharge_kw,
                house_load_w=None if rest_kw is None else rest_kw * 1000.0,
                own_draw_kw=0.0,
                sustained_solar_kw=sustained_solar_kw,
            )

        draw_by_bin = [
            sum(
                self.draw_for(group).draw_kw(bin_index, ASSUMED_UNIT_KW)
                for group in room.groups
            )
            for bin_index in range(len(BIN_NAMES))
        ]
        previous = self._ceiling_bins.get(room.room_id)
        bin_index = held_ceiling_bin(
            allowance_kw,
            draw_by_bin,
            previous=previous[0] if previous else None,
            previous_since=previous[1] if previous else None,
            now=now,
        )
        enforced = room.power_management == POWER_MANAGEMENT_ENFORCED
        holding_off: datetime | None = None
        if enforced:
            # 0.9.4, DR-067. A room that was running is turned off only once
            # the shortfall has lasted `OFF_HOLD`, held at the gentlest bin
            # until then. Until 0.9.4 one short reading turned it off.
            was_on = previous[0] is not None if previous else running
            afforded = bin_index
            bin_index, since = hold_before_off(
                bin_index,
                was_on=was_on,
                shortfall_since=self._shortfall_since.get(room.room_id),
                now=now,
            )
            if since is None:
                self._shortfall_since.pop(room.room_id, None)
            else:
                self._shortfall_since[room.room_id] = since
                if afforded is None and bin_index is not None:
                    holding_off = since + OFF_HOLD
        if previous is None or previous[0] != bin_index:
            self._ceiling_bins[room.room_id] = (bin_index, now)

        guidance_note = (
            " (guidance only, not enforced)"
            if room.power_management == POWER_MANAGEMENT_GUIDANCE
            else ""
        )
        trace.power_budget_kw = round(allowance_kw, 3)
        trace.grid_importing_now = (
            None if context.grid_import_w is None else context.grid_import_w > 0
        )
        coasting = trace.mode is Mode.COAST
        if holding_off is not None:
            trace.reasons.append(
                "power budget: the "
                f"{allowance_kw:.2f} kW allowance does not cover holding at "
                "setpoint; held at the gentlest setting, and off at "
                f"{dt_util.as_local(holding_off):%H:%M} unless it recovers"
            )

        if bin_index is None:
            # Even holding at setpoint would exceed the allowance.
            trace.power_bin = BIN_NAMES[0]
            if enforced:
                # 0.9.3, DR-063. Enforced means no grid import is the more
                # important thing for this room, so it is stopped rather than
                # held at the room's own temperature and still drawing.
                trace.power_ceiling_c = None
                trace.actuator = ActuatorStep.OFF
                trace.hold_setpoint = False
                trace.comfort_reduction_active = True
                trace.reasons.append(
                    "power budget: even holding at setpoint exceeds the "
                    f"{allowance_kw:.2f} kW allowance; enforced, so the unit is "
                    "off until the budget allows it"
                )
                return
            trace.power_ceiling_c = 0.0
            trace.reasons.append(
                "power budget: even holding at setpoint exceeds the "
                f"{allowance_kw:.2f} kW allowance; ceiling held at 0.0 C"
                + guidance_note
            )
            return

        if coasting:
            # Nothing to throttle: the coasting step has no setpoint.
            trace.reasons.append(
                f"power budget: {allowance_kw:.2f} kW allowance, enough for this "
                "room while it coasts"
            )
            return

        trace.power_ceiling_c = self._BIN_CEILING_C[bin_index]
        trace.power_bin = BIN_NAMES[bin_index]
        # Whether the room is actually being held back by this right now:
        # enforced, and the room needs correction.
        trace.comfort_reduction_active = enforced and trace.demand is not None
        trace.reasons.append(
            f"power budget: {allowance_kw:.2f} kW allowance, "
            f"{BIN_NAMES[bin_index]} bin, ceiling "
            f"{trace.power_ceiling_c:.1f} C approach"
            + (
                guidance_note
                if guidance_note
                else ", comfort reduction in effect"
                if trace.demand is not None
                else ""
            )
        )

    def _interval_at(self, at: datetime) -> Interval | None:
        """The tariff interval in force, or None if there is no tariff.

        Also None past the end of the fetched series, which is a fetch that
        has been failing for a day rather than a plan with a gap in it. The
        repair issue raised by the fetch says which.
        """
        if self.tariff is None:
            return None
        interval = self.tariff.interval_at(at)
        if interval is None:
            LOGGER.warning(
                "The tariff series does not reach %s; it covers to %s",
                at,
                self.tariff.covers_until,
            )
        return interval

    def interval_now(self) -> Interval | None:
        """The interval in force right now, for the sensors."""
        return self._interval_at(dt_util.utcnow())

    @callback
    def _note_availability(self, entity_id: str, available: bool) -> None:
        """Log each availability transition once, not once per evaluation."""
        if available:
            if entity_id in self._unavailable:
                self._unavailable.discard(entity_id)
                LOGGER.info("%s is available again", entity_id)
            return
        if entity_id not in self._unavailable:
            self._unavailable.add(entity_id)
            LOGGER.warning(
                "%s is unavailable; rooms depending on it will hold or stop "
                "actuating until it returns",
                entity_id,
            )

    def _fresh(
        self,
        entity_id: str,
        state: State,
        tolerance: timedelta | None,
        room_id: str | None,
    ) -> bool:
        """Whether a state is recent enough to act on.

        Home Assistant keeps the last known state of a device that has fallen
        off the network for as long as it is configured, so a reading that is
        present is not the same as a reading that is current.

        0.9.3, DR-066. Age is measured from `last_reported`, not
        `last_updated`. `last_updated` only moves when the value or an
        attribute changes, so a sensor reporting the same temperature for two
        hours read as two hours stale, and the room held with no reading.
        `last_reported` moves on every report, changed or not. A sensor that
        reports nothing at all is still caught.

        0.9.6, DR-070. Room temperature and humidity have no age limit
        (`INDOOR_TOLERANCE` is None), so they pass here at any age and only
        an unavailable or unknown state makes them absent.
        """
        verdict = assess(
            max(state.last_updated, state.last_reported), dt_util.utcnow(), tolerance
        )
        if verdict.fresh:
            return True
        note = f"{entity_id}: {verdict.reason}"
        if room_id is not None:
            self._stale.setdefault(room_id, []).append(note)
        if entity_id not in self._unavailable:
            self._unavailable.add(entity_id)
            LOGGER.warning("%s", note)
        return False

    def _number(
        self,
        entity_id: str | None,
        tolerance: timedelta | None = INDOOR_TOLERANCE,
        room_id: str | None = None,
    ) -> float | None:
        """A numeric reading, or None where there is none or it is stale."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is not None and not self._fresh(
            entity_id, state, tolerance, room_id
        ):
            return None
        if state is None or state.state.lower() in _NON_NUMERIC:
            self._note_availability(entity_id, available=False)
            return None
        try:
            value = float(state.state)
        except ValueError:
            # Logged once, on the transition, like any other unavailable
            # reading. Until 0.9.3 this warned on every evaluation, every
            # thirty seconds, for as long as the sensor kept the value.
            if entity_id not in self._unavailable:
                LOGGER.warning(
                    "%s reported a non-numeric value: %s", entity_id, state.state
                )
            self._note_availability(entity_id, available=False)
            return None
        self._note_availability(entity_id, available=True)
        return value

    def _opening_open_since(self, room: RoomConfig) -> datetime | None:
        """When the longest-open opening in this room opened.

        `last_changed` rather than anything tracked here: it survives a
        restart, and a window that was already open before Home Assistant
        came up should not buy itself a fresh grace.

        None where nothing is open. An opening whose reading is too old to
        trust is not counted as open at all (see `_bool`), so an open opening
        always has a state, and with it a time it last changed.
        """
        opened: list[datetime] = []
        for entity_id in room.opening_entity_ids:
            if self._bool(entity_id, CONTACT_TOLERANCE, room.room_id) is not True:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None:
                opened.append(state.last_changed)
        return min(opened) if opened else None

    def _warn_about_opening(
        self, room: RoomConfig, inputs: RoomInputs, now: datetime
    ) -> None:
        """Queue the spoken warnings for an open window or door (DR-050).

        0.9.6, DR-073. Nothing is said for an opening made while outdoor air
        was judged better than the room's: that is what was advised, and the
        unit is stopped for it without a warning.
        """
        warnings = self._opening_warnings.setdefault(
            room.room_id, OpeningWarningState()
        )
        announcement = evaluate_opening_warnings(
            warnings,
            open_since=inputs.opening_open_since if inputs.opening_open else None,
            now=now,
            grace=timedelta(minutes=room.opening_grace_minutes),
            warning_grace=room.grace.warning_grace,
            announce=room.grace.announce and not inputs.free_cooling_advised,
        )
        if announcement is not Announcement.NONE:
            self._pending_announcements.append((room, announcement))

    def _bool(
        self,
        entity_id: str | None,
        tolerance: timedelta = CONTACT_TOLERANCE,
        room_id: str | None = None,
    ) -> bool | None:
        """An on/off reading, or None where there is none or it is stale."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is not None and not self._fresh(
            entity_id, state, tolerance, room_id
        ):
            return None
        if state is None or state.state.lower() in _NON_NUMERIC:
            self._note_availability(entity_id, available=False)
            return None
        self._note_availability(entity_id, available=True)
        return state.state == "on"


def _comfort_modes_from_store(
    stored: Mapping[str, str], rooms: set[str]
) -> dict[str, ComfortMode]:
    """The kept Comfort modes for rooms that still exist (DR-058).

    A value this build does not know, or a kept Schedule, is dropped: the
    room is on Schedule, which is what it would be with nothing kept.
    """
    modes: dict[str, ComfortMode] = {}
    for room_id, value in stored.items():
        if room_id not in rooms:
            continue
        try:
            mode = ComfortMode(value)
        except ValueError:
            continue
        if mode is not ComfortMode.SCHEDULE:
            modes[room_id] = mode
    return modes


def _watched_entities(rooms: dict[str, RoomConfig]) -> set[str]:
    """Every entity the coordinator reads, across all rooms."""
    watched: set[str] = set()
    for room in rooms.values():
        watched.update(
            entity_id
            for entity_id in (
                room.temperature_entity_id,
                room.humidity_entity_id,
                room.presence_entity_id,
                room.direct_sun_entity_id,
                room.heat_load_entity_id,
                room.air_movement_entity_id,
                room.sleep_schedule_entity_id,
            )
            if entity_id
        )
        watched.update(room.climate_entity_ids)
        watched.update(room.opening_entity_ids)
        watched.update(room.cover_entity_ids)
    return watched


def _rooms_from_entry(entry: HvacConfigEntry) -> dict[str, RoomConfig]:
    """Build room objects from configuration.

    Bad configuration raises ConfigEntryError, which Home Assistant shows on
    the integration page. Letting a KeyError or a ValueError escape here would
    surface as an unhandled traceback in the log and tell the user nothing.
    """
    rooms: dict[str, RoomConfig] = {}
    raw_rooms: list[dict[str, Any]] = entry.options.get(
        CONF_ROOMS, entry.data.get(CONF_ROOMS, [])
    )
    for raw in raw_rooms:
        try:
            bands = {
                Mode(name): ComfortBand(
                    low=values[CONF_BAND_LOW], high=values[CONF_BAND_HIGH]
                )
                for name, values in raw.get(CONF_BANDS, {}).items()
            }
        except (KeyError, TypeError, ValueError) as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="room_bands_invalid",
                translation_placeholders={
                    "room_id": str(raw.get(CONF_ROOM_ID, "?")),
                    "error": str(err),
                },
            ) from err

        try:
            room = _room_from_raw(raw, bands)
        except KeyError as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="room_setting_missing",
                translation_placeholders={
                    "room_id": str(raw.get(CONF_ROOM_ID, "?")),
                    "setting": str(err),
                },
            ) from err
        rooms[room.room_id] = room
    return _lock_out_shared_climate(rooms)


def _lock_out_shared_climate(
    rooms: dict[str, RoomConfig],
) -> dict[str, RoomConfig]:
    """Lock out every room that shares its climate entity with another.

    Two rooms driving one entity each command their own solved setpoint every
    cycle, and each one's dedupe cache in `actuator.py` sees only its own
    writes — so nothing errors, nothing logs, and the unit is left doing
    whichever arrived last. The configuration is reachable through the options
    flow today.

    A lockout is used rather than a `ConfigEntryError` because refusing to load
    the entry would take every correctly configured room down with the two that
    are not. The affected rooms stop actuating, say why in their trace, and a
    repair issue names the entity and the rooms.
    """
    claimed: dict[str, list[str]] = {}
    for room in rooms.values():
        for entity_id in room.climate_entity_ids:
            claimed.setdefault(entity_id, []).append(room.room_id)

    for entity_id, room_ids in claimed.items():
        if len(room_ids) < 2:
            continue
        others = {room_id: rooms[room_id].name for room_id in room_ids}
        for room_id in room_ids:
            named = sorted(name for key, name in others.items() if key != room_id)
            rooms[room_id] = replace(
                rooms[room_id],
                lockout_reason=(
                    f"{entity_id} is also configured for "
                    f"{', '.join(named)}; one climate entity cannot be driven "
                    "by more than one room"
                ),
            )
    return rooms


def _heads(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """A room's heads, reading either shape.

    The scalar `climate_entity_id` is pre-0.8.8. The migration rewrites every
    stored room, so this fallback exists for an entry that is being read
    before migration has run, and for nothing else.
    """
    heads = raw.get(CONF_CLIMATE_ENTITIES)
    if heads:
        return tuple(str(entity) for entity in heads)
    single = raw.get(CONF_CLIMATE_ENTITY)
    return (str(single),) if single else ()


def _room_from_raw(
    raw: dict[str, Any], bands: dict[Mode, ComfortBand]
) -> RoomConfig:
    """Build one room. Raises KeyError if a required field is absent."""
    return RoomConfig(
        room_id=raw[CONF_ROOM_ID],
        name=raw["name"],
        climate_entity_ids=_heads(raw),
        head_groups={
            str(entity): str(group)
            for entity, group in (raw.get(CONF_HEAD_GROUPS) or {}).items()
            if group
        },
        bands=bands,
        temperature_entity_id=raw.get(CONF_TEMPERATURE_ENTITY),
        humidity_entity_id=raw.get(CONF_HUMIDITY_ENTITY),
        presence_entity_id=raw.get(CONF_PRESENCE_ENTITY),
        sleep_schedule_entity_id=raw.get(CONF_SLEEP_SCHEDULE_ENTITY),
        direct_sun_entity_id=raw.get(CONF_DIRECT_SUN_ENTITY),
        window_direction=raw.get(CONF_WINDOW_DIRECTION),
        overhang_projection_m=raw.get(CONF_OVERHANG_PROJECTION),
        overhang_height_m=raw.get(CONF_OVERHANG_HEIGHT),
        heat_load_entity_id=raw.get(CONF_HEAT_LOAD_ENTITY),
        air_movement_entity_id=raw.get(CONF_FAN_ENTITY),
        grace=GraceSettings.from_minutes(
            occupied_after=raw.get(CONF_OCCUPIED_AFTER),
            vacant_after=raw.get(CONF_VACANT_AFTER),
            warning_grace=raw.get(CONF_WARNING_GRACE),
            announce=bool(raw.get(CONF_ANNOUNCE, False)),
        ),
        announce_target_entity_ids=tuple(raw.get(CONF_ANNOUNCE_TARGETS, []) or []),
        opening_entity_ids=tuple(raw.get(CONF_OPENING_ENTITIES, []) or []),
        cover_entity_ids=tuple(raw.get(CONF_COVER_ENTITIES, []) or []),
        allow_cover_control=bool(raw.get(CONF_ALLOW_COVER_CONTROL, True)),
        lockout_reason=raw.get(CONF_LOCKOUT_REASON),
        power_management=power_management_from_raw(
            raw.get(CONF_ALLOW_COMFORT_REDUCTION, POWER_MANAGEMENT_OFF)
        ),
        opening_grace_minutes=_opening_grace_from_raw(raw.get(CONF_OPENING_GRACE)),
        capabilities=RoomCapabilities.from_dict(raw.get(CONF_CAPABILITIES)),
    )


def _opening_grace_from_raw(value: object) -> float:
    """A room's opening grace in minutes, five where none is stored."""
    try:
        minutes = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_OPENING_GRACE_MINUTES
    return minutes if minutes >= 0 else DEFAULT_OPENING_GRACE_MINUTES


def _announcement_text(room: RoomConfig, announcement: Announcement) -> str:
    """What to say. Plain, and it names the room so it is unambiguous."""
    if announcement is Announcement.FREE_COOLING:
        return (
            f"Opening the windows would help the {room.name} right now — it's "
            "cooler and drier outside than in."
        )
    if announcement is Announcement.OPENING_FIRST_WARNING:
        minutes = int(room.grace.warning_grace.total_seconds() // 60)
        return (
            f"A window or door in the {room.name} has been open for a while. "
            f"The air conditioning will turn off in about {minutes} minutes "
            "unless it is closed."
        )
    if announcement is Announcement.OPENING_FINAL_WARNING:
        return (
            f"Turning the {room.name} air conditioning off because a window or "
            "door is open."
        )
    minutes = int(room.grace.vacant_after.total_seconds() // 60)
    if announcement is Announcement.FIRST_WARNING:
        return (
            f"The {room.name} has been empty for {minutes} minutes and the air "
            "conditioning is still running."
        )
    return f"Turning the {room.name} air conditioning off."
