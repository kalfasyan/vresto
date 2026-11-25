"""WorldCover service utilities.

This module fetches ESA WorldCover tiles from the public S3 bucket using GDAL/vsis3
for efficient streaming (no full file downloads), and aligns them to Sentinel raster
grids for overlay visualization.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

from vresto.services.overlay_base import CogOverlayService, degree_tiles, public_s3_env, tile_code

WORLDCOVER_BUCKET = "esa-worldcover"
WORLDCOVER_PREFIX_BY_YEAR = {
    "2021": "v200/2021/map/",
    "2020": "v100/2020/map/",
}

# Tile naming pattern: ESA_WorldCover_10m_{year}_v{version}_{tile}_Map.tif
# e.g., ESA_WorldCover_10m_2021_v200_N39E023_Map.tif

WORLDCOVER_TILE_DEG = 3  # 3°×3° COG tiles
WORLDCOVER_REGION = "eu-central-1"

# WorldCover class definitions with labels for legend display.
# Exported as list of tuples: (class_id, R, G, B, label)
# Class 0 (no data) is excluded from legend display
WORLDCOVER_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (10, 0, 100, 0, "Trees"),
    (20, 255, 187, 34, "Shrubland"),
    (30, 255, 255, 76, "Grassland"),
    (40, 240, 150, 255, "Cropland"),
    (50, 250, 0, 0, "Built-up"),
    (60, 180, 180, 180, "Bare / sparse vegetation"),
    (70, 240, 240, 240, "Snow and ice"),
    (80, 0, 100, 200, "Permanent water bodies"),
    (90, 0, 150, 160, "Herbaceous wetland"),
    (95, 0, 207, 117, "Mangroves"),
    (100, 250, 230, 160, "Moss and lichen"),
]

# Legacy dict format for backwards compatibility
WORLDCOVER_CLASSES: Dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in WORLDCOVER_CLASS_LEGENDS}


class WorldCoverService(CogOverlayService):
    """Fetch and align ESA WorldCover (10 m, 2020 and 2021) for Sentinel overlays via GDAL vsis3."""

    name = "worldcover"
    label = "WorldCover"
    key_prefix = "wc"
    rgba_token = "wc-rgba-v2"
    aligned_nodata = 0
    snap_to_overview = True
    classes = WORLDCOVER_CLASSES

    def _key(self, year: str = "2021") -> Optional[Tuple[str, ...]]:
        year = str(year)
        if year not in WORLDCOVER_PREFIX_BY_YEAR:
            logger.warning(f"Unsupported WorldCover year: {year}")
            return None
        return (year,)

    def _source_paths(self, left: float, bottom: float, right: float, top: float, year: str) -> List[str]:
        return [self._vsis3_path(tile_code(lat, lon), year) for lat, lon in degree_tiles(left, bottom, right, top, WORLDCOVER_TILE_DEG)]

    def _s3_env(self):
        return public_s3_env(WORLDCOVER_REGION)

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        """Build vsis3 path for a WorldCover tile.

        Key format: v{version}/{year}/map/ESA_WorldCover_10m_{year}_v{version}_{tile}_Map.tif
        e.g., /vsis3/esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_N39E023_Map.tif
        """
        prefix = WORLDCOVER_PREFIX_BY_YEAR[year]
        version = "v200" if year == "2021" else "v100"
        filename = f"ESA_WorldCover_10m_{year}_{version}_{tile_code}_Map.tif"
        return f"/vsis3/{WORLDCOVER_BUCKET}/{prefix}{filename}"


worldcover_service = WorldCoverService()
