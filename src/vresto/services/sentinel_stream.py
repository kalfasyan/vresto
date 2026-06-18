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
from pathlib import Path
from typing import Optional

from loguru import logger

CDSE_S3_BUCKET = "eodata"
CDSE_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"

# Sentinel-2 L2A path pattern on CDSE S3
# s3://eodata/Sentinel-2/MSI/L2A_N{baseline}/{YYYY}/{MM}/{DD}/{product}.SAFE/
S2_S3_BASE = "Sentinel-2/MSI"

# TCI band filename patterns
TCI_PATTERN_L2A = re.compile(r"_TCI_10m\.jp2$")
TCI_PATTERN_L1C = re.compile(r"_TCI\.jp2$")


class SentinelStreamService:
    """Stream Sentinel-2 TCI from CDSE S3 and cache as local COGs."""

    def __init__(self, cache_root: Optional[Path] = None):
        self.cache_root = cache_root or (Path.home() / "vresto_downloads" / "streaming")
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def get_cached_tci_path(self, tile_code: str, date: str) -> Optional[str]:
        """Return cached COG path if it exists, else None."""
        cache_path = self._cache_path(tile_code, date)
        if cache_path.exists():
            return str(cache_path)
        return None

    def stream_tci(
        self,
        s3_path: str,
        tile_code: str,
        date: str,
        tci_vsis3_path: Optional[str] = None,
    ) -> Optional[str]:
        """Stream TCI band from CDSE S3 and cache as a local COG.

        Args:
            s3_path: Full S3 path to the product SAFE directory
                     (e.g., "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/01/01/S2A_....SAFE/")
            tile_code: MGRS tile code (e.g., "T34SFJ")
            date: Sensing date string (e.g., "20200101")
            tci_vsis3_path: Exact /vsis3/ path to the TCI file. If provided,
                            skips the wildcard-based path construction.

        Returns:
            Path to cached COG file, or None on failure.
        """
        cache_path = self._cache_path(tile_code, date)
        if cache_path.exists():
            logger.info(f"TCI cache hit: {cache_path}")
            return str(cache_path)

        try:
            import rasterio
            from rasterio.enums import Resampling

            # Prefer the exact path if provided; fall back to listing S3
            tci_vsis3 = tci_vsis3_path
            if not tci_vsis3:
                tci_vsis3 = self.find_tci_path_in_product(s3_path, tile_code)
            if not tci_vsis3:
                logger.error(f"Could not determine TCI path for {s3_path}")
                return None

            logger.info(f"Streaming TCI from {tci_vsis3}")

            with self._s3_env():
                with rasterio.open(tci_vsis3) as src:
                    # Read at a reduced overview level for fast initial display
                    # TCI is 10980x10980 at 10m; read at overview factor 4 → ~2745x2745
                    factor = self._choose_overview_factor(src)
                    out_height = max(1, src.height // factor)
                    out_width = max(1, src.width // factor)

                    data = src.read(
                        out_shape=(src.count, out_height, out_width),
                        resampling=Resampling.bilinear,
                    )

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

            # Write GeoTIFF — skip post-write overviews; the cached raster
            # is small enough (~1400x1400 at factor 8) that localtileserver
            # does not benefit from internal pyramids at typical zooms.
            with rasterio.open(str(cache_path), "w", **profile) as dst:
                dst.write(data)

            logger.info(f"TCI cached: {cache_path} ({out_width}x{out_height} px)")
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

    def _cache_path(self, tile_code: str, date: str) -> Path:
        """Compute cache file path for a given tile and date."""
        return self.cache_root / f"{tile_code}_{date}_tci.tif"

    def _build_tci_vsis3_path(self, s3_path: str, tile_code: str) -> Optional[str]:
        """Construct the /vsis3/ path to the TCI band within a product.

        The TCI band in L2A products lives at:
        .SAFE/GRANULE/L2A_.../IMG_DATA/R10m/T{tile}_..._TCI_10m.jp2
        """
        # Normalize path — strip s3:// prefix, leading slash, and bucket name
        path = s3_path
        if path.startswith("s3://"):
            path = path[5:].lstrip("/")
        else:
            path = path.lstrip("/")
        if path.startswith(f"{CDSE_S3_BUCKET}/"):
            path = path[len(CDSE_S3_BUCKET) + 1:]

        # Ensure trailing slash
        if not path.endswith("/"):
            path += "/"

        # Determine product level from path
        is_l2a = "L2A" in path

        if is_l2a:
            # L2A structure: .SAFE/GRANULE/<granule_id>/IMG_DATA/R10m/<tile>_<date>_TCI_10m.jp2
            # We need to construct the granule path — use a wildcard approach via GDAL
            # Since we can't list S3 easily, construct the expected path pattern
            # The granule ID matches: L2A_T{tile}_{sensing_time}
            vsis3_base = f"/vsis3/{CDSE_S3_BUCKET}/{path}"
            # TCI at 10m resolution in R10m subdirectory
            # Pattern: GRANULE/*/IMG_DATA/R10m/*_TCI_10m.jp2
            # We'll try the common structure
            return f"{vsis3_base}GRANULE/*/IMG_DATA/R10m/*_TCI_10m.jp2"
        else:
            # L1C: .SAFE/GRANULE/<granule_id>/IMG_DATA/<tile>_<date>_TCI.jp2
            vsis3_base = f"/vsis3/{CDSE_S3_BUCKET}/{path}"
            return f"{vsis3_base}GRANULE/*/IMG_DATA/*_TCI.jp2"

    def find_tci_path_in_product(self, s3_path: str, tile_code: str) -> Optional[str]:
        """List product contents on S3 to find exact TCI path.

        Falls back to constructing the path from the product name if listing fails.
        """
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
                prefix = prefix[len(CDSE_S3_BUCKET) + 1:]
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
                    tile_with_t = (
                        tile_code if tile_code.startswith("T") else f"T{tile_code}"
                    )
                    tci_key = (
                        f"{granule_prefix}IMG_DATA/R10m/"
                        f"{tile_with_t}_{product_datetime}_TCI_10m.jp2"
                    )
                    # Cheap HEAD verify (~50-150 ms) before returning so any
                    # unexpected layout falls back to the full LIST below
                    # rather than blowing up later in ``stream_tci`` with
                    # ``NoSuchKey``.
                    try:
                        s3_client.head_object(Bucket=CDSE_S3_BUCKET, Key=tci_key)
                        return f"/vsis3/{CDSE_S3_BUCKET}/{tci_key}"
                    except Exception:
                        logger.debug(
                            f"Constructed TCI path missing, falling back to "
                            f"LIST: {tci_key}"
                        )
                # If delimiter LIST returned nothing, fall through to full LIST.

            # Fallback: full prefix LIST (covers L1C and any unexpected layout)
            for page in paginator.paginate(Bucket=CDSE_S3_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if TCI_PATTERN_L2A.search(key) or TCI_PATTERN_L1C.search(key):
                        return f"/vsis3/{CDSE_S3_BUCKET}/{key}"

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
        """Build internal overviews for fast tile serving."""
        try:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path, "r+") as dst:
                dst.build_overviews([2, 4, 8, 16], Resampling.bilinear)
                dst.update_tags(ns="rio_overview", resampling="bilinear")
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
