"""Point inspector and pixel history clicks for the Map Search tab."""

import asyncio
from html import escape as html_escape

from loguru import logger

from vresto.ui.overlays import (
    OverlaySpec,
    build_point_popup_html,
)

# Point inspector popup messages for a click that has no value to show.
INSPECT_OUTSIDE_TEXT = "Outside the streamed tile"


INSPECT_NODATA_TEXT = "No data at this point"


INSPECT_ERROR_TEXT = "Could not read the value at this point"


def _history_popup_html(lat: float, lon: float, text: str) -> str:
    """Popup marking a pixel-history point."""
    return f'<div style="font-size:12px;line-height:1.35"><b>📈 Pixel history</b><br>{html_escape(text)}<br><span style="color:#6b7280">{lat:.5f}, {lon:.5f}</span></div>'


class PointInspectorMixin:
    """Reads overlay values and pixel history at a clicked point, mixed into MapSearchTab."""

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
