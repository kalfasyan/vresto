"""Copernicus DEM (GLO-30) service utilities.

Streams Copernicus DEM GLO-30 tiles from the public AWS Open Data bucket
``copernicus-dem-30m`` using GDAL ``/vsis3/`` (no full-file downloads, no
credentials), and aligns them to Sentinel raster grids for terrain overlays.

Unlike the categorical land-cover overlays (LCM, WorldCover), the DEM is a
*continuous* surface, so it is rendered with a hypsometric colour ramp combined
with hillshade relief instead of a discrete class palette.

Tile naming convention (1°×1° tiles, SW corner):
    s3://copernicus-dem-30m/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/
        Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
from loguru import logger

DEM_BUCKET = "copernicus-dem-30m"
DEM_REGION = "eu-central-1"
DEM_TILE_DEG = 1  # Copernicus DEM tiles are 1°×1°
DEM_NODATA = -9999.0

# Relative hypsometric legend (matplotlib "terrain" sub-range [0.25, 1.0]).
# The overlay applies a per-scene 2–98% stretch, so labels are relative.
# Exported as list of tuples for the shared legend renderer: (id, R, G, B, label)
DEM_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 1, 204, 102, "Lower"),
    (1, 193, 243, 141, "Low–mid"),
    (2, 190, 172, 118, "Mid"),
    (3, 159, 132, 126, "Mid–high"),
    (4, 255, 255, 255, "Higher"),
]


def _tile_dir(lat_origin: int, lon_origin: int) -> str:
    """Build the tile directory/file stem like ``Copernicus_DSM_COG_10_N50_00_E004_00_DEM``."""
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_origin):02d}_00_{ew}{abs(lon_origin):03d}_00_DEM"


def _tiles_for_bounds(left: float, bottom: float, right: float, top: float) -> List[Tuple[int, int]]:
    """Return (lat_origin, lon_origin) SW corners for all 1°×1° tiles intersecting the bbox."""
    step = DEM_TILE_DEG
    lat0 = math.floor(bottom / step) * step
    lon0 = math.floor(left / step) * step
    tiles: List[Tuple[int, int]] = []
    lat = lat0
    while lat < top:
        lon = lon0
        while lon < right:
            tiles.append((lat, lon))
            lon += step
        lat += step
    return tiles


@contextlib.contextmanager
def _public_s3_env():
    """Configure GDAL for anonymous access to the public DEM bucket.

    Sets ``AWS_NO_SIGN_REQUEST`` and clears any CDSE/eodata endpoint or static
    credentials that a credentialed overlay (e.g. LCM) may have left in the
    environment, restoring the previous values on exit. Reuses the shared GDAL
    tuning defaults and env lock from the sentinel_stream service.
    """
    from vresto.services.sentinel_stream import _GDAL_TUNING_DEFAULTS, _gdal_env_lock

    new_vals = {
        "AWS_NO_SIGN_REQUEST": "YES",
        "AWS_REGION": DEM_REGION,
        "AWS_DEFAULT_REGION": DEM_REGION,
        "AWS_VIRTUAL_HOSTING": "TRUE",
        "AWS_HTTPS": "YES",
    }
    # Vars that must NOT leak from a credentialed (eodata) context.
    clear_keys = ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    saved: dict[str, Optional[str]] = {}
    with _gdal_env_lock:
        for k, v in new_vals.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        for k in clear_keys:
            saved[k] = os.environ.get(k)
            os.environ.pop(k, None)
        for k, v in _GDAL_TUNING_DEFAULTS.items():
            os.environ.setdefault(k, v)
    try:
        yield
    finally:
        with _gdal_env_lock:
            for k, prev in saved.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev


class DEMService:
    """Fetch and align Copernicus DEM GLO-30 data for Sentinel overlays via GDAL vsis3."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "dem")
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_aligned_dem_path(self, reference_raster: str, target_resolution_m: int) -> Optional[str]:
        """Create or reuse an aligned DEM raster (float32, metres) matching a reference grid.

        Streams only the needed window from the public COG tiles via GDAL/vsis3.
        """
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception:
            logger.exception("rasterio is required for DEM alignment")
            return None

        aligned_key = self._aligned_key(reference_raster, target_resolution_m)
        aligned_path = self.aligned_cache / f"{aligned_key}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached DEM overlay: {aligned_path}")
            return str(aligned_path)

        try:
            with rasterio.open(reference_raster) as ref:
                ref_bounds_4326 = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                left, bottom, right, top = ref_bounds_4326
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs

            tiles = _tiles_for_bounds(left, bottom, right, top)
            if not tiles:
                logger.warning("No DEM tiles found for reference bounds")
                return None

            logger.info(f"DEM tiles needed for {left:.2f},{bottom:.2f}..{right:.2f},{top:.2f}: {[_tile_dir(la, lo) for la, lo in tiles]}")

            tile_arrays: List[np.ndarray] = []
            tile_transforms = []
            with _public_s3_env():
                for lat_o, lon_o in tiles:
                    arr, transform = self._read_tile_window(lat_o, lon_o, left, bottom, right, top, target_resolution_m)
                    if arr is not None:
                        tile_arrays.append(arr)
                        tile_transforms.append(transform)

            if not tile_arrays:
                logger.warning("No DEM tile data could be read")
                return None

            if len(tile_arrays) == 1:
                mosaic_data = tile_arrays[0]
                mosaic_transform = tile_transforms[0]
            else:
                mosaic_data, mosaic_transform = self._mosaic(tile_arrays, tile_transforms)

            h, w = mosaic_data.shape
            # ``mosaic_transform`` is already a north-up affine for the mosaic grid.
            tmp_profile = {
                "driver": "GTiff",
                "dtype": "float32",
                "count": 1,
                "crs": "EPSG:4326",
                "transform": mosaic_transform,
                "width": w,
                "height": h,
                "nodata": DEM_NODATA,
            }

            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
                tmp_path = tmpf.name
            with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
                tmp_dst.write(mosaic_data.astype("float32"), 1)

            dst_profile = ref_profile.copy()
            dst_profile.update(
                driver="GTiff",
                dtype="float32",
                count=1,
                compress="deflate",
                tiled=True,
                nodata=DEM_NODATA,
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
                        src_nodata=DEM_NODATA,
                        dst_nodata=DEM_NODATA,
                        resampling=Resampling.bilinear,
                    )
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            self._build_overviews(str(aligned_path))
            logger.info(f"DEM overlay written: {aligned_path}")
            return str(aligned_path)

        except Exception as exc:
            logger.error(f"DEM alignment failed: {exc}")
            return None

    def get_colorized_dem_path(self, reference_raster: str, target_resolution_m: int) -> Optional[str]:
        """Return an RGBA GeoTIFF with a hypsometric tint + hillshade relief.

        nodata (no tile coverage, e.g. ocean) is rendered fully transparent.
        """
        try:
            import rasterio
        except Exception:
            logger.exception("rasterio is required for DEM colorization")
            return None

        aligned = self.get_aligned_dem_path(reference_raster, target_resolution_m)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|dem-rgba-v1".encode("utf-8")).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

        try:
            lut = self._colormap_lut()
            with rasterio.open(aligned) as src:
                nodata = src.nodata if src.nodata is not None else DEM_NODATA
                cellsize = abs(src.transform.a) or 1.0
                vmin, vmax = self._compute_stretch(src, nodata)
                span = max(vmax - vmin, 1e-6)

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

                pad = 8
                with rasterio.open(colorized_path, "w", **profile) as dst:
                    for _, window in src.block_windows(1):
                        row_off = int(window.row_off)
                        col_off = int(window.col_off)
                        bh = int(window.height)
                        bw = int(window.width)

                        padded_window = ((row_off - pad, row_off + bh + pad), (col_off - pad, col_off + bw + pad))
                        z = src.read(1, window=padded_window, boundless=True, fill_value=nodata).astype("float32")
                        valid = z != nodata

                        if not valid.any():
                            zeros = np.zeros((bh, bw), dtype=np.uint8)
                            for band in range(1, 5):
                                dst.write(zeros, band, window=window)
                            continue

                        z_filled = np.where(valid, z, vmin)
                        shade = self._hillshade(z_filled, cellsize)

                        # Crop padding back to the block extent.
                        z_c = z[pad : pad + bh, pad : pad + bw]
                        valid_c = valid[pad : pad + bh, pad : pad + bw]
                        shade_c = shade[pad : pad + bh, pad : pad + bw]

                        norm = np.clip((z_c - vmin) / span, 0.0, 1.0)
                        idx = (norm * 255.0).astype(np.uint8)
                        rgb = lut[idx].astype(np.float32)  # (bh, bw, 3)
                        rgb *= (0.35 + 0.65 * shade_c)[..., None]
                        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
                        alpha = np.where(valid_c, 255, 0).astype(np.uint8)

                        dst.write(rgb[:, :, 0], 1, window=window)
                        dst.write(rgb[:, :, 1], 2, window=window)
                        dst.write(rgb[:, :, 2], 3, window=window)
                        dst.write(alpha, 4, window=window)

            self._build_overviews(str(colorized_path))
            return str(colorized_path)
        except Exception:
            logger.exception("Failed to build colorized DEM overlay")
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _vsis3_path(self, lat_origin: int, lon_origin: int) -> str:
        stem = _tile_dir(lat_origin, lon_origin)
        return f"/vsis3/{DEM_BUCKET}/{stem}/{stem}.tif"

    def _read_tile_window(
        self,
        lat_origin: int,
        lon_origin: int,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open a DEM tile via vsis3, read the intersecting window at target resolution."""
        import rasterio
        import rasterio.transform as rtransform
        import rasterio.windows as rwin
        from rasterio.enums import Resampling

        path = self._vsis3_path(lat_origin, lon_origin)
        try:
            with rasterio.open(path) as src:
                b = src.bounds
                lon_min = max(left, b.left)
                lat_min = max(bottom, b.bottom)
                lon_max = min(right, b.right)
                lat_max = min(top, b.top)
                if lon_min >= lon_max or lat_min >= lat_max:
                    return None, None

                window = rwin.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)

                # Decimate the read to the target ground resolution so GDAL serves
                # it from the nearest COG overview (minimal bytes over the wire).
                # Copernicus DEM uses latitude-dependent column counts, so estimate
                # the longitudinal span in metres with a cos(lat) correction rather
                # than from the raw degree pixel size.
                mid_lat = math.radians((lat_min + lat_max) / 2.0)
                lon_span_m = (lon_max - lon_min) * 111_320.0 * max(math.cos(mid_lat), 0.1)
                lat_span_m = (lat_max - lat_min) * 110_540.0
                out_w = max(1, min(int(round(window.width)), int(round(lon_span_m / target_resolution_m))))
                out_h = max(1, min(int(round(window.height)), int(round(lat_span_m / target_resolution_m))))

                data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
                src_nodata = src.nodata
                if src_nodata is not None:
                    data = np.where(data == np.float32(src_nodata), DEM_NODATA, data)

                left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
                transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
                logger.info(f"DEM tile {_tile_dir(lat_origin, lon_origin)}: read {out_w}×{out_h} px (target ~{target_resolution_m}m)")
                return data, transform
        except Exception as exc:
            logger.warning(f"Could not read DEM tile {_tile_dir(lat_origin, lon_origin)} via vsis3: {exc}")
            return None, None

    def _mosaic(self, arrays: List[np.ndarray], transforms: List) -> Tuple[np.ndarray, Any]:
        """Merge multiple float32 tile arrays using rasterio.merge."""
        from rasterio.io import MemoryFile
        from rasterio.merge import merge

        mem_files = []
        open_dsets = []
        try:
            for arr, t in zip(arrays, transforms):
                h, w = arr.shape
                profile = {
                    "driver": "GTiff",
                    "dtype": "float32",
                    "count": 1,
                    "crs": "EPSG:4326",
                    "transform": t,
                    "width": w,
                    "height": h,
                    "nodata": DEM_NODATA,
                }
                mf = MemoryFile()
                with mf.open(**profile) as ds:
                    ds.write(arr.astype("float32"), 1)
                mem_files.append(mf)
                open_dsets.append(mf.open())

            mosaic_data, mosaic_transform = merge(open_dsets, nodata=DEM_NODATA)
            return mosaic_data[0], mosaic_transform
        finally:
            for ds in open_dsets:
                ds.close()
            for mf in mem_files:
                mf.close()

    def _compute_stretch(self, src, nodata: float) -> Tuple[float, float]:
        """Compute a 2–98% elevation stretch from a downsampled read of the aligned raster."""
        maxdim = 1024
        scale = max(1, int(max(src.width, src.height) / maxdim))
        out_h = max(1, src.height // scale)
        out_w = max(1, src.width // scale)
        arr = src.read(1, out_shape=(out_h, out_w)).astype("float32")
        valid = arr != nodata
        if not valid.any():
            return 0.0, 1.0
        vals = arr[valid]
        vmin = float(np.percentile(vals, 2))
        vmax = float(np.percentile(vals, 98))
        if vmax <= vmin:
            vmax = vmin + 1.0
        return vmin, vmax

    @staticmethod
    def _colormap_lut() -> np.ndarray:
        """256×3 uint8 hypsometric LUT from the matplotlib 'terrain' colormap (land sub-range)."""
        import matplotlib

        cmap = matplotlib.colormaps["terrain"]
        return (cmap(np.linspace(0.25, 1.0, 256))[:, :3] * 255).astype(np.uint8)

    @staticmethod
    def _hillshade(z: np.ndarray, cellsize: float, azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
        """Return a 0–1 hillshade for a 2D elevation array (metres) on a metric grid."""
        if cellsize <= 0:
            cellsize = 1.0
        gy, gx = np.gradient(z, cellsize)
        slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az = np.radians(360.0 - azimuth + 90.0)
        alt = np.radians(altitude)
        shaded = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        return np.clip((shaded + 1.0) / 2.0, 0.0, 1.0)

    def _aligned_key(self, reference_raster: str, resolution: int) -> str:
        ref_id = hashlib.sha1(reference_raster.encode()).hexdigest()[:8]
        return f"dem_glo30_{resolution}m_{ref_id}"

    def _build_overviews(self, path: str) -> None:
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")
        except Exception as exc:
            logger.debug(f"Overview build failed for {path}: {exc}")


dem_service = DEMService()
