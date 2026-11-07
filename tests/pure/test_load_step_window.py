"""The house-load step (4.8, source 3) judges the minute after a command
only once a load reading from the end of that minute is in, run in the
coordinator's order: Observe builds the snapshot before the cycle's own
house-load reading is added."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from tests.pure._load import load

models = load("models")
observe = load("observe")

CLIMATE = "climate.living_room_aircon"
UNIT = "own:" + CLIMATE
T0 = datetime(2026, 11, 7, 3, 0, 0, tzinfo=UTC)


def _room() -> object:
    return models.RoomConfig(
        room_id="living", name="Living", heads=(models.HeadConfig(CLIMATE),),
        temperature_sensor="sensor.t", humidity_sensor="sensor.h",
        occupied_band=(23.0, 26.0), sleep_band=(20.0, 23.0), precool_band=(23.0, 26.0),
    )


def _states(now: datetime, mode: str) -> dict:
    attrs = {"current_temperature": 26.0, "temperature": 24.0}
    return {CLIMATE: models.RawState(CLIMATE, mode, attrs, T0, now)}


class LoadStepWindow(unittest.TestCase):
    def test_a_start_40_seconds_after_the_command_is_seen(self) -> None:
        observer = observe.RoomObserver(_room())
        lh = observe.LoadHistory()
        commands: dict[str, datetime] = {}
        command_at = T0 + timedelta(minutes=1)  # Act sends cool in this cycle
        start_at = command_at + timedelta(seconds=40)  # the compressor starts 40 s later

        seen = []
        now = T0
        while now <= T0 + timedelta(minutes=6):
            mode = "off" if now <= command_at else "cool"
            snap = observer.build(
                house=models.HouseConfig(house_load="sensor.load"), states=_states(now, mode), now=now,
                choices=models.RoomChoices(), location=None, forecast=(), forecast_temperature_unit=None,
                forecast_wind_unit=None, tariff=(), load_history=lh, commands=commands,
                draw_kw={UNIT: 1.5}, head_outdoor_units={CLIMATE: UNIT},
            )
            seen.append((now.strftime("%M:%S"), snap.heads[0].compressor.value))
            lh.add(now, 1.0 + (1.5 if now >= start_at else 0.0))  # coordinator._house
            if now == command_at:
                commands[CLIMATE] = now
            now += timedelta(seconds=30)
        self.assertIn("running", {s for _, s in seen}, f"compressor by cycle: {seen}")



if __name__ == "__main__":
    unittest.main()
