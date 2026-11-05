"""The setup, reconfigure and options screens (Sections 7 and 8) [R18].

First setup asks for the house, then adds the first room. Reconfigure
reopens the house's screens. The options add, edit and remove rooms
without a reload, and seed or reset a room's learning. Every field has a
selector, a label and an explanation in strings.json; every error is
translated and the form is shown again so it can be corrected.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant import data_entry_flow
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import UnitOfEnergy, UnitOfLength, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    EntityWithDeviceFilterSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import capabilities
from .const import (
    BAND_FIELD_MAX,
    BAND_FIELD_MIN,
    BAND_FIELD_STEP,
    CONF_ALLOW_BLIND_CONTROL,
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_THROUGH,
    CONF_BATTERY_CHARGE,
    CONF_BATTERY_MAX_DISCHARGE,
    CONF_BATTERY_SIZE,
    CONF_BLINDS,
    CONF_CLIMATE,
    CONF_EMPTY_AFTER,
    CONF_FREQUENCY_SENSOR,
    CONF_GRID_POSITIVE_IS_IMPORT,
    CONF_GRID_POWER,
    CONF_HOUSE_LOAD,
    CONF_HUMIDITY_SENSOR,
    CONF_LOCKOUT_REASON,
    CONF_NAME,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_GRACE,
    CONF_OPENINGS,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_OUTDOOR_UNIT,
    CONF_OUTDOOR_WIND,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_POWER_MANAGEMENT,
    CONF_PRESENCE_SENSOR,
    CONF_RESERVE_MARGIN,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE,
    CONF_SOLAR_FORECAST_SOURCES,
    CONF_SOLAR_POWER,
    CONF_SUN_SENSOR,
    CONF_TARIFF_ENTRY,
    CONF_TEMPERATURE_SENSOR,
    CONF_TTS_ENGINE,
    CONF_WARNING_GRACE,
    CONF_WEATHER,
    CONF_WINDOW_FACING,
    DOMAIN,
    OVERHANG_FIELD_MAX_M,
    OVERHANG_FIELD_STEP_M,
    WINDOW_FACINGS,
)
from .coordinator import native_limits, native_temperature_unit
from .forms import (
    BAND_KEYS,
    ENTRY_MINOR_VERSION,
    ENTRY_VERSION,
    ROOM_DEFAULTS,
    band_inverted,
    build_room,
    clean_house,
    climate_used_elsewhere,
    grid_sensor_changed,
    house_from_data,
    lockout_choices,
    outdoor_unit_choices,
    power_kw,
    remove_room,
    replace_room,
    room_answers,
    rooms_from_options,
    summary,
)
from .models import CapabilityProfile, PowerManagement

if TYPE_CHECKING:
    from . import AbodeHvacConfigEntry

TITLE = "Abode HVAC Coordinator"
TARIFF_INTEGRATION = "abode_power_tariffs"
MINUTES_FIELD_MAX = 240.0  # [Set]
GRID_IMPORT = "import"
GRID_EXPORT = "export"


def _shown_if(key: str, field: str, value: bool | None = None) -> probatio.Optional:
    """An optional field shown only while another field is filled in (value
    None) or holds value. Home Assistant 2026.11 hides it and leaves it out
    of the answers otherwise (data_entry_flow.Optional's visible,
    helpers/field_visibility.py, 2026.11.0b0); before 2026.11 the field is
    always shown, as before [HA]."""
    marker = getattr(data_entry_flow, "Optional", None)
    if marker is None:
        return probatio.Optional(key)
    condition: dict[str, Any] = (
        {"field": field, "operator": "exists"} if value is None else {"field": field, "value": value}
    )
    shown: probatio.Optional = marker(key, visible=condition)
    return shown


def _entity(domain: str | list[str], device_class: str | None = None, multiple: bool = False) -> EntitySelector:
    f = EntityWithDeviceFilterSelectorConfig(domain=domain)
    if device_class is not None:
        f["device_class"] = device_class
    return EntitySelector(EntitySelectorConfig(filter=f, multiple=multiple))


def _number(lo: float, hi: float, step: float, unit: str | None = None) -> NumberSelector:
    cfg = NumberSelectorConfig(min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX)
    if unit is not None:
        cfg["unit_of_measurement"] = unit
    return NumberSelector(cfg)


def _minutes() -> NumberSelector:
    return _number(0.0, MINUTES_FIELD_MAX, 0.5, UnitOfTime.MINUTES)


def _band() -> NumberSelector:
    return _number(BAND_FIELD_MIN, BAND_FIELD_MAX, BAND_FIELD_STEP)


# --------------------------------------------------------------------------
# The house's screens, shared by first setup and Reconfigure
# --------------------------------------------------------------------------


TARIFF_SCHEMA = probatio.Schema({
    probatio.Optional(CONF_TARIFF_ENTRY): ConfigEntrySelector(ConfigEntrySelectorConfig(integration=TARIFF_INTEGRATION)),
})
OUTDOOR_SCHEMA = probatio.Schema({
    probatio.Optional(CONF_OUTDOOR_TEMPERATURE): _entity("sensor", "temperature"),
    probatio.Optional(CONF_OUTDOOR_HUMIDITY): _entity("sensor", "humidity"),
    probatio.Optional(CONF_OUTDOOR_WIND): _entity("sensor", "wind_speed"),
})
FORECAST_SCHEMA = probatio.Schema({probatio.Optional(CONF_WEATHER): _entity("weather")})
ANNOUNCE_SCHEMA = probatio.Schema({probatio.Optional(CONF_TTS_ENGINE): _entity("tts")})


async def _solar_forecast_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Every config entry whose integration offers the energy platform's
    solar forecast [D061]."""
    from homeassistant.components.energy.websocket_api import async_get_energy_platforms

    domains = await async_get_energy_platforms(hass)
    return [SelectOptionDict(value=e.entry_id, label=e.title) for e in hass.config_entries.async_entries()
            if e.domain in domains]


def _power_schema(solar_options: list[SelectOptionDict]) -> probatio.Schema:
    fields: dict[Any, Any] = {
        probatio.Optional(CONF_BATTERY_CHARGE): _entity("sensor", "battery"),
        # without a charge sensor the budget sets no limit, so the battery's figures are asked for with it
        _shown_if(CONF_BATTERY_SIZE, CONF_BATTERY_CHARGE): _number(0.0, 1000.0, 0.1, UnitOfEnergy.KILO_WATT_HOUR),
        _shown_if(CONF_BATTERY_MAX_DISCHARGE, CONF_BATTERY_CHARGE): _number(0.0, 100.0, 0.1, UnitOfPower.KILO_WATT),
        probatio.Optional(CONF_SOLAR_POWER): _entity("sensor", "power"),
        probatio.Optional(CONF_HOUSE_LOAD): _entity("sensor", "power"),
        _shown_if(CONF_RESERVE_MARGIN, CONF_BATTERY_CHARGE): _number(0.0, 1000.0, 0.1, UnitOfEnergy.KILO_WATT_HOUR),
        probatio.Optional(CONF_GRID_POWER): _entity("sensor", "power"),
    }
    if solar_options:
        # the solar forecast is read only with a solar power sensor (coordinator.async_read_solar)
        fields[_shown_if(CONF_SOLAR_FORECAST_SOURCES, CONF_SOLAR_POWER)] = SelectSelector(
            SelectSelectorConfig(options=solar_options, multiple=True, mode=SelectSelectorMode.LIST)
        )
    return probatio.Schema(fields)


GRID_SCHEMA = probatio.Schema({
    probatio.Required(CONF_GRID_POSITIVE_IS_IMPORT): SelectSelector(SelectSelectorConfig(
        options=[GRID_IMPORT, GRID_EXPORT], mode=SelectSelectorMode.LIST, translation_key="grid_direction")),
})


class _HouseScreens:
    """The House screens: Tariff, Outdoor, Forecast, Power, (Grid
    direction), Announcements [Set]. The answers gather in self.house."""

    hass: HomeAssistant
    house: dict[str, Any]
    house_before: Mapping[str, Any]

    if TYPE_CHECKING:  # provided by the flow that uses these screens

        def async_show_form(self, **kwargs: Any) -> ConfigFlowResult: ...

        def add_suggested_values_to_schema(
            self, data_schema: probatio.Schema, suggested_values: Mapping[str, Any] | None
        ) -> probatio.Schema: ...

        async def async_house_done(self) -> ConfigFlowResult: ...

        def grid_suggestion(self, grid_entity: str) -> bool | None: ...

    def _form(self, step_id: str, schema: probatio.Schema, errors: dict[str, str] | None = None,
              placeholders: Mapping[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(step_id=step_id,
                                    data_schema=self.add_suggested_values_to_schema(schema, self.house),
                                    errors=errors or {}, description_placeholders=dict(placeholders or {}))

    def _take(self, user_input: Mapping[str, Any], schema: probatio.Schema) -> None:
        """This screen's answers; a field emptied is removed."""
        for key in schema.schema:
            name = str(key)
            if name in user_input and user_input[name] not in (None, "", []):
                self.house[name] = user_input[name]
            else:
                self.house.pop(name, None)

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._take(user_input, TARIFF_SCHEMA)
            return await self.async_step_outdoor()
        return self._form("tariff", TARIFF_SCHEMA)

    async def async_step_outdoor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._take(user_input, OUTDOOR_SCHEMA)
            return await self.async_step_forecast()
        return self._form("outdoor", OUTDOOR_SCHEMA)

    async def async_step_forecast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._take(user_input, FORECAST_SCHEMA)
            return await self.async_step_power()
        return self._form("forecast", FORECAST_SCHEMA)

    async def async_step_power(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = _power_schema(await _solar_forecast_options(self.hass))
        if user_input is not None:
            self._take(user_input, schema)
            if grid_sensor_changed(self.house_before, self.house):
                self.house.pop(CONF_GRID_POSITIVE_IS_IMPORT, None)
                return await self.async_step_grid_direction()
            if CONF_GRID_POWER not in self.house:
                self.house.pop(CONF_GRID_POSITIVE_IS_IMPORT, None)
            return await self.async_step_announcements()
        return self._form("power", schema)

    async def async_step_grid_direction(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which way a positive grid reading points; stored, and never
        changed at run time [D038]."""
        if user_input is not None:
            self.house[CONF_GRID_POSITIVE_IS_IMPORT] = user_input[CONF_GRID_POSITIVE_IS_IMPORT] == GRID_IMPORT
            return await self.async_step_announcements()
        suggested = self.grid_suggestion(str(self.house[CONF_GRID_POWER]))
        values = {} if suggested is None else {CONF_GRID_POSITIVE_IS_IMPORT: GRID_IMPORT if suggested else GRID_EXPORT}
        return self.async_show_form(step_id="grid_direction",
                                    data_schema=self.add_suggested_values_to_schema(GRID_SCHEMA, values),
                                    description_placeholders={"sensor": str(self.house[CONF_GRID_POWER])})

    async def async_step_announcements(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._take(user_input, ANNOUNCE_SCHEMA)
            return await self.async_house_done()
        return self._form("announcements", ANNOUNCE_SCHEMA)


# --------------------------------------------------------------------------
# The Room screens, shared by first setup and the options
# --------------------------------------------------------------------------


class _RoomScreens:
    """Room screens 1-3. The room being added or edited gathers in
    self.room; self.options are the options it will be saved into."""

    hass: HomeAssistant
    options: dict[str, Any]
    room: dict[str, Any]
    room_id: str | None
    heads: list[dict[str, Any]]
    head_index: int
    profile: CapabilityProfile | None

    if TYPE_CHECKING:  # provided by the flow that uses these screens

        def async_show_form(self, **kwargs: Any) -> ConfigFlowResult: ...

        def add_suggested_values_to_schema(
            self, data_schema: probatio.Schema, suggested_values: Mapping[str, Any] | None
        ) -> probatio.Schema: ...

        async def async_room_done(self, stored: dict[str, Any]) -> ConfigFlowResult: ...

    def _room_1_schema(self) -> probatio.Schema:
        # option values are lower case, as Home Assistant's translation keys must be
        facings = [k.lower() for k in WINDOW_FACINGS]
        return probatio.Schema({
            probatio.Required(CONF_NAME): TextSelector(),
            probatio.Required(CONF_CLIMATE): _entity("climate", multiple=True),
            probatio.Required(CONF_TEMPERATURE_SENSOR): _entity("sensor", "temperature"),
            probatio.Required(CONF_HUMIDITY_SENSOR): _entity("sensor", "humidity"),
            probatio.Optional(CONF_PRESENCE_SENSOR): _entity("binary_sensor"),
            probatio.Optional(CONF_SLEEP_SCHEDULE): _entity(["schedule", "input_boolean", "binary_sensor"]),
            probatio.Optional(CONF_SUN_SENSOR): _entity("binary_sensor"),
            probatio.Optional(CONF_OPENINGS): _entity("binary_sensor", multiple=True),
            probatio.Optional(CONF_BLINDS): _entity("cover", multiple=True),
            probatio.Optional(CONF_ALLOW_BLIND_CONTROL): BooleanSelector(),
            probatio.Optional(CONF_WINDOW_FACING): SelectSelector(SelectSelectorConfig(
                options=facings, mode=SelectSelectorMode.DROPDOWN, translation_key="window_facing")),
            # an overhang shades the glass only by the way the window faces (sun.py)
            _shown_if(CONF_OVERHANG_PROJECTION, CONF_WINDOW_FACING): _number(
                0.0, OVERHANG_FIELD_MAX_M, OVERHANG_FIELD_STEP_M, UnitOfLength.METERS),
            _shown_if(CONF_OVERHANG_HEIGHT, CONF_WINDOW_FACING): _number(
                0.0, OVERHANG_FIELD_MAX_M, OVERHANG_FIELD_STEP_M, UnitOfLength.METERS),
            probatio.Optional(CONF_OCCUPIED_AFTER): _minutes(),
            probatio.Optional(CONF_EMPTY_AFTER): _minutes(),
            probatio.Optional(CONF_WARNING_GRACE): _minutes(),
            probatio.Optional(CONF_ANNOUNCE): BooleanSelector(),
            _shown_if(CONF_ANNOUNCE_THROUGH, CONF_ANNOUNCE, True): _entity("media_player", multiple=True),
            probatio.Optional(CONF_OPENING_GRACE): _minutes(),
            probatio.Optional(CONF_LOCKOUT_REASON): SelectSelector(SelectSelectorConfig(
                options=lockout_choices(self.options), custom_value=True, mode=SelectSelectorMode.DROPDOWN)),
        })

    def _read_profile(self, climates: list[str]) -> tuple[CapabilityProfile | None, str | None, str | None]:
        """Each air conditioner's live state, read now [D048]: the combined
        profile, or the error and the entity it is about."""
        unit = self.hass.config.units.temperature_unit
        profiles: list[CapabilityProfile] = []
        for c in climates:
            st = self.hass.states.get(c)
            p = capabilities.read_profile(st.state if st else None, st.attributes if st else {}, unit,
                                          native_temperature_unit(self.hass, c), native_limits(self.hass, c))
            if p is None:
                return None, "not_reporting", c
            profiles.append(p)
        combined = capabilities.combine(profiles)  # two heads: only what both can do [D020]
        if not capabilities.heads_agree_on_setpoints(combined):
            return None, "heads_take_different_setpoints", None
        if capabilities.setup_problem(combined) is not None:
            return None, "ranges_do_not_overlap", None
        return combined, None, None

    async def async_step_room_1(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Room screen 1, the room."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        schema = self._room_1_schema()
        if user_input is not None:
            climates = list(user_input.get(CONF_CLIMATE) or [])
            used = climate_used_elsewhere(self.options.get(CONF_ROOMS, []), climates, self.room_id)
            profile: CapabilityProfile | None = None
            if not climates:
                errors[CONF_CLIMATE] = "no_climate"
            elif used is not None:
                errors[CONF_CLIMATE] = "climate_in_use"
                placeholders = {"entity": used[0], "room": used[1]}
            else:
                profile, error, entity = self._read_profile(climates)
                if error is not None:
                    errors[CONF_CLIMATE] = error
                    placeholders = {"entity": entity or ""}
            if not str(user_input.get(CONF_NAME, "")).strip():
                errors[CONF_NAME] = "no_name"
            kept = {k: v for k, v in self.room.items() if k not in schema.schema}
            self.room = {**kept, **user_input}
            for key in schema.schema:  # a field emptied on Edit is removed
                if str(key) not in user_input:
                    self.room.pop(str(key), None)
            if not errors:
                self.profile = profile
                before = {h.get(CONF_CLIMATE): h for h in self.heads}
                self.heads = [dict(before.get(c, {CONF_CLIMATE: c})) for c in climates]
                self.head_index = 0
                return await self.async_step_room_2()
        values = {**ROOM_DEFAULTS, **self.room}
        return self.async_show_form(step_id="room_1", data_schema=self.add_suggested_values_to_schema(schema, values),
                                    errors=errors, description_placeholders=placeholders)

    async def async_step_room_2(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Room screen 2, once for each air conditioner: its outdoor unit and
        compressor frequency sensor [D020][Prop]."""
        head = self.heads[self.head_index]
        schema = probatio.Schema({
            probatio.Optional(CONF_OUTDOOR_UNIT): SelectSelector(SelectSelectorConfig(
                options=outdoor_unit_choices(self.options), custom_value=True, mode=SelectSelectorMode.DROPDOWN)),
            probatio.Optional(CONF_FREQUENCY_SENSOR): _entity("sensor", "frequency"),
        })
        if user_input is not None:
            head.pop(CONF_OUTDOOR_UNIT, None)
            head.pop(CONF_FREQUENCY_SENSOR, None)
            head.update({k: v for k, v in user_input.items() if v not in (None, "")})
            self.head_index += 1
            if self.head_index < len(self.heads):
                return await self.async_step_room_2()
            return await self.async_step_room_3()
        return self.async_show_form(step_id="room_2", data_schema=self.add_suggested_values_to_schema(schema, head),
                                    description_placeholders={"climate": str(head[CONF_CLIMATE]),
                                                              "count": f"{self.head_index + 1} of {len(self.heads)}"})

    async def async_step_room_3(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Room screen 3, comfort: the bands and power management [Set][D041]."""
        fields: dict[Any, Any] = {probatio.Optional(k): _band() for pair in BAND_KEYS for k in pair}
        fields[probatio.Optional(CONF_POWER_MANAGEMENT)] = SelectSelector(SelectSelectorConfig(
            options=[m.value for m in PowerManagement], mode=SelectSelectorMode.LIST,
            translation_key="power_management"))
        schema = probatio.Schema(fields)
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            self.room.update(user_input)
            for key in schema.schema:  # a field emptied on Edit takes its default again
                if str(key) not in user_input:
                    self.room.pop(str(key), None)
            inverted = band_inverted(self.room)
            if inverted is not None:
                errors["base"] = "band_inverted"
                placeholders = {"band": inverted}
            else:
                assert self.profile is not None
                room_id = self.room_id or uuid.uuid4().hex  # a UUID made once, never changed [R18]
                stored = build_room(room_id, self.room, self.heads, self.room, self.profile)
                return await self.async_room_done(stored)
        values = {**ROOM_DEFAULTS, **self.room}
        return self.async_show_form(step_id="room_3", data_schema=self.add_suggested_values_to_schema(schema, values),
                                    errors=errors, description_placeholders=placeholders)


# --------------------------------------------------------------------------
# First setup and Reconfigure
# --------------------------------------------------------------------------


class AbodeHvacConfigFlow(_HouseScreens, _RoomScreens, ConfigFlow, domain=DOMAIN):
    """First setup: the house, then the first room. A second setup is
    refused by single_config_entry in the manifest [R18]."""

    VERSION = ENTRY_VERSION
    MINOR_VERSION = ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        self.house = {}
        self.house_before = {}
        self.options = {}
        self.room = {}
        self.room_id = None
        self.heads = []
        self.head_index = 0
        self.profile = None
        self._reconfigure = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AbodeHvacOptionsFlow:
        return AbodeHvacOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_tariff()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The House screens, pre-filled [R18]."""
        entry = self._get_reconfigure_entry()
        self._reconfigure = True
        self.house = dict(entry.data)
        self.house_before = dict(entry.data)
        return await self.async_step_tariff()

    def grid_suggestion(self, grid_entity: str) -> bool | None:
        if not self._reconfigure:
            return None
        entry: AbodeHvacConfigEntry = self._get_reconfigure_entry()
        runtime = getattr(entry, "runtime_data", None)
        st = self.hass.states.get(grid_entity)
        if runtime is None or st is None:
            return None
        suggestion: bool | None = runtime.coordinator.grid_sign_suggestion(
            power_kw(st.state, st.attributes.get("unit_of_measurement"))
        )
        return suggestion

    async def async_house_done(self) -> ConfigFlowResult:
        house = clean_house(self.house)
        if self._reconfigure:
            # the entry's update listener reloads it when the house's data changes
            return self.async_update_and_abort(self._get_reconfigure_entry(), data=house)
        self.house = house
        return await self.async_step_room_1()

    async def async_room_done(self, stored: dict[str, Any]) -> ConfigFlowResult:
        options = replace_room({}, stored)
        return self.async_create_entry(title=TITLE, data=self.house, options=options)


# --------------------------------------------------------------------------
# The options: Rooms and Learning
# --------------------------------------------------------------------------


class AbodeHvacOptionsFlow(_RoomScreens, OptionsFlow):
    """Rooms: add, edit, remove, saved without a reload. Learning: seed a
    room from history, or reset it [Set][Prop]."""

    def __init__(self) -> None:
        self.options = {}
        self.room = {}
        self.room_id = None
        self.heads = []
        self.head_index = 0
        self.profile = None
        self._learning: str | None = None

    def _rooms(self) -> list[SelectOptionDict]:
        return [SelectOptionDict(value=r.room_id, label=r.name) for r in rooms_from_options(self.options)]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self.options = dict(self.config_entry.options)
        text = summary(house_from_data(self.config_entry.data), rooms_from_options(self.options))
        return self.async_show_menu(step_id="init", menu_options=["rooms", "learning"],
                                    description_placeholders={"summary": text})

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["add_room", "edit_room", "remove_room"] if self._rooms() else ["add_room"]
        return self.async_show_menu(step_id="rooms", menu_options=menu)

    async def async_step_add_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self.room, self.room_id, self.heads = {}, None, []
        return await self.async_step_room_1()

    def _pick_schema(self) -> probatio.Schema:
        return probatio.Schema({probatio.Required(CONF_ROOM_ID): SelectSelector(
            SelectSelectorConfig(options=self._rooms(), mode=SelectSelectorMode.LIST))})

    def _stored(self, room_id: str) -> dict[str, Any]:
        return next(dict(r) for r in self.options.get(CONF_ROOMS, []) if r.get(CONF_ROOM_ID) == room_id)

    async def async_step_edit_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            stored = self._stored(user_input[CONF_ROOM_ID])
            self.room_id = user_input[CONF_ROOM_ID]
            self.room = room_answers(stored)
            self.heads = [dict(h) for h in stored.get("heads", [])]
            return await self.async_step_room_1()
        return self.async_show_form(step_id="edit_room", data_schema=self._pick_schema())

    async def async_step_remove_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=remove_room(self.options, user_input[CONF_ROOM_ID]))
        return self.async_show_form(step_id="remove_room", data_schema=self._pick_schema())

    async def async_room_done(self, stored: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data=replace_room(self.options, stored))

    async def async_step_learning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["seed", "reset"] if self._rooms() else []
        if not menu:
            return self.async_abort(reason="no_rooms")
        return self.async_show_menu(step_id="learning", menu_options=menu)

    async def async_step_seed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.room_id, self._learning = user_input[CONF_ROOM_ID], "seed"
            return await self.async_step_confirm()
        return self.async_show_form(step_id="seed", data_schema=self._pick_schema())

    async def async_step_reset(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.room_id, self._learning = user_input[CONF_ROOM_ID], "reset"
            return await self.async_step_confirm()
        return self.async_show_form(step_id="reset", data_schema=self._pick_schema())

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm, then start: seeding runs in the background [Prop]."""
        assert self.room_id is not None and self._learning is not None
        name = next((r["label"] for r in self._rooms() if r["value"] == self.room_id), self.room_id)
        if user_input is not None:
            entry: AbodeHvacConfigEntry = self.config_entry
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                return self.async_abort(reason="not_loaded")
            run = runtime.coordinator.rooms.get(self.room_id)
            if run is not None and run.seeding:
                # one seeding at a time: a second would be dropped, so say so
                return self.async_abort(reason="seeding_in_progress", description_placeholders={"room": name})
            entry.async_create_background_task(
                self.hass, runtime.coordinator.async_seed(self.room_id, reset=self._learning == "reset"),
                f"{DOMAIN} {self._learning} {self.room_id}",
            )
            return self.async_abort(reason=f"{self._learning}_started", description_placeholders={"room": name})
        return self.async_show_form(step_id=f"confirm_{self._learning}", data_schema=probatio.Schema({}),
                                    description_placeholders={"room": name})

    async def async_step_confirm_seed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_confirm(user_input)

    async def async_step_confirm_reset(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_confirm(user_input)
