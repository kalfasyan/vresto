"""Pytest configuration to ensure tests can import the package from the `src/` layout.

Some CI environments (or test runners) don't set PYTHONPATH to the repo root.
This hook prepends the repository root to sys.path so tests can import `src.vresto...`
without requiring an editable install. It's a small, test-only convenience.
"""

import os
import sys


def pytest_configure(config):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
