from vresto.services.ndvi import NDVIService, ndvi_lts_period_from_date


def test_ndvi_lts_period_from_date_uses_current_dekad():
    assert ndvi_lts_period_from_date("2020-06-03") == ("06", "01")
    assert ndvi_lts_period_from_date("2020-06-19") == ("06", "11")
    assert ndvi_lts_period_from_date("20200627") == ("06", "21")


def test_ndvi_lts_path_matches_collection_layout(tmp_path):
    service = NDVIService(cache_root=tmp_path)
    path = service._vsis3_path("06", "21")
    assert path == ("/vsis3/eodata/CLMS/bio-geophysical/vegetation_indices/ndvi-lts_global_1km_10daily_v3/1999/06/21/c_gls_NDVI-LTS_1999-2019-0621_GLOBE_VGT-PROBAV_V3.0.1_cog/c_gls_NDVI-MEAN-LTS_1999-2019-0621_GLOBE_VGT-PROBAV_V3.0.1.tiff")
