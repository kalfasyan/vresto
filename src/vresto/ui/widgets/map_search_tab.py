"""Map search tab widget combining map, date picker, and search controls."""

import asyncio
import time
from typing import Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api import BoundingBox, CatalogSearch, ProductInfo
from vresto.api.auth import get_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
    get_product_capabilities,
)
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
from vresto.ui.widgets.map_widget import MapWidget
from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget


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

        # State
        self.current_state = {
            "bbox": None,
            "date_range": {"from": "2020-01-01", "to": "2020-01-31"},
            "products": [],
        }

        # UI elements
        self.messages_column = None
        self.map_widget = None
        self.map_widget_obj = None
        self.results_display = None
        self.date_picker = None

        # Grid & streaming state
        self._grid_enabled = False
        self._streaming_tile_code: Optional[str] = None
        self._streaming_date: Optional[str] = None
        self._worldcover_enabled = False
        self._lcm_enabled = False
        self._dem_enabled = False
        self._lc100_enabled = False
        self._lc100_year = "2019"
        self._overlay_opacity = 1.0
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
            picker_widget = DatePickerWidget(
                default_from="2020-01-01",
                default_to="2020-01-31",
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
            ui.label("🛰️ Tile Streaming").classes("text-sm font-semibold")

            if not has_creds:
                ui.label("Configure S3 credentials to enable tile streaming").classes("text-xs text-gray-500 italic")
                return

            if not mgrs_available():
                ui.label("mgrs package not installed").classes("text-xs text-red-500 italic")
                return

            # Grid toggle
            grid_switch = ui.switch("Show MGRS Grid", value=False, on_change=self._toggle_grid)
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

            ui.separator().classes("my-1")
            ui.label("Overlays (click a tile first)").classes("text-xs text-gray-500")

            # WorldCover toggle
            self._wc_switch = ui.switch("WorldCover 2021", value=False, on_change=self._toggle_worldcover)
            self._wc_switch.classes("text-xs")
            self._wc_switch.props("disable")

            # LCM toggle
            self._lcm_switch = ui.switch("LCM 2020", value=False, on_change=self._toggle_lcm)
            self._lcm_switch.classes("text-xs")
            self._lcm_switch.props("disable")

            # DEM (terrain) toggle
            self._dem_switch = ui.switch("DEM terrain (GLO-30)", value=False, on_change=self._toggle_dem)
            self._dem_switch.classes("text-xs")
            self._dem_switch.props("disable")

            # Global Land Cover 100m (CGLS) toggle + year selector
            self._lc100_switch = ui.switch("Global LC 100m", value=False, on_change=self._toggle_lc100)
            self._lc100_switch.classes("text-xs")
            self._lc100_switch.props("disable")
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

            # Opacity slider
            self._opacity_slider = ui.slider(min=0.1, max=1.0, step=0.05, value=1.0, on_change=self._update_overlay_opacity)
            self._opacity_slider.classes("w-full")
            ui.label("Overlay opacity").classes("text-xs text-gray-400")

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
        """Handle click on an MGRS grid tile: stream TCI."""
        self._streaming_tile_code = tile_code
        self._add_message(f"🛰️ Clicked tile: {tile_code}")
        ui.notify(f"Loading tile {tile_code}...", position="top", type="info", spinner=True)

        # Visual feedback on the map: yellow-highlight the clicked tile
        # while the streaming task runs.
        if self.map_widget_obj:
            self.map_widget_obj.highlight_tile(tile_code)

        # Enable overlay toggles now that a tile is selected
        if hasattr(self, "_wc_switch"):
            self._wc_switch.props(remove="disable")
        if hasattr(self, "_lcm_switch"):
            self._lcm_switch.props(remove="disable")
        if hasattr(self, "_dem_switch"):
            self._dem_switch.props(remove="disable")
        if hasattr(self, "_lc100_switch"):
            self._lc100_switch.props(remove="disable")
        if hasattr(self, "_lc100_year_select"):
            self._lc100_year_select.props(remove="disable")

        try:
            await self._stream_tile(tile_code)
        finally:
            # Always reset the highlight, even if streaming raised.
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()

    async def _stream_tile(self, tile_code: str):
        """Stream TCI for the given MGRS tile: quicklook first, then full-res."""
        t_e2e = time.perf_counter()
        # Find the latest product for this tile — from search results or catalog query
        product = self._find_product_for_tile(tile_code)
        if not product:
            # No product in current results — query catalog directly
            self._add_message(f"🔍 Searching catalog for tile {tile_code}...")
            t_search = time.perf_counter()
            product = await self._search_product_for_tile(tile_code)
            logger.info(f"[perf] _stream_tile '{tile_code}': catalog search {(time.perf_counter() - t_search) * 1000:.0f} ms")

        if not product:
            self._add_message(f"⚠️ No S2 L2A product found for tile {tile_code} in selected date range.")
            ui.notify("No product found for this tile in the date range.", position="top", type="warning")
            return

        date = product.sensing_date.replace("-", "")[:8]
        self._streaming_date = date
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
            return

        # Need to find exact TCI path on S3
        if not product.s3_path:
            self._add_message(f"❌ No S3 path for product {product.name}")
            return

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
            return

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
            logger.info(f"[perf] _stream_tile '{tile_code}' end-to-end {(time.perf_counter() - t_e2e) * 1000:.0f} ms")
        else:
            self._add_message(f"❌ Failed to stream TCI for {tile_code}")
            ui.notify("TCI streaming failed", position="top", type="negative")

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

    def _find_product_for_tile(self, tile_code: str) -> Optional[ProductInfo]:
        """Find a product in current search results that matches the MGRS tile."""
        # tile_code from MGRS is like "33UUP"; product names have "T33UUP"
        search_code = tile_code if tile_code.startswith("T") else f"T{tile_code}"
        for product in self.current_state.get("products", []):
            if search_code in product.name:
                return product
        # If no exact match, return the first S2 product (user may need to search)
        for product in self.current_state.get("products", []):
            if product.s3_path and "Sentinel-2" in (product.s3_path or ""):
                return product
        return None

    async def _search_product_for_tile(self, tile_code: str) -> Optional[ProductInfo]:
        """Query the CDSE catalog for a Sentinel-2 L2A product matching the tile and date range."""
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
                "$top": 1,
                "$orderby": "ContentDate/Start desc",
                "$expand": "Attributes",
            }
            resp = requests.get(url, params=params, headers=headers, timeout=60)
            if resp.status_code != 200:
                return None

            from datetime import datetime as _dt

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
                return ProductInfo(
                    id=item.get("Id", ""),
                    name=item.get("Name", ""),
                    collection="SENTINEL-2",
                    sensing_date=sensing,
                    size_mb=size_mb,
                    s3_path=s3_path,
                    cloud_cover=cloud_cover,
                )
            return None

        try:
            product = await asyncio.to_thread(_query)
            if product:
                logger.info(f"Auto-found product for tile {tile_code}: {product.name}")
            return product
        except Exception as e:
            logger.warning(f"Catalog search for tile {tile_code} failed: {e}")
            return None

    async def _toggle_worldcover(self, e):
        """Toggle WorldCover overlay."""
        self._worldcover_enabled = e.value
        if not self._streaming_tile_code:
            return
        if e.value:
            await self._load_worldcover_overlay()
        else:
            tile_pool.remove(f"wc_{self._streaming_tile_code}")
            if self.map_widget_obj:
                self.map_widget_obj.remove_tile_layer(f"wc_{self._streaming_tile_code}")

    async def _toggle_lcm(self, e):
        """Toggle LCM overlay."""
        self._lcm_enabled = e.value
        if not self._streaming_tile_code:
            return
        if e.value:
            await self._load_lcm_overlay()
        else:
            tile_pool.remove(f"lcm_{self._streaming_tile_code}")
            if self.map_widget_obj:
                self.map_widget_obj.remove_tile_layer(f"lcm_{self._streaming_tile_code}")

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
            layer_name = f"wc_{tile_code}"
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self.map_widget_obj.add_tile_layer(url, name=layer_name, opacity=self._overlay_opacity)
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
            layer_name = f"lcm_{tile_code}"
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self.map_widget_obj.add_tile_layer(url, name=layer_name, opacity=self._overlay_opacity)
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] LCM overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ LCM overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(
                    f"✅ LCM overlay active for {tile_code}",
                    position="top",
                    type="positive",
                )
                return

        logger.warning(f"LCM overlay failed for {tile_code}")
        self._add_message(f"❌ LCM overlay failed for {tile_code}")
        ui.notify(
            f"LCM overlay failed for {tile_code}",
            position="top",
            type="negative",
        )

    async def _toggle_dem(self, e):
        """Toggle DEM (terrain) overlay."""
        self._dem_enabled = e.value
        if not self._streaming_tile_code:
            return
        if e.value:
            await self._load_dem_overlay()
        else:
            tile_pool.remove(f"dem_{self._streaming_tile_code}")
            if self.map_widget_obj:
                self.map_widget_obj.remove_tile_layer(f"dem_{self._streaming_tile_code}")

    async def _toggle_lc100(self, e):
        """Toggle CGLS Global Land Cover 100m overlay."""
        self._lc100_enabled = e.value
        if not self._streaming_tile_code:
            return
        if e.value:
            await self._load_lc100_overlay()
        else:
            tile_pool.remove(f"lc100_{self._streaming_tile_code}")
            if self.map_widget_obj:
                self.map_widget_obj.remove_tile_layer(f"lc100_{self._streaming_tile_code}")

    async def _on_lc100_year_change(self, e):
        """Change the LC100 epoch year and reload the overlay if active."""
        self._lc100_year = str(e.value or "2019")
        if self._lc100_enabled and self._streaming_tile_code:
            tile_pool.remove(f"lc100_{self._streaming_tile_code}")
            if self.map_widget_obj:
                self.map_widget_obj.remove_tile_layer(f"lc100_{self._streaming_tile_code}")
            await self._load_lc100_overlay()

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
            layer_name = f"dem_{tile_code}"
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self.map_widget_obj.add_tile_layer(url, name=layer_name, opacity=self._overlay_opacity)
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] DEM overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ DEM overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ DEM overlay active for {tile_code}", position="top", type="positive")
                return

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
            layer_name = f"lc100_{tile_code}"
            url = await asyncio.to_thread(tile_pool.get_or_create, layer_name, colorized)
            if url and self.map_widget_obj:
                self.map_widget_obj.add_tile_layer(url, name=layer_name, opacity=self._overlay_opacity)
                elapsed_ms = (time.perf_counter() - t_overlay) * 1000
                logger.info(f"[perf] LC100 overlay loaded for {tile_code} in {elapsed_ms:.0f} ms")
                self._add_message(f"✅ Global LC 100m overlay active for {tile_code} ({elapsed_ms:.0f} ms)")
                ui.notify(f"✅ Global LC 100m overlay active for {tile_code}", position="top", type="positive")
                return

        logger.warning(f"LC100 overlay failed for {tile_code}")
        self._add_message(f"❌ Global LC 100m overlay failed for {tile_code}")
        ui.notify(f"Global LC 100m overlay failed for {tile_code}", position="top", type="negative")

    def _update_overlay_opacity(self, e):
        """Update opacity for active overlay layers."""
        self._overlay_opacity = e.value
        # Opacity changes require re-adding layers (Leaflet limitation)
        # For now just update state; next toggle will use new value

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
