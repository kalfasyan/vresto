"""Water Bodies (WB) overlay utilities.

Uses the CLMS Water Bodies 100 m monthly collection and renders the
``wb100_wb`` asset. The product is categorical: pixels are flagged as Sea (0),
Water (70), No-data (251) or No-water (255). Only Sea and Water are coloured;
everything else is rendered transparent. Asset discovery is done through CDSE
STAC so the overlay snaps to the nearest available monthly product to the
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

WB_COLLECTION_ID = "clms_wb_global_100m_monthly_v1_cog"
WB_ASSET_KEY = "wb100_wb"
WB_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
WB_RAW_NODATA = 251

# Categorical classes that get a colour. Sea (0) and Water (70) are the only
# values rendered; No-data (251) and No-water (255) stay transparent.
WB_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (70, 31, 120, 180, "Water"),
    (0, 8, 48, 107, "Sea"),
]

WB_CLASSES: dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in WB_CLASS_LEGENDS}


@dataclass(frozen=True)
class WBOverlayResult:
    """Colorized Water Bodies overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


class WBService:
    """Fetch and align monthly Water Bodies data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "wb")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_wb_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned Water Bodies raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for Water Bodies alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read Water Bodies reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            WB_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            WB_ASSET_KEY,
            search_window_days=31,
            max_items=6,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached Water Bodies overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No Water Bodies data could be read for reference bounds")
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
            "nodata": WB_RAW_NODATA,
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
            nodata=WB_RAW_NODATA,
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
                    src_nodata=WB_RAW_NODATA,
                    dst_nodata=WB_RAW_NODATA,
                    resampling=Resampling.nearest,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"Water Bodies overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_wb_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned Water Bodies raster path."""
        result = self.get_aligned_wb_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_wb_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[WBOverlayResult]:
        """Return a colorized Water Bodies overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_wb_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|wb-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return WBOverlayResult(str(colorized_path), selected_datetime)

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
                    dn = src.read(1, window=window)

                    r = np.zeros_like(dn, dtype=np.uint8)
                    g = np.zeros_like(dn, dtype=np.uint8)
                    b = np.zeros_like(dn, dtype=np.uint8)
                    a = np.zeros_like(dn, dtype=np.uint8)

                    for value, (cr, cg, cb) in WB_CLASSES.items():
                        mask = dn == value
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
        return WBOverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_wb_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized Water Bodies overlay path."""
        result = self.get_colorized_wb_result(reference_raster, target_resolution_m, date)
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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; Water Bodies vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=WB_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected Water Bodies COG via vsis3 and read the intersecting window."""
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

                    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.nearest)
                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                    logger.info(f"WB {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read Water Bodies via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"wb_{date_token}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


wb_service = WBService()
