from pathlib import Path

from src.vresto.api.env_loader import parse_env_file, write_env_file


def test_write_and_parse_env_file(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    data = {
        "COPERNICUS_USERNAME": "user1",
        "COPERNICUS_PASSWORD": "pass\nwithnewline",
        "COPERNICUS_S3_ACCESS_KEY": "AKIA...",
    }
    write_env_file(p, data)
    parsed = parse_env_file(p)
    assert parsed["COPERNICUS_USERNAME"] == "user1"
    assert parsed["COPERNICUS_PASSWORD"] == "pass\nwithnewline"
    assert parsed["COPERNICUS_S3_ACCESS_KEY"] == "AKIA..."
