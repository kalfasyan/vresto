"""Parser for Copernicus product names (Sentinel-2, Sentinel-1, Sentinel-5P).

Provides a ProductName class that extracts structured attributes from
product identifiers such as S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207
and helpers to build likely S3 paths and SAFE names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProductName:
    raw: str

    satellite: Optional[str] = None
    product_level: Optional[str] = None
    acquisition_datetime: Optional[str] = None
    processing_baseline: Optional[str] = None
    relative_orbit: Optional[str] = None
    tile: Optional[str] = None
    product_discriminator: Optional[str] = None
    suffix_safe: bool = False
    product_type: Optional[str] = None

    def __post_init__(self):
        name = self.raw
        # handle s3 paths or SAFE suffix
        if name.startswith("s3://"):
            # try to extract the final component
            name = name.rstrip("/")
            name = name.split("/")[-1]

        if name.endswith(".SAFE"):
            self.suffix_safe = True
            name = name[:-5]

        self._parse(name)

    def _parse(self, name: str) -> None:
        # Strict Sentinel-2 compact naming per Copernicus spec:
        # MMM_MSIXXX_YYYYMMDDTHHMMSS_Nxxyy_ROOO_Txxxxx_<Product Discriminator>
        # Example:
        # S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207
        s2_re = re.compile(
            r"^(S2[AB])_"  # mission
            r"(MSI[L][12][A-Z])_"  # product level (MSIL1C or MSIL2A)
            r"(\d{8}T\d{6})_"  # datatake sensing start time
            r"(N\d{4})_"  # processing baseline
            r"R(\d{3})_"  # relative orbit
            r"T([A-Z0-9]{5})_"  # tile
            r"([0-9T]{15})$"  # product discriminator (15 chars, typically datetime-like)
        )

        m = s2_re.match(name)
        if m:
            self.satellite = m.group(1)
            self.product_level = m.group(2)
            self.acquisition_datetime = m.group(3)
            self.processing_baseline = m.group(4)
            self.relative_orbit = m.group(5)
            self.tile = m.group(6)
            self.product_discriminator = m.group(7)
            self.product_type = "S2"
            return

        # Sentinel-1 pattern: e.g. S1A_IW_SLC__1SDV_20201212T235129_20201212T235156_036789_046ABC_1234
        s1_re = re.compile(r"^(S1[AB])_([A-Z0-9_]+)_(\d)SDV_([0-9T]+)_([0-9T]+)_([0-9]{6})_([0-9A-F]{6})_([0-9]{4})$")
        m = s1_re.match(name)
        if m:
            self.satellite = m.group(1)
            # second group contains modes and product type
            self.product_level = m.group(2)
            self.product_discriminator = m.group(4)
            self.acquisition_datetime = m.group(3)
            self.product_type = "S1"
            return

        # Sentinel-5P simple pattern: e.g. S5P_OFFL_L2__AER_AI_20201212T235129
        s5p_re = re.compile(r"^(S5P)_([A-Z0-9_]+)_([A-Z0-9_]+)_([0-9T]+)$")
        m = s5p_re.match(name)
        if m:
            self.satellite = m.group(1)
            self.product_level = m.group(2)
            self.product_type = "S5P"
            self.product_discriminator = m.group(4)
            return

        # fallback: try to split on underscores and set what we can
        parts = name.split("_")
        if parts:
            self.satellite = parts[0] if len(parts) > 0 else None
        if len(parts) > 1:
            self.product_level = parts[1]
        if len(parts) > 2:
            self.acquisition_datetime = parts[2]

    @property
    def product_timestamp(self) -> Optional[str]:
        """Backwards-compatible alias for the product discriminator field.

        Historically code used `product_timestamp` to refer to the 15-char
        product discriminator (which often contains a datetime). The new name
        `product_discriminator` is clearer; provide an alias for compatibility.
        """
        return self.product_discriminator

    def processing_baseline_pretty(self) -> Optional[str]:
        """Return processing baseline in dotted form, e.g. N0204 -> 02.04"""  # noqa
        if not self.processing_baseline:
            return None
        m = re.match(r"N(\d{2})(\d{2})", self.processing_baseline)
        if not m:
            return None
        return f"{m.group(1)}.{m.group(2)}"

    def safe_name(self) -> str:
        if self.suffix_safe:
            return self.raw if self.raw.endswith(".SAFE") else f"{self.raw}.SAFE"
        return f"{self.raw}.SAFE"

    def s3_prefix(self) -> Optional[str]:
        """Build a likely s3 prefix for the product using common EODATA layout.

        Returns e.g. "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/<name>.SAFE/"
        or None if not enough information.
        """
        if self.product_type == "S2":
            # derive prod_folder from product_level and processing baseline
            prod_type = self.product_level or "MSI"
            n_token = self.processing_baseline
            if n_token:
                prod_folder = f"L{prod_type[-2:]}_{n_token}"
            else:
                prod_folder = prod_type.replace("MSI", "")

            if not self.acquisition_datetime or len(self.acquisition_datetime) < 8:
                return None
            date = self.acquisition_datetime[:8]
            year = date[:4]
            month = date[4:6]
            day = date[6:8]
            safe_name = self.safe_name()
            return f"s3://eodata/Sentinel-2/MSI/{prod_folder}/{year}/{month}/{day}/{safe_name}/"
        # Only Sentinel-2 S3 prefix generation supported for now
        if self.product_type in (None, "S1", "S5P"):
            raise NotImplementedError(f"S3 prefix generation not implemented for product type: {self.product_type}")

        # Unknown product family
        raise NotImplementedError(f"S3 prefix generation not implemented for product type: {self.product_type}")

    def __repr__(self) -> str:
        return f"ProductName(raw={self.raw!r}, product_type={self.product_type!r}, satellite={self.satellite!r})"
