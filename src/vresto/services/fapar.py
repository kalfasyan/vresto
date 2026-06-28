"""FAPAR (Fraction of Absorbed Photosynthetically Active Radiation) overlay utilities.

Uses the CLMS FAPAR 300 m 10-daily collection and renders the ``fapar300_fapar``
asset. Asset discovery is done through CDSE STAC so the overlay snaps to the
nearest available 10-daily product to the streamed Sentinel acquisition date.
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

FAPAR_COLLECTION_ID = "clms_fapar_global_300m_10daily_v2_cog"
FAPAR_ASSET_KEY = "fapar300_fapar"
FAPAR_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
FAPAR_RAW_NODATA = 255
FAPAR_NODATA = 255
FAPAR_SCALE = 0.004
FAPAR_OFFSET = 0.0
FAPAR_MIN_VALUE = 0.0
FAPAR_MAX_VALUE = 0.94

# Official FAPAR 300 m legend (bin centre values as published in the product
# documentation). Each entry maps a display value to an RGB colour and label.
FAPAR_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (1, 255, 0, 0, "0.0"),
    (2, 255, 17, 0, "0.01"),
    (3, 255, 34, 0, "0.02"),
    (4, 255, 51, 0, "0.03"),
    (5, 255, 68, 0, "0.04"),
    (6, 255, 85, 0, "0.05"),
    (7, 255, 102, 0, "0.06"),
    (8, 255, 119, 0, "0.07"),
    (9, 255, 136, 0, "0.08"),
    (10, 255, 153, 0, "0.09"),
    (11, 255, 170, 0, "0.10"),
    (12, 255, 187, 0, "0.11"),
    (13, 255, 204, 0, "0.12"),
    (14, 255, 221, 0, "0.13"),
    (15, 255, 238, 0, "0.14"),
    (16, 247, 255, 0, "0.15"),
    (17, 230, 255, 0, "0.16"),
    (18, 213, 255, 0, "0.17"),
    (19, 196, 255, 0, "0.18"),
    (20, 179, 255, 0, "0.19"),
    (21, 162, 255, 0, "0.20"),
    (22, 145, 255, 0, "0.21"),
    (23, 128, 255, 0, "0.22"),
    (24, 111, 255, 0, "0.23"),
    (25, 94, 255, 0, "0.24"),
    (26, 77, 255, 0, "0.25"),
    (27, 60, 255, 0, "0.26"),
    (28, 43, 255, 0, "0.27"),
    (29, 26, 255, 0, "0.28"),
    (30, 9, 255, 0, "0.29"),
    (31, 0, 255, 8, "0.30"),
    (32, 0, 255, 25, "0.31"),
    (33, 0, 255, 42, "0.32"),
    (34, 0, 255, 59, "0.33"),
    (35, 0, 255, 76, "0.34"),
    (36, 0, 255, 93, "0.35"),
    (37, 0, 255, 110, "0.36"),
    (38, 0, 255, 127, "0.37"),
    (39, 0, 255, 144, "0.38"),
    (40, 0, 255, 161, "0.39"),
    (41, 0, 255, 178, "0.40"),
    (42, 0, 255, 195, "0.41"),
    (43, 0, 255, 212, "0.42"),
    (44, 0, 255, 229, "0.43"),
    (45, 0, 255, 246, "0.44"),
    (46, 0, 238, 255, "0.45"),
    (47, 0, 221, 255, "0.46"),
    (48, 0, 204, 255, "0.47"),
    (49, 0, 187, 255, "0.48"),
    (50, 0, 170, 255, "0.49"),
    (51, 0, 153, 255, "0.50"),
    (52, 0, 136, 255, "0.51"),
    (53, 0, 119, 255, "0.52"),
    (54, 0, 102, 255, "0.53"),
    (55, 0, 85, 255, "0.54"),
    (56, 0, 68, 255, "0.55"),
    (57, 0, 51, 255, "0.56"),
    (58, 0, 34, 255, "0.57"),
    (59, 0, 17, 255, "0.58"),
    (60, 0, 0, 255, "0.59"),
    (61, 17, 0, 255, "0.60"),
    (62, 34, 0, 255, "0.61"),
    (63, 51, 0, 255, "0.62"),
    (64, 68, 0, 255, "0.63"),
    (65, 85, 0, 255, "0.64"),
    (66, 102, 0, 255, "0.65"),
    (67, 119, 0, 255, "0.66"),
    (68, 136, 0, 255, "0.67"),
    (69, 153, 0, 255, "0.68"),
    (70, 170, 0, 255, "0.69"),
    (71, 187, 0, 255, "0.70"),
    (72, 204, 0, 255, "0.71"),
    (73, 221, 0, 255, "0.72"),
    (74, 238, 0, 255, "0.73"),
    (75, 255, 0, 255, "0.74"),
    (76, 255, 0, 238, "0.75"),
    (77, 255, 0, 221, "0.76"),
    (78, 255, 0, 204, "0.77"),
    (79, 255, 0, 187, "0.78"),
    (80, 255, 0, 170, "0.79"),
    (81, 255, 0, 153, "0.80"),
    (82, 255, 0, 136, "0.81"),
    (83, 255, 0, 119, "0.82"),
    (84, 255, 0, 102, "0.83"),
    (85, 255, 0, 85, "0.84"),
    (86, 255, 0, 68, "0.85"),
    (87, 255, 0, 51, "0.86"),
    (88, 255, 0, 34, "0.87"),
    (89, 255, 0, 17, "0.88"),
    (90, 255, 0, 0, "0.89"),
    (91, 221, 0, 0, "0.90"),
    (92, 187, 0, 0, "0.91"),
    (93, 153, 0, 0, "0.92"),
    (94, 119, 0, 0, "0.93"),
    (95, 85, 0, 0, "0.94"),
]

# LUT indexed by the raw pixel value (DN 0..250 scaled) for fast colorization.
# The official scale is 0.004, so 250 * 0.004 = 1.0. We therefore map each raw
# DN directly to the corresponding legend colour.
FAPAR_COLOR_BY_VALUE: dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in FAPAR_LEGEND}


@dataclass(frozen=True)
class FAPAROverlayResult:
    """Colorized FAPAR overlay plus the selected source timestamp."""

    colorized_path: str
    selected_datetime: datetime


def raw_fapar_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw FAPAR DNs to physical FAPAR values using the product scale."""
    return raw.astype("float32") * FAPAR_SCALE + FAPAR_OFFSET


class FAPARService:
    """Fetch and align 10-daily FAPAR data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "fapar")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_fapar_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned FAPAR raster plus the selected timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for FAPAR alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read FAPAR reference raster {reference_raster}: {exc}")
            return None

        asset = find_closest_stac_asset(
            FAPAR_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            FAPAR_ASSET_KEY,
            search_window_days=15,
            max_items=12,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_key = self._aligned_key(reference_raster, target_resolution_m, date_token)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached FAPAR overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning("No FAPAR data could be read for reference bounds")
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
            "nodata": FAPAR_NODATA,
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
            nodata=FAPAR_NODATA,
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
                    src_nodata=FAPAR_NODATA,
                    dst_nodata=FAPAR_NODATA,
                    resampling=Resampling.nearest,
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"FAPAR overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_fapar_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the aligned FAPAR raster path."""
        result = self.get_aligned_fapar_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_fapar_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[FAPAROverlayResult]:
        """Return a colorized FAPAR overlay plus the selected timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_fapar_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None

        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|fapar-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return FAPAROverlayResult(str(colorized_path), selected_datetime)

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
                    valid = dn != FAPAR_NODATA

                    r = np.zeros_like(dn, dtype=np.uint8)
                    g = np.zeros_like(dn, dtype=np.uint8)
                    b = np.zeros_like(dn, dtype=np.uint8)
                    a = np.where(valid, 255, 0).astype(np.uint8)

                    for value, (cr, cg, cb) in FAPAR_COLOR_BY_VALUE.items():
                        mask = dn == value
                        if np.any(mask):
                            r[mask] = cr
                            g[mask] = cg
                            b[mask] = cb

                    dst.write(r, 1, window=window)
                    dst.write(g, 2, window=window)
                    dst.write(b, 3, window=window)
                    dst.write(a, 4, window=window)

        self._build_overviews(str(colorized_path))
        return FAPAROverlayResult(str(colorized_path), selected_datetime)

    def get_colorized_fapar_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[str]:
        """Return only the colorized FAPAR overlay path."""
        result = self.get_colorized_fapar_result(reference_raster, target_resolution_m, date)
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
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; FAPAR vsis3 reads may fail")
        return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=FAPAR_S3_ENDPOINT)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected FAPAR COG via vsis3 and read the intersecting window."""
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
                    logger.info(f"FAPAR {href}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read FAPAR via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"fapar_{date_token}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


fapar_service = FAPARService()
