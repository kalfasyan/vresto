"""Tests for the AWS Open Data (Earth Search) fallback: search, COG streaming and source selection.

The sandbox has no network, so the Earth Search client is replaced by a scripted fake. The COG
writer runs for real on a small GeoTIFF written to tmp_path; only the /vsicurl/ path is swapped
for that local file.
"""

import os
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
import requests
from pystac_client.exceptions import APIError
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin

from vresto.api.catalog import ProductInfo
from vresto.services import public_s2
from vresto.services.sentinel_stream import _GDAL_TUNING_DEFAULTS

VISUAL = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/31/U/FS/2024/1/S2B_31UFS_20240101_0_L2A/TCI.tif"
THUMB = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/31/U/FS/2024/1/S2B_31UFS_20240101_0_L2A/preview.jpg"
KEYS = {"COPERNICUS_S3_ACCESS_KEY": "key", "COPERNICUS_S3_SECRET_KEY": "secret"}


def _item(
    item_id="S2B_31UFS_20240101_0_L2A",
    datetime_="2024-01-01T10:33:29.024000Z",
    cloud=12.5,
    product_uri="S2B_MSIL2A_20240101T103329_N0510_R022_T31UFS_20240101T120402.SAFE",
    visual=VISUAL,
    thumbnail=THUMB,
):
    """A STAC item dict shaped like the Earth Search items the client returns."""
    assets = {}
    if visual is not None:
        assets["visual"] = {"href": visual}
    if thumbnail is not None:
        assets["thumbnail"] = {"href": thumbnail}
    props = {"datetime": datetime_, "eo:cloud_cover": cloud}
    if product_uri is not None:
        props["s2:product_uri"] = product_uri
    return {"id": item_id, "properties": props, "assets": assets}


class _FakeSearch:
    def __init__(self, outcome):
        self._outcome = outcome

    def items_as_dicts(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return iter(self._outcome)


class _FakeClient:
    """Stands in for a pystac-client Client: each search call takes the next scripted outcome."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeSearch(self.outcomes.pop(0))


def _rejected(status: int) -> APIError:
    error = APIError("rejected")
    error.status_code = status
    return error


class TestSearchTile:
    def test_maps_an_item_to_a_public_product(self):
        client = _FakeClient([_item()])
        with patch.object(public_s2, "_get_client", return_value=client):
            (product,) = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert product == ProductInfo(
            id="S2B_31UFS_20240101_0_L2A",
            name="S2B_MSIL2A_20240101T103329_N0510_R022_T31UFS_20240101T120402.SAFE",
            collection="SENTINEL-2",
            sensing_date="2024-01-01 10:33:29",
            size_mb=0,
            s3_path=VISUAL,
            cloud_cover=12.5,
            source="earth-search",
            thumbnail_url=THUMB,
        )

    def test_name_falls_back_to_the_item_id_without_a_product_uri(self):
        client = _FakeClient([_item(product_uri=None)])
        with patch.object(public_s2, "_get_client", return_value=client):
            (product,) = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert product.name == "S2B_31UFS_20240101_0_L2A"

    def test_items_without_a_visual_asset_are_skipped(self):
        client = _FakeClient([_item(item_id="no-visual", visual=None), _item(item_id="kept", product_uri="P")])
        with patch.object(public_s2, "_get_client", return_value=client):
            products = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert [p.id for p in products] == ["kept"]

    def test_missing_thumbnail_leaves_thumbnail_url_empty(self):
        client = _FakeClient([_item(thumbnail=None)])
        with patch.object(public_s2, "_get_client", return_value=client):
            (product,) = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert product.thumbnail_url is None
        assert product.s3_path == VISUAL

    def test_sensing_date_is_utc_in_the_cdse_format(self):
        client = _FakeClient([_item(datetime_="2024-03-05T23:59:00+01:00")])
        with patch.object(public_s2, "_get_client", return_value=client):
            (product,) = public_s2.search_tile("31UFS", "2024-03-01", "2024-03-31")

        assert product.sensing_date == "2024-03-05 22:59:00"

    @pytest.mark.parametrize("tile_code", ["31UFS", "T31UFS"])
    def test_query_targets_the_tile_grid_code_and_cloud_cover(self, tile_code):
        client = _FakeClient([])
        with patch.object(public_s2, "_get_client", return_value=client):
            public_s2.search_tile(tile_code, "2024-01-05", "2024-01-09", limit=3, max_cloud=20.0)

        call = client.calls[0]
        assert call["collections"] == ["sentinel-2-c1-l2a"]
        assert call["datetime"] == "2024-01-05T00:00:00Z/2024-01-09T23:59:59Z"
        assert call["query"] == {"grid:code": {"eq": "MGRS-31UFS"}, "eo:cloud_cover": {"lte": 20.0}}
        assert call["sortby"] == [{"field": "properties.datetime", "direction": "desc"}]
        assert call["max_items"] == 3

    def test_newest_first_and_capped_at_the_limit(self):
        items = [_item(item_id=f"i{day}", datetime_=f"2024-01-{day:02d}T10:00:00Z", product_uri=f"P{day}") for day in (3, 10, 1, 7)]
        client = _FakeClient(items)
        with patch.object(public_s2, "_get_client", return_value=client):
            products = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31", limit=2)

        assert [p.name for p in products] == ["P10", "P7"]

    def test_rejected_sortby_is_retried_without_it_and_sorted_here(self):
        oldest_first = [
            _item(item_id="a", datetime_="2024-01-01T10:00:00Z", product_uri="A"),
            _item(item_id="b", datetime_="2024-01-09T10:00:00Z", product_uri="B"),
            _item(item_id="c", datetime_="2024-01-05T10:00:00Z", product_uri="C"),
        ]
        client = _FakeClient(_rejected(400), oldest_first)
        with patch.object(public_s2, "_get_client", return_value=client):
            products = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31", limit=2)

        assert [p.name for p in products] == ["B", "C"]
        assert len(client.calls) == 2
        assert "sortby" not in client.calls[1]
        assert client.calls[1]["max_items"] == public_s2._UNSORTED_MAX_ITEMS

    def test_server_error_is_not_retried(self):
        client = _FakeClient(_rejected(503))
        with patch.object(public_s2, "_get_client", return_value=client):
            products = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert products == []
        assert len(client.calls) == 1

    def test_network_failure_returns_no_products(self):
        client = _FakeClient(requests.ConnectionError("unreachable"))
        with patch.object(public_s2, "_get_client", return_value=client):
            products = public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31")

        assert products == []
        assert len(client.calls) == 1

    def test_client_that_cannot_open_returns_no_products(self):
        with patch.object(public_s2, "_get_client", side_effect=OSError("dns failure")):
            assert public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31") == []

    def test_zero_limit_makes_no_request(self):
        with patch.object(public_s2, "_get_client") as get_client:
            assert public_s2.search_tile("31UFS", "2024-01-01", "2024-01-31", limit=0) == []

        get_client.assert_not_called()


class TestClient:
    def test_client_is_opened_once_with_a_timeout(self, monkeypatch):
        monkeypatch.setattr(public_s2, "_client", None)
        sentinel = object()
        with patch("pystac_client.Client.open", return_value=sentinel) as open_client:
            first = public_s2._get_client()
            second = public_s2._get_client()

        assert first is second is sentinel
        open_client.assert_called_once_with(public_s2.EARTH_SEARCH_URL, timeout=public_s2.REQUEST_TIMEOUT_S)

    def test_failed_open_is_not_remembered(self, monkeypatch):
        monkeypatch.setattr(public_s2, "_client", None)
        sentinel = object()
        with patch("pystac_client.Client.open", side_effect=[OSError("down"), sentinel]):
            with pytest.raises(OSError):
                public_s2._get_client()
            assert public_s2._get_client() is sentinel


class TestStreamSource:
    @pytest.mark.parametrize(
        ("setting", "with_keys", "expected"),
        [
            (None, False, "public"),
            (None, True, "cdse"),
            ("auto", False, "public"),
            ("auto", True, "cdse"),
            ("public", True, "public"),
            ("cdse", False, "cdse"),
            (" CDSE ", False, "cdse"),
            ("bogus", False, "public"),
            ("bogus", True, "cdse"),
        ],
    )
    def test_source_for_each_setting(self, setting, with_keys, expected):
        env = dict(KEYS) if with_keys else {}
        if setting is not None:
            env["VRESTO_STREAM_SOURCE"] = setting
        with patch.dict(os.environ, env, clear=True):
            assert public_s2.stream_source() == expected

    def test_keys_added_after_start_switch_the_source_without_a_restart(self):
        with patch.dict(os.environ, {}, clear=True):
            assert public_s2.stream_source() == "public"
            os.environ.update(KEYS)
            assert public_s2.stream_source() == "cdse"


class TestVsicurlUrl:
    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            (VISUAL, f"/vsicurl/{VISUAL}"),
            ("s3://sentinel-cogs/a/TCI.tif", "/vsicurl/https://sentinel-cogs.s3.amazonaws.com/a/TCI.tif"),
        ],
    )
    def test_converts_hrefs_for_gdal(self, href, expected):
        assert public_s2._vsicurl_url(href) == expected

    @pytest.mark.parametrize("href", ["http://insecure.example/TCI.tif", "s3://bucket-only", "s3://", "TCI.tif", ""])
    def test_refuses_other_hrefs(self, href):
        with pytest.raises(ValueError):
            public_s2._vsicurl_url(href)

    def test_public_reads_keep_the_cdse_gdal_settings(self):
        # rasterio.Env sets these for the whole process, so they must match the CDSE tuning defaults.
        assert "AWS_NO_SIGN_REQUEST" not in public_s2._PUBLIC_GDAL_OPTIONS
        for key, value in public_s2._PUBLIC_GDAL_OPTIONS.items():
            assert value == _GDAL_TUNING_DEFAULTS[key]


def _write_visual_cog(path, size=600, nodata=None):
    """A 10 m, three-band uint8 GeoTIFF with internal overviews, filled with one value per band."""
    data = np.empty((3, size, size), dtype="uint8")
    data[0], data[1], data[2] = 50, 100, 150
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 3,
        "width": size,
        "height": size,
        "crs": CRS.from_epsg(32631),
        "transform": from_origin(300000, 5600000, 10, 10),
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    with rasterio.open(path, "r+") as dst:
        dst.build_overviews([2, 4, 8], Resampling.average)
    return path


class TestStreamVisualCog:
    @pytest.fixture
    def source(self, tmp_path, monkeypatch):
        """A local COG standing in for the remote visual asset."""
        path = _write_visual_cog(tmp_path / "TCI.tif")
        monkeypatch.setattr(public_s2, "_vsicurl_url", lambda href: str(path))
        return path

    @pytest.fixture
    def cache_dir(self, tmp_path):
        directory = tmp_path / "cache"
        directory.mkdir()
        return directory

    @pytest.mark.parametrize(
        ("resolution", "size", "pixel"),
        [("60m", 100, 60.0), ("20m", 300, 20.0), ("10m", 600, 10.0)],
    )
    def test_resolution_sets_the_output_size_and_pixel(self, source, cache_dir, resolution, size, pixel):
        cache = cache_dir / f"31UFS_20240101_{resolution}_tci.tif"

        result = public_s2.stream_visual_cog("https://example.test/TCI.tif", cache, resolution)

        assert result == str(cache)
        with rasterio.open(cache) as out:
            assert (out.width, out.height) == (size, size)
            assert out.res == (pixel, pixel)
            assert out.count == 3
            assert out.dtypes == ("uint8", "uint8", "uint8")
            assert out.crs == CRS.from_epsg(32631)
            assert out.bounds == pytest.approx(rasterio.open(source).bounds)
            assert out.overviews(1)  # internal overviews are built, as for CDSE streams
            assert out.profile["compress"] == "deflate"
            assert out.profile["tiled"]

    def test_downsampled_bands_keep_their_values(self, source, cache_dir):
        cache = cache_dir / "31UFS_20240101_60m_tci.tif"

        public_s2.stream_visual_cog("https://example.test/TCI.tif", cache, "60m")

        with rasterio.open(cache) as out:
            data = out.read()
        assert (data[0] == 50).all()
        assert (data[1] == 100).all()
        assert (data[2] == 150).all()

    def test_source_nodata_is_kept(self, tmp_path, monkeypatch, cache_dir):
        source = _write_visual_cog(tmp_path / "with_nodata.tif", nodata=0)
        monkeypatch.setattr(public_s2, "_vsicurl_url", lambda href: str(source))
        cache = cache_dir / "31UFS_20240101_60m_tci.tif"

        public_s2.stream_visual_cog("https://example.test/TCI.tif", cache, "60m")

        with rasterio.open(cache) as out:
            assert out.nodata == 0

    def test_unreadable_source_returns_none_and_leaves_no_file(self, tmp_path, monkeypatch, cache_dir):
        monkeypatch.setattr(public_s2, "_vsicurl_url", lambda href: str(tmp_path / "missing.tif"))

        result = public_s2.stream_visual_cog("https://example.test/TCI.tif", cache_dir / "31UFS_20240101_60m_tci.tif", "60m")

        assert result is None
        assert list(cache_dir.iterdir()) == []

    def test_failure_after_the_write_leaves_no_partial_cache_file(self, source, cache_dir):
        cache = cache_dir / "31UFS_20240101_60m_tci.tif"

        with patch.object(public_s2, "build_overviews", side_effect=RuntimeError("disk full")):
            result = public_s2.stream_visual_cog("https://example.test/TCI.tif", cache, "60m")

        assert result is None
        assert not cache.exists()
        assert list(cache_dir.iterdir()) == []

    def test_bad_href_returns_none(self, cache_dir):
        result = public_s2.stream_visual_cog("http://insecure.example/TCI.tif", cache_dir / "x_60m_tci.tif", "60m")

        assert result is None
        assert list(cache_dir.iterdir()) == []

    def test_unsupported_resolution_returns_none(self, source, cache_dir):
        result = public_s2.stream_visual_cog("https://example.test/TCI.tif", cache_dir / "x_5m_tci.tif", "5m")

        assert result is None
        assert list(cache_dir.iterdir()) == []
