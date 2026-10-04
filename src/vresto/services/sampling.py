"""Point sampling of the aligned overlay rasters.

Every overlay service writes an *aligned* raster on the streamed Sentinel-2 tile's
own grid before colourising it. That file holds the raw values (class codes, DNs,
physical units) the map only shows as colours, so reading one pixel from it tells
the user the actual value under the cursor. The read is a single-pixel window on a
local file: no network access and no tile server involved.

This module is deliberately free of UI and overlay knowledge. Turning a raw value
into display text is the job of the decoders in :mod:`vresto.ui.overlays`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import rasterio
from rasterio.warp import transform


@dataclass(frozen=True)
class PointSample:
    """Outcome of sampling one point.

    Attributes:
        status: ``"ok"`` when the pixel holds a value, ``"nodata"`` when it is masked
            (an expected result, e.g. cloud-masked LST or open sea for NDVI), and
            ``"outside"`` when the point lies beyond the raster's extent.
        raw: The raw pixel value; set only when ``status`` is ``"ok"``.
    """

    status: Literal["ok", "outside", "nodata"]
    raw: float | None = None


def wrap_longitude(lon: float) -> float:
    """Normalise a longitude into ``[-180, 180)``.

    Leaflet reports unwrapped longitudes after the map is panned across the antimeridian,
    so anything reading a click position should wrap it before using or displaying it.
    """
    return (lon + 180.0) % 360.0 - 180.0


def sample_raster(path: str, lat: float, lon: float) -> PointSample:
    """Read the first band's value at a WGS84 position.

    Args:
        path: Path of the raster to sample. Pass the exact aligned raster written by
            the overlay service, never the colourised ``*_rgba.tif`` next to it.
        lat: Latitude in degrees (EPSG:4326).
        lon: Longitude in degrees (EPSG:4326); values outside ``[-180, 180]`` are wrapped.

    Returns:
        A :class:`PointSample`. Pixels masked by the raster's own nodata value, and
        non-finite values, are reported as ``"nodata"``.

    Raises:
        rasterio.errors.RasterioIOError: If the raster cannot be opened.
    """
    with rasterio.open(path) as ds:
        xs, ys = transform("EPSG:4326", ds.crs, [wrap_longitude(lon)], [lat])
        row, col = ds.index(xs[0], ys[0])
        if not (0 <= row < ds.height and 0 <= col < ds.width):
            return PointSample("outside")

        value = ds.read(1, window=((row, row + 1), (col, col + 1)), masked=True)[0, 0]

    if value is np.ma.masked or not math.isfinite(value):
        return PointSample("nodata")
    return PointSample("ok", float(value))
