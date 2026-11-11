"""Overlay picker, loading and legends for the Map Search tab."""

import asyncio
import re
import time
from datetime import timezone
from typing import Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api.config import CopernicusConfig
from vresto.api.stac_assets import parse_date_like
from vresto.services.sentinel_stream import (
    sentinel_stream_service,
)
from vresto.services.tiles import tile_pool
from vresto.ui.overlays import (
    OVERLAY_NAMES,
    OverlayRequest,
    OverlaySpec,
    build_overlay_legend_html,
)

# Value of the overlay picker when no overlay is chosen.
OVERLAY_NONE = ""


class OverlaysMixin:
    """Data overlays (WorldCover, DEM, NDVI, ...) on the streamed tile, mixed into MapSearchTab."""

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
