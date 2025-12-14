**Sentinel Browser UI**

This page describes the web user interface and layout of the Sentinel Browser. It explains what a user sees on each tab and where important controls and information are located. The text is written for end users — no code references, only how to use the interface.

**General Layout**
- **Header**: The app title appears at the top of the page and remains visible when you switch tabs.
- **Tabs**: Tabs run horizontally near the top. Each tab opens a separate screen area with its own left/center/right layout.
- **Three-column layout (typical)**: Most tabs use a left sidebar, a central main area, and a right sidebar. The left and right sidebars contain controls and logs, while the center shows the main interactive content (map, lists, or previews).

**Map Search**
- **Where to find it**: Select the "Map Search" tab (map icon).
- **Top-left (left sidebar)**: Date selector card. Use the date control to choose a single date or a date range. Below the date card is the activity log that displays messages about your actions (searches performed, downloads, errors, etc.).
- **Center (main area)**: Large interactive map. You can drop a marker or draw shapes on the map using the drawing controls. Drawing a point or shape defines the search area used by catalog searches.
- **Top-right (right sidebar)**: Search controls card. Choose the satellite collection (for example Sentinel-2), product processing level, cloud-cover limit, and maximum results. The main search button triggers a search using the drawn map area and selected date range.
- **Right column, below search controls**: Results area — a scrollable list of products found by the search. Each result shows the product name, sensing date, size, and cloud cover (if available). Actions for each product include viewing a quicklook (image) and viewing metadata.
- **Activity notes**: Notifications are shown briefly near the top of the screen for status updates (search started, search finished, errors). The activity log in the left sidebar keeps a persistent record.

**Search by Name**
- **Where to find it**: Select the "Search by Name" tab (search icon).
- **Left column**: Product name input and search button. Enter a full or partial product name (wildcards or patterns are supported) and press the search button. Below the input is an activity log specific to name searches.
- **Center / main area**: Results list and summary. After searching, the main panel shows how many products the server returned and how many match client-side filters, plus a scrollable list of matching products. Each product card mirrors the map-search results: name, sensing date, size, cloud cover, and buttons for quicklook and metadata.
- **Quick actions**: Use the quicklook button to preview a product image and the metadata button to open product metadata in a scrollable dialog.

**Download Product**
- **Where to find it**: Select the "Download Product" tab (download icon).
- **Left column**: Product identifier input. Enter a product name or S3 path and press the button to fetch available bands. Choose the download resolution (Native, 60, 20, 10) and use the band selection area to mark which bands you want to download. Helper buttons are available to select all bands or select bands by resolution (10m / 20m / 60m). Set the destination folder where files will be saved.
- **Center / right column**: Activity log and progress. The right side shows an activity log of the download process plus a progress indicator and textual progress counter while files are being downloaded.
- **Download basics**: After selecting bands and destination, press the download button. The UI will show progress and add entries to the activity log as each file completes or fails.

**Product Analysis (Local)**
- **Where to find it**: Select the "Product Analysis" tab (folder icon).
- **Left column**: Local folder selector and scan controls. Enter a path to a folder containing already-downloaded products and press the scan button to discover products in that folder. There is also a simple substring filter to narrow results.
- **Center column**: Discovered products list. A dropdown lists discovered products and a scrollable area shows each discovered product with an "Inspect" action. Selecting or inspecting a product populates the preview area.
- **Right column**: Preview & Bands. This area shows available bands for the inspected product, a single-band selector, composite options (RGB), resolution hints, and a preview display area. Use the preview button to generate a quick browser-friendly visualization (single-band heatmap, RGB composite, or a grid of thumbnails for all bands). Notes on limits (in-browser preview resolution) and helpful tips appear near the controls.

**Common Controls & Messages**
- **Activity Log**: Most tabs include a scrollable activity log that records actions, errors, and status messages. Use it to review what the app has done.
- **Notifications**: Brief, transient messages appear near the top of the screen to confirm actions or warn about missing inputs (for example, trying to search without a map marker or without entering a product name).
- **Dialogs**: Quicklooks and product metadata open in modal dialogs so you can inspect images or XML/metadata without leaving the current tab.

**Basic Workflows**
- **Find by map**: Go to "Map Search", draw or drop a marker on the map, pick a date or date range, choose search filters on the right, and press the search button. View results on the right and open quicklooks or metadata as needed.
- **Find by name**: Go to "Search by Name", paste or type a product name (or a short pattern), press search, and inspect results. Use the quicklook button to preview images or the metadata button for details.
- **Download a product**: Open "Download Product", provide a product name or S3 path, fetch available bands, select bands and resolution, set destination, and press the download button. Watch progress and activity messages on the right.
- **Inspect local products**: Open "Product Analysis", point to a local download folder, scan for products, pick one from the list, and use the preview controls to build quick visualizations of bands.

**Tips**
- Draw or drop a marker before searching on the map — searches require a selected location.
- Use short date ranges to limit search results and speed up queries.
- Band previews in the browser are optimized for lower resolution; for full-resolution analysis use local tools or a tiler/viewer that supports full JP2 tiling.

**File**: `docs/ui.md`
