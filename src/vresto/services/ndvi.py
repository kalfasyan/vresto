"""NDVI long-term statistics (NDVI-LTS) service utilities.

Reads the CLMS global NDVI-LTS COGs from CDSE S3 (eodata) using GDAL
``/vsis3/`` virtual filesystem. The dataset is a single global COG per dekadal
period and statistic, so only the overlapping window is read via HTTP range
requests.

Path convention (static climatology, directory year fixed to 1999):
    s3://eodata/CLMS/bio-geophysical/vegetation_indices/ndvi-lts_global_1km_10daily_v3/
        1999/{MM}/{DD}/c_gls_NDVI-LTS_1999-2019-{MMDD}_GLOBE_VGT-PROBAV_V3.0.1_cog/
        c_gls_NDVI-MEAN-LTS_1999-2019-{MMDD}_GLOBE_VGT-PROBAV_V3.0.1.tiff
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np

from vresto.services.overlay_base import CogOverlayService, cdse_s3_env, ramp_rgba

NDVI_S3_BUCKET = "eodata"
NDVI_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
NDVI_DIR_YEAR = "1999"
NDVI_STAT = "MEAN"
NDVI_NODATA = 255
NDVI_SEA = 254
NDVI_VALID_MAX_DN = 250
NDVI_SCALE = 0.004
NDVI_OFFSET = -0.08
NDVI_MIN_VALUE = 0.0
NDVI_MAX_VALUE = 0.9

NDVI_S3_KEY_PATTERN = (
    "CLMS/bio-geophysical/vegetation_indices/ndvi-lts_global_1km_10daily_v3/1999/{month}/{day}/c_gls_NDVI-LTS_1999-2019-{month}{day}_GLOBE_VGT-PROBAV_V3.0.1_cog/c_gls_NDVI-MEAN-LTS_1999-2019-{month}{day}_GLOBE_VGT-PROBAV_V3.0.1.tiff"
)

NDVI_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 110, 78, 51, "0.0-0.2"),
    (1, 163, 117, 73, "0.2-0.4"),
    (2, 200, 166, 94, "0.4-0.6"),
    (3, 138, 173, 90, "0.6-0.75"),
    (4, 46, 125, 50, "0.75-0.9"),
]


def ndvi_lts_period_from_date(date_str: str) -> Tuple[str, str]:
    """Map a streamed Sentinel date to the matching NDVI-LTS dekadal period.

    The NDVI-LTS climatology is published for days 01, 11, and 21 of each month.
    Use the current dekad rather than a future period.
    """
    digits = "".join(char for char in str(date_str or "") if char.isdigit())
    if len(digits) < 8:
        raise ValueError(f"Unsupported date format: {date_str!r}")
    parsed = datetime.strptime(digits[:8], "%Y%m%d")
    if parsed.day >= 21:
        day = "21"
    elif parsed.day >= 11:
        day = "11"
    else:
        day = "01"
    return parsed.strftime("%m"), day


class NDVIService(CogOverlayService):
    """Fetch and align NDVI-LTS data for Sentinel overlays via GDAL vsis3.

    Its methods take the streamed Sentinel date, which picks the dekadal period.
    """

    name = "ndvi"
    label = "NDVI"
    key_prefix = "ndvi_lts"
    rgba_token = "ndvi-rgba-v1"
    aligned_nodata = NDVI_NODATA
    overview_resampling = "average"

    def _key(self, date: str) -> Optional[Tuple[str, ...]]:
        month, day = ndvi_lts_period_from_date(date)
        return (f"{month}{day}",)

    def _source_paths(self, left: float, bottom: float, right: float, top: float, period: str) -> List[str]:
        return [self._vsis3_path(period[:2], period[2:])]

    def _s3_env(self):
        return cdse_s3_env(NDVI_S3_ENDPOINT, "NDVI")

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, ...]:
        ndvi = values.astype("float32") * NDVI_SCALE + NDVI_OFFSET
        return ramp_rgba(ndvi, values <= NDVI_VALID_MAX_DN, self._colormap_lut(), NDVI_MIN_VALUE, NDVI_MAX_VALUE)

    def _vsis3_path(self, month: str, day: str) -> str:
        key = NDVI_S3_KEY_PATTERN.format(month=month, day=day)
        return f"/vsis3/{NDVI_S3_BUCKET}/{key}"

    @staticmethod
    def _colormap_lut() -> np.ndarray:
        import matplotlib.colors as mcolors

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "ndvi_lts",
            ["#6e4e33", "#b58b55", "#d0b56d", "#8fb058", "#2e7d32"],
            N=256,
        )
        return (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)


ndvi_service = NDVIService()
