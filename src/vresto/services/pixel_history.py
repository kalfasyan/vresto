"""Pixel history: the Sentinel-2 record of one point over a date range, without credentials.

For a WGS84 point this finds every Sentinel-2 L2A scene of one MGRS tile on AWS Open Data
(Element84 Earth Search) in the date range, then reads three pixels per scene from the
scene's cloud-optimised GeoTIFFs: red (B04), near infrared (B08) and the scene
classification (SCL). Only the block holding the pixel is fetched from each file, so a
year of scenes costs a few hundred small HTTP range reads rather than full downloads.

The result is one :class:`PixelObservation` per scene, carrying NDVI and whether the
point was clear, cloudy or snow-covered according to SCL. Each observation keeps the
scene as a :class:`~vresto.api.catalog.ProductInfo`, so the UI can stream that date's
true-colour image when the user picks it.

Example::

    from vresto.services.pixel_history import point_history, observations_to_csv

    series = point_history(50.85, 4.35, "2025-04-01", "2025-09-30")
    clear = [obs for obs in series if obs.clear]
    print(observations_to_csv(series))
"""

from __future__ import annotations

import csv
import io
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Sequence

import numpy as np
from loguru import logger

from vresto.api.catalog import ProductInfo
from vresto.services import public_s2

# Scenes read for one history. A year of one tile is ~70-150 scenes, so this covers about a year.
DEFAULT_MAX_SCENES = 150
# Concurrent scenes being read. Each one makes three small range reads.
READ_WORKERS = 8

# Earth Search asset keys, with the older band-named keys some items still use as a fallback.
RED_ASSET_KEYS = ("red", "B04")
NIR_ASSET_KEYS = ("nir", "B08")
SCL_ASSET_KEYS = ("scl", "SCL")
# Reflectance = DN * scale + offset, unless the asset's ``raster:bands`` says otherwise.
DEFAULT_REFLECTANCE_SCALE = 0.0001
DEFAULT_REFLECTANCE_OFFSET = 0.0

# Sen2Cor scene classification (SCL) codes grouped by what they mean for a point's value.
SCL_CLEAR = frozenset({2, 4, 5, 6, 7})  # dark area, vegetation, bare soil, water, unclassified
SCL_CLOUD = frozenset({8, 9, 10})  # cloud medium / high probability, thin cirrus
SCL_SHADOW = frozenset({3})  # cloud shadow
SCL_SNOW = frozenset({11})
SCL_NODATA = frozenset({0, 1})  # no data, saturated or defective

PointStatus = Literal["clear", "cloud", "shadow", "snow", "nodata", "error"]

CSV_COLUMNS = ("date", "datetime_utc", "tile", "status", "ndvi", "red", "nir", "scl", "scene_cloud_cover", "item_id")


@dataclass(frozen=True)
class PixelObservation:
    """What one Sentinel-2 scene saw at the point.

    Attributes:
        datetime: Sensing time (UTC).
        item_id: Earth Search item id of the scene.
        tile_code: MGRS tile the scene belongs to (e.g. ``"31UFS"``).
        status: ``"clear"`` when SCL marks the pixel as usable, ``"cloud"``, ``"shadow"`` or
            ``"snow"`` when it is contaminated, ``"nodata"`` when the scene has no valid
            pixel there, and ``"error"`` when the files could not be read.
        ndvi: (NIR - red) / (NIR + red) from surface reflectance; None when not computable.
        red: Surface reflectance of B04 (0..1).
        nir: Surface reflectance of B08 (0..1).
        scl: Raw scene-classification code at the pixel.
        scene_cloud_cover: Cloud cover of the whole scene in percent, as catalogued.
        product: The scene as a product, for streaming its true-colour image.
    """

    datetime: datetime
    item_id: str
    tile_code: str
    status: PointStatus
    ndvi: Optional[float] = None
    red: Optional[float] = None
    nir: Optional[float] = None
    scl: Optional[int] = None
    scene_cloud_cover: Optional[float] = None
    product: Optional[ProductInfo] = None

    @property
    def clear(self) -> bool:
        """True when the pixel was usable: clear according to SCL and with an NDVI value."""
        return self.status == "clear" and self.ndvi is not None

    @property
    def date(self) -> str:
        """Sensing day as ``YYYY-MM-DD``."""
        return self.datetime.strftime("%Y-%m-%d")


def classify_scl(code: Optional[int]) -> PointStatus:
    """Map a scene-classification code to the status of the point."""
    if code is None or code in SCL_NODATA:
        return "nodata"
    if code in SCL_CLOUD:
        return "cloud"
    if code in SCL_SHADOW:
        return "shadow"
    if code in SCL_SNOW:
        return "snow"
    if code in SCL_CLEAR:
        return "clear"
    return "nodata"


def compute_ndvi(red: Optional[float], nir: Optional[float]) -> Optional[float]:
    """NDVI from surface reflectance, or None when either band is missing or the sum is not positive."""
    if red is None or nir is None:
        return None
    total = nir + red
    if total <= 0:
        return None
    return max(-1.0, min(1.0, (nir - red) / total))


def search_point_scenes(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    tile_code: Optional[str] = None,
    max_cloud: float = 100.0,
    max_scenes: int = DEFAULT_MAX_SCENES,
) -> list[dict]:
    """Find the Earth Search Sentinel-2 L2A items covering a point, as plain STAC dicts.

    A point near a tile edge is covered by several overlapping MGRS tiles. Their pixels
    are not co-registered, so the series sticks to one: ``tile_code`` when given,
    otherwise the tile with the most scenes over the point.

    Returns:
        Items, oldest first. Empty when nothing matches or Earth Search cannot be reached.
    """
    query: dict[str, Any] = {"eo:cloud_cover": {"lte": max_cloud}}
    if tile_code:
        query["grid:code"] = {"eq": public_s2._grid_code(tile_code)}
    params = {
        "collections": [public_s2.EARTH_SEARCH_COLLECTION],
        "datetime": public_s2._datetime_range(start_date, end_date),
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "query": query,
    }
    try:
        items = public_s2._fetch_items(params, max_scenes)
    except Exception as exc:
        logger.warning(f"Earth Search point search failed at ({lat:.5f}, {lon:.5f}): {exc}")
        return []

    if not tile_code:
        tiles = Counter(_item_tile(item) for item in items)
        if len(tiles) > 1:
            chosen = tiles.most_common(1)[0][0]
            logger.info(f"Point is covered by tiles {sorted(tiles)}; using {chosen}")
            items = [item for item in items if _item_tile(item) == chosen]

    items.sort(key=lambda item: str((item.get("properties") or {}).get("datetime") or ""))
    return items[-max_scenes:]


def read_observation(item: dict, lat: float, lon: float) -> PixelObservation:
    """Read SCL, red and NIR at the point from one scene's COGs."""
    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    observation = {
        "datetime": _parse_datetime(props.get("datetime")),
        "item_id": str(item.get("id") or ""),
        "tile_code": _item_tile(item),
        "scene_cloud_cover": _float_or_none(props.get("eo:cloud_cover")),
        "product": public_s2._to_product(item),
    }

    try:
        scl_raw = _read_asset_pixel(assets, SCL_ASSET_KEYS, lat, lon)
        scl = None if scl_raw is None else int(scl_raw)
        status = classify_scl(scl)
        red = nir = None
        if status != "nodata":
            red = _reflectance(assets, RED_ASSET_KEYS, lat, lon)
            nir = _reflectance(assets, NIR_ASSET_KEYS, lat, lon)
    except Exception as exc:
        logger.warning(f"Could not read pixel of {observation['item_id']}: {exc}")
        return PixelObservation(status="error", **observation)

    return PixelObservation(status=status, ndvi=compute_ndvi(red, nir), red=red, nir=nir, scl=scl, **observation)


def point_history(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    tile_code: Optional[str] = None,
    max_cloud: float = 100.0,
    max_scenes: int = DEFAULT_MAX_SCENES,
) -> list[PixelObservation]:
    """Return the Sentinel-2 history of a point: one observation per scene, oldest first.

    Needs no credentials. Scene-level cloud cover is a poor guide for one pixel (a 90 %
    cloudy scene may still be clear over the point), so ``max_cloud`` defaults to keeping
    every scene and each observation's ``status`` says whether the pixel itself was clear.

    Args:
        lat: Latitude in degrees (EPSG:4326).
        lon: Longitude in degrees (EPSG:4326).
        start_date: First sensing day, ``YYYY-MM-DD``.
        end_date: Last sensing day, ``YYYY-MM-DD``.
        tile_code: MGRS tile to read (e.g. ``"31UFS"``). Picked automatically when omitted.
        max_cloud: Skip scenes whose catalogued cloud cover exceeds this, in percent.
        max_scenes: Most scenes to read; the newest are kept when there are more.
    """
    items = search_point_scenes(lat, lon, start_date, end_date, tile_code=tile_code, max_cloud=max_cloud, max_scenes=max_scenes)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=READ_WORKERS, thread_name_prefix="pixel-history") as pool:
        observations = list(pool.map(lambda item: read_observation(item, lat, lon), items))
    clear = sum(obs.clear for obs in observations)
    logger.info(f"Pixel history at ({lat:.5f}, {lon:.5f}): {len(observations)} scenes, {clear} clear")
    return observations


def observations_to_rows(observations: Sequence[PixelObservation]) -> list[dict]:
    """Flatten observations into dicts keyed by :data:`CSV_COLUMNS`."""
    return [
        {
            "date": obs.date,
            "datetime_utc": obs.datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tile": obs.tile_code,
            "status": obs.status,
            "ndvi": _round(obs.ndvi),
            "red": _round(obs.red),
            "nir": _round(obs.nir),
            "scl": obs.scl,
            "scene_cloud_cover": obs.scene_cloud_cover,
            "item_id": obs.item_id,
        }
        for obs in observations
    ]


def observations_to_csv(observations: Sequence[PixelObservation]) -> str:
    """Render observations as CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(observations_to_rows(observations))
    return buffer.getvalue()


# ── Internals ────────────────────────────────────────────────────────────────


def _item_tile(item: dict) -> str:
    """MGRS code of an item (``31UFS``), from ``grid:code`` (``MGRS-31UFS``) or the MGRS properties."""
    props = item.get("properties") or {}
    code = str(props.get("grid:code") or "")
    if code.upper().startswith("MGRS-"):
        return code[5:].upper()
    parts = (props.get("mgrs:utm_zone"), props.get("mgrs:latitude_band"), props.get("mgrs:grid_square"))
    if all(part is not None for part in parts):
        return f"{int(parts[0]):02d}{parts[1]}{parts[2]}".upper()
    return code.upper()


def _parse_datetime(value: Any) -> datetime:
    """Parse a STAC datetime string into an aware UTC datetime (epoch when missing or malformed)."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def _find_asset(assets: dict, keys: Sequence[str]) -> Optional[dict]:
    for key in keys:
        asset = assets.get(key)
        if isinstance(asset, dict) and asset.get("href"):
            return asset
    return None


def _gdal_path(href: str) -> str:
    """GDAL path for an asset href: ``/vsicurl/`` for remote files, the path itself for local ones."""
    if href.startswith(("https://", "s3://")):
        return public_s2._vsicurl_url(href)
    if os.path.exists(href):
        return href
    raise ValueError(f"Unsupported asset href: {href!r}")


def _read_asset_pixel(assets: dict, keys: Sequence[str], lat: float, lon: float) -> Optional[float]:
    """Raw value of the first matching asset at the point; None when missing, outside or nodata."""
    asset = _find_asset(assets, keys)
    if asset is None:
        return None

    import rasterio

    from vresto.services.sampling import read_pixel

    with rasterio.Env(**public_s2._PUBLIC_GDAL_OPTIONS), rasterio.open(_gdal_path(asset["href"])) as src:
        pixel = read_pixel(src, lat, lon, masked=True)
    if pixel is None or np.ma.getmaskarray(pixel)[0, 0]:
        return None
    return float(pixel[0, 0])


def _reflectance(assets: dict, keys: Sequence[str], lat: float, lon: float) -> Optional[float]:
    """Surface reflectance of a band at the point, using the asset's own scale and offset."""
    raw = _read_asset_pixel(assets, keys, lat, lon)
    if raw is None or raw <= 0:  # 0 is the L2A no-data value
        return None
    scale, offset = DEFAULT_REFLECTANCE_SCALE, DEFAULT_REFLECTANCE_OFFSET
    bands = (_find_asset(assets, keys) or {}).get("raster:bands")
    if isinstance(bands, list) and bands and isinstance(bands[0], dict):
        scale = _float_or_none(bands[0].get("scale")) or scale
        band_offset = _float_or_none(bands[0].get("offset"))
        offset = offset if band_offset is None else band_offset
    return raw * scale + offset
