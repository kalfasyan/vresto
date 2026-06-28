"""Soil Water Index (SWI) overlay utilities.

Uses the CLMS SWI 12.5 km daily global collection and renders the
``swi_swi010`` asset (Soil Water Index with characteristic time length T = 10,
a common root-zone moisture proxy). Asset discovery is done through CDSE STAC
so the overlay snaps to the nearest available daily product to the streamed
Sentinel acquisition date.
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

SWI_COLLECTION_ID = "clms_swi_global_12.5km_daily_v4_cog"
SWI_ASSET_KEY = "swi_swi010"
SWI_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
SWI_RAW_NODATA = 255
SWI_NODATA = -9999.0
SWI_SCALE = 0.5
SWI_OFFSET = 0.0
# Raw DNs 0..200 carry valid SWI percentages; 255 is no-data.
SWI_VALID_MAX_DN = 200
SWI_MIN_VALUE = 0.0
SWI_MAX_VALUE = 100.0

SWI_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 217, "0%"),
    (1, 199, 233, 180, "20%"),
    (2, 127, 205, 187, "40%"),
    (3, 65, 182, 196, "60%"),
    (4, 34, 94, 168, "80%"),
    (5, 12, 44, 132, "100%"),
]


@dataclass(frozen=True)
class SWIOverlayResult:
    """Colorized SWI overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


def raw_swi_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw SWI DNs to percentage using the product scale."""
    return raw.astype("float32") * SWI_SCALE + SWI_OFFSET


class SWIService:
    """Fetch and align daily SWI data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "swi")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_swi_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned SWI raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for SWI alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read SWI reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            SWI_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            SWI_ASSET_KEY,
            search_window_days=5,
            max_items=10,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached SWI overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No SWI data could be read for reference bounds")
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
            "nodata": SWI_NODATA,
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
            nodata=SWI_NODATA,
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
                    src_nodata=SWI_NODATA,
                    dst_nodata=SWI_NODATA,
                    resampling=Resampling.bilinear,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"SWI overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_swi_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned SWI raster path."""
        result = self.get_aligned_swi_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_swi_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[SWIOverlayResult]:
        """Return a colorized SWI overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_swi_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|swi-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return SWIOverlayResult(str(colorized_path), selected_datetime)

        lut = self._colormap_lut()
        span = max(SWI_MAX_VALUE - SWI_MIN_VALUE, 1e-6)

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
                    valid = value != SWI_NODATA

                    r = np.zeros_like(value, dtype=np.uint8)
                    g = np.zeros_like(value, dtype=np.uint8)
                    b = np.zeros_like(value, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    if np.any(valid):
                        norm = np.clip((value - SWI_MIN_VALUE) / span, 0.0, 1.0)
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
        return SWIOverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_swi_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized SWI overlay path."""
        result = self.get_colorized_swi_result(reference_raster, target_resolution_m, date)
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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; SWI vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=SWI_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected SWI COG via vsis3 and read the intersecting window."""
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

                    # Nearest keeps the no-data DN intact so it can be masked before scaling.
                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    valid = data <= SWI_VALID_MAX_DN
                    physical = np.where(valid, raw_swi_to_physical(data), SWI_NODATA).astype("float32")

                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"SWI {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return physical, transform
        except Exception as exc:
            logger.warning(f"Could not read SWI via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"swi_{date_token}_{resolution}m_{ref_id}"

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


swi_service = SWIService()
