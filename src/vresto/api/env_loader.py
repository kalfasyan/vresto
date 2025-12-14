"""Utility to read/write simple .env files for local development."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


def write_env_file(path: Path, data: Dict[str, str]) -> None:
    lines = []
    for k, v in data.items():
        if v is None:
            continue
        safe = v.replace("\n", "\\n")
        lines.append(f"{k}={safe}")
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")


def parse_env_file(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        result[key] = val.replace("\\n", "\n")
    return result


def load_env(path: Optional[Path] = None) -> None:
    """Load variables from `.env` into `os.environ` without overwriting existing keys."""
    if path is None:
        path = Path.cwd() / ".env"
    for k, v in parse_env_file(path).items():
        if k not in os.environ:
            os.environ[k] = v
