"""FAPAR (Fraction of Absorbed Photosynthetically Active Radiation) overlay utilities.

Uses the CLMS FAPAR 300 m 10-daily collection and renders the ``fapar300_fapar``
asset. Asset discovery is done through CDSE STAC so the overlay snaps to the
nearest available 10-daily product to the streamed Sentinel acquisition date.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import ClmsStacOverlayService, OverlayResult

FAPAR_COLLECTION_ID = "clms_fapar_global_300m_10daily_v2_cog"
FAPAR_ASSET_KEY = "fapar300_fapar"
FAPAR_S3_ENDPOINT = "eodata.dataspace.copernicus.eu"
FAPAR_RAW_NODATA = 255
FAPAR_NODATA = 255
FAPAR_SCALE = 0.004
FAPAR_OFFSET = 0.0
FAPAR_MIN_VALUE = 0.0
FAPAR_MAX_VALUE = 0.94

# Official FAPAR 300 m legend (bin centre values as published in the product
# documentation). Each entry maps a display value to an RGB colour and label.
FAPAR_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (1, 255, 0, 0, "0.0"),
    (2, 255, 17, 0, "0.01"),
    (3, 255, 34, 0, "0.02"),
    (4, 255, 51, 0, "0.03"),
    (5, 255, 68, 0, "0.04"),
    (6, 255, 85, 0, "0.05"),
    (7, 255, 102, 0, "0.06"),
    (8, 255, 119, 0, "0.07"),
    (9, 255, 136, 0, "0.08"),
    (10, 255, 153, 0, "0.09"),
    (11, 255, 170, 0, "0.10"),
    (12, 255, 187, 0, "0.11"),
    (13, 255, 204, 0, "0.12"),
    (14, 255, 221, 0, "0.13"),
    (15, 255, 238, 0, "0.14"),
    (16, 247, 255, 0, "0.15"),
    (17, 230, 255, 0, "0.16"),
    (18, 213, 255, 0, "0.17"),
    (19, 196, 255, 0, "0.18"),
    (20, 179, 255, 0, "0.19"),
    (21, 162, 255, 0, "0.20"),
    (22, 145, 255, 0, "0.21"),
    (23, 128, 255, 0, "0.22"),
    (24, 111, 255, 0, "0.23"),
    (25, 94, 255, 0, "0.24"),
    (26, 77, 255, 0, "0.25"),
    (27, 60, 255, 0, "0.26"),
    (28, 43, 255, 0, "0.27"),
    (29, 26, 255, 0, "0.28"),
    (30, 9, 255, 0, "0.29"),
    (31, 0, 255, 8, "0.30"),
    (32, 0, 255, 25, "0.31"),
    (33, 0, 255, 42, "0.32"),
    (34, 0, 255, 59, "0.33"),
    (35, 0, 255, 76, "0.34"),
    (36, 0, 255, 93, "0.35"),
    (37, 0, 255, 110, "0.36"),
    (38, 0, 255, 127, "0.37"),
    (39, 0, 255, 144, "0.38"),
    (40, 0, 255, 161, "0.39"),
    (41, 0, 255, 178, "0.40"),
    (42, 0, 255, 195, "0.41"),
    (43, 0, 255, 212, "0.42"),
    (44, 0, 255, 229, "0.43"),
    (45, 0, 255, 246, "0.44"),
    (46, 0, 238, 255, "0.45"),
    (47, 0, 221, 255, "0.46"),
    (48, 0, 204, 255, "0.47"),
    (49, 0, 187, 255, "0.48"),
    (50, 0, 170, 255, "0.49"),
    (51, 0, 153, 255, "0.50"),
    (52, 0, 136, 255, "0.51"),
    (53, 0, 119, 255, "0.52"),
    (54, 0, 102, 255, "0.53"),
    (55, 0, 85, 255, "0.54"),
    (56, 0, 68, 255, "0.55"),
    (57, 0, 51, 255, "0.56"),
    (58, 0, 34, 255, "0.57"),
    (59, 0, 17, 255, "0.58"),
    (60, 0, 0, 255, "0.59"),
    (61, 17, 0, 255, "0.60"),
    (62, 34, 0, 255, "0.61"),
    (63, 51, 0, 255, "0.62"),
    (64, 68, 0, 255, "0.63"),
    (65, 85, 0, 255, "0.64"),
    (66, 102, 0, 255, "0.65"),
    (67, 119, 0, 255, "0.66"),
    (68, 136, 0, 255, "0.67"),
    (69, 153, 0, 255, "0.68"),
    (70, 170, 0, 255, "0.69"),
    (71, 187, 0, 255, "0.70"),
    (72, 204, 0, 255, "0.71"),
    (73, 221, 0, 255, "0.72"),
    (74, 238, 0, 255, "0.73"),
    (75, 255, 0, 255, "0.74"),
    (76, 255, 0, 238, "0.75"),
    (77, 255, 0, 221, "0.76"),
    (78, 255, 0, 204, "0.77"),
    (79, 255, 0, 187, "0.78"),
    (80, 255, 0, 170, "0.79"),
    (81, 255, 0, 153, "0.80"),
    (82, 255, 0, 136, "0.81"),
    (83, 255, 0, 119, "0.82"),
    (84, 255, 0, 102, "0.83"),
    (85, 255, 0, 85, "0.84"),
    (86, 255, 0, 68, "0.85"),
    (87, 255, 0, 51, "0.86"),
    (88, 255, 0, 34, "0.87"),
    (89, 255, 0, 17, "0.88"),
    (90, 255, 0, 0, "0.89"),
    (91, 221, 0, 0, "0.90"),
    (92, 187, 0, 0, "0.91"),
    (93, 153, 0, 0, "0.92"),
    (94, 119, 0, 0, "0.93"),
    (95, 85, 0, 0, "0.94"),
]

# Legend colour per bin id. Bin ``k`` covers FAPAR ``(k - 1) / 100``, so bins are 0.01 wide.
FAPAR_COLOR_BY_VALUE: dict[int, Tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in FAPAR_LEGEND}
# Raw DNs 0..250 are FAPAR 0..1 (scale 0.004); 251..254 are flags and 255 is no data.
FAPAR_VALID_MAX_DN = 250


def fapar_legend_bin(dn: int) -> int:
    """Legend bin of a raw DN: its FAPAR value rounded to 0.01, with values above 0.94 in the last bin."""
    return min(len(FAPAR_LEGEND), int(round(dn * FAPAR_SCALE * 100)) + 1)


# RGB per raw DN (rows 0..255), so a block is coloured with one lookup. Rows above the valid range stay black;
# they are drawn transparent.
FAPAR_RGB_BY_DN = np.zeros((256, 3), dtype=np.uint8)
for _dn in range(FAPAR_VALID_MAX_DN + 1):
    FAPAR_RGB_BY_DN[_dn] = FAPAR_COLOR_BY_VALUE[fapar_legend_bin(_dn)]
del _dn


# Kept for callers that import the per-product name.
FAPAROverlayResult = OverlayResult


def raw_fapar_to_physical(raw: np.ndarray) -> np.ndarray:
    """Convert raw FAPAR DNs to physical FAPAR values using the product scale."""
    return raw.astype("float32") * FAPAR_SCALE + FAPAR_OFFSET


class FAPARService(ClmsStacOverlayService):
    """Fetch and align 10-daily FAPAR data for Sentinel overlays via GDAL vsis3."""

    name = "fapar"
    label = "FAPAR"
    collection_id = FAPAR_COLLECTION_ID
    asset_key = FAPAR_ASSET_KEY
    s3_endpoint = FAPAR_S3_ENDPOINT
    search_window_days = 15
    max_items = 12
    aligned_dtype = "uint8"
    aligned_nodata = FAPAR_NODATA
    align_resampling = "nearest"
    overview_resampling = "nearest"
    # v2: colours follow each DN's FAPAR value (v1 looked DNs up as legend bin ids) and flags are transparent.
    rgba_version = "v2"

    get_aligned_fapar_result = ClmsStacOverlayService.get_aligned_result
    get_aligned_fapar_path = ClmsStacOverlayService.get_aligned_path
    get_colorized_fapar_result = ClmsStacOverlayService.get_colorized_result
    get_colorized_fapar_path = ClmsStacOverlayService.get_colorized_path

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        return data

    def _colorize(self, values: np.ndarray) -> Tuple[np.ndarray, ...]:
        rgb = FAPAR_RGB_BY_DN[values]
        alpha = np.where(values <= FAPAR_VALID_MAX_DN, 255, 0).astype(np.uint8)
        return rgb[..., 0], rgb[..., 1], rgb[..., 2], alpha

    def _to_physical(self, values: np.ndarray) -> np.ndarray:
        # The aligned raster keeps raw DNs for the class colouring; DNs above 250 are flags.
        return np.where(values <= 250, raw_fapar_to_physical(values), FAPAR_NODATA)


fapar_service = FAPARService()
