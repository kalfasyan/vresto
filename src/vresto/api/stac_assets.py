"""Focused helpers for resolving raster asset hrefs from CDSE STAC collections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable, Optional, Sequence

from loguru import logger

from vresto.api.config import CopernicusConfig


@dataclass(frozen=True)
class STACAssetMatch:
    """Resolved STAC asset selection for a target date and collection."""

    item_id: str
    item_datetime: datetime
    asset_key: str
    href: str


def parse_date_like(date_str: str) -> datetime:
    """Parse compact or dashed dates/datetimes into a UTC datetime."""
    digits = "".join(char for char in str(date_str or "") if char.isdigit())
    if len(digits) >= 14:
        parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    elif len(digits) >= 12:
        parsed = datetime.strptime(digits[:12], "%Y%m%d%H%M")
    elif len(digits) >= 8:
        parsed = datetime.strptime(digits[:8], "%Y%m%d")
    else:
        raise ValueError(f"Unsupported date format: {date_str!r}")
    return parsed.replace(tzinfo=timezone.utc)


def normalize_stac_href_to_vsis3(href: str) -> str:
    """Normalize STAC raster hrefs to a GDAL-readable form."""
    if href.startswith("/vsis3/"):
        return href
    if href.startswith("s3://"):
        return f"/vsis3/{href[5:]}"
    if href.startswith("s3://https://"):
        return href[5:]
    return href


def select_nearest_stac_item(items: Iterable, target_dt: datetime):
    """Return the item whose datetime is closest to the target date."""
    nearest = None
    nearest_delta = None
    for item in items:
        item_dt = getattr(item, "datetime", None)
        if item_dt is None:
            continue
        if item_dt.tzinfo is None:
            item_dt = item_dt.replace(tzinfo=timezone.utc)
        delta = abs(item_dt - target_dt)
        if nearest is None or delta < nearest_delta:
            nearest = item
            nearest_delta = delta
    return nearest


def find_stac_assets(
    collection_id: str,
    bbox: Sequence[float],
    date_str: str,
    asset_key: str,
    *,
    search_window: timedelta,
    max_items: int = 12,
) -> list[STACAssetMatch]:
    """Resolve all matching STAC assets within a search window."""
    target_dt = parse_date_like(date_str)
    start_dt = target_dt - search_window
    end_dt = target_dt + search_window
    datetime_range = f"{start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    try:
        search = _get_client().search(
            collections=[collection_id],
            bbox=list(bbox),
            datetime=datetime_range,
            max_items=max_items,
        )
        items = list(search.items())
    except Exception as exc:
        logger.warning(f"STAC asset search failed for {collection_id}: {exc}")
        return []

    matches: list[STACAssetMatch] = []
    for item in items:
        asset = item.assets.get(asset_key)
        if not asset:
            continue

        item_dt = item.datetime
        if item_dt is None:
            continue
        if item_dt.tzinfo is None:
            item_dt = item_dt.replace(tzinfo=timezone.utc)

        matches.append(
            STACAssetMatch(
                item_id=item.id,
                item_datetime=item_dt,
                asset_key=asset_key,
                href=normalize_stac_href_to_vsis3(asset.href),
            )
        )

    matches.sort(key=lambda match: match.item_datetime)
    return matches


@lru_cache(maxsize=1)
def _get_client():
    from pystac_client import Client

    return Client.open(CopernicusConfig().STAC_BASE_URL)


def find_closest_stac_asset(
    collection_id: str,
    bbox: Sequence[float],
    date_str: str,
    asset_key: str,
    *,
    search_window_days: int = 15,
    max_items: int = 12,
) -> Optional[STACAssetMatch]:
    """Resolve the nearest STAC asset for a date, collection, and bbox."""
    target_dt = parse_date_like(date_str)
    matches = find_stac_assets(
        collection_id,
        bbox,
        date_str,
        asset_key,
        search_window=timedelta(days=search_window_days),
        max_items=max_items,
    )
    if not matches:
        logger.info(f"No STAC items found for {collection_id} near {date_str}")
        return None

    return min(matches, key=lambda match: abs(match.item_datetime - target_dt))
