"""Tile management service for high-resolution product visualization.

This module provides a manager to handle local tile server instances using localtileserver,
allowing high-resolution Sentinel-2 bands to be served as map layers.

Port handling strategy
----------------------
- **Local / Terrascope VM**: The tile server is started on a random free port (``port=0``).
  ``localtileserver`` picks an available port and builds URLs from that actual port.  Nothing
  needs to be forwarded; the browser talks directly to the local process.

- **Docker**: The container must expose a *fixed* port (set via the ``VRESTO_BASE_TILE_PORT``
  environment variable, default ``8611``) so that Docker's port-forwarding can map traffic from
  the host into the container.  In this case we pass ``port=<fixed>`` and bind on ``0.0.0.0``
  because the tile server must accept connections that arrive through the Docker bridge.
  The URL produced by ``localtileserver`` will reference that fixed port, which the host browser
  can reach through ``-p 8611:8611`` (or equivalent).
"""

from __future__ import annotations

import collections
import os
import tempfile
import threading
from typing import Dict, List, Optional, Tuple

from loguru import logger

try:
    from localtileserver import TileClient

    HAS_TILESERVER = True
except ImportError:
    HAS_TILESERVER = False
    logger.warning("localtileserver not installed. High-res visualization will be unavailable.")


class TilePool:
    """Manages a pool of named TileClient instances with LRU eviction.

    Supports up to MAX_CLIENTS concurrent tile layers.  When the pool is full,
    the least-recently-used client is evicted and shut down.

    MAX_CLIENTS = 12 keeps a session's worth of recent clicks live so that
    re-clicking a tile is instant (URL already cached in ``_urls``). Each
    TileClient holds ~50 MB RAM, one port and one thread.
    """

    MAX_CLIENTS = 12

    def __init__(self):
        self._clients: collections.OrderedDict[str, TileClient] = collections.OrderedDict()
        self._urls: Dict[str, str] = {}
        self._vrts: Dict[str, str] = {}  # name -> temp VRT path
        self._lock = threading.Lock()
        self._next_docker_port = int(os.getenv("VRESTO_BASE_TILE_PORT", "8611"))

    def is_available(self) -> bool:
        """Check if localtileserver is installed and available."""
        return HAS_TILESERVER

    def get_or_create(
        self,
        name: str,
        path: str | List[str],
        port: int = 0,
        palette: Optional[str | List[str]] = None,
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
        nodata: Optional[int] = None,
        external_host: Optional[str] = None,
    ) -> Optional[str]:
        """Get an existing tile URL by name, or create a new TileClient.

        If the pool is full, the least-recently-used entry is evicted.

        Returns:
            Tile URL template or None on failure.
        """
        if not HAS_TILESERVER:
            logger.error("Cannot get tile URL: localtileserver not installed")
            return None

        evicted = []
        with self._lock:
            # If already exists, move to end (most-recently-used) and return URL
            if name in self._clients:
                self._clients.move_to_end(name)
                return self._urls.get(name)

            # Evict LRU if at capacity
            while len(self._clients) >= self.MAX_CLIENTS:
                evict_name, evict_client = self._clients.popitem(last=False)
                evicted.append((evict_name, evict_client))

        # Shut down evicted clients outside the lock
        for evict_name, evict_client in evicted:
            self._shutdown_client_obj(evict_name, evict_client)

        # Build the client outside the lock (network I/O)
        url = self._create_client(name, path, port, palette, min_val, max_val, nodata, external_host)
        return url

    def remove(self, name: str) -> None:
        """Shut down and remove a named tile client."""
        client = None
        with self._lock:
            if name in self._clients:
                client = self._clients.pop(name)
        # Shut down outside the lock to avoid deadlock
        if client:
            self._shutdown_client_obj(name, client)

    def shutdown_all(self) -> None:
        """Shut down all active tile clients."""
        clients_to_shutdown = []
        with self._lock:
            for name, client in self._clients.items():
                clients_to_shutdown.append((name, client))
            self._clients.clear()
        for name, client in clients_to_shutdown:
            self._shutdown_client_obj(name, client)
        logger.info("TilePool: all clients shut down")

    def get_bounds(self, name: str) -> Optional[Tuple[float, float, float, float]]:
        """Get bounds of a named client as (min_lat, min_lon, max_lat, max_lon)."""
        with self._lock:
            client = self._clients.get(name)
        if client:
            try:
                bounds = client.bounds()
                # TileClient.bounds() returns (south, north, west, east)
                return (bounds[0], bounds[2], bounds[1], bounds[3])
            except Exception:
                return None
        return None

    @property
    def active_count(self) -> int:
        """Number of active tile clients."""
        return len(self._clients)

    def _create_client(
        self,
        name: str,
        path: str | List[str],
        port: int,
        palette: Optional[str | List[str]],
        min_val: Optional[int],
        max_val: Optional[int],
        nodata: Optional[int],
        external_host: Optional[str],
    ) -> Optional[str]:
        """Create a TileClient and register it in the pool."""
        # Validate path
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
            actual_path = path
            if isinstance(path, list):
                actual_path = self._create_vrt(name, path)
                if not actual_path:
                    return None

            logger.info(f"TilePool: starting tile server '{name}' for {actual_path}")

            in_docker = os.path.exists("/.dockerenv")

            if in_docker:
                bind_host = "0.0.0.0"
                bind_port = port if port else self._allocate_docker_port()
            else:
                bind_host = "127.0.0.1"
                bind_port = 0

            client_host = external_host if external_host and external_host.lower() != "auto" else None

            kwargs: dict = {"host": bind_host, "cors_all": True, "port": bind_port}

            if in_docker:
                kwargs["client_host"] = client_host or "localhost"
                kwargs["client_port"] = bind_port
            elif client_host:
                kwargs["client_host"] = client_host

            client = TileClient(actual_path, **kwargs)

            url = client.get_tile_url(client=in_docker)
            if url:
                import time
                import urllib.parse

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
                url += f"{separator}t={int(time.time())}"

            with self._lock:
                self._clients[name] = client
                self._clients.move_to_end(name)
                self._urls[name] = url

            logger.info(f"TilePool: '{name}' started at {url}")
            return url

        except Exception as e:
            logger.exception(f"TilePool: failed to start '{name}' for {path}: {e}")
            return None

    def _shutdown_client_obj(self, name: str, client) -> None:
        """Shut down a single client object (caller must have already removed from _clients)."""
        self._urls.pop(name, None)
        vrt = self._vrts.pop(name, None)

        if client:
            try:
                if hasattr(client, "shutdown"):
                    try:
                        client.shutdown(quiet=True)
                    except Exception:
                        client.shutdown()
                try:
                    from server_thread.server import ServerManager
                    if hasattr(client, "_key"):
                        ServerManager.shutdown_server(client._key, force=True)
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"TilePool: error shutting down '{name}': {e}")
            logger.info(f"TilePool: shut down '{name}'")

        if vrt and os.path.exists(vrt):
            try:
                os.remove(vrt)
            except Exception as e:
                logger.warning(f"TilePool: error removing VRT for '{name}': {e}")

    def _allocate_docker_port(self) -> int:
        """Allocate the next Docker port from the range."""
        port = self._next_docker_port
        self._next_docker_port += 1
        return port

    def _create_vrt(self, name: str, paths: List[str]) -> Optional[str]:
        """Create a temporary VRT from a list of band paths."""
        try:
            import rasterio

            fd, vrt_path = tempfile.mkstemp(suffix=".vrt")
            os.close(fd)

            for p in paths:
                if not os.path.exists(p):
                    logger.error(f"Band file not found: {p}")
                    return None

            srcs = [rasterio.open(p) for p in paths]
            vrt_content = _generate_vrt_xml(paths)
            with open(vrt_path, "w") as f:
                f.write(vrt_content)
            for s in srcs:
                s.close()

            self._vrts[name] = vrt_path
            return vrt_path

        except Exception as e:
            logger.exception(f"TilePool: failed to create VRT for '{name}': {e}")
            return None


# Module-level singleton pool instance
tile_pool = TilePool()


class TileManager:
    """Manages local tile server instances for product visualization.

    Thin wrapper around TilePool that maintains backward-compatible single-client semantics.
    Each TileManager instance uses a unique pool name so multiple managers can coexist.
    """

    _instance_counter = 0

    def __init__(self, pool_name: Optional[str] = None):
        TileManager._instance_counter += 1
        self._pool_name = pool_name or f"tile_mgr_{TileManager._instance_counter}"
        self._active_path: Optional[str] = None

    def is_available(self) -> bool:
        """Check if localtileserver is installed and available."""
        return tile_pool.is_available()

    def get_tile_url(
        self,
        path: str | List[str],
        port: int = 0,
        palette: Optional[str | List[str]] = None,
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
        nodata: Optional[int] = None,
        external_host: Optional[str] = None,
    ) -> Optional[str]:
        """Start a tile server for the given file(s) and return the tile URL.

        If a list of paths is provided, a temporary VRT will be created.

        Args:
            path: Path to the GeoTIFF or JP2 file, or list of paths.
            port: Preferred port for the server.
                  In Docker this should be the forwarded port (e.g. 8611).
                  Outside Docker pass 0 (default) and the OS picks a free port.
            palette: Optional palette name or list of colors.
            min_val: Minimum value for scaling.
            max_val: Maximum value for scaling.
            nodata: Nodata value.
            external_host: Optional hostname to use in returned URLs (e.g., for Docker).
                           If "auto", the request Host header will be used.

        Returns:
            The tile URL template (e.g., 'http://localhost:PORT/tiles/{z}/{x}/{y}.png?...')
            or None if starting the server fails.
        """
        # Shut down previous before creating new (single-client semantics)
        self.shutdown()
        self._active_path = path
        return tile_pool.get_or_create(
            self._pool_name, path,
            port=port, palette=palette, min_val=min_val,
            max_val=max_val, nodata=nodata, external_host=external_host,
        )

    def shutdown(self):
        """Shutdown the active tile server client."""
        tile_pool.remove(self._pool_name)
        self._active_path = None

    def get_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Get current active client bounds as (min_lat, min_lon, max_lat, max_lon)."""
        with tile_pool._lock:
            client = tile_pool._clients.get(self._pool_name)
        if client:
            try:
                bounds = client.bounds()
                # TileClient.bounds() returns (south, north, west, east)
                return (bounds[0], bounds[2], bounds[1], bounds[3])
            except Exception:
                return None
        return None


def _generate_vrt_xml(paths: List[str]) -> str:
    """Generate a basic VRT XML to stack multiple files as bands."""
    import rasterio

    with rasterio.open(paths[0]) as src:
        width = src.width
        height = src.height
        crs = src.crs.to_wkt()
        transform = src.transform
        dtype = src.dtypes[0]

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


# Global instance
tile_manager = TileManager()
