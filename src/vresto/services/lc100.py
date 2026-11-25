"""CGLS-LC100 (Copernicus Global Land Cover 100m, collection 3) service utilities.

Reads the global discrete land-cover classification COG from CDSE S3 (eodata)
using GDAL /vsis3/. The product is a single global Cloud-Optimized GeoTIFF per
year with internal overviews, so only the window covering the reference raster is
fetched via HTTP range requests — no full-file download and no tiling/mosaic.

Path convention (single global COG, annual 2015-2019):
    s3://eodata/CLMS/landcover_landuse/dynamic_land_cover/lc_global_100m_yearly_v3/
        {year}/01/01/PROBAV_LC100_global_v3.1.2_{year}_cog/
        PROBAV_LC100_global_v3.1.2_{year}-{epoch}_Discrete-Classification-map_EPSG-4326.tif
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

from vresto.services.overlay_base import CogOverlayService, cdse_s3_env

LC100_S3_BUCKET = "eodata"
LC100_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"

# The annual epoch label embedded in the filename differs per year.
LC100_EPOCH_BY_YEAR = {
    "2015": "base",
    "2016": "conso",
    "2017": "conso",
    "2018": "conso",
    "2019": "nrt",
}
LC100_AVAILABLE_YEARS = list(LC100_EPOCH_BY_YEAR.keys())
LC100_NODATA = 255

LC100_S3_KEY_PATTERN = "CLMS/landcover_landuse/dynamic_land_cover/lc_global_100m_yearly_v3/{year}/01/01/PROBAV_LC100_global_v3.1.2_{year}_cog/PROBAV_LC100_global_v3.1.2_{year}-{epoch}_Discrete-Classification-map_EPSG-4326.tif"

# Official CGLS-LC100 discrete classification palette (verified against the
# product's embedded colour table). (class_id, R, G, B, label); 0 (unknown) and
# 255 (no-data) are intentionally excluded so they render transparent.
LC100_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (20, 255, 187, 34, "Shrubs"),
    (30, 255, 255, 76, "Herbaceous vegetation"),
    (40, 240, 150, 255, "Cropland"),
    (50, 250, 0, 0, "Built-up"),
    (60, 180, 180, 180, "Bare / sparse vegetation"),
    (70, 240, 240, 240, "Snow and ice"),
    (80, 0, 50, 200, "Permanent water bodies"),
    (90, 0, 150, 160, "Herbaceous wetland"),
    (100, 250, 230, 160, "Moss and lichen"),
    (111, 88, 72, 31, "Closed forest, evergreen needle leaf"),
    (112, 0, 153, 0, "Closed forest, evergreen broad leaf"),
    (113, 112, 102, 62, "Closed forest, deciduous needle leaf"),
    (114, 0, 204, 0, "Closed forest, deciduous broad leaf"),
    (115, 78, 117, 31, "Closed forest, mixed"),
    (116, 0, 120, 0, "Closed forest, unknown"),
    (121, 102, 96, 0, "Open forest, evergreen needle leaf"),
    (122, 141, 180, 0, "Open forest, evergreen broad leaf"),
    (123, 141, 116, 0, "Open forest, deciduous needle leaf"),
    (124, 160, 220, 0, "Open forest, deciduous broad leaf"),
    (125, 146, 153, 0, "Open forest, mixed"),
    (126, 100, 140, 0, "Open forest, unknown"),
    (200, 0, 0, 128, "Open sea"),
]

# Legacy dict format used by the colorizer.
LC100_CLASSES: Dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in LC100_CLASS_LEGENDS}


class LC100Service(CogOverlayService):
    """Fetch and align CGLS-LC100 global land cover for Sentinel overlays via GDAL vsis3."""

    name = "lc100"
    label = "LC100"
    key_prefix = "lc100"
    rgba_token = "lc100-rgba-v1"
    aligned_nodata = LC100_NODATA
    classes = LC100_CLASSES

    def _key(self, year: str = "2019") -> Optional[Tuple[str, ...]]:
        year = str(year)
        if year not in LC100_EPOCH_BY_YEAR:
            logger.warning(f"LC100 year {year} not available; supported: {LC100_AVAILABLE_YEARS}")
            return None
        return (year,)

    def _source_paths(self, left: float, bottom: float, right: float, top: float, year: str) -> List[str]:
        return [self._vsis3_path(year)]

    def _s3_env(self):
        return cdse_s3_env(LC100_S3_ENDPOINT, "LC100")

    def _vsis3_path(self, year: str) -> str:
        epoch = LC100_EPOCH_BY_YEAR[year]
        key = LC100_S3_KEY_PATTERN.format(year=year, epoch=epoch)
        return f"/vsis3/{LC100_S3_BUCKET}/{key}"


lc100_service = LC100Service()
