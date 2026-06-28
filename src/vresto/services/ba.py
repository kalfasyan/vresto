"""Burned Area (BA) overlay utilities.

Uses the CLMS Burned Area 300 m monthly collection and renders the
``ba300_dob_ntc`` (day-of-burn) asset. Burned pixels carry the day-of-year of
the detected burn (1..366); unburned pixels (0), flags (Too few observations,
Water) and no-data are rendered transparent. Asset discovery is done through
CDSE STAC so the overlay snaps to the nearest available monthly product to the
streamed Sentinel acquisition date.
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

BA_COLLECTION_ID = "clms_ba_global_300m_monthly_v4_cog"
BA_ASSET_KEY = "ba300_dob_ntc"
BA_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
BA_RAW_NODATA = -32768
BA_NODATA = -9999.0
# Day-of-burn is encoded directly as the day-of-year of detection.
BA_MIN_VALUE = 1.0
BA_MAX_VALUE = 366.0

# Day-of-year ranges mapped onto a YlOrRd ramp so the overlay reads as a
# burn-recency map within the year.
BA_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 178, "Jan-Feb"),
    (1, 254, 217, 118, "Mar-Apr"),
    (2, 254, 178, 76, "May-Jun"),
    (3, 253, 141, 60, "Jul-Aug"),
    (4, 240, 59, 32, "Sep-Oct"),
    (5, 189, 0, 38, "Nov-Dec"),
]


@dataclass(frozen=True)
class BAOverlayResult:
    """Colorized Burned Area overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


class BAService:
    """Fetch and align monthly Burned Area data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "ba")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_ba_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned Burned Area raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for Burned Area alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read Burned Area reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            BA_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            BA_ASSET_KEY,
            search_window_days=31,
            max_items=6,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached Burned Area overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No Burned Area data could be read for reference bounds")
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
            "nodata": BA_NODATA,
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
            nodata=BA_NODATA,
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
                    src_nodata=BA_NODATA,
                    dst_nodata=BA_NODATA,
                    resampling=Resampling.nearest,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"Burned Area overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_ba_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned Burned Area raster path."""
        result = self.get_aligned_ba_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_ba_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[BAOverlayResult]:
        """Return a colorized Burned Area overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_ba_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|ba-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return BAOverlayResult(str(colorized_path), selected_datetime)

        lut = self._colormap_lut()
        span = max(BA_MAX_VALUE - BA_MIN_VALUE, 1e-6)

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
                    valid = value != BA_NODATA

                    r = np.zeros_like(value, dtype=np.uint8)
                    g = np.zeros_like(value, dtype=np.uint8)
                    b = np.zeros_like(value, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    if np.any(valid):
                        norm = np.clip((value - BA_MIN_VALUE) / span, 0.0, 1.0)
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
        return BAOverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_ba_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized Burned Area overlay path."""
        result = self.get_colorized_ba_result(reference_raster, target_resolution_m, date)
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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; Burned Area vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=BA_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected Burned Area COG via vsis3 and read the intersecting window."""
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

                    # Nearest preserves the day-of-burn codes; only true burns
                    # (1..366) are kept, everything else becomes transparent.
                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    burned = (data >= int(BA_MIN_VALUE)) & (data <= int(BA_MAX_VALUE))
                    physical = np.where(burned, data.astype("float32"), BA_NODATA).astype("float32")

                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"BA {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return physical, transform
        except Exception as exc:
            logger.warning(f"Could not read Burned Area via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"ba_{date_token}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")

    @staticmethod
    def _colormap_lut() -> np.ndarray:
        import matplotlib

        cmap = matplotlib.colormaps["YlOrRd"]
        return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


ba_service = BAService()
