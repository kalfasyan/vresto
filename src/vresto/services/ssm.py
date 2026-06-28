"""Surface Soil Moisture (SSM) overlay utilities.

Uses the CLMS SSM 1 km daily collection (Europe only) and renders the
``ssm1km_ssm`` asset. Asset discovery is done through CDSE STAC so the overlay
snaps to the nearest available daily product to the streamed Sentinel
acquisition date.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
from loguru import logger

from vresto.api.stac_assets import find_closest_stac_asset

SSM_COLLECTION_ID = "clms_ssm_europe_1km_daily_v1_cog"
SSM_ASSET_KEY = "ssm1km_ssm"
SSM_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
SSM_RAW_NODATA = 255
SSM_NODATA = -9999.0
SSM_SCALE = 0.5
SSM_OFFSET = 0.0
# Raw DNs 0..200 carry valid degree-of-saturation percentages; 241..253 are
# flags (exceeding min/max, water/sensitivity/slope masks) and 255 is no-data.
SSM_VALID_MAX_DN = 200
SSM_MIN_VALUE = 0.0
SSM_MAX_VALUE = 100.0

# SSM is a European product; gate the overlay to the published grid extent.
SSM_MIN_LON = -11.0
SSM_MAX_LON = 50.0
SSM_MIN_LAT = 35.0
SSM_MAX_LAT = 72.0

SSM_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 217, "0%"),
    (1, 199, 233, 180, "20%"),
    (2, 127, 205, 187, "40%"),
    (3, 65, 182, 196, "60%"),
    (4, 34, 94, 168, "80%"),
    (5, 12, 44, 132, "100% sat."),
]


def ssm_has_coverage(left: float, bottom: float, right: float, top: float) -> bool:
    """Return whether a bbox intersects the European SSM coverage extent."""
    return right > SSM_MIN_LON and left < SSM_MAX_LON and top > SSM_MIN_LAT and bottom < SSM_MAX_LAT


@dataclass(frozen=True)
class SSMOverlayResult:
    """Colorized SSM overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


def raw_ssm_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw SSM DNs to degree-of-saturation percentage using the scale."""
    return raw.astype("float32") * SSM_SCALE + SSM_OFFSET


class SSMService:
    """Fetch and align daily SSM data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "ssm")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_ssm_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned SSM raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for SSM alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                if not ssm_has_coverage(left, bottom, right, top):
                    logger.info("SSM requested outside European coverage")
                    return None
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read SSM reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            SSM_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            SSM_ASSET_KEY,
            search_window_days=7,
            max_items=14,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached SSM overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No SSM data could be read for reference bounds")
            return None

        h, w = data.shape
        tmp_profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "crs": "EPSG:4326",
            "transform": src_transform,
            "width": w,
            "height": h,
            "nodata": SSM_NODATA,
        }

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
            tmp_path = tmpf.name
        with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
            tmp_dst.write(data, 1)

        dst_profile = ref_profile.copy()
        dst_profile.update(
            driver="GTiff",
            dtype="float32",
            count=1,
            compress="deflate",
            tiled=True,
            nodata=SSM_NODATA,
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
                    src_nodata=SSM_NODATA,
                    dst_nodata=SSM_NODATA,
                    resampling=Resampling.bilinear,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"SSM overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_ssm_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned SSM raster path."""
        result = self.get_aligned_ssm_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_ssm_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[SSMOverlayResult]:
        """Return a colorized SSM overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_ssm_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|ssm-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return SSMOverlayResult(str(colorized_path), selected_datetime)

        lut = self._colormap_lut()
        span = max(SSM_MAX_VALUE - SSM_MIN_VALUE, 1e-6)

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
                    value = src.read(1, window=window).astype("float32")
                    valid = value != SSM_NODATA

                    r = np.zeros_like(value, dtype=np.uint8)
                    g = np.zeros_like(value, dtype=np.uint8)
                    b = np.zeros_like(value, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    if np.any(valid):
                        norm = np.clip((value - SSM_MIN_VALUE) / span, 0.0, 1.0)
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
        return SSMOverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_ssm_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized SSM overlay path."""
        result = self.get_colorized_ssm_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result.colorized_path

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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; SSM vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=SSM_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected SSM COG via vsis3 and read the intersecting window."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        try:
            with self._s3_env():
                with rasterio.open(href) as src:
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

                    # Nearest keeps flag DNs intact so they can be masked before scaling.
                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    valid = data <= SSM_VALID_MAX_DN
                    physical = np.where(valid, raw_ssm_to_physical(data), SSM_NODATA).astype("float32")

                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"SSM {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return physical, transform
        except Exception as exc:
            logger.warning(f"Could not read SSM via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"ssm_{date_token}_{resolution}m_{ref_id}"

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
        import matplotlib

        cmap = matplotlib.colormaps["YlGnBu"]
        return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


ssm_service = SSMService()
