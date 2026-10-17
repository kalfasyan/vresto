"""Soil Water Index (SWI) overlay utilities.

Uses the CLMS SWI 12.5 km daily global collection and renders the
``swi_swi010`` asset (Soil Water Index with characteristic time length T = 10,
a common root-zone moisture proxy). Asset discovery is done through CDSE STAC
so the overlay snaps to the nearest available daily product to the streamed
Sentinel acquisition date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import OverlayResult, RampOverlayService

SWI_COLLECTION_ID = "clms_swi_global_12.5km_daily_v4_cog"
SWI_ASSET_KEY = "swi_swi010"
SWI_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
SWI_RAW_NODATA = 255
SWI_NODATA = -9999.0
SWI_SCALE = 0.5
SWI_OFFSET = 0.0
# Raw DNs 0..200 carry valid SWI percentages; 255 is no-data.
SWI_VALID_MAX_DN = 200
SWI_MIN_VALUE = 0.0
SWI_MAX_VALUE = 100.0

SWI_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 217, "0%"),
    (1, 199, 233, 180, "20%"),
    (2, 127, 205, 187, "40%"),
    (3, 65, 182, 196, "60%"),
    (4, 34, 94, 168, "80%"),
    (5, 12, 44, 132, "100%"),
]


# Kept for callers that import the per-product name.
SWIOverlayResult = OverlayResult


def raw_swi_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw SWI DNs to percentage using the product scale."""
    return raw.astype("float32") * SWI_SCALE + SWI_OFFSET


class SWIService(RampOverlayService):
    """Fetch and align daily SWI data for Sentinel overlays via GDAL vsis3."""

    name = "swi"
    label = "SWI"
    collection_id = SWI_COLLECTION_ID
    asset_key = SWI_ASSET_KEY
    s3_endpoint = SWI_S3_ENDPOINT
    search_window_days = 5
    max_items = 10
    aligned_nodata = SWI_NODATA
    colormap = "YlGnBu"
    vmin = SWI_MIN_VALUE
    vmax = SWI_MAX_VALUE

    get_aligned_swi_result = RampOverlayService.get_aligned_result
    get_aligned_swi_path = RampOverlayService.get_aligned_path
    get_colorized_swi_result = RampOverlayService.get_colorized_result
    get_colorized_swi_path = RampOverlayService.get_colorized_path

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        # Nearest keeps the no-data DN intact so it can be masked before scaling.
        valid = data <= SWI_VALID_MAX_DN
        return np.where(valid, raw_swi_to_physical(data), SWI_NODATA).astype("float32")


swi_service = SWIService()
