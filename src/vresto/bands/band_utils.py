"""Band utilities: SCL processing and preview resizing helpers.

This module provides small, single-responsibility classes to handle
Scene Classification Layer (SCL) visualization and image resizing for
client-side previews. The goal is to keep logic modular so UI code
can import concise helpers.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

try:
    from PIL import Image
except Exception:  # pragma: no cover - imported conditionally in tests
    Image = None  # type: ignore


class SclProcessor:
    """Provide SCL palette, labels and mapping utilities.

    Responsibilities:
    - Return palette as uint8 RGB array shape (12,3).
    - Return labels for classes 0..11.
    - Map a 2D SCL array to an RGB uint8 image.
    """

    LABELS = [
        "No Data (Missing data)",
        "Saturated or defective pixel",
        "Topographic casted shadows",
        "Cloud shadows",
        "Vegetation",
        "Not-vegetated",
        "Water",
        "Unclassified",
        "Cloud medium probability",
        "Cloud high probability",
        "Thin cirrus",
        "Snow or ice",
    ]

    COLORS = [
        (0, 0, 0),
        (255, 0, 0),
        (47, 47, 47),
        (100, 50, 0),
        (0, 160, 0),
        (255, 230, 90),
        (0, 0, 255),
        (128, 128, 128),
        (192, 192, 192),
        (255, 255, 255),
        (100, 200, 255),
        (255, 150, 255),
    ]

    @classmethod
    def palette(cls) -> np.ndarray:
        """Return RGB palette as uint8 numpy array of shape (12,3)."""
        return np.array(cls.COLORS, dtype=np.uint8)

    @classmethod
    def labels(cls) -> Tuple[str, ...]:
        """Return labels tuple for SCL classes 0..11."""
        return tuple(cls.LABELS)

    @classmethod
    def to_rgb(cls, scl_array: np.ndarray) -> np.ndarray:
        """Map a 2D integer SCL array to an RGB uint8 image.

        Args:
            scl_array: 2D array with integer class codes (0..11). Values
                       outside range will be clipped to 0..11.

        Returns:
            RGB image as numpy array shape (h, w, 3), dtype uint8.
        """
        if scl_array.ndim != 2:
            raise ValueError("scl_array must be a 2D array")

        pal = cls.palette()
        # Clip values to valid indices
        idx = np.clip(scl_array.astype(int), 0, pal.shape[0] - 1)
        h, w = idx.shape
        rgb = pal[idx].reshape(h, w, 3)
        return rgb


class BandPreviewResizer:
    """Resize band arrays for browser-friendly previews.

    Responsibilities:
    - Compute output shape preserving aspect ratio with a max dimension.
    - Resize 2D or 3D arrays to target size using Pillow when available.
    - Preserve color ordering and return uint8 arrays for images.
    """

    @staticmethod
    def compute_preview_shape(orig_h: int, orig_w: int, max_dim: int) -> Tuple[int, int]:
        """Compute (out_h, out_w) so max(out_h, out_w) <= max_dim preserving aspect.

        Returns a tuple of ints (out_h, out_w).
        """
        if orig_h <= 0 or orig_w <= 0:
            raise ValueError("orig_h and orig_w must be positive integers")
        if max_dim <= 0:
            raise ValueError("max_dim must be a positive integer")

        scale = min(1.0, max_dim / max(orig_h, orig_w))
        out_h = max(1, int(round(orig_h * scale)))
        out_w = max(1, int(round(orig_w * scale)))
        return out_h, out_w

    @classmethod
    def resize_array(cls, arr: np.ndarray, max_dim: int) -> np.ndarray:
        """Resize a 2D or 3D numpy array to have max dimension <= max_dim.

        For 3D arrays (H,W,C) the channels are preserved. For single-band 2D
        arrays the returned image will be uint8 scaled to 0..255 if input is
        float or other dtype.
        """
        if Image is None:
            raise RuntimeError("Pillow is required for resizing previews")

        if arr.ndim == 2:
            h, w = arr.shape
            out_h, out_w = cls.compute_preview_shape(h, w, max_dim)
            pil_mode = "L"
            img = Image.fromarray(cls._to_uint8(arr), mode=pil_mode)
            img = img.resize((out_w, out_h), resample=Image.BILINEAR)
            return np.array(img)
        elif arr.ndim == 3:
            h, w, c = arr.shape
            out_h, out_w = cls.compute_preview_shape(h, w, max_dim)
            # Ensure uint8
            arr_u8 = cls._to_uint8(arr)
            # PIL expects (W,H)
            img = Image.fromarray(arr_u8)
            img = img.resize((out_w, out_h), resample=Image.BILINEAR)
            return np.array(img)
        else:
            raise ValueError("arr must be a 2D or 3D numpy array")

    @staticmethod
    def _to_uint8(arr: np.ndarray) -> np.ndarray:
        """Convert array to uint8 suitable for image display.

        - If dtype is float, assume range 0..1 and scale to 0..255.
        - If dtype is integer and max <=255, cast to uint8.
        - Otherwise, normalize to 0..255.
        """
        if np.issubdtype(arr.dtype, np.floating):
            arr_clipped = np.nan_to_num(arr, nan=0.0)
            arr_scaled = np.clip(arr_clipped, 0.0, 1.0) * 255.0
            return arr_scaled.astype(np.uint8)

        if np.issubdtype(arr.dtype, np.integer):
            max_val = int(arr.max()) if arr.size else 0
            if max_val <= 255:
                return arr.astype(np.uint8)
            # normalize to 0..255
            arr = arr.astype(np.float32)
            arr = (arr - arr.min()) / max(1e-8, (arr.max() - arr.min()))
            return (arr * 255.0).astype(np.uint8)

        # Fallback: cast via float normalization
        arr = arr.astype(np.float32)
        arr = (arr - arr.min()) / max(1e-8, (arr.max() - arr.min()))
        return (arr * 255.0).astype(np.uint8)


__all__ = ["SclProcessor", "BandPreviewResizer"]


def create_scl_legend_image(box_width: int = 40, box_height: int = 24, pad: int = 8, font_size: int = 12) -> str | None:
    """Create a vertical legend PNG file for SCL classes and return temp filepath.

    Returns None if Pillow is not available or on failure.
    """
    try:
        import tempfile

        from PIL import Image, ImageDraw, ImageFont

        cmap = SclProcessor.palette()
        labels = SclProcessor.labels()
        n = len(labels)

        # load default font
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        # measure max text width
        dummy = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy)
        max_text_w = 0
        for lab in labels:
            w, h = draw.textsize(lab, font=font)
            if w > max_text_w:
                max_text_w = w

        img_w = box_width + pad + max_text_w + pad * 2
        img_h = n * (box_height + pad) + pad
        img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        y = pad
        for i, lab in enumerate(labels):
            c = tuple(int(x) for x in cmap[i])
            draw.rectangle([pad, y, pad + box_width, y + box_height], fill=c)
            tx = pad + box_width + pad
            ty = y + max(0, (box_height - (font.getsize(lab)[1] if font else 0)) // 2)
            draw.text((tx, ty), f"{i}: {lab}", fill=(0, 0, 0), font=font)
            y += box_height + pad

        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmpf.close()
        img.save(tmpf.name, format="PNG")
        return tmpf.name
    except Exception:
        return None
