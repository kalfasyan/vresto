"""Hi-Res Tiler tab widget for high-resolution product inspection."""

import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from nicegui import ui

from vresto.ui.widgets.map_widget import MapWidget


class HiResTilerTab:
    """Encapsulates the Hi-Res Tiler tab for high-resolution visualization."""

    def __init__(self):
        self.map_widget_obj = None
        self.map_container = None
        self.products_column = None
        self.product_search_input = None
        self.selected_product_name: Optional[str] = None
        self.resolution_selector = None
        self.bands_container = None
        self.scanned_products: Dict[str, str] = {}
        self.current_img_root: Optional[str] = None
        self.available_bands: Dict[str, Set[int]] = {}
        self.selected_bands: List[str] = []

    def create(self):
        """Create and return the Hi-Res Tiler tab UI."""
        with ui.column().classes("w-full h-full gap-4") as self.map_container:
            with ui.row().classes("w-full gap-4"):
                # Left side: Map
                with ui.column().classes("flex-1 h-[700px]"):
                    self.map_widget_obj = MapWidget(center=(50.0, 10.0), zoom=5, title="High-Resolution Map View", draw_control=False)
                    self.map_widget_obj.create()

                # Right side: Controls
                with ui.column().classes("w-96 gap-4"):
                    self._create_controls_card()

            # Setup JavaScript event listener to receive tile URLs from ProductAnalysisTab
            ui.add_body_html(
                """
                <script>
                window.addEventListener("vresto:show_tiles", (event) => {
                    // This is a bridge between the global custom event and NiceGUI
                    const detail = event.detail;
                    console.log("vresto:show_tiles event received", detail);
                    // We call a NiceGUI function defined in the component
                    if (window.vresto_update_map) {
                        window.vresto_update_map(detail.url, detail.name, detail.bounds, detail.attribution);
                    }
                });
                </script>
            """
            )

            def update_map(url, name, bounds, attribution):
                if self.map_widget_obj:
                    self.map_widget_obj.clear_tile_layers()
                    self.map_widget_obj.add_tile_layer(url, name=name, attribution=attribution)
                    if bounds:
                        self.map_widget_obj.fit_bounds(bounds)

            # Expose the update function to JavaScript
            def _setup_js():
                ui.run_javascript(
                    f'''
                    window.vresto_update_map = (url, name, bounds, attribution) => {{
                        const container = document.getElementById("{self.map_container.id}");
                        if (container) {{
                            container.dispatchEvent(new CustomEvent("update_map", {{
                                detail: {{url, name, bounds, attribution}}
                            }}));
                        }}
                    }};
                '''
                )

            ui.context.client.on_connect(_setup_js)

            self.map_container.on(
                "update_map",
                lambda e: update_map(
                    e.args["detail"]["url"],
                    e.args["detail"]["name"],
                    e.args["detail"]["bounds"],
                    e.args["detail"]["attribution"],
                ),
            )

            # Initial scan of downloads folder
            ui.timer(1.0, self._scan_downloads, once=True)

        return self.map_container

    def _create_controls_card(self):
        """Create the control card for product, resolution and bands."""
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between mb-3"):
                ui.label("Tile Server Controls").classes("text-lg font-semibold")
                ui.button(icon="refresh", on_click=self._scan_downloads).props("flat dense")

            self.product_search_input = ui.input(
                placeholder="Search products...",
                on_change=self._filter_products,
            ).classes("w-full mb-1")

            ui.label("Products").classes("text-sm text-gray-600 mt-2")
            with ui.scroll_area().classes("w-full h-48 border rounded p-2 mb-2"):
                self.products_column = ui.column().classes("w-full gap-2")

            self.resolution_selector = ui.select(
                options={"10": "10m (High)", "20": "20m (Med)", "60": "60m (Low)"},
                value="10",
                label="Preferred Resolution",
                on_change=self._on_resolution_change,
            ).classes("w-full mb-2")

            ui.label("Bands to display").classes("text-sm text-gray-600 mt-2")
            self.bands_container = ui.column().classes("w-full gap-1")

            with ui.row().classes("w-full gap-2 mt-4"):
                ui.button("Clear Map", on_click=self._clear_map).classes("flex-1")
                ui.button("Zoom to Product", on_click=self._zoom_to_product).classes("flex-1").props("outline")

    def _zoom_to_product(self):
        """Manually zoom the map to the currently selected product."""
        bounds = getattr(self, "_product_bounds", None)
        if bounds:
            self._apply_zoom_to_bounds(bounds)
            ui.notify("Zooming to product", type="info")
        else:
            ui.notify("No product selected or bounds unknown", type="warning")

    def _apply_zoom_to_bounds(self, bounds):
        """Apply zoom and center to the map based on bounds."""
        from loguru import logger

        if not self.map_widget_obj or not self.map_widget_obj._map:
            return

        try:
            min_lat, min_lon, max_lat, max_lon = bounds
            center_lat = (min_lat + max_lat) / 2
            center_lon = (min_lon + max_lon) / 2

            logger.info(f"Setting map center to: {center_lat}, {center_lon}")
            self.map_widget_obj._map.set_center((center_lat, center_lon))
            # Set a zoom level that ensures the whole Sentinel-2 tile fits (approx 100km x 100km)
            self.map_widget_obj._map.set_zoom(9)

            # Try fit_bounds as well for precision if supported
            self.map_widget_obj.fit_bounds(bounds)
        except Exception as e:
            logger.error(f"Error applying zoom to bounds: {e}")

    def _scan_downloads(self):
        """Scan the default download folder for products."""
        root = str(Path.home() / "vresto_downloads")
        if not os.path.exists(root):
            return

        found_set = set()
        for dirpath, dirnames, _ in os.walk(root):
            for d in list(dirnames):
                if d.endswith(".SAFE"):
                    found_set.add(os.path.join(dirpath, d))

        self.scanned_products = {os.path.basename(p).replace(".SAFE", ""): p for p in found_set}
        self._filter_products()

    def _filter_products(self):
        """Filter products based on search input and update the list."""
        search_text = (self.product_search_input.value or "").strip().lower()
        self.products_column.clear()

        if search_text:
            filtered_names = [name for name in self.scanned_products.keys() if search_text in name.lower()]
        else:
            filtered_names = list(self.scanned_products.keys())

        for name in sorted(filtered_names):
            is_selected = name == self.selected_product_name
            with self.products_column:
                with ui.card().classes(f"w-full p-2 {'bg-blue-50 border-blue-200' if is_selected else 'bg-gray-50'}"):
                    ui.label(name).classes("text-xs font-mono break-all")
                    with ui.row().classes("w-full justify-end mt-1"):

                        async def _on_select(n=name):
                            await self._select_product(n)

                        ui.button("Select", on_click=_on_select).classes("text-xs").props("flat" if is_selected else "")

    async def _select_product(self, product_name: str):
        """Handle product selection."""
        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        self.selected_product_name = product_name
        # Note: We don't call self._filter_products() here immediately to avoid losing NiceGUI context
        # during subsequent async calls, which can cause "parent element deleted" errors.

        product_path = self.scanned_products.get(product_name)
        if not product_path:
            self._filter_products()
            return

        temp_tab = ProductAnalysisTab()
        self.current_img_root = temp_tab._find_img_data(product_path)

        # Calculate bounds explicitly to ensure map centers even if tile server is slow
        try:
            import rasterio

            if self.current_img_root:
                representative_band = "TCI"
                band_file = temp_tab._find_band_file(representative_band, self.current_img_root)
                if not band_file:
                    band_file = temp_tab._find_band_file("B04", self.current_img_root)

                if band_file:
                    with rasterio.open(band_file) as src:
                        from rasterio.warp import transform_bounds

                        left, bottom, right, top = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                        # Store bounds to be used when updating map
                        self._product_bounds = (bottom, left, top, right)
                else:
                    self._product_bounds = None
        except Exception as e:
            from loguru import logger

            logger.error(f"Failed to calculate product bounds: {e}")
            self._product_bounds = None

        if self.current_img_root:
            self.available_bands = temp_tab._list_available_bands(self.current_img_root)

            # Auto-load TCI or first available band
            if self.available_bands:
                target_band = "TCI" if "TCI" in self.available_bands else sorted(self.available_bands.keys())[0]
                self.selected_bands = [target_band]

                # If TCI is not available at the current preferred resolution, switch resolution if possible
                target_res = int(self.resolution_selector.value)
                if target_res not in self.available_bands[target_band]:
                    # Find first resolution that supports the target band
                    new_res = sorted(list(self.available_bands[target_band]))[0]
                    self.resolution_selector.value = str(new_res)

            self._update_bands_ui()

            if self.selected_bands:
                # Sequential execution ensured by await
                await self._refresh_tile_layer()
        else:
            ui.notify("No bands found for this product", type="warning")
            self.bands_container.clear()

        # Refresh UI to show selection at the end
        self._filter_products()

    async def _on_resolution_change(self):
        """Handle resolution selection change."""
        self._update_bands_ui()
        if self.selected_bands:
            await self._refresh_tile_layer()

    def _update_bands_ui(self):
        """Update the bands list UI based on available bands and selected resolution."""
        if not self.current_img_root:
            return

        self.bands_container.clear()
        target_res = int(self.resolution_selector.value)

        with self.bands_container:
            for band, res_set in sorted(self.available_bands.items()):
                # Only show bands that support the selected resolution (or close to it)
                # Some products (L1C) might not have resolution folders, res_set will be {10} (defaulted)
                # or empty if not found.
                is_available = target_res in res_set or not res_set

                with ui.row().classes("w-full items-center justify-between"):
                    # Use a checkbox but track it manually to avoid lambda issues in loops
                    label = f"{band} ({min(res_set)}m native)" if res_set else band
                    ui.checkbox(
                        text=label,
                        value=band in self.selected_bands,
                        on_change=lambda e, b=band: self._on_band_toggle(b, e.value),
                    ).props(f"{'disabled' if not is_available else ''}")

                    if not is_available:
                        # Show all available resolutions to help the user
                        res_list = ",".join(str(r) for r in sorted(res_set))
                        ui.label(f"N/A ({res_list}m only)").classes("text-xs text-gray-400")

    async def _on_band_toggle(self, band: str, enabled: bool):
        """Handle band selection toggle (enforcing single selection)."""
        # Save client to ensure we have a stable context even if elements are deleted
        client = ui.context.client

        if enabled:
            self.selected_bands = [band]
            # Refresh UI to uncheck other bands
            self._update_bands_ui()

            # Important: after updating UI (clearing/recreating elements),
            # we must ensure we're not in a deleted context when calling refresh.
            # We call refresh_tile_layer via a timer with an explicit client to escape the current slot context
            with client:
                ui.timer(0.01, self._refresh_tile_layer, once=True)
        else:
            if band in self.selected_bands:
                self.selected_bands.remove(band)
                with client:
                    ui.timer(0.01, self._refresh_tile_layer, once=True)

    async def _refresh_tile_layer(self):
        """Refresh the tile layer on the map based on selected bands."""
        from loguru import logger

        if not self.selected_bands or not self.current_img_root:
            logger.info(f"Nothing to refresh: selected_bands={self.selected_bands}, current_img_root={self.current_img_root}")
            self.map_widget_obj.clear_tile_layers()
            return

        from vresto.services.tiles import tile_manager
        from vresto.ui.visualization.helpers import SCL_PALETTE
        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        if not tile_manager.is_available():
            ui.notify("localtileserver is not installed", type="warning")
            return

        temp_tab = ProductAnalysisTab()
        band_files = []
        resolution = self.resolution_selector.value

        # We need to sort selected bands to ensure consistent VRT generation if needed
        for b in sorted(self.selected_bands):
            bf = temp_tab._find_band_file(b, self.current_img_root, preferred_resolution=resolution)
            if bf:
                band_files.append(bf)
            else:
                logger.warning(f"Band file not found for band {b} at resolution {resolution} in {self.current_img_root}")

        if not band_files:
            ui.notify(f"Could not find files for bands: {', '.join(self.selected_bands)}", type="warning")
            return

        ui.notify(f"🚀 Updating map with {len(band_files)} bands...", position="bottom-right", type="info")
        if resolution == "10":
            ui.notify("⏳ 10m resolution selected. Loading high-res tiles may take a few seconds...", position="bottom-right", type="warning", duration=5)

        # Handle SCL palette
        palette = None
        min_val, max_val, nodata = None, None, None
        if len(self.selected_bands) == 1 and self.selected_bands[0].upper() == "SCL":
            # Convert SCL_PALETTE to localtileserver expected format (list of colors)
            palette = [f"#{r:02x}{g:02x}{b:02x}" for i, (r, g, b) in sorted(SCL_PALETTE.items())]
            min_val, max_val, nodata = 0, 11, 0

        # Start tile server
        url = tile_manager.get_tile_url(
            band_files[0] if len(band_files) == 1 else band_files,
            palette=palette,
            min_val=min_val,
            max_val=max_val,
            nodata=nodata,
        )

        if url:
            name = ", ".join(sorted(self.selected_bands))
            self.map_widget_obj.clear_tile_layers()
            self.map_widget_obj.add_tile_layer(url, name=name)

            # Important: Leaflet and the tile server need a moment to settle
            # especially for sequential selections.
            import asyncio

            await asyncio.sleep(0.2)

            # Try to get bounds and zoom - prefer explicitly calculated bounds if available
            bounds = getattr(self, "_product_bounds", None)
            if not bounds:
                bounds = tile_manager.get_bounds()

            logger.info(f"Map fit bounds: {bounds}")
            if bounds:
                self._apply_zoom_to_bounds(bounds)
            else:
                # Fallback: notify that bounds couldn't be determined for auto-zoom
                logger.warning("Could not determine bounds for auto-zoom")
        else:
            ui.notify("❌ Failed to update tile server", type="negative")

    def _clear_map(self):
        """Clear all tile layers from the map."""
        if self.map_widget_obj:
            self.map_widget_obj.clear_tile_layers()
        self.selected_bands = []
        self._update_bands_ui()
