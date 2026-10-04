import numpy as np
import pytest
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin
from rasterio.warp import transform

from vresto.services.sampling import PointSample, sample_raster, wrap_longitude

UTM_31N = "EPSG:32631"


def _write(path, data, *, crs=UTM_31N, nodata=None, origin=(500000.0, 5600000.0), pixel=60.0):
    """Write a one-band GeoTIFF whose north-west corner is ``origin``."""
    height, width = data.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=data.dtype,
        crs=crs,
        transform=from_origin(origin[0], origin[1], pixel, pixel),
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return str(path)


def _lat_lon(path, row, col, offset=0.5):
    """WGS84 position of a pixel; ``offset`` 0.5 is its centre. ``row``/``col`` may be fractional or outside the raster."""
    with rasterio.open(path) as ds:
        x, y = ds.transform * (col + offset, row + offset)
        lons, lats = transform(ds.crs, "EPSG:4326", [x], [y])
    return lats[0], lons[0]


@pytest.fixture
def grid(tmp_path):
    """A 4x5 uint8 raster holding its own flat index, so every pixel is distinguishable."""
    data = np.arange(20, dtype="uint8").reshape(4, 5)
    return _write(tmp_path / "grid.tif", data), data


def test_returns_the_value_under_the_point(grid):
    path, data = grid
    for row, col in [(0, 0), (1, 2), (2, 3), (3, 4)]:
        assert sample_raster(path, *_lat_lon(path, row, col)) == PointSample("ok", float(data[row, col]))


def test_edge_pixels_are_inside(grid):
    path, data = grid
    height, width = data.shape
    # 0.01 px inside each corner of the raster.
    for row, col in [(0.01, 0.01), (0.01, width - 0.01), (height - 0.01, 0.01), (height - 0.01, width - 0.01)]:
        assert sample_raster(path, *_lat_lon(path, row, col, offset=0)).status == "ok"


@pytest.mark.parametrize(
    ("row", "col"),
    [(-1, 2), (4, 2), (2, -1), (2, 5)],
    ids=["north", "south", "west", "east"],
)
def test_points_beyond_each_edge_are_outside(grid, row, col):
    path, _ = grid
    assert sample_raster(path, *_lat_lon(path, row, col)) == PointSample("outside")


def test_points_far_from_the_raster_are_outside(grid):
    path, _ = grid
    assert sample_raster(path, 0.0, 0.0).status == "outside"
    assert sample_raster(path, -60.0, 120.0).status == "outside"


def test_uint8_nodata_is_masked(tmp_path):
    data = np.array([[1, 255], [2, 3]], dtype="uint8")
    path = _write(tmp_path / "ndvi.tif", data, nodata=255)

    assert sample_raster(path, *_lat_lon(path, 0, 1)) == PointSample("nodata")
    assert sample_raster(path, *_lat_lon(path, 0, 0)) == PointSample("ok", 1.0)


def test_float_nodata_is_masked(tmp_path):
    data = np.array([[14.25, -9999.0], [0.0, -3.5]], dtype="float32")
    path = _write(tmp_path / "lst.tif", data, nodata=-9999.0)

    assert sample_raster(path, *_lat_lon(path, 0, 1)) == PointSample("nodata")
    assert sample_raster(path, *_lat_lon(path, 0, 0)) == PointSample("ok", 14.25)
    assert sample_raster(path, *_lat_lon(path, 1, 1)) == PointSample("ok", -3.5)


def test_zero_is_a_value_not_nodata_unless_declared(tmp_path):
    """Class 0 is real data for some products (water bodies' "sea"); only the declared nodata is masked."""
    data = np.array([[0, 251]], dtype="uint8")
    path = _write(tmp_path / "wb.tif", data, nodata=251)

    assert sample_raster(path, *_lat_lon(path, 0, 0)) == PointSample("ok", 0.0)
    assert sample_raster(path, *_lat_lon(path, 0, 1)) == PointSample("nodata")


def test_nan_without_a_declared_nodata_is_reported_as_nodata(tmp_path):
    path = _write(tmp_path / "nan.tif", np.array([[np.nan, 1.0]], dtype="float32"))

    assert sample_raster(path, *_lat_lon(path, 0, 0)) == PointSample("nodata")
    assert sample_raster(path, *_lat_lon(path, 0, 1)) == PointSample("ok", 1.0)


@pytest.mark.parametrize(
    ("crs", "origin", "pixel"),
    [
        ("EPSG:4326", (4.0, 51.0), 0.01),
        ("EPSG:3035", (3900000.0, 3100000.0), 1000.0),
    ],
    ids=["geographic", "laea-europe"],
)
def test_works_for_non_utm_crs(tmp_path, crs, origin, pixel):
    data = np.arange(12, dtype="uint8").reshape(3, 4)
    path = _write(tmp_path / "other.tif", data, crs=crs, origin=origin, pixel=pixel)

    assert sample_raster(path, *_lat_lon(path, 2, 1)) == PointSample("ok", float(data[2, 1]))
    assert sample_raster(path, *_lat_lon(path, 3, 1)).status == "outside"


@pytest.mark.parametrize(
    ("lon", "wrapped"),
    [(0.0, 0.0), (-180.0, -180.0), (180.0, -180.0), (181.0, -179.0), (-181.0, 179.0), (539.0, 179.0), (-539.0, -179.0)],
)
def test_wrap_longitude(lon, wrapped):
    assert wrap_longitude(lon) == pytest.approx(wrapped)


def test_longitude_is_wrapped_across_the_antimeridian(tmp_path):
    """A raster over -180..-178 must answer for 181 as it does for -179."""
    data = np.arange(8, dtype="uint8").reshape(2, 4)
    path = _write(tmp_path / "pacific.tif", data, crs="EPSG:4326", origin=(-180.0, 10.0), pixel=0.5)

    expected = PointSample("ok", float(data[0, 2]))  # lon -179.0 is the left edge of column 2
    assert sample_raster(path, 9.75, -178.9) == expected
    assert sample_raster(path, 9.75, 181.1) == expected
    assert sample_raster(path, 9.75, 179.0).status == "outside"


def test_raises_for_an_unreadable_raster(tmp_path):
    with pytest.raises(RasterioIOError):
        sample_raster(str(tmp_path / "missing.tif"), 50.0, 3.0)
