"""Unit tests for API authentication module."""

from unittest.mock import Mock, patch

import pytest
import requests

from vresto.api.auth import AuthenticationError, CopernicusAuth
from vresto.api.config import CopernicusConfig


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return CopernicusConfig(username="test_user", password="test_pass")


@pytest.fixture
def auth(mock_config):
    """Create an auth instance with mock config."""
    return CopernicusAuth(config=mock_config)


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
        auth._access_token = "cached_token"

        with patch("requests.post") as mock_post:
            token = auth.get_access_token()

            assert token == "cached_token"
            mock_post.assert_not_called()

    def test_get_access_token_force_refresh(self, auth):
        """Test force refresh bypasses cache."""
        auth._access_token = "old_token"
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
        auth._access_token = "test_token"

        headers = auth.get_headers()

        assert headers["Authorization"] == "Bearer test_token"
        assert headers["Accept"] == "application/json"

    def test_get_s3_credentials_success(self, auth):
        """Test successful S3 credentials retrieval."""
        auth._access_token = "test_token"
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
        auth._access_token = "test_token"
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Failed to create temporary S3 credentials"):
                auth.get_s3_credentials()

    def test_delete_s3_credentials_success(self, auth):
        """Test successful S3 credentials deletion."""
        auth._access_token = "test_token"
        auth._s3_credentials = {"access_id": "to_delete", "secret": "secret"}
        mock_response = Mock()
        mock_response.status_code = 204

        with patch("requests.delete", return_value=mock_response):
            result = auth.delete_s3_credentials()

            assert result is True
            assert auth._s3_credentials is None

    def test_delete_s3_credentials_with_explicit_id(self, auth):
        """Test deletion with explicit access ID."""
        auth._access_token = "test_token"
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
        auth._access_token = "test_token"
        auth._s3_credentials = {"access_id": "to_delete", "secret": "secret"}
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("requests.delete", return_value=mock_response):
            result = auth.delete_s3_credentials()

            assert result is False


class TestAuthenticationError:
    """Tests for AuthenticationError exception."""

    def test_authentication_error_is_exception(self):
        """Test that AuthenticationError is an Exception."""
        error = AuthenticationError("Test error")

        assert isinstance(error, Exception)
        assert str(error) == "Test error"
