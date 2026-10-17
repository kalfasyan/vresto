"""Surface Soil Moisture (SSM) overlay utilities.

Uses the CLMS SSM 1 km daily collection (Europe only) and renders the
``ssm1km_ssm`` asset. Asset discovery is done through CDSE STAC so the overlay
snaps to the nearest available daily product to the streamed Sentinel
acquisition date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import OverlayResult, RampOverlayService

SSM_COLLECTION_ID = "clms_ssm_europe_1km_daily_v1_cog"
SSM_ASSET_KEY = "ssm1km_ssm"
SSM_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
SSM_RAW_NODATA = 255
SSM_NODATA = -9999.0
SSM_SCALE = 0.5
SSM_OFFSET = 0.0
# Raw DNs 0..200 carry valid degree-of-saturation percentages; 241..253 are
# flags (exceeding min/max, water/sensitivity/slope masks) and 255 is no-data.
SSM_VALID_MAX_DN = 200
SSM_MIN_VALUE = 0.0
SSM_MAX_VALUE = 100.0

# SSM is a European product; gate the overlay to the published grid extent.
SSM_MIN_LON = -11.0
SSM_MAX_LON = 50.0
SSM_MIN_LAT = 35.0
SSM_MAX_LAT = 72.0

SSM_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 255, 255, 217, "0%"),
    (1, 199, 233, 180, "20%"),
    (2, 127, 205, 187, "40%"),
    (3, 65, 182, 196, "60%"),
    (4, 34, 94, 168, "80%"),
    (5, 12, 44, 132, "100% sat."),
]


def ssm_has_coverage(left: float, bottom: float, right: float, top: float) -> bool:
    """Return whether a bbox intersects the European SSM coverage extent."""
    return right > SSM_MIN_LON and left < SSM_MAX_LON and top > SSM_MIN_LAT and bottom < SSM_MAX_LAT


# Kept for callers that import the per-product name.
SSMOverlayResult = OverlayResult


def raw_ssm_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw SSM DNs to degree-of-saturation percentage using the scale."""
    return raw.astype("float32") * SSM_SCALE + SSM_OFFSET


class SSMService(RampOverlayService):
    """Fetch and align daily SSM data for Sentinel overlays via GDAL vsis3."""

    name = "ssm"
    label = "SSM"
    collection_id = SSM_COLLECTION_ID
    asset_key = SSM_ASSET_KEY
    s3_endpoint = SSM_S3_ENDPOINT
    search_window_days = 7
    max_items = 14
    aligned_nodata = SSM_NODATA
    colormap = "YlGnBu"
    vmin = SSM_MIN_VALUE
    vmax = SSM_MAX_VALUE

    get_aligned_ssm_result = RampOverlayService.get_aligned_result
    get_aligned_ssm_path = RampOverlayService.get_aligned_path
    get_colorized_ssm_result = RampOverlayService.get_colorized_result
    get_colorized_ssm_path = RampOverlayService.get_colorized_path

    def _covers(self, left: float, bottom: float, right: float, top: float) -> bool:
        return ssm_has_coverage(left, bottom, right, top)

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        # Nearest keeps flag DNs intact so they can be masked before scaling.
        valid = data <= SSM_VALID_MAX_DN
        return np.where(valid, raw_ssm_to_physical(data), SSM_NODATA).astype("float32")


ssm_service = SSMService()
