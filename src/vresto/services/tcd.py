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

import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

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


def _tile_code(lat_origin: int, lon_origin: int) -> str:
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"{ns}{abs(lat_origin):02d}{ew}{abs(lon_origin):03d}"


def _tiles_for_bounds(left: float, bottom: float, right: float, top: float) -> List[str]:
    """Return tile codes for all 3°×3° TCD tiles that intersect the bbox."""
    step = TCD_TILE_DEG
    lat0 = math.floor(bottom / step) * step
    lon0 = math.floor(left / step) * step
    codes = []
    lat = lat0
    while lat < top:
        lon = lon0
        while lon < right:
            codes.append(_tile_code(lat, lon))
            lon += step
        lat += step
    return codes


def tcd_has_coverage(bottom: float, top: float) -> bool:
    """Return whether a latitude span intersects TCD pantropical coverage."""
    return top > TCD_MIN_LAT and bottom < TCD_MAX_LAT


class TCDService:
    """Fetch and align TCD-10 data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "tcd")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def get_aligned_tcd_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        year: str = "2020",
    ) -> Optional[str]:
        """Return a cached aligned TCD GeoTIFF matching the reference raster grid."""
        year = str(year)
        if year not in TCD_AVAILABLE_YEARS:
            logger.warning(f"TCD year {year} not available; supported: {TCD_AVAILABLE_YEARS}")
            return None

        try:
            import rasterio
            import rasterio.transform as rtransform
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for TCD alignment: {exc}")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m, year)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached TCD overlay: {aligned_path}")
            return str(aligned_path)

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                if not tcd_has_coverage(bottom, top):
                    logger.info("TCD requested outside pantropical coverage")
                    return None

                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            tile_codes = _tiles_for_bounds(left, bottom, right, top)
            if not tile_codes:
                logger.warning("No TCD tiles found for reference bounds")
                return None

            tile_arrays: List[np.ndarray] = []
            tile_transforms = []
            for tile_code in tile_codes:
                arr, transform = self._read_tile_window(tile_code, year, left, bottom, right, top, target_resolution_m)
                if arr is not None:
                    tile_arrays.append(arr)
                    tile_transforms.append(transform)

            if not tile_arrays:
                logger.warning("No TCD tile data could be read")
                return None

            if len(tile_arrays) == 1:
                mosaic_data = tile_arrays[0]
                mosaic_transform = tile_transforms[0]
            else:
                mosaic_data, mosaic_transform = self._mosaic(tile_arrays, tile_transforms)

            h, w = mosaic_data.shape
            tmp_transform = rtransform.from_bounds(
                mosaic_transform.c,
                mosaic_transform.f + mosaic_transform.e * h,
                mosaic_transform.c + mosaic_transform.a * w,
                mosaic_transform.f,
                w,
                h,
            )
            tmp_profile = {
                "driver": "GTiff",
                "dtype": "uint8",
                "count": 1,
                "crs": "EPSG:4326",
                "transform": tmp_transform,
                "width": w,
                "height": h,
                "nodata": TCD_NODATA,
            }

            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
                tmp_path = tmpf.name
            with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
                tmp_dst.write(mosaic_data, 1)

            dst_profile = ref_profile.copy()
            dst_profile.update(
                driver="GTiff",
                dtype="uint8",
                count=1,
                compress="deflate",
                tiled=True,
                nodata=TCD_NODATA,
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
                        src_nodata=TCD_NODATA,
                        dst_nodata=TCD_NODATA,
                        resampling=Resampling.nearest,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"TCD overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"TCD alignment failed: {exc}")
            return None

    def get_colorized_tcd_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        year: str = "2020",
    ) -> Optional[str]:
        """Return an RGBA GeoTIFF with TCD palette colours and transparent nodata."""
        try:
            import rasterio
        except Exception:
            return None

        aligned = self.get_aligned_tcd_path(reference_raster, target_resolution_m, year)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|tcd-rgba-v1".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

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

                    r = np.zeros_like(classes, dtype=np.uint8)
                    g = np.zeros_like(classes, dtype=np.uint8)
                    b = np.zeros_like(classes, dtype=np.uint8)
                    a = np.zeros_like(classes, dtype=np.uint8)

                    for klass, (cr, cg, cb) in TCD_CLASSES.items():
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

    def _get_gdal_s3_env(self) -> dict:
        from vresto.api.config import CopernicusConfig

        config = CopernicusConfig()
        if config.has_static_s3_credentials():
            access_key, secret_key = config.get_s3_credentials()
        else:
            access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
            secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")
        if not access_key:
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; TCD vsis3 reads may fail")
        return {"access_key": access_key, "secret_key": secret_key}

    def _s3_env(self):
        from vresto.services.sentinel_stream import _gdal_s3_env

        creds = self._get_gdal_s3_env()
        return _gdal_s3_env(
            access_key=creds["access_key"],
            secret_key=creds["secret_key"],
            endpoint=TCD_S3_ENDPOINT,
        )

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        key = TCD_S3_KEY_PATTERN.format(year=year, tile=tile_code)
        return f"/vsis3/{TCD_S3_BUCKET}/{key}"

    def _read_tile_window(
        self,
        tile_code: str,
        year: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[object]]:
        """Open a TCD tile via vsis3 and read the intersecting window."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        path = self._vsis3_path(tile_code, year)
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

                    factor = self._optimal_factor(src, target_resolution_m)
                    window = rwin.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
                    h = max(1, round(window.height / factor))
                    w = max(1, round(window.width / factor))

                    data = src.read(1, window=window, out_shape=(h, w), resampling=Resampling.nearest)
                    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, w, h)
                    logger.info(f"TCD tile {tile_code}: read {w}×{h} px (overview factor ~{factor}, res ~{target_resolution_m}m)")
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read TCD tile {tile_code} via vsis3: {exc}")
            return None, None

    def _mosaic(self, arrays: List[np.ndarray], transforms: List) -> Tuple[np.ndarray, object]:
        """Merge multiple tile arrays using rasterio.merge."""
        from rasterio.io import MemoryFile
        from rasterio.merge import merge

        mem_files = []
        open_dsets = []
        try:
            for arr, transform in zip(arrays, transforms):
                h, w = arr.shape
                profile = {
                    "driver": "GTiff",
                    "dtype": "uint8",
                    "count": 1,
                    "crs": "EPSG:4326",
                    "transform": transform,
                    "width": w,
                    "height": h,
                    "nodata": TCD_NODATA,
                }
                mf = MemoryFile()
                with mf.open(**profile) as ds:
                    ds.write(arr, 1)
                mem_files.append(mf)
                open_dsets.append(mf.open())

            mosaic_data, mosaic_transform = merge(open_dsets, nodata=TCD_NODATA)
            return mosaic_data[0], mosaic_transform
        finally:
            for ds in open_dsets:
                ds.close()
            for mf in mem_files:
                mf.close()

    def _optimal_factor(self, src, target_resolution_m: int) -> int:
        """Choose the coarsest COG overview less than or equal to the ideal factor."""
        deg_per_pixel = abs(src.transform.a)
        native_m = deg_per_pixel * 111_320.0
        ideal = target_resolution_m / native_m
        overviews = src.overviews(1) or [1]
        candidates = [factor for factor in overviews if factor <= ideal]
        return int(max(candidates)) if candidates else 1

    def _aligned_key(self, reference_raster: str, resolution: int, year: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"tcd_{year}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


tcd_service = TCDService()
