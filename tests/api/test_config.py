"""Unit tests for API configuration module."""

import os
from unittest.mock import patch

import pytest

from vresto.api.config import CopernicusConfig


class TestCopernicusConfig:
    """Tests for CopernicusConfig class."""

    def test_init_with_explicit_credentials(self):
        """Test initialization with explicitly provided credentials."""
        config = CopernicusConfig(username="test_user", password="test_pass")

        assert config.username == "test_user"
        assert config.password == "test_pass"

    def test_init_from_env_vars(self):
        """Test initialization from environment variables."""
        with patch.dict(os.environ, {"COPERNICUS_USERNAME": "env_user", "COPERNICUS_PASSWORD": "env_pass"}):
            config = CopernicusConfig()

            assert config.username == "env_user"
            assert config.password == "env_pass"

    def test_init_explicit_overrides_env(self):
        """Test that explicit credentials override environment variables."""
        with patch.dict(os.environ, {"COPERNICUS_USERNAME": "env_user", "COPERNICUS_PASSWORD": "env_pass"}):
            config = CopernicusConfig(username="explicit_user", password="explicit_pass")

            assert config.username == "explicit_user"
            assert config.password == "explicit_pass"

    def test_init_with_no_credentials(self):
        """Test initialization with no credentials available."""
        with patch.dict(os.environ, {}, clear=True):
            config = CopernicusConfig()

            assert config.username is None
            assert config.password is None

    def test_validate_with_valid_credentials(self):
        """Test validation with valid credentials."""
        config = CopernicusConfig(username="user", password="pass")

        assert config.validate() is True

    def test_validate_with_missing_username(self):
        """Test validation fails with missing username."""
        with patch.dict(os.environ, {}, clear=True):
            config = CopernicusConfig(username=None, password="pass")

            assert config.validate() is False

    def test_validate_with_missing_password(self):
        """Test validation fails with missing password."""
        with patch.dict(os.environ, {}, clear=True):
            config = CopernicusConfig(username="user", password=None)

            assert config.validate() is False

    def test_validate_with_empty_credentials(self):
        """Test validation fails with empty credentials."""
        with patch.dict(os.environ, {}, clear=True):
            config = CopernicusConfig(username="", password="")

            assert config.validate() is False

    def test_get_credentials_success(self):
        """Test getting credentials when valid."""
        config = CopernicusConfig(username="user", password="pass")

        username, password = config.get_credentials()

        assert username == "user"
        assert password == "pass"

    def test_get_credentials_raises_on_invalid(self):
        """Test get_credentials raises ValueError when invalid."""
        with patch.dict(os.environ, {}, clear=True):
            config = CopernicusConfig(username=None, password=None)

            with pytest.raises(ValueError, match="Copernicus credentials not configured"):
                config.get_credentials()

    def test_api_endpoints_are_set(self):
        """Test that API endpoint URLs are configured."""
        assert CopernicusConfig.AUTH_URL.startswith("https://")
        assert CopernicusConfig.ODATA_BASE_URL.startswith("https://")
        assert CopernicusConfig.S3_ENDPOINT_URL.startswith("https://")
        assert CopernicusConfig.S3_KEYS_MANAGER_URL.startswith("https://")

    def test_client_id_is_set(self):
        """Test that client ID is configured."""
        assert CopernicusConfig.CLIENT_ID == "cdse-public"
