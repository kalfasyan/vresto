"""Tests for MGRS grid computation service."""

import pytest

from vresto.services.mgrs_grid import (
    EDGE_DENSIFY_POINTS,
    MGRS_TILE_SIZE_M,
    MGRSTile,
    S2_GRANULE_SIZE_M,
    compute_visible_tiles,
    compute_visible_tiles_geojson,
    is_available,
)

# A UTM-densified ring has N points per edge × 4 edges + 1 closing vertex.
_EXPECTED_RING_LEN = 4 * EDGE_DENSIFY_POINTS + 1


@pytest.mark.skipif(not is_available(), reason="mgrs package not installed")
class TestMGRSGrid:
    def test_is_available(self):
        assert is_available() is True

    def test_compute_visible_tiles_low_zoom(self):
        """At zoom < 5, no tiles should be returned."""
        bbox = (10.0, 45.0, 15.0, 50.0)
        tiles = compute_visible_tiles(bbox, zoom=3)
        assert tiles == []

    def test_compute_visible_tiles_valid_bbox(self):
        """At zoom >= 5 with a European bbox, should return tiles."""
        bbox = (11.0, 46.0, 12.0, 47.0)  # ~Austria/Italy border
        tiles = compute_visible_tiles(bbox, zoom=7)
        assert len(tiles) > 0
        assert all(isinstance(t, MGRSTile) for t in tiles)
        assert all(len(t.code) >= 3 for t in tiles)
        # Closed densified ring (UTM-aware) or flat 5-point fallback for UPS zones.
        assert all(len(t.polygon) in (5, _EXPECTED_RING_LEN) for t in tiles)
        # First and last points must be identical (closed ring).
        assert all(t.polygon[0] == t.polygon[-1] for t in tiles)

    def test_compute_visible_tiles_max_limit(self):
        """Should respect max_tiles limit."""
        bbox = (-10.0, 30.0, 30.0, 60.0)  # large area
        tiles = compute_visible_tiles(bbox, zoom=6, max_tiles=10)
        assert len(tiles) <= 10

    def test_compute_visible_tiles_geojson_format(self):
        """GeoJSON output should be a valid FeatureCollection."""
        bbox = (11.0, 46.0, 12.0, 47.0)
        geojson = compute_visible_tiles_geojson(bbox, zoom=7)
        assert geojson is not None
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) > 0

        feature = geojson["features"][0]
        assert feature["type"] == "Feature"
        assert "mgrs_code" in feature["properties"]
        assert feature["geometry"]["type"] == "Polygon"
        assert len(feature["geometry"]["coordinates"]) == 1  # single ring
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) in (5, _EXPECTED_RING_LEN)
        assert ring[0] == ring[-1]

    def test_compute_visible_tiles_geojson_none_low_zoom(self):
        """Should return None at low zoom."""
        bbox = (10.0, 45.0, 15.0, 50.0)
        geojson = compute_visible_tiles_geojson(bbox, zoom=3)
        assert geojson is None

    def test_tile_polygon_coordinates_valid(self):
        """Polygon coordinates should be in valid WGS84 range."""
        bbox = (11.0, 46.0, 12.0, 47.0)
        tiles = compute_visible_tiles(bbox, zoom=7)
        for tile in tiles:
            for lon, lat in tile.polygon:
                assert -180.0 <= lon <= 180.0
                assert -90.0 <= lat <= 90.0

    def test_invalid_bbox_returns_empty(self):
        """Invalid bbox (min > max) should return empty."""
        bbox = (15.0, 50.0, 10.0, 45.0)  # inverted
        tiles = compute_visible_tiles(bbox, zoom=7)
        assert tiles == []


@pytest.mark.skipif(not is_available(), reason="mgrs package not installed")
class TestMGRSTileUtmAccuracy:
    """Verify the UTM-aware polygon matches the Sentinel-2 granule footprint.

    The polygon's vertices, projected back into the tile's native UTM CRS,
    must be anchored at the canonical MGRS tile's NW corner on the 100 km
    grid, while the actual footprint extends to the east and south to the
    full 109.8 km × 109.8 km Sentinel-2 granule size.
    """

    def test_polygon_vertices_lie_on_utm_grid(self):
        pyproj = pytest.importorskip("pyproj")
        bbox = (11.0, 46.0, 12.0, 47.0)  # zone 33, northern hemisphere
        tiles = compute_visible_tiles(bbox, zoom=7)
        assert tiles, "expected at least one tile in this viewport"

        # Pick one tile from the viewport whose code parses cleanly as a UTM zone.
        from vresto.services.mgrs_grid import _parse_utm_zone

        utm_tiles = [t for t in tiles if _parse_utm_zone(t.code)[0] is not None]
        assert utm_tiles, "expected at least one UTM-zone (non-polar) tile"
        tile = utm_tiles[0]

        # Skip the flat-earth fallback shape (only 5 points).
        assert len(tile.polygon) == _EXPECTED_RING_LEN

        zone_number, hemisphere = _parse_utm_zone(tile.code)
        epsg = (32600 if hemisphere == "north" else 32700) + zone_number
        fwd = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)

        eastings = []
        northings = []
        for lon, lat in tile.polygon[:-1]:  # drop closing duplicate
            east, north = fwd.transform(lon, lat)
            eastings.append(east)
            northings.append(north)

        nw_east = min(eastings)
        nw_north = max(northings)
        ne_east = max(eastings)
        sw_north = min(northings)

        # NW corner snaps cleanly to the 100 km MGRS grid.
        assert abs(nw_east - round(nw_east / MGRS_TILE_SIZE_M) * MGRS_TILE_SIZE_M) < 1.0
        assert abs(nw_north - round(nw_north / MGRS_TILE_SIZE_M) * MGRS_TILE_SIZE_M) < 1.0
        # Extent is the full Sentinel-2 granule footprint in each axis.
        assert abs((ne_east - nw_east) - S2_GRANULE_SIZE_M) < 1.0
        assert abs((nw_north - sw_north) - S2_GRANULE_SIZE_M) < 1.0

    def test_utm_polygon_is_wider_than_flat_approximation(self):
        """At mid-latitude the true UTM polygon is wider in lon than the
        flat-earth ``cos(lat_sw)`` rectangle on its top edge."""
        pytest.importorskip("pyproj")

        import mgrs as mgrs_lib

        from vresto.services.mgrs_grid import (
            _mgrs_tile_polygon,
            _mgrs_tile_polygon_flat,
        )

        converter = mgrs_lib.MGRS()
        # Pick a known mid-latitude MGRS tile (Austria).
        code = converter.toMGRS(47.0, 11.5, MGRSPrecision=0).strip()

        utm_ring = _mgrs_tile_polygon(converter, code)
        flat_ring = _mgrs_tile_polygon_flat(converter, code)
        assert utm_ring is not None and flat_ring is not None

        utm_lon_span = max(p[0] for p in utm_ring) - min(p[0] for p in utm_ring)
        flat_lon_span = max(p[0] for p in flat_ring) - min(p[0] for p in flat_ring)

        # The flat approximation uses cos(lat_sw) and so undershoots the
        # true east extent; the UTM-projected ring must be at least as wide.
        assert utm_lon_span > flat_lon_span
