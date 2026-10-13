"""Sentinel-2 true-colour previews from AWS Open Data, without CDSE credentials.

Element84 Earth Search publishes Sentinel-2 L2A scenes as cloud-optimised GeoTIFFs on
AWS Open Data. Searching it and reading those files needs no account, so tile streaming
falls back to it when no CDSE S3 keys are configured. Its imagery can lag CDSE by hours
to about a day, and its coverage can differ slightly, so CDSE stays the authoritative source.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from loguru import logger

from vresto.api.catalog import ProductInfo
from vresto.api.config import CopernicusConfig
from vresto.services.sentinel_stream import (
    _GDAL_TUNING_DEFAULTS,
    DEFAULT_TCI_RESOLUTION,
    TciResolution,
    build_overviews,
)

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
EARTH_SEARCH_COLLECTION = "sentinel-2-c1-l2a"
# Value of ``ProductInfo.source`` for products found on Earth Search.
PUBLIC_SOURCE = "earth-search"
PUBLIC_ATTRIBUTION = "Imagery via AWS Open Data (Element84 Earth Search)"

# Environment variable that picks the streaming source: ``auto`` (default), ``cdse`` or ``public``.
STREAM_SOURCE_ENV = "VRESTO_STREAM_SOURCE"
StreamSource = Literal["cdse", "public"]

# Seconds before a silent server is given up on, so a stalled search cannot hold a worker thread forever.
REQUEST_TIMEOUT_S = 30.0
# Newest scenes first, as the server sorts them when it accepts ``sortby``.
_NEWEST_FIRST = [{"field": "properties.datetime", "direction": "desc"}]
# Window fetched when the server rejects ``sortby``. The caller then sorts it and keeps the newest.
_UNSORTED_MAX_ITEMS = 200
# Pixel size of the visual COG is 10 m; a request for a coarser resolution reads a matching overview.
_RESOLUTION_METRES = {"60m": 60.0, "20m": 20.0, "10m": 10.0}
# GDAL settings for /vsicurl/ reads. rasterio.Env sets them for the whole process, so each value
# must match the CDSE tuning defaults in sentinel_stream: a CDSE read in another thread would
# otherwise see them too. AWS_NO_SIGN_REQUEST is left out on purpose. It only affects /vsis3/,
# and setting it here would override the unsigned-request setting that the CDSE path relies on.
_PUBLIC_GDAL_OPTIONS = {
    key: _GDAL_TUNING_DEFAULTS[key]
    for key in (
        "GDAL_DISABLE_READDIR_ON_OPEN",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS",
        "GDAL_HTTP_MULTIPLEX",
        "VSI_CACHE",
    )
}

_client: Any = None
_client_lock = threading.Lock()


def _get_client() -> Any:
    """Return the shared anonymous Earth Search client, opening it on first use.

    The client is created once per process. A failed open is not remembered, so the next call tries again.
    """
    global _client
    with _client_lock:
        if _client is None:
            from pystac_client import Client

            _client = Client.open(EARTH_SEARCH_URL, timeout=REQUEST_TIMEOUT_S)
        return _client


def _grid_code(tile_code: str) -> str:
    """Return the Earth Search ``grid:code`` for a tile: ``MGRS-31UFS`` for ``31UFS`` or ``T31UFS``."""
    code = tile_code.strip().upper()
    if code.startswith("T"):
        code = code[1:]
    return f"MGRS-{code}"


def _datetime_range(start_date: str, end_date: str) -> str:
    """Build the STAC ``datetime`` interval, widening plain ``YYYY-MM-DD`` dates to whole UTC days."""
    start = f"{start_date}T00:00:00Z" if len(start_date) == 10 else start_date
    end = f"{end_date}T23:59:59Z" if len(end_date) == 10 else end_date
    return f"{start}/{end}"


def _is_rejected(exc: Exception) -> bool:
    """True when the server answered with a 4xx status, which is how it refuses a request parameter."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


def _fetch_items(params: dict[str, Any], limit: int) -> list[dict]:
    """Return up to ``limit`` items as plain dicts, newest first when the server can sort them.

    If the server rejects ``sortby``, the search is repeated without it over a wider window,
    and the caller sorts the result. Other failures, such as a timeout, are raised as they are.
    """
    client = _get_client()
    try:
        return list(client.search(sortby=_NEWEST_FIRST, max_items=limit, **params).items_as_dicts())
    except Exception as exc:
        if not _is_rejected(exc):
            raise
        logger.debug(f"Earth Search rejected sortby ({exc}); sorting client side")
    return list(client.search(max_items=_UNSORTED_MAX_ITEMS, **params).items_as_dicts())


def _sensing_date(value: Any) -> str:
    """Format a STAC datetime as ``YYYY-MM-DD HH:MM:SS`` in UTC, the form the CDSE search produces."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _asset_href(assets: dict, key: str) -> Optional[str]:
    """Return the href of one STAC asset, or None when the item lacks it."""
    asset = assets.get(key)
    if not isinstance(asset, dict):
        return None
    href = asset.get("href")
    return href if isinstance(href, str) and href else None


def _to_product(item: dict) -> Optional[ProductInfo]:
    """Map one STAC item to a ProductInfo, or None when it has no visual asset to stream."""
    assets = item.get("assets") or {}
    visual_href = _asset_href(assets, "visual")
    if visual_href is None:
        return None
    props = item.get("properties") or {}
    item_id = str(item.get("id") or "")
    cloud = props.get("eo:cloud_cover")
    cloud_cover = None if isinstance(cloud, bool) or not isinstance(cloud, (int, float)) else float(cloud)
    return ProductInfo(
        id=item_id,
        name=str(props.get("s2:product_uri") or item_id),
        collection="SENTINEL-2",
        sensing_date=_sensing_date(props.get("datetime")),
        size_mb=0,
        s3_path=visual_href,
        cloud_cover=cloud_cover,
        source=PUBLIC_SOURCE,
        thumbnail_url=_asset_href(assets, "thumbnail"),
    )


def search_tile(
    tile_code: str,
    start_date: str,
    end_date: str,
    limit: int = 5,
    max_cloud: float = 50.0,
) -> list[ProductInfo]:
    """Find the newest Sentinel-2 L2A scenes of an MGRS tile on AWS Open Data.

    Makes one anonymous search, or two if the server rejects ``sortby``. Items without a
    visual asset are skipped.

    Args:
        tile_code: MGRS tile, with or without the leading ``T`` (``31UFS`` or ``T31UFS``).
        start_date: First sensing day, ``YYYY-MM-DD``.
        end_date: Last sensing day, ``YYYY-MM-DD``.
        limit: Most products to return.
        max_cloud: Highest cloud cover, in percent, that a product may have.

    Returns:
        Products, newest first. Empty when nothing matches or Earth Search cannot be reached.
    """
    if limit <= 0:
        return []
    params = {
        "collections": [EARTH_SEARCH_COLLECTION],
        "datetime": _datetime_range(start_date, end_date),
        "query": {"grid:code": {"eq": _grid_code(tile_code)}, "eo:cloud_cover": {"lte": max_cloud}},
    }
    try:
        items = _fetch_items(params, limit)
    except Exception as exc:
        logger.warning(f"Earth Search search failed for tile {tile_code}: {exc}")
        return []

    products = [product for product in (_to_product(item) for item in items) if product is not None]
    products.sort(key=lambda product: product.sensing_date, reverse=True)
    return products[:limit]


def stream_source() -> StreamSource:
    """Return where tile streaming reads from. The answer is decided on each call.

    ``VRESTO_STREAM_SOURCE`` may be ``cdse`` or ``public``. ``auto``, the default, streams from
    CDSE when static S3 keys are configured and from AWS Open Data otherwise. An unrecognised
    value is treated as ``auto``.
    """
    requested = (os.getenv(STREAM_SOURCE_ENV) or "auto").strip().lower()
    if requested == "cdse":
        return "cdse"
    if requested == "public":
        return "public"
    if requested != "auto":
        logger.warning(f"Unknown {STREAM_SOURCE_ENV}={requested!r}; using auto")
    return "cdse" if CopernicusConfig().has_static_s3_credentials() else "public"


def _vsicurl_url(href: str) -> str:
    """Return the GDAL ``/vsicurl/`` path for an asset href.

    ``s3://bucket/key`` becomes its virtual-hosted HTTPS form, and other schemes are refused.

    Raises:
        ValueError: If the href is neither ``https://`` nor a well-formed ``s3://`` URL.
    """
    if href.startswith("s3://"):
        bucket, _, key = href[len("s3://") :].partition("/")
        if not bucket or not key:
            raise ValueError(f"Malformed S3 href: {href!r}")
        href = f"https://{bucket}.s3.amazonaws.com/{key}"
    if not href.startswith("https://"):
        raise ValueError(f"Unsupported asset href: {href!r}")
    return f"/vsicurl/{href}"


def stream_visual_cog(
    href: str,
    cache_path: Path,
    resolution: TciResolution = DEFAULT_TCI_RESOLUTION,
) -> Optional[str]:
    """Write a product's visual COG to ``cache_path`` at the requested resolution.

    The source is 10 m. Reading it at a coarser size lets GDAL serve a matching internal
    overview, so the 60 m read touches far fewer pixels than the 10 m one. The GeoTIFF is
    written to a temporary name beside ``cache_path`` and renamed only once it is complete,
    so a failed stream never leaves a partial cache file.

    Args:
        href: ``https://`` or ``s3://`` href of the visual asset (the TCI COG).
        cache_path: Destination file, normally from ``SentinelStreamService._cache_path``.
        resolution: ``"60m"`` (default), ``"20m"`` or ``"10m"``.

    Returns:
        ``str(cache_path)`` on success, or None on failure.
    """
    target_m = _RESOLUTION_METRES.get(resolution)
    if target_m is None:
        logger.error(f"Unsupported TCI resolution {resolution!r}")
        return None

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine

    tmp_path = cache_path.with_name(f"{cache_path.stem}.{uuid.uuid4().hex}.part.tif")
    t0 = time.perf_counter()
    try:
        with rasterio.Env(**_PUBLIC_GDAL_OPTIONS), rasterio.open(_vsicurl_url(href)) as src:
            factor = max(1, round(target_m / src.res[0]))
            out_width = max(1, round(src.width / factor))
            out_height = max(1, round(src.height / factor))
            data = src.read(out_shape=(src.count, out_height, out_width), resampling=Resampling.average)
            transform = src.transform * Affine.scale(src.width / out_width, src.height / out_height)
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
            if src.nodata is not None:
                profile["nodata"] = src.nodata

        with rasterio.open(str(tmp_path), "w", **profile) as dst:
            dst.write(data)
        build_overviews(str(tmp_path))
        os.replace(tmp_path, cache_path)
    except Exception as exc:
        logger.error(f"Failed to stream public TCI from {href}: {exc}")
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        return None

    logger.info(f"[perf] public TCI cached: {cache_path} ({out_width}x{out_height} px, {resolution}) [total {(time.perf_counter() - t0) * 1000:.0f} ms]")
    return str(cache_path)
