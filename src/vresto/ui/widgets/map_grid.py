"""MGRS grid layer of the map: tile outlines, product-count colours and highlights."""

import json
from typing import Optional

from nicegui import ui

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


class MapGridMixin:
    """Draws and styles the MGRS grid on the Leaflet map, mixed into MapWidget."""

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
