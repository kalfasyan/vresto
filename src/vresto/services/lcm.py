"""LCM (Land Cover Map) service utilities.

Reads LCM-10 tiles from CDSE S3 (eodata) using GDAL /vsis3/ virtual filesystem.
Only the window covering the reference raster is fetched via HTTP range requests
against the COG tile — no full-file download, no OData API call needed.

Tile naming convention (3°×3° tiles):
    s3://eodata/CLMS/landcover_landuse/dynamic_land_cover/
        lcm_global_10m_yearly_v1/{year}/01/01/
        LCFM_LCM-10_V100_{year}_{tile}_cog/LCFM_LCM-10_V100_{year}_{tile}_MAP.tif

    where {tile} = N{lat:02d}E{lon:03d}  (SW corner in 3° steps)
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

LCM_S3_BUCKET = "eodata"
LCM_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
LCM_TILE_DEG = 3          # each tile is 3° × 3°
LCM_AVAILABLE_YEARS = {"2020"}

LCM_S3_KEY_PATTERN = (
    "CLMS/landcover_landuse/dynamic_land_cover/"
    "lcm_global_10m_yearly_v1/{year}/01/01/"
    "LCFM_LCM-10_V100_{year}_{tile}_cog/"
    "LCFM_LCM-10_V100_{year}_{tile}_MAP.tif"
)

# LCM-10 official colormap (class value -> (R, G, B)); 255 = no-data.
LCM_CLASSES: Dict[int, Tuple[int, int, int]] = {
    10:  (0,   100,  0),   # Tree cover
    20:  (255, 187, 34),   # Shrubland
    30:  (255, 255, 76),   # Grassland
    40:  (240, 150, 255),  # Cropland
    50:  (0,   150, 160),  # Herbaceous wetland
    60:  (0,   207, 117),  # Mangroves
    70:  (250, 230, 160),  # Moss and lichen
    80:  (180, 180, 180),  # Bare / sparse vegetation
    90:  (250,   0,   0),  # Built-up
    100: (0,   100, 200),  # Permanent water bodies
    110: (240, 240, 240),  # Snow and ice
    254: (10,   10,  10),  # Unclassifiable
}


def _tile_code(lat_origin: int, lon_origin: int) -> str:
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"{ns}{abs(lat_origin):02d}{ew}{abs(lon_origin):03d}"


def _tiles_for_bounds(
    left: float, bottom: float, right: float, top: float
) -> List[str]:
    """Return tile codes for all LCM_TILE_DEG×LCM_TILE_DEG tiles that intersect the bbox."""
    step = LCM_TILE_DEG
    lat0 = math.floor(bottom / step) * step
    lon0 = math.floor(left   / step) * step
    codes = []
    lat = lat0
    while lat < top:
        lon = lon0
        while lon < right:
            codes.append(_tile_code(lat, lon))
            lon += step
        lat += step
    return codes


class LCMService:
    """Fetch and align LCM-10 data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "lcm")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_aligned_lcm_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        year: str = "2020",
    ) -> Optional[str]:
        """Return a cached aligned LCM GeoTIFF matching the reference raster grid.

        Opens tiles directly via GDAL /vsis3/ using HTTP range requests — no
        full tile download.  Only the overlapping window is read, at the
        closest available COG overview level.
        """
        year = str(year)
        if year not in LCM_AVAILABLE_YEARS:
            logger.warning(f"LCM year {year} not available; supported: {LCM_AVAILABLE_YEARS}")
            return None

        try:
            import rasterio
            import rasterio.transform as rtransform
            import rasterio.windows as rwin
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for LCM alignment: {exc}")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m, year)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached LCM overlay: {aligned_path}")
            return str(aligned_path)

        self._configure_gdal_credentials()

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            tile_codes = _tiles_for_bounds(left, bottom, right, top)
            if not tile_codes:
                logger.warning("No LCM tiles found for reference bounds")
                return None

            logger.info(
                f"LCM tiles needed for {left:.2f},{bottom:.2f}..{right:.2f},{top:.2f}"
                f": {tile_codes}"
            )

            # Read each tile's window and mosaic if necessary.
            tile_arrays: List[np.ndarray] = []
            tile_transforms = []

            for tile_code in tile_codes:
                arr, transform = self._read_tile_window(
                    tile_code, year, left, bottom, right, top,
                    target_resolution_m, rwin, Resampling
                )
                if arr is not None:
                    tile_arrays.append(arr)
                    tile_transforms.append(transform)

            if not tile_arrays:
                logger.warning("No LCM tile data could be read")
                return None

            # Mosaic into one array (simple: stamp onto output grid).
            if len(tile_arrays) == 1:
                mosaic_data = tile_arrays[0]
                mosaic_transform = tile_transforms[0]
            else:
                mosaic_data, mosaic_transform = self._mosaic(
                    tile_arrays, tile_transforms, left, bottom, right, top, target_resolution_m
                )

            # Write intermediate geographic raster then reproject to reference grid.
            h, w = mosaic_data.shape
            tmp_transform = rtransform.from_bounds(
                mosaic_transform.c,
                mosaic_transform.f + mosaic_transform.e * h,
                mosaic_transform.c + mosaic_transform.a * w,
                mosaic_transform.f,
                w, h,
            )
            tmp_profile = {
                "driver": "GTiff",
                "dtype": "uint8",
                "count": 1,
                "crs": "EPSG:4326",
                "transform": tmp_transform,
                "width": w,
                "height": h,
                "nodata": 255,
            }

            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
                tmp_path = tmpf.name
            with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
                tmp_dst.write(mosaic_data, 1)

            dst_profile = ref_profile.copy()
            dst_profile.update(
                driver="GTiff", dtype="uint8", count=1,
                compress="deflate", tiled=True, nodata=255,
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
                        src_nodata=255,
                        dst_nodata=255,
                        resampling=Resampling.nearest,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"LCM overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"LCM alignment failed: {exc}")
            return None

    def get_colorized_lcm_path(
        self,
        reference_raster: str,
        target_resolution_m: int,
        year: str = "2020",
    ) -> Optional[str]:
        """Return an RGBA GeoTIFF with official LCM-10 class colours."""
        try:
            import rasterio
            from rasterio.enums import Resampling
        except Exception:
            return None

        aligned = self.get_aligned_lcm_path(reference_raster, target_resolution_m, year)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|lcm-rgba-v3".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

        with rasterio.open(aligned) as src:
            classes = src.read(1)
            profile = src.profile.copy()
            profile.update(count=4, dtype="uint8", nodata=None, compress="deflate", tiled=True)

        r = np.zeros_like(classes, dtype=np.uint8)
        g = np.zeros_like(classes, dtype=np.uint8)
        b = np.zeros_like(classes, dtype=np.uint8)
        a = np.zeros_like(classes, dtype=np.uint8)

        for klass, (cr, cg, cb) in LCM_CLASSES.items():
            mask = classes == klass
            if np.any(mask):
                r[mask] = cr
                g[mask] = cg
                b[mask] = cb
                a[mask] = 255

        with rasterio.open(colorized_path, "w", **profile) as dst:
            dst.write(r, 1)
            dst.write(g, 2)
            dst.write(b, 3)
            dst.write(a, 4)

        # Build overviews so rio-tiler can serve tiles efficiently.
        with rasterio.open(colorized_path, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
            dst.update_tags(ns="rio_overview", resampling="nearest")

        return str(colorized_path)

    def blend_overlay(
        self,
        rgb: np.ndarray,
        lcm_classes: np.ndarray,
        opacity: float,
    ) -> np.ndarray:
        """Blend an RGB array with LCM class colours."""
        if rgb.ndim != 3 or rgb.shape[-1] != 3:
            raise ValueError("rgb must have shape (H, W, 3)")
        alpha = float(max(0.0, min(1.0, opacity)))
        if alpha <= 0:
            return rgb
        overlay = np.zeros_like(rgb, dtype=np.uint8)
        for klass, color in LCM_CLASSES.items():
            mask = lcm_classes == klass
            if np.any(mask):
                overlay[mask] = color
        valid = lcm_classes != 255
        if not np.any(valid):
            return rgb
        out = rgb.astype(np.float32)
        over = overlay.astype(np.float32)
        out[valid] = (1.0 - alpha) * out[valid] + alpha * over[valid]
        return np.clip(out, 0, 255).astype(np.uint8)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_gdal_s3_env(self) -> dict:
        """Return credentials dict for use with _s3_env()."""
        from vresto.api.config import CopernicusConfig
        config = CopernicusConfig()
        if config.has_static_s3_credentials():
            access_key, secret_key = config.get_s3_credentials()
        else:
            access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
            secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")
        if not access_key:
            logger.warning("COPERNICUS_S3_ACCESS_KEY not set; LCM vsis3 reads may fail")
        return {"access_key": access_key, "secret_key": secret_key}

    def _s3_env(self):
        """Return a rasterio.Env context with CDSE S3 credentials for vsis3 reads."""
        import boto3
        import rasterio
        from rasterio.session import AWSSession
        creds = self._get_gdal_s3_env()
        boto_session = boto3.Session(
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
        )
        # endpoint_url must NOT include the scheme here; GDAL prepends https:// via AWS_HTTPS
        aws_sess = AWSSession(boto_session, endpoint_url=LCM_S3_ENDPOINT)
        return rasterio.Env(
            session=aws_sess,
            AWS_VIRTUAL_HOSTING="FALSE",
            AWS_HTTPS="YES",
        )

    def _configure_gdal_credentials(self) -> None:
        """Kept for compatibility."""
        pass

    def _vsis3_path(self, tile_code: str, year: str) -> str:
        key = LCM_S3_KEY_PATTERN.format(year=year, tile=tile_code)
        return f"/vsis3/{LCM_S3_BUCKET}/{key}"

    def _read_tile_window(
        self,
        tile_code: str,
        year: str,
        left: float, bottom: float, right: float, top: float,
        target_resolution_m: int,
        rwin,
        Resampling,
    ) -> Tuple[Optional[np.ndarray], Optional[object]]:
        """Open a tile via vsis3, read the intersecting window at target resolution."""
        import rasterio
        import rasterio.windows as rwmod
        path = self._vsis3_path(tile_code, year)
        try:
            with self._s3_env():
                with rasterio.open(path) as src:
                    b = src.bounds
                    lon_min = max(left,   b.left)
                    lat_min = max(bottom, b.bottom)
                    lon_max = min(right,  b.right)
                    lat_max = min(top,    b.top)
                    if lon_min >= lon_max or lat_min >= lat_max:
                        return None, None

                    factor = self._optimal_factor(src, target_resolution_m)
                    window = rwmod.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
                    h = max(1, round(window.height / factor))
                    w = max(1, round(window.width  / factor))

                    data = src.read(
                        1, window=window, out_shape=(h, w), resampling=Resampling.nearest
                    )
                    left_snap, bottom_snap, right_snap, top_snap = rwmod.bounds(window, src.transform)

                    import rasterio.transform as rtransform
                    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, w, h)
                    logger.info(
                        f"LCM tile {tile_code}: read {w}\u00d7{h} px "
                        f"(overview factor ~{factor}, res ~{target_resolution_m}m)"
                    )
                    return data, transform
        except Exception as exc:
            logger.warning(f"Could not read LCM tile {tile_code} via vsis3: {exc}")
            return None, None

    def _mosaic(
        self,
        arrays: List[np.ndarray],
        transforms: List,
        left: float, bottom: float, right: float, top: float,
        target_resolution_m: int,
    ) -> Tuple[np.ndarray, object]:
        """Merge multiple tile arrays using rasterio.merge (preserves exact transforms)."""
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
                    "nodata": 255,
                }
                mf = MemoryFile()
                with mf.open(**profile) as ds:
                    ds.write(arr, 1)
                mem_files.append(mf)
                open_dsets.append(mf.open())

            mosaic_data, mosaic_transform = merge(open_dsets, nodata=255)
            return mosaic_data[0], mosaic_transform
        finally:
            for ds in open_dsets:
                ds.close()
            for mf in mem_files:
                mf.close()

    def _optimal_factor(self, src, target_resolution_m: int) -> int:
        """Choose the coarsest COG overview ≤ the ideal downsample factor."""
        deg_per_pixel = abs(src.transform.a)
        native_m = deg_per_pixel * 111_320.0
        ideal = target_resolution_m / native_m
        overviews = src.overviews(1) or [1]
        candidates = [f for f in overviews if f <= ideal]
        factor = int(max(candidates)) if candidates else 1
        return factor

    def _aligned_key(self, reference_raster: str, resolution: int, year: str) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"lcm_{year}_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling
            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


lcm_service = LCMService()
