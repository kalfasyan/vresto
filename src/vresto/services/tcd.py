"""Tree Cover Density (TCD) service utilities.

Reads CLMS TCD-10 pantropical tiles from CDSE S3 (eodata) using GDAL
``/vsis3/`` virtual filesystem. Only the window covering the reference raster
is fetched via HTTP range requests against the COG tile.

Tile naming convention (3°×3° tiles):
    s3://eodata/CLMS/landcover_landuse/dynamic_land_cover/
        tcd_pantropical_10m_yearly_v1/{year}/01/01/
        LCFM_TCD-10_V100_{year}_{tile}_cog/LCFM_TCD-10_V100_{year}_{tile}_MAP.tif

Coverage is pantropical only (latitudes -48° .. 36°).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

from vresto.services.overlay_base import CogOverlayService, cdse_s3_env, degree_tiles, tile_code

TCD_S3_BUCKET = "eodata"
TCD_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
TCD_TILE_DEG = 3
TCD_MIN_LAT = -48.0
TCD_MAX_LAT = 36.0
TCD_NODATA = 255
TCD_AVAILABLE_YEARS = ["2020"]

TCD_S3_KEY_PATTERN = "CLMS/landcover_landuse/dynamic_land_cover/tcd_pantropical_10m_yearly_v1/{year}/01/01/LCFM_TCD-10_V100_{year}_{tile}_cog/LCFM_TCD-10_V100_{year}_{tile}_MAP.tif"

# Embedded TCD palette extracted from the source COG. Labels intentionally use
# the encoded percentage value to avoid inventing bin semantics not present in
# the STAC metadata.
TCD_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (10, 240, 240, 240, "10%"),
    (20, 253, 255, 115, "20%"),
    (30, 183, 255, 115, "30%"),
    (40, 148, 250, 92, "40%"),
    (50, 113, 240, 46, "50%"),
    (60, 76, 230, 0, "60%"),
    (70, 66, 199, 36, "70%"),
    (80, 65, 171, 62, "80%"),
    (90, 61, 145, 67, "90%"),
    (100, 43, 117, 51, "100%"),
]

TCD_CLASSES: Dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in TCD_CLASS_LEGENDS}


def tcd_has_coverage(bottom: float, top: float) -> bool:
    """Return whether a latitude span intersects TCD pantropical coverage."""
    return top > TCD_MIN_LAT and bottom < TCD_MAX_LAT


class TCDService(CogOverlayService):
    """Fetch and align TCD-10 data for Sentinel overlays via GDAL vsis3."""

    name = "tcd"
    label = "TCD"
    key_prefix = "tcd"
    rgba_token = "tcd-rgba-v1"
    aligned_nodata = 255
    snap_to_overview = True
    classes = TCD_CLASSES

    def _key(self, year: str = "2020") -> Optional[Tuple[str, ...]]:
        year = str(year)
        if year not in TCD_AVAILABLE_YEARS:
            logger.warning(f"TCD year {year} not available; supported: {TCD_AVAILABLE_YEARS}")
            return None
        return (year,)

    def _source_paths(self, left: float, bottom: float, right: float, top: float, year: str) -> List[str]:
        return [self._vsis3_path(tile_code(lat, lon), year) for lat, lon in degree_tiles(left, bottom, right, top, TCD_TILE_DEG)]

    def _s3_env(self):
        return cdse_s3_env(TCD_S3_ENDPOINT, "TCD")

    def _covers(self, left: float, bottom: float, right: float, top: float) -> bool:
        return tcd_has_coverage(bottom, top)

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        key = TCD_S3_KEY_PATTERN.format(year=year, tile=tile_code)
        return f"/vsis3/{TCD_S3_BUCKET}/{key}"


tcd_service = TCDService()
