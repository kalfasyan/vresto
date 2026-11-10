"""Tests for public mode: per-visitor credentials, browser downloads and the tile proxy."""

import asyncio
import math
import os
import time
import zipfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import rasterio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from vresto.services.tiles import TILE_PROXY_PREFIX, TilePool, _proxied_tile_url
from vresto.ui import public_mode
from vresto.ui.public_mode import VisitorCredentials
from vresto.ui.tile_proxy import proxy_tile
from vresto.ui.widgets.credentials_menu import CredentialsMenu, download_credentials_configured

SERVER_ENV = {
    "COPERNICUS_USERNAME": "operator",
    "COPERNICUS_PASSWORD": "operator-secret",
    "COPERNICUS_S3_ACCESS_KEY": "OPERATORKEY",
    "COPERNICUS_S3_SECRET_KEY": "operator-s3-secret",
}


class TestVisitorCredentials:
    def test_config_never_falls_back_to_the_servers_credentials(self):
        with patch.dict(os.environ, SERVER_ENV):
            config = VisitorCredentials().config()

        assert config.username is None
        assert config.password is None
        assert config.s3_access_key is None
        assert config.s3_secret_key is None

    def test_config_holds_the_visitors_credentials(self):
        visitor = VisitorCredentials()
        visitor.set(username="me", password="pw")
        with patch.dict(os.environ, SERVER_ENV):
            config = visitor.config()

        assert (config.username, config.password) == ("me", "pw")
        assert config.s3_access_key is None

    def test_download_access_uses_only_the_visitor(self):
        visitor = VisitorCredentials()
        with patch.dict(os.environ, SERVER_ENV):
            assert download_credentials_configured(visitor) is False
            visitor.set(s3_access_key="K", s3_secret_key="S")
            assert download_credentials_configured(visitor) is True

    def test_clear_forgets_everything(self):
        visitor = VisitorCredentials()
        visitor.set(username="me", password="pw")
        visitor.clear()
        assert visitor.has_download_access() is False

    def test_forget_deletes_temporary_s3_keys(self):
        visitor = VisitorCredentials()
        visitor.set(username="me", password="pw")
        auth = visitor.auth()
        auth._s3_credentials = {"access_id": "TEMP", "secret": "x"}
        with patch.object(type(auth), "delete_s3_credentials") as delete, patch("vresto.ui.public_mode.threading.Thread") as thread:
            thread.side_effect = lambda target, args, **kw: MagicMock(start=lambda: target(*args))
            visitor.clear()
        delete.assert_called_once()


class TestCredentialsMenuInPublicMode:
    def test_does_not_show_the_servers_credentials(self, tmp_path):
        with patch.dict(os.environ, SERVER_ENV):
            menu = CredentialsMenu(env_path=tmp_path / ".env", visitor=VisitorCredentials())

        assert menu.current_username is None
        assert menu.current_password is None
        assert menu.current_access_key is None

    def test_save_keeps_credentials_in_the_tab_not_in_env(self, tmp_path):
        visitor = VisitorCredentials()
        env_path = tmp_path / ".env"
        menu = CredentialsMenu(env_path=env_path, visitor=visitor)
        menu.username_input = MagicMock(value="me")
        menu.password_input = MagicMock(value="pw")
        menu.access_key_input = MagicMock(value="")
        menu.secret_key_input = MagicMock(value="")
        menu.backend_select = None
        menu.status_label = MagicMock()

        with patch.dict(os.environ, {}, clear=True), patch("vresto.ui.widgets.credentials_menu.ui"):
            asyncio.run(menu._handle_save())
            assert "COPERNICUS_USERNAME" not in os.environ

        assert visitor.has_account() is True
        assert not env_path.exists()


class TestBrowserDownloads:
    def test_one_file_is_sent_as_is(self, tmp_path):
        band = tmp_path / "B04.jp2"
        band.write_bytes(b"jp2")
        assert public_mode.package_for_browser([band], tmp_path, "P_bands") == band

    def test_several_files_are_zipped(self, tmp_path):
        bands = []
        for name in ("B02.jp2", "B03.jp2"):
            path = tmp_path / name
            path.write_bytes(name.encode())
            bands.append(path)

        archive = public_mode.package_for_browser(bands, tmp_path, "P_bands")

        assert archive.name == "P_bands.zip"
        with zipfile.ZipFile(archive) as zf:
            assert sorted(zf.namelist()) == ["B02.jp2", "B03.jp2"]
        assert not any(p.exists() for p in bands)

    def test_old_download_folders_are_swept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VRESTO_PUBLIC_DOWNLOAD_DIR", str(tmp_path))
        old = public_mode.new_download_dir()
        past = time.time() - 2 * public_mode.DOWNLOAD_MAX_AGE_S
        os.utime(old, (past, past))
        fresh = public_mode.new_download_dir()

        assert not old.exists()
        assert fresh.exists()

    def test_download_tab_refuses_too_many_files(self, monkeypatch):
        from vresto.ui.widgets.download_tab import DownloadTab

        monkeypatch.setenv("VRESTO_PUBLIC_MAX_DOWNLOAD_FILES", "1")
        visitor = VisitorCredentials()
        visitor.set(username="me", password="pw")
        tab = DownloadTab(visitor=visitor)
        tab.product_input = MagicMock(value="S2A_MSIL2A_20240101T103321_N0510_R022_T31UFS_20240101T120000")
        tab.band_selections = {"B02": {10: MagicMock(value=True)}, "B03": {10: MagicMock(value=True)}}
        tab.messages_column = None

        with patch("vresto.ui.widgets.download_tab.ui") as mock_ui, patch("vresto.ui.widgets.download_tab.ProductsManager") as manager:
            asyncio.run(tab._handle_download())

        manager.assert_not_called()
        assert "at most 1 band files" in mock_ui.notify.call_args.args[0]


class TestTileProxy:
    def test_proxied_url_drops_the_filename(self):
        url = _proxied_tile_url("http://127.0.0.1:4242/api/tiles/{z}/{x}/{y}.png?&filename=%2Ftmp%2Fa.tif&indexes=1", "tok")
        assert url == f"{TILE_PROXY_PREFIX}/tok/{{z}}/{{x}}/{{y}}.png?indexes=1"

    @pytest.fixture
    def geotiff(self, tmp_path):
        path = tmp_path / "rgb.tif"
        profile = {"driver": "GTiff", "height": 256, "width": 256, "count": 3, "dtype": "uint8", "crs": "EPSG:4326", "transform": from_origin(4.0, 51.0, 0.001, 0.001)}
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.full((3, 256, 256), 200, dtype="uint8"))
        return path

    @pytest.fixture
    def proxied_pool(self, monkeypatch):
        monkeypatch.setenv("VRESTO_TILE_PROXY", "1")
        pool = TilePool()
        monkeypatch.setattr("vresto.ui.tile_proxy.tile_pool", pool)
        yield pool
        pool.shutdown_all()

    @pytest.fixture
    def http(self):
        app = FastAPI()
        app.add_api_route(f"{TILE_PROXY_PREFIX}/{{token}}/{{z}}/{{x}}/{{y}}.{{fmt}}", proxy_tile, methods=["GET"])
        with TestClient(app) as client:
            yield client

    @staticmethod
    def _tile(lat: float, lon: float, z: int) -> tuple[int, int, int]:
        n = 2**z
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return z, x, y

    def test_tiles_are_served_through_the_main_port(self, proxied_pool, geotiff, http):
        url = proxied_pool.get_or_create("visitor_a_tci_31UFS", str(geotiff))

        assert url.startswith(f"{TILE_PROXY_PREFIX}/")
        assert "filename" not in url
        z, x, y = self._tile(50.87, 4.13, 10)
        response = http.get(url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_visitors_cannot_point_the_proxy_at_another_file(self, proxied_pool, geotiff, http):
        url = proxied_pool.get_or_create("visitor_a_tci_31UFS", str(geotiff))
        token = url.split("/")[3]
        _, served_file = proxied_pool.resolve_proxy_target(token)

        with patch("vresto.ui.tile_proxy._http_client") as client:
            client.return_value.get = MagicMock(side_effect=RuntimeError("stop"))
            with pytest.raises(RuntimeError):
                http.get(f"{TILE_PROXY_PREFIX}/{token}/10/523/340.png?filename=/etc/passwd")
            params = client.return_value.get.call_args.kwargs["params"]

        assert ("filename", "/etc/passwd") not in params
        assert ("filename", served_file) in params

    def test_unknown_or_removed_tokens_are_not_found(self, proxied_pool, geotiff, http):
        url = proxied_pool.get_or_create("visitor_a_tci_31UFS", str(geotiff))
        token = url.split("/")[3]
        proxied_pool.remove_prefix("visitor_a_")

        assert proxied_pool.active_count == 0
        assert http.get(f"{TILE_PROXY_PREFIX}/{token}/10/523/340.png").status_code == 404
        assert http.get(f"{TILE_PROXY_PREFIX}/nope/10/523/340.png").status_code == 404


def test_public_mode_hides_the_server_disk_tabs():
    from vresto.ui import map_interface

    names = []

    def _tab(name, icon=None):
        names.append(name)
        return MagicMock()

    with (
        patch.object(map_interface, "ui") as mock_ui,
        patch.object(map_interface, "MapSearchTab"),
        patch.object(map_interface, "NameSearchTab"),
        patch.object(map_interface, "DownloadTab"),
        patch.object(map_interface, "ProductAnalysisTab") as analysis,
        patch.object(map_interface, "HiResTilerTab") as tiler,
    ):
        mock_ui.tab.side_effect = _tab
        map_interface.create_map_interface(visitor=VisitorCredentials())

    assert "Product Analysis" not in names and "Hi-Res Tiler" not in names
    analysis.assert_not_called()
    tiler.assert_not_called()
