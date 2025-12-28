"""MapWidget encapsulates a NiceGUI leaflet map with drawing controls and bbox extraction."""

from typing import Callable, Optional, Tuple

from loguru import logger
from nicegui import events, ui


class MapWidget:
    """Encapsulates interactive map with drawing controls.

    Args:
        center: Tuple of (lat, lon) for initial map center.
        zoom: Initial zoom level.
        on_bbox_update: Callable invoked with bbox tuple (min_lon, min_lat, max_lon, max_lat).
    """

    def __init__(self, center: Tuple[float, float] = (59.3293, 18.0686), zoom: int = 13, on_bbox_update: Callable = None, title: str = "Mark the location", draw_control: bool = True):
        self.center = center
        self.zoom = zoom
        self.on_bbox_update = on_bbox_update or (lambda bbox: None)
        self.title = title
        self.show_draw_control = draw_control
        self._map = None
        self._tile_layers = {}

    def create(self, messages_column=None):
        """Create and return the NiceGUI leaflet map element and wire event handlers.

        The provided `messages_column` is used for emitting activity log messages.
        """
        with ui.card().classes("w-full flex-1 p-0 overflow-hidden"):
            if self.title:
                ui.label(self.title).classes("text-lg font-semibold m-3")

            draw_control = None
            if self.show_draw_control:
                draw_control = {
                    "draw": {"marker": True},
                    "edit": {"edit": True, "remove": True},
                }

            m = ui.leaflet(center=self.center, zoom=self.zoom, draw_control=draw_control)
            m.classes("w-full h-screen rounded-lg")

            # attach handlers
            self._setup_map_handlers(m, messages_column)

            self._map = m

        return m

    def _add_message(self, messages_column, text: str):
        """Add a message to the provided messages column."""
        try:
            with messages_column:
                ui.markdown(text)
        except Exception:
            # best-effort; don't raise in UI handlers
            logger.exception("Failed to add activity message")

    def _setup_map_handlers(self, m, messages_column):
        """Wire draw event handlers on the map element."""

        def handle_draw(e: events.GenericEventArguments):
            layer_type = e.args.get("layerType")
            coords = e.args.get("layer", {}).get("_latlng") or e.args.get("layer", {}).get("_latlngs")
            message = f"✅ Drawn {layer_type} at {coords}"
            logger.info(message)
            self._add_message(messages_column, message)
            ui.notify(f"Marked a {layer_type}", position="top", type="positive")
            # update bbox
            try:
                bbox = self._update_bbox_from_layer(e.args.get("layer", {}), layer_type)
                if bbox is not None:
                    self.on_bbox_update(bbox)
            except Exception:
                logger.exception("Failed to update bbox from layer")

        def handle_edit(e: events.GenericEventArguments = None):
            message = "✏️ Edit completed"
            logger.info(message)
            self._add_message(messages_column, message)
            ui.notify("Locations updated", position="top", type="info")

        def handle_delete(e: events.GenericEventArguments = None):
            message = "🗑️ Marker deleted"
            logger.info(message)
            self._add_message(messages_column, message)
            ui.notify("Marker removed", position="top", type="warning")
            # notify parent that bbox is cleared
            self.on_bbox_update(None)

        m.on("draw:created", handle_draw)
        m.on("draw:edited", handle_edit)
        m.on("draw:deleted", handle_delete)

    def set_center(self, lat: float, lon: float, zoom: Optional[int] = None):
        """Set the map center and optionally zoom level."""
        if self._map:
            self._map.set_center((lat, lon))
            if zoom is not None:
                self._map.set_zoom(zoom)

    def fit_bounds(self, bounds: Tuple[float, float, float, float]):
        """Fit the map to the given bounds (min_lat, min_lon, max_lat, max_lon)."""
        if self._map:
            min_lat, min_lon, max_lat, max_lon = bounds
            logger.info(f"Fitting map bounds to: {bounds}")
            # Use run_method to call Leaflet's fitBounds directly
            # Leaflet fitBounds takes [[south, west], [north, east]]
            self._map.run_method("fitBounds", [[min_lat, min_lon], [max_lat, max_lon]])

    def add_tile_layer(self, url: str, name: str, attribution: str = ""):
        """Add a tile layer to the map."""
        if self._map:
            # Check if layer already exists
            if name in self._tile_layers:
                self.remove_tile_layer(name)

            # In NiceGUI leaflet, tile_layer takes url_template as keyword argument
            options = {"attribution": attribution} if attribution else {}
            layer = self._map.tile_layer(url_template=url, options=options)
            self._tile_layers[name] = layer
            return layer
        return None

    def remove_tile_layer(self, name: str):
        """Remove a tile layer from the map by name."""
        if name in self._tile_layers:
            layer = self._tile_layers.pop(name)
            # NiceGUI leaflet elements are standard NiceGUI elements
            # They should be removed from their parent (the map)
            if self._map:
                self._map.remove_layer(layer)

    def clear_tile_layers(self):
        """Remove all custom tile layers."""
        for name in list(self._tile_layers.keys()):
            self.remove_tile_layer(name)

    def add_geojson(self, data: dict):
        """Add a GeoJSON layer to the map."""
        if self._map:
            self._map.run_method("addData", data)

    def clear_layers(self):
        """Clear all layers except base layers."""
        if self._map:
            self.clear_tile_layers()
            self._map.run_method("eachLayer", "function(layer) { if(layer.feature) layer.remove(); }")

    def _update_bbox_from_layer(self, layer: dict, layer_type: str):
        """Extract a bounding box (min_lon, min_lat, max_lon, max_lat) from a drawn layer.

        Supports marker (single latlng) and polygon/polyline with nested latlngs.
        Returns None if bbox cannot be computed.
        """
        try:
            if not layer:
                return None

            # Marker
            if layer_type == "marker":
                latlng = layer.get("_latlng")
                if latlng and "lat" in latlng and "lng" in latlng:
                    lat = float(latlng["lat"]) if not isinstance(latlng["lat"], (list, tuple)) else float(latlng["lat"][0])
                    lng = float(latlng["lng"]) if not isinstance(latlng["lng"], (list, tuple)) else float(latlng["lng"][0])
                    return (lng, lat, lng, lat)

            # Polylines / polygons may have nested _latlngs structure
            latlngs = layer.get("_latlngs") or layer.get("_latlng")
            pts = []

            def collect(pp):
                if isinstance(pp, dict) and "lat" in pp and "lng" in pp:
                    pts.append((float(pp["lat"]), float(pp["lng"])))
                elif isinstance(pp, list):
                    for x in pp:
                        collect(x)

            # The incoming structures from Leaflet can be dicts or lists; attempt several strategies
            if isinstance(latlngs, dict):
                collect(list(latlngs.values()))
            else:
                collect(latlngs)

            if not pts and "latlngs" in layer:
                collect(layer.get("latlngs"))

            coords = []
            for p in pts:
                try:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        lat = float(p[0])
                        lng = float(p[1])
                        coords.append((lat, lng))
                except Exception:
                    continue

            if not coords:
                return None

            lats = [c[0] for c in coords]
            lngs = [c[1] for c in coords]
            min_lat, max_lat = min(lats), max(lats)
            min_lng, max_lng = min(lngs), max(lngs)

            return (min_lng, min_lat, max_lng, max_lat)
        except Exception:
            logger.exception("Error computing bbox from layer")
            return None
