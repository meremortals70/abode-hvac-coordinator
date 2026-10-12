"""The model store on its own, against Home Assistant's real `Store`.

What each method keeps, what it writes and when (DR-031, DR-048, DR-049,
DR-058, DR-064). The file on disk is read through the `hass_storage` fixture,
which is what Home Assistant's storage helper writes to in tests.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.abode_hvac_coordinator.store import (
    SAVE_DELAY_SECONDS,
    STORAGE_KEY,
    STORAGE_VERSION,
    USER_CHOICE_SAVE_DELAY_SECONDS,
    ModelStore,
)


def _on_disk(hass_storage: dict[str, Any]) -> dict[str, Any] | None:
    stored = hass_storage.get(STORAGE_KEY)
    return None if stored is None else stored["data"]


async def _wait(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _loaded(hass: HomeAssistant) -> ModelStore:
    store = ModelStore(hass)
    await store.async_load()
    return store


async def test_an_empty_store_starts_with_nothing_learned(hass: HomeAssistant) -> None:
    store = await _loaded(hass)
    assert store.room("living") == {}
    assert store.group("outside") == {}
    assert store.switched_off() == set()
    assert store.manual_vanes() == set()
    assert store.comfort_modes() == {}
    assert store.profile("living") is None


async def test_what_was_stored_is_read_back(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "rooms": {"living": {"k_loss": {"value": 0.1}}},
            "groups": {"outside": {"draw": 1.2}},
            "switched_off": ["office"],
            "manual_vanes": ["living"],
            "profiles": {"living": {"can_cool": True}},
            "comfort_modes": {"living": "sleep"},
        },
    }
    store = await _loaded(hass)
    assert store.room("living") == {"k_loss": {"value": 0.1}}
    assert store.group("outside") == {"draw": 1.2}
    assert store.switched_off() == {"office"}
    assert store.manual_vanes() == {"living"}
    assert store.profile("living") == {"can_cool": True}
    assert store.comfort_modes() == {"living": "sleep"}


async def test_a_file_from_before_a_key_existed_still_loads(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A store written by an older build has no comfort modes or profiles."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"rooms": {}},
    }
    store = await _loaded(hass)
    assert store.comfort_modes() == {}
    assert store.profile("living") is None
    assert store.switched_off() == set()


async def test_a_damaged_comfort_mode_entry_reads_as_none(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"comfort_modes": ["living"], "profiles": {"living": "cool"}},
    }
    store = await _loaded(hass)
    assert store.comfort_modes() == {}
    assert store.profile("living") is None


async def test_learned_state_is_written_once_after_the_delay(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.update_room("living", {"k_loss": 1})
    await _wait(hass, freezer, SAVE_DELAY_SECONDS - 10)
    assert _on_disk(hass_storage) is None
    store.update_group("outside", {"draw": 2})
    await _wait(hass, freezer, 20)
    written = _on_disk(hass_storage)
    assert written is not None
    assert written["rooms"]["living"] == {"k_loss": 1}
    assert written["groups"]["outside"] == {"draw": 2}


async def test_a_change_after_a_write_schedules_another(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.update_room("living", {"k_loss": 1})
    await _wait(hass, freezer, SAVE_DELAY_SECONDS + 1)
    store.update_room("living", {"k_loss": 2})
    await _wait(hass, freezer, SAVE_DELAY_SECONDS + 1)
    assert _on_disk(hass_storage)["rooms"]["living"] == {"k_loss": 2}


async def test_each_choice_is_written_within_seconds(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.set_switched_off("office", True)
    store.set_manual_vanes("living", True)
    store.set_comfort_mode("living", "sleep")
    store.set_profile("living", {"can_cool": True})
    await _wait(hass, freezer, USER_CHOICE_SAVE_DELAY_SECONDS + 1)
    written = _on_disk(hass_storage)
    assert written["switched_off"] == ["office"]
    assert written["manual_vanes"] == ["living"]
    assert written["comfort_modes"] == {"living": "sleep"}
    assert written["profiles"] == {"living": {"can_cool": True}}


async def test_choices_can_be_taken_back(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.set_switched_off("office", True)
    store.set_manual_vanes("living", True)
    store.set_comfort_mode("living", "sleep")
    store.set_profile("living", {"can_cool": True})
    store.set_switched_off("office", False)
    store.set_manual_vanes("living", False)
    store.set_comfort_mode("living", None)
    store.forget_profile("living")
    await _wait(hass, freezer, USER_CHOICE_SAVE_DELAY_SECONDS + 1)
    written = _on_disk(hass_storage)
    assert written["switched_off"] == []
    assert written["manual_vanes"] == []
    assert written["comfort_modes"] == {}
    assert written["profiles"] == {}


async def test_setting_schedule_on_a_room_already_on_schedule_writes_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.set_comfort_mode("living", None)
    store.forget_profile("living")
    store.forget_room("living")
    await _wait(hass, freezer, SAVE_DELAY_SECONDS + 1)
    assert _on_disk(hass_storage) is None


async def test_forgetting_a_room_drops_everything_kept_for_it(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.update_room("living", {"k_loss": 1})
    store.set_profile("living", {"can_cool": True})
    store.set_comfort_mode("living", "sleep")
    store.update_room("office", {"k_loss": 3})
    store.forget_room("living")
    await _wait(hass, freezer, USER_CHOICE_SAVE_DELAY_SECONDS + 1)
    written = _on_disk(hass_storage)
    assert "living" not in written["rooms"]
    assert written["rooms"]["office"] == {"k_loss": 3}
    assert written["profiles"] == {}
    assert written["comfort_modes"] == {}


async def test_forgetting_a_room_with_only_a_comfort_mode_is_written(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    store = await _loaded(hass)
    store.set_comfort_mode("living", "awake")
    await _wait(hass, freezer, USER_CHOICE_SAVE_DELAY_SECONDS + 1)
    store.forget_room("living")
    await _wait(hass, freezer, USER_CHOICE_SAVE_DELAY_SECONDS + 1)
    assert _on_disk(hass_storage)["comfort_modes"] == {}


async def test_flush_writes_at_once(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _loaded(hass)
    store.update_room("living", {"k_loss": 1})
    await store.async_flush()
    assert _on_disk(hass_storage)["rooms"]["living"] == {"k_loss": 1}


async def test_removing_the_store_deletes_the_file(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _loaded(hass)
    store.update_room("living", {"k_loss": 1})
    await store.async_flush()
    assert STORAGE_KEY in hass_storage
    await store.async_remove()
    assert STORAGE_KEY not in hass_storage
