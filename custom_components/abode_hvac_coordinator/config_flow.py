"""Config, options and reconfigure flows.

Only what a user cannot get a correct result without is exposed. Regulation
thresholds, model parameters and filter tuning are not settings.

The initial setup collects the first room, so the integration does something
the moment it is added; further rooms, edits and removals go through the
options flow.

Comfort bands arrive seeded with defaults derived from the ASHRAE 55 comfort
zone, so a fresh install is sensible with no configuration. Nothing specific to
any house is seeded: entity IDs are always the user's own.

The tariff is not entered here. It belongs to Abode Power Tariffs; this flow
collects only which of its entries to read from.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COVER_ENTITIES,
    CONF_DIRECT_SUN_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_LOCKOUT_REASONS,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_OUTDOOR_WIND_ENTITY,
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
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_DIRECTION,
    DOMAIN,
    NOT_LOCKED_OUT,
    TARIFF_DOMAIN,
)
from .forms import (
    BAND_MODES,
    bands_are_valid,
    bands_as_suggestions,
    bands_from_input,
    default_band_suggestions,
    default_grace_suggestions,
    describe_configuration,
    describe_global,
    describe_rooms,
    extend_lockout_reasons,
    known_lockout_reasons,
    room_from_input,
)
from .sun import WINDOW_DIRECTIONS

ROOM_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate")
        ),
        vol.Optional(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_HUMIDITY_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
        ),
        vol.Optional(CONF_PRESENCE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Optional(CONF_SLEEP_SCHEDULE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["schedule", "input_boolean", "binary_sensor"]
            )
        ),
        vol.Optional(CONF_DIRECT_SUN_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Optional(CONF_OPENING_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
        ),
        vol.Optional(CONF_COVER_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="cover", multiple=True)
        ),
    }
)


def _metres_selector() -> selector.NumberSelector:
    """A metres box, for the overhang measurements."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=10, step=0.05, unit_of_measurement="m",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _minutes_selector() -> selector.NumberSelector:
    """A minutes box for the grace timings."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=120, step=1, unit_of_measurement="min",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def room_schema(lockout_reasons: list[str]) -> vol.Schema:
    """The room form, with the lockout dropdown built from known reasons.

    Lockout is one field, not a tick box and a second screen. The first option
    means the room is not locked out, so choosing a reason is the same action
    as switching lockout on, and the reason can never be set by accident
    because it is never a free text box.
    """
    return ROOM_SCHEMA.extend(
        {
            vol.Optional(CONF_OCCUPIED_AFTER): _minutes_selector(),
            vol.Optional(CONF_VACANT_AFTER): _minutes_selector(),
            vol.Optional(CONF_WARNING_GRACE): _minutes_selector(),
            vol.Optional(CONF_ANNOUNCE, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_ANNOUNCE_TARGETS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)
            ),
            vol.Optional(CONF_OVERHANG_PROJECTION): _metres_selector(),
            vol.Optional(CONF_OVERHANG_HEIGHT): _metres_selector(),
            vol.Optional(CONF_WINDOW_DIRECTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WINDOW_DIRECTIONS),
                    translation_key="window_direction",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_LOCKOUT_REASON, default=NOT_LOCKED_OUT
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=lockout_reasons,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


BANDS_SCHEMA = vol.Schema(
    {
        vol.Optional(f"{mode}_{bound}"): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=0.5, mode=selector.NumberSelectorMode.BOX
            )
        )
        for mode in BAND_MODES
        for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
    }
)


TARIFF_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARIFF_ENTRY_ID): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration=TARIFF_DOMAIN)
        )
    }
)


class _RoomSteps:
    """The room, lockout and bands steps, shared by both flows.

    Both flows collect a room the same way. The only difference is what happens
    to it afterwards, which is what _save_room does.
    """

    _room: dict[str, Any]

    def _known_lockout_reasons(self) -> list[str]:
        """Built-in reasons plus any the user has added, deduplicated."""
        return known_lockout_reasons(self._stored_lockout_reasons())

    def _stored_lockout_reasons(self) -> list[str]:
        """Reasons the user has typed before. Empty for a fresh install."""
        return []

    def _suggested_room(self) -> dict[str, Any]:
        """Values to prefill the room form with.

        A new room arrives with the default grace timings already in it, so it
        behaves sensibly without anyone reasoning about compressor cycling.
        """
        return dict(default_grace_suggestions())

    def _suggested_bands(self) -> dict[str, float]:
        """Values to prefill the bands form with.

        A new room gets the seeded defaults, so the form arrives with sensible
        numbers rather than six empty boxes.
        """
        return default_band_suggestions()

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the room and the entities that describe it."""
        if user_input is None:
            schema = room_schema(known_lockout_reasons(self._stored_lockout_reasons()))
            return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
                step_id="room",
                data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                    schema, self._suggested_room()
                ),
            )

        self._room = room_from_input(user_input)
        return await self.async_step_bands()

    async def async_step_bands(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the comfort bands for this room."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bands = bands_from_input(user_input)
            if not bands_are_valid(bands):
                errors["base"] = "band_inverted"
            else:
                self._room[CONF_BANDS] = bands
                return self._save_room()

        suggested = self._suggested_bands()
        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="bands",
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                BANDS_SCHEMA, suggested
            ),
            errors=errors,
        )

    def _save_room(self) -> ConfigFlowResult:
        """Store the collected room. Implemented by each flow."""
        raise NotImplementedError


class HvacCoordinatorConfigFlow(_RoomSteps, ConfigFlow, domain=DOMAIN):
    """Initial setup. Collects the first room, so setup produces something."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._room = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start by describing the first room."""
        return await self.async_step_room(user_input)

    def _save_room(self) -> ConfigFlowResult:
        """Create the entry with the first room in it."""
        return self.async_create_entry(
            title="Abode HVAC Coordinator",
            data={
                CONF_ROOMS: [self._room],
                CONF_LOCKOUT_REASONS: extend_lockout_reasons([], self._room),
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> HvacCoordinatorOptionsFlow:
        """Return the options flow."""
        return HvacCoordinatorOptionsFlow()


class HvacCoordinatorOptionsFlow(_RoomSteps, OptionsFlow):
    """Add, edit and remove rooms."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._room = {}
        self._editing: str | None = None

    @property
    def _rooms(self) -> list[dict[str, Any]]:
        """Rooms as currently configured."""
        return list(
            self.config_entry.options.get(
                CONF_ROOMS, self.config_entry.data.get(CONF_ROOMS, [])
            )
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer what can be done, rather than assuming a room is being added."""
        if not self._rooms:
            return await self.async_step_room()
        return self.async_show_menu(
            step_id="init",
            description_placeholders={"configuration": self._summary()},
            menu_options=["rooms", "global"],
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Room configuration."""
        return self.async_show_menu(
            step_id="rooms",
            description_placeholders={"configuration": self._rooms_summary()},
            menu_options=["room", "edit_room", "remove_room"],
        )

    async def async_step_global(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Everything that applies to the whole house rather than one room."""
        return self.async_show_menu(
            step_id="global",
            description_placeholders={"configuration": self._global_summary()},
            menu_options=["tariff", "outdoor", "forecast"],
        )

    def _rooms_summary(self) -> str:
        """Every room and its settings."""
        return describe_rooms(self._rooms)

    def _global_summary(self) -> str:
        """The house-wide settings."""
        return describe_global(
            self._tariff_title(),
            self._outdoor_entity_id(),
            self._outdoor_humidity_entity_id(),
            self._outdoor_wind_entity_id(),
            self._weather_entity_id(),
        )

    def _summary(self) -> str:
        """Everything currently configured, shown on the menu itself."""
        return describe_configuration(
            self._rooms, self._tariff_title(), self._outdoor_entity_id()
        )

    def _stored(self, key: str) -> str | None:
        """One configured value, options first then data, as a string or None.

        Options and data are untyped mappings, so the narrowing has to happen
        somewhere. Doing it once here is the difference between one cast and
        one per caller.
        """
        value = self.config_entry.options.get(
            key, self.config_entry.data.get(key)
        )
        return None if value is None else str(value)

    def _outdoor_entity_id(self) -> str | None:
        return self._stored(CONF_OUTDOOR_TEMPERATURE_ENTITY)

    def _outdoor_humidity_entity_id(self) -> str | None:
        return self._stored(CONF_OUTDOOR_HUMIDITY_ENTITY)

    def _outdoor_wind_entity_id(self) -> str | None:
        return self._stored(CONF_OUTDOOR_WIND_ENTITY)

    def _weather_entity_id(self) -> str | None:
        return self._stored(CONF_WEATHER_ENTITY)

    def _tariff_entry_id(self) -> str | None:
        return self._stored(CONF_TARIFF_ENTRY_ID)

    def _tariff_title(self) -> str | None:
        """The selected tariff entry\'s title, so the menu names it.

        The id is shown if the entry has gone: an id the user cannot match to
        anything is still better than a blank that reads as "none selected"
        when something is in fact selected and broken.
        """
        entry_id = self._tariff_entry_id()
        if not entry_id:
            return None
        entry = self.hass.config_entries.async_get_entry(entry_id)
        return entry.title if entry is not None else f"{entry_id} (missing)"

    async def async_step_edit_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a room to edit, then reuse the room steps with it prefilled."""
        if user_input is None:
            return self.async_show_form(
                step_id="edit_room",
                data_schema=self._room_choice_schema(),
                description_placeholders={"configuration": self._summary()},
            )
        self._editing = user_input[CONF_ROOM_ID]
        return await self.async_step_room()

    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a room. Its device and entities go with it."""
        if user_input is None:
            return self.async_show_form(
                step_id="remove_room",
                data_schema=self._room_choice_schema(),
                description_placeholders={"configuration": self._summary()},
            )
        remaining = [
            room
            for room in self._rooms
            if room[CONF_ROOM_ID] != user_input[CONF_ROOM_ID]
        ]
        options = dict(self.config_entry.options)
        options[CONF_ROOMS] = remaining
        return self.async_create_entry(title="", data=options)

    # ---- tariff -------------------------------------------------------

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which Abode Power Tariffs entry supplies the plan.

        Only the entry is collected. Periods, prices, feed-in and the supply
        charge are entered once, in that integration, and read from here.
        Leaving it empty is a supported configuration: the controller then
        holds comfort and nothing window-driven.
        """
        if user_input is None:
            current = self._tariff_entry_id()
            return self.async_show_form(
                step_id="tariff",
                data_schema=self.add_suggested_values_to_schema(
                    TARIFF_SCHEMA, {CONF_TARIFF_ENTRY_ID: current}
                )
                if current
                else TARIFF_SCHEMA,
                description_placeholders={"tariff": self._global_summary()},
            )

        options = dict(self.config_entry.options)
        options[CONF_TARIFF_ENTRY_ID] = user_input.get(CONF_TARIFF_ENTRY_ID)
        options.setdefault(CONF_ROOMS, self._rooms)
        return self.async_create_entry(title="", data=options)

    async def async_step_forecast(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The weather entity supplying the hourly forecast.

        Precool is the decision that turns on this. Without it the controller
        compares current conditions, which at 11:00 on the day of a 38 C
        afternoon says there is no load coming.
        """
        if user_input is None:
            current = self._weather_entity_id()
            schema = vol.Schema(
                {
                    vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    )
                }
            )
            return self.async_show_form(
                step_id="forecast",
                data_schema=self.add_suggested_values_to_schema(
                    schema, {CONF_WEATHER_ENTITY: current}
                )
                if current
                else schema,
            )

        options = dict(self.config_entry.options)
        options[CONF_WEATHER_ENTITY] = user_input.get(CONF_WEATHER_ENTITY)
        options.setdefault(CONF_ROOMS, self._rooms)
        return self.async_create_entry(title="", data=options)

    async def async_step_outdoor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The outdoor temperature feed, used by the thermal model."""
        if user_input is None:
            suggested = {
                key: value
                for key in (
                    CONF_OUTDOOR_TEMPERATURE_ENTITY,
                    CONF_OUTDOOR_HUMIDITY_ENTITY,
                    CONF_OUTDOOR_WIND_ENTITY,
                )
                if (
                    value := self.config_entry.options.get(
                        key, self.config_entry.data.get(key)
                    )
                )
            }
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_OUTDOOR_TEMPERATURE_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="temperature"
                        )
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_HUMIDITY_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="humidity"
                        )
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_WIND_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="wind_speed"
                        )
                    ),
                }
            )
            return self.async_show_form(
                step_id="outdoor",
                data_schema=self.add_suggested_values_to_schema(schema, suggested)
                if suggested
                else schema,
            )

        options = dict(self.config_entry.options)
        options[CONF_OUTDOOR_TEMPERATURE_ENTITY] = user_input.get(
            CONF_OUTDOOR_TEMPERATURE_ENTITY
        )
        options[CONF_OUTDOOR_HUMIDITY_ENTITY] = user_input.get(
            CONF_OUTDOOR_HUMIDITY_ENTITY
        )
        options[CONF_OUTDOOR_WIND_ENTITY] = user_input.get(
            CONF_OUTDOOR_WIND_ENTITY
        )
        options.setdefault(CONF_ROOMS, self._rooms)
        return self.async_create_entry(title="", data=options)

    # ---- rooms --------------------------------------------------------

    def _room_choice_schema(self) -> vol.Schema:
        """A picker over the configured rooms."""
        return vol.Schema(
            {
                vol.Required(CONF_ROOM_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=room[CONF_ROOM_ID], label=room["name"]
                            )
                            for room in self._rooms
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _existing(self) -> dict[str, Any]:
        """The room being edited, or an empty dict when adding."""
        if self._editing is None:
            return {}
        return next(
            (room for room in self._rooms if room[CONF_ROOM_ID] == self._editing), {}
        )

    def _stored_lockout_reasons(self) -> list[str]:
        return list(
            self.config_entry.options.get(
                CONF_LOCKOUT_REASONS,
                self.config_entry.data.get(CONF_LOCKOUT_REASONS, []),
            )
        )

    def _suggested_room(self) -> dict[str, Any]:
        existing = self._existing()
        if not existing:
            return {}
        return {
            **default_grace_suggestions(),
            **{k: v for k, v in existing.items() if v is not None},
            CONF_LOCKOUT_REASON: existing.get(CONF_LOCKOUT_REASON) or NOT_LOCKED_OUT,
        }

    def _suggested_lockout(self) -> dict[str, Any]:
        reason = self._existing().get(CONF_LOCKOUT_REASON)
        return {CONF_LOCKOUT_REASON: reason} if reason else {}

    def _suggested_bands(self) -> dict[str, float]:
        """A room being edited shows its own bands; a new one shows defaults."""
        existing = self._existing().get(CONF_BANDS, {})
        if existing:
            return bands_as_suggestions(existing)
        return default_band_suggestions()

    def _save_room(self) -> ConfigFlowResult:
        """Add or replace the room in the entry options."""
        options = dict(self.config_entry.options)
        # Replace the room being edited, and any room whose name produces the
        # same id, so editing a name does not leave the old room behind.
        replaced = {self._room[CONF_ROOM_ID], self._editing}
        rooms = [room for room in self._rooms if room[CONF_ROOM_ID] not in replaced]
        rooms.append(self._room)
        options[CONF_ROOMS] = rooms
        options[CONF_LOCKOUT_REASONS] = extend_lockout_reasons(
            self._stored_lockout_reasons(), self._room
        )
        return self.async_create_entry(title="", data=options)
