"""NDVI long-term statistics (NDVI-LTS) service utilities.

Reads the CLMS global NDVI-LTS COGs from CDSE S3 (eodata) using GDAL
``/vsis3/`` virtual filesystem. The dataset is a single global COG per dekadal
period and statistic, so only the overlapping window is read via HTTP range
requests.

Path convention (static climatology, directory year fixed to 1999):
    s3://eodata/CLMS/bio-geophysical/vegetation_indices/ndvi-lts_global_1km_10daily_v3/
        1999/{MM}/{DD}/c_gls_NDVI-LTS_1999-2019-{MMDD}_GLOBE_VGT-PROBAV_V3.0.1_cog/
        c_gls_NDVI-MEAN-LTS_1999-2019-{MMDD}_GLOBE_VGT-PROBAV_V3.0.1.tiff
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
from loguru import logger

NDVI_S3_BUCKET = "eodata"
NDVI_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
NDVI_DIR_YEAR = "1999"
NDVI_STAT = "MEAN"
NDVI_NODATA = 255
NDVI_SEA = 254
NDVI_VALID_MAX_DN = 250
NDVI_SCALE = 0.004
NDVI_OFFSET = -0.08
NDVI_MIN_VALUE = 0.0
NDVI_MAX_VALUE = 0.9

NDVI_S3_KEY_PATTERN = (
    "CLMS/bio-geophysical/vegetation_indices/ndvi-lts_global_1km_10daily_v3/1999/{month}/{day}/c_gls_NDVI-LTS_1999-2019-{month}{day}_GLOBE_VGT-PROBAV_V3.0.1_cog/c_gls_NDVI-MEAN-LTS_1999-2019-{month}{day}_GLOBE_VGT-PROBAV_V3.0.1.tiff"
)

NDVI_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 110, 78, 51, "0.0-0.2"),
    (1, 163, 117, 73, "0.2-0.4"),
    (2, 200, 166, 94, "0.4-0.6"),
    (3, 138, 173, 90, "0.6-0.75"),
    (4, 46, 125, 50, "0.75-0.9"),
]


def ndvi_lts_period_from_date(date_str: str) -> Tuple[str, str]:
    """Map a streamed Sentinel date to the matching NDVI-LTS dekadal period.

    The NDVI-LTS climatology is published for days 01, 11, and 21 of each month.
    Use the current dekad rather than a future period.
    """
    digits = "".join(char for char in str(date_str or "") if char.isdigit())
    if len(digits) < 8:
        raise ValueError(f"Unsupported date format: {date_str!r}")
    parsed = datetime.strptime(digits[:8], "%Y%m%d")
    if parsed.day >= 21:
        day = "21"
    elif parsed.day >= 11:
        day = "11"
    else:
        day = "01"
    return parsed.strftime("%m"), day


class NDVIService:
    """Fetch and align NDVI-LTS data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "ndvi")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_ndvi_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return a cached aligned NDVI-LTS raster matching the reference grid."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for NDVI alignment: {exc}")
            return None

        month, day = ndvi_lts_period_from_date(date)
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, month, day)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached NDVI overlay: {aligned_path}")
            return str(aligned_path)

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            data, src_transform = self._read_global_window(left, bottom, right, top, target_resolution_m, month, day)
            if data is None:
                logger.warning("No NDVI-LTS data could be read for reference bounds")
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
                "nodata": NDVI_NODATA,
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
                nodata=NDVI_NODATA,
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
                        src_nodata=NDVI_NODATA,
                        dst_nodata=NDVI_NODATA,
                        resampling=Resampling.nearest,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"NDVI overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"NDVI alignment failed: {exc}")
            return None

    def get_colorized_ndvi_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return an RGBA GeoTIFF with a fixed brown-to-green NDVI ramp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned = self.get_aligned_ndvi_path(reference_raster, target_resolution_m, date)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|ndvi-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

        lut = self._colormap_lut()
        span = max(NDVI_MAX_VALUE - NDVI_MIN_VALUE, 1e-6)

        with rasterio.open(aligned) as src:
            profile = src.profile.copy()
            profile.update(
                count=4,
                dtype="uint8",
                nodata=None,
                compress="deflate",
                tiled=True,
                blockxsize=256,
                blockysize=256,
            )

            with rasterio.open(colorized_path, "w", **profile) as dst:
                for _, window in src.block_windows():
                    classes = src.read(1, window=window)
                    valid = classes <= NDVI_VALID_MAX_DN

                    r = np.zeros_like(classes, dtype=np.uint8)
                    g = np.zeros_like(classes, dtype=np.uint8)
                    b = np.zeros_like(classes, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    if np.any(valid):
                        ndvi = classes.astype("float32") * NDVI_SCALE + NDVI_OFFSET
                        norm = np.clip((ndvi - NDVI_MIN_VALUE) / span, 0.0, 1.0)
                        idx = (norm * 255.0).astype(np.uint8)
                        rgb = lut[idx]
                        r[valid] = rgb[:, :, 0][valid]
                        g[valid] = rgb[:, :, 1][valid]
                        b[valid] = rgb[:, :, 2][valid]

                    dst.write(r, 1, window=window)
                    dst.write(g, 2, window=window)
                    dst.write(b, 3, window=window)
                    dst.write(a, 4, window=window)

        self._build_overviews(str(colorized_path))
        return str(colorized_path)

    def _s3_env(self):
        from vresto.api.config import CopernicusConfig
        from vresto.services.sentinel_stream import _gdal_s3_env

        config = CopernicusConfig()
        if config.has_static_s3_credentials():
            access_key, secret_key = config.get_s3_credentials()
        else:
            access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
            secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")
        if not access_key:
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; NDVI vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=NDVI_S3_ENDPOINT)

    def _vsis3_path(self, month: str, day: str) -> str:
        key = NDVI_S3_KEY_PATTERN.format(month=month, day=day)
        return f"/vsis3/{NDVI_S3_BUCKET}/{key}"

    def _read_global_window(
        self,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
        month: str,
        day: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the global NDVI-LTS COG via vsis3 and read the intersecting window."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        path = self._vsis3_path(month, day)
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
                    mid_lat = math.radians((lat_min + lat_max) / 2.0)
                    lon_span_m = (lon_max - lon_min) * 111_320.0 * max(math.cos(mid_lat), 0.1)
                    lat_span_m = (lat_max - lat_min) * 110_540.0
                    out_w = max(1, min(int(round(window.width)), int(round(lon_span_m / target_resolution_m))))
                    out_h = max(1, min(int(round(window.height)), int(round(lat_span_m / target_resolution_m))))

                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"NDVI-LTS {month}{day}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read NDVI-LTS {month}{day} via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, month: str, day: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"ndvi_lts_{month}{day}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")

    @staticmethod
    def _colormap_lut() -> np.ndarray:
        import matplotlib.colors as mcolors

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "ndvi_lts",
            ["#6e4e33", "#b58b55", "#d0b56d", "#8fb058", "#2e7d32"],
            N=256,
        )
        return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


ndvi_service = NDVIService()
