"""Band composition utilities: read bands with rasterio and build previews.

This module provides a BandComposer class to centralize reading, resizing
and composing bands (single-band, RGB) for preview generation.
"""

from __future__ import annotations

import tempfile
from typing import Iterable, Optional, Tuple

import numpy as np


class BandComposer:
    """Read band files and create preview arrays or PNG files.

    Uses rasterio when available; falls back to simple file existence checks
    and raises informative errors when rasterio is not installed.
    """

    def __init__(self) -> None:
        try:
            import rasterio  # type: ignore

            self._rasterio = rasterio
        except Exception:
            self._rasterio = None

    def read_band_preview(self, band_file: str, out_shape: Optional[Tuple[int, int]] = None) -> np.ndarray:
        """Read a single band and return a 2D numpy array suitable for preview.

        If `out_shape` is provided it should be (height, width) and rasterio will
        attempt to read with resampling. If rasterio is unavailable, raises RuntimeError.
        """
        if self._rasterio is None:
            raise RuntimeError("rasterio is required to read band files")

        with self._rasterio.open(band_file) as src:
            if out_shape is not None:
                data = src.read(1, out_shape=out_shape)
            else:
                data = src.read(1)
        return data

    def build_rgb_preview(self, band_files: Iterable[str], out_shape: Optional[Tuple[int, int]] = None) -> np.ndarray:
        """Build an RGB uint8 array from three band files.

        `band_files` must be an iterable of three file paths in R,G,B order.
        Returns a uint8 numpy array with shape (h, w, 3).
        """
        files = list(band_files)
        if len(files) != 3:
            raise ValueError("band_files must contain exactly three file paths (R,G,B)")
        bands = []
        for f in files:
            try:
                data = self.read_band_preview(f, out_shape=out_shape)
            except Exception:
                # re-raise with context
                raise
            bands.append(data)

        rgb = np.stack(bands, axis=-1)
        # contrast-stretch per-band to 0..255
        p1 = np.percentile(rgb, 2)
        p99 = np.percentile(rgb, 98)
        rgb_scaled = (rgb - p1) / max((p99 - p1), 1e-6)
        rgb_u8 = (np.clip(rgb_scaled, 0.0, 1.0) * 255).astype("uint8")
        return rgb_u8

    def save_array_as_png(self, arr: np.ndarray, quality: int = 85) -> str:
        """Save a uint8 RGB or grayscale array to a temp PNG and return filepath."""
        try:
            from PIL import Image
        except Exception:
            raise RuntimeError("Pillow is required to save preview PNGs")

        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmpf.close()
        img = Image.fromarray(arr)
        img.save(tmpf.name, format="PNG", quality=quality)
        return tmpf.name


__all__ = ["BandComposer"]
