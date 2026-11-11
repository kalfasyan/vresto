"""Process-wide GDAL settings for reading Sentinel-2 rasters over S3/HTTP.

Shared by the streaming service, the public Earth Search reader and the
overlay services.
"""

from __future__ import annotations

import contextlib
import os
import threading
from typing import Optional

from loguru import logger


def build_overviews(path: str) -> None:
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
