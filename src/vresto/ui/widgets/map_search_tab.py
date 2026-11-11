"""Map search tab widget combining map, date picker, and search controls."""

import asyncio
import json
from datetime import date as _date
from datetime import timedelta
from typing import Any, Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api import BoundingBox, CatalogSearch
from vresto.api.auth import get_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
    get_product_capabilities,
    supports_band_download,
)
from vresto.services import geocode, public_s2
from vresto.services.mgrs_grid import is_available as mgrs_available
from vresto.services.sentinel_stream import (
    DEFAULT_TCI_RESOLUTION,
    TciResolution,
)
from vresto.ui.footprints import RESULT_SORTS, footprints_feature_collection, sort_products
from vresto.ui.overlays import (
    OVERLAY_NAMES,
    OVERLAY_REGISTRY,
    FetchedOverlay,
    OverlaySpec,
)
from vresto.ui.url_state import SharedView
from vresto.ui.widgets.activity_log import ActivityLogWidget
from vresto.ui.widgets.date_picker import DatePickerWidget
from vresto.ui.widgets.map_search_density import GridDensityMixin
from vresto.ui.widgets.map_search_inspector import PointInspectorMixin
from vresto.ui.widgets.map_search_overlays import OVERLAY_NONE, OverlaysMixin
from vresto.ui.widgets.map_search_tiles import TILE_PRODUCT_CHOICES, TileStreamingMixin, _scene_keys
from vresto.ui.widgets.map_widget import MapWidget
from vresto.ui.widgets.pixel_history_panel import PixelHistoryPanel
from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget


class MapSearchTab(OverlaysMixin, GridDensityMixin, TileStreamingMixin, PointInspectorMixin):
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

    # ------------------------------------------------------------------
    # Product-count colouring of the grid
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Overlay picker
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Point inspector
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pixel history
    # ------------------------------------------------------------------

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
