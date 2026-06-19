"""Pytest configuration to ensure tests can import the package from the `src/` layout.

Some CI environments (or test runners) don't set PYTHONPATH to the repo root.
This hook prepends the repository root to sys.path so tests can import `src.vresto...`
without requiring an editable install. It's a small, test-only convenience.

It also isolates the test process from the developer's real AWS configuration so
that ``moto`` mocks always intercept ``boto3`` calls. Without this, a shared
``~/.aws/config`` (e.g. one that sets a custom ``endpoint_url`` for the default
profile) silently bypasses moto and tests fail with ``InvalidAccessKeyId``.
"""

import os
import sys


def pytest_configure(config):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Isolate boto3 from any developer/CI AWS configuration so moto can mock
    # cleanly. This must run before tests import boto3 clients.
    os.environ["AWS_CONFIG_FILE"] = os.devnull
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    for var in ("AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        os.environ.pop(var, None)
