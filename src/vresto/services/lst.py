"""Hourly Land Surface Temperature overlay utilities.

Uses the CLMS hourly LST collection and renders the ``lst_lst`` asset, which
carries the actual land-surface temperature values. Asset discovery is done
through CDSE STAC so the overlay can snap to the nearest available hourly
product to the streamed Sentinel acquisition timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Tuple
from zoneinfo import ZoneInfo

import numpy as np
from loguru import logger

from vresto.api.stac_assets import find_stac_assets
from vresto.services.overlay_base import OverlayResult, RampOverlayService

LST_COLLECTION_ID = "clms_lst_global_3km_hourly_v3_cog"
LST_ASSET_KEY = "lst_lst"
LST_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
LST_RAW_NODATA = -9999.0
LST_CELSIUS_NODATA = -9999.0
LST_SCALE = 0.01
LST_C_MIN = -20.0
LST_C_MAX = 50.0
LST_DISPLAY_TIMEZONE = ZoneInfo("Europe/Brussels")

LST_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 49, 54, 149, "<-10 C"),
    (1, 69, 117, 180, "-10 to 0 C"),
    (2, 116, 173, 209, "0 to 10 C"),
    (3, 254, 224, 144, "10 to 20 C"),
    (4, 253, 174, 97, "20 to 30 C"),
    (5, 215, 48, 39, ">30 C"),
]


# Kept for callers that import the per-product name.
LSTOverlayResult = OverlayResult


def raw_lst_to_celsius(raw: np.ndarray) -> np.ndarray:
    """Convert raw LST median pixel values to Celsius using the product scale."""
    return raw.astype("float32") * LST_SCALE


def format_lst_selected_datetime(selected_datetime: datetime) -> str:
    """Format the selected hourly LST timestamp for UI display."""
    if selected_datetime.tzinfo is None:
        selected_datetime = selected_datetime.replace(tzinfo=timezone.utc)
    return selected_datetime.astimezone(LST_DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


class LSTService(RampOverlayService):
    """Fetch and align hourly LST data for Sentinel overlays via GDAL vsis3."""

    name = "lst"
    label = "LST"
    key_prefix = "lst_hourly"
    collection_id = LST_COLLECTION_ID
    asset_key = LST_ASSET_KEY
    s3_endpoint = LST_S3_ENDPOINT
    search_window_days = 1
    max_items = 48
    aligned_nodata = LST_CELSIUS_NODATA
    read_resampling = "bilinear"
    colormap = "RdYlBu_r"
    vmin = LST_C_MIN
    vmax = LST_C_MAX

    get_aligned_lst_result = RampOverlayService.get_aligned_result
    get_aligned_lst_path = RampOverlayService.get_aligned_path
    get_colorized_lst_result = RampOverlayService.get_colorized_result
    get_colorized_lst_path = RampOverlayService.get_colorized_path

    def list_available_lst_datetimes(self, reference_raster: str, date: str) -> list[datetime]:
        """List hourly LST scene timestamps near the requested acquisition time."""
        try:
            import rasterio
            from rasterio.warp import transform_bounds
        except Exception as exc:
            logger.error(f"rasterio required for LST timestamp listing: {exc}")
            return []

        try:
            with rasterio.open(reference_raster) as ref:
                left, bottom, right, top = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
        except Exception as exc:
            logger.error(f"Could not read LST reference raster {reference_raster}: {exc}")
            return []

        matches = find_stac_assets(
            LST_COLLECTION_ID,
            [left, bottom, right, top],
            date,
            LST_ASSET_KEY,
            search_window=timedelta(hours=12),
            max_items=24,
        )
        return [match.item_datetime for match in matches]

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        data = data.astype("float32")
        valid = data != np.float32(src.nodata if src.nodata is not None else LST_RAW_NODATA)
        return np.where(valid, raw_lst_to_celsius(data), LST_CELSIUS_NODATA).astype("float32")


lst_service = LSTService()
