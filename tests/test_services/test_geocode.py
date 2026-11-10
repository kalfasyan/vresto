"""Tests for place search (vresto.services.geocode)."""

from unittest.mock import MagicMock, patch

import pytest

from vresto.services import geocode


@pytest.fixture(autouse=True)
def _clear_cache():
    geocode._lookup.cache_clear()
    yield
    geocode._lookup.cache_clear()


@pytest.mark.parametrize("text", ["50.85, 4.35", "50.85 4.35", " -33.9;18.4 "])
def test_coordinates_need_no_network(text):
    with patch("vresto.services.geocode.requests.get") as get:
        place = geocode.find_place(text)
    get.assert_not_called()
    assert place is not None
    min_lat, min_lon, max_lat, max_lon = place.bounds
    assert min_lat < place.lat < max_lat and min_lon < place.lon < max_lon


@pytest.mark.parametrize("text", ["", "   ", "91, 0", "0, 181"])
def test_empty_or_out_of_range_is_not_a_coordinate(text):
    assert geocode.parse_latlon(text) is None


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_name_lookup_returns_bounds_in_map_order():
    hit = {"display_name": "Leuven, Belgium", "lat": "50.88", "lon": "4.70", "boundingbox": ["50.80", "50.95", "4.60", "4.80"]}
    with patch("vresto.services.geocode.requests.get", return_value=_response([hit])) as get:
        place = geocode.find_place("Leuven")
        geocode.find_place("leuven")  # cached, case-insensitive
    assert get.call_count == 1
    assert "User-Agent" in get.call_args.kwargs["headers"]
    assert place.name == "Leuven, Belgium"
    assert place.bounds == (50.80, 4.60, 50.95, 4.80)


def test_no_match_returns_none():
    with patch("vresto.services.geocode.requests.get", return_value=_response([])):
        assert geocode.find_place("nowhere-at-all") is None
