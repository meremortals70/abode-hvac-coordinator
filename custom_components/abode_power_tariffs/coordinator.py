"""Runtime state for one tariff channel.

There is no polling. The next period boundary is computed and scheduled; on
firing, entities update and the following boundary is scheduled. A separate
midnight trigger resets the daily allowance and the supply-charge accumulator.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from . import allowance as allowance_module
from . import intervals as intervals_module
from .const import (
    CONF_COUNT_ALLOWANCE,
    CONF_HOLIDAY_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_TARIFF_SELECTS,
    DOMAIN,
    SIGNAL_UPDATE,
)
from .plan import Plan, Rate, Resolution
from .validate import validate_plan

_LOGGER = logging.getLogger(__name__)

UNUSABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN, None, "")


def _allowance_slot(resolution: Resolution, day: date) -> str:
    """Identify one occurrence of one slot.

    The slot is the period, qualified by its timetable's rate, and the date it
    falls on. Two periods naming the same rate on the same day are two slots
    with an allowance each; the same period tomorrow is a different occurrence.
    """
    return (
        f"{resolution.rate.qualified_name}"
        f"@{resolution.period.start}-{resolution.period.end}"
        f"/{day.isoformat()}"
    )


@dataclass(slots=True)
class TariffState:
    """Everything the entities read, recomputed at each boundary."""

    resolution: Resolution | None = None
    effective_rate: Rate | None = None
    allowance_used_kwh: float = 0.0
    allowance_remaining_kwh: float | None = None
    allowance_exhausted: bool = False
    # Which occurrence of which capped slot the count above belongs to. The
    # allowance is the slot's, not the day's, so this and not the calendar is
    # what says whether a count is still live.
    allowance_slot: str | None = None
    next_change: datetime | None = None
    next_export_change: datetime | None = None
    plan_expired: bool = False
    trace: tuple[str, ...] = ()


class TariffCoordinator:
    """Holds the plan, resolves it, and tells the entities when it changed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        plan: Plan,
        options: dict[str, Any],
    ) -> None:
        """Initialise the coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.plan = plan
        self.options = options
        self.state = TariffState()

        self._unsubscribes: list[CALLBACK_TYPE] = []
        self._boundary_unsubscribe: CALLBACK_TYPE | None = None
        self._last_energy_total: float | None = None
        self._last_written_tariff: str | None = None
        self._holiday_warned = False
        self._energy_warned = False
        self._forward_key: tuple[Any, ...] | None = None
        self._forward_series: list[intervals_module.Interval] = []

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        """Begin tracking. Called once, from async_setup_entry."""
        holiday_entity = self.options.get(CONF_HOLIDAY_SENSOR)
        energy_entity = self.options.get(CONF_IMPORT_ENERGY_SENSOR)

        tracked = [entity for entity in (holiday_entity, energy_entity) if entity]
        if tracked:
            self._unsubscribes.append(
                async_track_state_change_event(
                    self.hass, tracked, self._handle_state_change
                )
            )

        # Recompute on the zero second of every minute. Boundaries are whole
        # minutes, so a tick lands on each one exactly, and resolve_at works
        # forwards from the current instant so it is already right inside a
        # repeated hour. Nothing is then load-bearing: a wrong scheduled
        # instant costs a minute rather than hours. A tick that changes
        # nothing writes nothing, because async_write_ha_state is a no-op on
        # an unchanged value.
        self._unsubscribes.append(
            async_track_time_change(self.hass, self.async_refresh, second=0)
        )

        self._seed_energy_total()
        self.async_refresh()

    @callback
    def async_shutdown(self) -> None:
        """Cancel every listener. Called from async_unload_entry."""
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        if self._boundary_unsubscribe is not None:
            self._boundary_unsubscribe()
            self._boundary_unsubscribe = None

    # -------------------------------------------------------------- resolving

    @property
    def counting_allowance(self) -> bool:
        """Return whether this channel keeps a running total against a cap.

        Off by default and separate from the plan. The plan always declares
        the cap and what is paid past it; counting is an estimate from a meter
        the user nominates, reset on a local 24-hour clock, and will not agree
        exactly with a retailer counting it their own way.
        """
        return bool(self.options.get(CONF_COUNT_ALLOWANCE)) and bool(
            self.options.get(CONF_IMPORT_ENERGY_SENSOR)
        )

    @property
    def zone(self) -> Any:
        """Return Home Assistant's configured time zone."""
        return dt_util.get_default_time_zone()

    def is_holiday(self, day: date) -> bool:
        """Return whether a date is a public holiday.

        Only today can be answered, because the nominated binary sensor reports
        one day at a time. Other dates are treated as ordinary, which is the
        same answer as having no sensor at all.
        """
        entity_id = self.options.get(CONF_HOLIDAY_SENSOR)
        if not entity_id:
            return False
        if day != dt_util.now().date():
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNUSABLE:
            if not self._holiday_warned:
                _LOGGER.warning(
                    "Holiday sensor %s is unavailable; treating today as an ordinary day",
                    entity_id,
                )
                self._holiday_warned = True
            return False
        if self._holiday_warned:
            _LOGGER.info("Holiday sensor %s is available again", entity_id)
            self._holiday_warned = False
        # A workday sensor is on when it is a working day, so a public holiday
        # is the off state. The option is documented as a holiday sensor, so
        # the inversion is applied here rather than asked of the user.
        is_working_day: bool = state.state == STATE_ON
        return not is_working_day

    @callback
    def async_refresh(self, _now: datetime | None = None) -> None:
        """Recompute the state and tell the entities."""
        now = dt_util.now()
        today = now.date()
        trace: list[str] = []

        self.state.plan_expired = not self.plan.is_active_on(today)
        if self.state.plan_expired:
            trace.append("plan validity has passed; holding the expired plan")

        resolution = intervals_module.resolve_at(
            self.plan, now, self.zone, self.is_holiday
        )
        self.state.resolution = resolution

        if resolution is None:
            self.state.effective_rate = None
            trace.append("no period resolves at this moment")
        else:
            trace.append(f"day set {resolution.day_pattern.name}")
            slot = _allowance_slot(resolution, today)
            if slot != self.state.allowance_slot:
                # A different slot, or the same slot on another day. The
                # allowance belongs to the slot, so the count starts again;
                # nothing is carried from an earlier one.
                self.state.allowance_used_kwh = 0.0
                self.state.allowance_slot = slot
            if self.counting_allowance:
                allowance_state = allowance_module.apply(
                    self.plan, resolution.rate, self.state.allowance_used_kwh
                )
                self.state.effective_rate = allowance_state.rate
                self.state.allowance_remaining_kwh = allowance_state.remaining_kwh
                self.state.allowance_exhausted = allowance_state.exhausted
                trace.append(allowance_state.reason)
                if allowance_state.exhausted:
                    trace.append(f"priced at {allowance_state.rate.name}")
            else:
                # The cap is declared and published; nothing is counted here,
                # so the price is the scheduled one and a consumer applying
                # the rule itself has the cap and the fallback to work from.
                self.state.effective_rate = resolution.rate
                self.state.allowance_remaining_kwh = None
                self.state.allowance_exhausted = False
                if resolution.rate.has_allowance:
                    trace.append("capped, but usage is not being counted here")

        # Two separate facts. The import rate can be flat all day while the
        # feed-in price moves, and a consumer weighing an export against a
        # cheaper import later needs to know which of the two is changing.
        self.state.next_change = intervals_module.next_boundary(
            self.plan, now, self.zone, self.is_holiday
        )
        self.state.next_export_change = intervals_module.next_boundary(
            self.plan, now, self.zone, self.is_holiday, export=True
        )
        self.state.trace = tuple(trace)

        self._schedule_next_boundary()
        self._async_write_tariff_selects()
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry_id}")

    def _schedule_next_boundary(self) -> None:
        """Wake at whichever comes first, the import change or the feed-in one.

        Compared as real instants. Two datetimes carrying the same tzinfo are
        compared on the wall clock, which picks the wrong one on the day the
        clocks go back.
        """
        if self._boundary_unsubscribe is not None:
            self._boundary_unsubscribe()
            self._boundary_unsubscribe = None
        candidates = [
            moment
            for moment in (self.state.next_change, self.state.next_export_change)
            if moment is not None
        ]
        if not candidates:
            _LOGGER.debug("No further boundary found; nothing scheduled")
            return
        self._boundary_unsubscribe = async_track_point_in_time(
            self.hass,
            self.async_refresh,
            min(candidates, key=lambda moment: moment.astimezone(UTC)),
        )

    # ------------------------------------------------------------- allowances

    def _seed_energy_total(self) -> None:
        if not self.counting_allowance:
            return
        entity_id = self.options.get(CONF_IMPORT_ENERGY_SENSOR)
        if not entity_id:
            return
        self._last_energy_total = self._read_float(entity_id)

    def _read_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNUSABLE:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if self.counting_allowance and entity_id == self.options.get(
            CONF_IMPORT_ENERGY_SENSOR
        ):
            self._accumulate_energy(entity_id)
        self.async_refresh()

    def _accumulate_energy(self, entity_id: str) -> None:
        reading = self._read_float(entity_id)
        if reading is None:
            if not self._energy_warned:
                _LOGGER.warning(
                    "Import energy sensor %s is unavailable; the allowance is not "
                    "being counted",
                    entity_id,
                )
                self._energy_warned = True
            return
        if self._energy_warned:
            _LOGGER.info("Import energy sensor %s is available again", entity_id)
            self._energy_warned = False

        resolution = self.state.resolution
        if resolution is not None and resolution.rate.has_allowance:
            self.state.allowance_used_kwh = allowance_module.accumulate(
                self._last_energy_total, reading, self.state.allowance_used_kwh
            )
        self._last_energy_total = reading

    # ------------------------------------------------------- utility  meters

    @callback
    def _async_write_tariff_selects(self) -> None:
        """Set nominated utility_meter tariff selects to the current rate.

        The only write this integration performs, and only to selects the user
        has explicitly nominated.
        """
        selects: list[str] = list(self.options.get(CONF_TARIFF_SELECTS) or [])
        if not selects:
            return
        rate = self.state.effective_rate
        tariff = None if rate is None else rate.qualified_name
        if tariff is None or tariff == self._last_written_tariff:
            return

        for entity_id in selects:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.warning("Tariff select %s does not exist", entity_id)
                continue
            options = state.attributes.get("options") or []
            if tariff not in options:
                _LOGGER.warning(
                    "Tariff select %s has no option '%s'; its options are: %s",
                    entity_id,
                    tariff,
                    ", ".join(options) or "none",
                )
                continue
            if state.state == tariff:
                continue
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": entity_id, "option": tariff},
                    blocking=False,
                )
            )
        self._last_written_tariff = tariff

    # ------------------------------------------------------------------ views

    def export_price_now(self) -> float:
        """Return the feed-in price in force, in dollars per kWh."""
        now = dt_util.now()
        return self.plan.export_price_at(
            now.date(), now.hour * 60 + now.minute, self.is_holiday(now.date())
        )

    def forward_intervals(
        self, hours: int, resolution_minutes: int
    ) -> list[intervals_module.Interval]:
        """Return the forward interval series from now.

        Held between calls. The series is aligned to the resolution grid, so it
        is the same for every moment inside one slot, and the price sensors ask
        for it every time their attributes are read — which is every state
        write, which with an energy meter attached is several times a minute.
        """
        now = dt_util.now()
        today = now.date()
        aligned = (
            (now.hour * 60 + now.minute) // resolution_minutes
        ) * resolution_minutes
        # ``today``/``aligned`` are wall clock, and a wall-clock minute names
        # two real instants an hour apart on the morning the clocks go back.
        # ``now.fold`` tells them apart the same way ``instants_at`` does, so
        # the second pass forces a rebuild instead of being served the first
        # pass's series.
        key = (
            today,
            aligned,
            hours,
            resolution_minutes,
            self.is_holiday(today),
            now.fold,
        )
        if key != self._forward_key:
            self._forward_series = intervals_module.generate(
                self.plan,
                now,
                self.zone,
                self.is_holiday,
                hours=hours,
                resolution_minutes=resolution_minutes,
            )
            self._forward_key = key
        return self._forward_series

    @property
    def problems(self) -> list[str]:
        """Return the plan's validation problems as text."""
        return [str(problem) for problem in validate_plan(self.plan)]

    @property
    def device_identifier(self) -> tuple[str, str]:
        """Return the device registry identifier for this channel."""
        return (DOMAIN, self.entry_id)

    def apply_plan(self, plan: Plan, options: dict[str, Any]) -> None:
        """Replace the plan after the options flow has run."""
        self.plan = plan
        self.options = options
        self._last_written_tariff = None
        self._forward_key = None
        self.async_refresh()


HolidayCheck = Callable[[date], bool]
