"""Golden-output tests for the overlay services that read lon/lat COGs from S3.

Each service is run end to end on small synthetic source COGs written to a temp
folder (``rasterio.open`` maps ``/vsis3/...`` paths onto that folder), with a UTM
reference raster that straddles tile corners, so tile mosaicking is exercised too.
The aligned and colourised outputs, including their first overview, are compared
against fingerprints recorded from the known-good implementation. A change in
any pixel, the grid, the nodata value or the overviews fails the test.

If an output change is intended (a new palette, say), bump the service's cache
token and re-record the fingerprint with ``VRESTO_PRINT_GOLDEN=1 pytest -s``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_bounds, from_origin  # noqa: E402

CENTER_LON, CENTER_LAT = 6.0, 33.0  # a 3° and 1° tile corner, inside TCD coverage
SOURCE_HALF_DEG = 0.25
SOURCE_RES_DEG = 0.0005
REF_PIXEL_M = 100
REF_SIZE = 128
DATE = "20200627"


def _reference_raster(path: Path) -> str:
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True).transform(CENTER_LON, CENTER_LAT)
    half = REF_PIXEL_M * REF_SIZE / 2
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 3,
        "width": REF_SIZE,
        "height": REF_SIZE,
        "crs": "EPSG:32632",
        "transform": from_origin(x - half, y + half, REF_PIXEL_M, REF_PIXEL_M),
        "tiled": True,
        "blockxsize": 64,
        "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((3, REF_SIZE, REF_SIZE), 128, dtype=np.uint8))
    return str(path)


def _write_source(path: Path, extent: tuple[float, float, float, float], values, dtype: str, nodata) -> None:
    """Write a lon/lat GeoTIFF with overviews; ``values(rows, cols)`` gives the pixel values from global pixel indices."""
    left, bottom, right, top = extent
    left = max(left, CENTER_LON - SOURCE_HALF_DEG)
    right = min(right, CENTER_LON + SOURCE_HALF_DEG)
    bottom = max(bottom, CENTER_LAT - SOURCE_HALF_DEG)
    top = min(top, CENTER_LAT + SOURCE_HALF_DEG)
    w = int(round((right - left) / SOURCE_RES_DEG))
    h = int(round((top - bottom) / SOURCE_RES_DEG))
    # Global indices make the pattern continuous across neighbouring tiles.
    rows = np.arange(h)[:, None] + int(round((90 - top) / SOURCE_RES_DEG))
    cols = np.arange(w)[None, :] + int(round((left + 180) / SOURCE_RES_DEG))
    data = np.asarray(values(rows, cols)).astype(dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "dtype": dtype,
        "count": 1,
        "width": w,
        "height": h,
        "crs": "EPSG:4326",
        "transform": from_bounds(left, bottom, right, top, w, h),
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        dst.build_overviews([2, 4], rasterio.enums.Resampling.nearest)


def _classes(class_ids, nodata):
    ids = np.array(list(class_ids) + [nodata], dtype=np.int64)
    return lambda rows, cols: ids[(rows // 37 + cols // 23) % len(ids)]


def _tiles(step: int):
    """SW corners of the ``step``° tiles around the centre."""
    lat0 = int(CENTER_LAT // step) * step
    lon0 = int(CENTER_LON // step) * step
    return [(lat, lon) for lat in (lat0 - step, lat0) for lon in (lon0 - step, lon0)]


def _fingerprint(path: str) -> str:
    digest = hashlib.sha256()
    with rasterio.open(path) as src:
        digest.update(repr((src.count, src.dtypes, src.nodata, src.width, src.height, src.crs.to_epsg(), tuple(round(v, 6) for v in src.transform), src.overviews(1))).encode())
        digest.update(src.read().tobytes())
    with rasterio.open(path, overview_level=0) as ovr:
        digest.update(ovr.read().tobytes())
    return digest.hexdigest()[:16]


def _setup_worldcover(root, resolution_m=20):
    from vresto.services.worldcover import WORLDCOVER_CLASSES, WorldCoverService

    svc = WorldCoverService(cache_root=root / "cache")
    for lat, lon in _tiles(3):
        code = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
        _write_source(_local(root, svc._vsis3_path(code, "2021")), (lon, lat, lon + 3, lat + 3), _classes(WORLDCOVER_CLASSES, 0), "uint8", 0)
    return svc, lambda ref: (svc.get_aligned_path(ref, resolution_m, "2021"), svc.get_colorized_path(ref, resolution_m, "2021"))


def _setup_lcm(root):
    from vresto.services.lcm import LCM_CLASSES, LCMService

    svc = LCMService(cache_root=root / "cache")
    for lat, lon in _tiles(3):
        code = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
        _write_source(_local(root, svc._vsis3_path(code, "2020")), (lon, lat, lon + 3, lat + 3), _classes(LCM_CLASSES, 255), "uint8", 255)
    return svc, lambda ref: (svc.get_aligned_path(ref, 20, "2020"), svc.get_colorized_path(ref, 20, "2020"))


def _setup_tcd(root):
    from vresto.services.tcd import TCD_CLASSES, TCDService

    svc = TCDService(cache_root=root / "cache")
    for lat, lon in _tiles(3):
        code = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
        _write_source(_local(root, svc._vsis3_path(code, "2020")), (lon, lat, lon + 3, lat + 3), _classes(TCD_CLASSES, 255), "uint8", 255)
    return svc, lambda ref: (svc.get_aligned_path(ref, 20, "2020"), svc.get_colorized_path(ref, 20, "2020"))


def _setup_dem(root):
    from vresto.services.dem import DEMService

    svc = DEMService(cache_root=root / "cache")

    def elevation(rows, cols):
        z = 200.0 + 150.0 * np.sin(rows / 41.0) * np.cos(cols / 29.0) + (rows % 7) * 3.0
        return np.where((rows // 61 + cols // 53) % 11 == 0, -32767.0, z)

    for lat, lon in _tiles(1):
        _write_source(_local(root, svc._vsis3_path(lat, lon)), (lon, lat, lon + 1, lat + 1), elevation, "float32", -32767.0)
    return svc, lambda ref: (svc.get_aligned_path(ref, 60), svc.get_colorized_path(ref, 60))


def _setup_lc100(root):
    from vresto.services.lc100 import LC100_CLASSES, LC100Service

    svc = LC100Service(cache_root=root / "cache")
    _write_source(_local(root, svc._vsis3_path("2019")), (-180, -90, 180, 90), _classes(list(LC100_CLASSES) + [0], 255), "uint8", 255)
    return svc, lambda ref: (svc.get_aligned_path(ref, 20, "2019"), svc.get_colorized_path(ref, 20, "2019"))


def _setup_ndvi(root):
    from vresto.services.ndvi import NDVIService

    svc = NDVIService(cache_root=root / "cache")
    _write_source(_local(root, svc._vsis3_path("06", "21")), (-180, -90, 180, 90), lambda rows, cols: (rows * 3 + cols * 5) % 256, "uint8", 255)
    return svc, lambda ref: (svc.get_aligned_path(ref, 1000, DATE), svc.get_colorized_path(ref, 1000, DATE))


def _local(root: Path, vsis3_path: str) -> Path:
    return root / "s3" / vsis3_path.removeprefix("/vsis3/")


SETUPS = {
    "worldcover": _setup_worldcover,
    # Coarse enough that the source read comes from a COG overview.
    "worldcover_200m": lambda root: _setup_worldcover(root, 200),
    "lcm": _setup_lcm,
    "tcd": _setup_tcd,
    "dem": _setup_dem,
    "lc100": _setup_lc100,
    "ndvi": _setup_ndvi,
}

# (aligned, colorized) fingerprints recorded from the implementation before the shared-base refactor.
GOLDEN = {
    "worldcover": ("f48e70863484f1a7", "9660d39d34ca62cc"),
    "worldcover_200m": ("fd7ffa2e955d6a0c", "78ef5dc11f994796"),
    "lcm": ("1350b2b794f62e8a", "679580fc20d0ad4b"),
    "tcd": ("46828896b5ca5a6d", "29ff5216f1595edc"),
    "dem": ("aa6cd8cbcaac7a41", "9916ebfdebb43b6d"),
    "lc100": ("28299c3280ff188e", "7ced97c53b2b1224"),
    "ndvi": ("47ea476ca96709c4", "6586ea9aadd4d5aa"),
}


@pytest.fixture
def local_vsis3(tmp_path, monkeypatch):
    """Serve ``/vsis3/<bucket>/<key>`` from ``tmp_path/s3/<bucket>/<key>`` and keep GDAL off the network."""
    real_open = rasterio.open

    def fake_open(fp, *args, **kwargs):
        if isinstance(fp, str) and fp.startswith("/vsis3/"):
            fp = str(_local(tmp_path, fp))
        return real_open(fp, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", fake_open)
    return tmp_path


@pytest.mark.parametrize("name", list(SETUPS))
def test_overlay_output_matches_golden(name, local_vsis3):
    ref = _reference_raster(local_vsis3 / "ref.tif")
    _, run = SETUPS[name](local_vsis3)

    aligned, colorized = run(ref)

    assert aligned and os.path.exists(aligned)
    assert colorized and os.path.exists(colorized)
    with rasterio.open(colorized) as src:
        alpha = src.read(4)
    assert 0 < np.count_nonzero(alpha) < alpha.size, "expected both coloured and transparent pixels"

    got = (_fingerprint(aligned), _fingerprint(colorized))
    if os.environ.get("VRESTO_PRINT_GOLDEN"):
        print(f'\n    "{name}": {got!r},')
    assert got == GOLDEN[name]


@pytest.mark.parametrize("name", list(SETUPS))
def test_overlay_reuses_cached_output(name, local_vsis3):
    ref = _reference_raster(local_vsis3 / "ref.tif")
    _, run = SETUPS[name](local_vsis3)
    first = run(ref)

    second = run(ref)

    assert second == first
