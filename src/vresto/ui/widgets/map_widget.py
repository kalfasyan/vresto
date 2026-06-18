"""MapWidget encapsulates a NiceGUI leaflet map with drawing controls and bbox extraction."""

import asyncio
from typing import Callable, Optional, Tuple

from loguru import logger
from nicegui import events, ui


class MapWidget:
    """Encapsulates interactive map with drawing controls.

    Args:
        center: Tuple of (lat, lon) for initial map center.
        zoom: Initial zoom level.
        on_bbox_update: Callable invoked with bbox tuple (min_lon, min_lat, max_lon, max_lat).
        on_tile_click: Callable invoked with MGRS tile code when a grid tile is clicked.
        on_moveend: Callable invoked with (bbox, zoom) when map view changes.
    """

    def __init__(self, center: Tuple[float, float] = (59.3293, 18.0686), zoom: int = 13, on_bbox_update: Callable = None, on_tile_click: Callable = None, on_moveend: Callable = None, title: str = "Mark the location", draw_control: bool = True):
        self.center = center
        self.zoom = zoom
        self.on_bbox_update = on_bbox_update or (lambda bbox: None)
        self.on_tile_click = on_tile_click or (lambda code: None)
        self.on_moveend = on_moveend or (lambda bbox, zoom: None)
        self.title = title
        self.show_draw_control = draw_control
        self._map = None
        self._tile_layers = {}
        self._grid_layer = None

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
            # Use JavaScript to call fitBounds on the Leaflet map instance
            map_id = self._map.id
            ui.run_javascript(f"""
                (function() {{
                    const el = getElement({map_id});
                    if (el && el.map) {{
                        el.map.fitBounds([[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]]);
                    }}
                }})();
            """)

    def add_tile_layer(self, url: str, name: str, attribution: str = "", opacity: Optional[float] = None):
        """Add a tile layer to the map."""
        if self._map:
            # Check if layer already exists
            if name in self._tile_layers:
                self.remove_tile_layer(name)

            # In NiceGUI leaflet, tile_layer takes url_template as keyword argument
            options = {"attribution": attribution} if attribution else {}
            if opacity is not None:
                options["opacity"] = max(0.0, min(1.0, float(opacity)))
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

    # ------------------------------------------------------------------
    # MGRS Grid Layer
    # ------------------------------------------------------------------

    def set_grid_layer(self, geojson: dict) -> None:
        """Set (replace) the interactive MGRS grid GeoJSON layer on the map.

        The GeoJSON features are expected to have a `mgrs_code` property.
        Clicking a feature triggers the `on_tile_click` callback.
        """
        if not self._map:
            return

        self.clear_grid_layer()

        import json

        map_id = self._map.id
        geojson_str = json.dumps(geojson)

        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map) return;
            const map = el.map;

            if (window._vrestoGridLayer) {{
                map.removeLayer(window._vrestoGridLayer);
            }}

            const geojson = {geojson_str};
            window._vrestoGridLayer = L.geoJSON(geojson, {{
                style: function(feature) {{
                    return {{
                        color: '#2563eb',
                        weight: 1.5,
                        fillColor: '#3b82f6',
                        fillOpacity: 0.05,
                        dashArray: '4 2'
                    }};
                }},
                onEachFeature: function(feature, layer) {{
                    if (feature.properties && feature.properties.mgrs_code) {{
                        layer.bindTooltip(feature.properties.mgrs_code, {{
                            permanent: false,
                            direction: 'center',
                            className: 'mgrs-tooltip'
                        }});
                        layer.on('click', function(e) {{
                            L.DomEvent.stopPropagation(e);
                            el.$emit('mgrs_tile_click', {{code: feature.properties.mgrs_code}});
                        }});
                        layer.on('mouseover', function() {{
                            layer.setStyle({{fillOpacity: 0.2, weight: 2.5}});
                        }});
                        layer.on('mouseout', function() {{
                            layer.setStyle({{fillOpacity: 0.05, weight: 1.5}});
                        }});
                    }}
                }}
            }}).addTo(map);
        }})();
        """

        ui.run_javascript(js)
        self._grid_layer = True

    def clear_grid_layer(self) -> None:
        """Remove the MGRS grid layer from the map."""
        if self._map and self._grid_layer:
            map_id = self._map.id
            js = f"""
            (function() {{
                const el = getElement({map_id});
                if (!el || !el.map) return;
                const map = el.map;
                if (window._vrestoGridLayer) {{
                    map.removeLayer(window._vrestoGridLayer);
                    window._vrestoGridLayer = null;
                }}
            }})();
            """
            ui.run_javascript(js)
            self._grid_layer = None

    def highlight_tile(self, code: str) -> None:
        """Visually mark a single MGRS tile as 'loading' (yellow fill).

        No-op if the grid layer is not currently shown or the code is not
        found. Intended to be paired with :meth:`clear_tile_highlight` once
        the streaming task completes.
        """
        if not self._map:
            return
        map_id = self._map.id
        # JSON-encode the code to be safe against injection
        import json

        code_js = json.dumps(code)
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                const f = layer.feature;
                if (f && f.properties && f.properties.mgrs_code === {code_js}) {{
                    layer.setStyle({{
                        color: '#eab308',
                        weight: 3,
                        fillColor: '#facc15',
                        fillOpacity: 0.35,
                        dashArray: null
                    }});
                    if (layer.bringToFront) layer.bringToFront();
                }}
            }});
        }})();
        """
        ui.run_javascript(js)

    def clear_tile_highlight(self) -> None:
        """Reset all grid-tile styles to the default (after loading completes)."""
        if not self._map:
            return
        map_id = self._map.id
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                layer.setStyle({{
                    color: '#2563eb',
                    weight: 1.5,
                    fillColor: '#3b82f6',
                    fillOpacity: 0.05,
                    dashArray: '4 2'
                }});
            }});
        }})();
        """
        ui.run_javascript(js)

    def setup_moveend(self) -> None:
        """Wire the moveend event to emit viewport changes for grid refresh."""
        if not self._map:
            return

        map_id = self._map.id

        # Wire custom event handlers on the NiceGUI element
        self._map.on("mgrs_tile_click", self._handle_tile_click)
        self._map.on("map_moveend", self._handle_moveend)

        # Set up moveend event on the Leaflet map that emits back to NiceGUI
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map) return;
            const map = el.map;
            if (map._vrestoMoveendWired) return;
            map._vrestoMoveendWired = true;

            let debounceTimer = null;
            map.on('moveend', function() {{
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(function() {{
                    const bounds = map.getBounds();
                    const zoom = map.getZoom();
                    el.$emit('map_moveend', {{
                        bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
                        zoom: zoom
                    }});
                }}, 300);
            }});
        }})();
        """

        # Defer JS execution until client is connected
        ui.context.client.on_connect(lambda: ui.run_javascript(js))

    def _handle_moveend(self, e: events.GenericEventArguments) -> None:
        """Handle map moveend event."""
        try:
            args = e.args if hasattr(e, "args") else e
            bbox = tuple(args.get("bbox", []))
            zoom = int(args.get("zoom", 0))
            if bbox and len(bbox) == 4:
                self.on_moveend(bbox, zoom)
        except Exception:
            logger.debug("Failed to handle moveend event")

    async def _handle_tile_click(self, e: events.GenericEventArguments) -> None:
        """Handle MGRS tile click event."""
        try:
            args = e.args if hasattr(e, "args") else e
            code = args.get("code", "")
            if code:
                result = self.on_tile_click(code)
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            logger.debug("Failed to handle tile click event")
