"""Abode Power Tariffs.

Holds the household's electricity plan and answers two questions: what energy
costs right now and what rules are in force, and what that will be over the
next N hours.

It decides nothing. It writes nothing except nominated utility_meter tariff
selects.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_HOURS,
    ATTR_RESOLUTION_MINUTES,
    DEFAULT_HOURS,
    DEFAULT_RESOLUTION_MINUTES,
    DOMAIN,
    SERVICE_GET_INTERVALS,
)
from .coordinator import TariffCoordinator
from .plan import Plan, PlanError
from .validate import validate_plan

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type TariffConfigEntry = ConfigEntry[TariffCoordinator]

GET_INTERVALS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_HOURS, default=DEFAULT_HOURS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=168)
        ),
        vol.Optional(
            ATTR_RESOLUTION_MINUTES, default=DEFAULT_RESOLUTION_MINUTES
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions.

    Registered here rather than in async_setup_entry so automations using them
    can be validated even when no entry is loaded, and so an unloaded entry is
    reported as such rather than the action appearing not to exist.
    """

    async def _get_intervals(call: ServiceCall) -> ServiceResponse:
        entry_id: str = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        coordinator: TariffCoordinator | None = getattr(entry, "runtime_data", None)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"title": entry.title},
            )
        series = coordinator.forward_intervals(
            call.data[ATTR_HOURS], call.data[ATTR_RESOLUTION_MINUTES]
        )
        return {"intervals": [interval.as_dict() for interval in series]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_INTERVALS,
        _get_intervals,
        schema=GET_INTERVALS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TariffConfigEntry) -> bool:
    """Set up one tariff channel."""
    options = dict(entry.options)

    try:
        plan = Plan.from_dict({**options, "name": entry.title})
    except PlanError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="plan_unreadable",
            translation_placeholders={"error": str(err)},
        ) from err

    issue_id = f"invalid_plan_{entry.entry_id}"
    problems = validate_plan(plan)
    if problems:
        # The configuration flow refuses to save an invalid plan, so this can
        # only be a plan written by an older version. Surfaced rather than
        # swallowed; setup continues so the user can go and fix it.
        async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=IssueSeverity.ERROR,
            translation_key="invalid_plan",
            translation_placeholders={
                "title": entry.title,
                "problems": "\n".join(str(problem) for problem in problems),
            },
        )
    else:
        async_delete_issue(hass, DOMAIN, issue_id)

    coordinator = TariffCoordinator(hass, entry.entry_id, plan, options)
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TariffConfigEntry) -> bool:
    """Unload one tariff channel."""
    unloaded: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.async_shutdown()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: TariffConfigEntry) -> None:
    """Reload when the plan changes, so entities match the new rate names."""
    await hass.config_entries.async_reload(entry.entry_id)
