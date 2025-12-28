"""Test script to verify TileManager and localtileserver integration."""

import os

import numpy as np
import rasterio
from rasterio.transform import from_origin

from vresto.services.tiles import tile_manager


def create_dummy_geotiff(filename, color=(100, 150, 200)):
    """Create a small dummy GeoTIFF for testing."""
    width, height = 512, 512
    # Simple transform and CRS
    transform = from_origin(10.0, 50.0, 0.001, 0.001)
    crs = "EPSG:4326"

    with rasterio.open(
        filename,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=3,
        dtype="uint8",
        crs=crs,
        transform=transform,
    ) as dst:
        for i in range(1, 4):
            dst.write(np.full((height, width), color[i - 1], dtype="uint8"), i)
    print(f"Created dummy GeoTIFF: {filename}")


def test_tile_manager():
    """Test the TileManager with the dummy GeoTIFF."""
    tif_path = "test_tile_server.tif"
    create_dummy_geotiff(tif_path)

    try:
        print("Checking if TileManager is available...")
        if not tile_manager.is_available():
            print("❌ TileManager is NOT available (localtileserver not installed correctly)")
            return

        print(f"Starting tile server for {tif_path}...")
        url = tile_manager.get_tile_url(os.path.abspath(tif_path))

        if url:
            print("✅ Tile server started successfully!")
            print(f"🔗 Tile URL: {url}")

            bounds = tile_manager.get_bounds()
            print(f"📍 Bounds (s, w, n, e): {bounds}")

            print("\nTo test in browser, copy the Tile URL and replace {z}/{x}/{y} with 13/4209/2692 (for example)")
            print("Note: The server will stop when this script ends.")
        else:
            print("❌ Failed to get tile URL from TileManager")

    finally:
        tile_manager.shutdown()
        if os.path.exists(tif_path):
            os.remove(tif_path)
            print(f"Cleaned up {tif_path}")


if __name__ == "__main__":
    test_tile_manager()
