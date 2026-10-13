"""Tests for tile streaming without CDSE S3 keys.

Covers where tile candidates come from, how a public product streams, what the streaming
controls say about the source, and how the chooser presents AWS Open Data products.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vresto.api.catalog import ProductInfo
from vresto.services.public_s2 import PUBLIC_ATTRIBUTION
from vresto.ui.widgets.credentials_menu import STREAM_CREDENTIALS_HINT
from vresto.ui.widgets.map_search_tab import MapSearchTab

KEYS = {"COPERNICUS_S3_ACCESS_KEY": "key", "COPERNICUS_S3_SECRET_KEY": "secret"}
VISUAL = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/31/U/FS/2024/1/S2B_31UFS_20240101_0_L2A/TCI.tif"
THUMB = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/31/U/FS/2024/1/S2B_31UFS_20240101_0_L2A/preview.jpg"
PUBLIC_NOTE = "No S3 keys: streaming previews from AWS Open Data (Element84 Earth Search). Add CDSE S3 keys in ☰ → Settings to stream from CDSE."
SERVICE = "vresto.ui.widgets.map_search_tab.sentinel_stream_service"


@pytest.fixture
def mock_ui():
    """Stub NiceGUI's ui in the map search tab; every label is a fresh object so each can be inspected."""
    mock = MagicMock()
    mock.label.side_effect = lambda *args, **kwargs: MagicMock()
    with patch("vresto.ui.widgets.map_search_tab.ui", mock):
        yield mock


def _cdse_product(name="S2A_MSIL2A_20240101T103321_N0510_R022_T31UFS_20240101T120000.SAFE"):
    return ProductInfo(
        id="cdse-id",
        name=name,
        collection="SENTINEL-2",
        sensing_date="2024-01-01 10:33:21",
        size_mb=900.0,
        s3_path=f"/eodata/Sentinel-2/{name}/",
        cloud_cover=8.0,
    )


def _public_product(thumbnail=THUMB):
    return ProductInfo(
        id="S2B_31UFS_20240101_0_L2A",
        name="S2B_MSIL2A_20240101T103329_N0510_R022_T31UFS_20240101T120402.SAFE",
        collection="SENTINEL-2",
        sensing_date="2024-01-01 10:33:29",
        size_mb=0,
        s3_path=VISUAL,
        cloud_cover=12.5,
        source="earth-search",
        thumbnail_url=thumbnail,
    )


def _messages(widget) -> list[str]:
    return [c.args[0] for c in widget._add_message.call_args_list]


class TestPublicCandidates:
    def test_without_keys_candidates_come_from_aws_open_data(self, mock_ui):
        widget = MapSearchTab()
        widget._add_message = MagicMock()
        widget.current_state["date_range"] = {"from": "2024-01-01", "to": "2024-01-31"}
        # A CDSE result cached from an earlier search cannot be streamed without keys, so it must not be offered.
        widget.current_state["products"] = [_cdse_product()]
        public = _public_product()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("vresto.services.public_s2.search_tile", return_value=[public]) as search,
            patch.object(widget, "_search_products_for_tile", new=AsyncMock(return_value=[])) as cdse_search,
        ):
            products = asyncio.run(widget._get_tile_candidates("31UFS", limit=5))

        assert products == [public]
        search.assert_called_once_with("31UFS", "2024-01-01", "2024-01-31", limit=5)
        cdse_search.assert_not_awaited()

    def test_with_keys_cached_cdse_matches_are_used_first(self, mock_ui):
        widget = MapSearchTab()
        widget._add_message = MagicMock()
        cached = _cdse_product()
        widget.current_state["products"] = [cached]

        with patch.dict(os.environ, KEYS, clear=True), patch("vresto.services.public_s2.search_tile") as search:
            products = asyncio.run(widget._get_tile_candidates("31UFS", limit=5))

        assert products == [cached]
        search.assert_not_called()

    def test_public_source_is_used_when_chosen_even_with_keys(self, mock_ui):
        widget = MapSearchTab()
        widget._add_message = MagicMock()
        public = _public_product()

        with patch.dict(os.environ, dict(KEYS, VRESTO_STREAM_SOURCE="public"), clear=True), patch("vresto.services.public_s2.search_tile", return_value=[public]) as search:
            products = asyncio.run(widget._get_tile_candidates("31UFS", limit=5))

        assert products == [public]
        search.assert_called_once()


class TestPublicStreaming:
    @staticmethod
    def _widget() -> MapSearchTab:
        widget = MapSearchTab()
        widget._add_message = MagicMock()
        widget._display_tci_layer = AsyncMock()
        return widget

    def test_public_product_streams_without_any_cdse_call(self, mock_ui):
        widget = self._widget()
        product = _public_product()

        with patch.dict(os.environ, {}, clear=True), patch(SERVICE) as service:
            service.get_cached_tci_path.return_value = None
            service.stream_public_tci.return_value = "/cache/31UFS_20240101_60m_tci.tif"
            streamed = asyncio.run(widget._stream_product("31UFS", product))

        assert streamed is True
        service.stream_public_tci.assert_called_once_with(product, "31UFS", "20240101", "60m")
        service.find_tci_path_in_product.assert_not_called()
        service.stream_tci.assert_not_called()
        widget._display_tci_layer.assert_awaited_once_with("/cache/31UFS_20240101_60m_tci.tif", "31UFS")
        messages = _messages(widget)
        assert f"ℹ️ {PUBLIC_ATTRIBUTION}" in messages
        assert not any(STREAM_CREDENTIALS_HINT in m for m in messages)

    def test_cached_public_tile_still_names_its_source(self, mock_ui):
        widget = self._widget()

        with patch.dict(os.environ, {}, clear=True), patch(SERVICE) as service:
            service.get_cached_tci_path.return_value = "/cache/31UFS_20240101_60m_tci.tif"
            streamed = asyncio.run(widget._stream_product("31UFS", _public_product()))

        assert streamed is True
        service.stream_public_tci.assert_not_called()
        assert f"ℹ️ {PUBLIC_ATTRIBUTION}" in _messages(widget)

    def test_failed_public_stream_says_where_it_failed(self, mock_ui):
        widget = self._widget()

        with patch.dict(os.environ, {}, clear=True), patch(SERVICE) as service:
            service.get_cached_tci_path.return_value = None
            service.stream_public_tci.return_value = None
            streamed = asyncio.run(widget._stream_product("31UFS", _public_product()))

        assert streamed is False
        widget._display_tci_layer.assert_not_awaited()
        assert any("from AWS Open Data" in m for m in _messages(widget))
        mock_ui.notify.assert_any_call("TCI streaming failed", position="top", type="negative")


class TestStreamingControls:
    def test_controls_are_shown_without_keys_and_name_the_source(self, mock_ui):
        widget = MapSearchTab()

        with patch.dict(os.environ, {}, clear=True), patch("vresto.ui.widgets.map_search_tab.mgrs_available", return_value=True):
            widget._create_streaming_controls()

        switch_labels = [c.args[0] for c in mock_ui.switch.call_args_list if c.args]
        assert "Show MGRS Grid" in switch_labels
        assert "Colour tiles by product count" in switch_labels
        assert "High resolution (10 m)" in switch_labels
        widget._stream_source_label.set_text.assert_called_with(PUBLIC_NOTE)
        widget._stream_source_label.set_visibility.assert_called_with(True)

    def test_keys_hide_the_source_note(self, mock_ui):
        widget = MapSearchTab()

        with patch.dict(os.environ, KEYS, clear=True), patch("vresto.ui.widgets.map_search_tab.mgrs_available", return_value=True):
            widget._create_streaming_controls()

        widget._stream_source_label.set_visibility.assert_called_with(False)

    def test_note_follows_keys_added_after_the_controls_were_built(self, mock_ui):
        widget = MapSearchTab()

        with patch.dict(os.environ, {}, clear=True), patch("vresto.ui.widgets.map_search_tab.mgrs_available", return_value=True):
            widget._create_streaming_controls()
            os.environ.update(KEYS)  # added in Settings
            widget._sync_stream_source_note()

        widget._stream_source_label.set_visibility.assert_called_with(False)

    def test_forced_cdse_source_without_keys_asks_for_keys(self, mock_ui):
        widget = MapSearchTab()

        with patch.dict(os.environ, {"VRESTO_STREAM_SOURCE": "cdse"}, clear=True):
            widget._create_streaming_controls()

        widget._stream_source_label.set_text.assert_called_with("Tile streaming needs CDSE S3 keys. Add them in ☰ → Settings.")
        widget._stream_source_label.set_visibility.assert_called_with(True)


class TestPublicChooser:
    def test_public_cards_show_the_thumbnail_and_no_cdse_quicklook(self, mock_ui):
        widget = MapSearchTab(on_quicklook=MagicMock())
        widget.map_widget_obj = MagicMock()
        widget._tile_status_label = MagicMock()
        widget.messages_column = MagicMock()
        cdse = _cdse_product()

        mock_ui.button.reset_mock()
        mock_ui.image.reset_mock()
        widget._show_tile_product_chooser("31UFS", [_public_product(), cdse], previous_tile_code=None)

        assert [c.args[0] for c in mock_ui.image.call_args_list] == [THUMB]
        button_labels = [c.args[0] for c in mock_ui.button.call_args_list if c.args]
        assert button_labels.count("Quicklook") == 1  # the CDSE card keeps its quicklook
        assert button_labels.count("Stream this tile") == 2
        label_texts = [c.args[0] for c in mock_ui.label.call_args_list if c.args]
        assert PUBLIC_ATTRIBUTION in label_texts
        assert "💾 900.0 MB" in label_texts  # the CDSE card still shows its size
        assert not any(text.startswith("💾 0.0") for text in label_texts)  # AWS Open Data cards have no size to show

    def test_public_card_without_thumbnail_shows_no_image(self, mock_ui):
        widget = MapSearchTab()
        widget.map_widget_obj = MagicMock()
        widget._tile_status_label = MagicMock()
        widget.messages_column = MagicMock()

        mock_ui.image.reset_mock()
        widget._show_tile_product_chooser("31UFS", [_public_product(thumbnail=None), _public_product(thumbnail=None)], previous_tile_code=None)

        mock_ui.image.assert_not_called()
