"""Sentinel-2 streaming service for on-demand TCI tile rendering from CDSE S3.

Streams Sentinel-2 TCI (True Color Image) bands directly from CDSE S3 via
GDAL /vsis3/, caches them locally as COGs for fast re-serving, and provides
quicklook URLs for instant preview while full-res loads in the background.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from loguru import logger

CDSE_S3_BUCKET = "eodata"
CDSE_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"

# Sentinel-2 L2A path pattern on CDSE S3
# s3://eodata/Sentinel-2/MSI/L2A_N{baseline}/{YYYY}/{MM}/{DD}/{product}.SAFE/
S2_S3_BASE = "Sentinel-2/MSI"

# Default L2A TCI resolution served as a preview. R60m decodes ~36× less
# data than R10m and is visually indistinguishable at the MGRS-tile-wide
# Leaflet zooms (z7–z11) where the layer is first shown.
DEFAULT_TCI_RESOLUTION: "TciResolution" = "60m"

# Subset of allowed L2A resolutions, in increasing detail.
TciResolution = Literal["60m", "20m", "10m"]
_L2A_RESOLUTIONS: tuple[TciResolution, ...] = ("60m", "20m", "10m")

# TCI band filename patterns. L2A files carry the resolution suffix
# (``_TCI_10m.jp2`` / ``_TCI_20m.jp2`` / ``_TCI_60m.jp2``); L1C files do not.
TCI_PATTERN_L2A = re.compile(r"_TCI_(?:10|20|60)m\.jp2$")
TCI_PATTERN_L1C = re.compile(r"_TCI\.jp2$")


class SentinelStreamService:
    """Stream Sentinel-2 TCI from CDSE S3 and cache as local COGs."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "streaming")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._s3_prewarmed = False

    def prewarm_s3(self) -> None:
        """Open one cheap connection to CDSE S3 to warm the GDAL + boto3 pools.

        The first ``/vsis3/`` operation in a process pays ~12 s of TCP
        slow-start, TLS handshake and HTTP/2 negotiation against the CDSE
        endpoint. Doing it once at app startup means the first user click
        reuses an already-warm socket.

        Boto3 and GDAL maintain *separate* connection pools, so we warm
        both:

        * ``s3_client.head_bucket`` — primes boto3 (used by
          ``find_tci_path_in_product``).
        * ``gdal.VSIStatL`` against a nonexistent key — primes GDAL/CURL
          (used by ``rasterio.open('/vsis3/...')``).

        Idempotent; safe to call from any thread.
        """
        if self._s3_prewarmed:
            return
        t0 = time.perf_counter()
        try:
            import boto3
            import rasterio
            from rasterio.errors import RasterioIOError

            from vresto.api.config import CopernicusConfig

            config = CopernicusConfig()
            if config.has_static_s3_credentials():
                access_key, secret_key = config.get_s3_credentials()
            else:
                access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
                secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")

            if not access_key or not secret_key:
                logger.debug("prewarm_s3: no credentials; skipping")
                return

            with _gdal_s3_env(access_key, secret_key, CDSE_S3_ENDPOINT):
                # boto3 leg
                try:
                    s3_client = boto3.client(
                        "s3",
                        endpoint_url=f"https://{CDSE_S3_ENDPOINT}",
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key,
                    )
                    s3_client.head_bucket(Bucket=CDSE_S3_BUCKET)
                except Exception as e:
                    logger.debug(f"prewarm_s3: boto3 head_bucket failed: {e}")

                # GDAL/CURL leg — opening a nonexistent /vsis3/ key triggers
                # a HEAD that returns 404. The open() raises, but the
                # TCP+TLS+HTTP/2 handshake still completes and seeds CURL's
                # connection cache so the first real rasterio.open() reuses
                # the warm socket.
                try:
                    with rasterio.open(f"/vsis3/{CDSE_S3_BUCKET}/__vresto_prewarm_probe__.tif"):
                        pass
                except (RasterioIOError, Exception) as e:
                    logger.debug(f"prewarm_s3: CURL warm probe completed: {e!r}")

            self._s3_prewarmed = True
            logger.info(f"[perf] SentinelStreamService: S3 prewarm complete in {(time.perf_counter() - t0) * 1000:.0f} ms")
        except Exception as e:
            logger.debug(f"prewarm_s3: skipped ({e})")

    def get_cached_tci_path(
        self,
        tile_code: str,
        date: str,
        resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
    ) -> Optional[str]:
        """Return cached COG path for the given resolution if it exists."""
        cache_path = self._cache_path(tile_code, date, resolution)
        if cache_path.exists():
            return str(cache_path)
        return None

    def find_any_cached_tci(self, tile_code: str, date: str) -> Optional[str]:
        """Return *any* cached TCI for this tile/date, preferring the highest
        available resolution. Useful for downstream consumers (WorldCover,
        LCM overlays) that only need a reference raster's CRS/extent and
        don't care about pixel resolution.
        """
        # Try newest format first (10m → 20m → 60m), then fall back to the
        # legacy resolution-less name for backwards compatibility with
        # caches written by earlier versions.
        for res in ("10m", "20m", "60m"):
            p = self._cache_path(tile_code, date, res)  # type: ignore[arg-type]
            if p.exists():
                return str(p)
        legacy = self.cache_root / f"{tile_code}_{date}_tci.tif"
        if legacy.exists():
            return str(legacy)
        return None

    def stream_tci(
        self,
        s3_path: str,
        tile_code: str,
        date: str,
        tci_vsis3_path: Optional[str] = None,
        resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
    ) -> Optional[str]:
        """Stream TCI band from CDSE S3 and cache as a local COG.

        Args:
            s3_path: Full S3 path to the product SAFE directory
                     (e.g., "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/01/01/S2A_....SAFE/")
            tile_code: MGRS tile code (e.g., "T34SFJ")
            date: Sensing date string (e.g., "20200101")
            tci_vsis3_path: Exact /vsis3/ path to the TCI file. If provided,
                            skips the wildcard-based path construction.
            resolution: Which L2A TCI to fetch — ``"60m"`` (default, fastest),
                ``"20m"`` (balanced), or ``"10m"`` (highest detail, ~36\u00d7
                slower than 60m). Ignored for L1C products which only
                publish a single TCI band.

        Returns:
            Path to cached COG file, or None on failure.
        """
        cache_path = self._cache_path(tile_code, date, resolution)
        if cache_path.exists():
            logger.info(f"TCI cache hit: {cache_path}")
            return str(cache_path)

        t_total = time.perf_counter()
        try:
            import rasterio

            # Prefer the exact path if provided; fall back to listing S3
            tci_vsis3 = tci_vsis3_path
            if not tci_vsis3:
                t_find = time.perf_counter()
                tci_vsis3 = self.find_tci_path_in_product(s3_path, tile_code, resolution)
                logger.info(f"[perf] stream_tci: find_tci_path_in_product took {(time.perf_counter() - t_find) * 1000:.0f} ms")
            if not tci_vsis3:
                logger.error(f"Could not determine TCI path for {s3_path}")
                return None

            logger.info(f"Streaming TCI ({resolution}) from {tci_vsis3}")

            with self._s3_env():
                # First, peek at the source to decide which overview level to
                # request. The peek is cheap (~300 ms, header-only range read)
                # because GDAL_DISABLE_READDIR_ON_OPEN is set.
                t_open = time.perf_counter()
                with rasterio.open(tci_vsis3) as probe:
                    factor = self._choose_overview_factor(probe)
                    src_width, src_height = probe.width, probe.height
                # Translate factor (1, 2, 4, 8, 16) → OVERVIEW_LEVEL (-1, 0, 1, 2, 3).
                # OpenJPEG exposes the JP2 DWT decomposition levels as overviews,
                # so opening with OVERVIEW_LEVEL=N decodes ONLY the bytes needed
                # for that resolution instead of fetching+decoding the full file.
                overview_level = self._factor_to_overview_level(factor)
                open_kwargs = {}
                if overview_level >= 0:
                    open_kwargs["OVERVIEW_LEVEL"] = overview_level

                with rasterio.open(tci_vsis3, **open_kwargs) as src:
                    open_ms = (time.perf_counter() - t_open) * 1000
                    out_width, out_height = src.width, src.height
                    logger.info(f"[perf] stream_tci: rasterio.open(/vsis3/) {open_ms:.0f} ms (src {src_width}x{src_height}, factor={factor}, OVERVIEW_LEVEL={overview_level}, out {out_width}x{out_height})")

                    t_read = time.perf_counter()
                    # Native-resolution read of the chosen overview — no
                    # client-side resampling, GDAL streams only the wavelet
                    # subbands needed for this level.
                    data = src.read()
                    read_ms = (time.perf_counter() - t_read) * 1000
                    nbytes_mb = data.nbytes / (1024 * 1024)
                    logger.info(f"[perf] stream_tci: src.read (S3 fetch + JP2 decode) {read_ms:.0f} ms ({nbytes_mb:.1f} MB decoded)")

                    # Compute output transform
                    from rasterio.transform import from_bounds

                    transform = from_bounds(*src.bounds, out_width, out_height)

                    profile = {
                        "driver": "GTiff",
                        "dtype": data.dtype,
                        "count": src.count,
                        "crs": src.crs,
                        "transform": transform,
                        "width": out_width,
                        "height": out_height,
                        "compress": "deflate",
                        "tiled": True,
                        "blockxsize": 256,
                        "blockysize": 256,
                    }

            # Write GeoTIFF then build internal overviews so the local tile
            # server (rio-tiler) can serve zoomed-out Leaflet tiles by reading
            # a pre-decoded pyramid level instead of re-decoding the full
            # raster on every HTTP request.
            t_write = time.perf_counter()
            with rasterio.open(str(cache_path), "w", **profile) as dst:
                dst.write(data)
            write_ms = (time.perf_counter() - t_write) * 1000
            cache_mb = cache_path.stat().st_size / (1024 * 1024)
            logger.info(f"[perf] stream_tci: GTiff write {write_ms:.0f} ms ({cache_mb:.1f} MB on disk)")

            t_ovr = time.perf_counter()
            self._build_overviews(str(cache_path))
            logger.info(f"[perf] stream_tci: build_overviews {(time.perf_counter() - t_ovr) * 1000:.0f} ms")

            total_ms = (time.perf_counter() - t_total) * 1000
            logger.info(f"TCI cached: {cache_path} ({out_width}x{out_height} px) [total {total_ms:.0f} ms]")
            return str(cache_path)

        except Exception as e:
            logger.error(f"Failed to stream TCI for {tile_code}/{date}: {e}")
            # Clean up partial file
            if cache_path.exists():
                cache_path.unlink()
            return None

    def get_quicklook_url(self, product_name: str) -> Optional[str]:
        """Build a quicklook URL for instant preview from the OData API.

        Args:
            product_name: Full product name (e.g., "S2A_MSIL2A_20200101T...SAFE")

        Returns:
            HTTPS URL to the quicklook image or None.
        """
        # CDSE OData quicklook endpoint
        clean_name = product_name.replace(".SAFE", "")
        quicklook_url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Assets('{clean_name}.SAFE')/quicklook"
        return quicklook_url

    def _cache_path(
        self,
        tile_code: str,
        date: str,
        resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
    ) -> Path:
        """Compute cache file path for a given tile, date, and resolution."""
        return self.cache_root / f"{tile_code}_{date}_{resolution}_tci.tif"

    def _build_tci_vsis3_path(
        self,
        s3_path: str,
        tile_code: str,
        resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
    ) -> Optional[str]:
        """Construct the /vsis3/ path to the TCI band within a product.

        The TCI band in L2A products lives at:
        .SAFE/GRANULE/L2A_.../IMG_DATA/R{resolution}/T{tile}_..._TCI_{resolution}.jp2
        """
        # Normalize path — strip s3:// prefix, leading slash, and bucket name
        path = s3_path
        if path.startswith("s3://"):
            path = path[5:].lstrip("/")
        else:
            path = path.lstrip("/")
        if path.startswith(f"{CDSE_S3_BUCKET}/"):
            path = path[len(CDSE_S3_BUCKET) + 1 :]

        # Ensure trailing slash
        if not path.endswith("/"):
            path += "/"

        # Determine product level from path
        is_l2a = "L2A" in path

        if is_l2a:
            # L2A structure: .SAFE/GRANULE/<granule_id>/IMG_DATA/R{res}/<tile>_<date>_TCI_{res}.jp2
            vsis3_base = f"/vsis3/{CDSE_S3_BUCKET}/{path}"
            return f"{vsis3_base}GRANULE/*/IMG_DATA/R{resolution}/*_TCI_{resolution}.jp2"
        else:
            # L1C: .SAFE/GRANULE/<granule_id>/IMG_DATA/<tile>_<date>_TCI.jp2 (no resolution suffix)
            vsis3_base = f"/vsis3/{CDSE_S3_BUCKET}/{path}"
            return f"{vsis3_base}GRANULE/*/IMG_DATA/*_TCI.jp2"

    def find_tci_path_in_product(
        self,
        s3_path: str,
        tile_code: str,
        resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
    ) -> Optional[str]:
        """List product contents on S3 to find exact TCI path.

        Falls back to constructing the path from the product name if listing fails.
        """
        t_find = time.perf_counter()
        try:
            import boto3

            from vresto.api.config import CopernicusConfig

            config = CopernicusConfig()
            if config.has_static_s3_credentials():
                access_key, secret_key = config.get_s3_credentials()
            else:
                access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
                secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")

            s3_client = boto3.client(
                "s3",
                endpoint_url=f"https://{CDSE_S3_ENDPOINT}",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )

            # Extract prefix from s3_path — normalize various formats:
            # "/eodata/Sentinel-2/..." or "s3://eodata/Sentinel-2/..." or "eodata/Sentinel-2/..."
            prefix = s3_path
            if prefix.startswith("s3://"):
                prefix = prefix[5:].lstrip("/")
            else:
                prefix = prefix.lstrip("/")
            # Strip bucket name (eodata/) from prefix to get the S3 key
            if prefix.startswith(f"{CDSE_S3_BUCKET}/"):
                prefix = prefix[len(CDSE_S3_BUCKET) + 1 :]
            if not prefix.endswith("/"):
                prefix += "/"

            paginator = s3_client.get_paginator("list_objects_v2")

            # Fast path (L2A): use Delimiter to find the single granule
            # subdirectory (1 CommonPrefix vs. thousands of files), then
            # construct the deterministic TCI filename from the product
            # datetime parsed out of the SAFE path.
            is_l2a = "L2A" in prefix
            product_datetime = self._extract_product_datetime(prefix)
            if is_l2a and product_datetime:
                granule_prefix: Optional[str] = None
                for page in paginator.paginate(
                    Bucket=CDSE_S3_BUCKET,
                    Prefix=f"{prefix}GRANULE/",
                    Delimiter="/",
                ):
                    common = page.get("CommonPrefixes") or []
                    if common:
                        granule_prefix = common[0]["Prefix"]
                        break
                if granule_prefix:
                    # Sentinel-2 TCI filenames always carry the ``T`` prefix on
                    # the tile id (``T34TFL_...``); MGRS callers may pass the
                    # bare code (``34TFL``).  Normalise here.
                    tile_with_t = tile_code if tile_code.startswith("T") else f"T{tile_code}"
                    tci_key = f"{granule_prefix}IMG_DATA/R{resolution}/{tile_with_t}_{product_datetime}_TCI_{resolution}.jp2"
                    # Cheap HEAD verify (~50-150 ms) before returning so any
                    # unexpected layout falls back to the full LIST below
                    # rather than blowing up later in ``stream_tci`` with
                    # ``NoSuchKey``.
                    try:
                        s3_client.head_object(Bucket=CDSE_S3_BUCKET, Key=tci_key)
                        logger.info(f"[perf] find_tci_path_in_product (fast L2A, {resolution}): {(time.perf_counter() - t_find) * 1000:.0f} ms")
                        return f"/vsis3/{CDSE_S3_BUCKET}/{tci_key}"
                    except Exception:
                        logger.debug(f"Constructed TCI path missing, falling back to LIST: {tci_key}")
                # If delimiter LIST returned nothing, fall through to full LIST.

            # Fallback: full prefix LIST (covers L1C and any unexpected layout).
            # When a specific L2A resolution was requested, prefer an exact
            # match on that resolution; otherwise accept any TCI band.
            l2a_exact = re.compile(rf"_TCI_{resolution}\.jp2$")
            best_match: Optional[str] = None
            for page in paginator.paginate(Bucket=CDSE_S3_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if l2a_exact.search(key):
                        logger.info(f"[perf] find_tci_path_in_product (full LIST, {resolution}): {(time.perf_counter() - t_find) * 1000:.0f} ms")
                        return f"/vsis3/{CDSE_S3_BUCKET}/{key}"
                    if best_match is None and (TCI_PATTERN_L2A.search(key) or TCI_PATTERN_L1C.search(key)):
                        best_match = key

            if best_match:
                logger.info(f"[perf] find_tci_path_in_product (full LIST, fallback): {(time.perf_counter() - t_find) * 1000:.0f} ms")
                return f"/vsis3/{CDSE_S3_BUCKET}/{best_match}"

            logger.warning(f"TCI band not found in {s3_path}")
            return None

        except Exception as e:
            logger.warning(f"Failed to list S3 for TCI path: {e}")
            return None

    @staticmethod
    def _extract_product_datetime(prefix: str) -> Optional[str]:
        """Parse the sensing-datetime token (e.g. ``20200131T091231``) from a
        Sentinel-2 SAFE path. Returns ``None`` if no match.
        """
        m = re.search(r"S2[AB]_MSIL(?:2A|1C)_(\d{8}T\d{6})_", prefix)
        return m.group(1) if m else None

    def _choose_overview_factor(self, src) -> int:
        """Choose overview factor for initial display (target ~1400px wide).

        At 10980x10980 source resolution this picks factor 8 → 1372x1372,
        which decodes/writes ~4x faster than factor 4 while still looking
        sharp at Leaflet zooms 7-11 (the typical post-fit_bounds range for
        an MGRS tile).
        """
        target_width = 1400
        if src.width <= target_width:
            return 1
        factor = src.width // target_width
        # Snap to nearest power of 2
        valid_factors = [1, 2, 4, 8, 16]
        for f in reversed(valid_factors):
            if f <= factor:
                return f
        return 1

    @staticmethod
    def _factor_to_overview_level(factor: int) -> int:
        """Map a downsample factor (powers of 2) to a GDAL OVERVIEW_LEVEL.

        OVERVIEW_LEVEL is 0-indexed: 0 is the first overview (factor 2),
        1 is the second (factor 4), etc. Factor 1 means "full resolution"
        and is represented as -1 (i.e. don't pass the open option).
        """
        if factor <= 1:
            return -1
        # log2(factor) - 1: 2→0, 4→1, 8→2, 16→3
        level = factor.bit_length() - 2
        return max(0, level)

    def _s3_env(self):
        """Return a context manager that configures GDAL S3 credentials via os.environ.

        rasterio.Env(session=AWSSession(...)) does not propagate GDAL config to
        worker threads (used by asyncio.to_thread).  Setting os.environ directly
        is the only reliable approach for thread-safe /vsis3/ access.
        """
        from vresto.api.config import CopernicusConfig

        config = CopernicusConfig()
        if config.has_static_s3_credentials():
            access_key, secret_key = config.get_s3_credentials()
        else:
            access_key = os.environ.get("COPERNICUS_S3_ACCESS_KEY", "")
            secret_key = os.environ.get("COPERNICUS_S3_SECRET_KEY", "")

        return _gdal_s3_env(
            access_key=access_key,
            secret_key=secret_key,
            endpoint=CDSE_S3_ENDPOINT,
        )

    def _build_overviews(self, path: str) -> None:
        """Build internal overviews for fast tile serving.

        Levels [2, 4, 8] are sufficient for a ~2745² cached raster: at
        factor 8 the smallest level is ~343², well below Leaflet's
        256-pixel tile size.
        """
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8], Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")
        except Exception as e:
            logger.debug(f"Overview build failed for {path}: {e}")


# ---------------------------------------------------------------------------
# Thread-safe GDAL S3 configuration via os.environ
# ---------------------------------------------------------------------------
_gdal_env_lock = threading.Lock()

# AWS credential + endpoint vars (cleaned up on exit)
_GDAL_S3_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_S3_ENDPOINT",
    "AWS_VIRTUAL_HOSTING",
    "AWS_HTTPS",
    "AWS_NO_SIGN_REQUEST",
)

# GDAL/CURL performance-tuning vars (set once-and-leave; process-wide).
# Applying these only at /vsis3/ access time still propagates because GDAL
# re-reads the env on every open. Listed separately so they survive after
# the context manager exits (no harm to leave them set).
_GDAL_TUNING_DEFAULTS = {
    # Don't issue a sibling LIST on every .jp2/.tif open — saves 1 S3 op.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    # Restrict CURL VSI to known raster extensions (skips spurious probes).
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".jp2,.tif,.TIF,.tiff",
    # In-memory cache of CURL range-read chunks (hits on overview rebuilds,
    # tile re-decodes).
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "100000000",  # 100 MB
    # GDAL block cache for decoded tiles.
    "GDAL_CACHEMAX": "512",  # MB
    # Network resilience.
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    # Parallel inverse-DWT for OpenJPEG decode + parallel block IO for GTiff
    # writes. Single biggest CPU-side win on multi-core machines for the
    # 10980² Sentinel-2 TCI tiles.
    "GDAL_NUM_THREADS": "ALL_CPUS",
    # Prefer HTTP/2 with multiplexed range reads — CDSE supports it and the
    # per-request overhead drops sharply when many ranges share one TCP/TLS
    # connection.
    "GDAL_HTTP_VERSION": "2",
    "GDAL_HTTP_MULTIPLEX": "YES",
    # Default CURL chunk size for /vsicurl/ is 16 KB, which forces hundreds
    # of HTTP range requests for a single JP2 decode. 1 MB batches the
    # reads without over-fetching: OpenJPEG asks for many small subband
    # offsets, and a 10 MB chunk was empirically observed to drag much
    # more data than the OVERVIEW_LEVEL=1 decode actually needs.
    "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",  # 1 MB
    # Note: GDAL_HTTP_MULTIRANGE and GDAL_HTTP_MERGE_CONSECUTIVE_RANGES
    # were tried and removed — CDSE responded by serving a single merged
    # range that spanned most of the JP2 file, defeating OVERVIEW_LEVEL=1
    # and pushing the warm-connection decode from ~6 s to ~26 s.
}


@contextlib.contextmanager
def _gdal_s3_env(access_key: str, secret_key: str, endpoint: str):
    """Set GDAL S3 env vars for the duration of a ``with`` block.

    rasterio.Env(session=AWSSession(...)) does not propagate to worker
    threads.  Setting ``os.environ`` works reliably in all threads because
    GDAL reads process-wide environment variables on each /vsis3/ request.

    Also applies a set of GDAL/CURL tuning defaults (cache sizes, retry,
    READDIR suppression) without overriding any value the user has already
    exported in their shell.
    """
    new_vals = {
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_S3_ENDPOINT": endpoint,
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_HTTPS": "YES",
        "AWS_NO_SIGN_REQUEST": "NO",
    }
    old_vals: dict[str, Optional[str]] = {}
    with _gdal_env_lock:
        for k, v in new_vals.items():
            old_vals[k] = os.environ.get(k)
            os.environ[k] = v
        # Apply tuning defaults only if not already set by the environment.
        for k, v in _GDAL_TUNING_DEFAULTS.items():
            if k not in os.environ:
                os.environ[k] = v
    try:
        yield
    finally:
        with _gdal_env_lock:
            for k in _GDAL_S3_KEYS:
                prev = old_vals.get(k)
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev
            # Leave _GDAL_TUNING_DEFAULTS in place: harmless for the rest
            # of the process and avoids per-call thrash on hot paths.


# Module-level singleton
sentinel_stream_service = SentinelStreamService()
