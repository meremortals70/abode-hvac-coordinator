"""The coordinator.

Reads Home Assistant state, hands it to the pure evaluator in `modes.py`, and
publishes the resulting traces. Every decision is made in the pure modules; this
file gathers inputs and manages lifecycle only.

Actuation itself lives in `actuator.py`. This file gathers inputs, runs the
evaluator and hands the resulting decision on.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID
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
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actuator import Actuator, mean_cover_position, supported_hvac_modes
from .const import (
    COAST_HORIZON_HOURS,
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COVER_ENTITIES,
    CONF_DIRECT_SUN_ENTITY,
    CONF_FAN_ENTITY,
    CONF_HEAT_LOAD_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_PRESENCE_ENTITY,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_TARIFF_ENTRY_ID,
    CONF_TEMPERATURE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_WINDOW_DIRECTION,
    DOMAIN,
    EVALUATION_INTERVAL,
    ISSUE_NO_BANDS,
    ISSUE_TARIFF_UNAVAILABLE,
    ISSUE_UNRECOGNISED_CONSTRAINT,
    LOGGER,
    PRECOOL_DEMAND_MARGIN_C,
    TARIFF_DOMAIN,
    TARIFF_HORIZON_HOURS,
    TARIFF_REFRESH_INTERVAL,
    TARIFF_RESOLUTION_MINUTES,
    TARIFF_SERVICE_GET_INTERVALS,
)
from .forecast import (
    DEFAULT_HORIZON_HOURS,
    DemandForecast,
    RoomForecastInput,
    build_forecast,
)
from .grace import Announcement, GraceSettings, GraceState, evaluate_grace
from .hci import ComfortBand, dry_bulb_for_index
from .models import ActuatorStep, DecisionTrace, Mode, RoomConfig, RoomInputs
from .modes import evaluate_room
from .psychro import dew_point_c
from .regulate import (
    RegulatorState,
    commanded_setpoint,
    integrate,
    note_transition,
    permit_transition,
)
from .scheduling import plan_precondition
from .staleness import (
    CONTACT_TOLERANCE,
    INDOOR_TOLERANCE,
    OUTDOOR_TOLERANCE,
    PRESENCE_TOLERANCE,
    assess,
)
from .store import ModelStore
from .sun import azimuth_for_direction, sun_on_window
from .tariff import (
    CONSTRAINT_NO_GRID_IMPORT,
    CONSTRAINT_PRECOOL_OPPORTUNITY,
    Interval,
    TariffPayloadError,
    TariffSeries,
)
from .thermal import Observation, ThermalModel

if TYPE_CHECKING:
    from . import HvacConfigEntry

#: State strings that carry no reading, as distinct from a number.
_NON_NUMERIC = frozenset({"unknown", "unavailable", "none", ""})


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
            CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
        )
        self.outdoor_humidity_entity_id: str | None = config_entry.options.get(
            CONF_OUTDOOR_HUMIDITY_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_HUMIDITY_ENTITY),
        )
        #: Layer 2 state, one per room. Not persisted: a trim is only valid
        #: for the conditions that produced it.
        self._regulators: dict[str, RegulatorState] = {}
        #: The mode each room was in last evaluation, and when it changed, so
        #: the band can ramp across a sleep transition instead of stepping.
        self._previous_mode: dict[str, Mode] = {}
        self._mode_changed_at: dict[str, datetime] = {}
        #: Feeds reported stale this evaluation, per room, for the trace.
        self._stale: dict[str, list[str]] = {}
        #: Why a heading-home room is not actuating yet, for the trace.
        self._precondition_reason: dict[str, str] = {}
        #: Learned thermal behaviour, one per room, restored from the store.
        self.models: dict[str, ThermalModel] = {}
        #: The last reading of each room, so the next evaluation can measure
        #: what actually happened over the interval and learn from it.
        self._previous: dict[str, tuple[datetime, float, float, int, bool]] = {}
        #: The published demand forecast. Never contains vendor concepts.
        self.forecast: DemandForecast | None = None
        #: Occupancy grace, one per room. Raw presence is the wrong signal for
        #: a compressor: someone dropping a laptop off should not start it, and
        #: someone signing for a delivery should not stop it.
        self._grace: dict[str, GraceState] = {}
        #: Why each room is considered occupied or not, for the trace.
        self._grace_reason: dict[str, str] = {}
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
        #: Heading-home requests, per room, with the deadline if one was given.
        #: There is no target: a heading-home room is driven to its comfort band.
        self._heading_home: dict[str, datetime | None] = {}

    async def async_prepare(self) -> None:
        """Fetch the tariff, subscribe to entities, and raise any issues."""
        await self._async_refresh_tariff()
        if self.tariff_entry_id:
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._async_tariff_tick,
                    TARIFF_REFRESH_INTERVAL,
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


    async def _async_tariff_tick(self, now: datetime) -> None:
        """Refetch the series on the interval timer, then re-evaluate."""
        await self._async_refresh_tariff()
        self._async_check_configuration()
        await self.async_request_refresh()

    async def _async_refresh_tariff(self) -> None:
        """Fetch the forward interval series from Abode Power Tariffs.

        A failure holds the series already in hand rather than discarding it.
        The alternative — dropping to no tariff on one bad call — would turn a
        momentary reload of the tariff integration into a room losing its
        constraints, which is a worse outcome than a series a few minutes old.
        """
        if not self.tariff_entry_id:
            self.tariff = None
            return

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
                "Abode Power Tariffs is not installed, or is not loaded"
            )
            return
        except HomeAssistantError as err:
            self._async_tariff_issue(str(err))
            return

        try:
            self.tariff = TariffSeries.from_response(
                dict(response) if response else None, dt_util.utcnow()
            )
        except TariffPayloadError as err:
            self._async_tariff_issue(f"the interval series could not be read: {err}")
            return

        LOGGER.debug(
            "Tariff series refreshed: %d intervals, covering to %s",
            len(self.tariff.intervals),
            self.tariff.covers_until,
        )
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_TARIFF_UNAVAILABLE)

    @callback
    def _async_tariff_issue(self, reason: str) -> None:
        """Raise a repair issue naming why the tariff could not be read."""
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

    @callback
    def async_request_heading_home(
        self, room_id: str, deadline: datetime | None = None
    ) -> None:
        """Bring a room to its comfort band ahead of arrival."""
        self._heading_home[room_id] = deadline

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
        for room in self.rooms.values():
            inputs = self._inputs_for(room, now)
            self._learn(room, inputs, now)
            trace = evaluate_room(room, inputs)
            trace.model = self.model_for(room.room_id).diagnostics()
            if reason := self._grace_reason.get(room.room_id):
                trace.reasons.append(reason)
            if reason := self._precondition_reason.get(room.room_id):
                trace.rejected.append(reason)
            trace.stale_feeds = self._stale.get(room.room_id, [])
            self._regulate(room, inputs, trace, now)
            self._guard_cycling(room, trace, now)
            self._track_mode(room.room_id, trace.mode, now)
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

        await self._async_announce()
        self._async_remove_stale_devices(set(traces))
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
        """Layer 2. Trim the commanded setpoint until the room reaches target.

        The unit's own thermostat regulates against its return-air sensor,
        which is not the room. This closes the outer loop around it, and it is
        the only place in the project allowed to command a temperature other
        than the one the comfort index solved for.
        """
        state = self._regulators.setdefault(room.room_id, RegulatorState())
        integrate(
            state,
            target_c=trace.target_dry_bulb_c,
            room_c=inputs.temperature_c,
            now=now,
            regulating=trace.actuator is ActuatorStep.COMPRESSOR,
        )
        trace.reasons.extend(state.notes)
        trace.regulation_trim_c = state.trim_c
        trace.commanded_dry_bulb_c = commanded_setpoint(
            state, trace.target_dry_bulb_c
        )

    def _guard_cycling(
        self, room: RoomConfig, trace: DecisionTrace, now: datetime
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
        state = self._regulators.setdefault(room.room_id, RegulatorState())
        wants = trace.actuator is ActuatorStep.COMPRESSOR
        permitted, reason = permit_transition(state, want_running=wants, now=now)
        if not permitted and reason is not None:
            trace.rejected.append(reason)
            # Hold what the unit is already doing rather than commanding the
            # transition. Stopping is deferred by holding the compressor;
            # starting is deferred by commanding nothing.
            trace.actuator = (
                ActuatorStep.COMPRESSOR if state.running else ActuatorStep.NONE
            )
            return
        note_transition(state, running=wants, now=now)

    def _track_mode(self, room_id: str, mode: Mode, now: datetime) -> None:
        """Remember the mode and when it last changed, for the band ramp."""
        if self._previous_mode.get(room_id) is not mode:
            if room_id in self._previous_mode:
                self._mode_changed_at[room_id] = now
            self._previous_mode[room_id] = mode

    def _learn(self, room: RoomConfig, inputs: RoomInputs, now: datetime) -> None:
        """Fold the interval since the last evaluation into the room's model.

        What the room actually did is measured at both ends; nothing here is
        inferred from what was commanded.
        """
        previous = self._previous.get(room.room_id)
        current = (
            now,
            inputs.temperature_c,
            inputs.relative_humidity,
            self._compressor_direction(room),
            self._is_drying(room),
        )
        if inputs.temperature_c is None or inputs.relative_humidity is None:
            # No reading at this end of the interval. Drop the anchor rather
            # than learning from a gap.
            self._previous.pop(room.room_id, None)
            return

        self._previous[room.room_id] = current  # type: ignore[assignment]
        if previous is None:
            return

        started, start_c, start_rh, compressor, drying = previous
        if start_c is None or start_rh is None:
            return

        elapsed = (now - started).total_seconds() / 3600.0
        self.model_for(room.room_id).observe(
            Observation(
                elapsed_hours=elapsed,
                indoor_start_c=start_c,
                indoor_end_c=inputs.temperature_c,
                humidity_start=start_rh,
                humidity_end=inputs.relative_humidity,
                outdoor_c=self._number(self.outdoor_entity_id),
                direct_sun=inputs.direct_sun is True,
                compressor=compressor,
                drying=drying,
            )
        )

    def _predicted_to_hold(self, room: RoomConfig) -> bool | None:
        """Whether the room stays in band unaided over the coast horizon.

        None means the model cannot say, which the evaluator treats as "do not
        coast" rather than as "yes". That is the hysteresis fallback: until the
        filter has converged, the band is simply held.
        """
        model = self.model_for(room.room_id)
        indoor = self._number(room.temperature_entity_id)
        humidity = self._number(room.humidity_entity_id)
        if indoor is None or humidity is None:
            return None

        band = room.band_for(Mode.SLEEP if self._sleeping(room) else Mode.OCCUPIED)
        if band is None:
            return None

        # The band is in comfort index; the model works in dry bulb. Convert
        # the bounds at the current humidity so the two are comparable.
        lower_c = dry_bulb_for_index(band.low, humidity)
        upper_c = dry_bulb_for_index(band.high, humidity)

        return model.holds_through(
            indoor,
            self._number(self.outdoor_entity_id),
            direct_sun=self._direct_sun(room) is True,
            hours=COAST_HORIZON_HOURS,
            lower_c=lower_c,
            upper_c=upper_c,
        )

    def _demand_ahead(self, room: RoomConfig) -> bool:
        """Whether this room is forecast to need cooling later today.

        Precool banks thermal mass against a load that is coming. Without a
        load coming it is just spending energy early, so this gates it.
        """
        model = self.model_for(room.room_id)
        indoor = self._number(room.temperature_entity_id)
        outdoor = self._number(self.outdoor_entity_id)
        if indoor is None or outdoor is None:
            return False

        drift = model.drift_rate(
            indoor, outdoor, direct_sun=self._direct_sun(room) is True
        )
        if drift is None:
            # Not learned yet. Outdoor above indoor is the honest fallback: the
            # room will warm, even if we cannot say how fast.
            return outdoor > indoor + PRECOOL_DEMAND_MARGIN_C
        return drift > 0

    def _graced_presence(self, room: RoomConfig, now: datetime) -> bool | None:
        """Presence after the grace periods, not the raw sensor.

        Announcements raised here are dispatched by the caller, so that this
        stays a pure read of state.
        """
        if room.presence_entity_id is None:
            return None

        state = self._grace.setdefault(room.room_id, GraceState())
        result = evaluate_grace(
            state,
            self._bool(room.presence_entity_id, PRESENCE_TOLERANCE, room.room_id),
            now,
            room.grace,
        )
        self._grace_reason[room.room_id] = result.reason
        if result.announcement is not Announcement.NONE:
            self._pending_announcements.append((room, result.announcement))
        return result.occupied

    def outdoor_reading(self) -> float | None:
        """The outdoor temperature, for the house-wide sensor."""
        return self._number(self.outdoor_entity_id, OUTDOOR_TOLERANCE)

    def outdoor_dew_point(self) -> float | None:
        """Outdoor dew point, where both outdoor feeds are configured."""
        temp = self.outdoor_reading()
        humidity = self._number(self.outdoor_humidity_entity_id, OUTDOOR_TOLERANCE)
        if temp is None or humidity is None:
            return None
        return round(dew_point_c(temp, humidity), 1)

    def _precondition_plan(self, room: RoomConfig, now: datetime):
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
        """Whether the room's air is moving.

        A configured fan entity answers it directly. Otherwise the air
        conditioner itself counts: any mode other than off moves air.
        """
        if room.air_movement_entity_id and (
            moving := self._bool(room.air_movement_entity_id)
        ) is not None:
            return moving
        state = self.hass.states.get(room.climate_entity_id)
        return state is not None and state.state not in ("off", "unavailable", "unknown")

    def _sleeping(self, room: RoomConfig) -> bool:
        """Whether the room's sleep schedule is currently on."""
        return self._bool(room.sleep_schedule_entity_id) is True

    async def _async_announce(self) -> None:
        """Speak any warnings raised this evaluation."""
        for room, announcement in self._pending_announcements:
            if not room.announce_target_entity_ids:
                continue
            message = _announcement_text(room, announcement)
            try:
                await self.hass.services.async_call(
                    "tts",
                    "speak",
                    {
                        ATTR_ENTITY_ID: list(room.announce_target_entity_ids),
                        "message": message,
                    },
                    blocking=False,
                )
            except HomeAssistantError as err:
                LOGGER.warning("Announcement for %s failed: %s", room.room_id, err)
        self._pending_announcements.clear()

    def _compressor_direction(self, room: RoomConfig) -> int:
        """Whether the unit is moving sensible heat, and which way."""
        state = self.hass.states.get(room.climate_entity_id)
        if state is None:
            return 0
        if state.state == "cool":
            return -1
        if state.state == "heat":
            return 1
        if state.state in ("heat_cool", "auto"):
            # Direction is whatever the unit decided. hvac_action says which.
            action = state.attributes.get("hvac_action")
            if action == "cooling":
                return -1
            if action == "heating":
                return 1
        return 0

    def _is_drying(self, room: RoomConfig) -> bool:
        """Whether the unit is in dry mode."""
        state = self.hass.states.get(room.climate_entity_id)
        return state is not None and state.state == "dry"

    def _persist_models(self) -> None:
        """Write learned state back to the store, on its own delay."""
        for room_id, model in self.models.items():
            record = dict(self.store.room(room_id))
            record["thermal"] = model.as_dict()
            self.store.update_room(room_id, record)

    def _build_forecast(
        self, now: datetime, traces: dict[str, DecisionTrace]
    ) -> DemandForecast:
        """Project HVAC energy over the horizon. No vendor concepts in it."""
        outdoor = self._number(self.outdoor_entity_id)
        inputs: list[RoomForecastInput] = []
        for room_id, room in self.rooms.items():
            trace = traces.get(room_id)
            inputs.append(
                RoomForecastInput(
                    room_id=room_id,
                    model=self.model_for(room_id),
                    indoor_c=self._number(room.temperature_entity_id),
                    target_c=trace.target_dry_bulb_c if trace else None,
                    outdoor_c=outdoor,
                    direct_sun=self._direct_sun(room) is True,
                    will_run=trace is not None
                    and trace.mode not in (Mode.LOCKOUT, Mode.UNOCCUPIED),
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
            for room_id in stale:
                self.actuator.forget(room_id)
                self.models.pop(room_id, None)
                self._grace.pop(room_id, None)
                self._grace_reason.pop(room_id, None)
                self._regulators.pop(room_id, None)
                self._previous_mode.pop(room_id, None)
                self._mode_changed_at.pop(room_id, None)
                self._precondition_reason.pop(room_id, None)
                self._previous.pop(room_id, None)
                self.store.forget_room(room_id)
                if device := registry.async_get_device(
                    identifiers={(DOMAIN, room_id)}
                ):
                    registry.async_update_device(
                        device_id=device.id,
                        remove_config_entry_id=self.config_entry.entry_id,
                    )
        self.previous_rooms = current

    def _inputs_for(self, room: RoomConfig, now: datetime) -> RoomInputs:
        """Assemble everything the evaluator is allowed to see."""
        interval = self._interval_at(now)
        constraints = interval.constraints if interval else frozenset()
        plan = self._precondition_plan(room, now)
        if plan is not None and not plan.start_now:
            self._precondition_reason[room.room_id] = plan.reason
        else:
            self._precondition_reason.pop(room.room_id, None)

        return RoomInputs(
            now=now,
            temperature_c=self._number(
                room.temperature_entity_id, INDOOR_TOLERANCE, room.room_id
            ),
            relative_humidity=self._number(
                room.humidity_entity_id, INDOOR_TOLERANCE, room.room_id
            ),
            presence=self._graced_presence(room, now),
            illuminance_lux=self._number(
                room.illuminance_entity_id, INDOOR_TOLERANCE, room.room_id
            ),
            direct_sun=self._direct_sun(room),
            heat_load=self._bool(room.heat_load_entity_id) is True,
            air_moving=self._air_moving(room),
            has_covers=bool(room.cover_entity_ids),
            cover_position=mean_cover_position(self.hass, room.cover_entity_ids),
            **self._capabilities(room),
            opening_open=any(
                self._bool(entity_id, CONTACT_TOLERANCE, room.room_id) is True
                for entity_id in room.opening_entity_ids
            ),
            precool_opportunity=CONSTRAINT_PRECOOL_OPPORTUNITY in constraints,
            no_grid_import=CONSTRAINT_NO_GRID_IMPORT in constraints,
            coasting_permitted=interval.coasting_permitted if interval else True,
            outdoor_c=self.outdoor_reading(),
            outdoor_relative_humidity=self._number(
                self.outdoor_humidity_entity_id, OUTDOOR_TOLERANCE
            ),
            precondition_ready=plan is None or plan.start_now,
            previous_mode=self._previous_mode.get(room.room_id),
            mode_changed_at=self._mode_changed_at.get(room.room_id),
            heading_home=room.room_id in self._heading_home,
            precondition_deadline=self._heading_home.get(room.room_id),
            predicted_to_hold=self._predicted_to_hold(room),
            forecast_demand_ahead=self._demand_ahead(room),
            sleep_schedule_active=self._bool(room.sleep_schedule_entity_id)
            is True,
        )

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
        state = self.hass.states.get(room.climate_entity_id)
        if state is None:
            return {
                "can_cool": False,
                "can_heat": False,
                "can_dry": False,
                "can_fan_only": False,
            }
        modes = supported_hvac_modes(state)
        return {
            "can_cool": bool(modes & {"cool", "heat_cool", "auto"}),
            "can_heat": bool(modes & {"heat", "heat_cool", "auto"}),
            "can_dry": "dry" in modes,
            "can_fan_only": "fan_only" in modes,
        }

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
        self, entity_id: str, state: State, tolerance: timedelta, room_id: str | None
    ) -> bool:
        """Whether a state is recent enough to act on.

        Home Assistant keeps the last known state of a device that has fallen
        off the network for as long as it is configured, so a reading that is
        present is not the same as a reading that is current.
        """
        verdict = assess(state.last_updated, dt_util.utcnow(), tolerance)
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
        tolerance: timedelta = INDOOR_TOLERANCE,
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
            LOGGER.warning(
                "%s reported a non-numeric value: %s", entity_id, state.state
            )
            self._note_availability(entity_id, available=False)
            return None
        self._note_availability(entity_id, available=True)
        return value

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
                room.illuminance_entity_id,
                room.direct_sun_entity_id,
                room.heat_load_entity_id,
                room.air_movement_entity_id,
                room.sleep_schedule_entity_id,
            )
            if entity_id
        )
        watched.add(room.climate_entity_id)
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
                f"Comfort bands for room {raw.get(CONF_ROOM_ID, '?')} are "
                f"invalid: {err}"
            ) from err

        try:
            room = _room_from_raw(raw, bands)
        except KeyError as err:
            raise ConfigEntryError(
                f"Room configuration is missing {err}"
            ) from err
        rooms[room.room_id] = room
    return rooms


def _room_from_raw(
    raw: dict[str, Any], bands: dict[Mode, ComfortBand]
) -> RoomConfig:
    """Build one room. Raises KeyError if a required field is absent."""
    return RoomConfig(
        room_id=raw[CONF_ROOM_ID],
        name=raw["name"],
        climate_entity_id=raw[CONF_CLIMATE_ENTITY],
        bands=bands,
        temperature_entity_id=raw.get(CONF_TEMPERATURE_ENTITY),
        humidity_entity_id=raw.get(CONF_HUMIDITY_ENTITY),
        presence_entity_id=raw.get(CONF_PRESENCE_ENTITY),
        sleep_schedule_entity_id=raw.get(CONF_SLEEP_SCHEDULE_ENTITY),
        illuminance_entity_id=raw.get(CONF_ILLUMINANCE_ENTITY),
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
        lockout_reason=raw.get(CONF_LOCKOUT_REASON),
    )


def _announcement_text(room: RoomConfig, announcement: Announcement) -> str:
    """What to say. Plain, and it names the room so it is unambiguous."""
    minutes = int(room.grace.vacant_after.total_seconds() // 60)
    if announcement is Announcement.FIRST_WARNING:
        return (
            f"The {room.name} has been empty for {minutes} minutes and the air "
            "conditioning is still running."
        )
    return f"Turning the {room.name} air conditioning off."
