"""Unit tests for the ProductName parser."""

import pytest

from vresto.products.product_name import ProductName


class TestSentinel2Parsing:
    """Strict Sentinel-2 compact naming: MMM_MSIXXX_YYYYMMDDTHHMMSS_Nxxyy_ROOO_Txxxxx_<disc>."""

    L2A_NAME = "S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"
    L1C_NAME = "S2A_MSIL1C_20190615T103031_N0207_R108_T32TNS_20190615T123014"

    def test_l2a_fields(self):
        pn = ProductName(self.L2A_NAME)

        assert pn.product_type == "S2"
        assert pn.satellite == "S2B"
        assert pn.product_level == "MSIL2A"
        assert pn.acquisition_datetime == "20201212T235129"
        assert pn.processing_baseline == "N0500"
        assert pn.relative_orbit == "073"
        assert pn.tile == "59UNV"
        assert pn.product_discriminator == "20230226T030207"
        assert pn.suffix_safe is False

    def test_l1c_fields(self):
        pn = ProductName(self.L1C_NAME)

        assert pn.product_type == "S2"
        assert pn.satellite == "S2A"
        assert pn.product_level == "MSIL1C"
        assert pn.tile == "32TNS"
        assert pn.processing_baseline == "N0207"
        assert pn.relative_orbit == "108"

    def test_safe_suffix_is_stripped_and_remembered(self):
        pn = ProductName(self.L2A_NAME + ".SAFE")

        assert pn.suffix_safe is True
        # Parsing succeeds after suffix stripping
        assert pn.product_type == "S2"
        assert pn.tile == "59UNV"

    def test_s3_uri_input_extracts_final_component(self):
        uri = "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/" + self.L2A_NAME + ".SAFE/"
        pn = ProductName(uri)

        assert pn.product_type == "S2"
        assert pn.suffix_safe is True
        assert pn.tile == "59UNV"

    def test_product_timestamp_alias_matches_discriminator(self):
        pn = ProductName(self.L2A_NAME)

        assert pn.product_timestamp == pn.product_discriminator
        assert pn.product_timestamp == "20230226T030207"

    def test_processing_baseline_pretty(self):
        pn = ProductName(self.L2A_NAME)
        assert pn.processing_baseline_pretty() == "05.00"

        pn = ProductName(self.L1C_NAME)
        assert pn.processing_baseline_pretty() == "02.07"

    def test_processing_baseline_pretty_handles_missing(self):
        pn = ProductName("S2A_unknown_format")
        # Fallback parser leaves processing_baseline as None
        assert pn.processing_baseline is None
        assert pn.processing_baseline_pretty() is None

    def test_processing_baseline_pretty_handles_malformed(self):
        pn = ProductName(self.L2A_NAME)
        pn.processing_baseline = "BADTOKEN"
        assert pn.processing_baseline_pretty() is None

    def test_safe_name_appends_suffix_when_missing(self):
        pn = ProductName(self.L2A_NAME)
        assert pn.safe_name() == self.L2A_NAME + ".SAFE"

    def test_safe_name_preserves_existing_suffix(self):
        raw = self.L2A_NAME + ".SAFE"
        pn = ProductName(raw)
        assert pn.safe_name() == raw


class TestS3PrefixGeneration:
    L2A_NAME = "S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"

    def test_s2_l2a_s3_prefix(self):
        pn = ProductName(self.L2A_NAME)
        expected = "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/" + self.L2A_NAME + ".SAFE/"
        assert pn.s3_prefix() == expected

    def test_s2_l1c_s3_prefix(self):
        l1c = "S2A_MSIL1C_20190615T103031_N0207_R108_T32TNS_20190615T123014"
        pn = ProductName(l1c)
        assert pn.s3_prefix() == ("s3://eodata/Sentinel-2/MSI/L1C_N0207/2019/06/15/" + l1c + ".SAFE/")

    def test_s2_prefix_returns_none_without_acquisition_date(self):
        pn = ProductName(self.L2A_NAME)
        pn.acquisition_datetime = None
        assert pn.s3_prefix() is None

    def test_s1_raises_not_implemented(self):
        pn = ProductName("S1A_IW_SLC__1SDV_20201212T235129_20201212T235156_036789_046ABC_1234")
        assert pn.product_type == "S1"
        with pytest.raises(NotImplementedError, match="S1"):
            pn.s3_prefix()

    def test_s5p_raises_not_implemented(self):
        pn = ProductName("S5P_OFFL_L2__AER_AI_20201212T235129")
        assert pn.product_type == "S5P"
        with pytest.raises(NotImplementedError, match="S5P"):
            pn.s3_prefix()

    def test_unknown_product_type_raises_not_implemented(self):
        # Fallback parser leaves product_type as None
        pn = ProductName("totally_unrecognised_thing")
        assert pn.product_type is None
        with pytest.raises(NotImplementedError):
            pn.s3_prefix()


class TestSentinel1AndS5PParsing:
    def test_s1_basic_parse(self):
        pn = ProductName("S1A_IW_SLC__1SDV_20201212T235129_20201212T235156_036789_046ABC_1234")
        assert pn.product_type == "S1"
        assert pn.satellite == "S1A"
        # product_level holds the mode+type token (e.g. "IW_SLC_")
        assert pn.product_level is not None
        assert "IW" in pn.product_level

    def test_s5p_basic_parse(self):
        pn = ProductName("S5P_OFFL_L2__AER_AI_20201212T235129")
        assert pn.product_type == "S5P"
        assert pn.satellite == "S5P"
        assert pn.product_discriminator == "20201212T235129"


class TestFallbackParsing:
    def test_fallback_splits_on_underscore(self):
        pn = ProductName("FOO_BAR_BAZ_QUX")

        assert pn.product_type is None  # no regex matched
        assert pn.satellite == "FOO"
        assert pn.product_level == "BAR"
        assert pn.acquisition_datetime == "BAZ"

    def test_repr_includes_key_fields(self):
        pn = ProductName("S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207")
        r = repr(pn)
        assert "ProductName" in r
        assert "S2" in r
        assert "S2B" in r
