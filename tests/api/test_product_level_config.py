"""Unit tests for product level configuration."""

from vresto.api.product_level_config import (
    BETA_SUPPORT_COLLECTIONS,
    COLLECTION_PRODUCT_LEVELS,
    FULLY_SUPPORTED_COLLECTIONS,
    get_level_description,
    get_supported_levels,
    get_unsupported_levels,
    is_collection_fully_supported,
    is_level_supported,
)


class TestCollectionProductLevels:
    """Tests for the COLLECTION_PRODUCT_LEVELS registry."""

    def test_sentinel2_levels(self):
        assert COLLECTION_PRODUCT_LEVELS["SENTINEL-2"] == ["L1C", "L2A"]

    def test_sentinel1_levels(self):
        assert COLLECTION_PRODUCT_LEVELS["SENTINEL-1"] == ["GRD", "SLC", "RAW", "OCN"]

    def test_sentinel5p_levels(self):
        assert COLLECTION_PRODUCT_LEVELS["SENTINEL-5P"] == ["L1B", "L2"]

    def test_sentinel3_levels(self):
        levels = COLLECTION_PRODUCT_LEVELS["SENTINEL-3"]
        assert "L0" in levels and "L1" in levels and "L2" in levels

    def test_landsat8_levels(self):
        levels = COLLECTION_PRODUCT_LEVELS["LANDSAT-8"]
        assert "L1TP" in levels and "L2SP" in levels


class TestGetSupportedLevels:
    """Tests for get_supported_levels."""

    def test_sentinel2(self):
        assert get_supported_levels("SENTINEL-2") == ["L1C", "L2A"]

    def test_sentinel1(self):
        levels = get_supported_levels("SENTINEL-1")
        assert "GRD" in levels
        assert "SLC" in levels

    def test_sentinel5p(self):
        levels = get_supported_levels("SENTINEL-5P")
        assert "L1B" in levels
        assert "L2" in levels

    def test_unknown_collection_returns_empty(self):
        assert get_supported_levels("UNKNOWN") == []


class TestIsLevelSupported:
    """Tests for is_level_supported — parity across all collections."""

    # SENTINEL-2 (reference, fully supported)
    def test_sentinel2_l1c(self):
        assert is_level_supported("SENTINEL-2", "L1C") is True

    def test_sentinel2_l2a(self):
        assert is_level_supported("SENTINEL-2", "L2A") is True

    def test_sentinel2_invalid(self):
        assert is_level_supported("SENTINEL-2", "GRD") is False

    # SENTINEL-1 (new)
    def test_sentinel1_grd(self):
        assert is_level_supported("SENTINEL-1", "GRD") is True

    def test_sentinel1_slc(self):
        assert is_level_supported("SENTINEL-1", "SLC") is True

    def test_sentinel1_raw(self):
        assert is_level_supported("SENTINEL-1", "RAW") is True

    def test_sentinel1_ocn(self):
        assert is_level_supported("SENTINEL-1", "OCN") is True

    def test_sentinel1_invalid(self):
        assert is_level_supported("SENTINEL-1", "L2A") is False

    # SENTINEL-5P (new)
    def test_sentinel5p_l1b(self):
        assert is_level_supported("SENTINEL-5P", "L1B") is True

    def test_sentinel5p_l2(self):
        assert is_level_supported("SENTINEL-5P", "L2") is True

    def test_sentinel5p_invalid(self):
        assert is_level_supported("SENTINEL-5P", "GRD") is False


class TestGetUnsupportedLevels:
    """Tests for get_unsupported_levels."""

    def test_all_supported_returns_empty(self):
        assert get_unsupported_levels("SENTINEL-1", ["GRD", "SLC"]) == []

    def test_mixed_returns_only_unsupported(self):
        result = get_unsupported_levels("SENTINEL-1", ["GRD", "L2A"])
        assert result == ["L2A"]

    def test_sentinel5p_invalid_levels(self):
        result = get_unsupported_levels("SENTINEL-5P", ["L1B", "GRD"])
        assert result == ["GRD"]


class TestIsCollectionFullySupported:
    """Tests for is_collection_fully_supported."""

    def test_sentinel2_is_fully_supported(self):
        assert is_collection_fully_supported("SENTINEL-2") is True

    def test_sentinel1_is_not_fully_supported(self):
        assert is_collection_fully_supported("SENTINEL-1") is False

    def test_sentinel5p_is_not_fully_supported(self):
        assert is_collection_fully_supported("SENTINEL-5P") is False

    def test_landsat8_is_not_fully_supported(self):
        assert is_collection_fully_supported("LANDSAT-8") is False


class TestBetaSupportCollections:
    """Tests confirming beta-support list is accurate."""

    def test_sentinel1_in_beta(self):
        assert "SENTINEL-1" in BETA_SUPPORT_COLLECTIONS

    def test_sentinel5p_in_beta(self):
        assert "SENTINEL-5P" in BETA_SUPPORT_COLLECTIONS

    def test_sentinel2_not_in_beta(self):
        assert "SENTINEL-2" not in BETA_SUPPORT_COLLECTIONS

    def test_sentinel2_in_fully_supported(self):
        assert "SENTINEL-2" in FULLY_SUPPORTED_COLLECTIONS


class TestGetLevelDescription:
    """Tests for get_level_description — parity checks across new sources."""

    # SENTINEL-2 reference
    def test_sentinel2_l2a_description(self):
        assert get_level_description("SENTINEL-2", "L2A") == "Atmospherically corrected"

    # SENTINEL-1 new
    def test_sentinel1_grd_description(self):
        assert get_level_description("SENTINEL-1", "GRD") == "Ground Range Detected"

    def test_sentinel1_slc_description(self):
        assert get_level_description("SENTINEL-1", "SLC") == "Single Look Complex"

    def test_sentinel1_raw_description(self):
        assert get_level_description("SENTINEL-1", "RAW") == "Raw data"

    def test_sentinel1_ocn_description(self):
        assert get_level_description("SENTINEL-1", "OCN") == "Ocean products"

    # SENTINEL-5P new
    def test_sentinel5p_l1b_description(self):
        assert get_level_description("SENTINEL-5P", "L1B") == "Radiance data"

    def test_sentinel5p_l2_description(self):
        assert get_level_description("SENTINEL-5P", "L2") == "Geophysical data"

    def test_unknown_level_returns_level_code(self):
        """Unknown levels fall back to the level code itself."""
        assert get_level_description("SENTINEL-1", "UNKNOWN") == "UNKNOWN"
