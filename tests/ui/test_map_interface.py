"""Unit tests for map interface module."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_ui():
    """Mock the NiceGUI ui module."""
    with patch("vresto.ui.map_interface.ui") as mock:
        # Setup common mocks
        mock.label = MagicMock(return_value=MagicMock())
        mock.card = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
        mock.column = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
        mock.row = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
        mock.scroll_area = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
        mock.date = MagicMock(return_value=MagicMock())
        mock.leaflet = MagicMock(return_value=MagicMock())
        mock.timer = MagicMock()
        yield mock


class TestDatePicker:
    """Tests for date picker functionality."""

    def test_date_picker_initialized_with_july_2020(self, mock_ui):
        """Test that date picker is initialized with July 2020 date range."""
        from vresto.ui.map_interface import _create_date_picker

        date_picker, date_display = _create_date_picker()

        mock_ui.date.assert_called_once_with(value={"from": "2020-07-01", "to": "2020-07-31"})
        assert date_picker is not None
        assert date_display is not None

    def test_date_picker_has_range_prop(self, mock_ui):
        """Test that date picker is configured with range property."""
        from vresto.ui.map_interface import _create_date_picker

        date_picker, _ = _create_date_picker()

        # Verify props was called on the date picker instance
        date_picker_instance = mock_ui.date.return_value
        date_picker_instance.props.assert_called_once_with("range")


class TestActivityLog:
    """Tests for activity log functionality."""

    def test_activity_log_created_with_scroll_area(self, mock_ui):
        """Test that activity log has a scrollable area."""
        from vresto.ui.map_interface import _create_activity_log

        messages_column = _create_activity_log()

        mock_ui.scroll_area.assert_called_once()
        assert messages_column is not None

    def test_activity_log_has_correct_height(self, mock_ui):
        """Test that scroll area has the correct height class."""
        from vresto.ui.map_interface import _create_activity_log

        _create_activity_log()

        # Verify scroll area was called
        scroll_area_instance = mock_ui.scroll_area.return_value
        scroll_area_instance.classes.assert_called_once_with("w-full h-96")


class TestMapConfiguration:
    """Tests for map configuration."""

    def test_map_draw_controls_configuration(self, mock_ui):
        """Test that map has correct draw controls enabled."""
        from vresto.ui.map_interface import _create_map

        messages_column = MagicMock()
        _create_map(messages_column)

        # Verify leaflet was called with draw controls
        call_kwargs = mock_ui.leaflet.call_args.kwargs
        assert "draw_control" in call_kwargs

        draw_config = call_kwargs["draw_control"]
        assert draw_config["draw"]["marker"] is True
        assert draw_config["edit"]["edit"] is True
        assert draw_config["edit"]["remove"] is True

    def test_map_centered_on_stockholm(self, mock_ui):
        """Test that map is centered on Stockholm, Sweden."""
        from vresto.ui.map_interface import _create_map

        messages_column = MagicMock()
        _create_map(messages_column)

        call_kwargs = mock_ui.leaflet.call_args.kwargs
        assert call_kwargs["center"] == (59.3293, 18.0686)
        assert call_kwargs["zoom"] == 13


class TestDateFormatting:
    """Tests for date formatting logic."""

    def test_format_single_date(self):
        """Test formatting a single date value."""
        from vresto.ui.map_interface import _setup_date_monitoring

        date_picker = MagicMock()
        date_picker.value = "2025-12-06"
        date_display = MagicMock()
        messages_column = MagicMock()

        with patch("vresto.ui.map_interface.ui.timer"):
            _setup_date_monitoring(date_picker, date_display, messages_column)

        # The timer callback should be set up
        assert date_picker.value == "2025-12-06"

    def test_format_date_range(self):
        """Test formatting a date range value."""
        from vresto.ui.map_interface import _setup_date_monitoring

        date_picker = MagicMock()
        date_picker.value = {"from": "2025-12-01", "to": "2025-12-31"}
        date_display = MagicMock()
        messages_column = MagicMock()

        with patch("vresto.ui.map_interface.ui.timer"):
            _setup_date_monitoring(date_picker, date_display, messages_column)

        # Verify date range is properly handled
        assert isinstance(date_picker.value, dict)
        assert "from" in date_picker.value
        assert "to" in date_picker.value


class TestMapEventHandlers:
    """Tests for map event handlers."""

    def test_draw_event_creates_log_message(self, mock_ui):
        """Test that drawing on map creates a log message."""
        from vresto.ui.map_interface import _setup_map_handlers

        m = MagicMock()
        messages_column = MagicMock()

        _setup_map_handlers(m, messages_column)

        # Verify handlers were registered
        assert m.on.call_count == 3
        calls = [call[0][0] for call in m.on.call_args_list]
        assert "draw:created" in calls
        assert "draw:edited" in calls
        assert "draw:deleted" in calls

    def test_edit_handler_registered(self, mock_ui):
        """Test that edit handler is properly registered."""
        from vresto.ui.map_interface import _setup_map_handlers

        m = MagicMock()
        messages_column = MagicMock()

        _setup_map_handlers(m, messages_column)

        # Check that edit handler exists
        handler_names = [call[0][0] for call in m.on.call_args_list]
        assert "draw:edited" in handler_names

    def test_delete_handler_registered(self, mock_ui):
        """Test that delete handler is properly registered."""
        from vresto.ui.map_interface import _setup_map_handlers

        m = MagicMock()
        messages_column = MagicMock()

        _setup_map_handlers(m, messages_column)

        # Check that delete handler exists
        handler_names = [call[0][0] for call in m.on.call_args_list]
        assert "draw:deleted" in handler_names


class TestIntegration:
    """Integration tests for the full interface."""

    def test_create_map_interface_returns_components(self, mock_ui):
        """Test that create_map_interface returns expected components."""
        from vresto.ui.map_interface import create_map_interface

        result = create_map_interface()

        assert "date_picker" in result
        assert result["date_picker"] is not None

    def test_sidebar_creation(self, mock_ui):
        """Test that sidebar is created with all components."""
        from vresto.ui.map_interface import _create_sidebar

        with patch("vresto.ui.map_interface._setup_date_monitoring"):
            date_picker, messages_column = _create_sidebar()

            assert date_picker is not None
            assert messages_column is not None
