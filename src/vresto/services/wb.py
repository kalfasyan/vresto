"""Water Bodies (WB) overlay utilities.

Uses the CLMS Water Bodies 100 m monthly collection and renders the
``wb100_wb`` asset. The product is categorical: pixels are flagged as Sea (0),
Water (70), No-data (251) or No-water (255). Only Sea and Water are coloured;
everything else is rendered transparent. Asset discovery is done through CDSE
STAC so the overlay snaps to the nearest available monthly product to the
streamed Sentinel acquisition date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import ClmsStacOverlayService, OverlayResult, paint_classes

WB_COLLECTION_ID = "clms_wb_global_100m_monthly_v1_cog"
WB_ASSET_KEY = "wb100_wb"
WB_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
WB_RAW_NODATA = 251
WB_NO_WATER = 255

# Categorical classes that get a colour. Sea (0) and Water (70) are the only
# values rendered; No-data (251) and No-water (255) stay transparent.
WB_CLASS_LEGENDS: List[Tuple[int, int, int, int, str]] = [
    (70, 31, 120, 180, "Water"),
    (0, 8, 48, 107, "Sea"),
]

WB_CLASSES: dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in WB_CLASS_LEGENDS}


# Kept for callers that import the per-product name.
WBOverlayResult = OverlayResult


class WBService(ClmsStacOverlayService):
    """Fetch and align monthly Water Bodies data for Sentinel overlays via GDAL vsis3."""

    name = "wb"
    label = "Water Bodies"
    collection_id = WB_COLLECTION_ID
    asset_key = WB_ASSET_KEY
    s3_endpoint = WB_S3_ENDPOINT
    search_window_days = 31
    max_items = 6
    aligned_dtype = "uint8"
    aligned_nodata = WB_RAW_NODATA
    align_resampling = "nearest"
    overview_resampling = "nearest"

    get_aligned_wb_result = ClmsStacOverlayService.get_aligned_result
    get_aligned_wb_path = ClmsStacOverlayService.get_aligned_path
    get_colorized_wb_result = ClmsStacOverlayService.get_colorized_result
    get_colorized_wb_path = ClmsStacOverlayService.get_colorized_path

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        return data

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, ...]:
        r, g, b, matched = paint_classes(values, WB_CLASSES)
        return r, g, b, np.where(matched, 255, 0).astype(np.uint8)


wb_service = WBService()
