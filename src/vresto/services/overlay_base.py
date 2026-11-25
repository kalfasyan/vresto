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

import contextlib
import functools
import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

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


@dataclass(frozen=True)
class SeriesPoint:
    """Value of an overlay product at one point and time; ``value`` is None where the product has no data."""

    datetime: datetime
    value: Optional[float]
    item_id: str = ""


def cdse_s3_env(endpoint: str = CDSE_EODATA_ENDPOINT, label: str = "overlay"):
    """Return a context manager that points GDAL's ``/vsis3/`` at CDSE with the configured keys.

    Static keys from :class:`CopernicusConfig` win; otherwise the
    ``COPERNICUS_S3_*`` environment variables are used. ``label`` names the
    product in the warning logged when no access key is available.
    """
    from vresto.api.config import CopernicusConfig
    from vresto.services.gdal_env import _gdal_s3_env

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


def tile_code(lat_origin: int, lon_origin: int) -> str:
    """Name a degree tile by its SW corner, like ``N33E006`` or ``S12W063``."""
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"{ns}{abs(lat_origin):02d}{ew}{abs(lon_origin):03d}"


def degree_tiles(left: float, bottom: float, right: float, top: float, step: int) -> List[Tuple[int, int]]:
    """Return the ``(lat, lon)`` SW corners of all ``step``° tiles that intersect a lon/lat bbox."""
    tiles = []
    lat = math.floor(bottom / step) * step
    while lat < top:
        lon = math.floor(left / step) * step
        while lon < right:
            tiles.append((lat, lon))
            lon += step
        lat += step
    return tiles


def read_window_at_overview(src: Any, left: float, bottom: float, right: float, top: float, target_resolution_m: int) -> Optional[Tuple[np.ndarray, Any]]:
    """Like :func:`read_window_at_resolution`, but decimate by the coarsest COG overview factor that keeps ``target_resolution_m``.

    Suits categorical tiles whose native grid is close to the target: the read
    snaps to an existing overview level and uses nearest resampling.
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

    ideal = target_resolution_m / (abs(src.transform.a) * 111_320.0)
    candidates = [f for f in (src.overviews(1) or [1]) if f <= ideal]
    factor = int(max(candidates)) if candidates else 1

    window = rwin.from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
    h = max(1, round(window.height / factor))
    w = max(1, round(window.width / factor))
    data = src.read(1, window=window, out_shape=(h, w), resampling=Resampling.nearest)
    transform = rtransform.from_bounds(*rwin.bounds(window, src.transform), w, h)
    return data, transform


def mosaic_arrays(arrays: List[np.ndarray], transforms: List[Any], dtype: str, nodata: float) -> Tuple[np.ndarray, Any]:
    """Merge lon/lat arrays (one per source tile) into one, keeping each tile's exact grid."""
    if len(arrays) == 1:
        return arrays[0], transforms[0]

    from rasterio.io import MemoryFile
    from rasterio.merge import merge

    mem_files = []
    datasets = []
    try:
        for arr, transform in zip(arrays, transforms):
            h, w = arr.shape
            mf = MemoryFile()
            with mf.open(driver="GTiff", dtype=dtype, count=1, crs="EPSG:4326", transform=transform, width=w, height=h, nodata=nodata) as ds:
                ds.write(arr.astype(dtype), 1)
            mem_files.append(mf)
            datasets.append(mf.open())
        data, transform = merge(datasets, nodata=nodata)
        return data[0], transform
    finally:
        for ds in datasets:
            ds.close()
        for mf in mem_files:
            mf.close()


def reference_grid(reference_raster: str) -> Tuple[Tuple[float, float, float, float], Dict[str, Any]]:
    """Return the reference raster's lon/lat bounds and its profile (CRS, transform, size)."""
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(reference_raster) as ref:
        return transform_bounds(ref.crs, "EPSG:4326", *ref.bounds), ref.profile.copy()


def write_aligned(path: Path, data: np.ndarray, src_transform: Any, ref_profile: Dict[str, Any], dtype: str, nodata: float, resampling: str) -> None:
    """Reproject a lon/lat array onto the reference grid and write it as a tiled GeoTIFF at ``path``."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    h, w = data.shape
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmpf:
        tmp_path = tmpf.name
    try:
        with rasterio.open(tmp_path, "w", driver="GTiff", dtype=dtype, count=1, crs="EPSG:4326", transform=src_transform, width=w, height=h, nodata=nodata) as tmp:
            tmp.write(data.astype(dtype), 1)

        profile = ref_profile.copy()
        profile.update(driver="GTiff", dtype=dtype, count=1, compress="deflate", tiled=True, nodata=nodata)
        with rasterio.open(tmp_path) as src, rasterio.open(path, "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=profile["transform"],
                dst_crs=profile["crs"],
                src_nodata=nodata,
                dst_nodata=nodata,
                resampling=Resampling[resampling],
            )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def write_colorized(aligned: str, path: Path, colorize: Callable[[np.ndarray], Tuple[np.ndarray, ...]]) -> None:
    """Write an RGBA GeoTIFF at ``path``, colouring the aligned raster block by block with ``colorize``."""
    import rasterio

    with rasterio.open(aligned) as src:
        profile = src.profile.copy()
        profile.update(count=4, dtype="uint8", nodata=None, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(path, "w", **profile) as dst:
            for _, window in src.block_windows():
                for band, channel in enumerate(colorize(src.read(1, window=window)), start=1):
                    dst.write(channel, band, window=window)


@contextlib.contextmanager
def public_s3_env(region: str):
    """Configure GDAL for anonymous reads from a public AWS Open Data bucket in ``region``.

    Clears any CDSE endpoint or static keys a credentialed overlay may have set,
    and restores the previous values on exit.
    """
    from vresto.services.gdal_env import _GDAL_TUNING_DEFAULTS, _gdal_env_lock

    new_vals = {
        "AWS_NO_SIGN_REQUEST": "YES",
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
        "AWS_VIRTUAL_HOSTING": "TRUE",
        "AWS_HTTPS": "YES",
    }
    clear_keys = ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    saved: Dict[str, Optional[str]] = {}
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
    #: Whether :meth:`point_series` makes sense for the product (continuous values, not classes).
    supports_series: ClassVar[bool] = True
    #: Most products read by one :meth:`point_series` call.
    series_max_items: ClassVar[int] = 120
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

    def _to_physical(self, values: np.ndarray) -> np.ndarray:
        """Turn decoded values into physical units for :meth:`point_series` (``aligned_nodata`` where invalid).

        Products whose aligned raster already holds physical values need no override.
        """
        return values

    # ── Pipeline ───────────────────────────────────────────────────────────

    def get_aligned_result(
        self,
        reference_raster: str,
        target_resolution_m: int,
        date: str,
    ) -> Optional[Tuple[str, datetime]]:
        """Return a cached aligned raster plus the selected source timestamp."""
        try:
            (left, bottom, right, top), ref_profile = reference_grid(reference_raster)
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

        write_aligned(aligned_path, data, src_transform, ref_profile, self.aligned_dtype, self.aligned_nodata, self.align_resampling)
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
        aligned_result = self.get_aligned_result(reference_raster, target_resolution_m, date)
        if not aligned_result:
            return None
        aligned, selected_datetime = aligned_result

        token = hashlib.sha1(f"{aligned}|{self.name}-rgba-{self.rgba_version}".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return OverlayResult(str(colorized_path), selected_datetime, aligned_path=aligned)

        write_colorized(aligned, colorized_path, self._colorize)
        self._build_overviews(str(colorized_path))
        return OverlayResult(str(colorized_path), selected_datetime, aligned_path=aligned)

    def get_colorized_path(self, reference_raster: str, target_resolution_m: int, date: str) -> Optional[str]:
        """Return only the colorized raster path."""
        result = self.get_colorized_result(reference_raster, target_resolution_m, date)
        if not result:
            return None
        return result.colorized_path

    def point_series(self, lat: float, lon: float, start_date: str, end_date: str) -> list[SeriesPoint]:
        """Return the product's value at a point for every item between two dates, oldest first.

        Reads one pixel per item straight from the source COG, with no alignment or caching.
        Needs CDSE S3 credentials, like the overlay itself.
        """
        if not self.supports_series or not self._covers(lon, lat, lon, lat):
            return []

        start = stac_assets.parse_date_like(start_date)
        end = stac_assets.parse_date_like(end_date).replace(hour=23, minute=59, second=59)
        eps = 1e-4  # a degenerate bbox is valid STAC, but some servers reject it
        assets = stac_assets.find_stac_assets_between(self.collection_id, [lon - eps, lat - eps, lon + eps, lat + eps], start, end, self.asset_key, max_items=self.series_max_items)
        if not assets:
            return []

        from concurrent.futures import ThreadPoolExecutor

        # One credentials context around all reads: it sets process-wide variables that a
        # per-read context would restore while other threads are still reading.
        with self._s3_env(), ThreadPoolExecutor(max_workers=8, thread_name_prefix=f"{self.name}-series") as pool:
            values = list(pool.map(lambda asset: self._read_point_value(asset.href, lat, lon), assets))
        return [SeriesPoint(asset.item_datetime, value, asset.item_id) for asset, value in zip(assets, values)]

    def _read_point_value(self, href: str, lat: float, lon: float) -> Optional[float]:
        """Physical value of one source COG at a point; None when outside, invalid or unreadable."""
        import rasterio

        from vresto.services.sampling import read_pixel

        try:
            with rasterio.open(href) as src:
                pixel = read_pixel(src, lat, lon)
                if pixel is None:
                    return None
                value = float(self._to_physical(self._decode(pixel, src))[0, 0])
        except Exception as exc:
            logger.warning(f"Could not read {self.label} at ({lat:.5f}, {lon:.5f}) from {href}: {exc}")
            return None
        if value == self.aligned_nodata or not math.isfinite(value):
            return None
        return value

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


class CogOverlayService:
    """Overlay pipeline for a product stored as lon/lat COGs at fixed S3 paths (one global file or degree tiles).

    For a reference raster the service reads the intersecting window of every
    source COG over ``/vsis3/``, mosaics the tiles, reprojects the result onto the
    reference grid (the aligned raster) and colours it block by block (the
    colorized raster). Both files are cached under ``cache_root``.

    A subclass sets the class attributes below and implements :meth:`_key`
    (validates the product's own arguments, such as the year) and
    :meth:`_source_paths`. Categorical products set ``classes``; others override
    :meth:`_colorize`. The cache key formats must stay stable across releases,
    otherwise users' existing overlay caches are silently invalidated.
    """

    #: Short product key: names the default cache directory.
    name: ClassVar[str]
    #: Human-readable product name used in log messages.
    label: ClassVar[str]
    #: Prefix of aligned-raster cache file names.
    key_prefix: ClassVar[str]
    #: Hashed into colorized-raster cache file names; change it when the colours change.
    rgba_token: ClassVar[str]

    aligned_dtype: ClassVar[str] = "uint8"
    aligned_nodata: ClassVar[float] = 255
    #: Read the source at the coarsest overview level that keeps the target resolution (categorical tiles).
    snap_to_overview: ClassVar[bool] = False
    read_resampling: ClassVar[str] = "nearest"
    align_resampling: ClassVar[str] = "nearest"
    overview_resampling: ClassVar[str] = "nearest"
    #: Class value → RGB for categorical products; other values are transparent.
    classes: ClassVar[Optional[Dict[int, RGB]]] = None

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / self.name)
        self.aligned_cache = self.cache_root / "aligned"
        self.aligned_cache.mkdir(parents=True, exist_ok=True)

    # ── Hooks ──────────────────────────────────────────────────────────────

    def _key(self, *args: Any, **kwargs: Any) -> Optional[Tuple[str, ...]]:
        """Turn the product's own arguments (a year, a date) into cache-key parts; None when unsupported."""
        return ()

    def _source_paths(self, left: float, bottom: float, right: float, top: float, *key: str) -> List[str]:
        """Return the ``/vsis3/`` paths of the source COGs that may cover a lon/lat bbox."""
        raise NotImplementedError

    def _s3_env(self):
        return cdse_s3_env(CDSE_EODATA_ENDPOINT, self.label)

    def _covers(self, left: float, bottom: float, right: float, top: float) -> bool:
        """Whether the product can have data for a lon/lat bbox; override for regional products."""
        return True

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        """Turn raw pixels read from a source COG into aligned values (``aligned_nodata`` where invalid)."""
        return data

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, ...]:
        """Return the ``(r, g, b, a)`` uint8 bands for a block of aligned values."""
        r, g, b, matched = paint_classes(values, self.classes or {})
        return r, g, b, np.where(matched, 255, 0).astype(np.uint8)

    def _write_colorized(self, aligned: str, path: Path) -> None:
        """Write the colorized raster; products that colour from more than one block at a time override it."""
        write_colorized(aligned, path, self._colorize)

    # ── Pipeline ───────────────────────────────────────────────────────────

    def get_aligned_path(self, reference_raster: str, target_resolution_m: int, *args: Any, **kwargs: Any) -> Optional[str]:
        """Return a cached raster of the product's values on the reference raster's grid, or None."""
        key = self._key(*args, **kwargs)
        if key is None:
            return None

        aligned_path = self.aligned_cache / f"{'_'.join([self.key_prefix, *key, f'{target_resolution_m}m', reference_id(reference_raster)])}.tif"
        if aligned_path.exists():
            logger.info(f"Reusing cached {self.label} overlay: {aligned_path}")
            return str(aligned_path)

        try:
            (left, bottom, right, top), ref_profile = reference_grid(reference_raster)
            if not self._covers(left, bottom, right, top):
                logger.info(f"{self.label} requested outside its coverage")
                return None

            arrays, transforms = [], []
            with self._s3_env():
                for path in self._source_paths(left, bottom, right, top, *key):
                    read = self._read_window(path, left, bottom, right, top, target_resolution_m)
                    if read is not None:
                        arrays.append(read[0])
                        transforms.append(read[1])
            if not arrays:
                logger.warning(f"No {self.label} data could be read for reference bounds")
                return None

            data, transform = mosaic_arrays(arrays, transforms, self.aligned_dtype, self.aligned_nodata)
            write_aligned(aligned_path, data, transform, ref_profile, self.aligned_dtype, self.aligned_nodata, self.align_resampling)
            build_overlay_overviews(str(aligned_path), self.overview_resampling)
            logger.info(f"{self.label} overlay written: {aligned_path}")
            return str(aligned_path)
        except Exception as exc:
            logger.error(f"{self.label} alignment failed: {exc}")
            aligned_path.unlink(missing_ok=True)
            return None

    def get_colorized_path(self, reference_raster: str, target_resolution_m: int, *args: Any, **kwargs: Any) -> Optional[str]:
        """Return a cached RGBA raster of the product on the reference raster's grid, or None."""
        aligned = self.get_aligned_path(reference_raster, target_resolution_m, *args, **kwargs)
        if not aligned:
            return None

        token = hashlib.sha1(f"{aligned}|{self.rgba_token}".encode()).hexdigest()
        colorized_path = self.aligned_cache / f"{token}_rgba.tif"
        if colorized_path.exists():
            return str(colorized_path)

        try:
            self._write_colorized(aligned, colorized_path)
            build_overlay_overviews(str(colorized_path), self.overview_resampling)
            return str(colorized_path)
        except Exception:
            logger.exception(f"Failed to build colorized {self.label} overlay")
            colorized_path.unlink(missing_ok=True)
            return None

    # ── Internals ──────────────────────────────────────────────────────────

    def _read_window(self, path: str, left: float, bottom: float, right: float, top: float, target_resolution_m: int) -> Optional[Tuple[np.ndarray, Any]]:
        """Read and decode the part of one source COG inside the bbox; None when it misses or cannot be read."""
        import rasterio

        try:
            with rasterio.open(path) as src:
                if self.snap_to_overview:
                    read = read_window_at_overview(src, left, bottom, right, top, target_resolution_m)
                else:
                    read = read_window_at_resolution(src, left, bottom, right, top, target_resolution_m, self.read_resampling)
                if read is None:
                    return None
                data, transform = read
                logger.info(f"{self.label} {os.path.basename(path)}: read {data.shape[1]}×{data.shape[0]} px (target ~{target_resolution_m}m)")
                return self._decode(data, src), transform
        except Exception as exc:
            logger.warning(f"Could not read {self.label} {path} via vsis3: {exc}")
            return None
