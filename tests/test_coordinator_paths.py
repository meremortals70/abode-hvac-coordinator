"""Coordinator paths a normal evaluation reaches only in particular conditions.

Each test drives the coordinator as Home Assistant runs it: real states, real
service calls, a real config entry. Where a path is reached only at a point in
time, or after a transition the test cannot make the unit perform, the method
that owns it is called directly against that real coordinator.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.abode_hvac_coordinator.const import (
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_DIRECT_SUN_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_WINDOW_DIRECTION,
    DOMAIN,
    ISSUE_ANNOUNCEMENTS_UNAVAILABLE,
    ISSUE_FORECAST_UNAVAILABLE,
    POWER_MANAGEMENT_ENFORCED,
)
from custom_components.abode_hvac_coordinator.coordinator import (
    DRAW_POST_WINDOW,
    DRAW_RAMP_ALLOWANCE,
)
from custom_components.abode_hvac_coordinator.grace import Announcement
from custom_components.abode_hvac_coordinator.models import (
    ActuatorStep,
    ComfortMode,
    DecisionTrace,
    Mode,
    RoomInputs,
)
from custom_components.abode_hvac_coordinator.regulate import (
    CompressorState,
    PidSetpoint,
)
from custom_components.abode_hvac_coordinator.thermal import (
    Coefficient,
    ThermalModel,
    approach_bin,
)
from custom_components.abode_hvac_coordinator.weather import (
    OVERCAST_TRANSMISSION,
    ForecastPoint,
    WeatherTrajectory,
)

import voluptuous as vol
from real_tts import ENGINE, real_tts_speak
from test_living_room_night import _UNIT, _ceiling, _setup, _setup_power

_GROUP = "climate.test"


def _trajectory(start: datetime, hours: int, **fields: Any) -> WeatherTrajectory:
    """An hourly forecast from `start`, every hour the same unless overridden."""
    points = []
    for hour in range(hours):
        values = {
            name: (value(hour) if callable(value) else value)
            for name, value in fields.items()
        }
        values.setdefault("temperature_c", 25.0)
        points.append(ForecastPoint(at=start + timedelta(hours=hour), **values))
    return WeatherTrajectory(tuple(points), start)


# ---------------------------------------------------------------------------
# Finding 14 - an outdoor unit's draw is learned from a step in house load
# ---------------------------------------------------------------------------


async def _with_house_load(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    hass.states.async_set("sensor.house_load", "1000")
    return await _setup(
        hass, entry, options_extra={CONF_HOUSE_LOAD_ENTITY: "sensor.house_load"}
    )


def _sample(hass: HomeAssistant, coordinator: Any, at: datetime, watts: float) -> None:
    """A house-load report at `at`, recorded as an evaluation then would."""
    with freeze_time(at):
        hass.states.async_set("sensor.house_load", str(watts), force_update=True)
        coordinator._record_house_load_sample(at)


def _step(
    hass: HomeAssistant,
    coordinator: Any,
    changed_at: datetime,
    *,
    before: list[float],
    after: list[float],
) -> datetime:
    """House load before and after a compressor change, a minute apart.

    Returns the moment the candidate is mature.
    """
    for index, watts in enumerate(before):
        _sample(
            hass,
            coordinator,
            changed_at - timedelta(seconds=30 * (len(before) - index)),
            watts,
        )
    for index, watts in enumerate(after):
        _sample(
            hass,
            coordinator,
            changed_at + DRAW_RAMP_ALLOWANCE + timedelta(seconds=30 * index),
            watts,
        )
    return changed_at + DRAW_RAMP_ALLOWANCE + DRAW_POST_WINDOW


async def test_a_compressor_start_teaches_its_draw_from_the_house_load(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    coordinator._draw_pending.clear()
    coordinator._last_target.pop("test_room", None)
    changed_at = dt_util.utcnow() + timedelta(minutes=30)
    coordinator._compressors[_GROUP] = CompressorState(running=True, changed_at=changed_at)
    coordinator._note_compressor_transition(_GROUP, started=True, now=changed_at)
    mature = _step(
        hass,
        coordinator,
        changed_at,
        before=[1000.0, 1000.0, 1000.0, 1000.0],
        after=[2500.0, 2500.0, 2500.0, 2500.0, 2500.0, 2500.0],
    )
    before = coordinator.draw_for(_GROUP).bins[3].samples
    coordinator._process_draw_candidates(mature)
    learned = coordinator.draw_for(_GROUP).bins[3]
    # No room in the group has a commanded setpoint yet: the pulldown bin.
    assert learned.samples == before + 1
    assert _GROUP not in coordinator._draw_pending
    pooled = coordinator.draw_for(_GROUP).pooled
    # The 1.5 kW step pulled the estimate up from the 1.0 kW seed.
    assert pooled.value > 1.0


async def test_a_compressor_stop_is_read_as_the_same_draw_falling_away(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    coordinator._draw_pending.clear()
    changed_at = dt_util.utcnow() + timedelta(minutes=30)
    coordinator._compressors[_GROUP] = CompressorState(running=False, changed_at=changed_at)
    coordinator._note_compressor_transition(_GROUP, started=False, now=changed_at)
    coordinator._last_target["test_room"] = 25.9 - 1.5
    mature = _step(
        hass,
        coordinator,
        changed_at,
        before=[3000.0, 3000.0, 3000.0, 3000.0],
        after=[1200.0, 1200.0, 1200.0, 1200.0, 1200.0, 1200.0],
    )
    bin_index = approach_bin(1.5)
    with patch.object(
        type(coordinator.draw_for(_GROUP)), "observe", autospec=True
    ) as observe:
        coordinator._process_draw_candidates(mature)
    observe.assert_called_once()
    _, observed_bin, draw_kw, quality = observe.call_args.args
    # The room is 1.5 C from its commanded setpoint: that bin, not pulldown.
    assert observed_bin == bin_index
    # A fall of 1.8 kW when the compressor stopped is a 1.8 kW draw.
    assert draw_kw == pytest.approx(1.8)
    # Six quiet samples after, four before: four of the six wanted.
    assert quality == pytest.approx(4 / 6)


async def test_a_noisy_house_load_is_trusted_less(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    coordinator._draw_pending.clear()
    changed_at = dt_util.utcnow() + timedelta(minutes=30)
    coordinator._compressors[_GROUP] = CompressorState(running=True, changed_at=changed_at)
    coordinator._note_compressor_transition(_GROUP, started=True, now=changed_at)
    mature = _step(
        hass,
        coordinator,
        changed_at,
        before=[600.0, 1400.0, 600.0, 1400.0],
        after=[2100.0, 2900.0, 2100.0, 2900.0, 2100.0, 2900.0],
    )
    with patch.object(
        type(coordinator.draw_for(_GROUP)), "observe", autospec=True
    ) as observe:
        coordinator._process_draw_candidates(mature)
    _, _, draw_kw, quality = observe.call_args.args
    assert draw_kw == pytest.approx(1.5)
    # 400 W of spread against the 150 W reference.
    assert quality == pytest.approx((4 / 6) * 150 / (150 + 400))


async def test_two_units_changing_together_teach_neither(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    coordinator._draw_pending.clear()
    changed_at = dt_util.utcnow() + timedelta(minutes=30)
    coordinator._note_compressor_transition(_GROUP, started=True, now=changed_at)
    coordinator._note_compressor_transition("climate.other", started=True, now=changed_at)
    mature = _step(hass, coordinator, changed_at, before=[1000.0] * 4, after=[3000.0] * 6)
    with patch.object(
        type(coordinator.draw_for(_GROUP)), "observe", autospec=True
    ) as observe:
        coordinator._process_draw_candidates(mature)
    observe.assert_not_called()
    assert not coordinator._draw_pending


async def test_a_change_with_no_readings_after_it_teaches_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    coordinator._draw_pending.clear()
    changed_at = dt_util.utcnow() + timedelta(minutes=30)
    coordinator._note_compressor_transition(_GROUP, started=True, now=changed_at)
    mature = _step(hass, coordinator, changed_at, before=[1000.0] * 4, after=[])
    with patch.object(
        type(coordinator.draw_for(_GROUP)), "observe", autospec=True
    ) as observe:
        coordinator._process_draw_candidates(mature)
    observe.assert_not_called()
    assert not coordinator._draw_pending


async def test_old_house_load_readings_are_dropped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _with_house_load(hass, mock_config_entry)
    start = dt_util.utcnow() + timedelta(hours=1)
    _sample(hass, coordinator, start, 900.0)
    _sample(hass, coordinator, start + timedelta(minutes=20), 1100.0)
    assert [watts for _, watts in coordinator._house_load_samples] == [1100.0]


# ---------------------------------------------------------------------------
# DR-039 - without a solar forecast, today's solar is derated by the weather
# ---------------------------------------------------------------------------


async def test_solar_is_derated_by_the_cloud_the_forecast_expects(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    # Clear now, clear for an hour, then overcast.
    coordinator.trajectory = _trajectory(
        now, 6, cloud_coverage=lambda hour: 0.0 if hour < 2 else 100.0
    )
    assert coordinator._sustained_solar_kw(now, 1.0, 4.0) == pytest.approx(4.0)
    assert coordinator._sustained_solar_kw(now, 3.0, 4.0) == pytest.approx(
        4.0 * OVERCAST_TRANSMISSION
    )


async def test_hours_the_forecast_does_not_cover_are_skipped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    coordinator.trajectory = _trajectory(now, 1, cloud_coverage=0.0)
    # Two hours past the end of a one-hour forecast: nothing to derate by.
    assert coordinator._sustained_solar_kw(now, 4.0, 3.0) == pytest.approx(3.0)


async def test_without_a_clear_sky_figure_now_the_reading_stands(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    coordinator.trajectory = _trajectory(now, 4)
    assert coordinator._sustained_solar_kw(now, 3.0, 2.5) == pytest.approx(2.5)
    coordinator.trajectory = None
    assert coordinator._sustained_solar_kw(now, 3.0, 2.5) == pytest.approx(2.5)
    assert coordinator._sustained_solar_kw(now, 3.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# DR-053 - what the weather coast's projection is fed
# ---------------------------------------------------------------------------


def _outdoor(hass: HomeAssistant) -> dict[str, str]:
    hass.states.async_set("sensor.outdoor_temperature", "20.0")
    hass.states.async_set("sensor.outdoor_humidity", "60.0")
    return {
        CONF_OUTDOOR_TEMPERATURE_ENTITY: "sensor.outdoor_temperature",
        CONF_OUTDOOR_HUMIDITY_ENTITY: "sensor.outdoor_humidity",
    }


async def _projection_inputs(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    now: datetime,
    *,
    trajectory: WeatherTrajectory | None,
    room_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The outdoor and sun callables the projection is handed, at `now`."""
    coordinator = await _setup(
        hass,
        entry,
        temperature="27.0",
        humidity="55.0",
        room_extra={"bands": {"occupied": {"low": 23.5, "high": 26.0}}, **(room_extra or {})},
        options_extra=_outdoor(hass),
    )
    model = coordinator.model_for("test_room")
    model.k_loss = Coefficient(value=0.5, variance=0.01, samples=40)
    model.k_solar = Coefficient(value=1.0, variance=0.01, samples=40)
    coordinator._was_cooling["test_room"] = False
    coordinator._stopped_at["test_room"] = None
    coordinator.trajectory = trajectory
    captured: dict[str, Any] = {}

    def capture(self: ThermalModel, indoor: float, **kwargs: Any) -> None:
        captured.update(kwargs)

    with (
        freeze_time(now),
        patch.object(ThermalModel, "project_unaided", autospec=True, side_effect=capture),
    ):
        # Readings reported at the moment being projected from, so none is stale.
        for entity_id in (
            "sensor.test_temperature",
            "sensor.test_humidity",
            "sensor.outdoor_temperature",
            "sensor.outdoor_humidity",
        ):
            state = hass.states.get(entity_id)
            assert state is not None
            hass.states.async_set(entity_id, state.state, force_update=True)
        coordinator._unaided_inputs(coordinator.rooms["test_room"], now)
    assert captured, "the projection was not reached"
    return captured


async def test_the_projection_uses_the_forecast_outdoor_temperature(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    fed = await _projection_inputs(
        hass,
        mock_config_entry,
        now,
        trajectory=_trajectory(now, 3, temperature_c=lambda hour: 30.0 + hour),
    )
    assert fed["outdoor_at"](0.0) == 30.0
    assert fed["outdoor_at"](1.5) == 31.0
    # Past the end of the forecast, the outdoor sensor.
    assert fed["outdoor_at"](6.0) == 20.0


async def test_without_a_forecast_the_outdoor_sensor_is_held(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    fed = await _projection_inputs(hass, mock_config_entry, now, trajectory=None)
    assert fed["outdoor_at"](0.0) == 20.0
    assert fed["outdoor_at"](0.75) == 20.0


async def test_the_sun_on_a_window_follows_the_sun_and_the_cloud(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    # The test harness puts Home Assistant in San Diego. At 22:00 UTC on
    # 12 October it is mid-afternoon there and the sun is in the south-west;
    # by 03:00 UTC it has set.
    now = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    fed = await _projection_inputs(
        hass,
        mock_config_entry,
        now,
        trajectory=_trajectory(
            now, 8, cloud_coverage=lambda hour: 0.0 if hour < 1 else 100.0
        ),
        room_extra={CONF_WINDOW_DIRECTION: "south_west"},
    )
    assert fed["sun_at"](0.0) == pytest.approx(1.0)
    assert fed["sun_at"](1.0) == pytest.approx(OVERCAST_TRANSMISSION)
    assert fed["sun_at"](5.0) == 0.0


async def test_a_sunlit_window_with_no_forecast_counts_full_sun(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    now = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    fed = await _projection_inputs(
        hass,
        mock_config_entry,
        now,
        trajectory=None,
        room_extra={CONF_WINDOW_DIRECTION: "south_west"},
    )
    assert fed["sun_at"](0.0) == 1.0
    # A window facing away from the afternoon sun.
    fed = await _projection_inputs(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=dict(mock_config_entry.data),
            options=dict(mock_config_entry.options),
        ),
        now,
        trajectory=None,
        room_extra={CONF_WINDOW_DIRECTION: "north_east"},
    )
    assert fed["sun_at"](0.0) == 0.0


async def test_a_direct_sun_sensor_holds_the_sun_as_it_is(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_sun", "on")
    now = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    fed = await _projection_inputs(
        hass,
        mock_config_entry,
        now,
        trajectory=_trajectory(now, 3, cloud_coverage=100.0),
        room_extra={
            CONF_WINDOW_DIRECTION: "south_west",
            CONF_DIRECT_SUN_ENTITY: "binary_sensor.test_sun",
        },
    )
    hass.states.async_set("binary_sensor.test_sun", "on")
    assert fed["sun_at"](0.0) == 1.0
    assert fed["sun_at"](2.0) == 1.0


# ---------------------------------------------------------------------------
# DR-012 - an empty room is warned about, then turned off, out loud
# ---------------------------------------------------------------------------


def _announcing(**extra: Any) -> dict[str, Any]:
    return {
        CONF_ANNOUNCE: True,
        CONF_ANNOUNCE_TARGETS: ["media_player.kitchen"],
        CONF_VACANT_AFTER: 10,
        CONF_WARNING_GRACE: 5,
        **extra,
    }


async def _leave(hass: HomeAssistant, coordinator: Any, minutes: int) -> None:
    hass.states.async_set("binary_sensor.test_presence", "off")
    start = dt_util.utcnow()
    for minute in range(1, minutes + 1):
        with freeze_time(start + timedelta(minutes=minute)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()


async def test_an_empty_room_is_warned_about_then_turned_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    await _leave(hass, coordinator, 17)
    messages = [call.data["message"] for call in spoken]
    assert messages == [
        "The Test Room has been empty for 10 minutes and the air conditioning "
        "is still running.",
        "Turning the Test Room air conditioning off.",
    ]
    # DR-069: through the engine, on the room's speakers.
    for call in spoken:
        assert call.data["entity_id"] == [ENGINE]
        assert call.data["media_player_entity_id"] == ["media_player.kitchen"]


async def test_a_warning_that_cannot_be_given_never_stops_the_evaluation(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """12-13 October: every vacancy warning raised and the evaluation failed.

    Whatever the call raises, the room is still evaluated, published and
    turned off, and the failure is logged once.
    """
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    await real_tts_speak(hass)
    hass.services.async_remove("tts", "speak")
    async_mock_service(
        hass, "tts", "speak", schema=vol.Schema({vol.Required("never"): str})
    )
    async_mock_service(hass, "climate", "set_hvac_mode")
    with caplog.at_level(logging.WARNING):
        await _leave(hass, coordinator, 17)
    assert coordinator.last_update_success
    assert "Unexpected error fetching" not in caplog.text
    assert caplog.text.count("Spoken warning not given: announcement for test_room") == 1
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None and state.state == "unoccupied"


async def test_with_no_engine_the_warning_is_skipped_and_reported(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass, engines=())
    async_mock_service(hass, "climate", "set_hvac_mode")
    with caplog.at_level(logging.WARNING):
        await _leave(hass, coordinator, 17)
    assert not spoken
    assert coordinator.last_update_success
    assert caplog.text.count("Home Assistant has no text-to-speech engine") == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ANNOUNCEMENTS_UNAVAILABLE)
    assert issue is not None
    assert issue.translation_placeholders == {
        "reason": "Home Assistant has no text-to-speech engine"
    }


async def test_with_several_engines_the_chosen_one_speaks(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(
        hass, engines=("tts.google_translate", "tts.home_assistant_cloud")
    )
    async_mock_service(hass, "climate", "set_hvac_mode")
    room = coordinator.rooms["test_room"]
    coordinator._pending_announcements.append((room, Announcement.FIRST_WARNING))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert not spoken
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ANNOUNCEMENTS_UNAVAILABLE)
    assert issue is not None
    assert "there are 2" in issue.translation_placeholders["reason"]

    coordinator.tts_engine_entity_id = "tts.home_assistant_cloud"
    coordinator._pending_announcements.append((room, Announcement.FIRST_WARNING))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in spoken] == [["tts.home_assistant_cloud"]]
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ANNOUNCEMENTS_UNAVAILABLE)
        is None
    )


async def test_a_chosen_engine_that_has_gone_is_named(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    await real_tts_speak(hass)
    coordinator.tts_engine_entity_id = "tts.removed"
    assert coordinator._tts_engine() == (
        None,
        "the chosen text-to-speech engine tts.removed does not exist",
    )


async def test_a_room_with_nowhere_to_speak_is_silent(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass, mock_config_entry, room_extra=_announcing(**{CONF_ANNOUNCE_TARGETS: []})
    )
    spoken = await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    await _leave(hass, coordinator, 17)
    assert not spoken


# ---------------------------------------------------------------------------
# DR-070 - a room speaks only while it is run and awake
# ---------------------------------------------------------------------------


async def test_a_room_with_automatic_control_off_says_nothing_when_it_empties(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """13 October: the Office, not under automatic control, still said it
    was turning its air conditioning off."""
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    await coordinator.async_set_room_switched_off("test_room", True)
    await hass.async_block_till_done()
    await _leave(hass, coordinator, 17)
    assert not spoken


async def test_a_room_asleep_by_its_schedule_says_nothing_when_it_empties(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    hass.states.async_set("binary_sensor.test_sleep", "on")
    coordinator = await _setup(
        hass,
        mock_config_entry,
        room_extra=_announcing(
            **{CONF_SLEEP_SCHEDULE_ENTITY: "binary_sensor.test_sleep"}
        ),
    )
    spoken = await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    await _leave(hass, coordinator, 17)
    assert not spoken


@pytest.mark.parametrize(
    "announcement", [Announcement.FIRST_WARNING, Announcement.FREE_COOLING]
)
async def test_a_room_on_sleep_says_nothing_and_speaks_again_once_awake(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, announcement: Announcement
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    room = coordinator.rooms["test_room"]
    coordinator._remember_comfort_mode("test_room", ComfortMode.SLEEP)
    coordinator._pending_announcements.append((room, announcement))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert not spoken

    coordinator._remember_comfort_mode("test_room", ComfortMode.SCHEDULE)
    coordinator._pending_announcements.append((room, announcement))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert len(spoken) == 1


async def test_free_cooling_advice_is_not_spoken_with_automatic_control_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    room = coordinator.rooms["test_room"]
    coordinator._switched_off.add("test_room")
    coordinator._pending_announcements.append((room, Announcement.FREE_COOLING))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert not spoken

    coordinator._switched_off.discard("test_room")
    coordinator._pending_announcements.append((room, Announcement.FREE_COOLING))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert len(spoken) == 1


async def test_a_room_waiting_for_its_unit_says_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    room = replace(coordinator.rooms["test_room"], capabilities=None)
    coordinator.rooms["test_room"] = room
    coordinator._pending_announcements.append((room, Announcement.FIRST_WARNING))
    await coordinator._async_announce()
    await hass.async_block_till_done()
    assert not spoken


# ---------------------------------------------------------------------------
# DR-081 - the model learns against the setpoint the unit reports
# ---------------------------------------------------------------------------


async def test_learning_uses_the_setpoint_the_unit_reports(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Until 0.9.6 the interval was filed against target plus trim, which the
    room loop has not sent since 0.9.0, and against the coordinator's figure
    whatever the unit had been set to by hand."""
    coordinator = await _setup(hass, mock_config_entry)
    seen = []
    model_class = type(coordinator.model_for("test_room"))
    original = model_class.observe

    def spy(self, obs):
        seen.append(obs)
        return original(self, obs)

    start = dt_util.utcnow()
    with patch.object(model_class, "observe", autospec=True, side_effect=spy):
        for minutes in (10, 12, 14, 16):
            with freeze_time(start + timedelta(minutes=minutes)):
                hass.states.async_set(
                    "climate.test", "cool", {**_UNIT, "temperature": 18.0},
                    force_update=True,
                )
                hass.states.async_set(
                    "sensor.test_temperature", str(25.9 - minutes / 100),
                    force_update=True,
                )
                await coordinator.async_refresh()
                await hass.async_block_till_done()
    assert seen
    assert {obs.commanded_setpoint_c for obs in seen} == {18.0}


async def test_a_unit_reporting_a_range_is_read_at_its_middle(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]
    hass.states.async_set(
        "climate.test",
        "heat_cool",
        {**_UNIT, "target_temp_low": 21.0, "target_temp_high": 25.0},
    )
    assert coordinator._unit_setpoint(room) == 23.0
    hass.states.async_set("climate.test", "unavailable", _UNIT)
    assert coordinator._unit_setpoint(room) is None


# ---------------------------------------------------------------------------
# DR-075 - the setpoint is rounded the way the room loop works
# ---------------------------------------------------------------------------


async def test_the_setpoint_rounds_the_way_the_loop_works(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A room 0.1 C below its target while the loop is still cooling: the
    room against its target says heat, which rounded up to the next whole
    degree on 13 October."""
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]
    now = dt_util.utcnow()
    inputs = RoomInputs(now=now, temperature_c=20.7, relative_humidity=60.0)
    trace = DecisionTrace(room_id="test_room", at=now, mode=Mode.SLEEP)
    trace.target_dry_bulb_c = 20.8
    trace.actuator = ActuatorStep.NONE
    pid = PidSetpoint(
        setpoint_c=20.4,
        approach_c=0.3,
        feed_forward_c=0.3,
        wanted_rate_c_per_hour=0.2,
        unit_rate_c_per_hour=0.1,
        reason="regulation: test",
        direction="cool",
    )
    with patch.object(coordinator, "_room_loop_setpoint", return_value=(20.4, pid)):
        coordinator._regulate(room, inputs, trace, now)
    assert trace.commanded_dry_bulb_c == 20.0


# ---------------------------------------------------------------------------
# DR-077 - a warning is said only when it is true
# ---------------------------------------------------------------------------


async def test_a_room_whose_unit_is_off_is_not_told_it_is_running(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """13 October: "empty for 10 minutes and the air conditioning is still
    running" for a unit that was not."""
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("climate.test", "off", _UNIT)
    await _leave(hass, coordinator, 17)
    assert not spoken


async def test_turning_off_is_said_only_with_the_off_command(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    room = coordinator.rooms["test_room"]
    trace = DecisionTrace(room_id="test_room", at=dt_util.utcnow(), mode=Mode.UNOCCUPIED)
    trace.actuator = ActuatorStep.OFF
    trace.hold_compressor = True
    coordinator._pending_announcements.append((room, Announcement.FINAL_WARNING))
    await coordinator._async_announce({"test_room": trace})
    await hass.async_block_till_done()
    assert not spoken
    assert coordinator._deferred_final == {"test_room": Announcement.FINAL_WARNING}

    # The guard lets the stop through: said now.
    trace.hold_compressor = False
    trace.stopped = True
    await coordinator._async_announce({"test_room": trace})
    await hass.async_block_till_done()
    assert [c.data["message"] for c in spoken] == [
        "Turning the Test Room air conditioning off."
    ]
    assert coordinator._deferred_final == {}


async def test_a_held_warning_is_dropped_when_the_room_no_longer_stops(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_announcing())
    spoken = await real_tts_speak(hass)
    coordinator._deferred_final["test_room"] = Announcement.OPENING_FINAL_WARNING
    trace = DecisionTrace(room_id="test_room", at=dt_util.utcnow(), mode=Mode.OCCUPIED)
    trace.actuator = ActuatorStep.COMPRESSOR
    await coordinator._async_announce({"test_room": trace})
    await hass.async_block_till_done()
    assert not spoken
    assert coordinator._deferred_final == {}


# ---------------------------------------------------------------------------
# DR-082 - no wait for a warning nobody will hear
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("targets", "unit", "waits"),
    [
        (["media_player.kitchen"], "cool", True),
        ([], "cool", False),
        (["media_player.kitchen"], "off", False),
    ],
)
async def test_the_warning_wait_only_where_a_warning_is_said(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    targets: list[str],
    unit: str,
    waits: bool,
) -> None:
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(
        hass,
        mock_config_entry,
        room_extra=_announcing(**{CONF_ANNOUNCE_TARGETS: targets}),
    )
    await real_tts_speak(hass)
    async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("climate.test", unit, _UNIT)
    await _leave(hass, coordinator, 11)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert (state.state != "unoccupied") is waits, state.state


# ---------------------------------------------------------------------------
# DR-073 - outdoor air judged better
# ---------------------------------------------------------------------------


async def test_the_free_cooling_wait_runs_from_the_first_advice(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]
    start = dt_util.utcnow()
    advised = DecisionTrace(room_id="test_room", at=start, mode=Mode.OCCUPIED)
    advised.free_cooling_advised = True
    coordinator._announce_free_cooling(room, advised)
    later = replace(advised, at=start + timedelta(minutes=5))
    coordinator._announce_free_cooling(room, later)
    assert coordinator._inputs_for(room, start + timedelta(minutes=5)).free_cooling_advised
    assert not coordinator._inputs_for(
        room, start + timedelta(minutes=5)
    ).free_cooling_wait_over
    assert coordinator._inputs_for(
        room, start + timedelta(minutes=10)
    ).free_cooling_wait_over

    ended = replace(advised, at=start + timedelta(minutes=11))
    ended.free_cooling_advised = False
    coordinator._announce_free_cooling(room, ended)
    inputs = coordinator._inputs_for(room, start + timedelta(minutes=12))
    assert not inputs.free_cooling_advised
    assert not inputs.free_cooling_wait_over


async def test_an_opening_made_on_the_advice_is_not_warned_about(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass, mock_config_entry, room_extra=_announcing(opening_entity_ids=[])
    )
    room = coordinator.rooms["test_room"]
    now = dt_util.utcnow()
    opened = RoomInputs(
        now=now,
        opening_open=True,
        opening_open_since=now - timedelta(minutes=30),
        free_cooling_advised=True,
    )
    coordinator._warn_about_opening(room, opened, now)
    assert coordinator._pending_announcements == []
    coordinator._warn_about_opening(
        room, replace(opened, free_cooling_advised=False), now
    )
    assert coordinator._pending_announcements == [
        (room, Announcement.OPENING_FINAL_WARNING)
    ]


# ---------------------------------------------------------------------------
# DR-074 - the unit's own reading stands in for a room sensor that is down
# ---------------------------------------------------------------------------


async def test_an_unavailable_room_sensor_is_replaced_by_the_units_reading(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set(
        "climate.test", "off", {**_UNIT, "current_temperature": 26.0}
    )
    hass.states.async_set("sensor.test_temperature", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    trace = coordinator.data["test_room"]
    assert trace.room_c == 26.0
    assert trace.hci is not None
    assert any("air conditioner's own sensor, 26.0 C" in r for r in trace.reasons)
    # Nothing is learned from it.
    assert "test_room" not in coordinator._previous


async def test_the_units_humidity_stands_in_too_where_it_reports_one(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set(
        "climate.test",
        "off",
        {**_UNIT, "current_temperature": 26.0, "current_humidity": 58},
    )
    hass.states.async_set("sensor.test_temperature", "unavailable")
    hass.states.async_set("sensor.test_humidity", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    trace = coordinator.data["test_room"]
    assert trace.hci is not None
    assert any("humidity from the air conditioner's own sensor, 58%" in r for r in trace.reasons)


async def test_with_no_unit_reading_the_room_still_says_what_is_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set("sensor.test_temperature", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    trace = coordinator.data["test_room"]
    assert any("not reporting" in r for r in trace.rejected)


# ---------------------------------------------------------------------------
# A removed room's device goes with it, through the calls Home Assistant
# keeps after 2027.8
# ---------------------------------------------------------------------------


async def test_a_removed_rooms_device_is_removed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "old_room")},
    )
    coordinator.previous_rooms = {*coordinator.previous_rooms, "old_room"}
    coordinator._async_remove_stale_devices({"test_room"})
    assert registry.async_get(device.id) is None
    assert registry.async_get_device_by_identifier(
        (DOMAIN, "test_room"), mock_config_entry.entry_id
    ) is not None


# ---------------------------------------------------------------------------
# A reading that cannot be used is logged once, and once when it comes back
# ---------------------------------------------------------------------------


async def test_a_non_numeric_reading_is_logged_once_and_its_return_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set("sensor.test_humidity", "E3")
    start = dt_util.utcnow()
    with caplog.at_level(logging.INFO):
        for minute in range(1, 6):
            with freeze_time(start + timedelta(minutes=minute)):
                await coordinator.async_refresh()
                await hass.async_block_till_done()
        assert coordinator._number("sensor.test_humidity") is None
        hass.states.async_set("sensor.test_humidity", "58.0")
        for minute in range(6, 9):
            with freeze_time(start + timedelta(minutes=minute)):
                await coordinator.async_refresh()
                await hass.async_block_till_done()
    assert caplog.text.count("sensor.test_humidity reported a non-numeric value: E3") == 1
    assert caplog.text.count("sensor.test_humidity is available again") == 1
    assert coordinator._number("sensor.test_humidity") == 58.0


# ---------------------------------------------------------------------------
# DR-033 - a forecast that cannot be compared with the clock costs only precool
# ---------------------------------------------------------------------------


async def test_a_forecast_with_no_time_zone_falls_back_and_is_reported(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    naive = datetime(2026, 10, 12, 2, 0)
    coordinator.trajectory = WeatherTrajectory(
        tuple(
            ForecastPoint(at=naive + timedelta(hours=hour), temperature_c=32.0)
            for hour in range(6)
        ),
        naive,
    )
    room = coordinator.rooms["test_room"]
    result = coordinator._demand_ahead(room, dt_util.utcnow())
    assert result in (True, False)
    assert coordinator.trajectory is None
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_FORECAST_UNAVAILABLE)
    assert issue is not None
    assert "could not be compared against the clock" in issue.translation_placeholders[
        "reason"
    ]


# ---------------------------------------------------------------------------
# DR-023 - direct sun on a window, from the sun's own position
# ---------------------------------------------------------------------------


async def test_the_sun_entity_says_whether_the_sun_is_on_the_window(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass, mock_config_entry, room_extra={CONF_WINDOW_DIRECTION: "south_west"}
    )
    room = coordinator.rooms["test_room"]
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": 225.0, "elevation": 40.0})
    assert coordinator._direct_sun(room) is True
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": 45.0, "elevation": 40.0})
    assert coordinator._direct_sun(room) is False
    # A sun entity without a usable position says nothing either way.
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": "unknown", "elevation": 40.0})
    assert coordinator._direct_sun(room) is None


# ---------------------------------------------------------------------------
# DR-051, DR-062, DR-065 - the power ceiling at the edges of a window
# ---------------------------------------------------------------------------


def _regulated(coordinator: Any, ceiling_c: float | None) -> Any:
    now = dt_util.utcnow()
    room = coordinator.rooms["test_room"]
    inputs = coordinator._inputs_for(room, now)
    trace = DecisionTrace(
        room_id="test_room",
        at=now,
        mode=Mode.OCCUPIED,
        actuator=ActuatorStep.COMPRESSOR,
        demand="cool",
    )
    trace.target_dry_bulb_c = 21.0
    trace.power_ceiling_c = ceiling_c
    coordinator._regulate(room, inputs, trace, now)
    return trace


async def test_a_binding_ceiling_rounds_towards_the_room_to_stay_inside_it(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Room 23.4 C, 0.5 C allowed: 22.9 C is the limit, and 22.0 C would break it."""
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    trace = _regulated(coordinator, 0.5)
    assert trace.commanded_dry_bulb_c == 23.0, trace.reasons
    assert any("commanded held at 22.9 C" in note for note in trace.reasons)


async def test_without_a_ceiling_cooling_rounds_down(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    trace = _regulated(coordinator, None)
    assert trace.commanded_dry_bulb_c is not None
    assert trace.commanded_dry_bulb_c <= 21.0, trace.reasons
    assert not any("commanded held at" in note for note in trace.reasons)


async def test_the_held_ceiling_is_let_go_when_the_window_ends(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    now = dt_util.utcnow()
    coordinator._ceiling_bins["test_room"] = (0, now)
    # Five hours on, in the free interval after the no-import window.
    later = now + timedelta(hours=5)
    with freeze_time(later):
        for entity_id in ("sensor.test_temperature", "sensor.test_humidity"):
            state = hass.states.get(entity_id)
            assert state is not None
            hass.states.async_set(entity_id, state.state, force_update=True)
        trace = _ceiling(coordinator, now=later)
    assert "test_room" not in coordinator._ceiling_bins
    assert trace.power_ceiling_c is None
    assert trace.actuator is ActuatorStep.COMPRESSOR


async def test_with_no_battery_reading_the_room_is_not_held_back(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """DR-044: a budget that cannot be worked out never stops or throttles."""
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    hass.states.async_set("sensor.battery_soc", "unavailable")
    coordinator._power_context = coordinator._compute_power_context(dt_util.utcnow())
    trace = _ceiling(coordinator)
    assert trace.actuator is ActuatorStep.COMPRESSOR
    assert trace.power_ceiling_c is None
    assert trace.power_budget_kw is None


# ---------------------------------------------------------------------------
# A room that cannot be read stops setup with a message the user can act on
# ---------------------------------------------------------------------------


async def _setup_with_room(hass: HomeAssistant, room: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"rooms": []}, options={"rooms": [room]})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_room_with_unreadable_bands_stops_setup_and_says_which(
    hass: HomeAssistant, room_config: dict[str, Any]
) -> None:
    room = {**room_config, "bands": {"occupied": {"low": 25.0}}}
    entry = await _setup_with_room(hass, room)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "room_bands_invalid"
    assert entry.error_reason_translation_placeholders == {
        "room_id": "test_room",
        "error": "'high'",
    }


async def test_a_room_missing_its_name_stops_setup_and_says_which(
    hass: HomeAssistant, room_config: dict[str, Any]
) -> None:
    room = {key: value for key, value in room_config.items() if key != "name"}
    entry = await _setup_with_room(hass, room)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "room_setting_missing"
    assert entry.error_reason_translation_placeholders == {
        "room_id": "test_room",
        "setting": "'name'",
    }
