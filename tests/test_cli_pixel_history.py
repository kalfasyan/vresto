"""``vresto-cli pixel-history``: table, summary, CSV output and the empty case."""

from datetime import datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from vresto.cli.main import app
from vresto.services.pixel_history import PixelObservation

runner = CliRunner()


def _obs(day, status, ndvi):
    return PixelObservation(datetime(2025, 6, day, 10, 30, tzinfo=timezone.utc), f"item{day}", "31UFS", status, ndvi=ndvi, scene_cloud_cover=20.0)


SERIES = [_obs(1, "clear", 0.6), _obs(6, "cloud", 0.1), _obs(11, "clear", 0.8)]


def test_pixel_history_prints_table_summary_and_writes_csv(tmp_path):
    out = tmp_path / "ndvi.csv"
    with patch("vresto.services.pixel_history.point_history", return_value=SERIES) as history:
        result = runner.invoke(app, ["pixel-history", "50.85", "4.35", "--start", "2025-06-01", "--end", "2025-06-30", "--tile", "31UFS", "-o", str(out)])

    assert result.exit_code == 0, result.output
    history.assert_called_once_with(50.85, 4.35, "2025-06-01", "2025-06-30", tile_code="31UFS", max_cloud=100.0, max_scenes=150)
    assert "3 scenes, 2 clear, NDVI 0.60 – 0.80 (mean 0.70)" in result.output
    assert "2025-06-06" in result.output
    assert out.read_text().splitlines()[0].startswith("date,datetime_utc,tile,status,ndvi")
    assert len(out.read_text().splitlines()) == 4


def test_pixel_history_clear_only_hides_flagged_scenes():
    with patch("vresto.services.pixel_history.point_history", return_value=SERIES):
        result = runner.invoke(app, ["pixel-history", "50.85", "4.35", "--start", "2025-06-01", "--end", "2025-06-30", "--clear-only"])
    assert result.exit_code == 0
    assert "2025-06-06" not in result.output and "2025-06-11" in result.output


def test_pixel_history_without_scenes_exits_with_error():
    with patch("vresto.services.pixel_history.point_history", return_value=[]):
        result = runner.invoke(app, ["pixel-history", "50.85", "4.35", "--start", "2025-06-01", "--end", "2025-06-30"])
    assert result.exit_code == 1
    assert "No Sentinel-2 scenes found" in result.output
