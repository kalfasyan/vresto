"""Dry Matter Productivity (DMP) overlay utilities.

Uses the CLMS DMP 300 m 10-daily collection and renders the ``dmp300_dmp``
asset (the v1 collection is used because the v2 COGs are not present on the
eodata bucket). Asset discovery is done through CDSE STAC so the overlay snaps
to the nearest available 10-daily product to the streamed Sentinel acquisition
date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import OverlayResult, RampOverlayService

DMP_COLLECTION_ID = "clms_dmp_global_300m_10daily_v1_cog"
DMP_ASSET_KEY = "dmp300_dmp"
DMP_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
DMP_RAW_NODATA = -1
DMP_NODATA = -9999.0
DMP_SCALE = 0.01
DMP_OFFSET = 0.0
# Physical units are kg / ha / day. A fixed 0..150 ramp keeps colours stable
# across tiles; productive vegetation peaks well below this in most regions.
DMP_MIN_VALUE = 0.0
DMP_MAX_VALUE = 150.0

DMP_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 229, "0"),
    (1, 247, 252, 185, "25"),
    (2, 217, 240, 163, "50"),
    (3, 173, 221, 142, "75"),
    (4, 120, 198, 121, "100"),
    (5, 49, 163, 84, "125"),
    (6, 0, 104, 55, "150 kg/ha/day"),
]


# Kept for callers that import the per-product name.
DMPOverlayResult = OverlayResult


def raw_dmp_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw DMP DNs to physical DMP values using the product scale."""
    return raw.astype("float32") * DMP_SCALE + DMP_OFFSET


class DMPService(RampOverlayService):
    """Fetch and align 10-daily DMP data for Sentinel overlays via GDAL vsis3."""

    name = "dmp"
    label = "DMP"
    collection_id = DMP_COLLECTION_ID
    asset_key = DMP_ASSET_KEY
    s3_endpoint = DMP_S3_ENDPOINT
    search_window_days = 10
    max_items = 8
    aligned_nodata = DMP_NODATA
    read_resampling = "bilinear"
    colormap = "YlGn"
    vmin = DMP_MIN_VALUE
    vmax = DMP_MAX_VALUE

    get_aligned_dmp_result = RampOverlayService.get_aligned_result
    get_aligned_dmp_path = RampOverlayService.get_aligned_path
    get_colorized_dmp_result = RampOverlayService.get_colorized_result
    get_colorized_dmp_path = RampOverlayService.get_colorized_path

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        data = data.astype("float32")
        # Flags (-1 Missing, -2 Sea) and nodata are all negative; the
        # valid physical range starts at 0.
        valid = data >= 0
        return np.where(valid, raw_dmp_to_physical(data), DMP_NODATA).astype("float32")


dmp_service = DMPService()
