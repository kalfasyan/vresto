"""MGRS grid computation service for dynamic viewport-based tile display.

Computes Sentinel-2 MGRS tile boundaries from viewport bounds and returns
GeoJSON FeatureCollections suitable for interactive map overlays.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from loguru import logger

try:
    import mgrs as mgrs_lib

    HAS_MGRS = True
except ImportError:
    HAS_MGRS = False
    logger.warning("mgrs package not installed. MGRS grid overlay will be unavailable.")

# Sentinel-2 MGRS tiles are 100km × 100km in UTM
MGRS_TILE_SIZE_M = 100_000
# Minimum zoom level to display grid (avoid overwhelming at global scale)
MIN_ZOOM_FOR_GRID = 5


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

    Returns a closed ring of [lon, lat] coordinates or None on failure.
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
