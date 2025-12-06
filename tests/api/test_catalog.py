"""Unit tests for API catalog module."""

from unittest.mock import Mock, patch

import pytest
import requests

from vresto.api.catalog import BoundingBox, CatalogSearch, ProductInfo


class TestBoundingBox:
    """Tests for BoundingBox class."""

    def test_bbox_creation(self):
        """Test creating a bounding box."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        assert bbox.west == 4.0
        assert bbox.south == 50.0
        assert bbox.east == 5.0
        assert bbox.north == 51.0

    def test_bbox_to_wkt(self):
        """Test converting bounding box to WKT format."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        wkt = bbox.to_wkt()

        assert wkt == "POLYGON((4.0 50.0,5.0 50.0,5.0 51.0,4.0 51.0,4.0 50.0))"

    def test_bbox_to_bbox_string(self):
        """Test converting to comma-separated bbox string."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        bbox_str = bbox.to_bbox_string()

        assert bbox_str == "4.0,50.0,5.0,51.0"


class TestProductInfo:
    """Tests for ProductInfo class."""

    def test_product_info_creation(self):
        """Test creating a ProductInfo instance."""
        product = ProductInfo(
            id="test-id",
            name="S2A_MSIL2A_20240101T103321",
            collection="SENTINEL-2",
            sensing_date="2024-01-01 10:33:21",
            size_mb=1024.5,
        )

        assert product.id == "test-id"
        assert product.name == "S2A_MSIL2A_20240101T103321"
        assert product.collection == "SENTINEL-2"
        assert product.size_mb == 1024.5

    def test_product_info_with_optional_fields(self):
        """Test ProductInfo with optional fields."""
        product = ProductInfo(
            id="test-id",
            name="test-product",
            collection="SENTINEL-2",
            sensing_date="2024-01-01",
            size_mb=1024.5,
            cloud_cover=15.5,
            s3_path="/path/to/product",
        )

        assert product.cloud_cover == 15.5
        assert product.s3_path == "/path/to/product"

    def test_product_info_str(self):
        """Test string representation of ProductInfo."""
        product = ProductInfo(id="test-id", name="test-product", collection="SENTINEL-2", sensing_date="2024-01-01", size_mb=1024.5, cloud_cover=15.5)

        str_repr = str(product)

        assert "test-product" in str_repr
        assert "SENTINEL-2" in str_repr
        assert "1024.50 MB" in str_repr
        assert "15.5%" in str_repr


class TestCatalogSearch:
    """Tests for CatalogSearch class."""

    @pytest.fixture
    def mock_auth(self):
        """Create a mock authentication instance."""
        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer test_token", "Accept": "application/json"}
        return auth

    @pytest.fixture
    def catalog(self, mock_auth):
        """Create a CatalogSearch instance with mock auth."""
        with patch("vresto.api.catalog.CopernicusAuth", return_value=mock_auth):
            return CatalogSearch()

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        with patch("vresto.api.catalog.CopernicusAuth"), patch("vresto.api.catalog.CopernicusConfig"):
            catalog = CatalogSearch()

            assert catalog.config is not None
            assert catalog.auth is not None

    def test_search_products_builds_correct_filter(self, catalog, mock_auth):
        """Test that search builds correct OData filter."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response) as mock_get:
            catalog.search_products(bbox=bbox, start_date="2024-01-01", end_date="2024-01-07", collection="SENTINEL-2", max_cloud_cover=20, max_results=10)

            # Verify request was made
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            params = call_args[1]["params"]

            # Check filter includes necessary components
            filter_str = params["$filter"]
            assert "Collection/Name eq 'SENTINEL-2'" in filter_str
            assert "ContentDate/Start ge 2024-01-01" in filter_str
            assert "ContentDate/Start le 2024-01-07" in filter_str
            assert "Intersects" in filter_str
            assert "cloudCover" in filter_str

    def test_search_products_without_cloud_filter(self, catalog, mock_auth):
        """Test search without cloud cover filter."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response) as mock_get:
            catalog.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-1", max_results=5)

            params = mock_get.call_args[1]["params"]
            filter_str = params["$filter"]

            # Cloud cover filter should not be present
            assert "cloudCover" not in filter_str

    def test_search_products_uses_start_date_as_end_date(self, catalog, mock_auth):
        """Test that end_date defaults to start_date."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response) as mock_get:
            catalog.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2")

            params = mock_get.call_args[1]["params"]
            filter_str = params["$filter"]

            assert "ContentDate/Start le 2024-01-01" in filter_str

    def test_search_products_parses_results(self, catalog, mock_auth):
        """Test that search results are parsed correctly."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "Id": "prod-1",
                    "Name": "S2A_PRODUCT_1",
                    "Collection": {"Name": "SENTINEL-2"},
                    "ContentDate": {"Start": "2024-01-01T10:00:00Z"},
                    "ContentLength": 1073741824,  # 1GB
                    "Attributes": [{"Name": "cloudCover", "Value": 10.5}],
                    "S3Path": "/sentinel-2/product-1",
                }
            ]
        }

        with patch("requests.get", return_value=mock_response):
            products = catalog.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2")

            assert len(products) == 1
            assert products[0].id == "prod-1"
            assert products[0].name == "S2A_PRODUCT_1"
            assert products[0].collection == "SENTINEL-2"
            assert products[0].cloud_cover == 10.5
            assert products[0].size_mb == 1024.0

    def test_search_products_handles_error(self, catalog, mock_auth):
        """Test that search handles API errors gracefully."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch("requests.get", return_value=mock_response):
            products = catalog.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2")

            assert products == []

    def test_search_products_handles_network_error(self, catalog, mock_auth):
        """Test that search handles network errors."""
        bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

        with patch("requests.get", side_effect=requests.RequestException("Network error")):
            products = catalog.search_products(bbox=bbox, start_date="2024-01-01", collection="SENTINEL-2")

            assert products == []

    def test_get_product_by_name_success(self, catalog, mock_auth):
        """Test getting product by name."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "Id": "prod-1",
                    "Name": "S2A_PRODUCT_1",
                    "Collection": {"Name": "SENTINEL-2"},
                    "ContentDate": {"Start": "2024-01-01T10:00:00Z"},
                    "ContentLength": 1073741824,
                    "Attributes": [],
                }
            ]
        }

        with patch("requests.get", return_value=mock_response):
            product = catalog.get_product_by_name("S2A_PRODUCT_1")

            assert product is not None
            assert product.name == "S2A_PRODUCT_1"

    def test_get_product_by_name_not_found(self, catalog, mock_auth):
        """Test getting product by name when not found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}

        with patch("requests.get", return_value=mock_response):
            product = catalog.get_product_by_name("NONEXISTENT")

            assert product is None

    def test_parse_products_handles_missing_fields(self, catalog):
        """Test parsing products with missing optional fields."""
        response_data = {"value": [{"Id": "prod-1", "Name": "TEST_PRODUCT", "Collection": {}, "ContentDate": {}, "ContentLength": 0, "Attributes": []}]}

        products = catalog._parse_products(response_data)

        assert len(products) == 1
        assert products[0].name == "TEST_PRODUCT"
        assert products[0].cloud_cover is None
