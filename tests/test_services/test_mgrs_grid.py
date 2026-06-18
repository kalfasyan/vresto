"""Tests for MGRS grid computation service."""

import pytest

from vresto.services.mgrs_grid import (
    MGRSTile,
    compute_visible_tiles,
    compute_visible_tiles_geojson,
    is_available,
)


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
        assert all(len(t.polygon) == 5 for t in tiles)  # closed ring

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
        assert len(feature["geometry"]["coordinates"][0]) == 5  # closed

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
