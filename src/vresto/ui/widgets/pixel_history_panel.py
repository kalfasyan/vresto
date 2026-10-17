"""Pixel history panel: a time series of one point, with a slider that streams each date onto the map.

The user turns on *Pick a point* and clicks the map. The panel then charts the point over
the chosen period: Sentinel-2 NDVI by default (anonymous, from AWS Open Data), or one of
the continuous overlay products (CDSE S3 keys needed). Cloudy, shadowed and snowy
Sentinel-2 readings are drawn apart so the clear series stays readable.

The slider (or a click on a chart point) selects a date. For Sentinel-2 that date's scene
is handed to ``on_show_scene``, which streams its true-colour image onto the map, so a dip
in the curve can be checked against the actual image.

The chart and summary are built by pure functions (:func:`build_chart_options`,
:func:`summarize`) so they can be tested without a browser.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Optional, Sequence

from loguru import logger
from nicegui import ui

from vresto.api.config import CopernicusConfig

NDVI_KEY = "ndvi"
# Overlay products whose service supports ``point_series`` (continuous values, not hourly).
OVERLAY_SERIES = ("fapar", "dmp", "swi", "ssm")

# Period choices: label -> days back from today, or None for the date picker's range.
PERIOD_OPTIONS: dict[str, Optional[int]] = {
    "Selected dates": None,
    "Last 6 months": 182,
    "Last 12 months": 365,
    "Last 3 years": 1095,
}
DEFAULT_PERIOD = "Last 12 months"
# Scenes read for a Sentinel-2 series: one tile has up to ~1 scene every 2-3 days, so long periods
# need more than the service default. The cap bounds a 3-year history to about a minute of reads.
MAX_SCENES_PER_DAY = 0.5
MAX_SCENES_CAP = 500

CLEAR_COLOR = "#2e7d32"
FLAGGED_COLOR = "#9ca3af"
SELECTED_COLOR = "#f59e0b"

# Point statuses drawn as "flagged" (grey) rather than on the clear line.
FLAGGED_STATUSES = ("cloud", "shadow", "snow")

PICK_HINT = "Turn on Pick a point, then click the map."


@dataclass(frozen=True)
class HistoryVariable:
    """A quantity the panel can chart."""

    key: str
    label: str
    units: str
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    needs_cdse_s3: bool = False


@dataclass(frozen=True)
class HistoryPoint:
    """One dated value on the chart.

    ``scene`` is the Sentinel-2 :class:`~vresto.services.pixel_history.PixelObservation`
    the value came from; None for overlay products, which have no scene to stream.
    """

    when: datetime
    value: Optional[float]
    status: str
    scene: Any = None

    @property
    def label(self) -> str:
        return self.when.strftime("%Y-%m-%d")


def history_variables() -> list[HistoryVariable]:
    """NDVI first, then the overlay products that support point series, in registry order."""
    from vresto.ui.overlays import OVERLAY_REGISTRY

    variables = [HistoryVariable(NDVI_KEY, "NDVI · Sentinel-2 (no account needed)", "NDVI", -0.2, 1.0)]
    for spec in OVERLAY_REGISTRY:
        if spec.name in OVERLAY_SERIES:
            variables.append(HistoryVariable(spec.name, f"{spec.label} · CDSE (S3 keys)", spec.units, spec.vmin, spec.vmax, needs_cdse_s3=True))
    return variables


def period_dates(period: str, selected_range: tuple[str, str], today: Optional[date] = None) -> tuple[str, str]:
    """Resolve a period label into ``(start, end)`` as ``YYYY-MM-DD``."""
    days = PERIOD_OPTIONS.get(period)
    if days is None:
        return selected_range
    today = today or date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def max_scenes_for(start: str, end: str) -> int:
    """Scenes to read for a Sentinel-2 series over a period: the service default, more for long periods."""
    from vresto.services.pixel_history import DEFAULT_MAX_SCENES

    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return DEFAULT_MAX_SCENES
    return max(DEFAULT_MAX_SCENES, min(MAX_SCENES_CAP, int(days * MAX_SCENES_PER_DAY)))


def points_from_observations(observations: Sequence[Any]) -> list[HistoryPoint]:
    """Chart points from Sentinel-2 pixel observations (NDVI)."""
    return [HistoryPoint(obs.datetime, obs.ndvi, obs.status, obs) for obs in observations]


def points_from_series(series: Sequence[Any]) -> list[HistoryPoint]:
    """Chart points from an overlay product's ``point_series``."""
    return [HistoryPoint(point.datetime, point.value, "clear" if point.value is not None else "nodata") for point in series]


def summarize(points: Sequence[HistoryPoint], variable: HistoryVariable) -> str:
    """One-line summary of a series: how many dates, how many usable, and the value range."""
    if not points:
        return "No data found for this point and period."
    usable = [p.value for p in points if p.status == "clear" and p.value is not None]
    noun = "scene" if variable.key == NDVI_KEY else "date"
    parts = [f"{len(points)} {noun}{'s' if len(points) != 1 else ''}", f"{len(usable)} clear" if variable.key == NDVI_KEY else f"{len(usable)} with data"]
    if usable:
        parts.append(f"{variable.units} {_fmt(min(usable))} – {_fmt(max(usable))}")
    return " · ".join(parts)


def build_chart_options(points: Sequence[HistoryPoint], variable: HistoryVariable, selected: Optional[int] = None) -> dict:
    """ECharts options: the clear series as a line, flagged readings as grey dots, the selected date marked.

    Each data item is ``[timestamp_ms, value, index]`` so a click can be mapped back to its point.
    """
    clear, flagged = [], []
    for index, point in enumerate(points):
        if point.value is None:
            continue
        item = [int(point.when.timestamp() * 1000), round(point.value, 4), index]
        (flagged if point.status in FLAGGED_STATUSES else clear).append(item)

    clear_series: dict[str, Any] = {
        "name": "Clear",
        "type": "line",
        "data": clear,
        "symbolSize": 6,
        "itemStyle": {"color": CLEAR_COLOR},
        "lineStyle": {"width": 1.5},
    }
    if selected is not None and 0 <= selected < len(points):
        clear_series["markLine"] = {
            "silent": True,
            "symbol": "none",
            "label": {"show": False},
            "lineStyle": {"color": SELECTED_COLOR, "width": 2, "type": "solid"},
            "data": [{"xAxis": int(points[selected].when.timestamp() * 1000)}],
        }

    y_axis: dict[str, Any] = {"type": "value", "name": variable.units, "nameTextStyle": {"fontSize": 10}, "axisLabel": {"fontSize": 10}, "scale": True}
    if variable.vmin is not None:
        y_axis["min"] = variable.vmin
    if variable.vmax is not None:
        y_axis["max"] = variable.vmax

    return {
        "animation": False,
        "grid": {"left": 42, "right": 12, "top": 28, "bottom": 24},
        "tooltip": {"trigger": "item"},
        "legend": {"data": ["Clear", "Cloud / shadow / snow"], "top": 0, "textStyle": {"fontSize": 10}, "itemHeight": 8},
        "xAxis": {"type": "time", "axisLabel": {"fontSize": 10}},
        "yAxis": y_axis,
        "series": [
            clear_series,
            {"name": "Cloud / shadow / snow", "type": "scatter", "data": flagged, "symbolSize": 5, "itemStyle": {"color": FLAGGED_COLOR}},
        ],
    }


def series_to_csv(points: Sequence[HistoryPoint], variable: HistoryVariable) -> str:
    """CSV of the charted series. Sentinel-2 series keep every band and flag."""
    if variable.key == NDVI_KEY:
        from vresto.services.pixel_history import observations_to_csv

        return observations_to_csv([p.scene for p in points])
    lines = [f"date,datetime_utc,{variable.key}"]
    for p in points:
        value = "" if p.value is None else f"{p.value:.4f}"
        lines.append(f"{p.label},{p.when.strftime('%Y-%m-%dT%H:%M:%SZ')},{value}")
    return "\n".join(lines) + "\n"


def _fmt(value: float) -> str:
    return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"


class PixelHistoryPanel:
    """Sidebar card that charts a point over time and steps through its dates.

    Args:
        get_date_range: Returns the date picker's ``(from, to)`` range.
        get_tile_code: Returns the streamed MGRS tile, if any; its scenes are preferred so
            the slider keeps showing the same tile.
        on_pick_mode: Called with True/False when *Pick a point* is switched.
        on_show_scene: Awaited with a Sentinel-2 observation to stream its scene onto the map.
        on_point_loaded: Called with ``(lat, lon, summary)`` once a series has loaded.
    """

    def __init__(
        self,
        get_date_range: Callable[[], tuple[str, str]],
        get_tile_code: Callable[[], Optional[str]],
        on_pick_mode: Callable[[bool], None],
        on_show_scene: Callable[[Any], Awaitable[Any]],
        on_point_loaded: Optional[Callable[[float, float, str], None]] = None,
    ):
        self._get_date_range = get_date_range
        self._get_tile_code = get_tile_code
        self._on_pick_mode = on_pick_mode
        self._on_show_scene = on_show_scene
        self._on_point_loaded = on_point_loaded or (lambda lat, lon, summary: None)

        self.variables = {v.key: v for v in history_variables()}
        self.variable = self.variables[NDVI_KEY]
        self.period = DEFAULT_PERIOD
        self.picking = False
        self.points: list[HistoryPoint] = []
        self.selected: Optional[int] = None
        self.location: Optional[tuple[float, float]] = None

        # A newer click (or variable change) bumps the id; a slower load that finishes later is dropped.
        self._request_id = 0
        # Scene streaming is serialised: while one streams, only the newest pending pick is kept.
        self._pending_scene: Any = None
        self._streaming = False

        self._pick_switch: Any = None
        self._status: Any = None
        self._spinner: Any = None
        self._chart: Any = None
        self._slider: Any = None
        self._date_label: Any = None
        self._details: Any = None
        self._export_button: Any = None
        self._follow_switch: Any = None
        self._body: Any = None
        self._collapse_button: Any = None

    # ── Layout ──────────────────────────────────────────────────────────────

    def create(self, classes: str = "w-full p-3") -> Any:
        """Build the card. Returns the card element.

        Args:
            classes: Tailwind classes of the card, e.g. to float it over the map.
        """
        with ui.card().classes(classes) as card:
            with ui.column().classes("w-full gap-2"):
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    ui.label("📈 Pixel history").classes("text-sm font-semibold")
                    self._pick_switch = ui.switch("Pick a point", value=False, on_change=lambda e: self.set_picking(bool(e.value)))
                    self._pick_switch.classes("text-xs")
                    self._pick_switch.tooltip("While on, clicking the map charts that point over time instead of selecting tiles.")
                    self._spinner = ui.spinner(size="sm")
                    self._spinner.set_visibility(False)
                    self._collapse_button = ui.button(icon="expand_more", on_click=self._toggle_body).props("flat round dense size=sm")
                    self._collapse_button.tooltip("Hide or show the chart")

                with ui.column().classes("w-full gap-2") as body:
                    with ui.row().classes("w-full gap-2 no-wrap"):
                        ui.select(
                            options={key: v.label for key, v in self.variables.items()},
                            value=self.variable.key,
                            label="Variable",
                            on_change=self._on_variable_change,
                        ).props("dense outlined").classes("flex-1 text-xs")
                        ui.select(options=list(PERIOD_OPTIONS), value=self.period, label="Period", on_change=self._on_period_change).props("dense outlined").classes("w-36 text-xs")

                    self._status = ui.label(PICK_HINT).classes("text-xs text-gray-500")

                    self._chart = ui.echart(build_chart_options([], self.variable), on_point_click=self._on_chart_click).classes("w-full h-44")
                    self._chart.set_visibility(False)

                    self._slider = ui.slider(min=0, max=1, step=1, value=0, on_change=self._on_slider_move).props("dense")
                    self._slider.on("change", self._on_slider_release)
                    self._slider.set_visibility(False)
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        self._date_label = ui.label("").classes("text-xs font-medium")
                        self._details = ui.label("").classes("text-[11px] text-gray-500")

                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        self._follow_switch = ui.switch("Show date on map", value=True).classes("text-xs")
                        self._follow_switch.tooltip("Stream the selected date's true-colour image when you release the slider (Sentinel-2 only).")
                        self._follow_switch.set_visibility(False)
                        self._export_button = ui.button("CSV", icon="download", on_click=self.export_csv).props("flat dense size=sm")
                        self._export_button.tooltip("Download the series as CSV")
                        self._export_button.set_visibility(False)
                self._body = body
        return card

    def _toggle_body(self) -> None:
        """Collapse the panel to its header row, or expand it again."""
        if self._body is None:
            return
        expanded = not self._body.visible
        self._body.set_visibility(expanded)
        if self._collapse_button is not None:
            self._collapse_button.props(f"icon={'expand_more' if expanded else 'expand_less'}")

    # ── Pick mode ───────────────────────────────────────────────────────────

    def set_picking(self, enabled: bool) -> None:
        """Turn point picking on or off and tell the map tab."""
        if enabled == self.picking:
            return
        self.picking = enabled
        if self._pick_switch is not None and bool(self._pick_switch.value) != enabled:
            self._pick_switch.set_value(enabled)
        self._on_pick_mode(enabled)

    # ── Loading ─────────────────────────────────────────────────────────────

    async def load(self, lat: float, lon: float) -> None:
        """Fetch and chart the series of a point with the current variable and period."""
        self.location = (lat, lon)
        self._request_id += 1
        request_id = self._request_id
        variable = self.variable

        if variable.needs_cdse_s3 and not CopernicusConfig().has_static_s3_credentials():
            self._set_status(f"{variable.label.split(' · ')[0]} needs CDSE S3 keys (☰ → Settings). NDVI works without them.")
            return

        start, end = period_dates(self.period, self._get_date_range())
        self._set_status(f"Loading {variable.units} at {lat:.4f}, {lon:.4f} ({start} → {end})…")
        self._set_loading(True)
        try:
            points = await asyncio.to_thread(self._fetch, variable, lat, lon, start, end)
        except Exception as exc:
            logger.exception(f"Pixel history failed at ({lat:.5f}, {lon:.5f})")
            points = []
            failure = str(exc)
        else:
            failure = ""
        finally:
            if request_id == self._request_id:
                self._set_loading(False)

        if request_id != self._request_id:
            return  # superseded by a newer click or setting change
        self.points = points
        summary = f"Could not load the series: {failure}" if failure else summarize(points, variable)
        self._set_status(summary)
        self._render(select_last_clear=True)
        self._on_point_loaded(lat, lon, summary)

    def _fetch(self, variable: HistoryVariable, lat: float, lon: float, start: str, end: str) -> list[HistoryPoint]:
        """Blocking fetch, run in a worker thread."""
        if variable.key == NDVI_KEY:
            from vresto.services.pixel_history import point_history

            tile = self._get_tile_code()
            max_scenes = max_scenes_for(start, end)
            observations = point_history(lat, lon, start, end, tile_code=tile, max_scenes=max_scenes) if tile else []
            if not observations:  # no streamed tile, or the point lies outside it
                observations = point_history(lat, lon, start, end, max_scenes=max_scenes)
            return points_from_observations(observations)

        import importlib

        service = getattr(importlib.import_module(f"vresto.services.{variable.key}"), f"{variable.key}_service")
        return points_from_series(service.point_series(lat, lon, start, end))

    async def _reload(self) -> None:
        if self.location is not None:
            await self.load(*self.location)

    async def _on_variable_change(self, e) -> None:
        self.variable = self.variables.get(str(e.value), self.variables[NDVI_KEY])
        await self._reload()

    async def _on_period_change(self, e) -> None:
        self.period = str(e.value) if e.value in PERIOD_OPTIONS else DEFAULT_PERIOD
        await self._reload()

    # ── Selection ───────────────────────────────────────────────────────────

    def select(self, index: int) -> None:
        """Mark a date as selected: chart marker, slider position and date label."""
        if not 0 <= index < len(self.points):
            return
        self.selected = index
        point = self.points[index]
        if self._slider is not None and self._slider.value != index:
            self._slider.set_value(index)
        if self._chart is not None:
            self._chart.options.clear()
            self._chart.options.update(build_chart_options(self.points, self.variable, index))
            self._chart.update()
        if self._date_label is not None:
            self._date_label.set_text(point.label)
        if self._details is not None:
            value = "no value" if point.value is None else f"{self.variable.units} {_fmt(point.value)}"
            self._details.set_text(f"{value} · {point.status}")

    def _on_slider_move(self, e) -> None:
        if e.value is not None:
            self.select(int(e.value))

    async def _on_slider_release(self, _e=None) -> None:
        await self._show_selected_scene()

    async def _on_chart_click(self, e) -> None:
        data = getattr(e, "data", None)
        if isinstance(data, (list, tuple)) and len(data) >= 3:
            self.select(int(data[2]))
            await self._show_selected_scene()

    async def _show_selected_scene(self) -> None:
        """Stream the selected Sentinel-2 scene, keeping only the newest request while one streams."""
        if self.selected is None or not (self._follow_switch is None or self._follow_switch.value):
            return
        scene = self.points[self.selected].scene
        if scene is None or getattr(scene, "product", None) is None:
            return
        self._pending_scene = scene
        if self._streaming:
            return
        self._streaming = True
        try:
            while self._pending_scene is not None:
                scene, self._pending_scene = self._pending_scene, None
                await self._on_show_scene(scene)
        finally:
            self._streaming = False

    # ── Output ──────────────────────────────────────────────────────────────

    def export_csv(self) -> None:
        """Download the charted series as CSV."""
        if not self.points or self.location is None:
            return
        lat, lon = self.location
        filename = f"vresto_{self.variable.key}_{lat:.4f}_{lon:.4f}.csv"
        ui.download.content(series_to_csv(self.points, self.variable), filename, "text/csv")

    def _render(self, select_last_clear: bool = False) -> None:
        has_points = bool(self.points)
        for element in (self._chart, self._slider, self._export_button):
            if element is not None:
                element.set_visibility(has_points)
        if self._follow_switch is not None:
            self._follow_switch.set_visibility(has_points and self.variable.key == NDVI_KEY)
        if not has_points:
            self.selected = None
            for label in (self._date_label, self._details):
                if label is not None:
                    label.set_text("")
            return
        if self._slider is not None:
            self._slider._props["max"] = max(1, len(self.points) - 1)
            self._slider.update()
        clear = [i for i, p in enumerate(self.points) if p.status == "clear" and p.value is not None]
        self.select(clear[-1] if (select_last_clear and clear) else len(self.points) - 1)

    def _set_status(self, text: str) -> None:
        if self._status is not None:
            self._status.set_text(text)

    def _set_loading(self, loading: bool) -> None:
        if self._spinner is not None:
            self._spinner.set_visibility(loading)
