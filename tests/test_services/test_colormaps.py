"""Pin the built-in colour tables, so a change to one is deliberate."""

import hashlib

import numpy as np
import pytest

from vresto.services.colormaps import NAMES, colormap_lut

CHECKSUMS = {
    "YlGn": "df94883d54ab4345",
    "YlGnBu": "9906bfafe20a3749",
    "YlOrRd": "08ccd7c3ce9b5e96",
    "RdYlBu_r": "4ffa8bc42826ab83",
    "terrain_land": "faad706e2fe7758a",
    "ndvi_lts": "5e273566615f9f88",
}


def test_every_table_is_pinned():
    assert set(NAMES) == set(CHECKSUMS)


@pytest.mark.parametrize("name", list(CHECKSUMS))
def test_table_matches_its_checksum(name):
    lut = colormap_lut(name)
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8
    assert hashlib.sha256(lut.tobytes()).hexdigest()[:16] == CHECKSUMS[name]


def test_tables_are_read_only():
    with pytest.raises(ValueError):
        colormap_lut("YlGn")[0, 0] = 1
