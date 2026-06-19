"""Authentication module for Copernicus Data Space Ecosystem."""

import json
import threading
import time
from typing import Optional

import requests
from loguru import logger

from .config import CopernicusConfig

# Refresh the cached access token this many seconds *before* its real
# expiry, so an in-flight request never fires with an about-to-expire
# token. CDSE access tokens are typically valid for 600 s and refresh
# tokens for 3 600 s.
_TOKEN_REFRESH_MARGIN_S = 30


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class CopernicusAuth:
    """Handle authentication with Copernicus Data Space Ecosystem."""

    def __init__(self, config: Optional[CopernicusConfig] = None):
        """Initialize authentication handler.

        Args:
            config: CopernicusConfig instance. If not provided, will create one from env vars.
        """
        self.config = config or CopernicusConfig()
        self._access_token: Optional[str] = None
        self._access_expires_at: float = 0.0
        self._refresh_token: Optional[str] = None
        self._refresh_expires_at: float = 0.0
        self._s3_credentials: Optional[dict] = None
        # Serialise token acquisition so concurrent callers (e.g. several
        # tile-hover handlers fired back-to-back) don't each open their own
        # Keycloak session and trip CDSE's concurrent-session cap.
        self._token_lock = threading.Lock()

    def _token_still_valid(self) -> bool:
        return bool(self._access_token) and time.monotonic() < self._access_expires_at

    def _refresh_still_valid(self) -> bool:
        return bool(self._refresh_token) and time.monotonic() < self._refresh_expires_at

    def _store_token_payload(self, payload: dict) -> str:
        """Cache the access + refresh tokens and their expiry deadlines."""
        access = payload.get("access_token")
        if not access:
            raise AuthenticationError("Token response missing 'access_token'")
        now = time.monotonic()
        self._access_token = access
        # Default to 600 s if the IdP omits expires_in; subtract the safety
        # margin so we refresh slightly before the real expiry.
        expires_in = int(payload.get("expires_in", 600))
        self._access_expires_at = now + max(0, expires_in - _TOKEN_REFRESH_MARGIN_S)
        refresh = payload.get("refresh_token")
        if refresh:
            self._refresh_token = refresh
            refresh_expires_in = int(payload.get("refresh_expires_in", 3600))
            self._refresh_expires_at = now + max(0, refresh_expires_in - _TOKEN_REFRESH_MARGIN_S)
        return access

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Get access token for API calls.

        Returns the in-memory cached token if it is still valid. When it has
        expired (or ``force_refresh`` is set), uses the refresh-token grant
        if a refresh token is still valid, otherwise falls back to the
        password grant. This avoids opening a brand-new Keycloak session on
        every API call, which is what causes CDSE to lock the account after
        a burst of requests.

        Args:
            force_refresh: Force getting a new token even if one is cached

        Returns:
            Access token string

        Raises:
            AuthenticationError: If authentication fails
        """
        if not force_refresh and self._token_still_valid():
            return self._access_token  # type: ignore[return-value]

        with self._token_lock:
            # Re-check inside the lock — another thread may have just
            # refreshed the token while we were waiting.
            if not force_refresh and self._token_still_valid():
                return self._access_token  # type: ignore[return-value]

            if self._refresh_still_valid():
                try:
                    return self._do_refresh_grant()
                except AuthenticationError as e:
                    logger.warning(f"Refresh-token grant failed ({e}); falling back to password grant")
                    self._refresh_token = None
                    self._refresh_expires_at = 0.0

            return self._do_password_grant()

    def _do_password_grant(self) -> str:
        try:
            username, password = self.config.get_credentials()
        except ValueError as e:
            raise AuthenticationError(f"Credentials not configured: {e}")

        if not username or not password:
            raise AuthenticationError("Username or password cannot be empty")

        auth_data = {
            "client_id": self.config.CLIENT_ID,
            "grant_type": "password",
            "username": username,
            "password": password,
        }

        try:
            response = requests.post(
                self.config.AUTH_URL,
                data=auth_data,
                verify=True,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as e:
            raise AuthenticationError(f"Request failed: {e}")

        if response.status_code != 200:
            raise AuthenticationError(f"Failed to retrieve access token. Status code: {response.status_code}, Response: {response.text}")

        token = self._store_token_payload(json.loads(response.text))
        logger.info("Successfully obtained access token (password grant)")
        return token

    def _do_refresh_grant(self) -> str:
        data = {
            "client_id": self.config.CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

        try:
            response = requests.post(
                self.config.AUTH_URL,
                data=data,
                verify=True,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as e:
            raise AuthenticationError(f"Request failed: {e}")

        if response.status_code != 200:
            raise AuthenticationError(f"Refresh-token grant failed. Status code: {response.status_code}, Response: {response.text}")

        token = self._store_token_payload(json.loads(response.text))
        logger.info("Refreshed access token (refresh-token grant)")
        return token

    def get_headers(self) -> dict[str, str]:
        """Get authentication headers for API requests.

        Returns:
            Dictionary with Authorization and Accept headers
        """
        token = self.get_access_token()
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get_s3_credentials(self, force_refresh: bool = False) -> dict:
        """Get temporary S3 credentials for accessing data.

        Args:
            force_refresh: Force getting new credentials even if cached

        Returns:
            Dictionary with 'access_id' and 'secret' keys

        Raises:
            AuthenticationError: If credential creation fails
        """
        if self._s3_credentials and not force_refresh:
            return self._s3_credentials

        headers = self.get_headers()

        try:
            response = requests.post(self.config.S3_KEYS_MANAGER_URL, headers=headers, timeout=30)

            if response.status_code == 200:
                self._s3_credentials = response.json()
                logger.info(f"Successfully created temporary S3 credentials. Access ID: {self._s3_credentials['access_id']}")
                return self._s3_credentials
            else:
                raise AuthenticationError(f"Failed to create temporary S3 credentials. Status code: {response.status_code}, Response: {response.text}")
        except requests.RequestException as e:
            raise AuthenticationError(f"Request failed: {e}")

    def delete_s3_credentials(self, access_id: Optional[str] = None) -> bool:
        """Delete temporary S3 credentials.

        Args:
            access_id: Access ID to delete. If not provided, uses cached credentials.

        Returns:
            True if deletion was successful
        """
        if not access_id and self._s3_credentials:
            access_id = self._s3_credentials.get("access_id")

        if not access_id:
            logger.debug("No S3 credentials to delete")
            return False

        headers = self.get_headers()
        delete_url = f"{self.config.S3_KEYS_MANAGER_URL}/access_id/{access_id}"

        try:
            response = requests.delete(delete_url, headers=headers, timeout=30)

            if response.status_code == 204:
                logger.info(f"Successfully deleted S3 credentials: {access_id}")
                if self._s3_credentials and self._s3_credentials.get("access_id") == access_id:
                    self._s3_credentials = None
                return True
            else:
                logger.warning(f"Failed to delete S3 credentials. Status code: {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"Request to delete S3 credentials failed: {e}")
            return False


# ---------------------------------------------------------------------------
# Process-wide shared instance
# ---------------------------------------------------------------------------
# Constructing a fresh ``CopernicusAuth`` per request means a brand-new
# password-grant login (= new Keycloak session) every time, which trips
# CDSE's concurrent-session cap after a handful of tile clicks. UI handlers
# should call ``get_shared_auth()`` so a single cached bearer token (and its
# refresh token) is reused for the lifetime of the process.

_shared_auth_lock = threading.Lock()
_shared_auth: Optional[CopernicusAuth] = None


def get_shared_auth(config: Optional[CopernicusConfig] = None) -> CopernicusAuth:
    """Return a process-wide ``CopernicusAuth``, creating it on first call.

    The ``config`` argument is only honoured the first time, when the
    singleton is constructed; subsequent calls return the cached instance
    regardless of the argument.
    """
    global _shared_auth
    if _shared_auth is not None:
        return _shared_auth
    with _shared_auth_lock:
        if _shared_auth is None:
            _shared_auth = CopernicusAuth(config=config)
    return _shared_auth


def reset_shared_auth() -> None:
    """Drop the cached singleton. Intended for tests."""
    global _shared_auth
    with _shared_auth_lock:
        _shared_auth = None
