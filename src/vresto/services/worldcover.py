"""WorldCover service utilities.

This module fetches ESA WorldCover tiles from the public S3 bucket using GDAL/vsis3
for efficient streaming (no full file downloads), and aligns them to Sentinel raster
grids for overlay visualization.
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

WORLDCOVER_BUCKET = "esa-worldcover"
WORLDCOVER_PREFIX_BY_YEAR = {
    "2021": "v200/2021/map/",
    "2020": "v100/2020/map/",
}

# Tile naming pattern: ESA_WorldCover_10m_{year}_v{version}_{tile}_Map.tif
# e.g., ESA_WorldCover_10m_2021_v200_N39E023_Map.tif

WORLDCOVER_TILE_DEG = 3  # 3°×3° COG tiles

WORLDCOVER_CLASSES: Dict[int, Tuple[int, int, int]] = {
    0: (0, 0, 0),
    10: (0, 100, 0),
    20: (255, 187, 34),
    30: (255, 255, 76),
    40: (240, 150, 255),
    50: (250, 0, 0),
    60: (180, 180, 180),
    70: (240, 240, 240),
    80: (0, 100, 200),
    90: (0, 150, 160),
    95: (0, 207, 117),
    100: (250, 230, 160),
}


def _tile_code(lat_origin: int, lon_origin: int) -> str:
    """Generate tile code like N39E021 for 1°×1° tiles."""
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"{ns}{abs(lat_origin):02d}{ew}{abs(lon_origin):03d}"


def _tiles_for_bounds(left: float, bottom: float, right: float, top: float) -> List[str]:
    """Return tile codes for all 1°×1° tiles that intersect the bbox."""
    step = WORLDCOVER_TILE_DEG
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


class WorldCoverService:
    """Fetch and align WorldCover data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "worldcover")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        """Build vsis3 path for a WorldCover tile.

        Key format: v{version}/{year}/map/ESA_WorldCover_10m_{year}_v{version}_{tile}_Map.tif
        e.g., /vsis3/esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_N39E023_Map.tif
        """
        prefix = WORLDCOVER_PREFIX_BY_YEAR[year]
        version = "v200" if year == "2021" else "v100"
        filename = f"ESA_WorldCover_10m_{year}_{version}_{tile_code}_Map.tif"
        return f"/vsis3/{WORLDCOVER_BUCKET}/{prefix}{filename}"

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
        """Open a tile via vsis3, read the intersecting window at target resolution."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        path = self._vsis3_path(tile_code, year)
        try:
            # Set environment for public S3 access
            import os

            os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

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
                logger.info(f"WorldCover tile {tile_code}: read {w}x{h} px (overview factor ~{factor}, res ~{target_resolution_m}m)")
                return data, transform
        except Exception as exc:
            logger.warning(f"Could not read WorldCover tile {tile_code} via vsis3: {exc}")
            return None, None

    def _optimal_factor(self, src, target_resolution_m: int) -> int:
        """Choose the coarsest COG overview ≤ the ideal downsample factor."""
        deg_per_pixel = abs(src.transform.a)
        native_m = deg_per_pixel * 111_320.0
        ideal = target_resolution_m / native_m
        overviews = src.overviews(1) or [1]
        candidates = [f for f in overviews if f <= ideal]
        factor = int(max(candidates)) if candidates else 1
        return factor

    def _mosaic(
        self,
        arrays: List[np.ndarray],
        transforms: List,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[np.ndarray, object]:
        """Merge multiple tile arrays using rasterio.merge."""
        from rasterio.io import MemoryFile
        from rasterio.merge import merge

        mem_files = []
        open_dsets = []
        try:
            for arr, t in zip(arrays, transforms):
                h, w = arr.shape
                profile = {
                    "driver": "GTiff",
                    "dtype": "uint8",
                    "count": 1,
                    "crs": "EPSG:4326",
                    "transform": t,
                    "width": w,
                    "height": h,
                    "nodata": 0,
                }
                mf = MemoryFile()
                with mf.open(**profile) as ds:
                    ds.write(arr, 1)
                mem_files.append(mf)
                open_dsets.append(mf.open())

            mosaic_data, mosaic_transform = merge(open_dsets, nodata=0)
            return mosaic_data[0], mosaic_transform
        finally:
            for ds in open_dsets:
                ds.close()
            for mf in mem_files:
                mf.close()

    def get_aligned_worldcover_path(self, reference_raster: str, target_resolution_m: int, year: str = "2021") -> Optional[str]:
        """Create or reuse an aligned WorldCover raster matching a reference grid.

        Uses GDAL/vsis3 to stream only the needed window from COG tiles - no full
        file downloads required.
        """
        year = str(year)
        if year not in WORLDCOVER_PREFIX_BY_YEAR:
            logger.warning(f"Unsupported WorldCover year: {year}")
            return None

        try:
            import rasterio
            import rasterio.transform as rtransform
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception:
            logger.exception("rasterio is required for WorldCover alignment")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m, year)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached WorldCover overlay: {aligned_path}")
            return str(aligned_path)

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            tile_codes = _tiles_for_bounds(left, bottom, right, top)
            if not tile_codes:
                logger.warning("No WorldCover tiles found for reference bounds")
                return None

            logger.info(f"WorldCover tiles needed for {left:.2f},{bottom:.2f}..{right:.2f},{top:.2f}: {tile_codes}")

            # Read each tile's window
            tile_arrays: List[np.ndarray] = []
            tile_transforms = []

            for tile_code in tile_codes:
                arr, transform = self._read_tile_window(tile_code, year, left, bottom, right, top, target_resolution_m)
                if arr is not None:
                    tile_arrays.append(arr)
                    tile_transforms.append(transform)

            if not tile_arrays:
                logger.warning("No WorldCover tile data could be read")
                return None

            # Mosaic into one array
            if len(tile_arrays) == 1:
                mosaic_data = tile_arrays[0]
                mosaic_transform = tile_transforms[0]
            else:
                mosaic_data, mosaic_transform = self._mosaic(tile_arrays, tile_transforms, left, bottom, right, top, target_resolution_m)

            # Write intermediate geographic raster then reproject to reference grid
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
                "nodata": 0,
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
                nodata=0,
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
                        src_nodata=0,
                        dst_nodata=0,
                        resampling=Resampling.nearest,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"WorldCover overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"WorldCover alignment failed: {exc}")
            return None

    def get_colorized_worldcover_path(self, reference_raster: str, target_resolution_m: int, year: str = "2021") -> Optional[str]:
        """Return an RGBA GeoTIFF with exact WorldCover class colors and transparent nodata."""
        try:
            import rasterio
        except Exception:
            logger.exception("rasterio is required for WorldCover colorization")
            return None

        aligned = self.get_aligned_worldcover_path(reference_raster, target_resolution_m, year)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|wc-rgba-v2".encode("utf-8")).hexdigest()
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

                        for klass, (cr, cg, cb) in WORLDCOVER_CLASSES.items():
                            mask = classes == klass
                            if np.any(mask):
                                r[mask] = cr
                                g[mask] = cg
                                b[mask] = cb
                                if klass != 0:
                                    a[mask] = 255

                        dst.write(r, 1, window=window)
                        dst.write(g, 2, window=window)
                        dst.write(b, 3, window=window)
                        dst.write(a, 4, window=window)

            self._build_overviews(str(colorized_path))
            return str(colorized_path)
        except Exception:
            logger.exception("Failed to build colorized WorldCover overlay")
            return None

    def blend_overlay(self, rgb: np.ndarray, worldcover_classes: np.ndarray, opacity: float) -> np.ndarray:
        """Blend RGB array with WorldCover classes using categorical palette."""
        if rgb.ndim != 3 or rgb.shape[-1] != 3:
            raise ValueError("rgb must have shape (H, W, 3)")

        alpha = float(max(0.0, min(1.0, opacity)))
        if alpha <= 0:
            return rgb

        overlay = np.zeros_like(rgb, dtype=np.uint8)
        for klass, color in WORLDCOVER_CLASSES.items():
            mask = worldcover_classes == klass
            if np.any(mask):
                overlay[mask] = color

        valid_mask = worldcover_classes > 0
        if not np.any(valid_mask):
            return rgb

        out = rgb.astype(np.float32)
        over = overlay.astype(np.float32)
        out[valid_mask] = ((1.0 - alpha) * out[valid_mask]) + (alpha * over[valid_mask])
        return np.clip(out, 0, 255).astype(np.uint8)

    def _aligned_key(self, reference_raster: str, target_resolution_m: int, year: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"wc_{year}_{target_resolution_m}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        """Build lightweight overviews for smoother tile serving."""
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as ds:
                factors = [2, 4, 8, 16, 32]
                ds.build_overviews(factors, Resampling.nearest)
                ds.update_tags(ns="rio_overview", resampling="nearest")
        except Exception:
            logger.debug(f"Could not build overviews for {path}", exc_info=True)


worldcover_service = WorldCoverService()
