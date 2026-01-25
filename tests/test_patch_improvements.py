import os

from vresto.api.config import CopernicusConfig
from vresto.api.env_loader import load_env, write_env_file
from vresto.products.downloader import _parse_band_from_filename


def test_env_loader_search_parents(tmp_path):
    # Create a .env in a parent directory
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)

    env_file = parent / ".env"
    write_env_file(env_file, {"TEST_VAR": "parent_val"})

    # Change CWD to child
    os.chdir(child)

    # Load env with search_parents=True
    load_env(search_parents=True)
    assert os.environ.get("TEST_VAR") == "parent_val"

    # Cleanup
    if "TEST_VAR" in os.environ:
        del os.environ["TEST_VAR"]


def test_config_backward_compatibility(monitoring_env=None):
    os.environ["COPERNICUS_ENDPOINT"] = "https://old-endpoint.com"
    if "COPERNICUS_S3_ENDPOINT" in os.environ:
        del os.environ["COPERNICUS_S3_ENDPOINT"]

    config = CopernicusConfig()
    assert config.s3_endpoint == "https://old-endpoint.com"

    # S3_ENDPOINT takes precedence
    os.environ["COPERNICUS_S3_ENDPOINT"] = "https://new-endpoint.com"
    config = CopernicusConfig()
    assert config.s3_endpoint == "https://new-endpoint.com"

    # Cleanup
    del os.environ["COPERNICUS_ENDPOINT"]
    del os.environ["COPERNICUS_S3_ENDPOINT"]


def test_band_regex_standard_bands():
    # Test standard bands
    assert _parse_band_from_filename("S2A_MSIL2A_2023_B02_10m.jp2") == ("B02", 10)
    assert _parse_band_from_filename("S2B_MSIL1C_2023_B01.jp2") == ("B01", 60)
    assert _parse_band_from_filename("S2A_MSIL2A_2023_TCI_10m.jp2") == ("TCI", 10)
    assert _parse_band_from_filename("S2B_MSIL2A_2023_SCL_20m.jp2") == ("SCL", 20)


def test_config_load_env_integration(tmp_path):
    # This tests that CopernicusConfig successfully calls our improved load_env
    env_file = tmp_path / ".env"
    write_env_file(env_file, {"COPERNICUS_USERNAME": "testuser_config", "COPERNICUS_PASSWORD": "testpassword_config"})

    # Clear existing env vars to ensure we load from the new file
    if "COPERNICUS_USERNAME" in os.environ:
        del os.environ["COPERNICUS_USERNAME"]
    if "COPERNICUS_PASSWORD" in os.environ:
        del os.environ["COPERNICUS_PASSWORD"]

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        from vresto.api.env_loader import load_env

        load_env()
        config = CopernicusConfig()
        assert config.username == "testuser_config"
        assert config.password == "testpassword_config"
    finally:
        os.chdir(original_cwd)
        if "COPERNICUS_USERNAME" in os.environ:
            del os.environ["COPERNICUS_USERNAME"]
        if "COPERNICUS_PASSWORD" in os.environ:
            del os.environ["COPERNICUS_PASSWORD"]
