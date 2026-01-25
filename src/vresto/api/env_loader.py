"""Utility to read/write simple .env files for local development."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


def write_env_file(path: Path, data: Dict[str, str]) -> None:
    """Write environment variables to a .env file.

    Args:
        path: Path to the .env file
        data: Dictionary of environment variables
    """
    lines = []
    # Add a header if it's a new file
    if not path.exists():
        lines.append("# vresto environment configuration")

    for k, v in data.items():
        if v is None:
            continue
        safe = str(v).replace("\n", "\\n")
        lines.append(f"{k}={safe}")

    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    logger.info(f"Updated environment file: {path}")


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file into a dictionary.

    Args:
        path: Path to the .env file

    Returns:
        Dictionary of environment variables
    """
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            result[key] = val.replace("\\n", "\n")
    except Exception as e:
        logger.error(f"Error parsing .env file at {path}: {e}")
    return result


def load_env(path: Optional[Path] = None, search_parents: bool = True) -> None:
    """Load variables from `.env` into `os.environ` without overwriting existing keys.

    Args:
        path: Optional path to the .env file. Defaults to .env in current working directory.
        search_parents: If True and path is None, search for .env in parent directories.
    """
    if path is None:
        path = Path.cwd() / ".env"
        if not path.exists() and search_parents:
            # Search upwards for .env
            for parent in Path.cwd().parents:
                candidate = parent / ".env"
                if candidate.exists():
                    path = candidate
                    break

    if not path.exists():
        logger.debug(f"No .env file found at {path}")
        return

    env_vars = parse_env_file(path)
    loaded_count = 0
    for k, v in env_vars.items():
        if k not in os.environ:
            os.environ[k] = v
            loaded_count += 1

    if loaded_count > 0:
        logger.info(f"Loaded {loaded_count} environment variables from {path}")
