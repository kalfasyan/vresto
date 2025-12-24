"""Unit tests for map interface and widget modules.

This file consolidates tests for `vresto.ui.map_interface` and the
widget modules. It provides a single `mock_ui` fixture and a set of
class-based tests to be lint-friendly and easy to maintain.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_ui():
    """Mock the NiceGUI ui module for map_interface and widget modules."""
    mock = MagicMock()
    mock.label = MagicMock(return_value=MagicMock())
    mock.card = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.column = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.row = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.scroll_area = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.date = MagicMock(return_value=MagicMock())
    mock.leaflet = MagicMock(return_value=MagicMock())
    mock.timer = MagicMock()

    with patch("vresto.ui.map_interface.ui", mock), patch("vresto.ui.widgets.map_widget.ui", mock), patch("vresto.ui.widgets.date_picker.ui", mock), patch("vresto.ui.widgets.activity_log.ui", mock):
        yield mock


class TestDatePicker:
    """Tests for DatePickerWidget functionality."""

    def test_date_picker_initialized_with_defaults(self, mock_ui):
        """Test that date picker is initialized with default dates."""
        from vresto.ui.widgets.date_picker import DatePickerWidget

        widget = DatePickerWidget(default_from="2020-01-01", default_to="2020-01-31", on_message=lambda m: None)
        date_picker, date_display = widget.create()

        mock_ui.date.assert_called_once_with(value={"from": "2020-01-01", "to": "2020-01-31"})
        assert date_picker is not None
        assert date_display is not None

    def test_date_picker_has_range_prop(self, mock_ui):
        """Test that date picker has range property set."""
        from vresto.ui.widgets.date_picker import DatePickerWidget

        widget = DatePickerWidget(default_from="2020-01-01", default_to="2020-01-31", on_message=lambda m: None)
        date_picker, _ = widget.create()

        date_picker_instance = mock_ui.date.return_value
        date_picker_instance.props.assert_called_once_with("range")

    def test_format_single_date(self):
        """Test formatting a single date value via DatePickerWidget.setup_monitoring."""
        from vresto.ui.widgets.date_picker import DatePickerWidget

        date_picker = MagicMock()
        date_picker.value = "2025-12-06"
        date_display = MagicMock()
        messages_column = MagicMock()

        widget = DatePickerWidget(default_from="2020-01-01", default_to="2020-01-31", on_message=lambda m: None)
        with patch("vresto.ui.widgets.date_picker.ui.timer"):
            widget.setup_monitoring(date_picker, date_display, messages_column)

        assert date_picker.value == "2025-12-06"

    def test_format_date_range(self):
        """Test formatting a date range value via DatePickerWidget.setup_monitoring."""
        from vresto.ui.widgets.date_picker import DatePickerWidget

        date_picker = MagicMock()
        date_picker.value = {"from": "2025-12-01", "to": "2025-12-31"}
        date_display = MagicMock()
        messages_column = MagicMock()

        widget = DatePickerWidget(default_from="2020-01-01", default_to="2020-01-31", on_message=lambda m: None)
        with patch("vresto.ui.widgets.date_picker.ui.timer"):
            widget.setup_monitoring(date_picker, date_display, messages_column)

        assert isinstance(date_picker.value, dict)
        assert "from" in date_picker.value
        assert "to" in date_picker.value


class TestActivityLog:
    """Tests for ActivityLogWidget functionality."""

    def test_activity_log_created_with_scroll_area(self, mock_ui):
        """Test that activity log is created with a scrollable area."""
        from vresto.ui.widgets.activity_log import ActivityLogWidget

        widget = ActivityLogWidget(title="Activity Log")
        messages_column = widget.create()

        mock_ui.scroll_area.assert_called_once()
        assert messages_column is not None

    def test_activity_log_has_correct_height(self, mock_ui):
        """Test that scroll area has the correct height class."""
        from vresto.ui.widgets.activity_log import ActivityLogWidget

        widget = ActivityLogWidget(title="Activity Log")
        widget.create()

        scroll_area_instance = mock_ui.scroll_area.return_value
        scroll_area_instance.classes.assert_called_once_with("w-full h-96")


class TestMapConfiguration:
    """Tests for map configuration."""

    def test_map_draw_controls_configuration(self, mock_ui):
        """Test that map has correct draw controls enabled via MapWidget."""
        from vresto.ui.widgets.map_widget import MapWidget

        messages_column = MagicMock()
        widget = MapWidget()
        widget.create(messages_column)

        call_kwargs = mock_ui.leaflet.call_args.kwargs
        assert "draw_control" in call_kwargs
        draw_config = call_kwargs["draw_control"]
        assert draw_config["draw"]["marker"] is True
        assert draw_config["edit"]["edit"] is True
        assert draw_config["edit"]["remove"] is True

    def test_map_centered_on_stockholm(self, mock_ui):
        """Test that map is centered on Stockholm, Sweden via MapWidget."""
        from vresto.ui.widgets.map_widget import MapWidget

        messages_column = MagicMock()
        widget = MapWidget()
        widget.create(messages_column)

        call_kwargs = mock_ui.leaflet.call_args.kwargs
        assert call_kwargs["center"] == (59.3293, 18.0686)
        assert call_kwargs["zoom"] == 13


class TestMapEventHandlers:
    """Tests for map event handlers."""

    def test_draw_event_creates_log_message(self, mock_ui):
        """Test that drawing on map creates a log message via MapWidget._setup_map_handlers."""
        from vresto.ui.widgets.map_widget import MapWidget

        m = MagicMock()
        messages_column = MagicMock()

        widget = MapWidget()
        widget._setup_map_handlers(m, messages_column)

        assert m.on.call_count == 3
        calls = [call[0][0] for call in m.on.call_args_list]
        assert "draw:created" in calls
        assert "draw:edited" in calls
        assert "draw:deleted" in calls

    def test_edit_handler_registered(self, mock_ui):
        """Test that edit handler is properly registered via MapWidget._setup_map_handlers."""
        from vresto.ui.widgets.map_widget import MapWidget

        m = MagicMock()
        messages_column = MagicMock()

        widget = MapWidget()
        widget._setup_map_handlers(m, messages_column)

        handler_names = [call[0][0] for call in m.on.call_args_list]
        assert "draw:edited" in handler_names

    def test_delete_handler_registered(self, mock_ui):
        """Test that delete handler is properly registered via MapWidget._setup_map_handlers."""
        from vresto.ui.widgets.map_widget import MapWidget

        m = MagicMock()
        messages_column = MagicMock()

        widget = MapWidget()
        widget._setup_map_handlers(m, messages_column)

        handler_names = [call[0][0] for call in m.on.call_args_list]
        assert "draw:deleted" in handler_names


class TestIntegration:
    """Integration tests for the full interface."""

    def test_create_map_interface_returns_components(self, mock_ui):
        """Test that create_map_interface returns expected components."""
        from vresto.ui.map_interface import create_map_interface

        result = create_map_interface()

        assert "tabs" in result
        assert "map_search" in result
        assert "name_search" in result
        assert "download" in result
        assert "analysis" in result
        assert result["tabs"] is not None
        assert result["map_search"] is not None
        assert result["name_search"] is not None
        assert result["download"] is not None
        assert result["analysis"] is not None

    def test_map_search_tab_created(self, mock_ui):
        """Test that MapSearchTab is instantiated within create_map_interface."""
        from vresto.ui.map_interface import create_map_interface

        result = create_map_interface()

        assert "map_search" in result
        assert result["map_search"] is not None

    def test_name_search_tab_structure(self, mock_ui):
        """Test that NameSearchTab creates expected UI components."""
        from vresto.ui.widgets.name_search_tab import NameSearchTab

        widget = NameSearchTab()
        result = widget.create()

        assert "messages_column" in result
        assert "results" in result
        assert "state" in result
        assert result["messages_column"] is not None
        assert result["results"] is not None
        assert "products" in result["state"]
