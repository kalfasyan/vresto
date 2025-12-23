"""MapWidget encapsulates a NiceGUI leaflet map with drawing controls and bbox extraction."""

from typing import Callable, Tuple

from loguru import logger
from nicegui import events, ui


class MapWidget:
    """Encapsulates interactive map with drawing controls.

    Args:
        center: Tuple of (lat, lon) for initial map center.
        zoom: Initial zoom level.
        on_bbox_update: Callable invoked with bbox tuple (min_lon, min_lat, max_lon, max_lat).
    """

    def __init__(self, center: Tuple[float, float] = (59.3293, 18.0686), zoom: int = 13, on_bbox_update: Callable = None):
        self.center = center
        self.zoom = zoom
        self.on_bbox_update = on_bbox_update or (lambda bbox: None)
        self._map = None

    def create(self, messages_column):
        """Create and return the NiceGUI leaflet map element and wire event handlers.

        The provided `messages_column` is used for emitting activity log messages.
        """
        with ui.card().classes("flex-1"):
            ui.label("Mark the location").classes("text-lg font-semibold mb-3")

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
                    pts.append((float(pp[1]) if isinstance(pp[1], (list, tuple)) else float(pp["lat"]), float(pp["lng"])))
                elif isinstance(pp, list):
                    for x in pp:
                        collect(x)

            # The incoming structures from Leaflet can be dicts or lists; attempt several strategies
            if isinstance(latlngs, dict):
                # maybe a dict with numeric keys
                collect(list(latlngs.values()))
            else:
                collect(latlngs)

            # fallback if pts empty but layer contains 'latlngs' key
            if not pts and "latlngs" in layer:
                collect(layer.get("latlngs"))

            # Try to coerce points from common Leaflet shapes
            coords = []
            for p in pts:
                try:
                    # p might be (lat, lng) tuple
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

            # Return bbox as (min_lon, min_lat, max_lon, max_lat)
            return (min_lng, min_lat, max_lng, max_lat)
        except Exception:
            logger.exception("Error computing bbox from layer")
            return None
