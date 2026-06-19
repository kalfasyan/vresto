"""Unit tests for API authentication module."""

import time
from unittest.mock import Mock, patch

import pytest
import requests

from vresto.api.auth import (
    AuthenticationError,
    CopernicusAuth,
    get_shared_auth,
    reset_shared_auth,
)
from vresto.api.config import CopernicusConfig


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return CopernicusConfig(username="test_user", password="test_pass")


@pytest.fixture
def auth(mock_config):
    """Create an auth instance with mock config."""
    return CopernicusAuth(config=mock_config)


def _prime_valid_token(auth: CopernicusAuth, token: str = "cached_token") -> None:
    """Fake a freshly-issued bearer token whose expiry is well in the future."""
    auth._access_token = token
    auth._access_expires_at = time.monotonic() + 600


class TestCopernicusAuth:
    """Tests for CopernicusAuth class."""

    def test_init_with_config(self, mock_config):
        """Test initialization with provided config."""
        auth = CopernicusAuth(config=mock_config)

        assert auth.config == mock_config
        assert auth._access_token is None
        assert auth._s3_credentials is None

    def test_init_without_config(self):
        """Test initialization creates default config."""
        with patch.dict("os.environ", {"COPERNICUS_USERNAME": "user", "COPERNICUS_PASSWORD": "pass"}):
            auth = CopernicusAuth()

            assert auth.config is not None
            assert auth.config.username == "user"

    def test_get_access_token_success(self, auth):
        """Test successful access token retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"access_token": "test_token_123"}'

        with patch("requests.post", return_value=mock_response) as mock_post:
            token = auth.get_access_token()

            assert token == "test_token_123"
            assert auth._access_token == "test_token_123"
            mock_post.assert_called_once()

    def test_get_access_token_cached(self, auth):
        """Test that cached token is returned without new request."""
        _prime_valid_token(auth, "cached_token")

        with patch("requests.post") as mock_post:
            token = auth.get_access_token()

            assert token == "cached_token"
            mock_post.assert_not_called()

    def test_get_access_token_force_refresh(self, auth):
        """Test force refresh bypasses cache."""
        _prime_valid_token(auth, "old_token")
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"access_token": "new_token"}'

        with patch("requests.post", return_value=mock_response):
            token = auth.get_access_token(force_refresh=True)

            assert token == "new_token"

    def test_get_access_token_failure(self, auth):
        """Test authentication failure raises error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Failed to retrieve access token"):
                auth.get_access_token()

    def test_get_access_token_network_error(self, auth):
        """Test network error raises AuthenticationError."""
        with patch("requests.post", side_effect=requests.RequestException("Network error")):
            with pytest.raises(AuthenticationError, match="Request failed"):
                auth.get_access_token()

    def test_get_headers(self, auth):
        """Test getting authentication headers."""
        _prime_valid_token(auth, "test_token")

        headers = auth.get_headers()

        assert headers["Authorization"] == "Bearer test_token"
        assert headers["Accept"] == "application/json"

    def test_get_s3_credentials_success(self, auth):
        """Test successful S3 credentials retrieval."""
        _prime_valid_token(auth, "test_token")
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_id": "s3_access", "secret": "s3_secret"}

        with patch("requests.post", return_value=mock_response):
            creds = auth.get_s3_credentials()

            assert creds["access_id"] == "s3_access"
            assert creds["secret"] == "s3_secret"
            assert auth._s3_credentials == creds

    def test_get_s3_credentials_cached(self, auth):
        """Test cached S3 credentials are returned."""
        auth._s3_credentials = {"access_id": "cached", "secret": "cached_secret"}

        with patch("requests.post") as mock_post:
            creds = auth.get_s3_credentials()

            assert creds == auth._s3_credentials
            mock_post.assert_not_called()

    def test_get_s3_credentials_failure(self, auth):
        """Test S3 credentials failure raises error."""
        _prime_valid_token(auth, "test_token")
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Failed to create temporary S3 credentials"):
                auth.get_s3_credentials()

    def test_delete_s3_credentials_success(self, auth):
        """Test successful S3 credentials deletion."""
        _prime_valid_token(auth, "test_token")
        auth._s3_credentials = {"access_id": "to_delete", "secret": "secret"}
        mock_response = Mock()
        mock_response.status_code = 204

        with patch("requests.delete", return_value=mock_response):
            result = auth.delete_s3_credentials()

            assert result is True
            assert auth._s3_credentials is None

    def test_delete_s3_credentials_with_explicit_id(self, auth):
        """Test deletion with explicit access ID."""
        _prime_valid_token(auth, "test_token")
        mock_response = Mock()
        mock_response.status_code = 204

        with patch("requests.delete", return_value=mock_response) as mock_delete:
            result = auth.delete_s3_credentials(access_id="explicit_id")

            assert result is True
            assert "explicit_id" in mock_delete.call_args[0][0]

    def test_delete_s3_credentials_no_credentials(self, auth):
        """Test deletion with no credentials returns False."""
        result = auth.delete_s3_credentials()

        assert result is False

    def test_delete_s3_credentials_failure(self, auth):
        """Test failed deletion returns False."""
        _prime_valid_token(auth, "test_token")
        auth._s3_credentials = {"access_id": "to_delete", "secret": "secret"}
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("requests.delete", return_value=mock_response):
            result = auth.delete_s3_credentials()

            assert result is False


class TestTokenExpiryAndRefresh:
    """Verify that the cached token's expiry is honoured and that an
    expired token triggers a refresh-token grant rather than a fresh
    password-grant login (which is what trips CDSE's concurrent-session cap).
    """

    def test_expired_access_token_triggers_refresh_grant(self, auth):
        # Cached access token is already past its deadline; refresh token
        # is still valid for another hour.
        auth._access_token = "old_access"
        auth._access_expires_at = time.monotonic() - 1
        auth._refresh_token = "saved_refresh"
        auth._refresh_expires_at = time.monotonic() + 3600

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"access_token": "renewed", "expires_in": 600, "refresh_token": "new_refresh", "refresh_expires_in": 3600}'

        with patch("requests.post", return_value=mock_response) as mock_post:
            token = auth.get_access_token()

        assert token == "renewed"
        assert auth._refresh_token == "new_refresh"
        # Exactly one HTTP call — the refresh grant — was made; no second
        # password-grant login was opened.
        assert mock_post.call_count == 1
        sent_data = mock_post.call_args.kwargs["data"]
        assert sent_data["grant_type"] == "refresh_token"
        assert sent_data["refresh_token"] == "saved_refresh"

    def test_refresh_grant_failure_falls_back_to_password_grant(self, auth):
        auth._access_token = "old_access"
        auth._access_expires_at = time.monotonic() - 1
        auth._refresh_token = "stale_refresh"
        auth._refresh_expires_at = time.monotonic() + 3600

        refresh_fail = Mock(status_code=400, text="invalid_grant")
        password_ok = Mock(
            status_code=200,
            text='{"access_token": "fresh_via_password", "expires_in": 600}',
        )

        with patch("requests.post", side_effect=[refresh_fail, password_ok]) as mock_post:
            token = auth.get_access_token()

        assert token == "fresh_via_password"
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[0].kwargs["data"]["grant_type"] == "refresh_token"
        assert mock_post.call_args_list[1].kwargs["data"]["grant_type"] == "password"
        # The bad refresh token must have been cleared so we don't loop next time.
        assert auth._refresh_token is None

    def test_password_grant_caches_expiry_from_response(self, auth):
        mock_response = Mock(
            status_code=200,
            text='{"access_token": "abc", "expires_in": 600, "refresh_token": "rt", "refresh_expires_in": 3600}',
        )
        before = time.monotonic()
        with patch("requests.post", return_value=mock_response):
            auth.get_access_token()
        after = time.monotonic()

        # Expiry is roughly now + (expires_in - safety margin = 30 s).
        assert before + 569 <= auth._access_expires_at <= after + 570
        assert auth._refresh_token == "rt"
        assert before + 3569 <= auth._refresh_expires_at <= after + 3570


class TestSharedAuthSingleton:
    """``get_shared_auth`` must hand out the same instance across the process,
    so multiple UI handlers reuse a single cached bearer token instead of
    each opening a brand-new Keycloak session.
    """

    def teardown_method(self):
        reset_shared_auth()

    def test_returns_same_instance(self):
        cfg = CopernicusConfig(username="u", password="p")
        first = get_shared_auth(config=cfg)
        second = get_shared_auth()
        assert first is second

    def test_reset_lets_us_rebuild(self):
        cfg = CopernicusConfig(username="u", password="p")
        first = get_shared_auth(config=cfg)
        reset_shared_auth()
        third = get_shared_auth(config=cfg)
        assert third is not first


class TestAuthenticationError:
    """Tests for AuthenticationError exception."""

    def test_authentication_error_is_exception(self):
        """Test that AuthenticationError is an Exception."""
        error = AuthenticationError("Test error")

        assert isinstance(error, Exception)
        assert str(error) == "Test error"
