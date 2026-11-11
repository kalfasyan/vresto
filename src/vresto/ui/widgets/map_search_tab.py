"""Map search tab widget combining map, date picker, and search controls."""

import asyncio
import json
import re
import time
from datetime import date as _date
from datetime import timedelta, timezone
from html import escape as html_escape
from typing import Any, Callable, Optional

from loguru import logger
from nicegui import background_tasks, ui

from vresto.api import BoundingBox, CatalogSearch, ProductInfo
from vresto.api.auth import get_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
    get_product_capabilities,
    supports_band_download,
)
from vresto.api.stac_assets import parse_date_like
from vresto.services import geocode, public_s2
from vresto.services.mgrs_grid import MIN_ZOOM_FOR_GRID, compute_visible_tiles_geojson, compute_visible_zones_geojson
from vresto.services.mgrs_grid import is_available as mgrs_available
from vresto.services.sentinel_stream import (
    DEFAULT_TCI_RESOLUTION,
    TciResolution,
    sentinel_stream_service,
)
from vresto.services.tile_density import (
    INDEX_SOURCE,
    RATE_LIMIT_COOLDOWN_S,
    STATS_SOURCE,
    TileDensity,
    count_products_per_tile,
    count_products_per_zone,
    count_whole_months,
    rate_limited,
)
from vresto.services.tiles import tile_pool
from vresto.ui.footprints import RESULT_SORTS, footprints_feature_collection, sort_products
from vresto.ui.overlays import (
    OVERLAY_NAMES,
    OVERLAY_REGISTRY,
    FetchedOverlay,
    OverlayRequest,
    OverlaySpec,
    build_overlay_legend_html,
    build_point_popup_html,
)
from vresto.ui.url_state import SharedView
from vresto.ui.widgets.activity_log import ActivityLogWidget
from vresto.ui.widgets.credentials_menu import STREAM_CREDENTIALS_HINT
from vresto.ui.widgets.date_picker import DatePickerWidget
from vresto.ui.widgets.map_widget import GRID_DENSITY_STYLE, GRID_EMPTY_STYLE, MapWidget, grid_density_legend
from vresto.ui.widgets.pixel_history_panel import PixelHistoryPanel
from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget

# Value of the overlay picker when no overlay is chosen.
OVERLAY_NONE = ""

# Maximum number of latest products surfaced in the tile-click chooser dialog.
TILE_PRODUCT_CHOICES = 5

# Seconds to wait after the last map move or date change before counting products per tile.
DENSITY_DEBOUNCE_S = 0.5
# After the whole-month colours, wait this much longer for the map to settle before the exact
# counts, which take seconds, so panning across a region does not queue one exact count per stop.
EXACT_COUNT_DELAY_S = 1.0

# Notes shown under the product-count legend.
DENSITY_PARTIAL_TEXT = "Partial count: zoom in or narrow the dates for exact numbers."
DENSITY_RATE_LIMITED_TEXT = "The catalogue is busy (rate limit); tile colours will return in about a minute."

# Point inspector popup messages for a click that has no value to show.
INSPECT_OUTSIDE_TEXT = "Outside the streamed tile"
INSPECT_NODATA_TEXT = "No data at this point"
INSPECT_ERROR_TEXT = "Could not read the value at this point"


def _history_popup_html(lat: float, lon: float, text: str) -> str:
    """Popup marking a pixel-history point."""
    return f'<div style="font-size:12px;line-height:1.35"><b>📈 Pixel history</b><br>{html_escape(text)}<br><span style="color:#6b7280">{lat:.5f}, {lon:.5f}</span></div>'


def _scene_keys(product: ProductInfo) -> tuple[str, str]:
    """Return a product's sensing day (``YYYYMMDD``, the TCI cache key) and its timestamp (``YYYYMMDDhhmmss``)."""
    digits = "".join(ch for ch in str(product.sensing_date) if ch.isdigit())
    return digits[:8], digits[:14] or digits[:8]


def _pretty_day(day: str) -> str:
    """``20250612`` -> ``2025-06-12``."""
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day


def _density_caption_text(density: TileDensity) -> str:
    """Say where the tile counts came from and which period they cover."""
    if density.source == INDEX_SOURCE:
        return "Selected dates, Earth Search index on AWS Open Data."
    if density.source == STATS_SOURCE:
        return f"Whole months ({density.period}), Earth Search index on AWS Open Data."
    return "Selected dates, CDSE catalogue."


class MapSearchTab:
    """Encapsulates the Map Search tab with date picker, interactive map, and search controls.

    Usage:
        tab_widget = MapSearchTab(
            on_quicklook=lambda p, col: _show_quicklook(p, col),
            on_metadata=lambda p, col: _show_metadata(p, col),
        )
        tab_content = tab_widget.create()
    """

    def __init__(
        self,
        on_quicklook: Optional[Callable] = None,
        on_metadata: Optional[Callable] = None,
        on_download: Optional[Callable] = None,
        pool_namespace: str = "",
        shared_view: Optional[SharedView] = None,
    ):
        """Initialize the Map Search tab.

        Args:
            on_quicklook: Callback(product, messages_column) for quicklook requests
            on_metadata: Callback(product, messages_column) for metadata requests
            on_download: Callback(product) that opens the product in the Download tab; when omitted, no Download button is shown
            pool_namespace: Prefix for this tab's entries in the shared tile pool, so visitors of a shared site don't remove each other's layers
            shared_view: View opened from a link (map position, dates, tile, overlay) to restore
        """
        self._shared_view = shared_view or SharedView()
        self.pool_namespace = pool_namespace
        self.on_quicklook = on_quicklook or (lambda p, col: None)
        self.on_metadata = on_metadata or (lambda p, col: None)
        self.on_download = on_download

        # Default date range: rolling last 30 days.
        _today = _date.today()
        _default_to = _today.strftime("%Y-%m-%d")
        _default_from = (_today - timedelta(days=30)).strftime("%Y-%m-%d")

        if self._shared_view.date_from and self._shared_view.date_to:
            _default_from, _default_to = self._shared_view.date_from, self._shared_view.date_to

        # State
        self.current_state = {
            "bbox": None,
            "date_range": {"from": _default_from, "to": _default_to},
            "products": [],
        }
        # Search results: how they are ordered, and how many the catalogue returned before the level filter.
        self._result_sort = "newest"
        self._results_total = 0
        # Before/after comparison: the map layer of the second date, and that date.
        self._compare_layer: Optional[str] = None
        self._compare_date: Optional[str] = None
        self._compare_button: Any = None
        self._compare_stop_button: Any = None
        # Overlay picked (or opened from a link) before any tile was streamed; it loads on the first tile.
        self._pending_overlay: Optional[str] = self._shared_view.overlay

        # UI elements
        self.messages_column = None
        self.map_widget = None
        self.map_widget_obj = None
        self.results_display = None
        self.date_picker = None

        # Grid & streaming state
        self._grid_enabled = True
        # Product-count colouring of the grid. Every density request bumps the generation, and only
        # the newest request may colour the map (see _refresh_density).
        self._density_enabled = True
        self._density_generation = 0
        self._density_truncated = False
        self._density_rate_limited = False
        self._grid_codes: list[str] = []  # MGRS codes of the tiles (or grid zones) drawn on the map
        self._grid_zones = False  # True while the world view draws grid zones instead of 100 km tiles
        self._grid_bbox: Optional[tuple] = None  # bbox of the grid drawn on the map; None while hidden
        self._density_switch: Any = None
        self._density_legend: Any = None
        self._density_note: Any = None
        self._density_caption: Any = None
        # Says where tile previews come from while CDSE S3 keys are not set. None until the controls are built.
        self._stream_source_label: Any = None
        self._streaming_tile_code: Optional[str] = None
        self._streaming_date: Optional[str] = None
        self._streaming_timestamp: Optional[str] = None
        self._active_overlay: Optional[str] = None
        self._suppress_overlay_events = False
        self._tcd_year = "2020"
        self._tcd_year_select = None
        self._lc100_year = "2019"
        self._lc100_year_select = None
        self._lst_time_select = None
        self._lst_time_options_by_label = {}
        self._lst_user_selected_timestamp: Optional[str] = None
        self._lst_selected_timestamp_label: Optional[str] = None

        # Legend title suffix (year or snapped product date) reported by each
        # overlay's fetcher on its last successful load.
        self._overlay_legend_suffixes: dict[str, str] = {}
        # Overlays whose coverage the streamed tile falls outside of; absent means available.
        self._overlay_tile_available: dict[str, bool] = {}
        # Last successful fetch per overlay; its aligned raster is what the point inspector reads.
        self._overlay_fetched: dict[str, FetchedOverlay] = {}

        # Point inspector. The request counter lets a slow sample notice it was superseded
        # (by a newer click, or by the inspector being turned off or reset) and drop its popup.
        self._inspect_switch: Any = None
        self._inspect_enabled = False
        self._inspect_request_id = 0

        # Pixel history panel. It shares the map's click mode with the inspector, one at a time.
        self._pixel_history: Optional[PixelHistoryPanel] = None
        # Map position of the last pixel-history click, as clicked (longitude not wrapped).
        self._history_click: Optional[tuple[float, float]] = None
        # Whether showing a streamed tile zooms the map to it. Stepping through pixel-history
        # dates turns it off, so the map keeps the user's view on the point.
        self._fit_streamed_tile = True

        # Per-overlay booleans are derived from the registry so adding a new
        # overlay does not require touching this block.
        for name in OVERLAY_NAMES:
            setattr(self, f"_{name}_enabled", False)

        self._overlay_layer_urls: dict[str, str] = {}
        self._overlay_switches: dict[str, Any] = {}
        self._overlay_sliders: dict[str, Any] = {}
        self._overlay_section_rows: dict[str, Any] = {}  # settings of each overlay; only the picked one is visible
        self._overlay_select: Any = None

        self._overlay_opacity_by_name = {spec.name: spec.opacity for spec in OVERLAY_REGISTRY}
        self._overlay_titles = {spec.name: spec.title for spec in OVERLAY_REGISTRY}
        self._overlay_info_by_name = {spec.name: spec.info for spec in OVERLAY_REGISTRY}
        self._overlay_specs: dict[str, OverlaySpec] = {spec.name: spec for spec in OVERLAY_REGISTRY}
        # Phase 1: active-overlay status chip and how-to stepper references
        self._active_overlay_chip: Any = None
        self._how_to_card: Any = None
        # Phase 3: per-overlay coverage-badge labels
        self._overlay_coverage_labels: dict[str, Any] = {}
        # Default to the fastest L2A TCI resolution (60 m ≈ 1830² px). The
        # user can opt into 10 m via the sidebar switch when they need more
        # detail — it's ~36× more data to decode.
        self._tci_resolution: TciResolution = DEFAULT_TCI_RESOLUTION

    def create(self):
        """Create and return the Map Search tab UI."""
        with ui.row().classes("w-full gap-4 no-wrap items-start"):
            # Left sidebar: Date picker and activity log
            self._create_sidebar()

            # Map with draw controls and grid/streaming callbacks. The pixel-history panel floats over the
            # map's top-left, beside the zoom and draw tools, so the chart and the map stay in view together.
            with ui.element("div").classes("relative flex-1 min-w-0"):
                map_widget_obj = MapWidget(
                    center=self._shared_view.center or (50.8503, 4.3517),
                    zoom=self._shared_view.zoom or 7,
                    title="",
                    on_bbox_update=lambda bbox: self.current_state.update({"bbox": bbox}),
                    on_tile_click=self._handle_tile_click,
                    on_moveend=self._handle_moveend,
                    on_map_click=self._handle_map_click,
                )
                self.map_widget_obj = map_widget_obj
                self.map_widget = map_widget_obj.create(self.messages_column)

                self._pixel_history = PixelHistoryPanel(
                    get_date_range=lambda: (self.current_state["date_range"]["from"], self.current_state["date_range"]["to"]),
                    get_tile_code=lambda: self._streaming_tile_code,
                    on_pick_mode=self._on_pixel_history_mode,
                    on_show_scene=self._show_history_scene,
                    on_point_loaded=self._on_history_loaded,
                )
                self._pixel_history.create(classes="absolute left-14 top-[4.5rem] z-[1001] w-[420px] max-w-[calc(100%-4.5rem)] p-3 shadow-lg")

            # Wire moveend for grid refresh after map is created
            map_widget_obj.setup_moveend()

            # Phase 4: add scale bar, coordinate readout, and basemap switcher.
            map_widget_obj.add_map_chrome()

            # A link that names a tile streams it (and its overlay) once the page is up.
            if self._shared_view.tile:
                ui.timer(0.5, self._restore_shared_view, once=True)

            # Right sidebar: Search controls and results
            search_panel = SearchResultsPanelWidget()
            self.results_display, trigger_search = search_panel.create(
                messages_column=self.messages_column,
                on_search=self._handle_search,
            )

        return {
            "messages_column": self.messages_column,
            "map": self.map_widget,
            "results": self.results_display,
            "state": self.current_state,
        }

    def _create_sidebar(self):
        """Create the left sidebar with date picker, grid toggle, overlay controls, and activity log."""
        # Scrolls on its own, so the map beside it stays in view.
        with ui.column().classes("w-80 shrink-0 vresto-sidebar"):
            self._create_place_search()

            # Date picker with callback for date range updates
            date_range = self.current_state["date_range"]
            picker_widget = DatePickerWidget(
                default_from=date_range["from"],
                default_to=date_range["to"],
                on_date_change=self._on_date_change,
            )
            self.date_picker, date_display = picker_widget.create()

            # MGRS Grid & Streaming controls (gated behind credentials)
            self._create_streaming_controls()

            # Activity log
            activity_log = ActivityLogWidget(title="Activity Log")
            self.messages_column = activity_log.create()

        # Setup date monitoring with the now-existing messages_column
        picker_widget.setup_monitoring(self.date_picker, date_display, self.messages_column)

    def _create_place_search(self) -> None:
        """Add a box that moves the map to a place name or a ``lat, lon`` pair."""

        async def _go(_=None) -> None:
            query = (place_input.value or "").strip()
            if not query:
                return
            place_input.props(add="loading")
            try:
                place = await asyncio.to_thread(geocode.find_place, query)
            except Exception as e:
                logger.warning(f"Place search failed for {query!r}: {e}")
                ui.notify("Place search is unavailable right now. Try again, or type coordinates as 'lat, lon'.", position="top", type="warning")
                return
            finally:
                place_input.props(remove="loading")
            if place is None:
                ui.notify(f"No place found for '{query}'", position="top", type="warning")
                return
            if self.map_widget_obj:
                self.map_widget_obj.fit_bounds(place.bounds)
            if self.messages_column:
                with self.messages_column:
                    ui.label(f"📍 Moved to {place.name}").classes("text-sm text-gray-700 break-words")

        place_input = ui.input(placeholder="Go to a place or 'lat, lon'…").props("dense outlined clearable").classes("w-full").on("keydown.enter", _go)
        with place_input.add_slot("append"):
            ui.button(icon="search", on_click=_go).props("flat round dense size=sm").tooltip("Go to place")
        self._place_input = place_input

    def _on_date_change(self, start_date: str, end_date: str):
        """Handle date range changes from the date picker.

        The picker reports the range on every poll, so only a real change recounts the tiles.
        """
        new_range = {"from": start_date, "to": end_date}
        if new_range == self.current_state["date_range"]:
            return
        self.current_state["date_range"] = new_range
        self._push_url(**{"from": start_date, "to": end_date})
        if self._density_enabled and self._grid_bbox is not None and self.map_widget_obj:
            # The colours belong to the previous dates: show plain tiles until the new counts arrive.
            self._forget_density()
            self.map_widget_obj.clear_grid_density()
            self._schedule_density_refresh()

    # ------------------------------------------------------------------
    # Grid & Streaming controls
    # ------------------------------------------------------------------

    def _create_streaming_controls(self):
        """Create the MGRS grid toggle and overlay controls section.

        The controls are shown with or without CDSE S3 keys. Without them, tiles stream from AWS Open Data.
        """
        with ui.card().classes("w-full p-3 mt-2"):
            with ui.column().classes("w-full gap-2"):
                with ui.row().classes("w-full items-start justify-between"):
                    ui.label("🛰️ Tile Streaming").classes("text-sm font-semibold")
                    self._tile_status_label = ui.label("No tile selected").classes("text-xs text-gray-500")

                self._stream_source_label = ui.label("").classes("text-xs text-gray-500 italic")
                self._sync_stream_source_note()

                if not mgrs_available():
                    ui.label("mgrs package not installed").classes("text-xs text-red-500 italic")
                    return

                # Phase 1 – "How it works" stepper shown until first tile is streamed.
                with ui.card().classes("w-full p-2 bg-blue-50 dark:bg-slate-700 rounded-md") as how_to_card:
                    ui.label("How it works").classes("text-[11px] font-semibold text-blue-700 dark:text-blue-300 mb-1")
                    steps = [
                        ("1", "Set a date range above"),
                        ("2", "Zoom in — MGRS tiles appear at zoom ≥ 5"),
                        ("3", "Click a tile to stream its TCI"),
                        ("4", "Pick an overlay below, or compare two dates"),
                    ]
                    for num, text in steps:
                        with ui.row().classes("items-center gap-1 mb-0.5"):
                            ui.badge(num).props("color=blue").classes("text-[10px] min-w-[16px]")
                            ui.label(text).classes("text-[11px] text-blue-800 dark:text-blue-200")
                self._how_to_card = how_to_card

                ui.label("Base layer").classes("text-[11px] font-medium uppercase tracking-wide text-gray-500")
                grid_switch = ui.switch("Show MGRS Grid", value=True, on_change=self._toggle_grid)
                grid_switch.classes("text-xs")

                self._density_switch = ui.switch("Colour tiles by product count", value=self._density_enabled, on_change=self._toggle_density)
                self._density_switch.classes("text-xs")
                self._density_switch.tooltip("Shade each visible tile by how many Sentinel-2 L2A products it has in the selected dates.")
                self._create_density_legend()

                # High-resolution opt-in. Default off — 60 m TCI loads in ~1-3 s
                # vs ~15-20 s for 10 m, and looks the same at MGRS-tile zoom.
                self._hires_switch = ui.switch(
                    "High resolution (10 m)",
                    value=False,
                    on_change=self._toggle_hires,
                )
                self._hires_switch.classes("text-xs")
                self._hires_switch.tooltip("Off: stream 60 m TCI (~1–3 s, sharp at MGRS-tile zoom).\nOn: stream 10 m TCI (~15–20 s, full detail when zoomed in).")

                # Before/after: a second date of the streamed tile, shown left of a draggable divider.
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    self._compare_button = (
                        ui
                        .button("Compare dates", icon="compare", on_click=self._open_compare_chooser)
                        .props("outline dense size=sm color=deep-orange disable")
                        .classes("text-xs grow")
                        .tooltip("Stream another date of this tile and swipe between the two")
                    )
                    self._compare_stop_button = ui.button(icon="close", on_click=self._stop_compare).props("flat dense round size=sm").tooltip("Stop comparing")
                    self._compare_stop_button.set_visibility(False)

                self._overlay_status_label = ui.label("Click an MGRS tile to enable overlays.").classes("text-xs text-gray-500")

                # Phase 1 – active-overlay chip (hidden until an overlay is active).
                with ui.row().classes("w-full items-center gap-1"):
                    self._active_overlay_chip = ui.badge("").props("color=teal").classes("text-[11px] hidden")

                ui.separator().classes("my-1")
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Overlays").classes("text-xs font-medium")
                    ui.label("One at a time").classes("text-[11px] text-gray-400")

                # Point inspector: stays disabled until the active overlay has loaded with raw values to read.
                self._inspect_switch = ui.switch("Inspect values", value=False, on_change=self._on_inspect_toggle)
                self._inspect_switch.classes("text-xs")
                self._inspect_switch.props("disable")
                self._inspect_switch.tooltip("While on, clicking the map reads values instead of selecting tiles.")

                # Build any overlay-specific extra controls that need a widget
                # reference stored on self.
                def _build_tcd_controls():
                    self._tcd_year_select = (
                        ui
                        .select(
                            options=["2020"],
                            value=self._tcd_year,
                            label="TCD year",
                            on_change=self._on_tcd_year_change,
                        )
                        .props("dense outlined disable")
                        .classes("w-full text-xs")
                    )

                def _build_lc100_controls():
                    self._lc100_year_select = (
                        ui
                        .select(
                            options=["2019", "2018", "2017", "2016", "2015"],
                            value="2019",
                            label="LC100 year",
                            on_change=self._on_lc100_year_change,
                        )
                        .props("dense outlined disable")
                        .classes("w-full text-xs")
                    )

                def _build_lst_controls():
                    self._lst_time_select = (
                        ui
                        .select(
                            options=[],
                            value=None,
                            label="LST time (Europe/Brussels)",
                            on_change=self._on_lst_time_change,
                        )
                        .props("dense outlined disable")
                        .classes("w-full text-xs")
                    )
                    ui.label("Times shown in Europe/Brussels local time (CET/CEST).").classes("text-[11px] text-gray-400")

                extra_controls_by_name = {
                    "tcd": _build_tcd_controls,
                    "lc100": _build_lc100_controls,
                    "lst": _build_lst_controls,
                }

                # One picker for all overlays (type to filter); only the picked overlay's settings are shown.
                options = {OVERLAY_NONE: "None"}
                options.update({spec.name: f"{spec.category} · {spec.title}" for spec in OVERLAY_REGISTRY})
                self._overlay_select = ui.select(options=options, value=OVERLAY_NONE, label="Overlay", with_input=True, on_change=self._on_overlay_pick).props("dense outlined options-dense").classes("w-full text-xs")
                for spec in OVERLAY_REGISTRY:
                    self._create_overlay_section(
                        spec=spec,
                        on_toggle=self._make_toggle_handler(spec.name),
                        extra_controls=extra_controls_by_name.get(spec.name),
                    )

    def _sync_stream_source_note(self) -> None:
        """Say where tile previews come from when that is not CDSE, and how to change it. Hidden otherwise."""
        if self._stream_source_label is None:
            return
        has_keys = CopernicusConfig().has_static_s3_credentials()
        source = public_s2.stream_source()
        if source == "public" and not has_keys:
            text = "No S3 keys: streaming previews from AWS Open Data (Element84 Earth Search). Add CDSE S3 keys in ☰ → Settings to stream from CDSE."
        elif source == "public":
            text = "Streaming previews from AWS Open Data (Element84 Earth Search)."
        elif not has_keys:
            text = "Tile streaming needs CDSE S3 keys. Add them in ☰ → Settings."
        else:
            self._stream_source_label.set_visibility(False)
            return
        self._stream_source_label.set_text(text)
        self._stream_source_label.set_visibility(True)

    def _create_density_legend(self) -> None:
        """Build the legend for the product-count colours. It is shown only while grid tiles are coloured."""
        with ui.column().classes("w-full gap-0.5") as legend:
            with ui.row().classes("w-full items-center gap-x-2 gap-y-1 flex-wrap"):
                ui.label("Products per tile per month").classes("text-[11px] text-gray-500 dark:text-gray-400")
                self._legend_swatch("0", GRID_EMPTY_STYLE["fillColor"], GRID_EMPTY_STYLE["color"], dashed=True)
                for label, colour in grid_density_legend():
                    self._legend_swatch(label, colour, GRID_DENSITY_STYLE["color"])
            self._density_caption = ui.label("").classes("text-[11px] text-gray-500 dark:text-gray-400")
            self._density_note = ui.label(DENSITY_PARTIAL_TEXT).classes("text-[11px] text-amber-600 dark:text-amber-400 italic")
        self._density_legend = legend
        self._sync_density_legend()

    @staticmethod
    def _legend_swatch(label: str, fill: str, border: str, dashed: bool = False) -> None:
        """Add one colour swatch with its label to the density legend."""
        with ui.row().classes("items-center gap-1 no-wrap"):
            border_style = "dashed" if dashed else "solid"
            ui.element("div").classes("w-3 h-3 rounded-sm").style(f"background:{fill};border:1px {border_style} {border}")
            ui.label(label).classes("text-[11px] text-gray-600 dark:text-gray-300")

    def _create_overlay_section(
        self,
        spec: "OverlaySpec",
        on_toggle,
        extra_controls: Optional[Callable[[], None]] = None,
    ):
        """Create the settings of one overlay, hidden until it is picked in the overlay select."""
        overlay_name = spec.name
        title = spec.title
        description = spec.description
        icon = spec.icon

        # Only the overlay picked in the overlay select shows its settings.
        with ui.element("div").classes("w-full") as section_row:
            section_row.set_visibility(False)
            self._overlay_section_rows[overlay_name] = section_row
            with ui.column().classes("w-full gap-1"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.icon(icon).classes("text-gray-500")
                    ui.label(title).classes("text-sm font-medium")
                info_text = self._overlay_info_by_name.get(overlay_name)
                with ui.row().classes("w-full items-start gap-1"):
                    ui.label(description).classes("text-xs text-gray-500 grow")
                    if info_text:
                        self._create_overlay_info_button(info_text)

                # Phase 3 – coverage note badge (shown/updated on tile load).
                coverage_note = spec.coverage_note
                if coverage_note:
                    cov_label = ui.label(f"⚠ {coverage_note}").classes("text-[11px] text-amber-600 dark:text-amber-400 italic")
                else:
                    cov_label = ui.label("").classes("hidden")
                self._overlay_coverage_labels[overlay_name] = cov_label

                overlay_switch = ui.switch("Show overlay", value=False, on_change=on_toggle)
                overlay_switch.classes("text-xs")
                overlay_switch.props("disable")
                overlay_switch.tooltip("Stream a tile first to enable overlays")
                self._overlay_switches[overlay_name] = overlay_switch

                opacity_slider = ui.slider(
                    min=0.2,
                    max=1.0,
                    step=0.05,
                    value=self._overlay_opacity_by_name[overlay_name],
                    on_change=lambda e, name=overlay_name: self._on_overlay_opacity_change(name, e.value),
                )
                opacity_slider.classes("w-full")
                opacity_slider.props("disable")
                opacity_slider.tooltip("Stream a tile first to enable overlays")
                self._overlay_sliders[overlay_name] = opacity_slider
                ui.label("Opacity").classes("text-xs text-gray-400")

                if extra_controls:
                    extra_controls()

    def _create_overlay_info_button(self, info_text: str):
        """Render a click-to-open info popup with attribution and a clickable link.

        A plain hover tooltip cannot be used to reach the source website because it
        disappears as soon as the pointer leaves the icon. Instead this opens a menu
        on click that stays open until the user clicks away, so the website link is
        actually clickable.
        """
        match = re.search(r"https?://\S+", info_text)
        url = match.group(0).rstrip(".") if match else None
        # Drop the trailing "Website: <url>" so the link is not duplicated as text.
        label_text = re.sub(r"\s*Website:\s*https?://\S+\s*$", "", info_text).strip()
        with ui.button(icon="info").props("flat round dense size=sm color=blue").classes("shrink-0"):
            with ui.menu():
                with ui.column().classes("p-3 gap-2").style("max-width: 20rem"):
                    ui.label(label_text).classes("text-xs text-gray-600 whitespace-normal")
                    if url:
                        ui.link("Open source website", url).classes("text-xs text-blue-600 dark:text-sky-400 no-underline hover:underline").props("target=_blank")

    def _set_control_enabled(self, control, enabled: bool):
        """Enable or disable a single NiceGUI control."""
        if control is None:
            return
        if enabled:
            control.props(remove="disable")
        else:
            control.props("disable")

    def _set_overlay_controls_enabled(self, enabled: bool):
        """Enable or disable overlay switches and settings together."""
        controls = list(self._overlay_switches.values()) + list(self._overlay_sliders.values())
        if hasattr(self, "_tcd_year_select"):
            controls.append(self._tcd_year_select)
        if hasattr(self, "_lc100_year_select"):
            controls.append(self._lc100_year_select)
        if hasattr(self, "_lst_time_select"):
            controls.append(self._lst_time_select)

        for control in controls:
            self._set_control_enabled(control, enabled)

    async def _refresh_lst_time_options(self):
        """Refresh selectable hourly LST timestamps for the current tile."""
        if not self._lst_time_select:
            return

        tile_code = self._streaming_tile_code
        lookup_date = self._streaming_date or ""
        target_timestamp = self._streaming_timestamp or lookup_date
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, lookup_date) if tile_code else None

        if not ref_path or not target_timestamp:
            self._lst_time_options_by_label = {}
            self._lst_user_selected_timestamp = None
            self._lst_selected_timestamp_label = None
            self._lst_time_select.set_options([])
            self._lst_time_select.set_value(None)
            return

        from vresto.services.lst import format_lst_selected_datetime, lst_service

        datetimes = await asyncio.to_thread(lst_service.list_available_lst_datetimes, ref_path, target_timestamp)
        if not datetimes:
            self._lst_time_options_by_label = {}
            self._lst_user_selected_timestamp = None
            self._lst_selected_timestamp_label = None
            self._lst_time_select.set_options([])
            self._lst_time_select.set_value(None)
            return

        options_by_label = {format_lst_selected_datetime(dt): dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") for dt in datetimes}
        self._lst_time_options_by_label = options_by_label
        labels = list(options_by_label.keys())
        self._lst_time_select.set_options(labels)

        selected_timestamp = self._lst_user_selected_timestamp
        if not selected_timestamp or selected_timestamp not in options_by_label.values():
            target_dt = parse_date_like(target_timestamp)
            nearest_dt = min(datetimes, key=lambda dt: abs(dt - target_dt))
            selected_timestamp = nearest_dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")

        self._lst_user_selected_timestamp = selected_timestamp
        selected_label = next(label for label, value in options_by_label.items() if value == selected_timestamp)
        self._lst_selected_timestamp_label = selected_label
        self._lst_time_select.set_value(selected_label)

    def _set_tcd_controls_enabled(self, enabled: bool):
        """Enable or disable TCD-only controls based on tile coverage."""
        self._set_control_enabled(self._overlay_switches.get("tcd"), enabled)
        self._set_control_enabled(self._overlay_sliders.get("tcd"), enabled)
        if hasattr(self, "_tcd_year_select"):
            self._set_control_enabled(self._tcd_year_select, enabled)

    def _update_tcd_overlay_availability(self):
        """Disable TCD controls when the streamed tile is outside pantropical coverage."""
        tile_code = self._streaming_tile_code
        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date) if tile_code else None

        available = False
        if ref_path:
            try:
                import rasterio
                from rasterio.warp import transform_bounds

                from vresto.services.tcd import tcd_has_coverage

                with rasterio.open(ref_path) as ref:
                    _, bottom, _, top = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                available = tcd_has_coverage(bottom, top)
            except Exception as exc:
                logger.warning(f"Could not evaluate TCD coverage for {tile_code}: {exc}")

        self._overlay_tile_available["tcd"] = available
        self._set_tcd_controls_enabled(bool(ref_path) and available)

        if available:
            return

        self._switch_off_overlay("tcd", remove_layer=True)

        if hasattr(self, "_overlay_status_label"):
            self._overlay_status_label.set_text("Tree Cover Density is unavailable for this tile (pantropical coverage only). Choose another overlay.")

    def _show_overlay_section(self, overlay_name: Optional[str]) -> None:
        """Show the settings of one overlay (or none) under the overlay picker."""
        for name, row in self._overlay_section_rows.items():
            row.set_visibility(name == overlay_name)

    def _sync_overlay_sections(self, active_overlay: Optional[str]):
        """Show the active overlay in the picker with its settings, update the active chip, and keep it in the link.

        When no overlay is active, the picker keeps showing the last one, switched off, so it is one click to turn back on.
        """
        if active_overlay:
            self._show_overlay_section(active_overlay)
            if self._overlay_select is not None and self._overlay_select.value != active_overlay:
                suppressed = self._suppress_overlay_events
                self._suppress_overlay_events = True
                try:
                    self._overlay_select.set_value(active_overlay)
                finally:
                    self._suppress_overlay_events = suppressed
        self._push_url(overlay=active_overlay)

        # Phase 1: update the active-overlay chip
        if self._active_overlay_chip is not None:
            if active_overlay:
                title = self._overlay_titles.get(active_overlay, active_overlay)
                date_part = self._streaming_date or ""
                chip_text = f"Active: {title}" + (f"  ·  {date_part}" if date_part else "")
                self._active_overlay_chip.set_text(chip_text)
                self._active_overlay_chip.classes(remove="hidden")
            else:
                self._active_overlay_chip.set_text("")
                self._active_overlay_chip.classes(add="hidden")

    def _overlay_layer_name(self, overlay_name: str, tile_code: Optional[str] = None) -> str:
        """Build the map layer name for a given overlay and tile."""
        current_tile = tile_code or self._streaming_tile_code
        if not current_tile:
            return ""

        return f"{self._overlay_specs[overlay_name].layer_prefix}_{current_tile}"

    def _pool_name(self, layer_name: str) -> str:
        """Return the tile pool name for a map layer of this tab."""
        return f"{self.pool_namespace}{layer_name}"

    def _remove_overlay_layer(self, overlay_name: str, tile_code: Optional[str] = None):
        """Remove a single overlay layer from the map and cache."""
        layer_name = self._overlay_layer_name(overlay_name, tile_code)
        self._overlay_layer_urls.pop(overlay_name, None)
        self._overlay_fetched.pop(overlay_name, None)
        if overlay_name == self._active_overlay:
            # Every path that retargets or drops the active overlay (switch off, new tile,
            # new year / hour) comes through here, and its values are no longer on the map.
            self._reset_inspector()
        if not layer_name:
            return

        tile_pool.remove(self._pool_name(layer_name))
        if self.map_widget_obj:
            self.map_widget_obj.remove_tile_layer(layer_name)

    def _remove_overlay_layers(self, tile_code: str):
        """Remove all overlay layers associated with a given tile code."""
        for overlay_name in self._overlay_switches:
            self._remove_overlay_layer(overlay_name, tile_code)

    def _set_overlay_flag(self, overlay_name: str, enabled: bool):
        """Synchronize overlay booleans with generic handlers."""
        setattr(self, f"_{overlay_name}_enabled", enabled)

    def _get_enabled_overlay(self) -> Optional[str]:
        """Return the first currently enabled overlay."""
        for name in OVERLAY_NAMES:
            if getattr(self, f"_{name}_enabled"):
                return name
        return None

    def _clear_overlay_legend(self):
        """Remove any active legend from the map."""
        if self.map_widget_obj:
            self.map_widget_obj.clear_legend()

    def _show_overlay_legend(self, overlay_name: str):
        """Render the floating legend for the current active overlay."""
        if not self.map_widget_obj:
            return

        spec = self._overlay_specs[overlay_name]
        self.map_widget_obj.set_legend(build_overlay_legend_html(spec, self._overlay_legend_suffixes.get(overlay_name, "")))

    async def _activate_overlay(self, overlay_name: str):
        """Enable a single overlay and turn the others off."""
        if not self._streaming_tile_code:
            return

        self._active_overlay = overlay_name
        self._clear_overlay_legend()
        self._suppress_overlay_events = True
        try:
            for other_name, switch in self._overlay_switches.items():
                if other_name == overlay_name:
                    continue
                self._set_overlay_flag(other_name, False)
                switch.set_value(False)
                self._remove_overlay_layer(other_name)

            self._sync_overlay_sections(overlay_name)
        finally:
            self._suppress_overlay_events = False

        if hasattr(self, "_overlay_status_label"):
            title = self._overlay_titles[overlay_name]
            date_part = self._streaming_date or ""
            status = f"{title} is active"
            if date_part:
                status += f" for {self._streaming_tile_code} ({date_part})"
            status += ". Click another tile to retarget automatically."
            self._overlay_status_label.set_text(status)

        await self._load_overlay(overlay_name)

    async def _reload_enabled_overlays(self):
        """Reload the active overlay for the current tile."""
        overlay_name = self._active_overlay or self._get_enabled_overlay()
        if not overlay_name:
            self._clear_overlay_legend()
            return

        self._active_overlay = overlay_name
        self._clear_overlay_legend()
        self._sync_overlay_sections(overlay_name)
        await self._load_overlay(overlay_name)

    def _on_overlay_opacity_change(self, overlay_name: str, value: float):
        """Apply per-overlay opacity live using the cached layer URL."""
        self._overlay_opacity_by_name[overlay_name] = float(value or 0.0)
        if self._active_overlay != overlay_name or not self._streaming_tile_code:
            return

        layer_name = self._overlay_layer_name(overlay_name)
        layer_url = self._overlay_layer_urls.get(overlay_name)
        if not layer_name or not layer_url or not self.map_widget_obj:
            return

        self.map_widget_obj.remove_tile_layer(layer_name)
        self.map_widget_obj.add_tile_layer(
            layer_url,
            name=layer_name,
            opacity=self._overlay_opacity_by_name[overlay_name],
        )

    def _toggle_grid(self, e):
        """Toggle the MGRS grid overlay on/off."""
        self._grid_enabled = e.value
        if self._grid_enabled:
            # Trigger initial grid render by emitting a fake moveend
            if self.map_widget_obj and self.map_widget_obj._map:
                map_id = self.map_widget_obj._map.id
                js = f"""
                (function() {{
                    const el = getElement({map_id});
                    if (el && el.map) el.map.fire('moveend');
                }})();
                """
                ui.run_javascript(js)
        else:
            self._hide_grid()

    def _toggle_hires(self, e) -> None:
        """Switch between 60 m (fast preview) and 10 m (full detail) TCI."""
        self._tci_resolution = "10m" if e.value else "60m"
        logger.info(f"TCI resolution set to {self._tci_resolution}")

    def _handle_moveend(self, bbox: tuple, zoom: int):
        """Handle map moveend: redraw the MGRS grid if enabled, then recount its products."""
        if not self._grid_enabled:
            return

        # Below the tile zoom, show the ~1,200 grid zones (6 by 8 degrees) so the whole world is visible.
        zones = zoom < MIN_ZOOM_FOR_GRID
        geojson = compute_visible_zones_geojson(bbox) if zones else compute_visible_tiles_geojson(bbox, zoom)
        self._grid_zones = zones
        if geojson and self.map_widget_obj:
            self.map_widget_obj.set_grid_layer(geojson)
            self._grid_bbox = bbox
            self._grid_codes = [f["properties"]["mgrs_code"] for f in geojson.get("features", []) if f.get("properties", {}).get("mgrs_code")]
            self._forget_density()  # the new grid is plain until its counts arrive
            self._schedule_density_refresh()
        elif self.map_widget_obj:
            self._hide_grid()

    def _hide_grid(self) -> None:
        """Remove the MGRS grid, along with any product counts still on their way."""
        self._grid_bbox = None
        self._forget_density()
        if self.map_widget_obj:
            self.map_widget_obj.clear_grid_layer()

    # ------------------------------------------------------------------
    # Product-count colouring of the grid
    # ------------------------------------------------------------------

    def _toggle_density(self, e) -> None:
        """Switch the product-count colouring of the grid tiles on or off."""
        self._density_enabled = bool(e.value)
        if self._density_enabled:
            self._sync_density_legend()
            self._schedule_density_refresh()
        else:
            self._forget_density()
            if self.map_widget_obj:
                self.map_widget_obj.clear_grid_density()

    def _schedule_density_refresh(self) -> None:
        """Count products per visible tile in the background, then colour the grid with the result.

        Each request supersedes the one before it, so a burst of map moves or date changes costs one search.
        """
        if not (self._density_enabled and self._grid_bbox is not None and self.map_widget is not None and self.map_widget_obj):
            return
        self._density_generation += 1
        background_tasks.create(
            self._refresh_density(self._density_generation, self._grid_bbox, self.map_widget.client),
            name="mgrs-density",
        )

    async def _refresh_density(self, generation: int, bbox: tuple, client) -> None:
        """Count products for ``bbox`` after the debounce, and colour the grid if this request is still current.

        A request is stale once a newer one has been made (by a map move, a date change, or the switch
        being turned off). Stale requests return without touching the map, both while they wait and
        when their search finishes. Failures leave the plain grid in place.
        """
        await asyncio.sleep(DENSITY_DEBOUNCE_S)
        if generation != self._density_generation:
            return

        date_range = self.current_state["date_range"]
        quick: Optional[TileDensity] = None
        if not self._grid_zones:
            # Whole-month counts come from small cached files and arrive almost at once: show them
            # while the exact counts for the selected dates, which take seconds, are read.
            try:
                quick = await asyncio.to_thread(count_whole_months, date_range["from"], date_range["to"], list(self._grid_codes))
            except Exception as exc:
                logger.debug(f"Whole-month tile counts failed: {exc}")
            if generation != self._density_generation:
                return
            if quick is not None:
                try:
                    with client:
                        self._apply_density(quick, refining=True)
                except Exception as exc:
                    logger.debug(f"Could not colour the grid by product count: {exc}")
                await asyncio.sleep(EXACT_COUNT_DELAY_S)
                if generation != self._density_generation:
                    return
        try:
            if self._grid_zones:
                density = await asyncio.to_thread(count_products_per_zone, date_range["from"], date_range["to"], list(self._grid_codes))
            else:
                density = await asyncio.to_thread(count_products_per_tile, bbox, date_range["from"], date_range["to"], tile_codes=list(self._grid_codes))
        except Exception as exc:
            logger.debug(f"Tile density lookup failed: {exc}")
            return
        if generation != self._density_generation:
            return
        if density is None and quick is not None:
            density = quick  # exact counts unavailable: keep the whole-month colours, without the "refining" note
        if density is None:
            logger.debug("Tile density unavailable; the grid stays plain")
            if rate_limited():
                try:
                    with client:
                        self._density_rate_limited = True
                        self._sync_density_legend()
                except Exception as exc:
                    logger.debug(f"Could not show the rate-limit note: {exc}")
                    return
                # Try once more when the pause is over, unless the view or dates changed meanwhile.
                await asyncio.sleep(RATE_LIMIT_COOLDOWN_S + 1)
                if generation == self._density_generation:
                    with client:
                        self._schedule_density_refresh()
            return

        try:
            with client:
                self._apply_density(density)
        except Exception as exc:
            logger.debug(f"Could not colour the grid by product count: {exc}")

    def _apply_density(self, density: TileDensity, refining: bool = False) -> None:
        """Colour the grid tiles with a density result, and show the partial-count note when needed."""
        self._density_truncated = density.truncated
        self._density_rate_limited = False
        if self._density_caption is not None:
            caption = _density_caption_text(density)
            if refining:
                caption += " Refining to the selected dates…"
            self._density_caption.set_text(caption)
        months = density.months if density.months > 0 else 1.0
        payload = {}
        for code, count in density.counts.items():
            tiles = density.tiles.get(code)  # grid zones: average over the zone's tiles with products
            rate = count / months / (tiles or 1)
            payload[code] = {"count": count, "min_cloud": density.min_cloud.get(code), "rate": round(rate, 2)}
            if tiles:
                payload[code]["tiles"] = tiles
        if self.map_widget_obj:
            self.map_widget_obj.update_grid_density(payload, truncated=density.truncated)
        self._sync_density_legend()

    def _forget_density(self) -> None:
        """Drop the current counts: a request still in flight is ignored and the partial-count note hides."""
        self._density_generation += 1
        self._density_truncated = False
        self._density_rate_limited = False
        self._sync_density_legend()

    def _sync_density_legend(self) -> None:
        """Show the legend, and its partial-count note, only while grid tiles are shown and coloured."""
        shown = self._density_enabled and self._grid_bbox is not None
        if self._density_legend is not None:
            self._density_legend.set_visibility(shown)
        if self._density_note is not None:
            if self._density_rate_limited:
                self._density_note.set_text(DENSITY_RATE_LIMITED_TEXT)
            elif self._density_truncated:
                self._density_note.set_text(DENSITY_PARTIAL_TEXT)
            self._density_note.set_visibility(shown and (self._density_truncated or self._density_rate_limited))

    async def _handle_tile_click(self, tile_code: str):
        """Handle click on an MGRS grid tile.

        Looks up the top-N latest matching products for the tile and date range,
        then either streams directly (single match) or opens a chooser dialog so
        the user picks which product to stream (multiple matches).
        """
        previous_tile_code = self._streaming_tile_code
        # Credentials may have been added in Settings since the controls were built.
        self._sync_stream_source_note()
        if hasattr(self, "_tile_status_label"):
            self._tile_status_label.set_text(f"Loading {tile_code}...")
        self._add_message(f"🛰️ Clicked tile: {tile_code}")
        ui.notify(f"Loading tile {tile_code}...", position="top", type="info", spinner=True)

        # Visual feedback on the map: yellow-highlight the clicked tile while
        # candidates are fetched / the chooser is open.
        if self.map_widget_obj:
            self.map_widget_obj.highlight_tile(tile_code)

        try:
            products = await self._get_tile_candidates(tile_code, limit=TILE_PRODUCT_CHOICES)
        except Exception as e:  # pragma: no cover - defensive, catalog errors already logged
            logger.warning(f"Tile candidate lookup failed for {tile_code}: {e}")
            products = []

        if not products:
            self._add_message(f"⚠️ No S2 L2A product found for tile {tile_code} in selected date range.")
            ui.notify("No product found for this tile in the date range.", position="top", type="warning")
            if hasattr(self, "_tile_status_label"):
                tile_label = self._streaming_tile_code or "No tile selected"
                if self._streaming_tile_code:
                    tile_label = f"Selected tile: {self._streaming_tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                self._tile_status_label.set_text(tile_label)
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()
            return

        if len(products) == 1:
            await self._commit_tile_selection(tile_code, products[0], previous_tile_code)
            return

        self._add_message(f"📄 {len(products)} candidates for {tile_code} — pick one to stream.")
        if hasattr(self, "_tile_status_label"):
            self._tile_status_label.set_text(f"Select product for {tile_code}...")
        self._show_tile_product_chooser(tile_code, products, previous_tile_code)

    async def _commit_tile_selection(
        self,
        tile_code: str,
        product: ProductInfo,
        previous_tile_code: Optional[str],
    ):
        """Commit a chosen product: wire overlay state, stream TCI, refresh overlays."""
        self._streaming_tile_code = tile_code
        self._stop_compare()
        if hasattr(self, "_overlay_status_label"):
            self._overlay_status_label.set_text("Pick an overlay. It follows tile changes automatically once streamed.")
        if self.map_widget_obj:
            self.map_widget_obj.highlight_tile(tile_code)
        self._set_overlay_controls_enabled(True)

        # Phase 1: hide the how-to card on first successful tile commit.
        if self._how_to_card is not None:
            self._how_to_card.set_visibility(False)

        try:
            if previous_tile_code and previous_tile_code != tile_code:
                self._remove_overlay_layers(previous_tile_code)

            streamed = await self._stream_product(tile_code, product)
            if streamed:
                if self.map_widget_obj:
                    self.map_widget_obj.set_streamed_tile(tile_code)
                if hasattr(self, "_tile_status_label"):
                    tile_label = f"Selected tile: {tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                    self._tile_status_label.set_text(tile_label)
                self._push_url(tile=tile_code, date=self._streaming_date)
                self._set_control_enabled(self._compare_button, True)
                self._update_tcd_overlay_availability()
                await self._refresh_lst_time_options()
                await self._reload_enabled_overlays()
                self._activate_pending_overlay()
        finally:
            # Always reset the highlight, even if streaming raised.
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()

    def _show_tile_product_chooser(
        self,
        tile_code: str,
        products: list,
        previous_tile_code: Optional[str],
    ) -> None:
        """Open a modal dialog letting the user pick which product to stream.

        Each card shows sensing date, cloud cover, and size with a single
        "Stream this tile" button. Cancel (or dismissal) clears the tile
        highlight and restores the previous status label.
        """
        selected = {"committed": False}

        def _reset_after_dismiss() -> None:
            if selected["committed"]:
                return
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()
            if hasattr(self, "_tile_status_label"):
                if self._streaming_tile_code:
                    tile_label = f"Selected tile: {self._streaming_tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                    self._tile_status_label.set_text(tile_label)
                else:
                    self._tile_status_label.set_text("No tile selected")

        with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-full"):
            ui.label(f"Select a product for tile {tile_code}").classes("text-base font-semibold")
            ui.label(f"Latest {len(products)} matches in the selected date range (cloud cover ≤ 50%).").classes("text-xs text-gray-500 mb-2")
            if any(p.source == public_s2.PUBLIC_SOURCE for p in products):
                ui.label(public_s2.PUBLIC_ATTRIBUTION).classes("text-xs text-gray-500 mb-2")

            with ui.column().classes("w-full gap-2"):
                for idx, product in enumerate(products, 1):
                    # AWS Open Data products show the thumbnail the catalogue offers, not a CDSE quicklook.
                    is_public = product.source == public_s2.PUBLIC_SOURCE
                    with ui.card().classes("w-full p-2 bg-gray-50 dark:bg-slate-800 shadow-sm rounded-md"):
                        display = getattr(product, "display_name", product.name)
                        ui.label(f"{idx}. {display}").classes("text-xs font-mono break-all")
                        if is_public and product.thumbnail_url:
                            ui.image(product.thumbnail_url).props("fit=contain").classes("w-full h-40 rounded")
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600 dark:text-gray-300")
                            if product.cloud_cover is not None:
                                ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600 dark:text-gray-300")
                            if not is_public:
                                ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600 dark:text-gray-300")
                            ui.space()

                            if not is_public:

                                def _make_quicklook_handler(p=product):
                                    async def _on_quicklook():
                                        # Preview the candidate without dismissing the chooser, so
                                        # the user can compare multiple scenes before committing.
                                        result = self.on_quicklook(p, self.messages_column)
                                        if asyncio.iscoroutine(result):
                                            await result

                                    return _on_quicklook

                                ui.button("Quicklook", on_click=_make_quicklook_handler()).props("flat size=sm").classes("text-xs")

                            def _make_stream_handler(p=product):
                                async def _on_stream():
                                    selected["committed"] = True
                                    dialog.close()
                                    await self._commit_tile_selection(tile_code, p, previous_tile_code)

                                return _on_stream

                            ui.button("Stream this tile", on_click=_make_stream_handler()).props("size=sm color=primary").classes("text-xs")

            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat size=sm")

        dialog.on("hide", lambda _e: _reset_after_dismiss())
        dialog.open()

    async def _stream_product(self, tile_code: str, product: ProductInfo) -> bool:
        """Stream TCI for the given (tile, product) and show it on the map: cache hit first, then full-res."""
        t_e2e = time.perf_counter()
        date, timestamp = _scene_keys(product)
        self._streaming_date = date
        self._streaming_timestamp = timestamp
        cog_path = await self._fetch_tci(tile_code, product)
        if not cog_path:
            return False
        await self._display_tci_layer(cog_path, tile_code)
        logger.info(f"[perf] _stream_product '{tile_code}' end-to-end {(time.perf_counter() - t_e2e) * 1000:.0f} ms")
        return True

    async def _fetch_tci(self, tile_code: str, product: ProductInfo) -> Optional[str]:
        """Return the path of the product's cached TCI for ``tile_code``, streaming it first if needed.

        Returns None (after saying why in the activity log) when it cannot be streamed.
        """
        date, _ = _scene_keys(product)
        resolution = self._tci_resolution
        is_public = product.source == public_s2.PUBLIC_SOURCE

        self._add_message(f"📡 Streaming TCI ({resolution}) for {tile_code} ({product.sensing_date})...")

        cached = sentinel_stream_service.get_cached_tci_path(tile_code, date, resolution)
        if cached:
            self._add_message(f"⚡ Cache hit ({resolution}) for {tile_code}")
            if is_public:
                self._add_message(f"ℹ️ {public_s2.PUBLIC_ATTRIBUTION}")
            return cached

        if is_public:
            t_stream = time.perf_counter()
            result = await asyncio.to_thread(sentinel_stream_service.stream_public_tci, product, tile_code, date, resolution)
            logger.info(f"[perf] _fetch_tci '{tile_code}': stream_public_tci {(time.perf_counter() - t_stream) * 1000:.0f} ms")
            if not result:
                self._add_message(f"❌ Failed to stream TCI for {tile_code} from AWS Open Data")
                ui.notify("TCI streaming failed", position="top", type="negative")
                return None
            self._add_message(f"✅ TCI ready for {tile_code}")
            self._add_message(f"ℹ️ {public_s2.PUBLIC_ATTRIBUTION}")
            return result

        # CDSE streaming reads S3 with static keys only, so it cannot run without them.
        if not CopernicusConfig().has_static_s3_credentials():
            self._add_message(f"⚠️ {STREAM_CREDENTIALS_HINT}")
            ui.notify(STREAM_CREDENTIALS_HINT, position="top", type="warning")
            return None

        # Need to find exact TCI path on S3
        if not product.s3_path:
            self._add_message(f"❌ No S3 path for product {product.name}")
            return None

        t_tci = time.perf_counter()
        tci_path = await asyncio.to_thread(
            sentinel_stream_service.find_tci_path_in_product,
            product.s3_path,
            tile_code,
            resolution,
        )
        logger.info(f"[perf] _fetch_tci '{tile_code}': find_tci_path_in_product {(time.perf_counter() - t_tci) * 1000:.0f} ms")
        if not tci_path:
            self._add_message(f"❌ Could not locate TCI band in {product.name}")
            return None

        # Stream and cache — pass the exact TCI path to avoid wildcard issues
        t_stream = time.perf_counter()
        result = await asyncio.to_thread(
            sentinel_stream_service.stream_tci,
            product.s3_path,
            tile_code,
            date,
            tci_path,
            resolution,
        )
        logger.info(f"[perf] _fetch_tci '{tile_code}': stream_tci (S3 + JP2 decode + write) {(time.perf_counter() - t_stream) * 1000:.0f} ms")
        if result:
            self._add_message(f"✅ TCI ready for {tile_code}")
            return result

        self._add_message(f"❌ Failed to stream TCI for {tile_code}")
        ui.notify("TCI streaming failed", position="top", type="negative")
        return None

    async def _display_tci_layer(self, cog_path: str, tile_code: str):
        """Display a cached TCI COG as a tile layer on the map."""
        layer_name = f"tci_{tile_code}"

        if self.map_widget_obj:
            # Remove any previous TCI layer before adding the new one
            for existing in list(self.map_widget_obj._tile_layers.keys()):
                if existing.startswith("tci_"):
                    tile_pool.remove(self._pool_name(existing))
                    self.map_widget_obj.remove_tile_layer(existing)

        # First-time TileClient bootstrap can take ~1–2 s; run off-loop.
        t_pool = time.perf_counter()
        url = await asyncio.to_thread(tile_pool.get_or_create, self._pool_name(layer_name), cog_path)
        logger.info(f"[perf] _display_tci_layer '{tile_code}': tile_pool.get_or_create {(time.perf_counter() - t_pool) * 1000:.0f} ms")
        if url and self.map_widget_obj:
            self.map_widget_obj.add_tile_layer(url, name=layer_name)

            # Zoom the map to the raster bounds
            bounds = tile_pool.get_bounds(self._pool_name(layer_name))
            if bounds and self._fit_streamed_tile:
                min_lat, min_lon, max_lat, max_lon = bounds
                self.map_widget_obj.fit_bounds(bounds)
            ui.notify(f"✅ Tile {tile_code} rendered", position="top", type="positive")

    def _find_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Return cached search-results products whose names match the MGRS tile.

        Newest sensing date first; capped at `limit`. Only returns products that
        explicitly contain the tile code in their name — the previous loose
        "first S2 product" fallback is intentionally dropped so the chooser
        always shows tile-specific candidates.
        """
        search_code = tile_code if tile_code.startswith("T") else f"T{tile_code}"
        matches = [p for p in self.current_state.get("products", []) if search_code in p.name]
        matches.sort(key=lambda p: p.sensing_date, reverse=True)
        return matches[:limit]

    async def _search_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Query the CDSE catalog for the latest N S2 L2A products matching the tile and date range."""
        date_range = self.current_state.get("date_range", {})
        start_date = date_range.get("from", "2020-01-01")
        end_date = date_range.get("to", start_date)

        search_code = tile_code if tile_code.startswith("T") else f"T{tile_code}"

        def _query():
            import requests

            from vresto.api.auth import get_shared_auth

            config = CopernicusConfig()
            # Reuse the process-wide CopernicusAuth so a single bearer token
            # (and its refresh token) is shared across every tile-hover and
            # streaming call. Constructing a fresh CopernicusAuth here would
            # open a brand-new Keycloak session per click and quickly trip
            # CDSE's concurrent-session cap.
            auth = get_shared_auth(config=config)
            # Catalogue search is anonymous on CDSE; a bearer token is only added when credentials are set.
            headers = auth.get_search_headers()

            # Build targeted OData filter: tile code + L2A + date range
            filters = [
                "Collection/Name eq 'SENTINEL-2'",
                f"contains(Name, '{search_code}')",
                "contains(Name, 'MSIL2A')",
                f"ContentDate/Start ge {start_date}T00:00:00.000Z",
                f"ContentDate/Start le {end_date}T23:59:59.999Z",
                "Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le 50.0)",
            ]
            filter_string = " and ".join(filters)
            url = f"{config.ODATA_BASE_URL}/Products"
            params = {
                "$filter": filter_string,
                "$top": limit,
                "$orderby": "ContentDate/Start desc",
                "$expand": "Attributes",
            }
            resp = requests.get(url, params=params, headers=headers, timeout=60)
            if resp.status_code != 200:
                return []

            from datetime import datetime as _dt

            products: list = []
            for item in resp.json().get("value", []):
                s3_path = item.get("S3Path", "")
                if not s3_path:
                    continue
                cloud_cover = None
                for attr in item.get("Attributes", []):
                    if attr.get("Name") == "cloudCover":
                        cloud_cover = attr.get("Value")
                        break
                sensing = item.get("ContentDate", {}).get("Start", "")
                if sensing:
                    try:
                        sensing = _dt.fromisoformat(sensing.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                size_mb = item.get("ContentLength", 0) / (1024 * 1024)
                products.append(
                    ProductInfo(
                        id=item.get("Id", ""),
                        name=item.get("Name", ""),
                        collection="SENTINEL-2",
                        sensing_date=sensing,
                        size_mb=size_mb,
                        s3_path=s3_path,
                        cloud_cover=cloud_cover,
                    )
                )
            return products

        try:
            products = await asyncio.to_thread(_query)
            if products:
                logger.info(f"Catalog returned {len(products)} candidate(s) for tile {tile_code}")
            return products
        except Exception as e:
            logger.warning(f"Catalog search for tile {tile_code} failed: {e}")
            return []

    async def _search_public_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Query AWS Open Data for the newest scenes of a tile in the selected date range. Needs no account."""
        date_range = self.current_state.get("date_range", {})
        start_date = date_range.get("from", "2020-01-01")
        end_date = date_range.get("to", start_date)
        try:
            products = await asyncio.to_thread(public_s2.search_tile, tile_code, start_date, end_date, limit=limit)
        except Exception as e:  # pragma: no cover - search_tile already logs its own failures
            logger.warning(f"AWS Open Data search for tile {tile_code} failed: {e}")
            return []
        if products:
            logger.info(f"AWS Open Data returned {len(products)} candidate(s) for tile {tile_code}")
        return products

    async def _get_tile_candidates(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Return up to `limit` latest products for a tile.

        Without static S3 keys the candidates come from AWS Open Data, because CDSE search
        results cannot be streamed without them. Otherwise, prefer cached matches from
        `current_state["products"]` (instant), and fall back to a CDSE catalog query.
        """
        if public_s2.stream_source() == "public":
            self._add_message(f"🔍 Searching AWS Open Data for tile {tile_code}...")
            t_search = time.perf_counter()
            products = await self._search_public_products_for_tile(tile_code, limit=limit)
            logger.info(f"[perf] _get_tile_candidates '{tile_code}': AWS Open Data search {(time.perf_counter() - t_search) * 1000:.0f} ms")
            return products

        cached = self._find_products_for_tile(tile_code, limit=limit)
        if cached:
            return cached

        self._add_message(f"🔍 Searching catalog for tile {tile_code}...")
        t_search = time.perf_counter()
        products = await self._search_products_for_tile(tile_code, limit=limit)
        logger.info(f"[perf] _get_tile_candidates '{tile_code}': catalog search {(time.perf_counter() - t_search) * 1000:.0f} ms")
        return products

    def _make_toggle_handler(self, overlay_name: str):
        """Return a NiceGUI switch handler for the given overlay key."""

        async def _toggle(e):
            if self._suppress_overlay_events:
                return

            self._set_overlay_flag(overlay_name, e.value)
            if e.value:
                await self._activate_overlay(overlay_name)
            else:
                self._remove_overlay_layer(overlay_name)
                if self._active_overlay == overlay_name:
                    self._active_overlay = None
                    self._clear_overlay_legend()
                    self._sync_overlay_sections(None)
                    if hasattr(self, "_overlay_status_label"):
                        self._overlay_status_label.set_text("Choose one overlay section for the selected tile.")

        return _toggle

    def _switch_off_overlay(self, overlay_name: str, remove_layer: bool = False):
        """Turn an overlay's switch off without firing its toggle handler.

        If the overlay was the active one, also clear the active state and legend.
        """
        self._suppress_overlay_events = True
        try:
            self._set_overlay_flag(overlay_name, False)
            switch = self._overlay_switches.get(overlay_name)
            if switch:
                switch.set_value(False)
        finally:
            self._suppress_overlay_events = False

        if self._active_overlay == overlay_name:
            self._active_overlay = None
            self._overlay_fetched.pop(overlay_name, None)
            self._reset_inspector()
            if remove_layer:
                self._remove_overlay_layer(overlay_name)
            self._clear_overlay_legend()
            self._sync_overlay_sections(None)

    def _overlay_setting(self, overlay_name: str) -> str:
        """Return the value of an overlay's own selector (year / hourly timestamp), or ``""``."""
        if overlay_name == "tcd":
            return self._tcd_year
        if overlay_name == "lc100":
            return self._lc100_year
        if overlay_name == "lst":
            return self._lst_user_selected_timestamp or self._streaming_timestamp or self._streaming_date or ""
        return ""

    def _on_overlay_fetched(self, overlay_name: str, fetched):
        """Record per-overlay state reported by a fetcher (``fetched`` is ``None`` on failure)."""
        self._overlay_legend_suffixes[overlay_name] = fetched.legend_suffix if fetched else ""
        if fetched:
            self._overlay_fetched[overlay_name] = fetched
        else:
            self._overlay_fetched.pop(overlay_name, None)
        if overlay_name == "lst":
            # The selector and legend both display the scene the nearest-hour snap picked.
            self._lst_selected_timestamp_label = fetched.detail if fetched else None

    async def _load_overlay(self, overlay_name: str):
        """Fetch an overlay for the streamed tile and show it on the map, with its legend.

        This is the single load pipeline shared by every overlay; what differs per
        overlay (which service to call, coverage gates, legend) lives in its
        :class:`OverlaySpec`.
        """
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        spec = self._overlay_specs[overlay_name]
        date = self._streaming_date or ""

        # The raster being inspected is about to change, so drop any popup that reads the old one.
        self._reset_inspector()

        # Overlays only need the CRS + extent of the reference raster, so any cached
        # TCI resolution will do.
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        request = OverlayRequest(
            ref_path=ref_path,
            tile_code=tile_code,
            date=date,
            timestamp=self._streaming_timestamp or date,
            setting=self._overlay_setting(overlay_name),
            tile_available=self._overlay_tile_available.get(overlay_name, True),
        )

        unavailable_reason = spec.precheck(request) if spec.precheck else None
        # Most overlays read CDSE S3 with the static keys; without them the read fails slowly, so say so up front.
        if not unavailable_reason and spec.needs_cdse_s3 and not CopernicusConfig().has_static_s3_credentials():
            unavailable_reason = f"{spec.label} is read from CDSE S3, which needs S3 keys. Add them in ☰ → Settings."
        if unavailable_reason:
            self._add_message(f"⚠️ {unavailable_reason}")
            ui.notify(unavailable_reason, position="top", type="warning", timeout=6000)
            self._switch_off_overlay(overlay_name)
            return

        self._add_message(f"{spec.emoji} Loading {spec.label} for {tile_code}...")
        ui.notify(f"Loading {spec.label} for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        fetched = await asyncio.to_thread(spec.fetch, request)
        self._on_overlay_fetched(overlay_name, fetched)

        if fetched:
            layer_name = self._overlay_layer_name(overlay_name, tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, self._pool_name(layer_name), fetched.colorized_path)
            if url and self.map_widget_obj:
                self._overlay_layer_urls[overlay_name] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name[overlay_name],
                )
                self._show_overlay_legend(overlay_name)
                self._update_inspector_availability(overlay_name)
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] {spec.label} overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                if fetched.detail and hasattr(self, "_overlay_status_label"):
                    self._overlay_status_label.set_text(f"{spec.title} is active for {tile_code} ({fetched.detail}). Click another tile to retarget it automatically.")
                detail = f"{fetched.detail}, " if fetched.detail else ""
                self._add_message(f"✅ {spec.label} overlay active for {tile_code} ({detail}{elapsed_ms:.0f} ms)")
                ui.notify(f"✅ {spec.label} overlay active for {tile_code}", position="top", type="positive")
                return

        # Either the fetcher returned nothing or the tile_pool/url stage failed.
        self._overlay_layer_urls.pop(overlay_name, None)
        logger.warning(f"{spec.label} overlay failed for {tile_code}")
        self._add_message(f"❌ {spec.label} overlay failed for {tile_code}")
        ui.notify(f"{spec.label} overlay failed for {tile_code}", position="top", type="negative")

    # ------------------------------------------------------------------
    # Overlay picker
    # ------------------------------------------------------------------

    def _on_overlay_pick(self, e) -> None:
        """Show the picked overlay's settings and turn it on (or turn the overlays off for "None").

        Picked before any tile is streamed, the overlay loads on the first tile.
        """
        if self._suppress_overlay_events:
            return
        name = e.value or None
        if name is None:
            self._pending_overlay = None
            active = self._active_overlay or self._get_enabled_overlay()
            self._show_overlay_section(None)
            if active and active in self._overlay_switches:
                self._overlay_switches[active].set_value(False)  # its handler removes the layer and legend
            return

        self._show_overlay_section(name)
        if not self._streaming_tile_code:
            self._pending_overlay = name
            if hasattr(self, "_overlay_status_label"):
                self._overlay_status_label.set_text(f"{self._overlay_titles[name]} will load on the tile you click.")
            return
        switch = self._overlay_switches.get(name)
        if switch is not None and not switch.value:
            switch.set_value(True)  # its handler activates the overlay and turns the others off

    def _activate_pending_overlay(self) -> None:
        """Turn on the overlay picked (or linked) before a tile was streamed."""
        name, self._pending_overlay = self._pending_overlay, None
        if not name or self._active_overlay or name not in self._overlay_switches:
            return
        if self._overlay_select is not None and self._overlay_select.value != name:
            self._suppress_overlay_events = True
            try:
                self._overlay_select.set_value(name)
            finally:
                self._suppress_overlay_events = False
        self._show_overlay_section(name)
        self._overlay_switches[name].set_value(True)

    # ------------------------------------------------------------------
    # Shareable links
    # ------------------------------------------------------------------

    def _push_url(self, **params: Optional[str]) -> None:
        """Keep a part of the current view in the address bar, so the link reopens it. None removes it."""
        try:
            ui.run_javascript(f"window.vrestoUrl && window.vrestoUrl.set({json.dumps(params)})")
        except Exception:  # noqa: BLE001 - outside a page (tests, shutdown) there is no address bar
            pass

    async def _restore_shared_view(self) -> None:
        """Stream the tile a link names, on the linked date when it is among the tile's scenes."""
        view = self._shared_view
        if not view.tile:
            return
        self._add_message(f"🔗 Opening the linked tile {view.tile}…")
        try:
            products = await self._get_tile_candidates(view.tile, limit=TILE_PRODUCT_CHOICES)
        except Exception as exc:  # noqa: BLE001 - the catalogue being down must not break the page
            logger.warning(f"Could not look up the linked tile {view.tile}: {exc}")
            products = []
        match = next((p for p in products if _scene_keys(p)[0] == view.scene_date), None)
        if match is None:
            await self._handle_tile_click(view.tile)  # streams the only scene, or lets the user pick
            return
        # A link that also sets the map view keeps it; otherwise the map zooms to the tile.
        self._fit_streamed_tile = view.center is None
        try:
            await self._commit_tile_selection(view.tile, match, self._streaming_tile_code)
        finally:
            self._fit_streamed_tile = True

    # ------------------------------------------------------------------
    # Before/after comparison
    # ------------------------------------------------------------------

    async def _open_compare_chooser(self) -> None:
        """Offer the other scenes of the streamed tile in the date range, to compare with the one on the map."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return
        try:
            products = await self._get_tile_candidates(tile_code, limit=TILE_PRODUCT_CHOICES + 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Compare: scene lookup failed for {tile_code}: {exc}")
            products = []
        others = [p for p in products if _scene_keys(p)[0] != self._streaming_date][:TILE_PRODUCT_CHOICES]
        if not others:
            ui.notify("No other date of this tile in the date range. Widen the dates to compare.", position="top", type="warning")
            return

        with ui.dialog() as dialog, ui.card().classes("w-[30rem] max-w-full"):
            ui.label(f"Compare {tile_code} ({_pretty_day(self._streaming_date or '')}) with").classes("text-base font-semibold")
            ui.label("The date you pick shows left of a divider you can drag across the map.").classes("text-xs text-gray-500 mb-2")
            with ui.column().classes("w-full gap-2"):
                for product in others:
                    with ui.row().classes("w-full items-center gap-3 p-2 rounded-md bg-gray-50 dark:bg-slate-800"):
                        if product.source == public_s2.PUBLIC_SOURCE and product.thumbnail_url:
                            ui.image(product.thumbnail_url).props("fit=cover").classes("w-16 h-16 rounded")
                        with ui.column().classes("gap-0 grow"):
                            ui.label(f"📅 {str(product.sensing_date)[:10]}").classes("text-sm")
                            if product.cloud_cover is not None:
                                ui.label(f"☁️ {product.cloud_cover:.0f}% cloud").classes("text-xs text-gray-500")

                        async def _pick(p=product) -> None:
                            dialog.close()
                            await self._start_compare(p)

                        ui.button("Compare", on_click=_pick).props("size=sm color=deep-orange unelevated")
            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat size=sm")
        dialog.open()

    async def _start_compare(self, product: ProductInfo) -> None:
        """Stream ``product`` for the streamed tile and show it left of a swipe divider."""
        tile_code = self._streaming_tile_code
        if not tile_code or not self.map_widget_obj:
            return
        self._stop_compare()
        ui.notify(f"Loading {str(product.sensing_date)[:10]} to compare…", position="top", type="info", spinner=True)
        cog_path = await self._fetch_tci(tile_code, product)
        if not cog_path or tile_code != self._streaming_tile_code:
            return  # failed (already reported), or the user moved to another tile meanwhile
        layer_name = f"compare_{tile_code}"
        url = await asyncio.to_thread(tile_pool.get_or_create, self._pool_name(layer_name), cog_path)
        if not url or tile_code != self._streaming_tile_code:
            return
        compare_day = _scene_keys(product)[0]
        self.map_widget_obj.add_tile_layer(url, name=layer_name)
        self.map_widget_obj.enable_swipe(layer_name, _pretty_day(compare_day), _pretty_day(self._streaming_date or ""))
        self._compare_layer, self._compare_date = layer_name, compare_day
        if self._compare_stop_button is not None:
            self._compare_stop_button.set_visibility(True)
        self._add_message(f"↔️ Comparing {tile_code}: {_pretty_day(compare_day)} (left) with {_pretty_day(self._streaming_date or '')} (right)")

    def _stop_compare(self) -> None:
        """Remove the second date and its divider."""
        if self._compare_stop_button is not None:
            self._compare_stop_button.set_visibility(False)
        if not self._compare_layer:
            return
        layer_name, self._compare_layer, self._compare_date = self._compare_layer, None, None
        tile_pool.remove(self._pool_name(layer_name))
        if self.map_widget_obj:
            self.map_widget_obj.disable_swipe()
            self.map_widget_obj.remove_tile_layer(layer_name)

    # ------------------------------------------------------------------
    # Point inspector
    # ------------------------------------------------------------------

    def _on_inspect_toggle(self, e):
        """Handle the *Inspect values* switch."""
        self._set_inspect_enabled(bool(e.value))

    def _set_inspect_enabled(self, enabled: bool):
        """Apply the inspector state to the map: click mode on or off, popup closed when off."""
        self._inspect_request_id += 1  # a sample still in flight must not open a popup after this
        self._inspect_enabled = enabled
        if enabled and self._pixel_history is not None:
            self._pixel_history.set_picking(False)  # the map click can serve only one of them
        if self.map_widget_obj:
            self.map_widget_obj.set_inspect_mode(self._map_click_mode())
            if not enabled:
                self.map_widget_obj.clear_point_popup()

    def _map_click_mode(self) -> bool:
        """Whether map clicks go to the tab (inspector or pixel history) rather than selecting tiles."""
        return self._inspect_enabled or bool(self._pixel_history and self._pixel_history.picking)

    # ------------------------------------------------------------------
    # Pixel history
    # ------------------------------------------------------------------

    def _on_pixel_history_mode(self, enabled: bool) -> None:
        """Hand the map click to the pixel-history panel, or give it back."""
        if enabled and self._inspect_enabled and self._inspect_switch is not None:
            self._inspect_switch.set_value(False)  # its on_change handler turns the inspector off
        if self.map_widget_obj:
            self.map_widget_obj.set_inspect_mode(self._map_click_mode())
            if not enabled:
                self.map_widget_obj.clear_point_popup()
        if enabled:
            self._add_message("📈 Pixel history: click a point on the map to chart it over time.")

    async def _handle_history_click(self, lat: float, lon: float) -> None:
        """Chart the clicked point and mark it on the map."""
        from vresto.services.sampling import wrap_longitude

        self._history_click = (lat, lon)
        wrapped_lon = wrap_longitude(lon)
        if self.map_widget_obj:
            self.map_widget_obj.show_point_popup(lat, lon, _history_popup_html(lat, wrapped_lon, "Loading history…"))
        await self._pixel_history.load(lat, wrapped_lon)

    def _on_history_loaded(self, lat: float, lon: float, summary: str) -> None:
        """Update the point's popup with the loaded series' summary."""
        self._add_message(f"📈 {lat:.4f}, {lon:.4f}: {summary}")
        if self.map_widget_obj and self._history_click is not None:
            click_lat, click_lon = self._history_click
            self.map_widget_obj.show_point_popup(click_lat, click_lon, _history_popup_html(lat, lon, summary))

    async def _show_history_scene(self, observation) -> None:
        """Stream the scene of a pixel-history date onto the map, keeping the current view."""
        self._fit_streamed_tile = False
        try:
            await self._commit_tile_selection(observation.tile_code, observation.product, self._streaming_tile_code)
        finally:
            self._fit_streamed_tile = True

    def _reset_inspector(self):
        """Turn the inspector off and disable its switch until an overlay has loaded again."""
        self._inspect_request_id += 1
        self._set_control_enabled(self._inspect_switch, False)
        if self._inspect_enabled and self._inspect_switch is not None:
            self._inspect_switch.set_value(False)  # its on_change handler then switches the map mode off

    def _update_inspector_availability(self, overlay_name: str):
        """Enable the switch when the active overlay has loaded and its raw values can be read."""
        if overlay_name != self._active_overlay:
            return  # a slow load of an overlay the user has since moved away from

        fetched = self._overlay_fetched.get(overlay_name)
        inspectable = bool(self._overlay_specs[overlay_name].decode and fetched and fetched.aligned_path)
        self._set_control_enabled(self._inspect_switch, inspectable)

    @staticmethod
    def _describe_sample(spec: OverlaySpec, sample) -> tuple[str, bool]:
        """Return the popup text for a sample, and whether it is a value (not a reason there is none)."""
        if sample.status == "outside":
            return INSPECT_OUTSIDE_TEXT, False
        value = spec.decode(sample.raw) if sample.status == "ok" else None
        if value is None:
            return spec.nodata_text or INSPECT_NODATA_TEXT, False
        return value, True

    async def _handle_map_click(self, lat: float, lon: float):
        """Read the active overlay's value at a clicked point and show it in a popup.

        Only acts while *Inspect values* is on. The value comes from the overlay's aligned
        raster (the raw values), not from the colourised one drawn on the map. While the
        pixel-history panel is picking, the click charts the point instead.
        """
        if self._pixel_history is not None and self._pixel_history.picking:
            await self._handle_history_click(lat, lon)
            return

        overlay_name = self._active_overlay
        fetched = self._overlay_fetched.get(overlay_name) if overlay_name else None
        if not (self._inspect_enabled and fetched and fetched.aligned_path and self.map_widget_obj):
            return
        spec = self._overlay_specs[overlay_name]
        if spec.decode is None:
            return

        # Imported lazily with the other raster-heavy modules the UI avoids loading up front.
        from vresto.services.sampling import sample_raster, wrap_longitude

        self._inspect_request_id += 1
        request_id = self._inspect_request_id
        wrapped_lon = wrap_longitude(lon)  # Leaflet reports unwrapped longitudes after panning across the antimeridian

        try:
            sample = await asyncio.to_thread(sample_raster, fetched.aligned_path, lat, wrapped_lon)
        except Exception as exc:
            logger.warning(f"Could not sample {spec.label} at ({lat:.5f}, {wrapped_lon:.5f}): {exc}")
            text, is_value = INSPECT_ERROR_TEXT, False
        else:
            text, is_value = self._describe_sample(spec, sample)

        if request_id != self._inspect_request_id:
            return  # superseded by a newer click, or the inspector was turned off or reset meanwhile

        html = build_point_popup_html(spec, text, lat, wrapped_lon, self._overlay_legend_suffixes.get(overlay_name, ""), show_resolution=is_value)
        self.map_widget_obj.show_point_popup(lat, lon, html)  # at the clicked position, wrapped or not

    async def _on_lc100_year_change(self, e):
        """Change the LC100 epoch year and reload the overlay if active."""
        self._lc100_year = str(e.value or "2019")
        if self._get_enabled_overlay() == "lc100" and self._streaming_tile_code:
            self._remove_overlay_layer("lc100")
            await self._load_overlay("lc100")

    async def _on_tcd_year_change(self, e):
        """Change the TCD year and reload the overlay if active."""
        self._tcd_year = str(e.value or "2020")
        if self._get_enabled_overlay() == "tcd" and self._streaming_tile_code:
            self._remove_overlay_layer("tcd")
            await self._load_overlay("tcd")

    async def _on_lst_time_change(self, e):
        """Change the selected hourly LST scene and reload if active."""
        selected_label = str(e.value or "")
        selected_timestamp = self._lst_time_options_by_label.get(selected_label)
        if not selected_timestamp:
            return

        self._lst_user_selected_timestamp = selected_timestamp
        self._lst_selected_timestamp_label = selected_label
        if self._get_enabled_overlay() == "lst" and self._streaming_tile_code:
            self._remove_overlay_layer("lst")
            await self._load_overlay("lst")

    def _add_message(self, text: str):
        """Add a message to the activity log."""
        if self.messages_column:
            try:
                with self.messages_column:
                    ui.label(text).classes("text-sm text-gray-700 break-words")
            except Exception:
                pass

    async def _handle_search(self, params: dict):
        """Handle the search action.

        Args:
            params: Dict with keys: collection, product_level, max_cloud_cover, max_results
        """

        def add_message(text: str):
            """Add a message to the activity log."""
            if self.messages_column:
                with self.messages_column:
                    ui.label(text).classes("text-sm text-gray-700 break-words")

        # Without a drawn area, search what the map shows.
        bbox = self.current_state["bbox"]
        if bbox is None:
            bbox = await self.map_widget_obj.get_view_bbox() if self.map_widget_obj else None
            if bbox is None:
                ui.notify("⚠️ Could not read the map view. Draw an area on the map, then search.", position="top", type="warning")
                add_message("⚠️ Search failed: no area to search")
                return
            add_message("🗺️ Searching the visible map area (draw a shape to search a specific area)")

        if self.current_state["date_range"] is None:
            ui.notify(
                "⚠️ Please select a date range",
                position="top",
                type="warning",
            )
            add_message("⚠️ Search failed: No date range selected")
            return

        # Extract parameters
        date_range = self.current_state["date_range"]
        start_date = date_range.get("from", "")
        end_date = date_range.get("to", start_date)

        collection = params.get("collection")
        product_level = params.get("product_level")
        max_cloud_cover = params.get("max_cloud_cover")
        max_results = params.get("max_results")

        # Validate product level support
        supported_levels = COLLECTION_PRODUCT_LEVELS.get(collection, [])

        if product_level not in supported_levels:
            warning_msg = f"⚠️ {collection} does not support product level: {product_level}. Supported levels: {', '.join(supported_levels)}"
            ui.notify(
                warning_msg,
                position="top",
                type="warning",
            )
            add_message(warning_msg)

        ui.notify(
            f"🔍 Searching {collection} products ({product_level})...",
            position="top",
            type="info",
        )
        add_message(f"🔍 Searching {collection} products ({product_level}) for {start_date} to {end_date}")

        # Clear results
        results_display = params.get("results_display")
        if results_display:
            results_display.clear()
            with results_display:
                ui.spinner(size="lg")
                ui.label("Searching...").classes("text-gray-600")

        await asyncio.sleep(0.1)

        try:
            # Perform search
            catalog = CatalogSearch(auth=get_shared_auth())

            # Convert bbox if needed
            try:
                if isinstance(bbox, (tuple, list)):
                    min_lon, min_lat, max_lon, max_lat = bbox
                    bbox = BoundingBox(west=min_lon, south=min_lat, east=max_lon, north=max_lat)
            except Exception:
                logger.exception("Failed to coerce bbox into BoundingBox")

            # Run the OData HTTP call in a worker thread so the NiceGUI
            # event loop stays responsive (otherwise a multi-second blocking
            # request can stall the WebSocket heartbeat and trigger a full
            # browser reconnect / page reset).
            products = await asyncio.to_thread(
                catalog.search_products,
                bbox=bbox,
                start_date=start_date,
                end_date=end_date,
                collection=collection,
                max_cloud_cover=max_cloud_cover if collection in ["SENTINEL-2", "SENTINEL-3", "SENTINEL-5P"] else None,
                max_results=int(max_results),
                product_level=product_level,
            )

            # Filter by product level
            filtered_products = self._filter_by_level(products, product_level, collection)
            self.current_state["products"] = filtered_products
            self._results_total = len(products)

            # Display results
            if results_display:
                self._render_results(results_display)
                if not filtered_products:
                    ui.notify(
                        "No products found with selected level",
                        position="top",
                        type="warning",
                    )
                    add_message("❌ No products found with selected level")
                else:
                    ui.notify(
                        f"✅ Found {len(filtered_products)} products",
                        position="top",
                        type="positive",
                    )
                    add_message(f"✅ Found {len(filtered_products)} products (from {len(products)} total)")
                    logger.info(f"Search completed: {len(filtered_products)} products found (filtered from {len(products)})")

        except Exception as e:
            logger.error(f"Search failed: {e}")
            if self.map_widget_obj:
                self.map_widget_obj.clear_footprints()
            if results_display:
                results_display.clear()
                with results_display:
                    ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
            ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
            add_message(f"❌ Search error: {str(e)}")

    def _render_results(self, results_display) -> None:
        """List the search results in the chosen order and draw their footprints on the map, numbered alike."""
        products = sort_products(self.current_state["products"], self._result_sort)
        results_display.clear()
        with results_display:
            if not products:
                ui.label("No products found with selected level").classes("text-gray-500 italic")
            else:
                with ui.row().classes("w-full items-center justify-between no-wrap mb-1"):
                    ui.label(f"Found {len(products)} products (filtered from {self._results_total} total)").classes("text-sm font-semibold text-green-600")

                def _on_sort(e) -> None:
                    self._result_sort = e.value
                    self._render_results(results_display)

                if len(products) > 1:
                    ui.select(options=RESULT_SORTS, value=self._result_sort, on_change=_on_sort).props("dense outlined options-dense").classes("w-full text-xs mb-1")
                for i, product in enumerate(products, 1):
                    self._create_product_card(results_display, i, product, self.messages_column)
        if self.map_widget_obj:
            if products:
                self.map_widget_obj.set_footprints(footprints_feature_collection(products))
            else:
                self.map_widget_obj.clear_footprints()

    def _filter_by_level(self, products: list, level_filter: str, collection: str = "") -> list:
        """Filter products by processing level.

        Args:
            products: List of ProductInfo objects
            level_filter: "L1C", "L2A", etc.
            collection: Collection name (to handle different naming conventions)

        Returns:
            Filtered list of ProductInfo objects
        """
        # For these collections, skip client-side filtering — server-side filtering
        # (via OData name contains or STAC collection) already scopes results.
        if collection in ("SENTINEL-3", "SENTINEL-1", "SENTINEL-5P"):
            return products

        filtered = []
        for product in products:
            if level_filter in product.name:
                filtered.append(product)

        return filtered

    def _create_product_card(self, container, index: int, product, messages_column):
        """Create a product result card with quicklook/metadata buttons."""
        collection = getattr(product, "collection", "").upper()
        caps = get_product_capabilities(collection)

        with container:
            card = ui.card().classes(f"w-full p-3 bg-gray-50 shadow-sm rounded-md vresto-result-card vresto-result-{index}")
            # Hovering a card highlights its footprint on the map (and the other way round), in the browser.
            card.on("mouseenter", js_handler=f"() => window.vrestoFootprint && window.vrestoFootprint.highlight({index}, true)")
            card.on("mouseleave", js_handler=f"() => window.vrestoFootprint && window.vrestoFootprint.highlight({index}, false)")
            with card:
                with ui.row().classes("w-full items-start no-wrap gap-1"):
                    ui.label(f"{index}. {getattr(product, 'display_name', product.name)}").classes("text-xs font-mono break-all grow")
                    if getattr(product, "footprint", None):
                        ui.button(icon="center_focus_strong").props("flat round dense size=sm").on("click", js_handler=f"() => window.vrestoFootprint && window.vrestoFootprint.zoomTo({index})").tooltip("Show on map")
                ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600")
                ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600")
                if product.cloud_cover is not None:
                    ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600")

                # Per-collection capability summary
                if caps.quicklook_available is True and caps.metadata_available and caps.visualization_available:
                    ui.label("✅ Full support: quicklook, metadata, visualization").classes("text-xs text-green-600 mt-1")
                else:
                    parts = []
                    if caps.quicklook_available is True:
                        parts.append("quicklook ✅")
                    elif caps.quicklook_available is None:
                        parts.append("quicklook ⚠️")
                    else:
                        parts.append("quicklook ❌")
                    parts.append("metadata ✅" if caps.metadata_available else "metadata ❌")
                    parts.append("visualization ✅" if caps.visualization_available else "visualization ❌")
                    ui.label("  |  ".join(parts)).classes("text-xs text-orange-600 mt-1")

                # Buttons for quicklook and metadata
                with ui.row().classes("w-full gap-2 mt-2"):
                    ql_disabled = caps.quicklook_available is False
                    ql_tooltip = caps.quicklook_note if (caps.quicklook_available is None or ql_disabled) else ""
                    ql_btn = (
                        ui
                        .button(
                            "🖼️ Quicklook",
                            on_click=lambda p=product: self.on_quicklook(p, messages_column),
                        )
                        .props("outline size=sm")
                        .classes("text-xs flex-1")
                    )
                    if ql_disabled:
                        ql_btn.props(add="disable")
                    if ql_tooltip:
                        ql_btn.tooltip(ql_tooltip)

                    meta_disabled = not caps.metadata_available
                    meta_btn = (
                        ui
                        .button(
                            "📋 Metadata",
                            on_click=lambda p=product: self.on_metadata(p, messages_column),
                        )
                        .props("outline size=sm")
                        .classes("text-xs flex-1")
                    )
                    if meta_disabled:
                        meta_btn.props(add="disable")
                        meta_btn.tooltip(caps.metadata_note)

                    if self.on_download is not None and supports_band_download(product):
                        ui.button(
                            "⬇️ Download",
                            on_click=lambda p=product: self.on_download(p),
                        ).props("unelevated size=sm color=deep-orange").classes("text-xs flex-1").tooltip("Open this product in the Download tab")
