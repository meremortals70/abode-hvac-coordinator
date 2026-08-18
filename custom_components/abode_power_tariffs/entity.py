"""Base entity for Abode Power Tariffs."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .coordinator import TariffCoordinator


class TariffEntity(Entity):
    """An entity backed by one tariff channel."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: TariffCoordinator, key: str) -> None:
        """Initialise the entity."""
        self.coordinator = coordinator
        self._key = key
        self._attr_unique_id = f"{coordinator.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={coordinator.device_identifier},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Abode",
            name=coordinator.plan.name,
            model="Tariff plan",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe once the entity is registered and can write state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATE}_{self.coordinator.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Write the new state after the coordinator recomputed."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the plan resolves at this moment.

        An expired plan is still available: it holds its last known prices and
        raises a repair issue, because removing the price entity would stop
        Energy dashboard cost tracking without saying so.
        """
        return self.coordinator.state.resolution is not None


DEVICE_DOMAIN = DOMAIN
