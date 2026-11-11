"""WorldCover and LCM overlays for the Hi-Res Tiler tab."""

import asyncio
import os
from typing import List

from nicegui import ui

from vresto.products.local_files import find_band_file
from vresto.services.lcm import LCM_CLASS_LEGENDS, lcm_service
from vresto.services.worldcover import WORLDCOVER_CLASS_LEGENDS, worldcover_service


class HiResOverlaysMixin:
    """WorldCover/LCM overlay toggles, refresh and legends, mixed into HiResTilerTab."""

    def _on_worldcover_toggle(self, enabled: bool):
        """Toggle WorldCover overlay layer."""
        self.worldcover_enabled = bool(enabled)

        if not self.map_widget_obj:
            return
        if not self.worldcover_enabled:
            self.map_widget_obj.remove_tile_layer("WorldCover")
            self._worldcover_layer_signature = None
            self._worldcover_layer_url = None
            self._hide_worldcover_legend()
            return

        # Show legend immediately when enabling
        self._show_worldcover_legend()

        # Capture client for use in detached async task
        client = ui.context.client

        async def _do_refresh():
            # Re-acquire slot context using the container element
            with client:
                await self._refresh_worldcover_overlay()

        asyncio.ensure_future(_do_refresh())

    def _on_lcm_toggle(self, enabled: bool):
        """Toggle LCM overlay layer."""
        self.lcm_enabled = bool(enabled)

        if not self.map_widget_obj:
            return
        if not self.lcm_enabled:
            self.map_widget_obj.remove_tile_layer("LCM")
            self._lcm_layer_signature = None
            self._lcm_layer_url = None
            self._hide_lcm_legend()
            return

        # Show legend immediately when enabling
        self._show_lcm_legend()

        # Capture client for use in detached async task
        client = ui.context.client

        async def _do_refresh():
            # Re-acquire slot context using the container element
            with client:
                await self._refresh_lcm_overlay()

        asyncio.ensure_future(_do_refresh())

    def _on_worldcover_opacity_change(self, value: float):
        """Update WorldCover overlay opacity."""
        self.worldcover_opacity = float(value or 0.0)
        if self.worldcover_enabled and self._worldcover_layer_url and self.map_widget_obj:
            self.map_widget_obj.remove_tile_layer("WorldCover")
            self.map_widget_obj.add_tile_layer(self._worldcover_layer_url, name="WorldCover", opacity=self.worldcover_opacity)

    def _on_lcm_opacity_change(self, value: float):
        """Update LCM overlay opacity."""
        self.lcm_opacity = float(value or 0.0)
        if self.lcm_enabled and self._lcm_layer_url and self.map_widget_obj:
            self.map_widget_obj.remove_tile_layer("LCM")
            self.map_widget_obj.add_tile_layer(self._lcm_layer_url, name="LCM", opacity=self.lcm_opacity)

    def _on_worldcover_year_change(self, value: str):
        """Update WorldCover overlay year and refresh if enabled."""
        self.worldcover_year = str(value or "2021")
        self._worldcover_layer_signature = None
        self._worldcover_layer_url = None
        if self.worldcover_enabled:
            # Capture client for use in detached async task
            client = ui.context.client

            async def _do_refresh():
                # Re-acquire slot context using the container element
                with client:
                    await self._refresh_worldcover_overlay()

            asyncio.ensure_future(_do_refresh())

    def _set_overlay_year_options(self, mode: str):
        """Adjust year selector based on selected overlay source."""
        if not self.overlay_year_selector:
            return
        self.overlay_year_selector.options = ["2021", "2020"]
        if str(self.overlay_year_selector.value) not in {"2021", "2020"}:
            self.overlay_year_selector.value = "2021"
        self.overlay_year_selector.enable()

    async def _refresh_worldcover_overlay(self):
        """Refresh WorldCover overlay while keeping base Sentinel layer visible."""
        try:
            await self.__refresh_worldcover_overlay_impl()
        except RuntimeError:
            pass  # parent slot deleted (tab navigated away)

    async def __refresh_worldcover_overlay_impl(self):
        from loguru import logger

        if not self.current_img_root or not self.map_widget_obj:
            logger.warning("WorldCover refresh skipped: no current_img_root or map_widget_obj")
            return
        if self._worldcover_refresh_in_progress:
            logger.debug("WorldCover refresh already in progress, skipping")
            return
        self._worldcover_refresh_in_progress = True

        try:
            reference_band = None
            if self.selected_bands:
                reference_band = self.selected_bands[0]
            elif self.available_bands:
                reference_band = "TCI" if "TCI" in self.available_bands else sorted(self.available_bands.keys())[0]

            if not reference_band:
                logger.warning("WorldCover refresh skipped: no reference band found")
                self._safe_notify("⚠️ WorldCover needs a band selected first", type="warning")
                return

            band_file = find_band_file(reference_band, self.current_img_root, preferred_resolution=self.resolution_selector.value)
            if not band_file:
                logger.warning(f"WorldCover refresh skipped: band file not found for {reference_band}")
                self._safe_notify(f"⚠️ Band file not found for {reference_band}", type="warning")
                return

            target_res = int(self.resolution_selector.value)
            signature = (os.path.abspath(band_file), target_res, self.worldcover_year)

            if self._worldcover_layer_signature == signature and self._worldcover_layer_url:
                logger.info("Reusing cached WorldCover layer")
                self.map_widget_obj.remove_tile_layer("WorldCover")
                self.map_widget_obj.add_tile_layer(self._worldcover_layer_url, name="WorldCover", opacity=self.worldcover_opacity)
                return

            logger.info(f"Generating WorldCover colorized overlay for {band_file} at {target_res}m, year={self.worldcover_year}")

            # Notify BEFORE background thread (safe in UI context)
            self._safe_notify("🌍 Generating WorldCover overlay...", position="bottom-right", type="info", duration=3)

            # Run heavy computation in background thread
            colorized = await asyncio.to_thread(worldcover_service.get_colorized_worldcover_path, band_file, target_res, year=self.worldcover_year)

            # All UI operations below run AFTER the thread completes, back in async context
            if not colorized:
                logger.warning("WorldCover colorized path is None - no tiles in extent or error occurred")
                self._safe_notify("⚠️ WorldCover overlay unavailable for this extent", type="warning")
                return

            logger.info(f"WorldCover colorized file: {colorized}")

            external_host = self._get_public_tile_host()
            logger.info(f"Starting WorldCover tile server on port {self.worldcover_tile_port} with external_host={external_host}")

            wc_url = self.worldcover_tile_manager.get_tile_url(colorized, port=self.worldcover_tile_port, external_host=external_host)
            if not wc_url:
                logger.error("WorldCover tile manager returned None URL")
                self._safe_notify("❌ WorldCover tile server failed to start", type="negative")
                return

            logger.info(f"WorldCover tile URL: {wc_url}")
            self._worldcover_layer_signature = signature
            self._worldcover_layer_url = wc_url
            self.map_widget_obj.remove_tile_layer("WorldCover")
            self.map_widget_obj.add_tile_layer(wc_url, name="WorldCover", opacity=self.worldcover_opacity)
            self._safe_notify("✅ WorldCover overlay loaded", type="positive")
        except Exception as e:
            logger.exception(f"WorldCover overlay failed: {e}")
            self._safe_notify(f"❌ WorldCover overlay failed: {e}", type="negative")
        finally:
            self._worldcover_refresh_in_progress = False

    async def _refresh_lcm_overlay(self):
        """Refresh LCM overlay while keeping base Sentinel layer visible."""
        try:
            await self.__refresh_lcm_overlay_impl()
        except RuntimeError:
            pass  # parent slot deleted (tab navigated away)

    async def __refresh_lcm_overlay_impl(self):
        from loguru import logger

        if not self.current_img_root or not self.map_widget_obj:
            logger.warning("LCM refresh skipped: no current_img_root or map_widget_obj")
            return
        if self._lcm_refresh_in_progress:
            logger.debug("LCM refresh already in progress, skipping")
            return
        self._lcm_refresh_in_progress = True

        try:
            reference_band = None
            if self.selected_bands:
                reference_band = self.selected_bands[0]
            elif self.available_bands:
                reference_band = "TCI" if "TCI" in self.available_bands else sorted(self.available_bands.keys())[0]

            if not reference_band:
                logger.warning("LCM refresh skipped: no reference band found")
                self._safe_notify("⚠️ LCM needs a band selected first", type="warning")
                return

            band_file = find_band_file(reference_band, self.current_img_root, preferred_resolution=self.resolution_selector.value)
            if not band_file:
                logger.warning(f"LCM refresh skipped: band file not found for {reference_band}")
                self._safe_notify(f"⚠️ Band file not found for {reference_band}", type="warning")
                return

            target_res = int(self.resolution_selector.value)
            year = "2020"
            signature = (os.path.abspath(band_file), target_res, year)

            if self._lcm_layer_signature == signature and self._lcm_layer_url:
                logger.info("Reusing cached LCM layer")
                self.map_widget_obj.remove_tile_layer("LCM")
                self.map_widget_obj.add_tile_layer(self._lcm_layer_url, name="LCM", opacity=self.lcm_opacity)
                return

            logger.info(f"Generating LCM colorized overlay for {band_file} at {target_res}m, year={year}")
            self._safe_notify("🗺️ Generating LCM overlay...", position="bottom-right", type="info", duration=3)

            colorized = await asyncio.to_thread(lcm_service.get_colorized_lcm_path, band_file, target_res, year=year)
            if not colorized:
                logger.warning("LCM colorized path is None - no tiles in extent or error occurred")
                self._safe_notify("⚠️ LCM overlay unavailable for this extent", type="warning")
                return

            logger.info(f"LCM colorized file: {colorized}")

            external_host = self._get_public_tile_host()
            logger.info(f"Starting LCM tile server on port {self.lcm_tile_port} with external_host={external_host}")

            lcm_url = self.lcm_tile_manager.get_tile_url(colorized, port=self.lcm_tile_port, external_host=external_host)
            if not lcm_url:
                logger.error("LCM tile manager returned None URL")
                self._safe_notify("❌ LCM tile server failed to start", type="negative")
                return

            logger.info(f"LCM tile URL: {lcm_url}")
            self._lcm_layer_signature = signature
            self._lcm_layer_url = lcm_url
            self.map_widget_obj.remove_tile_layer("LCM")
            self.map_widget_obj.add_tile_layer(lcm_url, name="LCM", opacity=self.lcm_opacity)
            self._safe_notify("✅ LCM overlay loaded", type="positive")
        except Exception as e:
            logger.exception(f"LCM overlay failed: {e}")
            self._safe_notify(f"❌ LCM overlay failed: {e}", type="negative")
        finally:
            self._lcm_refresh_in_progress = False

    def _create_legend_html(self, title: str, class_legends: List[tuple], title_color: str = "#333") -> str:
        """Generate HTML for a land cover class legend.

        Args:
            title: Legend title (e.g., "WorldCover Legend")
            class_legends: List of tuples (class_id, R, G, B, label)
            title_color: Color for the title text

        Returns:
            HTML string for the legend
        """
        items_html = ""
        for class_id, r, g, b, label in class_legends:
            color_hex = f"#{r:02x}{g:02x}{b:02x}"
            items_html += f"""
                <div style="display: flex; align-items: center; margin-bottom: 4px;">
                    <div style="
                        width: 20px;
                        height: 14px;
                        background-color: {color_hex};
                        border: 1px solid #999;
                        border-radius: 2px;
                        margin-right: 8px;
                        flex-shrink: 0;
                    "></div>
                    <span style="font-size: 11px; color: #555; line-height: 1.2;">{label}</span>
                </div>
            """

        return f"""
            <div style="
                background: white;
                border: 1px solid #ddd;
                border-radius: 6px;
                padding: 10px 12px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.1);
                max-height: 300px;
                overflow-y: auto;
            ">
                <div style="
                    font-size: 12px;
                    font-weight: 600;
                    color: {title_color};
                    margin-bottom: 8px;
                    padding-bottom: 6px;
                    border-bottom: 1px solid #eee;
                ">{title}</div>
                <div style="display: flex; flex-direction: column;">
                    {items_html}
                </div>
            </div>
        """

    def _show_worldcover_legend(self):
        """Display the WorldCover legend in the map."""
        if not self.worldcover_legend_container:
            return

        self.worldcover_legend_container.clear()
        legend_html = self._create_legend_html(title=f"🌍 WorldCover {self.worldcover_year}", class_legends=WORLDCOVER_CLASS_LEGENDS, title_color="#1a73e8")
        with self.worldcover_legend_container:
            ui.html(legend_html)

        # Auto-expand the legend when shown
        if self.worldcover_legend_expansion:
            self.worldcover_legend_expansion.value = True

    def _show_lcm_legend(self):
        """Display the LCM legend in the map."""
        if not self.lcm_legend_container:
            return

        self.lcm_legend_container.clear()
        legend_html = self._create_legend_html(title="🗺️ LCM 2020", class_legends=LCM_CLASS_LEGENDS, title_color="#e8710a")
        with self.lcm_legend_container:
            ui.html(legend_html)

        # Auto-expand the legend when shown
        if self.lcm_legend_expansion:
            self.lcm_legend_expansion.value = True

    def _hide_worldcover_legend(self):
        """Hide the WorldCover legend."""
        if self.worldcover_legend_container:
            self.worldcover_legend_container.clear()

    def _hide_lcm_legend(self):
        """Hide the LCM legend."""
        if self.lcm_legend_container:
            self.lcm_legend_container.clear()
