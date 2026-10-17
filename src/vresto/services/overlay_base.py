"""Shared building blocks for the map overlay services.

Every overlay service turns some global raster product into two cached files
aligned to a streamed Sentinel-2 tile (the *reference raster*):

* an **aligned** raster holding the product's values on the tile's grid, which
  the point inspector samples; and
* a **colorized** RGBA raster derived from it, which the tile server renders.

The helpers here cover the steps every service repeats (CDSE S3 credentials,
windowed reads at a target resolution, overviews, cache keys, colour ramps).
:class:`ClmsStacOverlayService` implements the whole pipeline for products
found through the CDSE STAC catalogue, so such a service only declares its
collection, how to decode raw pixels and how to colour them.
"""

from __future__ import annotations

import functools
import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

import numpy as np
from loguru import logger

from vresto.api import stac_assets

CDSE_EODATA_ENDPOINT = "eodata.dataspace.copernicus.eu"
OVERVIEW_FACTORS = [2, 4, 8, 16, 32]

RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class OverlayResult:
    """Colorized overlay plus the selected source timestamp and the aligned raster it was derived from."""

    colorized_path: str
    selected_datetime: datetime
    aligned_path: str = ""


def cdse_s3_env(endpoint: str = CDSE_EODATA_ENDPOINT, label: str = "overlay"):
    """Return a context manager that points GDAL's ``/vsis3/`` at CDSE with the configured keys.

    Static keys from :class:`CopernicusConfig` win; otherwise the
    ``COPERNICUS_S3_*`` environment variables are used. ``label`` names the
    product in the warning logged when no access key is available.
    """
    from vresto.api.config import CopernicusConfig
    from vresto.services.sentinel_stream import _gdal_s3_env

    config = CopernicusConfig()
    if config.has_static_s3_credentials():
        access_key, secret_key = config.get_s3_credentials()
    else:
        access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
        secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")
    if not access_key:
        logger.warning(f"COPERNICUS_S3_ACCESS_KEY not set; {label} vsis3 reads may fail")
    return _gdal_s3_env(access_key=access_key, secret_key=secret_key, endpoint=endpoint)


def read_window_at_resolution(
    src: Any,
    left: float,
    bottom: float,
    right: float,
    top: float,
    target_resolution_m: int,
    resampling: str = "nearest",
) -> Optional[Tuple[np.ndarray, Any]]:
    """Read band 1 of a lon/lat raster over a bbox, decimated to roughly ``target_resolution_m``.

    The read is never upsampled beyond the native grid, so GDAL can serve it
    from an overview. Returns ``(data, transform)`` with the transform snapped
    to the source pixel grid, or ``None`` when the bbox misses the raster.
    """
    import rasterio.transform as rtransform
    import rasterio.windows as rwin
    from rasterio.enums import Resampling

    b = src.bounds
    lon_min = max(left, b.left)
    lat_min = max(bottom, b.bottom)
    lon_max = min(right, b.right)
    lat_max = min(top, b.top)
    if lon_min >= lon_max or lat_min >= lat_max:
        return None

    window = rwin.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
    mid_lat = math.radians((lat_min + lat_max) / 2.0)
    lon_span_m = (lon_max - lon_min) * 111_320.0 * max(math.cos(mid_lat), 0.1)
    lat_span_m = (lat_max - lat_min) * 110_540.0
    out_w = max(1, min(int(round(window.width)), int(round(lon_span_m / target_resolution_m))))
    out_h = max(1, min(int(round(window.height)), int(round(lat_span_m / target_resolution_m))))

    data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling[resampling])
    left_snap, bottom_snap, right_snap, top_snap = rwin.bounds(window, src.transform)
    transform = rtransform.from_bounds(left_snap, bottom_snap, right_snap, top_snap, out_w, out_h)
    return data, transform


def build_overlay_overviews(path: str, resampling: str = "average") -> None:
    """Build internal overviews so the tile server can render zoomed-out views cheaply.

    Use ``"nearest"`` for categorical rasters so class codes are not blended.
    Failures are logged and ignored: overviews only speed up serving.
    """
    try:
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(path, "r+") as dst:
            dst.build_overviews(OVERVIEW_FACTORS, Resampling[resampling])
            dst.update_tags(ns="rio_overview", resampling=resampling)
    except Exception as exc:
        logger.debug(f"Overview build failed for {path}: {exc}")


def reference_id(reference_raster: str) -> str:
    """Short stable id of a reference raster path, used in cache file names."""
    return hashlib.sha1(reference_raster.encode()).hexdigest()[:8]


@functools.lru_cache(maxsize=None)
def colormap_lut(name: str) -> np.ndarray:
    """256-entry RGB lookup table (uint8) for a named matplotlib colormap. Cached; treat as read-only."""
    import matplotlib

    cmap = matplotlib.colormaps[name]
    return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


def ramp_rgba(values: np.ndarray, valid: np.ndarray, lut: np.ndarray, vmin: float, vmax: float) -> Tuple[np.ndarray, ...]:
    """Colour ``values`` on a linear ramp from ``vmin`` to ``vmax``; invalid pixels are transparent."""
    r = np.zeros_like(values, dtype=np.uint8)
    g = np.zeros_like(values, dtype=np.uint8)
    b = np.zeros_like(values, dtype=np.uint8)
    a = np.where(valid, 255, 0).astype(np.uint8)

    if np.any(valid):
        span = max(vmax - vmin, 1e-6)
        norm = np.clip((values - vmin) / span, 0.0, 1.0)
        idx = (norm * 255.0).astype(np.uint8)
        rgb = lut[idx]
        r[valid] = rgb[:, :, 0][valid]
        g[valid] = rgb[:, :, 1][valid]
        b[valid] = rgb[:, :, 2][valid]
    return r, g, b, a


def paint_classes(values: np.ndarray, colors: Dict[int, RGB]) -> Tuple[np.ndarray, ...]:
    """Colour each pixel whose value is a key of ``colors``; returns ``(r, g, b, matched)``."""
    r = np.zeros_like(values, dtype=np.uint8)
    g = np.zeros_like(values, dtype=np.uint8)
    b = np.zeros_like(values, dtype=np.uint8)
    matched = np.zeros(values.shape, dtype=bool)
    for value, (cr, cg, cb) in colors.items():
        mask = values == value
        if np.any(mask):
            r[mask] = cr
            g[mask] = cg
            b[mask] = cb
            matched |= mask
    return r, g, b, matched


class ClmsStacOverlayService:
    """Overlay pipeline for a single-asset COG product discovered through CDSE STAC.

    For a reference raster and date the service finds the closest STAC item,
    reads the intersecting window over ``/vsis3/``, decodes it, reprojects it
    onto the reference grid (the aligned raster) and colours it block by block
    (the colorized raster). Both files are cached under ``cache_root``.

    Subclasses set the class attributes below and implement :meth:`_decode` and
    :meth:`_colorize`. The cache key formats must stay stable across releases,
    otherwise users' existing overlay caches are silently invalidated.
    """

    #: Short product key: names the default cache directory and prefixes cache file names.
    name: ClassVar[str]
    #: Human-readable product name used in log messages.
    label: ClassVar[str]
    #: Prefix of aligned-raster cache file names, when it differs from ``name``.
    key_prefix: ClassVar[Optional[str]] = None
    #: Version hashed into colorized-raster cache file names; bump it when the colours change.
    rgba_version: ClassVar[str] = "v1"

    collection_id: ClassVar[str]
    asset_key: ClassVar[str]
    s3_endpoint: ClassVar[str] = CDSE_EODATA_ENDPOINT
    search_window_days: ClassVar[int]
    max_items: ClassVar[int]

    #: dtype and nodata of the aligned raster (the decoded values).
    aligned_dtype: ClassVar[str] = "float32"
    aligned_nodata: ClassVar[float] = -9999.0
    #: Resampling for the source read, the reprojection and the overviews.
    read_resampling: ClassVar[str] = "nearest"
    align_resampling: ClassVar[str] = "bilinear"
    overview_resampling: ClassVar[str] = "average"

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / self.name)
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    # ── Hooks ──────────────────────────────────────────────────────────────

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        """Turn raw pixels read from the source COG into aligned values (``aligned_nodata`` where invalid)."""
        raise NotImplementedError

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return the ``(r, g, b, a)`` uint8 bands for a block of aligned values."""
        raise NotImplementedError

    def _covers(self, left: float, bottom: float, right: float, top: float) -> bool:
        """Whether the product can have data for a lon/lat bbox; override for regional products."""
        return True

    # ── Pipeline ───────────────────────────────────────────────────────────

    def get_aligned_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned raster plus the selected source timestamp."""
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.warp import reproject, transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for {self.label} alignment: {exc}")
            return None

        try:
            with rasterio.open(reference_raster) as ref:
                left, bottom, right, top = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
                ref_profile = ref.profile.copy()
                dst_transform = ref.transform
                dst_crs = ref.crs
        except Exception as exc:
            logger.error(f"Could not read {self.label} reference raster {reference_raster}: {exc}")
            return None

        if not self._covers(left, bottom, right, top):
            logger.info(f"{self.label} requested outside its coverage")
            return None

        asset = stac_assets.find_closest_stac_asset(
            self.collection_id,
            [left, bottom, right, top],
            date,
            self.asset_key,
            search_window_days=self.search_window_days,
            max_items=self.max_items,
        )
        if not asset:
            return None

        date_token = asset.item_datetime.strftime("%Y%m%d")
        aligned_path = self.aligned_cache / f"{self._aligned_key(reference_raster, target_resolution_m, date_token)}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached {self.label} overlay: {aligned_path}")
            return str(aligned_path), asset.item_datetime

        data, src_transform = self._read_global_window(asset.href, left, bottom, right, top, target_resolution_m)
        if data is None:
            logger.warning(f"No {self.label} data could be read for reference bounds")
            return None

        h, w = data.shape
        tmp_profile = {
            "driver": "GTiff",
            "dtype": self.aligned_dtype,
            "count": 1,
            "crs": "EPSG:4326",
            "transform": src_transform,
            "width": w,
            "height": h,
            "nodata": self.aligned_nodata,
        }
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
            tmp_path = tmpf.name
        with rasterio.open(tmp_path, "w", **tmp_profile) as tmp_dst:
            tmp_dst.write(data, 1)

        dst_profile = ref_profile.copy()
        dst_profile.update(
            driver="GTiff",
            dtype=self.aligned_dtype,
            count=1,
            compress="deflate",
            tiled=True,
            nodata=self.aligned_nodata,
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
                    src_nodata=self.aligned_nodata,
                    dst_nodata=self.aligned_nodata,
                    resampling=Resampling[self.align_resampling],
                )
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        self._build_overviews(str(aligned_path))
        logger.info(f"{self.label} overlay written: {aligned_path}")
        return str(aligned_path), asset.item_datetime

    def get_aligned_path(self, reference_raster: str, target_resolution_m: int, date: str) -> Optional[str]:
        """Return only the aligned raster path."""
        result = self.get_aligned_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result[0]

    def get_colorized_result(self, reference_raster: str, target_resolution_m: int, date: str) -> Optional[OverlayResult]:
        """Return a colorized overlay plus the selected source timestamp."""
        try:
            import rasterio
        except Exception:
            return None

        aligned_result = self.get_aligned_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None
        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|{self.name}-rgba-{self.rgba_version}".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return OverlayResult(str(colorized_path), selected_datetime, aligned_path=aligned)

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
                    for band, channel in enumerate(self._colorize(src.read(1, window=window)), start=1):
                        dst.write(channel, band, window=window)

        self._build_overviews(str(colorized_path))
        return OverlayResult(str(colorized_path), selected_datetime, aligned_path=aligned)

    def get_colorized_path(self, reference_raster: str, target_resolution_m: int, date: str) -> Optional[str]:
        """Return only the colorized raster path."""
        result = self.get_colorized_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result.colorized_path

    # ── Internals ──────────────────────────────────────────────────────────

    def _s3_env(self):
        return cdse_s3_env(self.s3_endpoint, self.label)

    def _read_global_window(
        self,
        href: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
        target_resolution_m: int,
    ) -> Tuple[Optional[np.ndarray], Optional[Any]]:
        """Open the selected COG via vsis3, read the intersecting window and decode it."""
        import rasterio

        try:
            with self._s3_env():
                with rasterio.open(href) as src:
                    read = read_window_at_resolution(src, left, bottom, right, top, target_resolution_m, self.read_resampling)
                    if read is None:
                        return None, None
                    data, transform = read
                    h, w = data.shape
                    logger.info(f"{self.label} {href}: read {w}×{h} px (target ~{target_resolution_m}m)")
                    return self._decode(data, src), transform
        except Exception as exc:
            logger.warning(f"Could not read {self.label} via vsis3: {exc}")
            return None, None

    def _aligned_key(self, reference_raster: str, resolution: int, date_token: str) -> str:
        return f"{self.key_prefix or self.name}_{date_token}_{resolution}m_{reference_id(reference_raster)}"

    def _build_overviews(self, path: str) -> None:
        build_overlay_overviews(path, self.overview_resampling)


class RampOverlayService(ClmsStacOverlayService):
    """Continuous product coloured on a fixed ``vmin``..``vmax`` matplotlib ramp."""

    colormap: ClassVar[str]
    vmin: ClassVar[float]
    vmax: ClassVar[float]

    @classmethod
    def _colormap_lut(cls) -> np.ndarray:
        return colormap_lut(cls.colormap)

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, ...]:
        values = values.astype("float32")
        return ramp_rgba(values, values != self.aligned_nodata, self._colormap_lut(), self.vmin, self.vmax)
