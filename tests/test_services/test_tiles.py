import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import rasterio

from vresto.services.tiles import TileManager


@pytest.fixture
def tile_manager():
    return TileManager()


@pytest.fixture
def temp_geotiff():
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        # Create a dummy GeoTIFF
        data = np.zeros((100, 100), dtype=np.uint16)
        with rasterio.open(tmp.name, "w", driver="GTiff", height=100, width=100, count=1, dtype="uint16", crs="EPSG:4326", transform=rasterio.transform.from_origin(0, 0, 1, 1)) as dst:
            dst.write(data, 1)

        path = tmp.name

    yield path
    if os.path.exists(path):
        os.remove(path)


def test_tile_manager_initial_state(tile_manager):
    assert tile_manager._active_client is None
    assert tile_manager._active_path is None
    assert tile_manager._temp_vrt is None


def test_is_available(tile_manager):
    # Depending on environment, this might be True or False
    # But it should return a boolean
    assert isinstance(tile_manager.is_available(), bool)


@patch("vresto.services.tiles.HAS_TILESERVER", False)
def test_get_tile_url_no_tileserver(tile_manager, temp_geotiff):
    url = tile_manager.get_tile_url(temp_geotiff)
    assert url is None


def test_get_tile_url_file_not_found(tile_manager):
    url = tile_manager.get_tile_url("non_existent.tif")
    assert url is None


@patch("vresto.services.tiles.HAS_TILESERVER", True)
@patch("vresto.services.tiles.TileClient")
def test_get_tile_url_success(mock_tile_client, tile_manager, temp_geotiff):
    mock_client_instance = mock_tile_client.return_value
    mock_client_instance.get_tile_url.return_value = "http://localhost:8080/tiles/{z}/{x}/{y}.png"

    url = tile_manager.get_tile_url(temp_geotiff)

    assert url is not None
    assert "http://localhost:8080/tiles" in url
    assert "t=" in url  # Cache buster
    assert tile_manager._active_client == mock_client_instance
    assert tile_manager._active_path == temp_geotiff


@patch("vresto.services.tiles.HAS_TILESERVER", True)
@patch("vresto.services.tiles.TileClient")
def test_get_tile_url_with_palette(mock_tile_client, tile_manager, temp_geotiff):
    mock_client_instance = mock_tile_client.return_value
    mock_client_instance.get_tile_url.return_value = "http://localhost:8080/tiles/{z}/{x}/{y}.png"

    palette = ["#ff0000", "#00ff00"]
    url = tile_manager.get_tile_url(temp_geotiff, palette=palette)

    assert "palette=%23ff0000%2C%2300ff00" in url


@patch("vresto.services.tiles.HAS_TILESERVER", True)
@patch("vresto.services.tiles.TileClient")
def test_get_tile_url_list_paths(mock_tile_client, tile_manager, temp_geotiff):
    mock_client_instance = mock_tile_client.return_value
    mock_client_instance.get_tile_url.return_value = "http://localhost:8080/tiles/{z}/{x}/{y}.png"

    paths = [temp_geotiff, temp_geotiff]
    url = tile_manager.get_tile_url(paths)

    assert url is not None
    assert tile_manager._temp_vrt is not None
    assert os.path.exists(tile_manager._temp_vrt)

    tile_manager.shutdown()
    assert tile_manager._temp_vrt is None


def test_shutdown(tile_manager):
    mock_client = MagicMock()
    tile_manager._active_client = mock_client
    tile_manager._active_path = "some/path"

    tile_manager.shutdown()

    mock_client.shutdown.assert_called_once()
    assert tile_manager._active_client is None
    assert tile_manager._active_path is None


def test_get_bounds(tile_manager):
    mock_client = MagicMock()
    mock_client.bounds.return_value = (10.0, 20.0, 30.0, 40.0)  # south, north, west, east
    tile_manager._active_client = mock_client

    bounds = tile_manager.get_bounds()
    assert bounds == (10.0, 30.0, 20.0, 40.0)  # (min_lat, min_lon, max_lat, max_lon)
