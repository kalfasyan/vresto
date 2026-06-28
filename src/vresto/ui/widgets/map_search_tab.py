"""Map search tab widget combining map, date picker, and search controls."""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from datetime import timezone
from typing import Any, Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api import BoundingBox, CatalogSearch, ProductInfo
from vresto.api.auth import get_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
    get_product_capabilities,
)
from vresto.api.stac_assets import parse_date_like
from vresto.services.mgrs_grid import compute_visible_tiles_geojson
from vresto.services.mgrs_grid import is_available as mgrs_available
from vresto.services.sentinel_stream import (
    DEFAULT_TCI_RESOLUTION,
    TciResolution,
    sentinel_stream_service,
)
from vresto.services.tiles import tile_pool
from vresto.ui.widgets.activity_log import ActivityLogWidget
from vresto.ui.widgets.date_picker import DatePickerWidget
from vresto.ui.widgets.legend import build_continuous_legend_html, build_legend_html
from vresto.ui.widgets.map_widget import MapWidget
from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget

# Maximum number of latest products surfaced in the tile-click chooser dialog.
TILE_PRODUCT_CHOICES = 5


@dataclass(frozen=True)
class OverlaySpec:
    """Declarative specification for one tile overlay.

    Attributes:
        name: Machine key used for state, layer names, and switches.
        title: Human-readable title shown in the expansion header.
        description: Short helper text shown inside the expansion.
        icon: Material icon name for the expansion header.
        opacity: Default opacity (0.2–1.0).
        info: Attribution / source tooltip text.
        category: Sidebar group heading (used to render grouped section labels).
        legend_type: ``"discrete"`` (colour swatches) or ``"continuous"`` (gradient ramp).
        vmin: Min value for continuous legend (e.g. 0.0).
        vmax: Max value for continuous legend (e.g. 50.0).
        units: Physical unit label shown in the continuous legend (e.g. "°C").
        coverage_note: Human-readable note shown as a badge when the overlay has
            spatial/temporal coverage limits (e.g. "Europe only").
        extra_controls: Optional callable that builds overlay-specific settings.
        is_available: Optional callable(tile_bottom_lat, tile_top_lat) -> bool used
            to disable the overlay outside product coverage (e.g. TCD).
    """

    name: str
    title: str
    description: str
    icon: str
    opacity: float
    info: str
    category: str = "Other"
    legend_type: str = "discrete"
    vmin: float = 0.0
    vmax: float = 1.0
    units: str = ""
    coverage_note: str = ""
    extra_controls: Optional[Callable[[], None]] = None
    is_available: Optional[Callable[[float, float], bool]] = None


# Central registry of tile overlays.  Adding a new overlay now only requires
# appending a spec here and implementing the matching `_load_<name>_overlay`
# method on MapSearchTab.
OVERLAY_REGISTRY: tuple[OverlaySpec, ...] = (
    # ── Land Cover ─────────────────────────────────────────────────────────
    OverlaySpec(
        name="worldcover",
        title="WorldCover 2021",
        description="ESA global land cover classes for quick context.",
        icon="public",
        opacity=0.7,
        info="Source: ESA WorldCover. Creator: European Space Agency (ESA). Website: https://esa-worldcover.org",
        category="Land Cover",
    ),
    OverlaySpec(
        name="lcm",
        title="LCM 2020",
        description="Copernicus Dynamic Land Cover Map for the selected tile.",
        icon="map",
        opacity=0.7,
        info="Source: Copernicus Dynamic Land Cover Map. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu",
        category="Land Cover",
    ),
    OverlaySpec(
        name="lc100",
        title="Global LC 100m",
        description="Copernicus yearly global land cover classification.",
        icon="layers",
        opacity=0.7,
        info="Source: Copernicus Global Land Cover 100 m. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/lc",
        category="Land Cover",
    ),
    OverlaySpec(
        name="tcd",
        title="Tree Cover Density",
        description="Pantropical yearly tree cover density (disabled outside tropical coverage).",
        icon="park",
        opacity=0.7,
        info="Source: Tree Cover Density 10 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu",
        category="Land Cover",
        coverage_note="Pantropical only",
    ),
    # ── Terrain ─────────────────────────────────────────────────────────────
    OverlaySpec(
        name="dem",
        title="DEM terrain",
        description="Relative terrain shading for the selected tile.",
        icon="terrain",
        opacity=0.75,
        info="Source: Copernicus DEM GLO-30. Creator: Copernicus Programme. Website: https://dataspace.copernicus.eu",
        category="Terrain",
        legend_type="continuous",
        vmin=0.0,
        vmax=100.0,
        units="relative",
    ),
    # ── Vegetation & Productivity ────────────────────────────────────────────
    OverlaySpec(
        name="ndvi",
        title="NDVI climatology",
        description="Long-term NDVI mean for the streamed tile date dekad.",
        icon="eco",
        opacity=0.75,
        info="Source: Copernicus Global Land Service NDVI Long-Term Statistics. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/ndvi",
        category="Vegetation & Productivity",
        legend_type="continuous",
        vmin=0.0,
        vmax=0.9,
        units="NDVI",
    ),
    OverlaySpec(
        name="fapar",
        title="FAPAR",
        description="Nearest 10-daily FAPAR snapped to the streamed acquisition date.",
        icon="grass",
        opacity=0.75,
        info=("Source: Copernicus Global Land Service Fraction of Absorbed Photosynthetically Active Radiation (FAPAR) 300 m. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/fapar"),
        category="Vegetation & Productivity",
        legend_type="continuous",
        vmin=0.0,
        vmax=1.0,
        units="FAPAR",
    ),
    OverlaySpec(
        name="dmp",
        title="Dry Matter Prod.",
        description="Nearest 10-daily dry matter productivity snapped to the streamed date.",
        icon="spa",
        opacity=0.75,
        info="Source: Copernicus Global Land Service Dry Matter Productivity 300 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/dmp",
        category="Vegetation & Productivity",
        legend_type="continuous",
        vmin=0.0,
        vmax=150.0,
        units="kg/ha/day",
    ),
    # ── Thermal ──────────────────────────────────────────────────────────────
    OverlaySpec(
        name="lst",
        title="LST hourly",
        description="Nearest hourly land surface temperature snapped to the streamed acquisition time.",
        icon="device_thermostat",
        opacity=0.75,
        info=(
            "Source: Copernicus Global Land Service Land Surface Temperature. "
            "Creator: Copernicus Global Land Service. Website: "
            "https://land.copernicus.eu/en/products/temperature-and-reflectance/"
            "land-surface-temperature. Times are shown in Europe/Brussels local "
            "time (CET/CEST)."
        ),
        category="Thermal",
        legend_type="continuous",
        vmin=-20.0,
        vmax=50.0,
        units="°C",
    ),
    # ── Water & Soil ─────────────────────────────────────────────────────────
    OverlaySpec(
        name="ssm",
        title="Soil Moisture",
        description="Nearest daily surface soil moisture.",
        icon="water_drop",
        opacity=0.75,
        info="Source: Copernicus Land Monitoring Service Surface Soil Moisture 1 km (Europe). Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/en/products/soil-moisture",
        category="Water & Soil",
        legend_type="continuous",
        vmin=0.0,
        vmax=100.0,
        units="% sat.",
        coverage_note="Europe only",
    ),
    OverlaySpec(
        name="swi",
        title="Soil Water Index",
        description="Nearest daily soil water index (root-zone proxy, T=10).",
        icon="opacity",
        opacity=0.75,
        info="Source: Copernicus Global Land Service Soil Water Index 12.5 km. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/swi",
        category="Water & Soil",
        legend_type="continuous",
        vmin=0.0,
        vmax=100.0,
        units="% sat.",
    ),
    OverlaySpec(
        name="wb",
        title="Water Bodies",
        description="Nearest monthly surface water-body extent snapped to the streamed date.",
        icon="water",
        opacity=0.7,
        info="Source: Copernicus Global Land Service Water Bodies 100 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/wb",
        category="Water & Soil",
        coverage_note="Data from Oct 2020",
    ),
    # ── Hazards ───────────────────────────────────────────────────────────────
    OverlaySpec(
        name="ba",
        title="Burned Area",
        description="Nearest monthly burned area (day-of-burn) snapped to the streamed date.",
        icon="local_fire_department",
        opacity=0.8,
        info="Source: Copernicus Global Land Service Burned Area 300 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/ba",
        category="Hazards",
        legend_type="continuous",
        vmin=1.0,
        vmax=366.0,
        units="day of year",
    ),
)


OVERLAY_NAMES: tuple[str, ...] = tuple(spec.name for spec in OVERLAY_REGISTRY)


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
    ):
        """Initialize the Map Search tab.

        Args:
            on_quicklook: Callback(product, messages_column) for quicklook requests
            on_metadata: Callback(product, messages_column) for metadata requests
        """
        self.on_quicklook = on_quicklook or (lambda p, col: None)
        self.on_metadata = on_metadata or (lambda p, col: None)

        # Default date range: rolling last 30 days.
        _today = _date.today()
        _default_to = _today.strftime("%Y-%m-%d")
        _default_from = (_today - timedelta(days=30)).strftime("%Y-%m-%d")

        # State
        self.current_state = {
            "bbox": None,
            "date_range": {"from": _default_from, "to": _default_to},
            "products": [],
        }

        # UI elements
        self.messages_column = None
        self.map_widget = None
        self.map_widget_obj = None
        self.results_display = None
        self.date_picker = None

        # Grid & streaming state
        self._grid_enabled = True
        self._streaming_tile_code: Optional[str] = None
        self._streaming_date: Optional[str] = None
        self._streaming_timestamp: Optional[str] = None
        self._active_overlay: Optional[str] = None
        self._suppress_overlay_events = False
        self._tcd_available_for_tile = True
        self._tcd_year = "2020"
        self._tcd_year_select = None
        self._lc100_year = "2019"
        self._lc100_year_select = None
        self._lst_time_select = None
        self._lst_time_options_by_label = {}
        self._lst_user_selected_timestamp: Optional[str] = None
        self._lst_selected_timestamp_label: Optional[str] = None

        # Snapped source dates for the temporal STAC overlays, keyed by overlay
        # name, so legends can show the actual product date selected.
        self._overlay_source_dates: dict[str, str] = {}

        # Per-overlay booleans are derived from the registry so adding a new
        # overlay does not require touching this block.
        for name in OVERLAY_NAMES:
            setattr(self, f"_{name}_enabled", False)

        self._overlay_layer_urls: dict[str, str] = {}
        self._overlay_switches: dict[str, Any] = {}
        self._overlay_sliders: dict[str, Any] = {}
        self._overlay_expansions: dict[str, Any] = {}
        self._overlay_section_rows: dict[str, Any] = {}  # outer row per section (for filter visibility)
        self._overlay_opacity_by_name = {spec.name: spec.opacity for spec in OVERLAY_REGISTRY}
        self._overlay_titles = {spec.name: spec.title for spec in OVERLAY_REGISTRY}
        self._overlay_info_by_name = {spec.name: spec.info for spec in OVERLAY_REGISTRY}
        self._overlay_specs: dict[str, OverlaySpec] = {spec.name: spec for spec in OVERLAY_REGISTRY}
        # Phase 1: active-overlay status chip and how-to stepper references
        self._active_overlay_chip: Any = None
        self._how_to_card: Any = None
        # Phase 2: filter state
        self._overlay_filter_text: str = ""
        # Phase 3: per-overlay coverage-badge labels
        self._overlay_coverage_labels: dict[str, Any] = {}
        # Default to the fastest L2A TCI resolution (60 m ≈ 1830² px). The
        # user can opt into 10 m via the sidebar switch when they need more
        # detail — it's ~36× more data to decode.
        self._tci_resolution: TciResolution = DEFAULT_TCI_RESOLUTION

    def create(self):
        """Create and return the Map Search tab UI."""
        with ui.row().classes("w-full gap-6"):
            # Left sidebar: Date picker and activity log
            self._create_sidebar()

            # Map with draw controls and grid/streaming callbacks
            map_widget_obj = MapWidget(
                center=(50.8503, 4.3517),
                zoom=7,
                on_bbox_update=lambda bbox: self.current_state.update({"bbox": bbox}),
                on_tile_click=self._handle_tile_click,
                on_moveend=self._handle_moveend,
            )
            self.map_widget_obj = map_widget_obj
            self.map_widget = map_widget_obj.create(self.messages_column)

            # Wire moveend for grid refresh after map is created
            map_widget_obj.setup_moveend()

            # Phase 4: add scale bar, coordinate readout, and basemap switcher.
            map_widget_obj.add_map_chrome()

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
        with ui.column().classes("w-80"):
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

    def _on_date_change(self, start_date: str, end_date: str):
        """Handle date range changes from the date picker."""
        self.current_state["date_range"] = {"from": start_date, "to": end_date}

    # ------------------------------------------------------------------
    # Grid & Streaming controls
    # ------------------------------------------------------------------

    def _create_streaming_controls(self):
        """Create the MGRS grid toggle and overlay controls section."""
        config = CopernicusConfig()
        has_creds = config.has_static_s3_credentials()

        with ui.card().classes("w-full p-3 mt-2"):
            with ui.column().classes("w-full gap-2"):
                with ui.row().classes("w-full items-start justify-between"):
                    ui.label("🛰️ Tile Streaming").classes("text-sm font-semibold")
                    self._tile_status_label = ui.label("No tile selected").classes("text-xs text-gray-500")

                if not has_creds:
                    ui.label("Configure S3 credentials to enable tile streaming").classes("text-xs text-gray-500 italic")
                    return

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
                        ("4", "Toggle an overlay layer below"),
                    ]
                    for num, text in steps:
                        with ui.row().classes("items-center gap-1 mb-0.5"):
                            ui.badge(num).props("color=blue").classes("text-[10px] min-w-[16px]")
                            ui.label(text).classes("text-[11px] text-blue-800 dark:text-blue-200")
                self._how_to_card = how_to_card

                ui.label("Base layer").classes("text-[11px] font-medium uppercase tracking-wide text-gray-500")
                grid_switch = ui.switch("Show MGRS Grid", value=True, on_change=self._toggle_grid)
                grid_switch.classes("text-xs")

                # High-resolution opt-in. Default off — 60 m TCI loads in ~1-3 s
                # vs ~15-20 s for 10 m, and looks the same at MGRS-tile zoom.
                self._hires_switch = ui.switch(
                    "High resolution (10 m)",
                    value=False,
                    on_change=self._toggle_hires,
                )
                self._hires_switch.classes("text-xs")
                self._hires_switch.tooltip("Off: stream 60 m TCI (~1–3 s, sharp at MGRS-tile zoom).\nOn: stream 10 m TCI (~15–20 s, full detail when zoomed in).")

                self._overlay_status_label = ui.label("Click an MGRS tile to enable overlays.").classes("text-xs text-gray-500")

                # Phase 1 – active-overlay chip (hidden until an overlay is active).
                with ui.row().classes("w-full items-center gap-1"):
                    self._active_overlay_chip = ui.badge("").props("color=teal").classes("text-[11px] hidden")

                ui.separator().classes("my-1")
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Overlays").classes("text-xs font-medium")
                    ui.label("One at a time").classes("text-[11px] text-gray-400")

                # Phase 2 – overlay filter input.
                def _on_filter_change(e):
                    self._overlay_filter_text = (e.value or "").strip().lower()
                    self._apply_overlay_filter()

                (
                    ui.input(placeholder="Filter overlays…", on_change=_on_filter_change)
                    .props("dense outlined clearable")
                    .classes("w-full text-xs")
                )

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

                # Phase 2 – group overlays by category.
                current_category: list[str] = [""]  # mutable closure cell
                for spec in OVERLAY_REGISTRY:
                    if spec.category != current_category[0]:
                        current_category[0] = spec.category
                        ui.label(spec.category).classes(
                            "text-[11px] font-semibold uppercase tracking-wide "
                            "text-gray-400 dark:text-slate-400 mt-2 mb-0.5"
                        )
                    self._create_overlay_section(
                        spec=spec,
                        on_toggle=self._make_toggle_handler(spec.name),
                        extra_controls=extra_controls_by_name.get(spec.name),
                    )

    def _create_overlay_section(
        self,
        spec: "OverlaySpec",
        on_toggle,
        extra_controls: Optional[Callable[[], None]] = None,
    ):
        """Create one collapsible overlay section with lazy settings."""
        overlay_name = spec.name
        title = spec.title
        description = spec.description
        icon = spec.icon

        # Outer container tracked so the filter can show/hide the whole row.
        with ui.element("div").classes("w-full") as section_row:
            expansion = ui.expansion(title, icon=icon).classes("w-full vresto-overlay-expansion")
            self._overlay_expansions[overlay_name] = expansion
            self._overlay_section_rows[overlay_name] = section_row
            with expansion:
                with ui.column().classes("w-full gap-1"):
                    info_text = self._overlay_info_by_name.get(overlay_name)
                    with ui.row().classes("w-full items-start gap-1"):
                        ui.label(description).classes("text-xs text-gray-500 grow")
                        if info_text:
                            self._create_overlay_info_button(info_text)

                    # Phase 3 – coverage note badge (shown/updated on tile load).
                    coverage_note = spec.coverage_note
                    if coverage_note:
                        cov_label = ui.label(f"⚠ {coverage_note}").classes(
                            "text-[11px] text-amber-600 dark:text-amber-400 italic"
                        )
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

    def _apply_overlay_filter(self) -> None:
        """Show/hide overlay section rows based on the current filter text."""
        query = self._overlay_filter_text
        for name, row in self._overlay_section_rows.items():
            title = self._overlay_titles.get(name, "").lower()
            spec = self._overlay_specs.get(name)
            category = (spec.category if spec else "").lower()
            visible = not query or query in title or query in category
            row.set_visibility(visible)

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

        self._tcd_available_for_tile = available
        self._set_tcd_controls_enabled(bool(ref_path) and available)

        if available:
            return

        self._suppress_overlay_events = True
        try:
            self._set_overlay_flag("tcd", False)
            tcd_switch = self._overlay_switches.get("tcd")
            if tcd_switch:
                tcd_switch.set_value(False)
        finally:
            self._suppress_overlay_events = False

        if self._active_overlay == "tcd":
            self._active_overlay = None
            self._remove_overlay_layer("tcd")
            self._clear_overlay_legend()
            self._sync_overlay_sections(None)

        if hasattr(self, "_overlay_status_label"):
            self._overlay_status_label.set_text("Tree Cover Density is unavailable for this tile (pantropical coverage only). Choose another overlay.")

    def _sync_overlay_sections(self, active_overlay: Optional[str]):
        """Keep only the active overlay section expanded and update the active chip."""
        for name, expansion in self._overlay_expansions.items():
            expansion.value = name == active_overlay

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

    # Short layer prefixes used by the map tile pool.
    _OVERLAY_LAYER_PREFIXES: dict[str, str] = {
        "worldcover": "wc",
        "lcm": "lcm",
        "tcd": "tcd",
        "dem": "dem",
        "lc100": "lc100",
        "ndvi": "ndvi",
        "lst": "lst",
        "fapar": "fapar",
        "dmp": "dmp",
        "ssm": "ssm",
        "swi": "swi",
        "ba": "ba",
        "wb": "wb",
    }

    def _overlay_layer_name(self, overlay_name: str, tile_code: Optional[str] = None) -> str:
        """Build the map layer name for a given overlay and tile."""
        current_tile = tile_code or self._streaming_tile_code
        if not current_tile:
            return ""

        return f"{self._OVERLAY_LAYER_PREFIXES[overlay_name]}_{current_tile}"

    def _remove_overlay_layer(self, overlay_name: str, tile_code: Optional[str] = None):
        """Remove a single overlay layer from the map and cache."""
        layer_name = self._overlay_layer_name(overlay_name, tile_code)
        self._overlay_layer_urls.pop(overlay_name, None)
        if not layer_name:
            return

        tile_pool.remove(layer_name)
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

    def _get_overlay_loader(self, overlay_name: str):
        """Map overlay keys to their async loader."""
        return getattr(self, f"_load_{overlay_name}_overlay")

    def _clear_overlay_legend(self):
        """Remove any active legend from the map."""
        if self.map_widget_obj:
            self.map_widget_obj.clear_legend()

    def _show_overlay_legend(self, overlay_name: str):
        """Render the floating legend for the current active overlay."""
        if not self.map_widget_obj:
            return

        if overlay_name == "worldcover":
            from vresto.services.worldcover import WORLDCOVER_CLASS_LEGENDS

            html = build_legend_html("WorldCover 2021", WORLDCOVER_CLASS_LEGENDS, "#1a73e8")
        elif overlay_name == "lcm":
            from vresto.services.lcm import LCM_CLASS_LEGENDS

            html = build_legend_html("LCM 2020", LCM_CLASS_LEGENDS, "#e8710a")
        elif overlay_name == "tcd":
            from vresto.services.tcd import TCD_CLASS_LEGENDS

            html = build_legend_html(f"Tree Cover Density ({self._tcd_year})", TCD_CLASS_LEGENDS, "#2e7d32")
        elif overlay_name == "dem":
            html = build_continuous_legend_html(
                "DEM terrain", vmin=0.0, vmax=100.0, units="relative",
                stops=["#1a3a1a", "#4a7c3f", "#8fbc5f", "#d4c27a", "#c8a06e", "#f0f0f0"],
                title_color="#8d6e63",
            )
        elif overlay_name == "lc100":
            from vresto.services.lc100 import LC100_CLASS_LEGENDS

            html = build_legend_html(f"Global LC 100m ({self._lc100_year})", LC100_CLASS_LEGENDS, "#00695c")
        elif overlay_name == "ndvi":
            from vresto.services.ndvi import ndvi_lts_period_from_date

            month, day = ndvi_lts_period_from_date(self._streaming_date or "20200101")
            html = build_continuous_legend_html(
                f"NDVI climatology ({month}-{day})", vmin=0.0, vmax=0.9, units="NDVI",
                stops=["#d73027", "#fee08b", "#1a9850"],
                title_color="#558b2f",
            )
        elif overlay_name == "lst":
            title = "LST hourly (°C)"
            if self._lst_selected_timestamp_label:
                title = f"LST hourly ({self._lst_selected_timestamp_label})"
            html = build_continuous_legend_html(
                title, vmin=-20.0, vmax=50.0, units="°C",
                stops=["#313695", "#abd9e9", "#ffffbf", "#fdae61", "#d73027"],
                title_color="#d84315",
            )
        elif overlay_name == "fapar":
            selected_date = self._streaming_date or ""
            html = build_continuous_legend_html(
                f"FAPAR ({selected_date})", vmin=0.0, vmax=1.0, units="FAPAR",
                stops=["#ffffcc", "#78c679", "#005a32"],
                title_color="#2e7d32",
            )
        elif overlay_name == "dmp":
            source_date = self._overlay_source_dates.get("dmp", self._streaming_date or "")
            html = build_continuous_legend_html(
                f"Dry Matter Productivity ({source_date})", vmin=0.0, vmax=150.0, units="kg/ha/day",
                stops=["#ffffe5", "#addd8e", "#006837"],
                title_color="#006837",
            )
        elif overlay_name == "ssm":
            source_date = self._overlay_source_dates.get("ssm", self._streaming_date or "")
            html = build_continuous_legend_html(
                f"Soil Moisture ({source_date})", vmin=0.0, vmax=100.0, units="% sat.",
                stops=["#ffffd9", "#7fcdbb", "#081d58"],
                title_color="#0c2c84",
            )
        elif overlay_name == "swi":
            source_date = self._overlay_source_dates.get("swi", self._streaming_date or "")
            html = build_continuous_legend_html(
                f"Soil Water Index ({source_date})", vmin=0.0, vmax=100.0, units="% sat.",
                stops=["#ffffd9", "#7fcdbb", "#225ea8"],
                title_color="#225ea8",
            )
        elif overlay_name == "ba":
            source_date = self._overlay_source_dates.get("ba", self._streaming_date or "")
            html = build_continuous_legend_html(
                f"Burned Area ({source_date})", vmin=1.0, vmax=366.0, units="day of year",
                stops=["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
                title_color="#bd0026",
            )
        elif overlay_name == "wb":
            from vresto.services.wb import WB_CLASS_LEGENDS

            source_date = self._overlay_source_dates.get("wb", self._streaming_date or "")
            html = build_legend_html(f"Water Bodies ({source_date})", WB_CLASS_LEGENDS, "#1f78b4")
        else:
            return

        self.map_widget_obj.set_legend(html)

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

        await self._get_overlay_loader(overlay_name)()

    async def _reload_enabled_overlays(self):
        """Reload the active overlay for the current tile."""
        overlay_name = self._active_overlay or self._get_enabled_overlay()
        if not overlay_name:
            self._clear_overlay_legend()
            return

        self._active_overlay = overlay_name
        self._clear_overlay_legend()
        self._sync_overlay_sections(overlay_name)
        await self._get_overlay_loader(overlay_name)()

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
            if self.map_widget_obj:
                self.map_widget_obj.clear_grid_layer()

    def _toggle_hires(self, e) -> None:
        """Switch between 60 m (fast preview) and 10 m (full detail) TCI."""
        self._tci_resolution = "10m" if e.value else "60m"
        logger.info(f"TCI resolution set to {self._tci_resolution}")

    def _handle_moveend(self, bbox: tuple, zoom: int):
        """Handle map moveend: refresh MGRS grid if enabled."""
        if not self._grid_enabled:
            return

        geojson = compute_visible_tiles_geojson(bbox, zoom)
        if geojson and self.map_widget_obj:
            self.map_widget_obj.set_grid_layer(geojson)
        elif self.map_widget_obj:
            self.map_widget_obj.clear_grid_layer()

    async def _handle_tile_click(self, tile_code: str):
        """Handle click on an MGRS grid tile.

        Looks up the top-N latest matching products for the tile and date range,
        then either streams directly (single match) or opens a chooser dialog so
        the user picks which product to stream (multiple matches).
        """
        previous_tile_code = self._streaming_tile_code
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
        if hasattr(self, "_overlay_status_label"):
            self._overlay_status_label.set_text("Choose one overlay section. It will follow tile changes automatically once streamed.")
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
                if hasattr(self, "_tile_status_label"):
                    tile_label = f"Selected tile: {tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                    self._tile_status_label.set_text(tile_label)
                self._update_tcd_overlay_availability()
                await self._refresh_lst_time_options()
                await self._reload_enabled_overlays()
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

            with ui.column().classes("w-full gap-2"):
                for idx, product in enumerate(products, 1):
                    with ui.card().classes("w-full p-2 bg-gray-50 dark:bg-slate-800 shadow-sm rounded-md"):
                        display = getattr(product, "display_name", product.name)
                        ui.label(f"{idx}. {display}").classes("text-xs font-mono break-all")
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600 dark:text-gray-300")
                            if product.cloud_cover is not None:
                                ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600 dark:text-gray-300")
                            ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600 dark:text-gray-300")
                            ui.space()

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
        """Stream TCI for the given (tile, product): cache hit first, then full-res."""
        t_e2e = time.perf_counter()

        sensing_digits = "".join(ch for ch in product.sensing_date if ch.isdigit())
        date = sensing_digits[:8]
        self._streaming_date = date
        self._streaming_timestamp = sensing_digits[:14] or date
        resolution = self._tci_resolution

        # Step 1: Show quicklook immediately if available
        if product.assets and "thumbnail" in product.assets:
            thumb_url = product.assets["thumbnail"].get("href", "")
            if thumb_url and thumb_url.startswith("https://"):
                # Add as image overlay (approximation — use tile layer for now)
                self._add_message(f"🖼️ Loading quicklook for {tile_code}...")

        # Step 2: Stream full TCI in background
        self._add_message(f"📡 Streaming TCI ({resolution}) for {tile_code} ({product.sensing_date})...")

        cached = sentinel_stream_service.get_cached_tci_path(tile_code, date, resolution)
        if cached:
            self._add_message(f"⚡ Cache hit ({resolution}) for {tile_code}")
            await self._display_tci_layer(cached, tile_code)
            logger.info(f"[perf] _stream_tile '{tile_code}' (cache hit) end-to-end {(time.perf_counter() - t_e2e) * 1000:.0f} ms")
            return True

        # Need to find exact TCI path on S3
        if not product.s3_path:
            self._add_message(f"❌ No S3 path for product {product.name}")
            return False

        # Find TCI file path within the product
        t_tci = time.perf_counter()
        tci_path = await asyncio.to_thread(
            sentinel_stream_service.find_tci_path_in_product,
            product.s3_path,
            tile_code,
            resolution,
        )
        logger.info(f"[perf] _stream_tile '{tile_code}': find_tci_path_in_product {(time.perf_counter() - t_tci) * 1000:.0f} ms")

        if not tci_path:
            self._add_message(f"❌ Could not locate TCI band in {product.name}")
            return False

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
        logger.info(f"[perf] _stream_tile '{tile_code}': stream_tci (S3 + JP2 decode + write) {(time.perf_counter() - t_stream) * 1000:.0f} ms")

        if result:
            self._add_message(f"✅ TCI ready for {tile_code}")
            await self._display_tci_layer(result, tile_code)
            logger.info(f"[perf] _stream_product '{tile_code}' end-to-end {(time.perf_counter() - t_e2e) * 1000:.0f} ms")
            return True

        self._add_message(f"❌ Failed to stream TCI for {tile_code}")
        ui.notify("TCI streaming failed", position="top", type="negative")
        return False

    async def _display_tci_layer(self, cog_path: str, tile_code: str):
        """Display a cached TCI COG as a tile layer on the map."""
        layer_name = f"tci_{tile_code}"

        if self.map_widget_obj:
            # Remove any previous TCI layer before adding the new one
            for existing in list(self.map_widget_obj._tile_layers.keys()):
                if existing.startswith("tci_"):
                    tile_pool.remove(existing)
                    self.map_widget_obj.remove_tile_layer(existing)

        # First-time TileClient bootstrap can take ~1–2 s; run off-loop.
        t_pool = time.perf_counter()
        url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, cog_path)
        logger.info(f"[perf] _display_tci_layer '{tile_code}': tile_pool.get_or_create {(time.perf_counter() - t_pool) * 1000:.0f} ms")
        if url and self.map_widget_obj:
            self.map_widget_obj.add_tile_layer(url, name=layer_name)

            # Zoom the map to the raster bounds
            bounds = tile_pool.get_bounds(layer_name)
            if bounds:
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
            headers = auth.get_headers()

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

    async def _get_tile_candidates(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Return up to `limit` latest products for a tile.

        Prefers cached matches from `current_state["products"]` (instant); falls
        back to a CDSE catalog query when no cached match exists.
        """
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

    async def _load_worldcover_overlay(self):
        """Load WorldCover overlay for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.worldcover import worldcover_service

        # Use whichever cached TCI resolution exists as the reference raster.
        # Overlays only need the CRS + extent, not the source resolution.
        date = self._streaming_date or ""

        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify(
                "Stream a TCI tile first before enabling overlays",
                position="top",
                type="warning",
            )
            return

        self._add_message(f"🌍 Loading WorldCover for {tile_code}...")
        ui.notify(
            f"Loading WorldCover for {tile_code}...",
            position="top",
            type="info",
            spinner=True,
        )

        t_overlay = time.perf_counter()
        colorized = await asyncio.to_thread(
            worldcover_service.get_colorized_worldcover_path,
            ref_path,
            20,
            "2021",
        )

        if colorized:
            layer_name = self._overlay_layer_name("worldcover", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["worldcover"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["worldcover"],
                )
                self._show_overlay_legend("worldcover")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] WorldCover overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ WorldCover overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(
                    f"✅ WorldCover overlay active for {tile_code}",
                    position="top",
                    type="positive",
                )
                return

        # Either colorize returned None or the tile_pool/url stage failed
        self._overlay_layer_urls.pop("worldcover", None)
        logger.warning(f"WorldCover overlay failed for {tile_code}")
        self._add_message(f"❌ WorldCover overlay failed for {tile_code}")
        ui.notify(
            f"WorldCover overlay failed for {tile_code}",
            position="top",
            type="negative",
        )

    async def _load_lcm_overlay(self):
        """Load LCM overlay for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.lcm import lcm_service

        date = self._streaming_date or ""

        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify(
                "Stream a TCI tile first before enabling overlays",
                position="top",
                type="warning",
            )
            return

        self._add_message(f"🗺️ Loading LCM for {tile_code}...")
        ui.notify(
            f"Loading LCM for {tile_code}...",
            position="top",
            type="info",
            spinner=True,
        )

        t_overlay = time.perf_counter()
        colorized = await asyncio.to_thread(
            lcm_service.get_colorized_lcm_path,
            ref_path,
            20,
            "2020",
        )

        if colorized:
            layer_name = self._overlay_layer_name("lcm", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["lcm"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["lcm"],
                )
                self._show_overlay_legend("lcm")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] LCM overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ LCM overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(
                    f"✅ LCM overlay active for {tile_code}",
                    position="top",
                    type="positive",
                )
                return

        self._overlay_layer_urls.pop("lcm", None)
        logger.warning(f"LCM overlay failed for {tile_code}")
        self._add_message(f"❌ LCM overlay failed for {tile_code}")
        ui.notify(
            f"LCM overlay failed for {tile_code}",
            position="top",
            type="negative",
        )

    async def _load_tcd_overlay(self):
        """Load Tree Cover Density for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.tcd import tcd_service

        if not self._tcd_available_for_tile:
            self._add_message(f"⚠️ Tree Cover Density unavailable for {tile_code} (outside pantropical coverage)")
            ui.notify("Tree Cover Density is unavailable for this tile", position="top", type="warning")
            return

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify(
                "Stream a TCI tile first before enabling overlays",
                position="top",
                type="warning",
            )
            return

        year = self._tcd_year
        self._add_message(f"🌳 Loading Tree Cover Density ({year}) for {tile_code}...")
        ui.notify(
            f"Loading Tree Cover Density for {tile_code}...",
            position="top",
            type="info",
            spinner=True,
        )

        t_overlay = time.perf_counter()
        colorized = await asyncio.to_thread(
            tcd_service.get_colorized_tcd_path,
            ref_path,
            20,
            year,
        )

        if colorized:
            layer_name = self._overlay_layer_name("tcd", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["tcd"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["tcd"],
                )
                self._show_overlay_legend("tcd")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] TCD overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ Tree Cover Density overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(
                    f"✅ Tree Cover Density overlay active for {tile_code}",
                    position="top",
                    type="positive",
                )
                return

        self._overlay_layer_urls.pop("tcd", None)
        logger.warning(f"TCD overlay failed for {tile_code}")
        self._add_message(f"❌ Tree Cover Density overlay failed for {tile_code}")
        ui.notify(
            f"Tree Cover Density overlay failed for {tile_code}",
            position="top",
            type="negative",
        )

    async def _on_lc100_year_change(self, e):
        """Change the LC100 epoch year and reload the overlay if active."""
        self._lc100_year = str(e.value or "2019")
        if self._get_enabled_overlay() == "lc100" and self._streaming_tile_code:
            self._remove_overlay_layer("lc100")
            await self._load_lc100_overlay()

    async def _on_tcd_year_change(self, e):
        """Change the TCD year and reload the overlay if active."""
        self._tcd_year = str(e.value or "2020")
        if self._get_enabled_overlay() == "tcd" and self._streaming_tile_code:
            self._remove_overlay_layer("tcd")
            await self._load_tcd_overlay()

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
            await self._load_lst_overlay()

    async def _load_dem_overlay(self):
        """Load the Copernicus DEM (GLO-30) terrain overlay for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.dem import dem_service

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        self._add_message(f"⛰️ Loading DEM terrain for {tile_code}...")
        ui.notify(f"Loading DEM for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        # 60 m keeps the read on a COG overview (~2-3 s); a terrain backdrop does
        # not need finer than the DEM's ~30 m native sampling.
        colorized = await asyncio.to_thread(dem_service.get_colorized_dem_path, ref_path, 60)

        if colorized:
            layer_name = self._overlay_layer_name("dem", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["dem"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["dem"],
                )
                self._show_overlay_legend("dem")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] DEM overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ DEM overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ DEM overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop("dem", None)
        logger.warning(f"DEM overlay failed for {tile_code}")
        self._add_message(f"❌ DEM overlay failed for {tile_code}")
        ui.notify(f"DEM overlay failed for {tile_code}", position="top", type="negative")

    async def _load_lc100_overlay(self):
        """Load the CGLS Global Land Cover 100m overlay for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.lc100 import lc100_service

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        year = self._lc100_year
        self._add_message(f"🌐 Loading Global LC 100m ({year}) for {tile_code}...")
        ui.notify(f"Loading Global LC 100m for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        colorized = await asyncio.to_thread(lc100_service.get_colorized_lc100_path, ref_path, 20, year)

        if colorized:
            layer_name = self._overlay_layer_name("lc100", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["lc100"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["lc100"],
                )
                self._show_overlay_legend("lc100")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] LC100 overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ Global LC 100m overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ Global LC 100m overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop("lc100", None)
        logger.warning(f"LC100 overlay failed for {tile_code}")
        self._add_message(f"❌ Global LC 100m overlay failed for {tile_code}")
        ui.notify(f"Global LC 100m overlay failed for {tile_code}", position="top", type="negative")

    async def _load_ndvi_overlay(self):
        """Load NDVI-LTS mean for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.ndvi import ndvi_service

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        self._add_message(f"🌿 Loading NDVI climatology for {tile_code}...")
        ui.notify(f"Loading NDVI climatology for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        colorized = await asyncio.to_thread(ndvi_service.get_colorized_ndvi_path, ref_path, 1000, date)

        if colorized:
            layer_name = self._overlay_layer_name("ndvi", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["ndvi"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["ndvi"],
                )
                self._show_overlay_legend("ndvi")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] NDVI overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ NDVI climatology overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ NDVI climatology overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop("ndvi", None)
        logger.warning(f"NDVI overlay failed for {tile_code}")
        self._add_message(f"❌ NDVI climatology overlay failed for {tile_code}")
        ui.notify(f"NDVI climatology overlay failed for {tile_code}", position="top", type="negative")

    async def _load_lst_overlay(self):
        """Load hourly LST for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.lst import format_lst_selected_datetime, lst_service

        lookup_date = self._streaming_date or ""
        lst_timestamp = self._lst_user_selected_timestamp or self._streaming_timestamp or lookup_date
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, lookup_date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        self._add_message(f"🌡️ Loading hourly LST for {tile_code}...")
        ui.notify(f"Loading hourly LST for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        self._lst_selected_timestamp_label = None
        result = await asyncio.to_thread(lst_service.get_colorized_lst_result, ref_path, 3000, lst_timestamp)

        if result:
            colorized = result.colorized_path
            self._lst_selected_timestamp_label = format_lst_selected_datetime(result.selected_datetime)
            layer_name = self._overlay_layer_name("lst", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["lst"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["lst"],
                )
                self._show_overlay_legend("lst")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] LST overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                if hasattr(self, "_overlay_status_label") and self._lst_selected_timestamp_label:
                    self._overlay_status_label.set_text(f"LST hourly is active for {tile_code} ({self._lst_selected_timestamp_label}). Click another tile to retarget it automatically.")
                self._add_message(f"✅ Hourly LST overlay active for {tile_code} ({self._lst_selected_timestamp_label}, {elapsed_ms:.0f} ms)")
                ui.notify(f"✅ Hourly LST overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop("lst", None)
        logger.warning(f"LST overlay failed for {tile_code}")
        self._add_message(f"❌ Hourly LST overlay failed for {tile_code}")
        ui.notify(f"Hourly LST overlay failed for {tile_code}", position="top", type="negative")

    async def _load_fapar_overlay(self):
        """Load 10-daily FAPAR for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        from vresto.services.fapar import fapar_service

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        self._add_message(f"🌿 Loading FAPAR for {tile_code}...")
        ui.notify(f"Loading FAPAR for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        result = await asyncio.to_thread(fapar_service.get_colorized_fapar_result, ref_path, 300, date)

        if result:
            colorized = result.colorized_path
            layer_name = self._overlay_layer_name("fapar", tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls["fapar"] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name["fapar"],
                )
                self._show_overlay_legend("fapar")
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] FAPAR overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ FAPAR overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ FAPAR overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop("fapar", None)
        logger.warning(f"FAPAR overlay failed for {tile_code}")
        self._add_message(f"❌ FAPAR overlay failed for {tile_code}")
        ui.notify(f"FAPAR overlay failed for {tile_code}", position="top", type="negative")

    async def _load_stac_result_overlay(self, overlay_name, service_call, target_resolution_m, emoji, label):
        """Generic loader for temporal STAC overlays returning a result with a selected datetime.

        Shared by the DMP/SSM/SWI/BA/WB overlays, which all resolve their COG via
        CDSE STAC discovery and return an object exposing ``colorized_path`` and
        ``selected_datetime``.
        """
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date)
        if not ref_path:
            self._add_message("⚠️ Stream TCI first before enabling overlays")
            ui.notify("Stream a TCI tile first before enabling overlays", position="top", type="warning")
            return

        self._add_message(f"{emoji} Loading {label} for {tile_code}...")
        ui.notify(f"Loading {label} for {tile_code}...", position="top", type="info", spinner=True)

        t_overlay = time.perf_counter()
        result = await asyncio.to_thread(service_call, ref_path, target_resolution_m, date)

        if result:
            colorized = result.colorized_path
            self._overlay_source_dates[overlay_name] = result.selected_datetime.strftime("%Y-%m-%d")
            layer_name = self._overlay_layer_name(overlay_name, tile_code)
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self._overlay_layer_urls[overlay_name] = url
                self.map_widget_obj.add_tile_layer(
                    url,
                    name=layer_name,
                    opacity=self._overlay_opacity_by_name[overlay_name],
                )
                self._show_overlay_legend(overlay_name)
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] {label} overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ {label} overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ {label} overlay active for {tile_code}", position="top", type="positive")
                return

        self._overlay_layer_urls.pop(overlay_name, None)
        logger.warning(f"{label} overlay failed for {tile_code}")
        self._add_message(f"❌ {label} overlay failed for {tile_code}")
        ui.notify(f"{label} overlay failed for {tile_code}", position="top", type="negative")

    async def _load_dmp_overlay(self):
        """Load 10-daily Dry Matter Productivity for the current streaming tile."""
        from vresto.services.dmp import dmp_service

        await self._load_stac_result_overlay("dmp", dmp_service.get_colorized_dmp_result, 300, "🌱", "Dry Matter Productivity")

    async def _load_ssm_overlay(self):
        """Load daily Surface Soil Moisture for the current streaming tile."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return

        # SSM has European coverage only — check before making a STAC call.
        date = self._streaming_date or ""
        ref_path = sentinel_stream_service.find_any_cached_tci(tile_code, date) if tile_code else None
        if ref_path:
            try:
                import rasterio
                from rasterio.warp import transform_bounds

                from vresto.services.ssm import ssm_has_coverage

                with rasterio.open(ref_path) as ref:
                    left, bottom, right, top = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                if not ssm_has_coverage(left, bottom, right, top):
                    msg = "Soil Moisture (SSM) has European coverage only. This tile is outside the product extent."
                    self._add_message(f"⚠️ {msg}")
                    ui.notify(msg, position="top", type="warning", timeout=6000)
                    self._suppress_overlay_events = True
                    try:
                        self._set_overlay_flag("ssm", False)
                        ssm_switch = self._overlay_switches.get("ssm")
                        if ssm_switch:
                            ssm_switch.set_value(False)
                    finally:
                        self._suppress_overlay_events = False
                    if self._active_overlay == "ssm":
                        self._active_overlay = None
                        self._clear_overlay_legend()
                        self._sync_overlay_sections(None)
                    return
            except Exception as exc:
                logger.warning(f"Could not check SSM coverage for {tile_code}: {exc}")

        from vresto.services.ssm import ssm_service

        await self._load_stac_result_overlay("ssm", ssm_service.get_colorized_ssm_result, 1000, "💧", "Soil Moisture")

    async def _load_swi_overlay(self):
        """Load daily Soil Water Index for the current streaming tile."""
        from vresto.services.swi import swi_service

        await self._load_stac_result_overlay("swi", swi_service.get_colorized_swi_result, 12500, "💧", "Soil Water Index")

    async def _load_ba_overlay(self):
        """Load monthly Burned Area for the current streaming tile."""
        from vresto.services.ba import ba_service

        await self._load_stac_result_overlay("ba", ba_service.get_colorized_ba_result, 300, "🔥", "Burned Area")

    async def _load_wb_overlay(self):
        """Load monthly Water Bodies for the current streaming tile."""
        # WB data starts from October 2020 — gate early to avoid a silent STAC 404.
        WB_START_DATE = "20201001"
        date = self._streaming_date or ""
        if date and date < WB_START_DATE:
            msg = f"Water Bodies data is only available from October 2020. Streamed date is {date[:4]}-{date[4:6]}-{date[6:8]}."
            self._add_message(f"⚠️ {msg}")
            ui.notify(msg, position="top", type="warning", timeout=7000)
            self._suppress_overlay_events = True
            try:
                self._set_overlay_flag("wb", False)
                wb_switch = self._overlay_switches.get("wb")
                if wb_switch:
                    wb_switch.set_value(False)
            finally:
                self._suppress_overlay_events = False
            if self._active_overlay == "wb":
                self._active_overlay = None
                self._clear_overlay_legend()
                self._sync_overlay_sections(None)
            return

        from vresto.services.wb import wb_service

        await self._load_stac_result_overlay("wb", wb_service.get_colorized_wb_result, 100, "🌊", "Water Bodies")

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

        # Validate inputs
        if self.current_state["bbox"] is None:
            ui.notify(
                "⚠️ Please drop a pin (or draw) a location on the map first",
                position="top",
                type="warning",
            )
            add_message("⚠️ Search failed: No location selected")
            return

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
            bbox = self.current_state["bbox"]

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

            # Display results
            if results_display:
                results_display.clear()

                if not filtered_products:
                    with results_display:
                        ui.label("No products found with selected level").classes("text-gray-500 italic")
                    ui.notify(
                        "No products found with selected level",
                        position="top",
                        type="warning",
                    )
                    add_message("❌ No products found with selected level")
                else:
                    with results_display:
                        ui.label(f"Found {len(filtered_products)} products (filtered from {len(products)} total)").classes("text-sm font-semibold text-green-600 mb-2")

                        for i, product in enumerate(filtered_products, 1):
                            self._create_product_card(results_display, i, product, self.messages_column)

                    ui.notify(
                        f"✅ Found {len(filtered_products)} products",
                        position="top",
                        type="positive",
                    )
                    add_message(f"✅ Found {len(filtered_products)} products (from {len(products)} total)")
                    logger.info(f"Search completed: {len(filtered_products)} products found (filtered from {len(products)})")

        except Exception as e:
            logger.error(f"Search failed: {e}")
            if results_display:
                results_display.clear()
                with results_display:
                    ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
            ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
            add_message(f"❌ Search error: {str(e)}")

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
            with ui.card().classes("w-full p-3 bg-gray-50 shadow-sm rounded-md"):
                ui.label(f"{index}. {getattr(product, 'display_name', product.name)}").classes("text-xs font-mono break-all")
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
