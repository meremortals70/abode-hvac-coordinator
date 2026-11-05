"""Home Assistant's test helpers, from the package that matches the
release under test.

From 2026.11 they come from Home Assistant's own pytest-home-assistant
(packages/pytest-home-assistant in the core repository); before it, from
pytest-homeassistant-custom-component, which was generated from the same
helpers. The names are the same in both [HA].
"""

from __future__ import annotations

try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_service,
    )
    from pytest_homeassistant.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
    from pytest_homeassistant.components.recorder.common import (
        async_wait_recording_done,
    )
    from pytest_homeassistant.typing import (
        ClientSessionGenerator,
        WebSocketGenerator,
    )
except ModuleNotFoundError:  # before 2026.11
    from pytest_homeassistant_custom_component.common import (  # type: ignore[no-redef]
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_service,
    )
    from pytest_homeassistant_custom_component.components.diagnostics import (  # type: ignore[no-redef]
        get_diagnostics_for_config_entry,
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (  # type: ignore[no-redef]
        async_wait_recording_done,
    )
    from pytest_homeassistant_custom_component.typing import (  # type: ignore[no-redef]
        ClientSessionGenerator,
        WebSocketGenerator,
    )

__all__ = [
    "ClientSessionGenerator",
    "MockConfigEntry",
    "WebSocketGenerator",
    "async_fire_time_changed",
    "async_mock_service",
    "async_wait_recording_done",
    "get_diagnostics_for_config_entry",
]
