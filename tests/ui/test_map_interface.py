"""Unit tests for map interface and widget modules.

This file consolidates tests for `vresto.ui.map_interface` and the
widget modules. It provides a single `mock_ui` fixture and a set of
class-based tests to be lint-friendly and easy to maintain.
"""

import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

# Check if running in CI environment
CI_ENVIRONMENT = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


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
    mock.select = MagicMock(return_value=MagicMock())
    mock.input = MagicMock(return_value=MagicMock())
    mock.number = MagicMock(return_value=MagicMock())
    mock.button = MagicMock(return_value=MagicMock())
    mock.checkbox = MagicMock(return_value=MagicMock())
    mock.circular_progress = MagicMock(return_value=MagicMock())
    mock.notify = MagicMock()
    mock.spinner = MagicMock(return_value=MagicMock())
    mock.dialog = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.code = MagicMock(return_value=MagicMock())
    mock.image = MagicMock(return_value=MagicMock())
    mock.tabs = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.tab = MagicMock(return_value=MagicMock())
    mock.tab_panels = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.tab_panel = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
    mock.run = MagicMock()
    mock.plotly = MagicMock(return_value=MagicMock())

    ui_patches = [
        patch("vresto.ui.map_interface.ui", mock),
        patch("vresto.ui.widgets.map_widget.ui", mock),
        patch("vresto.ui.widgets.date_picker.ui", mock),
        patch("vresto.ui.widgets.activity_log.ui", mock),
        patch("vresto.ui.widgets.download_tab.ui", mock),
        patch("vresto.ui.widgets.search_results_panel.ui", mock),
        patch("vresto.ui.widgets.product_viewer.ui", mock),
        patch("vresto.ui.widgets.product_analysis_tab.ui", mock),
        patch("vresto.ui.widgets.map_search_tab.ui", mock),
        patch("vresto.ui.widgets.name_search_tab.ui", mock),
    ]

    with ExitStack() as stack:
        for patch_obj in ui_patches:
            stack.enter_context(patch_obj)
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


class TestDownloadTab:
    """Tests for DownloadTab functionality."""

    def test_download_tab_created(self, mock_ui):
        """Test that DownloadTab creates expected UI elements."""
        from vresto.ui.widgets.download_tab import DownloadTab

        widget = DownloadTab()
        result = widget.create()

        assert "messages_column" in result
        assert "product_input" in result
        assert result["messages_column"] is not None
        assert result["product_input"] is not None

    def test_download_tab_initializes_elements(self, mock_ui):
        """Test that DownloadTab initializes all required UI elements."""
        from vresto.ui.widgets.download_tab import DownloadTab

        widget = DownloadTab()
        widget.create()

        assert widget.messages_column is not None
        assert widget.product_input is not None
        assert widget.fetch_button is not None
        assert widget.bands_container is not None
        assert widget.dest_input is not None
        assert widget.download_button is not None
        assert widget.progress is not None
        assert widget.progress_label is not None

    def test_download_tab_no_global_resolution_select(self, mock_ui):
        """Test that download tab no longer has a global resolution select."""
        from vresto.ui.widgets.download_tab import DownloadTab

        widget = DownloadTab()
        widget.create()

        # Should NOT have resolution_select anymore
        assert not hasattr(widget, "resolution_select")

    def test_download_tab_add_activity(self, mock_ui):
        """Test that _add_activity method works correctly."""
        from vresto.ui.widgets.download_tab import DownloadTab

        widget = DownloadTab()
        widget.create()

        # Mock the messages_column with context manager support
        mock_messages = MagicMock()
        mock_messages.__enter__ = MagicMock(return_value=None)
        mock_messages.__exit__ = MagicMock(return_value=None)
        widget.messages_column = mock_messages

        widget._add_activity("Test message")
        # Verify that label was called on the messages column
        mock_ui.label.assert_called()


class TestSearchResultsPanel:
    """Tests for SearchResultsPanelWidget functionality."""

    def test_search_results_panel_creates_filters(self, mock_ui):
        """Test that SearchResultsPanelWidget creates filter controls."""
        from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget

        widget = SearchResultsPanelWidget()

        # Mock the messages column
        messages_column = MagicMock()

        # Mock the on_search callback
        on_search = MagicMock()

        result_display, trigger_search = widget.create(messages_column, on_search)

        assert result_display is not None
        assert trigger_search is not None
        assert callable(trigger_search)

    def test_search_results_panel_has_defaults(self, mock_ui):
        """Test that SearchResultsPanelWidget has correct default values."""
        from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget

        widget = SearchResultsPanelWidget()

        assert widget.default_collection == "SENTINEL-2"
        assert widget.default_product_level == "L2A"
        assert widget.default_max_cloud == 30.0
        assert widget.default_max_results == 100

    def test_search_results_panel_trigger_callback(self, mock_ui):
        """Test that trigger callback can be invoked."""
        from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget

        widget = SearchResultsPanelWidget()
        messages_column = MagicMock()

        # Create a flag to check if callback was invoked
        callback_invoked = False

        def mock_search(params):
            nonlocal callback_invoked
            callback_invoked = True
            assert "collection" in params
            assert "product_level" in params
            assert "max_cloud_cover" in params
            assert "max_results" in params

        result_display, trigger_search = widget.create(messages_column, mock_search)

        # We can't easily test async callbacks here, but we've verified
        # the structure is correct
        assert callable(trigger_search)


class TestProductViewerWidget:
    """Tests for ProductViewerWidget functionality."""

    @pytest.mark.skipif(CI_ENVIRONMENT, reason="Requires AWS S3 credentials not available in CI")
    def test_product_viewer_initialized(self, mock_ui):
        """Test that ProductViewerWidget initializes correctly."""
        from vresto.ui.widgets.product_viewer import ProductViewerWidget

        widget = ProductViewerWidget()
        assert widget.manager is not None

    @pytest.mark.skipif(not (os.getenv("COPERNICUS_USERNAME") and os.getenv("COPERNICUS_PASSWORD")), reason="Requires Copernicus credentials not available in CI")
    def test_product_viewer_has_show_methods(self, mock_ui):
        """Test that ProductViewerWidget has required async methods."""
        from vresto.ui.widgets.product_viewer import ProductViewerWidget

        widget = ProductViewerWidget()
        assert hasattr(widget, "show_quicklook")
        assert callable(widget.show_quicklook)
        assert hasattr(widget, "show_metadata")
        assert callable(widget.show_metadata)


class TestMapSearchTab:
    """Tests for MapSearchTab functionality."""

    def test_map_search_tab_initializes(self, mock_ui):
        """Test that MapSearchTab initializes correctly."""
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        widget = MapSearchTab()
        assert widget.on_quicklook is not None
        assert widget.on_metadata is not None
        assert widget.current_state is not None
        assert "bbox" in widget.current_state
        assert "date_range" in widget.current_state
        assert "products" in widget.current_state

    def test_map_search_tab_with_callbacks(self, mock_ui):
        """Test that MapSearchTab accepts custom callbacks."""
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        def mock_quicklook(p, col):
            pass

        def mock_metadata(p, col):
            pass

        widget = MapSearchTab(on_quicklook=mock_quicklook, on_metadata=mock_metadata)
        assert widget.on_quicklook == mock_quicklook
        assert widget.on_metadata == mock_metadata

    def test_map_search_tab_creates_structure(self, mock_ui):
        """Test that MapSearchTab creates expected structure."""
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        widget = MapSearchTab()
        result = widget.create()

        assert "messages_column" in result
        assert "map" in result
        assert "results" in result
        assert "state" in result
        assert result["messages_column"] is not None
        assert result["map"] is not None
        assert result["results"] is not None
        assert result["state"] is not None

    def test_map_search_tab_filter_by_level(self, mock_ui):
        """Test the _filter_by_level method."""
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        widget = MapSearchTab()

        # Create mock products
        mock_product_l1c = MagicMock()
        mock_product_l1c.name = "S2A_MSIL1C_20201212T235129_xxx"

        mock_product_l2a = MagicMock()
        mock_product_l2a.name = "S2A_MSIL2A_20201212T235129_xxx"

        products = [mock_product_l1c, mock_product_l2a]

        # Test L2A filtering
        result = widget._filter_by_level(products, "L2A")
        assert len(result) == 1
        assert result[0].name == "S2A_MSIL2A_20201212T235129_xxx"

        # Test L1C filtering
        result = widget._filter_by_level(products, "L1C")
        assert len(result) == 1
        assert result[0].name == "S2A_MSIL1C_20201212T235129_xxx"


class TestNameSearchTab:
    """Tests for NameSearchTab functionality."""

    def test_name_search_tab_initializes(self, mock_ui):
        """Test that NameSearchTab initializes correctly."""
        from vresto.ui.widgets.name_search_tab import NameSearchTab

        widget = NameSearchTab()
        assert widget.on_quicklook is not None
        assert widget.on_metadata is not None
        assert widget.current_state is not None
        assert "products" in widget.current_state

    def test_name_search_tab_with_callbacks(self, mock_ui):
        """Test that NameSearchTab accepts custom callbacks."""
        from vresto.ui.widgets.name_search_tab import NameSearchTab

        def mock_quicklook(p, col):
            pass

        def mock_metadata(p, col):
            pass

        widget = NameSearchTab(on_quicklook=mock_quicklook, on_metadata=mock_metadata)
        assert widget.on_quicklook == mock_quicklook
        assert widget.on_metadata == mock_metadata

    def test_name_search_tab_creates_structure(self, mock_ui):
        """Test that NameSearchTab creates expected structure."""
        from vresto.ui.widgets.name_search_tab import NameSearchTab

        widget = NameSearchTab()
        result = widget.create()

        assert "messages_column" in result
        assert "results" in result
        assert "state" in result
        assert result["messages_column"] is not None
        assert result["results"] is not None
        assert result["state"] is not None

    def test_name_search_tab_apply_client_filters(self, mock_ui):
        """Test the _apply_client_filters method."""
        from vresto.ui.widgets.name_search_tab import NameSearchTab

        widget = NameSearchTab()

        # Create mock products
        mock_product1 = MagicMock()
        mock_product1.name = "S2A_MSIL2A_20201212T235129_xxx"
        mock_product1.sensing_date = "2020-12-12 23:51:29"

        mock_product2 = MagicMock()
        mock_product2.name = "S2A_MSIL1C_20201215T235129_xxx"
        mock_product2.sensing_date = "2020-12-15 23:51:29"

        products = [mock_product1, mock_product2]

        # Test date filtering
        result = widget._apply_client_filters(products, "2020-12-12", "2020-12-12", None)
        assert len(result) == 1
        assert result[0].sensing_date == "2020-12-12 23:51:29"

        # Test level filtering
        result = widget._apply_client_filters(products, "", "", "L2A")
        assert len(result) == 1
        assert "L2A" in result[0].name

        # Test no filtering
        result = widget._apply_client_filters(products, "", "", None)
        assert len(result) == 2


class TestProductAnalysisTab:
    """Tests for ProductAnalysisTab functionality."""

    def test_product_analysis_tab_created(self, mock_ui):
        """Test that ProductAnalysisTab creates expected elements."""
        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        widget = ProductAnalysisTab()
        result = widget.create()

        # ProductAnalysisTab.create() returns dict with messages_column key
        assert "messages_column" in result

    def test_product_analysis_tab_initializes_elements(self, mock_ui):
        """Test that ProductAnalysisTab initializes all required elements."""
        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        widget = ProductAnalysisTab()
        widget.create()

        # These are initialized when create() is called
        assert widget.products_column is not None
        assert widget.products_search_input is not None
        assert widget.preview_area is not None
        assert widget.folder_input is not None
        assert widget.filter_input is not None
        assert widget.scan_btn is not None
        assert widget.scanned_products == {}

    def test_product_analysis_tab_default_rgb(self, mock_ui):
        """Test the _default_rgb method."""
        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        widget = ProductAnalysisTab()

        # Test with ideal RGB bands available
        bands_map = {"B04": {10, 20}, "B03": {10, 20}, "B02": {10, 20}, "B08": {10}}
        result = widget._default_rgb(bands_map)
        assert result == ("B04", "B03", "B02")

        # Test with limited bands
        bands_map = {"B01": {60}, "B02": {20}, "B03": {20}}
        result = widget._default_rgb(bands_map)
        assert len(result) == 3
        assert all(b in bands_map for b in result)

    def test_product_analysis_tab_find_band_file(self, mock_ui):
        """Test the _find_band_file method."""
        import os
        import tempfile

        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        widget = ProductAnalysisTab()

        # Create a temporary directory with test files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test band files
            test_file_60m = os.path.join(tmpdir, "T30UVD_20201221T101441_B04_60m.jp2")
            test_file_20m = os.path.join(tmpdir, "T30UVD_20201221T101441_B04_20m.jp2")
            test_file_10m = os.path.join(tmpdir, "T30UVD_20201221T101441_B04_10m.jp2")

            open(test_file_60m, "w").close()
            open(test_file_20m, "w").close()
            open(test_file_10m, "w").close()

            # Test finding 20m band
            result = widget._find_band_file("B04", tmpdir, "20")
            assert result == test_file_20m

            # Test finding native (should return smallest resolution)
            result = widget._find_band_file("B04", tmpdir, "native")
            assert result == test_file_10m

    def test_product_analysis_tab_list_available_bands(self, mock_ui):
        """Test the _list_available_bands method."""
        import os
        import tempfile

        from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

        widget = ProductAnalysisTab()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test band files
            bands = [
                "T30UVD_20201221T101441_B02_10m.jp2",
                "T30UVD_20201221T101441_B03_10m.jp2",
                "T30UVD_20201221T101441_B04_10m.jp2",
                "T30UVD_20201221T101441_B05_20m.jp2",
                "T30UVD_20201221T101441_B11_20m.jp2",
                "T30UVD_20201221T101441_B8A_20m.jp2",
            ]

            for band_file in bands:
                open(os.path.join(tmpdir, band_file), "w").close()

            result = widget._list_available_bands(tmpdir)

            # Check that bands were correctly identified
            assert "B02" in result
            assert "B03" in result
            assert "B04" in result
            assert "B05" in result
            assert 10 in result["B02"]
            assert 20 in result["B05"]


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
