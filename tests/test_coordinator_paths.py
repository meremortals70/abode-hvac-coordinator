"""Coordinator paths a normal evaluation reaches only in particular conditions.

Each test drives the coordinator as Home Assistant runs it: real states, real
service calls, a real config entry. Where a path is reached only at a point in
time, or after a transition the test cannot make the unit perform, the method
that owns it is called directly against that real coordinator.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
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
    DecisionTrace,
    Mode,
)
from custom_components.abode_hvac_coordinator.regulate import CompressorState
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
from test_living_room_night import _ceiling, _setup, _setup_power

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
