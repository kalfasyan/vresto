"""Hourly Land Surface Temperature overlay utilities.

Uses the CLMS hourly LST collection and renders the ``lst_lst`` asset, which
carries the actual land-surface temperature values. Asset discovery is done
through CDSE STAC so the overlay can snap to the nearest available hourly
product to the streamed Sentinel acquisition timestamp.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
from loguru import logger

from vresto.api.stac_assets import find_closest_stac_asset, find_stac_assets

LST_COLLECTION_ID = "clms_lst_global_3km_hourly_v3_cog"
LST_ASSET_KEY = "lst_lst"
LST_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
LST_RAW_NODATA = -9999.0
LST_CELSIUS_NODATA = -9999.0
LST_SCALE = 0.01
LST_C_MIN = -20.0
LST_C_MAX = 50.0
LST_DISPLAY_TIMEZONE = ZoneInfo("Europe/Brussels")

LST_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 49, 54, 149, "<-10 C"),
    (1, 69, 117, 180, "-10 to 0 C"),
    (2, 116, 173, 209, "0 to 10 C"),
    (3, 254, 224, 144, "10 to 20 C"),
    (4, 253, 174, 97, "20 to 30 C"),
    (5, 215, 48, 39, ">30 C"),
]


@dataclass(frozen=True)
class LSTOverlayResult:
    """Colorized hourly LST overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


def raw_lst_to_celsius(raw: np.ndarray) -> np.ndarray:
    """Convert raw LST median pixel values to Celsius using the product scale."""
    return raw.astype("float32") * LST_SCALE


def format_lst_selected_datetime(selected_datetime: datetime) -> str:
    """Format the selected hourly LST timestamp for UI display."""
    if selected_datetime.tzinfo is None:
        selected_datetime = selected_datetime.replace(tzinfo=timezone.utc)
    return selected_datetime.astimezone(LST_DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


class LSTService:
    """Fetch and align hourly LST data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "lst")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_lst_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned hourly LST raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for LST alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read LST reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            LST_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            LST_ASSET_KEY,
            search_window_days=1,
            max_items=48,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached LST overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No LST data could be read for reference bounds")
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
            "nodata": LST_CELSIUS_NODATA,
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
            nodata=LST_CELSIUS_NODATA,
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
                    src_nodata=LST_CELSIUS_NODATA,
                    dst_nodata=LST_CELSIUS_NODATA,
                    resampling=Resampling.bilinear,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"LST overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_lst_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned hourly LST raster path."""
        result = self.get_aligned_lst_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def list_available_lst_datetimes(self, reference_raster: str, date: str) -> list[datetime]:
        """List hourly LST scene timestamps near the requested acquisition time."""
        try:
            import rasterio
            from rasterio.warp import transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for LST timestamp listing: {exc}")
            return []

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
        except Exception as exc:
            logger.error(f"Could not read LST reference raster {reference_raster}: {exc}")
            return []

        matches = find_stac_assets(
            LST_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            LST_ASSET_KEY,
            search_window=timedelta(hours=12),
            max_items=24,
        )
        return [match.item_datetime for match in matches]

    def get_colorized_lst_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[LSTOverlayResult]:
        """Return a colorized hourly LST overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_lst_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|lst-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return LSTOverlayResult(str(colorized_path), selected_datetime)

        lut = self._colormap_lut()
        span = max(LST_C_MAX - LST_C_MIN, 1e-6)

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
                    temp_c = src.read(1, window=window).astype("float32")
                    valid = temp_c != LST_CELSIUS_NODATA

                    r = np.zeros_like(temp_c, dtype=np.uint8)
                    g = np.zeros_like(temp_c, dtype=np.uint8)
                    b = np.zeros_like(temp_c, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    if np.any(valid):
                        norm = np.clip((temp_c - LST_C_MIN) / span, 0.0, 1.0)
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
        return LSTOverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_lst_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized hourly LST overlay path."""
        result = self.get_colorized_lst_result(reference_raster, target_resolution_m, date)
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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; LST vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=LST_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected LST COG via vsis3 and read the intersecting window."""
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

                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
                    valid = data != np.float32(src.nodata if src.nodata is not None else LST_RAW_NODATA)
                    data = np.where(valid, raw_lst_to_celsius(data), LST_CELSIUS_NODATA)

                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"LST {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read LST via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"lst_hourly_{date_token}_{resolution}m_{ref_id}"

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

        cmap = matplotlib.colormaps["RdYlBu_r"]
        return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


lst_service = LSTService()
