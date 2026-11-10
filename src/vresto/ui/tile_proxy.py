"""Serve map tiles through the main web server, so a deployment only needs one open port.

Every streamed tile and overlay has its own ``localtileserver`` process-local server on a
loopback port. With the tile proxy on (``VRESTO_TILE_PROXY=1``, or public mode), the pool
hands the map relative ``/_vresto/tiles/<token>/{z}/{x}/{y}.png`` URLs and this route
forwards each request to the matching loopback server. The token only resolves to a server
the pool started, and the file it reads is always the one registered for that token.
"""

from __future__ import annotations

import asyncio
import threading

import requests
from fastapi import Request, Response

from vresto.services.tiles import TILE_PROXY_PREFIX, tile_pool

_ALLOWED_FORMATS = {"png", "jpg", "jpeg", "webp"}
_FORWARDED_HEADERS = ("content-type", "cache-control", "etag", "last-modified")

_TIMEOUT = (5.0, 60.0)  # connect, read

_local = threading.local()
_registered = False


def _http_client() -> requests.Session:
    """Return this thread's loopback HTTP session (no proxy: the tile servers run on this machine)."""
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _local.session = session
    return session


def _fetch(url: str, params: list[tuple[str, str]]) -> requests.Response:
    return _http_client().get(url, params=params, timeout=_TIMEOUT)


async def proxy_tile(token: str, z: int, x: int, y: int, fmt: str, request: Request) -> Response:
    """Forward one tile request to the tile server behind ``token``."""
    target = tile_pool.resolve_proxy_target(token)
    if target is None or fmt.lower() not in _ALLOWED_FORMATS:
        return Response(status_code=404)
    port, filename = target
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "filename"]
    params.append(("filename", filename))
    try:
        upstream = await asyncio.to_thread(_fetch, f"http://127.0.0.1:{port}/api/tiles/{z}/{x}/{y}.{fmt}", params)
    except requests.RequestException:
        # The layer was removed (or its server is still starting); the map retries failed tiles.
        return Response(status_code=502)
    headers = {k: upstream.headers[k] for k in _FORWARDED_HEADERS if k in upstream.headers}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


def register_tile_proxy(app) -> None:
    """Add the tile proxy route to the NiceGUI/FastAPI ``app``; safe to call more than once."""
    global _registered
    if _registered:
        return
    app.add_api_route(f"{TILE_PROXY_PREFIX}/{{token}}/{{z}}/{{x}}/{{y}}.{{fmt}}", proxy_tile, methods=["GET"], include_in_schema=False)
    _registered = True
