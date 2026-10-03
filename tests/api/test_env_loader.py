"""Tests for .env loading and its interaction with CopernicusConfig."""

import os
from unittest.mock import patch

import pytest

from vresto.api.config import CopernicusConfig
from vresto.api.env_loader import load_env, parse_env_file, write_env_file


def test_config_invalid_search_provider():
    """Test that invalid search provider raises ValueError."""
    with pytest.raises(ValueError, match="Invalid search provider"):
        CopernicusConfig(search_provider="invalid_provider")


def test_config_masked_password():
    """Test password masking logic."""
    with patch.dict(os.environ, {}, clear=True):
        # Long password
        pw = "supersecretpassword"
        config = CopernicusConfig(password=pw)
        expected = pw[:2] + "*" * (len(pw) - 4) + pw[-2:]
        assert config.masked_password == expected

        # Short password
        config = CopernicusConfig(password="123")
        assert config.masked_password == "***"

        # No password
        config = CopernicusConfig(password=None)
        assert config.masked_password == "N/A"


def test_env_loader_roundtrip(tmp_path):
    """Test writing and reading .env file."""
    env_file = tmp_path / ".env"
    data = {"KEY1": "value1", "KEY2": "value with spaces", "KEY3": "value_with_newline\\nline2"}

    write_env_file(env_file, data)
    assert env_file.exists()

    parsed = parse_env_file(env_file)
    # Note: parse_env_file replaces \\n with \n
    assert parsed["KEY1"] == "value1"
    assert parsed["KEY2"] == "value with spaces"
    assert parsed["KEY3"] == "value_with_newline\nline2"


def test_env_loader_empty_or_no_file(tmp_path):
    """Test env loader with non-existent or empty file."""
    non_existent = tmp_path / "does_not_exist"
    assert parse_env_file(non_existent) == {}

    empty_file = tmp_path / "empty.env"
    empty_file.write_text("")
    assert parse_env_file(empty_file) == {}


def test_env_loader_search_parents(tmp_path, monkeypatch):
    """A .env in a parent directory is picked up when searching upwards."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    write_env_file(parent / ".env", {"TEST_VAR": "parent_val"})

    monkeypatch.delenv("TEST_VAR", raising=False)
    monkeypatch.chdir(child)

    load_env(search_parents=True)
    try:
        assert os.environ.get("TEST_VAR") == "parent_val"
    finally:
        # load_env writes to os.environ directly, so monkeypatch cannot undo it.
        os.environ.pop("TEST_VAR", None)


def test_config_backward_compatible_endpoint(monkeypatch):
    """COPERNICUS_ENDPOINT is honoured, but COPERNICUS_S3_ENDPOINT takes precedence."""
    monkeypatch.delenv("COPERNICUS_S3_ENDPOINT", raising=False)
    monkeypatch.setenv("COPERNICUS_ENDPOINT", "https://old-endpoint.com")
    assert CopernicusConfig().s3_endpoint == "https://old-endpoint.com"

    monkeypatch.setenv("COPERNICUS_S3_ENDPOINT", "https://new-endpoint.com")
    assert CopernicusConfig().s3_endpoint == "https://new-endpoint.com"


def test_config_reads_credentials_from_env_file(tmp_path, monkeypatch):
    """CopernicusConfig sees credentials loaded from a .env in the working directory."""
    write_env_file(tmp_path / ".env", {"COPERNICUS_USERNAME": "testuser_config", "COPERNICUS_PASSWORD": "testpassword_config"})

    monkeypatch.delenv("COPERNICUS_USERNAME", raising=False)
    monkeypatch.delenv("COPERNICUS_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)

    load_env()
    try:
        config = CopernicusConfig()
        assert config.username == "testuser_config"
        assert config.password == "testpassword_config"
    finally:
        os.environ.pop("COPERNICUS_USERNAME", None)
        os.environ.pop("COPERNICUS_PASSWORD", None)
