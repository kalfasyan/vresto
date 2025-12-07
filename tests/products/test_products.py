"""Tests for the products module."""

import pytest

from vresto.products import ProductMetadata, ProductQuicklook, ProductsManager


class TestProductQuicklook:
    """Test ProductQuicklook class."""

    def test_quicklook_creation(self):
        """Test creating a quicklook object."""
        image_data = b"\xff\xd8\xff\xe0"  # JPEG magic bytes
        ql = ProductQuicklook(product_name="test-product", image_data=image_data)

        assert ql.product_name == "test-product"
        assert ql.image_data == image_data
        assert ql.image_format == "jpeg"

    def test_quicklook_base64(self):
        """Test getting base64 encoded data."""
        image_data = b"test_image_data"
        ql = ProductQuicklook(product_name="test-product", image_data=image_data)

        base64_str = ql.get_base64()
        assert isinstance(base64_str, str)
        assert len(base64_str) > 0

        # Verify it can be decoded back
        import base64

        decoded = base64.b64decode(base64_str)
        assert decoded == image_data


class TestProductMetadata:
    """Test ProductMetadata class."""

    def test_metadata_creation(self):
        """Test creating a metadata object."""
        xml_content = '<?xml version="1.0"?><root></root>'
        meta = ProductMetadata(product_name="test-product", metadata_xml=xml_content)

        assert meta.product_name == "test-product"
        assert meta.metadata_xml == xml_content


class TestProductsManager:
    """Test ProductsManager class."""

    def test_manager_initialization(self):
        """Test initializing ProductsManager."""
        # This will use env variables for credentials
        try:
            manager = ProductsManager()
            assert manager is not None
            assert manager.s3_client is not None
        except ValueError:
            # Expected if credentials are not set
            pytest.skip("Credentials not configured")

    @pytest.mark.requires_credentials
    def test_s3_path_extraction(self):
        """Test extracting bucket and key from S3 path."""
        manager = ProductsManager()

        # Test with s3:// prefix
        bucket, key = manager._extract_s3_path_components("s3://eodata/Sentinel-2/path/to/product/")
        assert bucket == "eodata"
        assert key == "Sentinel-2/path/to/product/"

        # Test without prefix
        bucket, key = manager._extract_s3_path_components("eodata/Sentinel-2/path/to/product/")
        assert bucket == "eodata"
        assert key == "Sentinel-2/path/to/product/"
