"""LCM (Land Cover Map) service utilities.

Reads LCM-10 tiles from CDSE S3 (eodata) using GDAL /vsis3/ virtual filesystem.
Only the window covering the reference raster is fetched via HTTP range requests
against the COG tile — no full-file download, no OData API call needed.

Tile naming convention (3°×3° tiles):
    s3://eodata/CLMS/landcover_landuse/dynamic_land_cover/
        lcm_global_10m_yearly_v1/{year}/01/01/
        LCFM_LCM-10_V100_{year}_{tile}_cog/LCFM_LCM-10_V100_{year}_{tile}_MAP.tif

    where {tile} = N{lat:02d}E{lon:03d}  (SW corner in 3° steps)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

from vresto.services.overlay_base import CogOverlayService, cdse_s3_env, degree_tiles, tile_code

LCM_S3_BUCKET = "eodata"
LCM_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
LCM_TILE_DEG = 3  # each tile is 3° × 3°
LCM_AVAILABLE_YEARS = {"2020"}

LCM_S3_KEY_PATTERN = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/{year}/01/01/LCFM_LCM-10_V100_{year}_{tile}_cog/LCFM_LCM-10_V100_{year}_{tile}_MAP.tif"

# LCM-10 official colormap (class value -> (R, G, B, label)); 255 = no-data.
# Exported as list of tuples for easy legend rendering: (class_id, R, G, B, label)
LCM_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (10, 0, 100, 0, "Tree cover"),
    (20, 255, 187, 34, "Shrubland"),
    (30, 255, 255, 76, "Grassland"),
    (40, 240, 150, 255, "Cropland"),
    (50, 0, 150, 160, "Herbaceous wetland"),
    (60, 0, 207, 117, "Mangroves"),
    (70, 250, 230, 160, "Moss and lichen"),
    (80, 180, 180, 180, "Bare / sparse vegetation"),
    (90, 250, 0, 0, "Built-up"),
    (100, 0, 100, 200, "Permanent water bodies"),
    (110, 240, 240, 240, "Snow and ice"),
    (254, 10, 10, 10, "Unclassifiable"),
]

# Legacy dict format for backwards compatibility
LCM_CLASSES: Dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in LCM_CLASS_LEGENDS}


class LCMService(CogOverlayService):
    """Fetch and align LCM-10 data for Sentinel overlays via GDAL vsis3."""

    name = "lcm"
    label = "LCM"
    key_prefix = "lcm"
    rgba_token = "lcm-rgba-v3"
    aligned_nodata = 255
    snap_to_overview = True
    classes = LCM_CLASSES

    def _key(self, year: str = "2020") -> Optional[Tuple[str, ...]]:
        year = str(year)
        if year not in LCM_AVAILABLE_YEARS:
            logger.warning(f"LCM year {year} not available; supported: {LCM_AVAILABLE_YEARS}")
            return None
        return (year,)

    def _source_paths(self, left: float, bottom: float, right: float, top: float, year: str) -> List[str]:
        return [self._vsis3_path(tile_code(lat, lon), year) for lat, lon in degree_tiles(left, bottom, right, top, LCM_TILE_DEG)]

    def _s3_env(self):
        return cdse_s3_env(LCM_S3_ENDPOINT, "LCM")

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        key = LCM_S3_KEY_PATTERN.format(year=year, tile=tile_code)
        return f"/vsis3/{LCM_S3_BUCKET}/{key}"


lcm_service = LCMService()
