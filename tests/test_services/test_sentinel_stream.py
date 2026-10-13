"""Tests for the public (AWS Open Data) branch of SentinelStreamService.

Public streams write to the same cache path as CDSE streams, so the lookups the overlays and the
tile server rely on must find them. Nothing here touches the network.
"""

import os
from unittest.mock import patch

import pytest

from vresto.api.catalog import ProductInfo
from vresto.services.sentinel_stream import SentinelStreamService

VISUAL = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/31/U/FS/2024/1/S2B_31UFS_20240101_0_L2A/TCI.tif"
KEYS = {"COPERNICUS_S3_ACCESS_KEY": "test", "COPERNICUS_S3_SECRET_KEY": "test"}


def _public_product(href=VISUAL):
    return ProductInfo(
        id="S2B_31UFS_20240101_0_L2A",
        name="S2B_MSIL2A_20240101T103329_N0510_R022_T31UFS_20240101T120402.SAFE",
        collection="SENTINEL-2",
        sensing_date="2024-01-01 10:33:29",
        size_mb=0,
        s3_path=href,
        source="earth-search",
    )


@pytest.fixture
def service(tmp_path):
    return SentinelStreamService(cache_root=tmp_path / "cache")


class TestStreamPublicTci:
    def test_writes_to_the_cdse_cache_path_for_the_resolution(self, service):
        with patch("vresto.services.public_s2.stream_visual_cog", return_value="written") as stream:
            result = service.stream_public_tci(_public_product(), "31UFS", "20240101", "60m")

        assert result == "written"
        stream.assert_called_once_with(VISUAL, service.cache_root / "31UFS_20240101_60m_tci.tif", "60m")

    def test_default_resolution_is_the_60m_preview(self, service):
        with patch("vresto.services.public_s2.stream_visual_cog", return_value="written") as stream:
            service.stream_public_tci(_public_product(), "31UFS", "20240101")

        assert stream.call_args.args[2] == "60m"

    def test_cached_file_is_reused_without_a_stream(self, service):
        cached = service.cache_root / "31UFS_20240101_60m_tci.tif"
        cached.write_bytes(b"already here")

        with patch("vresto.services.public_s2.stream_visual_cog") as stream:
            result = service.stream_public_tci(_public_product(), "31UFS", "20240101", "60m")

        assert result == str(cached)
        stream.assert_not_called()

    def test_product_without_a_visual_href_is_not_streamed(self, service):
        with patch("vresto.services.public_s2.stream_visual_cog") as stream:
            result = service.stream_public_tci(_public_product(href=None), "31UFS", "20240101", "60m")

        assert result is None
        stream.assert_not_called()

    def test_the_overlay_and_tile_server_lookups_find_a_public_stream(self, service):
        def _fake_stream(href, cache_path, resolution):
            cache_path.write_bytes(b"cog")
            return str(cache_path)

        with patch("vresto.services.public_s2.stream_visual_cog", side_effect=_fake_stream):
            written = service.stream_public_tci(_public_product(), "31UFS", "20240101", "60m")

        assert service.get_cached_tci_path("31UFS", "20240101", "60m") == written
        assert service.find_any_cached_tci("31UFS", "20240101") == written


class TestPrewarmSkipsPublicSource:
    def test_public_source_does_not_open_a_cdse_connection(self, service):
        env = dict(KEYS, VRESTO_STREAM_SOURCE="public")  # keys exist, but the user chose AWS Open Data
        with patch.dict(os.environ, env, clear=True), patch("boto3.client") as boto_client:
            service.prewarm_s3()

        boto_client.assert_not_called()
        assert service._s3_prewarmed is False

    def test_auto_without_keys_is_public_and_skipped(self, service):
        with patch.dict(os.environ, {}, clear=True), patch("boto3.client") as boto_client:
            service.prewarm_s3()

        boto_client.assert_not_called()

    def test_cdse_source_still_prewarms(self, service):
        env = dict(KEYS, VRESTO_STREAM_SOURCE="cdse")
        # The GDAL probe is stubbed so the test stays offline; the boto3 leg is what is under test.
        with patch.dict(os.environ, env, clear=True), patch("boto3.client") as boto_client, patch("rasterio.open", side_effect=RuntimeError("offline")):
            service.prewarm_s3()

        boto_client.assert_called_once()
        assert service._s3_prewarmed is True
