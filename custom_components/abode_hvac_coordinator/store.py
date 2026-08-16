"""Persistence for learned state.

Configuration lives in the config entry. This store holds only what the system
learns and cannot be asked for: thermal model parameters, filter covariance,
sample counts. Losing it costs convergence time, not correctness, because the
model falls back to hysteresis until it has converged again.

Verified against homeassistant/helpers/storage.py on the core dev branch:

  Store.__init__(hass, version, key, private=False, *, atomic_writes=False,
                 encoder=None, max_readable_version=None, minor_version=1,
                 read_only=False, serialize_in_event_loop=True)   line 228

  async_delay_save(data_func, delay=0)                            line 480
      Registers a final-write listener, so a pending delayed save is flushed
      when Home Assistant stops rather than lost.

  _async_migrate_func(old_major_version, old_minor_version, old_data)  line 620
      Called whenever the on-disk version is below ours.

atomic_writes is False by default in core and is set True here: a half-written
model file on power loss would fail to parse and take the learned state with it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_MINOR_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.model"

#: Learned state is written at most this often. The model changes slowly and
#: the flash under Home Assistant does not need a write per evaluation.
SAVE_DELAY_SECONDS = 300


class ModelStore:
    """Load and save learned per-room model state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            atomic_writes=True,
            minor_version=STORAGE_MINOR_VERSION,
        )
        self._data: dict[str, Any] = {"rooms": {}}

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored is None:
            _LOGGER.debug("No stored model state; starting from hysteresis fallback")
            return
        self._data = stored

    def room(self, room_id: str) -> dict[str, Any]:
        """Learned state for one room. Empty dict means nothing learned yet."""
        rooms: dict[str, Any] = self._data.setdefault("rooms", {})
        return rooms.setdefault(room_id, {})

    def update_room(self, room_id: str, state: dict[str, Any]) -> None:
        """Record learned state, written out on a delay."""
        self._data.setdefault("rooms", {})[room_id] = state
        self._store.async_delay_save(self._data_for_save, SAVE_DELAY_SECONDS)

    def forget_room(self, room_id: str) -> None:
        """Drop a removed room's learned state."""
        if self._data.get("rooms", {}).pop(room_id, None) is not None:
            self._store.async_delay_save(self._data_for_save, SAVE_DELAY_SECONDS)

    def _data_for_save(self) -> dict[str, Any]:
        return self._data

    async def async_remove(self) -> None:
        """Drop learned state. Called when the config entry is removed."""
        await self._store.async_remove()
