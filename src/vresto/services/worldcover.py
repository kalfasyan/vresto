"""WorldCover service utilities.

This module fetches ESA WorldCover tiles from the public S3 bucket, caches them locally,
and aligns them to Sentinel raster grids for overlay visualization.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
import numpy as np
from botocore import UNSIGNED
from botocore.config import Config
from loguru import logger

WORLDCOVER_BUCKET = "esa-worldcover"
WORLDCOVER_PREFIX_BY_YEAR = {
    "2021": "v200/2021/map/",
    "2020": "v100/2020/map/",
}

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

_TILE_CODE_RE = re.compile(r"([NS])(\d{2})([EW])(\d{3})", re.IGNORECASE)


class WorldCoverService:
    """Fetch and align WorldCover data for Sentinel overlays."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "worldcover")
        self.raw_cache = self.cache_root / "raw"
        self.aligned_cache = self.cache_root / "aligned"
        self.raw_cache.mkdir(parents=True, exist_ok=True)
        self.aligned_cache.mkdir(parents=True, exist_ok=True)
        self._s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        self._keys_by_year: Dict[str, List[str]] = {}

    def get_aligned_worldcover_path(self, reference_raster: str, target_resolution_m: int, year: str = "2021") -> Optional[str]:
        """Create or reuse an aligned WorldCover raster matching a reference grid."""
        year = str(year)
        if year not in WORLDCOVER_PREFIX_BY_YEAR:
            logger.warning(f"Unsupported WorldCover year: {year}")
            return None

        try:
            import rasterio
            import rasterio.windows as rwin
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
            from rasterio.merge import merge
        except Exception:
            logger.exception("rasterio is required for WorldCover alignment")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m, year)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            return str(aligned_path)

        with rasterio.open(reference_raster) as ref:
            ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
            left, bottom, right, top = ref_bounds_4326
            
            candidates = self._get_tile_candidates(left, bottom, right, top, year)
            if not candidates:
                logger.warning("No WorldCover tiles intersect reference bounds")
                return None

            # Setup destination profile based on reference
            profile = ref.profile.copy()
            profile.update({
                "driver": "GTiff",
                "dtype": "uint8",
                "count": 1,
                "compress": "deflate",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
                "nodata": 0,
            })

            # Create aligned output by accumulating tiles into a temp mosaic if needed
            with rasterio.open(aligned_path, "w", **profile) as dst:
                temp_srcs = []
                try:
                    for candidate_key in candidates:
                        tile_path = self._ensure_tile_cached(candidate_key, year)
                        if not tile_path:
                            continue
                        temp_srcs.append(rasterio.open(tile_path))

                    mosaic, mosaic_transform = merge(temp_srcs)
                    # Create a temporary in-memory dataset from the mosaic transform/data
                    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
                        tmp_mosaic_path = tmpf.name
                    
                    with rasterio.open(tmp_mosaic_path, "w", driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], count=1, dtype=mosaic.dtype, transform=mosaic_transform, crs="EPSG:4326") as tmp:
                        tmp.write(mosaic[0], 1)

                    with rasterio.open(tmp_mosaic_path) as src:
                        reproject(
                            source=rasterio.band(src, 1),
                            destination=rasterio.band(dst, 1),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=dst.transform,
                            dst_crs=dst.crs,
                            src_nodata=0,
                            dst_nodata=0,
                            resampling=Resampling.nearest,
                        )
                    os.remove(tmp_mosaic_path)
                finally:
                    for s in temp_srcs:
                        s.close()
            
            self._build_overviews(str(aligned_path))
            return str(aligned_path)

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

        token = hashlib.sha1(f"{aligned}|wc-rgba-v1".encode("utf-8")).hexdigest()
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

    def _list_keys_for_year(self, year: str) -> List[str]:
        if year in self._keys_by_year:
            return self._keys_by_year[year]

        prefix = WORLDCOVER_PREFIX_BY_YEAR[year]
        keys: List[str] = []
        token = None
        while True:
            kwargs = {"Bucket": WORLDCOVER_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kwargs)
            for item in resp.get("Contents", []):
                key = item.get("Key", "")
                if key.lower().endswith((".tif", ".tiff")):
                    keys.append(key)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")

        self._keys_by_year[year] = keys
        return keys

    def _get_tile_candidates(self, left: float, bottom: float, right: float, top: float, year: str) -> List[str]:
        candidates: List[str] = []
        for key in self._list_keys_for_year(year):
            tile_bounds = self._tile_bounds_from_key(key)
            if not tile_bounds:
                continue
            t_left, t_bottom, t_right, t_top = tile_bounds
            intersects = not (t_right <= left or t_left >= right or t_top <= bottom or t_bottom >= top)
            if intersects:
                candidates.append(key)
        return candidates

    def _tile_bounds_from_key(self, key: str) -> Optional[Tuple[float, float, float, float]]:
        m = _TILE_CODE_RE.search(key)
        if not m:
            return None
        lat_sign = 1 if m.group(1).upper() == "N" else -1
        lon_sign = 1 if m.group(3).upper() == "E" else -1
        lat = lat_sign * int(m.group(2))
        lon = lon_sign * int(m.group(4))
        # WorldCover v100/v200 are 3x3 degree tiles.
        return (lon, lat, lon + 3.0, lat + 3.0)

    def _ensure_tile_cached(self, key: str, year: str) -> Optional[str]:
        dest = self.raw_cache / year / Path(key).name
        if dest.exists():
            return str(dest)

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._s3.download_file(WORLDCOVER_BUCKET, key, str(dest))
            return str(dest)
        except Exception:
            logger.exception(f"Failed downloading WorldCover tile: {key}")
            return None

    def _aligned_key(self, reference_raster: str, target_resolution_m: int, year: str) -> str:
        token = f"{Path(reference_raster).resolve()}|{target_resolution_m}|{year}"
        return hashlib.sha1(token.encode("utf-8")).hexdigest()

    def _build_temp_mosaic(self, tile_paths: List[str]) -> Optional[str]:
        try:
            import tempfile

            import rasterio
            from rasterio.merge import merge

            srcs = [rasterio.open(p) for p in tile_paths]
            mosaic, transform = merge(srcs)
            profile = srcs[0].profile.copy()
            profile.update({
                "height": mosaic.shape[1],
                "width": mosaic.shape[2],
                "transform": transform,
                "count": 1,
                "dtype": "uint8",
                "compress": "deflate",
            })

            fd, out_path = tempfile.mkstemp(suffix="_worldcover_mosaic.tif")
            os.close(fd)
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(mosaic[0].astype(np.uint8), 1)

            for s in srcs:
                s.close()

            return out_path
        except Exception:
            logger.exception("Failed to build temporary WorldCover mosaic")
            return None

    def _build_overviews(self, path: str) -> None:
        """Build lightweight overviews for smoother tile serving."""
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as ds:
                factors = [2, 4, 8, 16]
                ds.build_overviews(factors, Resampling.nearest)
                ds.update_tags(ns="rio_overview", resampling="nearest")
        except Exception:
            logger.debug(f"Could not build overviews for {path}", exc_info=True)


worldcover_service = WorldCoverService()
