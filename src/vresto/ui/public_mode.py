"""Public mode: run vresto as a shared website that anyone can open without signing in.

Turned on with ``VRESTO_PUBLIC_MODE=1``. Exploring (search, streaming, the coverage grid,
pixel history) already works anonymously; public mode makes the rest safe on a server that
many visitors share:

- Credentials typed in Settings live only in memory, for that browser tab
  (:class:`VisitorCredentials`). They are never written to ``.env`` or ``os.environ``, and
  the server's own credentials are never used for a visitor's downloads.
- Downloads are written to a temporary folder, zipped and handed to the visitor's browser,
  then removed (:func:`new_download_dir`, :func:`sweep_old_downloads`).
- Tabs that browse the server's disk (Product Analysis, Hi-Res Tiler) are hidden.
- Map tiles go through the main web port (see ``vresto.ui.tile_proxy``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from vresto._env import public_mode_enabled
from vresto.api.auth import CopernicusAuth
from vresto.api.config import CopernicusConfig

# Downloaded files older than this are removed, in case a visitor left before the
# browser download finished.
DOWNLOAD_MAX_AGE_S = 3600


def is_public_mode() -> bool:
    """Return True when vresto runs as a shared public website (``VRESTO_PUBLIC_MODE``)."""
    return public_mode_enabled()


def max_download_files() -> int:
    """Return the most band files one public download may contain (``VRESTO_PUBLIC_MAX_DOWNLOAD_FILES``, 0 = no limit)."""
    try:
        return max(0, int(os.getenv("VRESTO_PUBLIC_MAX_DOWNLOAD_FILES", "12")))
    except ValueError:
        return 12


class VisitorCredentials:
    """One visitor's Copernicus credentials, kept in memory for a single browser tab.

    The config built from them has no fallback to environment variables, so the server
    operator's own account or keys are never used on a visitor's behalf.
    """

    def __init__(self):
        """Start with no credentials."""
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.s3_access_key: Optional[str] = None
        self.s3_secret_key: Optional[str] = None
        self._auth: Optional[CopernicusAuth] = None
        self._manager = None
        self._lock = threading.Lock()

    def set(self, username: str = "", password: str = "", s3_access_key: str = "", s3_secret_key: str = "") -> None:
        """Replace the stored credentials; empty values clear that field."""
        self.forget()
        self.username = username or None
        self.password = password or None
        self.s3_access_key = s3_access_key or None
        self.s3_secret_key = s3_secret_key or None

    def has_account(self) -> bool:
        """Return True when a Copernicus username and password are both set."""
        return bool(self.username and self.password)

    def has_static_s3(self) -> bool:
        """Return True when both S3 keys are set."""
        return bool(self.s3_access_key and self.s3_secret_key)

    def has_download_access(self) -> bool:
        """Return True when downloads can be authorised (an account, or S3 keys on their own)."""
        return self.has_account() or self.has_static_s3()

    def config(self) -> CopernicusConfig:
        """Return a config holding only this visitor's credentials."""
        config = CopernicusConfig()
        config.username = self.username
        config.password = self.password
        config.s3_access_key = self.s3_access_key
        config.s3_secret_key = self.s3_secret_key
        return config

    def auth(self) -> CopernicusAuth:
        """Return this visitor's auth handler, so their bearer token is reused across requests."""
        with self._lock:
            if self._auth is None:
                self._auth = CopernicusAuth(self.config())
            return self._auth

    def products_manager(self):
        """Return a ``ProductsManager`` signed in as this visitor, created on first use."""
        from vresto.products import ProductsManager

        auth = self.auth()
        with self._lock:
            if self._manager is None:
                self._manager = ProductsManager(config=auth.config, auth=auth)
            return self._manager

    def forget(self) -> None:
        """Drop the cached sign-in and delete any temporary S3 keys it created in the visitor's account."""
        with self._lock:
            auth, self._auth, self._manager = self._auth, None, None
        if auth is not None and getattr(auth, "_s3_credentials", None):
            threading.Thread(target=_delete_temporary_keys, args=(auth,), name="vresto-forget-keys", daemon=True).start()

    def clear(self) -> None:
        """Remove all credentials, as when the visitor closes the tab."""
        self.set()


def _delete_temporary_keys(auth: CopernicusAuth) -> None:
    """Delete the temporary S3 keys ``auth`` created; best effort."""
    try:
        auth.delete_s3_credentials()
    except Exception as e:  # noqa: BLE001 - cleanup must never raise
        logger.debug(f"Could not delete temporary S3 keys: {e}")


def downloads_root() -> Path:
    """Return the folder that holds public-mode downloads until they reach the browser."""
    root = Path(os.getenv("VRESTO_PUBLIC_DOWNLOAD_DIR") or (Path(tempfile.gettempdir()) / "vresto_public_downloads"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_download_dir() -> Path:
    """Create an empty folder for one download, after removing stale ones."""
    sweep_old_downloads()
    path = downloads_root() / uuid.uuid4().hex
    path.mkdir()
    return path


def sweep_old_downloads(max_age_s: float = DOWNLOAD_MAX_AGE_S) -> None:
    """Remove download folders older than ``max_age_s`` seconds."""
    cutoff = time.time() - max_age_s
    for entry in downloads_root().iterdir():
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
        except OSError:
            pass


def package_for_browser(files: list[Path], work_dir: Path, archive_stem: str) -> Path:
    """Return the file to send to the browser: the file itself, or a zip of several.

    Band files are already compressed (JP2), so the zip only stores them.
    """
    import zipfile

    if len(files) == 1:
        return files[0]
    archive = work_dir / f"{archive_stem}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    for path in files:
        path.unlink(missing_ok=True)
    return archive
