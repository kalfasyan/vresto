"""Burned Area (BA) overlay utilities.

Uses the CLMS Burned Area 300 m monthly collection and renders the
``ba300_dob_ntc`` (day-of-burn) asset. Burned pixels carry the day-of-year of
the detected burn (1..366); unburned pixels (0), flags (Too few observations,
Water) and no-data are rendered transparent. Asset discovery is done through
CDSE STAC so the overlay snaps to the nearest available monthly product to the
streamed Sentinel acquisition date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import OverlayResult, RampOverlayService

BA_COLLECTION_ID = "clms_ba_global_300m_monthly_v4_cog"
BA_ASSET_KEY = "ba300_dob_ntc"
BA_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
BA_RAW_NODATA = -32768
BA_NODATA = -9999.0
# Day-of-burn is encoded directly as the day-of-year of detection.
BA_MIN_VALUE = 1.0
BA_MAX_VALUE = 366.0

# Day-of-year ranges mapped onto a YlOrRd ramp so the overlay reads as a
# burn-recency map within the year.
BA_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 178, "Jan-Feb"),
    (1, 254, 217, 118, "Mar-Apr"),
    (2, 254, 178, 76, "May-Jun"),
    (3, 253, 141, 60, "Jul-Aug"),
    (4, 240, 59, 32, "Sep-Oct"),
    (5, 189, 0, 38, "Nov-Dec"),
]


# Kept for callers that import the per-product name.
BAOverlayResult = OverlayResult


class BAService(RampOverlayService):
    """Fetch and align monthly Burned Area data for Sentinel overlays via GDAL vsis3."""

    name = "ba"
    label = "Burned Area"
    collection_id = BA_COLLECTION_ID
    asset_key = BA_ASSET_KEY
    s3_endpoint = BA_S3_ENDPOINT
    search_window_days = 31
    max_items = 6
    aligned_nodata = BA_NODATA
    align_resampling = "nearest"
    overview_resampling = "nearest"
    colormap = "YlOrRd"
    vmin = BA_MIN_VALUE
    vmax = BA_MAX_VALUE

    get_aligned_ba_result = RampOverlayService.get_aligned_result
    get_aligned_ba_path = RampOverlayService.get_aligned_path
    get_colorized_ba_result = RampOverlayService.get_colorized_result
    get_colorized_ba_path = RampOverlayService.get_colorized_path

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        # Nearest preserves the day-of-burn codes; only true burns
        # (1..366) are kept, everything else becomes transparent.
        burned = (data >= int(BA_MIN_VALUE)) & (data <= int(BA_MAX_VALUE))
        return np.where(burned, data.astype("float32"), BA_NODATA).astype("float32")


ba_service = BAService()
