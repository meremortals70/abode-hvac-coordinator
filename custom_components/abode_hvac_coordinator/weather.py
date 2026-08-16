"""Weather forecast trajectory.

Pure. No Home Assistant imports.

WHAT THIS CLOSES
----------------
The thermal model learns from what happened. Until this module it never looked
at what was about to happen, and the cost was concentrated in one decision:
precool.

Precool banks thermal mass in a free or cheap window against a load arriving
later. Deciding whether that load exists was done by comparing the **current**
outdoor temperature against indoors. At 11:00 on the day of a 38 C afternoon,
outdoors is often 26 C and indoors is 25 C, so the old test said no demand
ahead and the free window went unused — on precisely the day it was worth the
most.

The forecast answers the question the model could not: not "is it hot now" but
"is it going to be".

WHAT IT DOES NOT DO
-------------------
It does not feed the thermal model's learning. The filter learns from measured
intervals, and folding a forecast into an observation would teach it what the
weather bureau predicted rather than what the room did.

It does not carry irradiance in W/m². No household weather feed publishes it.
Cloud cover and UV index are what is actually available, and they are combined
into a clear-sky fraction that scales solar gain — an approximation, labelled
as one, and far better than assuming every hour of daylight is cloudless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

#: How far ahead precool looks for a load worth banking against. An evening
#: peak seen from a midday free window is roughly this far out; much further
#: and the forecast is not worth trusting to a decision.
DEMAND_LOOKAHEAD = timedelta(hours=10)

#: How much hotter than indoors the forecast peak must be before the load
#: counts as real. Below this the room drifts up slowly enough that holding the
#: band later costs less than banking now.
DEMAND_MARGIN_C = 3.0

#: Cloud cover above which solar gain is treated as fully blocked. Not 100:
#: overcast still passes diffuse radiation, and a room with west glass under
#: heavy cloud is not the same as a room at night.
FULL_CLOUD_PERCENT = 90.0

#: The fraction of clear-sky solar gain that still arrives under full cloud,
#: as diffuse radiation.
OVERCAST_TRANSMISSION = 0.2


class ForecastPayloadError(ValueError):
    """The forecast could not be understood.

    Raised rather than returning None so the reason reaches the log. A forecast
    that cannot be parsed must not take the controller down: precool falls back
    to the current-conditions test and everything else is unaffected.
    """


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """One hour of the forecast, as Home Assistant publishes it.

    Temperature is the only field that is required. A feed without humidity,
    cloud cover or UV still answers the question precool asks, and refusing the
    whole forecast because one optional field is absent would be worse than
    using what is there.
    """

    at: datetime
    temperature_c: float
    humidity: float | None = None
    cloud_coverage: float | None = None
    uv_index: float | None = None
    wind_ms: float | None = None

    @property
    def solar_fraction(self) -> float | None:
        """Clear-sky solar gain reaching the ground, 0.0 to 1.0, or None.

        Cloud cover is the primary term. UV index is used only to distinguish
        night from an overcast day when cloud cover is absent, because a feed
        that publishes UV but not cloud is common and UV at night is zero.
        """
        if self.cloud_coverage is not None:
            cloud = min(max(self.cloud_coverage, 0.0), 100.0)
            if cloud >= FULL_CLOUD_PERCENT:
                return OVERCAST_TRANSMISSION
            clear = 1.0 - (cloud / FULL_CLOUD_PERCENT)
            return OVERCAST_TRANSMISSION + clear * (1.0 - OVERCAST_TRANSMISSION)
        if self.uv_index is not None:
            # Crude, and honest about it: UV index roughly 8 is a clear
            # subtropical noon. Anything at or above that is treated as full.
            return min(max(self.uv_index / 8.0, 0.0), 1.0)
        return None


def _as_datetime(value: Any) -> datetime:
    """Parse the ISO-8601 timestamp Home Assistant puts on each point."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise ForecastPayloadError(f"not a timestamp: {value!r}") from err


def _as_float(value: Any) -> float | None:
    """An optional numeric field, or None where the feed omits it."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def point_from_forecast(raw: dict[str, Any]) -> ForecastPoint:
    """Build one point from a Home Assistant `Forecast` dict.

    Field names follow `homeassistant.components.weather.Forecast` after unit
    conversion: `datetime`, `temperature`, `humidity`, `cloud_coverage`,
    `uv_index`, `wind_speed`. The `native_*` variants are pre-conversion and
    are not read.
    """
    temperature = _as_float(raw.get("temperature"))
    if temperature is None:
        raise ForecastPayloadError("forecast point carries no temperature")
    return ForecastPoint(
        at=_as_datetime(raw.get("datetime")),
        temperature_c=temperature,
        humidity=_as_float(raw.get("humidity")),
        cloud_coverage=_as_float(raw.get("cloud_coverage")),
        uv_index=_as_float(raw.get("uv_index")),
        wind_ms=_as_float(raw.get("wind_speed")),
    )


@dataclass(frozen=True, slots=True)
class DemandVerdict:
    """Whether a load is coming, and the reasoning either way."""

    demand_ahead: bool
    peak_c: float | None
    peak_at: datetime | None
    reason: str


class WeatherTrajectory:
    """The hourly forecast, in order.

    Deliberately thin. It answers three questions — what will it be at a given
    hour, what is the worst it gets over a window, and how much sun is in it —
    and holds no opinions about what to do with the answers.
    """

    def __init__(self, points: tuple[ForecastPoint, ...], fetched_at: datetime) -> None:
        """Hold the points and when they were fetched, so staleness is answerable."""
        self._points = tuple(sorted(points, key=lambda p: p.at))
        self.fetched_at = fetched_at

    @classmethod
    def from_response(
        cls, response: dict[str, Any] | None, fetched_at: datetime
    ) -> WeatherTrajectory:
        """Build from the `weather.get_forecasts` response.

        The service is registered as an entity service, so the response is
        keyed by entity id with the forecast list underneath. Both that shape
        and a bare `{"forecast": [...]}` are accepted, because the second is
        what a test fixture naturally produces and rejecting it would make the
        harness harder to write than the code it tests.
        """
        if not isinstance(response, dict):
            raise ForecastPayloadError(f"response is not a mapping: {type(response)}")

        raw_list: Any = None
        if "forecast" in response:
            raw_list = response["forecast"]
        else:
            for value in response.values():
                if isinstance(value, dict) and "forecast" in value:
                    raw_list = value["forecast"]
                    break

        if not isinstance(raw_list, list):
            raise ForecastPayloadError("response carries no forecast list")
        if not raw_list:
            raise ForecastPayloadError("response carries an empty forecast list")

        points: list[ForecastPoint] = []
        for raw in raw_list:
            if isinstance(raw, dict):
                points.append(point_from_forecast(raw))
        if not points:
            raise ForecastPayloadError("no usable points in the forecast list")
        return cls(tuple(points), fetched_at)

    @property
    def points(self) -> tuple[ForecastPoint, ...]:
        """Every point, earliest first."""
        return self._points

    @property
    def covers_until(self) -> datetime | None:
        """The last hour in the forecast, or None when it is empty."""
        return self._points[-1].at if self._points else None

    def at(self, when: datetime) -> ForecastPoint | None:
        """The forecast point covering an instant.

        The nearest point at or before the instant, so an hourly forecast
        answers for every minute of its hour. None before the first point or
        more than an hour past the last.
        """
        chosen: ForecastPoint | None = None
        for point in self._points:
            if point.at <= when:
                chosen = point
            else:
                break
        if chosen is None:
            return None
        if when - chosen.at > timedelta(hours=1) and chosen is self._points[-1]:
            return None
        return chosen

    def temperature_at(self, when: datetime) -> float | None:
        """Forecast outdoor temperature at an instant."""
        point = self.at(when)
        return None if point is None else point.temperature_c

    def solar_fraction_at(self, when: datetime) -> float | None:
        """Forecast clear-sky fraction at an instant."""
        point = self.at(when)
        return None if point is None else point.solar_fraction

    def peak_between(
        self, start: datetime, end: datetime
    ) -> tuple[float, datetime] | None:
        """The hottest forecast hour in a window, and when it falls."""
        inside = [p for p in self._points if start <= p.at <= end]
        if not inside:
            return None
        hottest = max(inside, key=lambda p: p.temperature_c)
        return hottest.temperature_c, hottest.at

    def mean_between(self, start: datetime, end: datetime) -> float | None:
        """Mean forecast temperature over a window.

        Used by the demand forecast, where what matters is the whole horizon's
        load rather than its worst hour.
        """
        inside = [p.temperature_c for p in self._points if start <= p.at <= end]
        if not inside:
            return None
        return sum(inside) / len(inside)


def demand_ahead(
    trajectory: WeatherTrajectory | None,
    *,
    now: datetime,
    indoor_c: float | None,
    lookahead: timedelta = DEMAND_LOOKAHEAD,
) -> DemandVerdict:
    """Whether a cooling load is coming that is worth banking against.

    This is the precool gate, and it is the whole reason this module exists.
    The question is not whether it is hot now — at 11:00 in the free window it
    usually is not — but whether the afternoon is going to arrive.

    With no forecast the caller falls back to comparing current conditions,
    which is what the controller did before and is stated as such rather than
    silently returning False.
    """
    if trajectory is None:
        return DemandVerdict(
            demand_ahead=False,
            peak_c=None,
            peak_at=None,
            reason="no weather forecast configured, falling back to current conditions",
        )

    if indoor_c is None:
        return DemandVerdict(
            demand_ahead=False,
            peak_c=None,
            peak_at=None,
            reason="precool: no indoor reading to compare the forecast against",
        )

    peak = trajectory.peak_between(now, now + lookahead)
    if peak is None:
        return DemandVerdict(
            demand_ahead=False,
            peak_c=None,
            peak_at=None,
            reason=(
                "precool: the forecast does not reach far enough ahead to see "
                "a load"
            ),
        )

    peak_c, peak_at = peak
    if peak_c >= indoor_c + DEMAND_MARGIN_C:
        return DemandVerdict(
            demand_ahead=True,
            peak_c=peak_c,
            peak_at=peak_at,
            reason=(
                f"precool: {peak_c:.1f} C forecast at {peak_at.strftime('%H:%M')} "
                f"against {indoor_c:.1f} C indoors — a load worth banking against"
            ),
        )

    return DemandVerdict(
        demand_ahead=False,
        peak_c=peak_c,
        peak_at=peak_at,
        reason=(
            f"precool: the day peaks at {peak_c:.1f} C against {indoor_c:.1f} C "
            "indoors, not enough of a load to bank against"
        ),
    )
