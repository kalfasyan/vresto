"""Unit tests for STAC collection ID mappings."""

from vresto.api.stac_mappings import (
    COLLECTION_MAPPING,
    STAC_ID_TO_VRESTO,
    get_stac_collection_id,
)


class TestCollectionMapping:
    """Tests for the COLLECTION_MAPPING dictionary."""

    def test_sentinel1_grd_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-1", "GRD")] == "sentinel-1-grd"

    def test_sentinel1_slc_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-1", "SLC")] == "sentinel-1-slc"

    def test_sentinel1_raw_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-1", "RAW")] == "sentinel-1-raw"

    def test_sentinel1_ocn_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-1", "OCN")] == "sentinel-1-ocn"

    def test_sentinel2_l1c_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-2", "L1C")] == "sentinel-2-l1c"

    def test_sentinel2_l2a_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-2", "L2A")] == "sentinel-2-l2a"

    def test_sentinel5p_l1b_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-5P", "L1B")] == "sentinel-5p-l1b"

    def test_sentinel5p_l2_mapped(self):
        assert COLLECTION_MAPPING[("SENTINEL-5P", "L2")] == "sentinel-5p-l2"

    def test_reverse_mapping_is_consistent(self):
        """Ensure reverse mapping covers all forward entries."""
        for key, value in COLLECTION_MAPPING.items():
            assert STAC_ID_TO_VRESTO[value] == key


class TestGetStacCollectionId:
    """Tests for get_stac_collection_id helper."""

    # --- SENTINEL-2 (existing, fully supported) ---
    def test_sentinel2_l2a(self):
        assert get_stac_collection_id("SENTINEL-2", "L2A") == "sentinel-2-l2a"

    def test_sentinel2_l1c(self):
        assert get_stac_collection_id("SENTINEL-2", "L1C") == "sentinel-2-l1c"

    def test_sentinel2_fallback_unknown_level(self):
        """Unknown level falls back to l1c for S2."""
        result = get_stac_collection_id("SENTINEL-2", "UNKNOWN")
        assert result == "sentinel-2-l1c"

    # --- SENTINEL-1 ---
    def test_sentinel1_grd(self):
        assert get_stac_collection_id("SENTINEL-1", "GRD") == "sentinel-1-grd"

    def test_sentinel1_slc(self):
        assert get_stac_collection_id("SENTINEL-1", "SLC") == "sentinel-1-slc"

    def test_sentinel1_raw(self):
        assert get_stac_collection_id("SENTINEL-1", "RAW") == "sentinel-1-raw"

    def test_sentinel1_ocn(self):
        assert get_stac_collection_id("SENTINEL-1", "OCN") == "sentinel-1-ocn"

    def test_sentinel1_fallback_no_level(self):
        """No level given falls back to GRD for S1."""
        assert get_stac_collection_id("SENTINEL-1") == "sentinel-1-grd"

    def test_sentinel1_fallback_unknown_level(self):
        """Unknown level falls back to GRD for S1."""
        assert get_stac_collection_id("SENTINEL-1", "UNKNOWN") == "sentinel-1-grd"

    # --- SENTINEL-5P ---
    def test_sentinel5p_l2(self):
        assert get_stac_collection_id("SENTINEL-5P", "L2") == "sentinel-5p-l2"

    def test_sentinel5p_l1b(self):
        assert get_stac_collection_id("SENTINEL-5P", "L1B") == "sentinel-5p-l1b"

    def test_sentinel5p_fallback_no_level(self):
        """No level given falls back to L2 for S5P."""
        assert get_stac_collection_id("SENTINEL-5P") == "sentinel-5p-l2"

    def test_sentinel5p_fallback_unknown_level(self):
        """Unknown level falls back to L2 for S5P."""
        assert get_stac_collection_id("SENTINEL-5P", "UNKNOWN") == "sentinel-5p-l2"

    def test_unknown_collection_returns_none(self):
        assert get_stac_collection_id("UNKNOWN-COLLECTION", "L2") is None
