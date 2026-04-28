"""Unit tests for catalog providers (OData and STAC)."""

from unittest.mock import Mock, patch

import pytest

from vresto.api.catalog import (
    BoundingBox,
    CatalogSearch,
    ODataCatalogSearch,
    STACCatalogSearch,
)
from vresto.api.config import CopernicusConfig


class TestODataCatalogSearch:
    """Tests for ODataCatalogSearch provider."""

    @pytest.fixture
    def config(self):
        return CopernicusConfig(search_provider="odata")

    @pytest.fixture
    def provider(self, config):
        with patch("vresto.api.catalog.CopernicusAuth"):
            return ODataCatalogSearch(config=config)

    def test_search_products_odata(self, provider):
        """Test OData search builds correct request."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "Id": "odata-1",
                    "Name": "S2A_MSIL2A_20240101",
                    "Collection": {"Name": "SENTINEL-2"},
                    "ContentDate": {"Start": "2024-01-01T10:00:00Z"},
                    "ContentLength": 1048576,
                    "Attributes": [],
                }
            ]
        }

        with patch("requests.get", return_value=mock_response) as mock_get:
            products = provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2", product_level="L2A")

            assert len(products) == 1
            assert products[0].id == "odata-1"
            assert "contains(Name, 'MSIL2A')" in mock_get.call_args[1]["params"]["$filter"]

    @pytest.mark.parametrize("level", ["GRD", "SLC", "RAW", "OCN"])
    def test_search_sentinel1_odata_level_filter(self, provider, level):
        """Test OData filter for each SENTINEL-1 product level."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response) as mock_get:
            provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-1", product_level=level)
            filter_str = mock_get.call_args[1]["params"]["$filter"]
            # GRD products use GRDH/GRDM naming, so match on prefix '_GRD' (no trailing _)
            if level == "GRD":
                assert "contains(Name, '_GRD')" in filter_str
            else:
                assert f"contains(Name, '_{level}_')" in filter_str

    @pytest.mark.parametrize("level", ["L1B", "L2"])
    def test_search_sentinel5p_odata_level_filter(self, provider, level):
        """Test OData filter for each SENTINEL-5P product level."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response) as mock_get:
            provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-5P", product_level=level)
            filter_str = mock_get.call_args[1]["params"]["$filter"]
            assert f"contains(Name, '_{level}_')" in filter_str


class TestSTACCatalogSearch:
    """Tests for STACCatalogSearch provider."""

    @pytest.fixture
    def config(self):
        return CopernicusConfig(search_provider="stac")

    @pytest.fixture
    def mock_stac_client(self):
        with patch("pystac_client.Client.open") as mock_open:
            client = mock_open.return_value
            yield client

    @pytest.fixture
    def provider(self, config, mock_stac_client):
        with patch("vresto.api.catalog.CopernicusAuth"):
            return STACCatalogSearch(config=config)

    def test_search_products_stac(self, provider, mock_stac_client):
        """Test STAC search interacts with pystac-client correctly."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        # Mock STAC Item
        mock_item = Mock()
        mock_item.id = "stac-1"
        mock_item.properties = {"datetime": "2024-01-01T10:00:00Z", "title": "S2A_MSIL2A_20240101", "eo:cloud_cover": 5.0, "s3:path": "s3://bucket/prod1"}
        mock_item.collection_id = "sentinel-2-l2a"
        mock_item.geometry = {"type": "Polygon", "coordinates": []}
        mock_item.assets = {}

        mock_search = Mock()
        mock_search.items.return_value = [mock_item]
        mock_stac_client.search.return_value = mock_search

        products = provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2", product_level="L2A", max_cloud_cover=10)

        assert len(products) == 1
        assert products[0].id == "stac-1"
        assert products[0].cloud_cover == 5.0

        # Verify pystac-client call
        mock_stac_client.search.assert_called_once()
        kwargs = mock_stac_client.search.call_args[1]
        assert "sentinel-2-l2a" in kwargs["collections"]
        assert kwargs["bbox"] == [4.0, 50.0, 5.0, 51.0]
        assert kwargs["filter"] == {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 10]}

    def test_get_product_by_name_stac(self, provider, mock_stac_client):
        """Test getting product by name in STAC."""
        mock_item = Mock()
        mock_item.id = "S2A_PROD"
        mock_item.properties = {"title": "S2A_PROD"}
        mock_item.assets = {}

        mock_search = Mock()
        mock_search.items.return_value = [mock_item]
        mock_stac_client.search.return_value = mock_search

        product = provider.get_product_by_name("S2A_PROD")

        assert product is not None
        assert product.id == "S2A_PROD"
        mock_stac_client.search.assert_called_with(ids=["S2A_PROD"])

    def test_search_sentinel1_stac_uses_correct_collection(self, provider, mock_stac_client):
        """STAC search for SENTINEL-1 GRD uses sentinel-1-grd collection."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_search = Mock()
        mock_search.items.return_value = []
        mock_stac_client.search.return_value = mock_search

        provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-1", product_level="GRD")

        kwargs = mock_stac_client.search.call_args[1]
        assert "sentinel-1-grd" in kwargs["collections"]

    def test_search_sentinel1_slc_stac_collection(self, provider, mock_stac_client):
        """STAC search for SENTINEL-1 SLC uses sentinel-1-slc collection."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_search = Mock()
        mock_search.items.return_value = []
        mock_stac_client.search.return_value = mock_search

        provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-1", product_level="SLC")

        kwargs = mock_stac_client.search.call_args[1]
        assert "sentinel-1-slc" in kwargs["collections"]

    def test_search_sentinel5p_l2_stac_collection(self, provider, mock_stac_client):
        """STAC search for SENTINEL-5P L2 uses sentinel-5p-l2 collection."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_search = Mock()
        mock_search.items.return_value = []
        mock_stac_client.search.return_value = mock_search

        provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-5P", product_level="L2")

        kwargs = mock_stac_client.search.call_args[1]
        assert "sentinel-5p-l2" in kwargs["collections"]

    def test_search_sentinel5p_l1b_stac_collection(self, provider, mock_stac_client):
        """STAC search for SENTINEL-5P L1B uses sentinel-5p-l1b collection."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_search = Mock()
        mock_search.items.return_value = []
        mock_stac_client.search.return_value = mock_search

        provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-5P", product_level="L1B")

        kwargs = mock_stac_client.search.call_args[1]
        assert "sentinel-5p-l1b" in kwargs["collections"]

    def test_stac_returns_empty_for_unmapped_collection(self, provider, mock_stac_client):
        """STAC search returns [] when no collection mapping exists."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        products = provider.search_products(bbox=bbox, start_date="2024-01-01", collection="UNKNOWN-SAT", product_level="L2")

        assert products == []
        mock_stac_client.search.assert_not_called()

    def test_search_sentinel1_parses_item(self, provider, mock_stac_client):
        """STAC response items for SENTINEL-1 are parsed into ProductInfo correctly."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        mock_item = Mock()
        mock_item.id = "S1A_IW_GRDH_20240101"
        mock_item.properties = {
            "datetime": "2024-01-01T06:00:00Z",
            "title": "S1A_IW_GRDH_20240101",
        }
        mock_item.collection_id = "sentinel-1-grd"
        mock_item.geometry = None
        mock_item.assets = {}

        mock_search = Mock()
        mock_search.items.return_value = [mock_item]
        mock_stac_client.search.return_value = mock_search

        products = provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-1", product_level="GRD")

        assert len(products) == 1
        assert products[0].id == "S1A_IW_GRDH_20240101"
        assert products[0].collection == "SENTINEL-1"

    def test_search_sentinel5p_parses_item(self, provider, mock_stac_client):
        """STAC response items for SENTINEL-5P are parsed into ProductInfo correctly."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        mock_item = Mock()
        mock_item.id = "S5P_OFFL_L2__NO2___20240101"
        mock_item.properties = {
            "datetime": "2024-01-01T00:00:00Z",
            "title": "S5P_OFFL_L2__NO2___20240101",
            "eo:cloud_cover": 20.0,
        }
        mock_item.collection_id = "sentinel-5p-l2"
        mock_item.geometry = None
        mock_item.assets = {}

        mock_search = Mock()
        mock_search.items.return_value = [mock_item]
        mock_stac_client.search.return_value = mock_search

        products = provider.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-5P", product_level="L2")

        assert len(products) == 1
        assert products[0].id == "S5P_OFFL_L2__NO2___20240101"
        assert products[0].cloud_cover == 20.0


class TestCatalogSearchFactory:
    """Tests for CatalogSearch factory function."""

    def test_factory_returns_odata(self):
        config = CopernicusConfig(search_provider="odata")
        with patch("vresto.api.catalog.CopernicusAuth"):
            catalog = CatalogSearch(config=config)
            assert isinstance(catalog, ODataCatalogSearch)

    def test_factory_returns_stac(self):
        config = CopernicusConfig(search_provider="stac")
        with patch("pystac_client.Client.open"), patch("vresto.api.catalog.CopernicusAuth"):
            catalog = CatalogSearch(config=config)
            assert isinstance(catalog, STACCatalogSearch)
