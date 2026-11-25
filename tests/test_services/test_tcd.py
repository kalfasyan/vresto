from vresto.services.overlay_base import degree_tiles, tile_code
from vresto.services.tcd import TCD_TILE_DEG, TCDService, tcd_has_coverage


def test_tcd_tile_code_uses_signed_prefixes():
    assert tile_code(-12, -63) == "S12W063"
    assert tile_code(3, 6) == "N03E006"


def test_tcd_tiles_for_bounds_returns_expected_3_degree_tile():
    assert [tile_code(*t) for t in degree_tiles(-62.9, -11.9, -60.1, -9.1, TCD_TILE_DEG)] == ["S12W063"]


def test_tcd_has_coverage_matches_pantropical_limits():
    assert tcd_has_coverage(-10.0, -9.0)
    assert not tcd_has_coverage(50.8, 50.9)
    assert not tcd_has_coverage(-60.0, -55.0)


def test_tcd_vsis3_path_matches_collection_layout(tmp_path):
    service = TCDService(cache_root=tmp_path)
    path = service._vsis3_path("S12W063", "2020")
    assert path == ("/vsis3/eodata/CLMS/landcover_landuse/dynamic_land_cover/tcd_pantropical_10m_yearly_v1/2020/01/01/LCFM_TCD-10_V100_2020_S12W063_cog/LCFM_TCD-10_V100_2020_S12W063_MAP.tif")
