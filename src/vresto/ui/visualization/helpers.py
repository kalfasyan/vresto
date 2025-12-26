"""Visualization helpers for image compositing, previews, and rendering.

This module provides utilities for:
- Image compositing (RGB, multi-band visualization)
- Preview image resizing and normalization
- Array normalization and color space conversions
- SCL (Scene Classification Layer) palette definitions and rendering
"""

import tempfile
from typing import Optional, Tuple

import numpy as np
from loguru import logger

# Constants
PREVIEW_MAX_DIM = 1830  # target maximum preview dimension


# ============================================================================
# SCL (Scene Classification Layer) Palette & Rendering
# ============================================================================

# SCL color palette - maps SCL values (0-11) to RGB colors
# Reference: https://sentinels.copernicus.eu/documents/247904/685211/Sentinel-2_L2A_SCP_PDGS.pdf
SCL_PALETTE = {
    0: (0, 0, 0),  # No Data (Missing data) #000000
    1: (255, 0, 0),  # Saturated or defective pixel #ff0000
    2: (47, 47, 47),  # Topographic casted shadows (Dark features/Shadows) #2f2f2f
    3: (100, 50, 0),  # Cloud shadows #643200
    4: (0, 160, 0),  # Vegetation #00a000
    5: (255, 230, 90),  # Not-vegetated #ffe65a
    6: (0, 0, 255),  # Water #0000ff
    7: (128, 128, 128),  # Unclassified #808080
    8: (192, 192, 192),  # Cloud medium probability #c0c0c0
    9: (255, 255, 255),  # Cloud high probability #ffffff
    10: (100, 200, 255),  # Thin cirrus #64c8ff
    11: (255, 150, 255),  # Snow or ice #ff96ff
}

SCL_LABELS = {
    0: "No Data (Missing data)",
    1: "Saturated or defective pixel",
    2: "Topographic casted shadows",
    3: "Cloud shadows",
    4: "Vegetation",
    5: "Not-vegetated",
    6: "Water",
    7: "Unclassified",
    8: "Cloud medium probability",
    9: "Cloud high probability",
    10: "Thin cirrus",
    11: "Snow or ice",
}


def create_scl_plotly_figure(scl_array: np.ndarray) -> Optional[object]:
    """Create an interactive Plotly figure for SCL data visualization.

    Args:
        scl_array: 2D numpy array with SCL values (0-11)

    Returns:
        A Plotly figure object with the SCL data visualized
    """
    try:
        import plotly.graph_objects as go

        # Create custom colorscale from SCL palette
        # Map each class value to its color
        colorscale = []
        for i in range(12):
            r, g, b = SCL_PALETTE[i]
            # Normalize position to 0-1 range
            pos = i / 11.0
            colorscale.append([pos, f"rgb({r},{g},{b})"])

        # Create heatmap with custom colorscale
        # Flip array vertically to match geospatial coordinates (top is north)
        flipped_scl = np.flipud(scl_array)
        fig = go.Figure(
            data=go.Heatmap(
                z=flipped_scl,
                colorscale=colorscale,
                zmin=0,
                zmax=11,
                colorbar=dict(
                    title="SCL Class",
                    tickvals=list(range(12)),
                    ticktext=[f"{i}: {SCL_LABELS[i]}" for i in range(12)],
                    len=0.8,
                ),
                hovertemplate="Class: %{z} (%{customdata})<extra></extra>",
                customdata=np.vectorize(lambda x: SCL_LABELS.get(int(x), f"Class {int(x)}"))(flipped_scl),
            )
        )

        fig.update_layout(
            title="SCL (Scene Classification Layer) - Interactive Map",
            xaxis_title="X (pixels)",
            yaxis_title="Y (pixels)",
            width=800,
            height=600,
            margin=dict(l=50, r=150, t=50, b=50),
        )

        # Ensure y-axis is not reversed (important for geospatial data)
        fig.update_yaxes(autorange=True)

        return fig
    except Exception as e:
        logger.error(f"Error creating SCL plotly figure: {e}")
        return None


# ============================================================================
# Image Compositing & Normalization
# ============================================================================


def normalize_image_array(
    arr: np.ndarray,
    percentile_low: float = 2.0,
    percentile_high: float = 98.0,
    clip_range: Tuple[float, float] = (0.0, 1.0),
) -> np.ndarray:
    """Normalize image array using percentile stretching.

    Args:
        arr: Input array (single band or multi-band)
        percentile_low: Lower percentile for normalization
        percentile_high: Upper percentile for normalization
        clip_range: Range to clip output to (default: 0.0-1.0)

    Returns:
        Normalized array with values in clip_range
    """
    try:
        p_low = np.percentile(arr, percentile_low)
        p_high = np.percentile(arr, percentile_high)

        normalized = (arr - p_low) / max((p_high - p_low), 1e-6)
        normalized = np.clip(normalized, clip_range[0], clip_range[1])

        return normalized
    except Exception as e:
        logger.error(f"Error normalizing array: {e}")
        raise


def convert_to_uint8(arr: np.ndarray) -> np.ndarray:
    """Convert normalized array to uint8 (0-255).

    Args:
        arr: Input array (values should be 0.0-1.0)

    Returns:
        uint8 array with values 0-255
    """
    try:
        if arr.dtype == np.uint8:
            return arr

        # If values are in range 0-1, scale to 0-255
        if arr.max() <= 1.0:
            return (arr * 255).astype(np.uint8)

        # Otherwise clip to 0-255
        return np.clip(arr, 0, 255).astype(np.uint8)
    except Exception as e:
        logger.error(f"Error converting to uint8: {e}")
        raise


def compose_rgb_bands(
    band_arrays: dict,
    band_order: Tuple[str, str, str],
    percentile_low: float = 2.0,
    percentile_high: float = 98.0,
) -> np.ndarray:
    """Compose RGB image from individual band arrays.

    Args:
        band_arrays: Dict mapping band names to numpy arrays
        band_order: Tuple of (R_band, G_band, B_band) names
        percentile_low: Lower percentile for normalization
        percentile_high: Upper percentile for normalization

    Returns:
        RGB array (H, W, 3) as uint8
    """
    try:
        # Stack bands in RGB order
        bands = [band_arrays[name] for name in band_order]
        rgb = np.stack(bands, axis=-1)

        # Normalize using percentile stretching
        rgb_norm = normalize_image_array(rgb, percentile_low, percentile_high)

        # Convert to uint8
        rgb_uint8 = convert_to_uint8(rgb_norm)

        return rgb_uint8
    except Exception as e:
        logger.error(f"Error composing RGB bands: {e}")
        raise


def flip_image_vertical(arr: np.ndarray) -> np.ndarray:
    """Flip image vertically (upside down).

    Args:
        arr: Input array (2D or 3D)

    Returns:
        Vertically flipped array
    """
    try:
        return np.flipud(arr)
    except Exception as e:
        logger.error(f"Error flipping image: {e}")
        return arr


# ============================================================================
# Preview Image Resizing & Shape Computation
# ============================================================================


def compute_preview_shape(
    orig_h: int,
    orig_w: int,
    max_dim: int = PREVIEW_MAX_DIM,
) -> Tuple[int, int]:
    """Compute preview shape preserving aspect ratio.

    Args:
        orig_h: Original height
        orig_w: Original width
        max_dim: Maximum dimension for preview

    Returns:
        Tuple of (preview_height, preview_width)
    """
    try:
        scale = max(orig_h / max_dim, orig_w / max_dim, 1.0)
        out_h = int(max(1, round(orig_h / scale)))
        out_w = int(max(1, round(orig_w / scale)))
        return out_h, out_w
    except Exception:
        return min(orig_h, max_dim), min(orig_w, max_dim)


def resize_array_to_preview(
    arr: np.ndarray,
    max_dim: int = PREVIEW_MAX_DIM,
) -> np.ndarray:
    """Resize numpy array to preview-friendly size using PIL.

    Args:
        arr: Input numpy array (2D or 3D)
        max_dim: Maximum dimension for preview

    Returns:
        Resized array
    """
    try:
        try:
            from PIL import Image
        except ImportError:
            logger.warning("PIL not available, returning original array")
            return arr

        if getattr(arr, "ndim", 0) == 2:
            # Grayscale image
            mode = "L"
            arr_clipped = np.clip(arr, 0, 255).astype("uint8")
            img = Image.fromarray(arr_clipped, mode=mode)
        else:
            # RGB or multi-channel image
            if arr.dtype != np.uint8:
                arr_copy = arr.copy()
                if arr_copy.max() <= 1.0:
                    arr_copy = (arr_copy * 255.0).astype("uint8")
                else:
                    arr_copy = np.clip(arr_copy, 0, 255).astype("uint8")
                img = Image.fromarray(arr_copy)
            else:
                img = Image.fromarray(arr)

        w, h = img.size
        scale = max(h / max_dim, w / max_dim, 1.0)
        new_w = int(max(1, round(w / scale)))
        new_h = int(max(1, round(h / scale)))

        img_rs = img.resize((new_w, new_h), resample=Image.BILINEAR)
        out = np.array(img_rs)
        return out
    except Exception as e:
        logger.warning(f"Error resizing array: {e}, returning original")
        return arr


# ============================================================================
# Image File I/O
# ============================================================================


def save_array_as_image(
    arr: np.ndarray,
    output_path: Optional[str] = None,
    format: str = "png",
) -> str:
    """Save numpy array as image file.

    Args:
        arr: Input array (uint8 grayscale or RGB)
        output_path: Path to save to; if None, uses temp file
        format: Image format (png, jpg, etc.)

    Returns:
        Path to saved image file
    """
    try:
        # Default to temp file if no path provided
        if output_path is None:
            tmpf = tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False)
            output_path = tmpf.name
            tmpf.close()

        # Try PIL first
        try:
            from PIL import Image

            img = Image.fromarray(arr)
            img.save(output_path, quality=85)
            return output_path
        except ImportError:
            pass

        # Fallback to imageio
        try:
            import imageio

            imageio.imwrite(output_path, arr)
            return output_path
        except ImportError:
            logger.error("Neither PIL nor imageio available for saving images")
            raise

    except Exception as e:
        logger.error(f"Error saving array as image: {e}")
        raise


# ============================================================================
# Band Utilities (Reused from product_analysis_tab)
# ============================================================================


def normalize_band_data(
    band_data: np.ndarray,
    percentile_low: float = 2.0,
    percentile_high: float = 98.0,
) -> Tuple[np.ndarray, float, float]:
    """Normalize single band data and return with min/max values.

    Args:
        band_data: Input band array
        percentile_low: Lower percentile for stretching
        percentile_high: Upper percentile for stretching

    Returns:
        Tuple of (normalized_array, vmin, vmax)
    """
    try:
        vmin = float(np.nanmin(band_data))
        vmax = float(np.nanmax(band_data))
        denom = vmax - vmin if (vmax - vmin) != 0 else 1.0
        normalized = (band_data - vmin) / denom

        return normalized, vmin, vmax
    except Exception as e:
        logger.error(f"Error normalizing band data: {e}")
        raise


def create_grayscale_thumbnail(
    band_data: np.ndarray,
    max_dim: int = 128,
    percentile_low: float = 2.0,
    percentile_high: float = 98.0,
) -> np.ndarray:
    """Create grayscale thumbnail from band data.

    Args:
        band_data: Input band array
        max_dim: Maximum dimension for thumbnail
        percentile_low: Lower percentile for normalization
        percentile_high: Upper percentile for normalization

    Returns:
        RGB thumbnail array (converts grayscale to RGB for consistency)
    """
    try:
        # Normalize data
        p_low = np.percentile(band_data, percentile_low)
        p_high = np.percentile(band_data, percentile_high)
        normalized = np.clip((band_data - p_low) / max((p_high - p_low), 1e-6), 0, 1)

        # Convert to uint8
        img = (normalized * 255).astype(np.uint8)

        # Resize
        resized = resize_array_to_preview(img, max_dim=max_dim)

        # Convert grayscale to RGB (stack 3 copies)
        tile_rgb = np.stack([resized, resized, resized], axis=-1)

        return tile_rgb
    except Exception as e:
        logger.error(f"Error creating grayscale thumbnail: {e}")
        raise
