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

import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

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


class LC100Service:
    """Fetch and align CGLS-LC100 global land cover for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "lc100")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_aligned_lc100_path(self, reference_raster: str, target_resolution_m: int, year: str = "2019") -> Optional[str]:
        """Return a cached aligned LC100 GeoTIFF matching the reference raster grid.

        Streams only the overlapping window from the global COG via /vsis3/.
        """
        year = str(year)
        if year not in LC100_EPOCH_BY_YEAR:
            logger.warning(f"LC100 year {year} not available; supported: {LC100_AVAILABLE_YEARS}")
            return None

        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception:
            logger.exception("rasterio is required for LC100 alignment")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m, year)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached LC100 overlay: {aligned_path}")
            return str(aligned_path)

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            data, src_transform = self._read_global_window(left, bottom, right, top, target_resolution_m, year)
            if data is None:
                logger.warning("No LC100 data could be read for reference bounds")
                return None

            h, w = data.shape
            tmp_profile = {
                "driver": "GTiff",
                "dtype": "uint8",
                "count": 1,
                "crs": "EPSG:4326",
                "transform": src_transform,
                "width": w,
                "height": h,
                "nodata": LC100_NODATA,
            }

            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
                tmp_path = tmpf.name
            with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
                tmp_dst.write(data, 1)

            dst_profile = ref_profile.copy()
            dst_profile.update(
                driver="GTiff",
                dtype="uint8",
                count=1,
                compress="deflate",
                tiled=True,
                nodata=LC100_NODATA,
            )

            with rasterio.open(tmp_path) as src:
                with rasterio.open(aligned_path, "w", **dst_profile) as dst:
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=rasterio.band(dst, 1),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=dst_crs,
                        src_nodata=LC100_NODATA,
                        dst_nodata=LC100_NODATA,
                        resampling=Resampling.nearest,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"LC100 overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"LC100 alignment failed: {exc}")
            return None

    def get_colorized_lc100_path(self, reference_raster: str, target_resolution_m: int, year: str = "2019") -> Optional[str]:
        """Return an RGBA GeoTIFF with official LC100 class colours and transparent nodata."""
        try:
            import rasterio
        except Exception:
            logger.exception("rasterio is required for LC100 colorization")
            return None

        aligned = self.get_aligned_lc100_path(reference_raster, target_resolution_m, year)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|lc100-rgba-v1".encode("utf-8")).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

        try:
            with rasterio.open(aligned) as src:
                profile = src.profile.copy()
                profile.update({
                    "count": 4,
                    "dtype": "uint8",
                    "nodata": None,
                    "compress": "deflate",
                    "tiled": True,
                    "blockxsize": 256,
                    "blockysize": 256,
                })

                with rasterio.open(colorized_path, "w", **profile) as dst:
                    for _, window in src.block_windows():
                        classes = src.read(1, window=window)

                        r = np.zeros_like(classes, dtype=np.uint8)
                        g = np.zeros_like(classes, dtype=np.uint8)
                        b = np.zeros_like(classes, dtype=np.uint8)
                        a = np.zeros_like(classes, dtype=np.uint8)

                        for klass, (cr, cg, cb) in LC100_CLASSES.items():
                            mask = classes == klass
                            if np.any(mask):
                                r[mask] = cr
                                g[mask] = cg
                                b[mask] = cb
                                a[mask] = 255

                        dst.write(r, 1, window=window)
                        dst.write(g, 2, window=window)
                        dst.write(b, 3, window=window)
                        dst.write(a, 4, window=window)

            self._build_overviews(str(colorized_path))
            return str(colorized_path)
        except Exception:
            logger.exception("Failed to build colorized LC100 overlay")
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _vsis3_path(self, year: str) -> str:
        epoch = LC100_EPOCH_BY_YEAR[year]
        key = LC100_S3_KEY_PATTERN.format(year=year, epoch=epoch)
        return f"/vsis3/{LC100_S3_BUCKET}/{key}"

    def _s3_env(self):
        """Return a context manager that configures GDAL S3 credentials for eodata."""
        from vresto.api.config import CopernicusConfig
        from vresto.services.sentinel_stream import _gdal_s3_env

        config = CopernicusConfig()
        if config.has_static_s3_credentials():
            access_key, secret_key = config.get_s3_credentials()
        else:
            access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
            secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")
        if not access_key:
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; LC100 vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=LC100_S3_ENDPOINT)

    def _read_global_window(
        self,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
        year: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the global LC100 COG via vsis3 and read the intersecting window."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        path = self._vsis3_path(year)
        try:
            with self._s3_env():
                with rasterio.open(path) as src:
                    b = src.bounds
                    lon_min = max(left, b.left)
                    lat_min = max(bottom, b.bottom)
                    lon_max = min(right, b.right)
                    lat_max = min(top, b.top)
                    if lon_min >= lon_max or lat_min >= lat_max:
                        return None, None

                    window = rwin.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)

                    # Decimate to roughly the target ground resolution so GDAL can
                    # serve the read from an overview; never upsample beyond native.
                    mid_lat = math.radians((lat_min + lat_max) / 2.0)
                    lon_span_m = (lon_max - lon_min) * 111_320.0 * max(math.cos(mid_lat), 0.1)
                    lat_span_m = (lat_max - lat_min) * 110_540.0
                    out_w = max(1, min(int(round(window.width)), int(round(lon_span_m / target_resolution_m))))
                    out_h = max(1, min(int(round(window.height)), int(round(lat_span_m / target_resolution_m))))

                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"LC100 {year}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read LC100 {year} via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, year: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"lc100_{year}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


lc100_service = LC100Service()
