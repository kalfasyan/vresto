"""Tile management service for high-resolution product visualization.

This module provides a manager to handle local tile server instances using localtileserver,
allowing high-resolution Sentinel-2 bands to be served as map layers.
"""

from __future__ import annotations

import os
import tempfile
from typing import List, Optional, Tuple

from loguru import logger

try:
    from localtileserver import TileClient

    HAS_TILESERVER = True
except ImportError:
    HAS_TILESERVER = False
    logger.warning("localtileserver not installed. High-res visualization will be unavailable.")


class TileManager:
    """Manages local tile server instances for product visualization.

    This class handles the lifecycle of tile servers for individual bands or RGB composites.
    """

    def __init__(self):
        self._active_client: Optional[TileClient] = None
        self._active_path: Optional[str] = None
        self._temp_vrt: Optional[str] = None

    def is_available(self) -> bool:
        """Check if localtileserver is installed and available."""
        return HAS_TILESERVER

    def get_tile_url(
        self,
        path: str | List[str],
        port: int = 0,
        palette: Optional[str | List[str]] = None,
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
        nodata: Optional[int] = None,
    ) -> Optional[str]:
        """Start a tile server for the given file(s) and return the tile URL.

        If a list of paths is provided, a temporary VRT will be created.

        Args:
            path: Path to the GeoTIFF or JP2 file, or list of paths.
            port: Preferred port for the server (0 for random).
            palette: Optional palette name or list of colors.
            min_val: Minimum value for scaling.
            max_val: Maximum value for scaling.
            nodata: Nodata value.

        Returns:
            The tile URL template (e.g., 'http://localhost:PORT/tiles/{z}/{x}/{y}.png?...')
            or None if starting the server fails.
        """
        if not HAS_TILESERVER:
            logger.error("Cannot get tile URL: localtileserver not installed")
            return None

        # Check path existence
        if isinstance(path, str):
            if not os.path.exists(path):
                logger.error(f"Cannot get tile URL: file not found at {path}")
                return None
        elif isinstance(path, list):
            for p in path:
                if not os.path.exists(p):
                    logger.error(f"Cannot get tile URL: file not found at {p}")
                    return None

        try:
            # Shutdown existing client - always recreate for now to ensure fresh state
            # and different port/URL if possible, or at least force refresh.
            self.shutdown()

            actual_path = path
            if isinstance(path, list):
                actual_path = self._create_vrt(path)
                if not actual_path:
                    return None

            # Start new client
            logger.info(f"Starting tile server for {actual_path}")
            if port > 0:
                self._active_client = TileClient(actual_path, port=port)
            else:
                self._active_client = TileClient(actual_path)
            self._active_path = path

            # Get the base URL
            url = self._active_client.get_tile_url()
            if url:
                # Handle Docker environment: replace 127.0.0.1/localhost with the host IP/name
                # so the browser can reach the tile server.
                external_host = os.getenv("VRESTO_TILE_SERVER_HOST")
                if external_host:
                    url = url.replace("127.0.0.1", external_host).replace("localhost", external_host)

                import time
                import urllib.parse

                # Add cache buster and visual parameters
                separator = "&" if "?" in url else "?"
                if palette:
                    palette_str = palette if isinstance(palette, str) else ",".join(palette)
                    url += f"{separator}palette={urllib.parse.quote(palette_str)}"
                    separator = "&"

                if min_val is not None:
                    url += f"{separator}min={min_val}"
                    separator = "&"
                if max_val is not None:
                    url += f"{separator}max={max_val}"
                    separator = "&"
                if nodata is not None:
                    url += f"{separator}nodata={nodata}"
                    separator = "&"

                # Cache buster ensures Leaflet/Browser actually fetches new tiles
                url += f"{separator}t={int(time.time())}"

            logger.info(f"Tile server started at {url}")
            return url

        except Exception as e:
            logger.exception(f"Failed to start tile server for {path}: {e}")
            self.shutdown()
            return None

    def shutdown(self):
        """Shutdown the active tile server client."""
        if self._active_client:
            logger.info(f"Shutting down tile server for {self._active_path}")
            try:
                if hasattr(self._active_client, "shutdown"):
                    self._active_client.shutdown()
            except Exception as e:
                logger.warning(f"Error during tile server shutdown: {e}")

            self._active_client = None
            self._active_path = None

        if self._temp_vrt and os.path.exists(self._temp_vrt):
            try:
                os.remove(self._temp_vrt)
            except Exception as e:
                logger.warning(f"Error removing temporary VRT: {e}")
            self._temp_vrt = None

    def _create_vrt(self, paths: List[str]) -> Optional[str]:
        """Create a temporary VRT from a list of band paths."""
        try:
            import rasterio

            # Use tempfile for VRT
            fd, vrt_path = tempfile.mkstemp(suffix=".vrt")
            os.close(fd)

            # Check if all files exist
            for p in paths:
                if not os.path.exists(p):
                    logger.error(f"Band file not found: {p}")
                    return None

            # Simple validation: open all datasets
            srcs = [rasterio.open(p) for p in paths]

            # Simplified VRT XML for stacking
            vrt_content = self._generate_vrt_xml(paths)
            with open(vrt_path, "w") as f:
                f.write(vrt_content)

            for s in srcs:
                s.close()

            self._temp_vrt = vrt_path
            return vrt_path

        except Exception as e:
            logger.exception(f"Failed to create VRT: {e}")
            return None

    def _generate_vrt_xml(self, paths: List[str]) -> str:
        """Generate a basic VRT XML to stack multiple files as bands."""
        import rasterio

        # Get metadata from first band
        with rasterio.open(paths[0]) as src:
            width = src.width
            height = src.height
            crs = src.crs.to_wkt()
            transform = src.transform
            dtype = src.dtypes[0]

        # GDAL uses different names for some data types in VRT XML
        dtype_map = {
            "uint8": "Byte",
            "int16": "Int16",
            "uint16": "UInt16",
            "int32": "Int32",
            "uint32": "UInt32",
            "float32": "Float32",
            "float64": "Float64",
        }
        gdal_dtype = dtype_map.get(str(dtype), str(dtype))

        # Build XML
        vrt = f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        vrt += f'  <SRS dataAxisToSRSAxisMapping="2,1">{crs}</SRS>\n'
        vrt += f"  <GeoTransform>{transform.c}, {transform.a}, {transform.b}, {transform.f}, {transform.d}, {transform.e}</GeoTransform>\n"

        for i, path in enumerate(paths, 1):
            vrt += f'  <VRTRasterBand dataType="{gdal_dtype}" band="{i}">\n'
            vrt += "    <SimpleSource>\n"
            vrt += f'      <SourceFilename relativeToVRT="0">{os.path.abspath(path)}</SourceFilename>\n'
            vrt += "      <SourceBand>1</SourceBand>\n"
            vrt += f'      <SrcRect xOff="0" yOff="0" xSize="{width}" ySize="{height}" />\n'
            vrt += f'      <DstRect xOff="0" yOff="0" xSize="{width}" ySize="{height}" />\n'
            vrt += "    </SimpleSource>\n"
            vrt += "  </VRTRasterBand>\n"

        vrt += "</VRTDataset>"
        return vrt

    def get_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Get the bounding box of the active product in (min_lat, min_lon, max_lat, max_lon)."""
        if self._active_client:
            try:
                # localtileserver returns (south, north, west, east)
                # which is (min_lat, max_lat, min_lon, max_lon)
                s, n, w, e = self._active_client.bounds()
                return (s, w, n, e)
            except Exception as e:
                logger.error(f"Error getting bounds from tile client: {e}")
        return None


# Global instance
tile_manager = TileManager()
