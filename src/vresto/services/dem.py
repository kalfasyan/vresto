"""Copernicus DEM (GLO-30) service utilities.

Streams Copernicus DEM GLO-30 tiles from the public AWS Open Data bucket
``copernicus-dem-30m`` using GDAL ``/vsis3/`` (no full-file downloads, no
credentials), and aligns them to Sentinel raster grids for terrain overlays.

Unlike the categorical land-cover overlays (LCM, WorldCover), the DEM is a
*continuous* surface, so it is rendered with a hypsometric colour ramp combined
with hillshade relief instead of a discrete class palette.

Tile naming convention (1°×1° tiles, SW corner):
    s3://copernicus-dem-30m/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/
        Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

import numpy as np

from vresto.services.overlay_base import CogOverlayService, degree_tiles, public_s3_env

DEM_BUCKET = "copernicus-dem-30m"
DEM_REGION = "eu-central-1"
DEM_TILE_DEG = 1  # Copernicus DEM tiles are 1°×1°
DEM_NODATA = -9999.0

# Relative hypsometric legend (matplotlib "terrain" sub-range [0.25, 1.0]).
# The overlay applies a per-scene 2–98% stretch, so labels are relative.
# Exported as list of tuples for the shared legend renderer: (id, R, G, B, label)
DEM_LEGEND: List[Tuple[int, int, int, int, str]] = [
    (0, 1, 204, 102, "Lower"),
    (1, 193, 243, 141, "Low–mid"),
    (2, 190, 172, 118, "Mid"),
    (3, 159, 132, 126, "Mid–high"),
    (4, 255, 255, 255, "Higher"),
]


def _tile_dir(lat_origin: int, lon_origin: int) -> str:
    """Build the tile directory/file stem like ``Copernicus_DSM_COG_10_N50_00_E004_00_DEM``."""
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_origin):02d}_00_{ew}{abs(lon_origin):03d}_00_DEM"


class DEMService(CogOverlayService):
    """Fetch and align Copernicus DEM GLO-30 data for Sentinel overlays via GDAL vsis3.

    The aligned raster holds float32 elevations in metres; the colorized raster is a
    hypsometric tint with hillshade relief.
    """

    name = "dem"
    label = "DEM"
    key_prefix = "dem_glo30"
    rgba_token = "dem-rgba-v1"
    aligned_dtype = "float32"
    aligned_nodata = DEM_NODATA
    read_resampling = "bilinear"
    align_resampling = "bilinear"
    overview_resampling = "average"

    def _source_paths(self, left: float, bottom: float, right: float, top: float) -> List[str]:
        return [self._vsis3_path(lat, lon) for lat, lon in degree_tiles(left, bottom, right, top, DEM_TILE_DEG)]

    def _s3_env(self):
        return public_s3_env(DEM_REGION)

    def _decode(self, data: np.ndarray, src: Any) -> np.ndarray:
        data = data.astype("float32")
        if src.nodata is not None:
            data = np.where(data == np.float32(src.nodata), DEM_NODATA, data)
        return data

    def _write_colorized(self, aligned: str, path: Path) -> None:
        """Colour with a per-scene 2–98% stretch; blocks are read with padding so hillshade has no seams."""
        import rasterio

        lut = self._colormap_lut()
        with rasterio.open(aligned) as src:
            nodata = src.nodata if src.nodata is not None else DEM_NODATA
            cellsize = abs(src.transform.a) or 1.0
            vmin, vmax = self._compute_stretch(src, nodata)
            span = max(vmax - vmin, 1e-6)

            profile = src.profile.copy()
            profile.update({
                "count": 4,
                "dtype": "uint8",
                "nodata": None,
                "compress": "deflate",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
            })

            pad = 8
            with rasterio.open(path, "w", **profile) as dst:
                for _, window in src.block_windows(1):
                    row_off = int(window.row_off)
                    col_off = int(window.col_off)
                    bh = int(window.height)
                    bw = int(window.width)

                    padded_window = ((row_off - pad, row_off + bh + pad), (col_off - pad, col_off + bw + pad))
                    z = src.read(1, window=padded_window, boundless=True, fill_value=nodata).astype("float32")
                    valid = z != nodata

                    if not valid.any():
                        zeros = np.zeros((bh, bw), dtype=np.uint8)
                        for band in range(1, 5):
                            dst.write(zeros, band, window=window)
                        continue

                    z_filled = np.where(valid, z, vmin)
                    shade = self._hillshade(z_filled, cellsize)

                    # Crop padding back to the block extent.
                    z_c = z[pad : pad + bh, pad : pad + bw]
                    valid_c = valid[pad : pad + bh, pad : pad + bw]
                    shade_c = shade[pad : pad + bh, pad : pad + bw]

                    norm = np.clip((z_c - vmin) / span, 0.0, 1.0)
                    idx = (norm * 255.0).astype(np.uint8)
                    rgb = lut[idx].astype(np.float32)  # (bh, bw, 3)
                    rgb *= (0.35 + 0.65 * shade_c)[..., None]
                    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
                    alpha = np.where(valid_c, 255, 0).astype(np.uint8)

                    dst.write(rgb[:, :, 0], 1, window=window)
                    dst.write(rgb[:, :, 1], 2, window=window)
                    dst.write(rgb[:, :, 2], 3, window=window)
                    dst.write(alpha, 4, window=window)

    def _vsis3_path(self, lat_origin: int, lon_origin: int) -> str:
        stem = _tile_dir(lat_origin, lon_origin)
        return f"/vsis3/{DEM_BUCKET}/{stem}/{stem}.tif"

    def _compute_stretch(self, src, nodata: float) -> Tuple[float, float]:
        """Compute a 2–98% elevation stretch from a downsampled read of the aligned raster."""
        maxdim = 1024
        scale = max(1, int(max(src.width, src.height) / maxdim))
        out_h = max(1, src.height // scale)
        out_w = max(1, src.width // scale)
        arr = src.read(1, out_shape=(out_h, out_w)).astype("float32")
        valid = arr != nodata
        if not valid.any():
            return 0.0, 1.0
        vals = arr[valid]
        vmin = float(np.percentile(vals, 2))
        vmax = float(np.percentile(vals, 98))
        if vmax <= vmin:
            vmax = vmin + 1.0
        return vmin, vmax

    @staticmethod
    def _colormap_lut() -> np.ndarray:
        """256×3 uint8 hypsometric LUT from the matplotlib 'terrain' colormap (land sub-range)."""
        import matplotlib

        cmap = matplotlib.colormaps["terrain"]
        return (cmap(np.linspace(0.25, 1.0, 256))[:, :3] * 255).astype(np.uint8)

    @staticmethod
    def _hillshade(z: np.ndarray, cellsize: float, azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
        """Return a 0–1 hillshade for a 2D elevation array (metres) on a metric grid."""
        if cellsize <= 0:
            cellsize = 1.0
        gy, gx = np.gradient(z, cellsize)
        slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az = np.radians(360.0 - azimuth + 90.0)
        alt = np.radians(altitude)
        shaded = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        return np.clip((shaded + 1.0) / 2.0, 0.0, 1.0)


dem_service = DEMService()
