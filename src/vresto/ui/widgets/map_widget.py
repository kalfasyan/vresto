"""MapWidget encapsulates a NiceGUI leaflet map with drawing controls and bbox extraction."""

import asyncio
import json
from typing import Callable, Optional, Tuple

from loguru import logger
from nicegui import events, ui

# Styles of the MGRS grid tiles. The flat style applies until product counts are known.
GRID_BASE_STYLE = {"color": "#2563eb", "weight": 1.5, "fillColor": "#3b82f6", "fillOpacity": 0.05, "dashArray": "4 2"}
# Tiles with no products in the date range: orange, which stays distinct from the blue ramp
# under red-green colour blindness (protan/deutan ΔE 23).
GRID_EMPTY_STYLE = {"color": "#eb6834", "weight": 1.5, "fillColor": "#eb6834", "fillOpacity": 0.18, "dashArray": "3 3"}
# Outline and opacity of tiles that have products; their fill colour comes from GRID_DENSITY_BINS.
GRID_DENSITY_STYLE = {"color": "#104281", "weight": 1.5, "fillOpacity": 0.55, "dashArray": None}
# Yellow highlight of the tile that is being loaded.
GRID_HIGHLIGHT_STYLE = {"color": "#eab308", "weight": 3, "fillColor": "#facc15", "fillOpacity": 0.35, "dashArray": None}
# Bins of products per tile per month, shared by the map colouring and the legend: (lowest rate in
# the bin, fill colour). A tile takes the colour of the highest bin its rate reaches; zero products
# uses GRID_EMPTY_STYLE. Binning by month keeps long date ranges from saturating the darkest bin.
# The colours are one blue hue, light to dark, validated as an ordinal ramp (monotone lightness,
# visible steps, light end at 2:1 on a light surface).
GRID_DENSITY_BINS: tuple[tuple[int, str], ...] = (
    (0, "#86b6ef"),
    (3, "#5598e7"),
    (6, "#256abf"),
    (9, "#184f95"),
    (12, "#0d366b"),
)


# How many times the map reloads a tile that failed, before leaving it blank.
TILE_RETRY_ATTEMPTS = 6

# Shared by every script that styles grid tiles: the tile whose imagery is on the map keeps its
# outline but drops its fill, so the density colour never covers the streamed image or overlays.
_GRID_TILE_STYLE_JS = """
window._vrestoTileStyle = function(layer) {
    const base = layer._vrestoBaseStyle;
    const code = layer.feature && layer.feature.properties && layer.feature.properties.mgrs_code;
    return base && code && code === window._vrestoStreamedCode ? Object.assign({}, base, {fillOpacity: 0}) : base;
};
"""


def grid_density_legend() -> list[tuple[str, str]]:
    """Return a (label, fill colour) pair for each products-per-month bin, such as ``("3–5", "#5598e7")``."""
    entries = []
    for index, (low, colour) in enumerate(GRID_DENSITY_BINS):
        if index + 1 < len(GRID_DENSITY_BINS):
            high = GRID_DENSITY_BINS[index + 1][0] - 1
            if low == 0:
                label = f"<{high + 1}"
            else:
                label = str(low) if high == low else f"{low}–{high}"
        else:
            label = f"{low}+"
        entries.append((label, colour))
    return entries


class MapWidget:
    """Encapsulates interactive map with drawing controls.

    Args:
        center: Tuple of (lat, lon) for initial map center.
        zoom: Initial zoom level.
        on_bbox_update: Callable invoked with bbox tuple (min_lon, min_lat, max_lon, max_lat).
        on_tile_click: Callable invoked with MGRS tile code when a grid tile is clicked.
        on_moveend: Callable invoked with (bbox, zoom) when map view changes.
        on_map_click: Callable invoked with (lat, lon) when the map is clicked while inspect
            mode is on (see :meth:`set_inspect_mode`). May be a coroutine function.
    """

    def __init__(
        self,
        center: Tuple[float, float] = (59.3293, 18.0686),
        zoom: int = 13,
        on_bbox_update: Callable = None,
        on_tile_click: Callable = None,
        on_moveend: Callable = None,
        title: str = "Mark the location",
        draw_control: bool = True,
        on_map_click: Callable = None,
    ):
        self.center = center
        self.zoom = zoom
        self.on_bbox_update = on_bbox_update or (lambda bbox: None)
        self.on_tile_click = on_tile_click or (lambda code: None)
        self.on_moveend = on_moveend or (lambda bbox, zoom: None)
        self.on_map_click = on_map_click or (lambda lat, lon: None)
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
            self._enable_tile_retry()
            return layer
        return None

    def _enable_tile_retry(self) -> None:
        """Retry map tiles that fail to load, with backoff, instead of leaving them blank.

        Each streamed tile and overlay is served by its own local tile server on a new port. The
        first requests can fail while that port is not reachable yet (WSL2, for instance, forwards
        a new port to the Windows browser only after a short delay), and Leaflet never asks for a
        failed tile again until the view changes. This reloads a failed tile up to six times,
        0.4 s to 13 s apart. Installed once per map; it covers tile layers added later as well.
        """
        if not self._map:
            return
        ui.run_javascript(
            f"""
            (function() {{
                const el = getElement({self._map.id});
                if (!el || !el.map) return;
                const map = el.map;
                const attach = function(layer) {{
                    if (!(layer instanceof L.TileLayer) || layer._vrestoRetry) return;
                    layer._vrestoRetry = true;
                    layer.on('tileerror', function(ev) {{
                        const img = ev.tile;
                        const attempt = img._vrestoRetries || 0;
                        if (attempt >= {TILE_RETRY_ATTEMPTS}) return;
                        img._vrestoRetries = attempt + 1;
                        setTimeout(function() {{
                            if (!img.src || !img.isConnected) return;
                            const base = img.src.replace(/[?&]_retry=\\d+/, '');
                            img.src = base + (base.indexOf('?') >= 0 ? '&' : '?') + '_retry=' + (attempt + 1);
                        }}, 400 * Math.pow(2, attempt));
                    }});
                }};
                if (!map._vrestoRetryHook) {{
                    map._vrestoRetryHook = true;
                    map.on('layeradd', function(e) {{ attach(e.layer); }});
                }}
                map.eachLayer(attach);
            }})();
            """
        )

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

    def set_legend(self, html: str):
        """Show a floating legend control on the map."""
        if not self._map:
            return

        map_id = self._map.id
        legend_html = json.dumps(html)
        ui.run_javascript(f"""
            (function() {{
                const el = getElement({map_id});
                if (!el || !el.map || typeof L === 'undefined') return;

                const map = el.map;
                if (map._vrestoLegend) {{
                    map.removeControl(map._vrestoLegend);
                    map._vrestoLegend = null;
                }}

                const legend = L.control({{position: 'bottomright'}});
                legend.onAdd = function() {{
                    const wrapper = L.DomUtil.create('div', 'vresto-legend');
                    wrapper.style.minWidth = '36px';

                    const header = L.DomUtil.create('div', '', wrapper);
                    header.style.display = 'flex';
                    header.style.justifyContent = 'flex-end';
                    header.style.marginBottom = '4px';

                    const toggle = L.DomUtil.create('button', '', header);
                    toggle.type = 'button';
                    toggle.textContent = '-';
                    toggle.title = 'Collapse legend';
                    toggle.style.width = '24px';
                    toggle.style.height = '24px';
                    toggle.style.border = '1px solid #d1d5db';
                    toggle.style.borderRadius = '6px';
                    toggle.style.background = 'rgba(255,255,255,0.95)';
                    toggle.style.cursor = 'pointer';
                    toggle.style.fontWeight = '600';
                    toggle.style.boxShadow = '0 1px 3px rgba(0,0,0,0.12)';

                    const body = L.DomUtil.create('div', '', wrapper);
                    body.innerHTML = {legend_html};

                    let collapsed = false;
                    toggle.onclick = function(event) {{
                        L.DomEvent.stop(event);
                        collapsed = !collapsed;
                        body.style.display = collapsed ? 'none' : 'block';
                        toggle.textContent = collapsed ? '+' : '-';
                        toggle.title = collapsed ? 'Expand legend' : 'Collapse legend';
                    }};

                    L.DomEvent.disableClickPropagation(wrapper);
                    L.DomEvent.disableScrollPropagation(wrapper);
                    return wrapper;
                }};

                legend.addTo(map);
                map._vrestoLegend = legend;
            }})();
        """)

    def clear_legend(self):
        """Remove the floating legend control from the map."""
        if not self._map:
            return

        map_id = self._map.id
        ui.run_javascript(f"""
            (function() {{
                const el = getElement({map_id});
                if (!el || !el.map) return;

                const map = el.map;
                if (map._vrestoLegend) {{
                    map.removeControl(map._vrestoLegend);
                    map._vrestoLegend = null;
                }}
            }})();
        """)

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
        Clicking a feature triggers the `on_tile_click` callback. The new layer
        starts in the flat style; :meth:`update_grid_density` colours it later.
        """
        if not self._map:
            return

        self.clear_grid_layer()

        map_id = self._map.id
        geojson_str = json.dumps(geojson)
        base_style = json.dumps(GRID_BASE_STYLE)

        js = (
            _GRID_TILE_STYLE_JS
            + f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map) return;
            const map = el.map;

            if (window._vrestoGridLayer) {{
                map.removeLayer(window._vrestoGridLayer);
            }}

            // Hover text: the code, plus product count and best cloud cover once density is known.
            // Grid zones (the world view below zoom 5) zoom in on click; 100 km tiles stream.
            const tileLabel = function(code, layer, isZone) {{
                const hint = isZone ? ' — click to zoom in' : (map._vrestoInspect ? '' : ' — click to stream');
                const d = layer._vrestoDensity;
                if (!d) return code + hint;
                if (d.count === 0) return code + ' · no products in date range' + (isZone ? hint : '');
                const perMonth = d.rate == null ? '' : (d.rate < 10 ? d.rate.toFixed(1) : String(Math.round(d.rate)));
                const count = (d.truncated ? '≥' : '') + d.count + (d.count === 1 ? ' product' : ' products')
                    + (isZone && d.tiles ? ' over ' + d.tiles + (d.tiles === 1 ? ' tile' : ' tiles') : '')
                    + (perMonth ? ' (' + perMonth + (isZone ? ' per tile per month)' : '/month)') : '');
                const cloud = d.min_cloud == null ? '' : ' · best ' + Math.round(d.min_cloud) + '% cloud';
                return code + ' · ' + count + cloud + hint;
            }};
            const hoverStyle = function(base) {{
                const fill = base.fillOpacity === 0 ? 0 : Math.min(0.9, base.fillOpacity + 0.15);
                return Object.assign({{}}, base, {{weight: 2.5, fillOpacity: fill}});
            }};

            const geojson = {geojson_str};
            window._vrestoGridLayer = L.geoJSON(geojson, {{
                style: function() {{
                    return {base_style};
                }},
                onEachFeature: function(feature, layer) {{
                    if (feature.properties && feature.properties.mgrs_code) {{
                        const code = feature.properties.mgrs_code;
                        const isZone = !!feature.properties.zone;
                        layer._vrestoBaseStyle = {base_style};
                        layer._vrestoDensity = null;
                        layer.bindTooltip(function() {{
                            return tileLabel(code, layer, isZone);
                        }}, {{
                            permanent: false,
                            direction: 'center',
                            className: 'mgrs-tooltip'
                        }});
                        layer.on('click', function(e) {{
                            // While inspecting, the click belongs to the map (value readout), not to tile selection.
                            if (map._vrestoInspect) return;
                            if (isZone) {{
                                L.DomEvent.stopPropagation(e);
                                map.fitBounds(layer.getBounds());
                                return;
                            }}
                            L.DomEvent.stopPropagation(e);
                            el.$emit('mgrs_tile_click', {{code: code}});
                        }});
                        layer.on('mouseover', function() {{
                            if (!layer._vrestoHighlighted) layer.setStyle(hoverStyle(window._vrestoTileStyle(layer)));
                        }});
                        layer.on('mouseout', function() {{
                            if (!layer._vrestoHighlighted) layer.setStyle(window._vrestoTileStyle(layer));
                        }});
                    }}
                }}
            }}).addTo(map);
        }})();
        """
        )

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
        code_js = json.dumps(code)
        highlight_js = json.dumps(GRID_HIGHLIGHT_STYLE)

        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                const f = layer.feature;
                if (f && f.properties && f.properties.mgrs_code === {code_js}) {{
                    layer._vrestoHighlighted = true;
                    layer.setStyle({highlight_js});
                    if (layer.bringToFront) layer.bringToFront();
                }}
            }});
        }})();
        """
        ui.run_javascript(js)

    def clear_tile_highlight(self) -> None:
        """Restore every grid tile to its own style (after loading completes).

        A tile goes back to its density colour, or to the flat style when no counts are shown.
        """
        if not self._map:
            return
        map_id = self._map.id
        js = (
            _GRID_TILE_STYLE_JS
            + f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                layer._vrestoHighlighted = false;
                if (layer._vrestoBaseStyle) layer.setStyle(window._vrestoTileStyle(layer));
            }});
        }})();
        """
        )
        ui.run_javascript(js)

    def update_grid_density(self, density: dict[str, dict], truncated: bool = False) -> None:
        """Colour the grid tiles by how many products they have, restyling the existing layer in place.

        Args:
            density: Maps tile codes (e.g. ``"31UFS"``) or grid zones (``"31U"``) to ``{"count": int,
                "min_cloud": float | None, "rate": float | None, "tiles": int | None}``. ``rate`` is
                products per tile per month and picks the colour bin; ``tiles`` is how many of a
                zone's tiles have products.
                Tiles missing from the mapping have no products.
            truncated: True when the counts are partial because the catalogue search hit its cap.
                Counts then read as minimums (``≥``), and a tile without a count stays plain rather
                than being shown as empty.
        """
        if not self._map:
            return
        map_id = self._map.id
        payload = json.dumps({"density": density, "truncated": bool(truncated)})
        bins = json.dumps(GRID_DENSITY_BINS)
        base_style = json.dumps(GRID_BASE_STYLE)
        empty_style = json.dumps(GRID_EMPTY_STYLE)
        density_style = json.dumps(GRID_DENSITY_STYLE)

        js = (
            _GRID_TILE_STYLE_JS
            + f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            const data = {payload};
            const bins = {bins};
            // Each bin is [lowest count, fill colour]; the highest bin reached gives the fill.
            const styleFor = function(count, rate) {{
                if (count === 0) return {empty_style};
                const level = rate == null ? count : rate;
                const fill = bins.reduce(function(acc, bin) {{ return level >= bin[0] ? bin[1] : acc; }}, bins[0][1]);
                return Object.assign({{}}, {density_style}, {{fillColor: fill}});
            }};
            window._vrestoGridLayer.eachLayer(function(layer) {{
                const code = layer.feature && layer.feature.properties && layer.feature.properties.mgrs_code;
                const info = data.density[code] || {{count: 0, min_cloud: null}};
                // Zero only means "no products" when the search was complete.
                const certain = info.count > 0 || !data.truncated;
                layer._vrestoDensity = certain ? Object.assign({{truncated: data.truncated}}, info) : null;
                layer._vrestoBaseStyle = certain ? styleFor(info.count, info.rate) : {base_style};
                if (!layer._vrestoHighlighted) layer.setStyle(window._vrestoTileStyle(layer));
            }});
        }})();
        """
        )
        ui.run_javascript(js)

    def clear_grid_density(self) -> None:
        """Return the grid tiles to the flat style, removing the product-count colours."""
        if not self._map:
            return
        map_id = self._map.id
        base_style = json.dumps(GRID_BASE_STYLE)

        js = (
            _GRID_TILE_STYLE_JS
            + f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                layer._vrestoDensity = null;
                layer._vrestoBaseStyle = {base_style};
                if (!layer._vrestoHighlighted) layer.setStyle(window._vrestoTileStyle(layer));
            }});
        }})();
        """
        )
        ui.run_javascript(js)

    def set_streamed_tile(self, code: Optional[str]) -> None:
        """Mark the grid tile whose imagery is on the map, or none, and restyle the grid.

        That tile keeps its outline but loses its fill, so the product-count colour does not hide
        the streamed image or the overlays drawn over it.
        """
        if not self._map:
            return
        map_id = self._map.id
        js = (
            _GRID_TILE_STYLE_JS
            + f"""
        (function() {{
            window._vrestoStreamedCode = {json.dumps(code)};
            const el = getElement({map_id});
            if (!el || !el.map || !window._vrestoGridLayer) return;
            window._vrestoGridLayer.eachLayer(function(layer) {{
                if (!layer._vrestoHighlighted && layer._vrestoBaseStyle) layer.setStyle(window._vrestoTileStyle(layer));
            }});
        }})();
        """
        )
        ui.run_javascript(js)

    def setup_moveend(self) -> None:
        """Wire the moveend event to emit viewport changes for grid refresh."""
        if not self._map:
            return

        map_id = self._map.id

        # Wire custom event handlers on the NiceGUI element
        self._map.on("mgrs_tile_click", self._handle_tile_click)
        self._map.on("map_moveend", self._handle_moveend)
        self._map.on("inspect_click", self._handle_inspect_click)

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

    async def _handle_inspect_click(self, e: events.GenericEventArguments) -> None:
        """Handle a map click made in inspect mode."""
        try:
            args = e.args if hasattr(e, "args") else e
            lat, lon = float(args["lat"]), float(args["lng"])
        except (KeyError, TypeError, ValueError):
            logger.debug("Ignoring inspect click without a usable position")
            return

        try:
            result = self.on_map_click(lat, lon)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Failed to handle inspect click")

    # ------------------------------------------------------------------
    # Point inspector: click mode and value popup
    # ------------------------------------------------------------------

    def set_inspect_mode(self, enabled: bool) -> None:
        """Turn the point-inspector click mode on or off.

        While on, a click on the map is reported through ``on_map_click`` instead of
        selecting an MGRS tile, and the cursor becomes a crosshair. Clicks made while a
        Leaflet.draw tool is in use are ignored so drawing keeps working.
        """
        if not self._map:
            return

        map_id = self._map.id
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map) return;
            const map = el.map;
            map._vrestoInspect = {json.dumps(bool(enabled))};
            map.getContainer().classList.toggle('vresto-inspecting', map._vrestoInspect);
            if (map._vrestoInspectWired) return;
            map._vrestoInspectWired = true;

            // Leaflet.draw fires its stop event before the browser's trailing click (a rectangle
            // ends on mouseup), so keep ignoring clicks briefly after a draw/edit/delete tool stops.
            let toolActive = false;
            ['draw:drawstart', 'draw:editstart', 'draw:deletestart'].forEach(function(type) {{
                map.on(type, function() {{ toolActive = true; }});
            }});
            ['draw:drawstop', 'draw:editstop', 'draw:deletestop'].forEach(function(type) {{
                map.on(type, function() {{ setTimeout(function() {{ toolActive = false; }}, 200); }});
            }});

            map.on('click', function(e) {{
                if (!map._vrestoInspect || toolActive) return;
                el.$emit('inspect_click', {{lat: e.latlng.lat, lng: e.latlng.lng}});
            }});
        }})();
        """
        ui.run_javascript(js)

    def show_point_popup(self, lat: float, lon: float, html: str) -> None:
        """Open a popup with ``html`` at a position, replacing any earlier inspector popup."""
        if not self._map:
            return

        map_id = self._map.id
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || typeof L === 'undefined') return;
            const map = el.map;
            if (map._vrestoPointPopup) map.closePopup(map._vrestoPointPopup);
            map._vrestoPointPopup = L.popup({{closeOnClick: false, maxWidth: 260}})
                .setLatLng([{float(lat)}, {float(lon)}])
                .setContent({json.dumps(html)})
                .openOn(map);
        }})();
        """
        ui.run_javascript(js)

    def clear_point_popup(self) -> None:
        """Close the inspector popup, if one is open."""
        if not self._map:
            return

        map_id = self._map.id
        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map) return;
            const map = el.map;
            if (map._vrestoPointPopup) {{
                map.closePopup(map._vrestoPointPopup);
                map._vrestoPointPopup = null;
            }}
        }})();
        """
        ui.run_javascript(js)

    # ------------------------------------------------------------------
    # Phase 4 map chrome: scale bar, coordinate readout, basemap switcher
    # ------------------------------------------------------------------

    def add_map_chrome(self) -> None:
        """Add scale bar, live coordinate readout, and basemap switcher to the map.

        Must be called after the map element is created and the client is
        connected (safe to call from setup_moveend or on_connect).
        """
        if not self._map:
            return
        map_id = self._map.id

        js = f"""
        (function() {{
            const el = getElement({map_id});
            if (!el || !el.map || el._vrestoChromeDone) return;
            el._vrestoChromeDone = true;
            const map = el.map;

            // ── Scale bar ──────────────────────────────────────────────
            L.control.scale({{imperial: false, position: 'bottomleft'}}).addTo(map);

            // ── Live coordinate + zoom readout ────────────────────────
            const coordCtrl = L.control({{position: 'bottomleft'}});
            coordCtrl.onAdd = function() {{
                const div = L.DomUtil.create('div', 'vresto-coords');
                div.style.cssText = 'background:rgba(255,255,255,0.85);padding:2px 6px;'
                    + 'border-radius:4px;font-size:11px;color:#333;font-family:monospace;'
                    + 'pointer-events:none;';
                div.textContent = 'Lat —  Lon —  z' + map.getZoom();
                map.on('mousemove', function(e) {{
                    div.textContent = 'Lat ' + e.latlng.lat.toFixed(5)
                        + '  Lon ' + e.latlng.lng.toFixed(5)
                        + '  z' + map.getZoom();
                }});
                map.on('zoomend', function() {{
                    const t = div.textContent;
                    div.textContent = t.replace(/z\\d+/, 'z' + map.getZoom());
                }});
                return div;
            }};
            coordCtrl.addTo(map);

            // ── Basemap switcher ───────────────────────────────────────
            const basemaps = {{
                '🗺️ OpenStreetMap': L.tileLayer(
                    'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
                    {{attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19}}
                ),
                '🛰️ Satellite (Esri)': L.tileLayer(
                    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
                    {{attribution: 'Tiles © Esri — Source: Esri, Maxar, GeoEye, Earthstar Geographics', maxZoom: 19}}
                ),
                '🌑 CARTO Dark': L.tileLayer(
                    'https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',
                    {{attribution: '© <a href="https://carto.com/">CARTO</a>', subdomains: 'abcd', maxZoom: 19}}
                ),
                '☁️ CARTO Light': L.tileLayer(
                    'https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',
                    {{attribution: '© <a href="https://carto.com/">CARTO</a>', subdomains: 'abcd', maxZoom: 19}}
                ),
            }};

            // Remove the default NiceGUI OSM basemap that was added at creation,
            // and wire our own OSM layer so layers-control tracks it.
            map.eachLayer(function(l) {{
                if (l._url && l._url.includes('openstreetmap.org')) map.removeLayer(l);
            }});
            basemaps['🗺️ OpenStreetMap'].addTo(map);

            L.control.layers(basemaps, {{}}, {{position: 'topright', collapsed: true}}).addTo(map);
        }})();
        """

        ui.context.client.on_connect(lambda: ui.run_javascript(js))
