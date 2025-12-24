# UI Widgets

This directory contains modular widget classes for the vresto web interface.

## Overview

The vresto UI has been refactored into reusable, self-contained widget classes. Each widget encapsulates a specific piece of functionality and can be imported and used independently.

## Existing Widgets

### Core Widgets (Already Extracted)

- **ActivityLogWidget** (`activity_log.py`)
  - Displays a scrollable activity log with messages
  - Reused across multiple tabs for status updates
  - Usage: `activity_log = ActivityLogWidget(title="Activity Log"); messages = activity_log.create()`

- **DatePickerWidget** (`date_picker.py`)
  - Date range selection with monitoring
  - Supports callbacks for date range changes
  - Usage: `date_picker = DatePickerWidget(on_date_change=callback); date_picker, display = date_picker.create()`

- **MapWidget** (`map_widget.py`)
  - Interactive Leaflet map with drawing controls
  - Extracts bounding box from drawn markers/polygons
  - Usage: `map_widget = MapWidget(center=(lat,lon), zoom=13, on_bbox_update=callback)`

- **SearchResultsPanelWidget** (`search_results_panel.py`)
  - Search filter controls (collection, product level, cloud cover, max results)
  - Handles search button state and loading indicators
  - Usage: `panel = SearchResultsPanelWidget(); results, trigger = panel.create(on_search=callback)`

- **ProductViewerWidget** (`product_viewer.py`)
  - Shows quicklook images and metadata in dialogs
  - Handles download and display of product information
  - Usage: `viewer = ProductViewerWidget(); await viewer.show_quicklook(product, messages_col)`

### Tab Widgets (New - Refactored from map_interface.py)

- **MapSearchTab** (`map_search_tab.py`)
  - Complete Map Search tab
  - Combines DatePickerWidget, MapWidget, and SearchResultsPanelWidget
  - Encapsulates catalog search logic and product filtering
  - State management for bbox, date range, and products
  - Product card rendering with quicklook/metadata buttons
  - Usage:
    ```python
    tab = MapSearchTab(
        on_quicklook=lambda p, col: show_quicklook(p, col),
        on_metadata=lambda p, col: show_metadata(p, col),
    )
    content = tab.create()
    ```

- **NameSearchTab** (`name_search_tab.py`)
  - Name-based product search
  - Parses product names for collection/level hints
  - Client-side filtering by date range and product level
  - Handles exact vs. contains matching
  - Generic pattern detection to prevent UI timeout
  - Product card rendering
  - Usage:
    ```python
    tab = NameSearchTab(
        on_quicklook=callback,
        on_metadata=callback,
    )
    content = tab.create()
    ```

- **DownloadTab** (`download_tab.py`)
  - Product download interface
  - Band fetching and selection
  - Resolution selection (60m, 20m, 10m, native)
  - Band filtering by resolution (10m, 20m, 60m)
  - Progress tracking with circular progress indicator
  - Activity logging
  - Usage:
    ```python
    tab = DownloadTab()
    content = tab.create()
    ```

- **ProductAnalysisTab** (`product_analysis_tab.py`)
  - Local product inspection and visualization
  - Folder scanning for downloaded products
  - Band discovery and listing
  - Single band heatmap visualization (Plotly)
  - RGB composite generation
  - All bands grid thumbnail view
  - Automatic image format conversion (JP2 to PNG)
  - Usage:
    ```python
    tab = ProductAnalysisTab()
    content = tab.create()
    ```

## Helper Functions

### ProductAnalysisTab Utilities

```python
def _compute_preview_shape(orig_h: int, orig_w: int, max_dim: int = PREVIEW_MAX_DIM) -> Tuple[int, int]
    # Compute preview dimensions preserving aspect ratio

def _resize_array_to_preview(arr, max_dim: int = PREVIEW_MAX_DIM)
    # Resize numpy arrays to preview-friendly sizes using PIL
```

## Architecture Benefits

### Modularity
- Each tab is self-contained and independent
- Widgets are composable and reusable
- Clear separation of concerns (UI layout vs. logic)

### Testability
- Individual widgets can be unit tested
- No global state (except for tab state objects)
- Dependencies are injected via callbacks

### Maintainability
- ~200-500 line files instead of 1400+ line monolith
- Clear class structure with documented methods
- Consistent naming conventions

### Extensibility
- New tabs can reuse existing widgets
- Product card display can be unified via inheritance
- Visualization helpers are extracted and reusable

## Integration with map_interface.py

The main `map_interface.py` file now acts as a thin orchestration layer:

```python
from vresto.ui.widgets.map_search_tab import MapSearchTab
from vresto.ui.widgets.name_search_tab import NameSearchTab
from vresto.ui.widgets.download_tab import DownloadTab
from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab

def create_map_interface():
    """Create tabs using widget classes"""
    # Header
    # Tab setup
    map_search = MapSearchTab(on_quicklook=..., on_metadata=...)
    name_search = NameSearchTab(on_quicklook=..., on_metadata=...)
    download = DownloadTab()
    analysis = ProductAnalysisTab()
    # Wire tabs and run
```

## Completed Steps

✅ **Updated map_interface.py** to use the new tab widgets
✅ **Extracted product viewing** (quicklook/metadata) into ProductViewerWidget
✅ **Removed global state dependencies** - widgets now use callbacks exclusively
✅ **Simplified date picker** - uses on_date_change callback instead of module-level state

## Remaining Next Steps

1. **Create visualization helpers package** for SCL rendering, image compositing
   - Extract SCL palette and rendering logic from product_analysis_tab.py
   - Extract image compositing utilities (RGB, preview resizing)
   - Create dedicated helpers module for reuse across tabs

2. **Add comprehensive tests** for each widget
   - Unit tests for widget initialization and create() methods
   - Integration tests for callback chains
   - Tests for search and filter logic

3. **Document callback interfaces** for inter-widget communication
   - Create interface specifications for all callbacks
   - Document expected callback signatures
   - Add examples of callback chaining between widgets
