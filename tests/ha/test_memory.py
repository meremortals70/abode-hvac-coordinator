"""memory.py through Home Assistant: the Store, the save timers and the recorder."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import UnsupportedStorageVersionError
from homeassistant.util import dt as dt_util

from tests.pure._load import load

from .harness import (
    async_fire_time_changed,
    async_wait_recording_done,
)

mem = load("memory")
KEY = "abode_hvac_coordinator"


class Collect:
    """What the coordinator would hand Memory: counts each build."""

    def __init__(self) -> None:
        self.calls = 0
        self.value = 1

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return {"rooms": {}, "draw": {}, "n": self.value}


async def test_one_versioned_file(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    memory = mem.Memory(hass, Collect())
    assert await memory.async_load() is None
    await memory.async_write_now()
    assert hass_storage[KEY]["version"] == 1
    assert hass_storage[KEY]["data"] == {"rooms": {}, "draw": {}, "n": 1}
    again = mem.Memory(hass, Collect())
    assert await again.async_load() == {"rooms": {}, "draw": {}, "n": 1}


async def test_a_learned_save_is_never_pushed_later(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    collect = Collect()
    memory = mem.Memory(hass, collect)
    memory.learned()
    for _ in range(4):  # learning goes on every minute; the waiting save stays where it is
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        memory.learned()
    assert KEY not in hass_storage
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[KEY]["data"]["n"] == 1  # written 5 minutes after the first [D064]
    assert collect.calls == 1
    collect.value = 2
    memory.learned()  # a new save may now wait
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[KEY]["data"]["n"] == 2


async def test_a_choice_is_saved_within_five_seconds(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    memory = mem.Memory(hass, Collect())
    memory.learned()
    memory.choice()
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[KEY]["data"]["n"] == 1  # [D064]
    memory.learned()  # the learned save that was waiting went with it; a new one may wait
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_learned_save_does_not_delay_a_waiting_choice(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    memory = mem.Memory(hass, Collect())
    memory.choice()
    memory.learned()  # the model learns a moment after the choice: the choice's save keeps its time
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[KEY]["data"]["n"] == 1  # [D064]


async def test_removed(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    memory = mem.Memory(hass, Collect())
    await memory.async_write_now()
    await memory.async_remove()
    assert KEY not in hass_storage


async def test_an_unreadable_file_raises(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    hass_storage[KEY] = {"version": 9, "minor_version": 1, "key": KEY, "data": {}}
    with pytest.raises(UnsupportedStorageVersionError):  # newer than this version can read: setup retries
        await mem.Memory(hass, Collect()).async_load()


async def test_history_read_from_the_recorder(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    start = dt_util.utcnow() - timedelta(minutes=1)
    hass.states.async_set("sensor.t", "24.1", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.t", "24.3", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.a", "cool", {"current_temperature": 24.0, "temperature": 23.0})
    hass.states.async_set("climate.a", "cool", {"current_temperature": 24.0, "temperature": 23.0,
                                                "hvac_action": "cooling"})  # only an attribute changed
    await async_wait_recording_done(hass)
    got = await mem.async_read_history(hass, ["sensor.t", "climate.a", "sensor.none"], start,
                                       dt_util.utcnow() + timedelta(minutes=1))
    assert [s.state for s in got["sensor.t"]] == ["24.1", "24.3"]
    assert got["climate.a"][0].attributes == {"current_temperature": 24.0, "temperature": 23.0}
    assert got["climate.a"][1].attributes["hvac_action"] == "cooling"  # the row the old query dropped
    assert got["climate.a"][1].last_changed == got["climate.a"][0].last_changed
    assert got["climate.a"][1].last_reported > got["climate.a"][1].last_changed
    assert await mem.async_read_history(hass, [], start, dt_util.utcnow()) == {}
    assert got["sensor.none"] == []
    assert mem.recorder_keep_days(hass, 3) == recorder_mock.keep_days == 10  # [HA]


async def test_keep_days_without_a_recorder(hass: HomeAssistant) -> None:
    assert mem.recorder_keep_days(hass, 10) == 10
