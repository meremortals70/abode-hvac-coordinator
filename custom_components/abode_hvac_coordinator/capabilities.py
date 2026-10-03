"""What a room's units can do, read when the room is configured.

Pure. No Home Assistant imports.

DR-048. Until 0.9.0 the component never recorded what a climate entity offers.
Fan and swing were looked up at command time against fixed lists of names, a
unit whose names were not on the lists was silently sent nothing, and there was
nothing to build a control from. The setup flow now reads each head once and
stores a profile; every command and every control the integration offers for
the room is built from it.

A room with two heads gets the **intersection**: a mode, a fan speed or a vane
position is only offered if both heads have it, and the temperature range is
the narrower of the two.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Keys of the stored profile, so a stored dict and the dataclass cannot drift.
KEY_HVAC_MODES = "hvac_modes"
KEY_FAN_MODES = "fan_modes"
KEY_SWING_MODES = "swing_modes"
KEY_SWING_HORIZONTAL_MODES = "swing_horizontal_modes"
KEY_MIN_TEMP = "min_temp"
KEY_MAX_TEMP = "max_temp"
KEY_STEP = "target_temp_step"
KEY_SINGLE_TARGET = "single_target"
KEY_RANGE_TARGET = "range_target"


@dataclass(frozen=True, slots=True)
class RoomCapabilities:
    """The controls a room's units offer, as read at setup."""

    hvac_modes: tuple[str, ...] = ()
    fan_modes: tuple[str, ...] = ()
    swing_modes: tuple[str, ...] = ()
    swing_horizontal_modes: tuple[str, ...] = ()
    min_temp: float | None = None
    max_temp: float | None = None
    target_temp_step: float | None = None
    single_target: bool = False
    range_target: bool = False

    @property
    def has_fan(self) -> bool:
        """Whether the units offer a choice of fan speed."""
        return bool(self.fan_modes)

    @property
    def has_swing(self) -> bool:
        """Whether the units offer a choice of vertical vane position."""
        return bool(self.swing_modes)

    @property
    def has_swing_horizontal(self) -> bool:
        """Whether the units offer a choice of horizontal vane position."""
        return bool(self.swing_horizontal_modes)

    @property
    def has_setpoint(self) -> bool:
        """Whether the units take a temperature target at all."""
        return self.single_target or self.range_target

    def to_dict(self) -> dict[str, Any]:
        """For storage in the config entry or the store."""
        return {
            KEY_HVAC_MODES: list(self.hvac_modes),
            KEY_FAN_MODES: list(self.fan_modes),
            KEY_SWING_MODES: list(self.swing_modes),
            KEY_SWING_HORIZONTAL_MODES: list(self.swing_horizontal_modes),
            KEY_MIN_TEMP: self.min_temp,
            KEY_MAX_TEMP: self.max_temp,
            KEY_STEP: self.target_temp_step,
            KEY_SINGLE_TARGET: self.single_target,
            KEY_RANGE_TARGET: self.range_target,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> RoomCapabilities | None:
        """A stored profile, or None where nothing valid is stored."""
        if not isinstance(raw, Mapping) or KEY_HVAC_MODES not in raw:
            return None

        def names(key: str) -> tuple[str, ...]:
            value = raw.get(key) or []
            return tuple(str(item) for item in value)

        def number(key: str) -> float | None:
            value = raw.get(key)
            try:
                return None if value is None else float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            hvac_modes=names(KEY_HVAC_MODES),
            fan_modes=names(KEY_FAN_MODES),
            swing_modes=names(KEY_SWING_MODES),
            swing_horizontal_modes=names(KEY_SWING_HORIZONTAL_MODES),
            min_temp=number(KEY_MIN_TEMP),
            max_temp=number(KEY_MAX_TEMP),
            target_temp_step=number(KEY_STEP),
            single_target=bool(raw.get(KEY_SINGLE_TARGET)),
            range_target=bool(raw.get(KEY_RANGE_TARGET)),
        )


def _common(lists: Sequence[Iterable[str]]) -> tuple[str, ...]:
    """Items present in every list, in the first list's order."""
    if not lists:
        return ()
    first = list(lists[0])
    rest = [set(other) for other in lists[1:]]
    return tuple(item for item in first if all(item in other for other in rest))


def intersect(profiles: Sequence[RoomCapabilities]) -> RoomCapabilities:
    """What a room with several heads can do on all of them.

    Modes and positions common to every head, the narrower temperature range,
    the coarser step, and a target type only if every head takes it. A step
    that is not a whole multiple across the heads is not reconciled here: the
    setpoint rounding says so (`regulate.combine_limits`).
    """
    if not profiles:
        return RoomCapabilities()
    if len(profiles) == 1:
        return profiles[0]
    minimums = [p.min_temp for p in profiles if p.min_temp is not None]
    maximums = [p.max_temp for p in profiles if p.max_temp is not None]
    steps = [p.target_temp_step for p in profiles if p.target_temp_step]
    return RoomCapabilities(
        hvac_modes=_common([p.hvac_modes for p in profiles]),
        fan_modes=_common([p.fan_modes for p in profiles]),
        swing_modes=_common([p.swing_modes for p in profiles]),
        swing_horizontal_modes=_common([p.swing_horizontal_modes for p in profiles]),
        min_temp=max(minimums) if minimums else None,
        max_temp=min(maximums) if maximums else None,
        target_temp_step=max(steps) if steps else None,
        single_target=all(p.single_target for p in profiles),
        range_target=all(p.range_target for p in profiles),
    )
