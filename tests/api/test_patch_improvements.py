"""Tests for new configuration and env loading improvements."""

import os
from unittest.mock import patch

import pytest

from vresto.api.config import CopernicusConfig
from vresto.api.env_loader import parse_env_file, write_env_file


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
