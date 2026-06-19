"""MGRS grid computation service for dynamic viewport-based tile display.

Computes Sentinel-2 MGRS tile boundaries from viewport bounds and returns
GeoJSON FeatureCollections suitable for interactive map overlays.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from loguru import logger

try:
    import mgrs as mgrs_lib

    HAS_MGRS = True
except ImportError:
    HAS_MGRS = False
    logger.warning("mgrs package not installed. MGRS grid overlay will be unavailable.")

try:
    from pyproj import Transformer

    HAS_PYPROJ = True
except ImportError:
    HAS_PYPROJ = False
    logger.warning("pyproj not installed. MGRS grid will fall back to a flat-earth approximation that does not match Sentinel-2 product footprints.")

# Sentinel-2 MGRS tiles are 100km × 100km in UTM
MGRS_TILE_SIZE_M = 100_000
# Minimum zoom level to display grid (avoid overwhelming at global scale)
MIN_ZOOM_FOR_GRID = 5
# Number of vertices per edge when densifying a UTM-aligned square back to
# WGS84. 10 samples per side keeps the polygon visually smooth at typical
# Leaflet zooms (the curvature from UTM meridian convergence is sub-pixel
# beyond that) while keeping the GeoJSON payload tiny.
EDGE_DENSIFY_POINTS = 10

# Cache pyproj Transformers per UTM EPSG. Constructing a Transformer is
# the slowest part of the polygon computation (~tens of ms each), so we
# memoize one forward + one inverse transformer per UTM zone for the
# lifetime of the process.
_TRANSFORMER_CACHE: Dict[int, Tuple["Transformer", "Transformer"]] = {}


class MGRSTile:
    """Represents a single MGRS tile with its code and WGS84 polygon."""

    __slots__ = ("code", "polygon")

    def __init__(self, code: str, polygon: List[List[float]]):
        self.code = code
        self.polygon = polygon  # List of [lon, lat] coordinate pairs (closed ring)


def is_available() -> bool:
    """Check if MGRS grid functionality is available."""
    return HAS_MGRS


def compute_visible_tiles(
    bbox: Tuple[float, float, float, float],
    zoom: int,
    max_tiles: int = 200,
) -> List[MGRSTile]:
    """Compute MGRS tiles visible within the given viewport.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84.
        zoom: Current map zoom level.
        max_tiles: Maximum tiles to return (safety limit).

    Returns:
        List of MGRSTile objects with codes and WGS84 polygons.
    """
    if not HAS_MGRS:
        return []

    if zoom < MIN_ZOOM_FOR_GRID:
        return []

    min_lon, min_lat, max_lon, max_lat = bbox

    # Clamp to valid lat/lon ranges
    min_lat = max(-80.0, min_lat)  # MGRS only goes to 84N / 80S
    max_lat = min(84.0, max_lat)
    min_lon = max(-180.0, min_lon)
    max_lon = min(180.0, max_lon)

    if min_lat >= max_lat or min_lon >= max_lon:
        return []

    converter = mgrs_lib.MGRS()
    seen = set()
    tiles = []

    # Sample points across the viewport to find intersecting MGRS tiles.
    # At 100km tile size, we need roughly 1 sample per ~0.5 degrees to catch all tiles.
    step_lat = min(0.5, (max_lat - min_lat) / 20)
    step_lon = min(0.5, (max_lon - min_lon) / 20)

    # Ensure at least a reasonable step
    step_lat = max(step_lat, 0.05)
    step_lon = max(step_lon, 0.05)

    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            try:
                # Convert lat/lon to MGRS at 100km precision (1 char precision = GZD + 2 chars)
                mgrs_code = converter.toMGRS(lat, lon, MGRSPrecision=0)
                # mgrs_code at precision 0 is like "33UUP" (5 chars: zone+band+col+row)
                code = mgrs_code.strip()

                if code and code not in seen:
                    seen.add(code)
                    polygon = _mgrs_tile_polygon(converter, code)
                    if polygon:
                        tiles.append(MGRSTile(code=code, polygon=polygon))

                    if len(tiles) >= max_tiles:
                        return tiles
            except Exception:
                pass  # Skip invalid coordinates (e.g., polar regions)
            lon += step_lon
        lat += step_lat

    return tiles


def compute_visible_tiles_geojson(
    bbox: Tuple[float, float, float, float],
    zoom: int,
    max_tiles: int = 200,
) -> Optional[dict]:
    """Compute visible MGRS tiles and return as a GeoJSON FeatureCollection.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84.
        zoom: Current map zoom level.
        max_tiles: Maximum tiles to return.

    Returns:
        GeoJSON FeatureCollection dict or None if no tiles.
    """
    tiles = compute_visible_tiles(bbox, zoom, max_tiles)
    if not tiles:
        return None

    features = []
    for tile in tiles:
        feature = {
            "type": "Feature",
            "properties": {
                "mgrs_code": tile.code,
                "label": tile.code,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [tile.polygon],
            },
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _mgrs_tile_polygon(converter, code: str) -> Optional[List[List[float]]]:
    """Compute the WGS84 polygon corners for an MGRS tile code.

    Builds the true 100 km × 100 km square in the tile's native UTM CRS and
    densifies each edge before reprojecting back to WGS84. The resulting
    polygon is a slight trapezoid (because of meridian convergence) rather
    than a lat/lon-axis-aligned rectangle, and it matches the footprint of
    a Sentinel-2 MGRS tile to within sub-meter accuracy after the snap to
    the 100 km UTM grid.

    Falls back to a flat-earth approximation when pyproj is not available.

    Returns a closed ring of [lon, lat] coordinates or None on failure.
    """
    if not HAS_PYPROJ:
        return _mgrs_tile_polygon_flat(converter, code)

    try:
        zone_number, hemisphere = _parse_utm_zone(code)
        if zone_number is None:
            # Polar (UPS) zone — not supported here. Fall back to the flat
            # approximation rather than dropping the tile entirely.
            return _mgrs_tile_polygon_flat(converter, code)

        epsg = (32600 if hemisphere == "north" else 32700) + zone_number
        fwd, inv = _get_transformers(epsg)

        # Anchor: SW corner of the 100 km square. ``toLatLon`` at precision 0
        # returns the SW corner in WGS84; reproject it into the native UTM
        # CRS and snap to the 100 km grid to wash out any sub-meter drift
        # introduced by the round-trip.
        sw_lat, sw_lon = converter.toLatLon(code)
        sw_east, sw_north = fwd.transform(sw_lon, sw_lat)
        sw_east = round(sw_east / MGRS_TILE_SIZE_M) * MGRS_TILE_SIZE_M
        sw_north = round(sw_north / MGRS_TILE_SIZE_M) * MGRS_TILE_SIZE_M

        # Densified UTM ring: SW → SE → NE → NW → SW. Sampling N points per
        # edge captures the curvature of the projected outline.
        n = EDGE_DENSIFY_POINTS
        side = MGRS_TILE_SIZE_M
        utm_ring: List[Tuple[float, float]] = []
        for i in range(n):  # south edge
            utm_ring.append((sw_east + side * i / n, sw_north))
        for i in range(n):  # east edge
            utm_ring.append((sw_east + side, sw_north + side * i / n))
        for i in range(n):  # north edge
            utm_ring.append((sw_east + side - side * i / n, sw_north + side))
        for i in range(n):  # west edge
            utm_ring.append((sw_east, sw_north + side - side * i / n))
        utm_ring.append((sw_east, sw_north))  # close

        polygon: List[List[float]] = []
        for east, north in utm_ring:
            lon, lat = inv.transform(east, north)
            polygon.append([lon, lat])
        return polygon
    except Exception as e:
        logger.debug(f"UTM polygon failed for {code}: {e}; falling back to flat approx")
        return _mgrs_tile_polygon_flat(converter, code)


def _mgrs_tile_polygon_flat(converter, code: str) -> Optional[List[List[float]]]:
    """Flat-earth polygon approximation (legacy fallback).

    Used when pyproj is unavailable or when the tile sits in a UPS (polar)
    zone. Builds a lat/lon-axis-aligned rectangle from the SW corner using a
    constant ``111_320 m / deg`` and a single ``cos(lat_sw)`` correction for
    longitude; this under-estimates the eastward extent and ignores meridian
    convergence, so the box is noticeably smaller than the real Sentinel-2
    granule on the south and east edges.
    """
    try:
        # Get the SW corner of the tile in lat/lon
        lat, lon = converter.toLatLon(code)

        # Approximate 100km in degrees at this latitude
        lat_extent = MGRS_TILE_SIZE_M / 111_320.0
        lon_extent = MGRS_TILE_SIZE_M / (111_320.0 * math.cos(math.radians(lat)))

        # Build polygon corners: SW, SE, NE, NW, SW (closed ring)
        sw = [lon, lat]
        se = [lon + lon_extent, lat]
        ne = [lon + lon_extent, lat + lat_extent]
        nw = [lon, lat + lat_extent]

        return [sw, se, ne, nw, sw]
    except Exception as e:
        logger.debug(f"Failed to compute polygon for MGRS code {code}: {e}")
        return None


def _parse_utm_zone(code: str) -> Tuple[Optional[int], str]:
    """Extract UTM zone number and hemisphere from an MGRS code.

    Returns ``(zone_number, "north" | "south")`` for UTM zones, or
    ``(None, "")`` for UPS (polar) zones whose band letter is A/B/Y/Z.

    MGRS codes start with a 1- or 2-digit zone number followed by the
    latitude band letter (C–X, excluding I and O). Bands N–X are northern
    hemisphere, C–M are southern.
    """
    if not code:
        return None, ""

    # Polar UPS bands have no leading digits — band letter is at index 0.
    if code[0] in ("A", "B", "Y", "Z"):
        return None, ""

    # Zone number is 1 or 2 leading digits.
    if len(code) >= 2 and code[1].isdigit():
        zone_str = code[:2]
        band_idx = 2
    else:
        zone_str = code[:1]
        band_idx = 1

    try:
        zone_number = int(zone_str)
    except ValueError:
        return None, ""

    if band_idx >= len(code):
        return None, ""

    band = code[band_idx]
    hemisphere = "north" if band >= "N" else "south"
    return zone_number, hemisphere


def _get_transformers(epsg: int) -> Tuple["Transformer", "Transformer"]:
    """Return cached (WGS84 → UTM, UTM → WGS84) Transformers for an EPSG code."""
    cached = _TRANSFORMER_CACHE.get(epsg)
    if cached is not None:
        return cached
    fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    _TRANSFORMER_CACHE[epsg] = (fwd, inv)
    return fwd, inv
