"""Download product tab widget for fetching bands and managing downloads."""

import asyncio
from pathlib import Path

from nicegui import ui

from vresto.products import ProductsManager
from vresto.products.downloader import ProductDownloader, _parse_s3_uri
from vresto.ui.widgets.activity_log import ActivityLogWidget


class DownloadTab:
    """Encapsulates the Download Product tab for band selection and downloads.

    Usage:
        tab_widget = DownloadTab()
        tab_content = tab_widget.create()
    """

    def __init__(self):
        """Initialize the Download tab."""
        # UI elements
        self.messages_column = None
        self.product_input = None
        self.fetch_button = None
        self.bands_container = None
        self.bands_table = None
        self.band_selections = {}  # band_name -> {res -> checkbox}
        self.dest_input = None
        self.download_button = None
        self.progress = None
        self.progress_label = None

    def create(self):
        """Create and return the Download Product tab UI."""
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full gap-6"):
                # Left column: product input and controls
                self._create_controls_panel()

                # Right column: activity log and progress
                self._create_activity_panel()

        return {
            "messages_column": self.messages_column,
            "product_input": self.product_input,
        }

    def _create_controls_panel(self):
        """Create the left panel with product input and download controls."""
        with ui.column().classes("w-[600px]"):
            with ui.card().classes("w-full"):
                ui.label("Download Product").classes("text-lg font-semibold mb-3")

                self.product_input = ui.input(
                    label="Product name or S3 path",
                    placeholder="S2A_MSIL2A_... or s3://.../PRODUCT.SAFE",
                ).classes("w-full mb-3")

                async def _on_fetch_click():
                    await self._handle_fetch()

                self.fetch_button = ui.button("📥 Fetch bands", on_click=_on_fetch_click).classes("w-full mb-2")
                self.fetch_button.props("color=primary")

                ui.label("Available bands and resolutions").classes("text-sm text-gray-600 mb-2")

                # Band selection helpers
                with ui.row().classes("w-full gap-2 mb-2"):
                    select_all_btn = ui.button("Select All").classes("text-sm")
                    deselect_all_btn = ui.button("Deselect All").classes("text-sm")
                    select_10m_btn = ui.button("Select all 10m").classes("text-sm")
                    select_20m_btn = ui.button("Select all 20m").classes("text-sm")
                    select_60m_btn = ui.button("Select all 60m").classes("text-sm")

                # Bands table container
                self.bands_container = ui.column().classes("w-full gap-1")

                # Band selection logic
                def _select_all(val: bool):
                    for band_res in self.band_selections.values():
                        for cb in band_res.values():
                            cb.value = val

                def _select_by_res(res_target: int, val: bool = True):
                    for band_res in self.band_selections.values():
                        if res_target in band_res:
                            band_res[res_target].value = val

                select_all_btn.on_click(lambda: _select_all(True))
                deselect_all_btn.on_click(lambda: _select_all(False))
                select_10m_btn.on_click(lambda: _select_by_res(10, True))
                select_20m_btn.on_click(lambda: _select_by_res(20, True))
                select_60m_btn.on_click(lambda: _select_by_res(60, True))

                self.dest_input = ui.input(
                    label="Destination directory",
                    value=str(Path.home() / "vresto_downloads"),
                ).classes("w-full mt-3 mb-3")

                async def _on_download_click():
                    await self._handle_download()

                self.download_button = ui.button("⬇️ Download selected", on_click=_on_download_click).classes("w-full")
                self.download_button.props("color=primary")

                # Progress UI
                self.progress = ui.circular_progress(value=0.0, max=1.0, size="lg", show_value=False).classes("m-auto mt-2")
                self.progress_label = ui.label("").classes("text-sm text-gray-600 mt-1")

    def _create_activity_panel(self):
        """Create the right panel with activity log."""
        with ui.column().classes("flex-1"):
            activity_log = ActivityLogWidget(title="Activity Log")
            self.messages_column = activity_log.create()

    def _add_activity(self, msg: str):
        """Add a message to the activity log."""
        if self.messages_column:
            with self.messages_column:
                ui.label(msg).classes("text-sm text-gray-700 break-words")

    async def _handle_fetch(self):
        """Handle the fetch bands button click."""
        product = self.product_input.value.strip() if self.product_input.value else ""
        if not product:
            ui.notify(
                "⚠️ Enter a product name or S3 path first",
                position="top",
                type="warning",
            )
            self._add_activity("⚠️ Fetch failed: no product provided")
            return

        self._add_activity(f"🔎 Resolving bands for: {product}")
        try:
            mgr = ProductsManager()
            # Construct S3 path from product name
            s3_path = mgr._construct_s3_path_from_name(product)

            # Use ProductDownloader to list available bands
            pd = ProductDownloader(s3_client=mgr.s3_client)
            bands_map = pd.list_available_bands(s3_path)

            self.bands_container.clear()
            self.band_selections.clear()

            if not bands_map:
                self._add_activity("ℹ️ No band files found for this product (or product not found)")
                ui.notify("No bands found", position="top", type="warning")
                return

            # Determine all unique resolutions
            all_resolutions = sorted(list(set().union(*bands_map.values())))

            with self.bands_container:
                with ui.grid(columns=len(all_resolutions) + 1).classes("w-full gap-2 items-center"):
                    # Header
                    ui.label("Band").classes("font-bold")
                    for res in all_resolutions:
                        ui.label(f"{res}m").classes("font-bold text-center")

                    # Rows
                    for band in sorted(bands_map.keys()):
                        ui.label(band)
                        res_set = bands_map[band]
                        self.band_selections[band] = {}
                        for res in all_resolutions:
                            if res in res_set:
                                cb = ui.checkbox().classes("mx-auto")
                                self.band_selections[band][res] = cb
                            else:
                                ui.label("-").classes("text-gray-300 text-center")

            self._add_activity(f"✅ Found bands: {', '.join(sorted(bands_map.keys()))}")
            ui.notify("Bands fetched", position="top", type="positive")

        except Exception as e:
            self._add_activity(f"❌ Error fetching bands: {e}")
            ui.notify(f"Error: {e}", position="top", type="negative")

    async def _handle_download(self):
        """Handle the download button click."""
        product = self.product_input.value.strip() if self.product_input.value else ""
        if not product:
            ui.notify(
                "⚠️ Enter a product name or S3 path first",
                position="top",
                type="warning",
            )
            self._add_activity("⚠️ Download failed: no product provided")
            return

        # Get selected band/resolution pairs
        selected_tasks_raw = []  # List of (band, resolution)
        for band, res_map in self.band_selections.items():
            for res, cb in res_map.items():
                if cb.value:
                    selected_tasks_raw.append((band, res))

        if not selected_tasks_raw:
            ui.notify(
                "⚠️ Select at least one band and resolution to download",
                position="top",
                type="warning",
            )
            self._add_activity("⚠️ Download failed: no bands selected")
            return

        dest_dir = self.dest_input.value or str(Path.home() / "vresto_downloads")

        self._add_activity(f"⬇️ Starting download for {product}")

        try:
            mgr = ProductsManager()
            pd = ProductDownloader(s3_client=mgr.s3_client)

            # Resolve product S3 prefix
            s3_path = mgr._construct_s3_path_from_name(product)
            img_uri = pd.mapper.resolve_img_prefix(s3_path)
            bucket, _ = _parse_s3_uri(img_uri)

            # Build keys for each selection, avoiding duplicates
            # (Sentinel-2 L1C bands map to a single native resolution even if we show multiple checkboxes)
            keys_set = set()
            for band, res in selected_tasks_raw:
                found_key = pd.mapper.find_band_key(img_uri, band, res)
                if found_key:
                    keys_set.add(f"s3://{bucket}/{found_key}")
                else:
                    self._add_activity(f"⚠️ Band {band} at {res}m not found on S3")

            keys = sorted(list(keys_set))
            total = len(keys)

            if not keys:
                ui.notify(
                    "⚠️ No downloadable files found for selection",
                    position="top",
                    type="warning",
                )
                self._add_activity("⚠️ Download failed: no files found")
                return

            # Initialize progress
            try:
                self.progress.set_value(0.0)
            except Exception:
                self.progress.value = 0.0
            self.progress_label.text = f"0.0% (0 / {total})"
            self._add_activity(f"⬇️ Downloading {total} unique files to {dest_dir}")

            downloaded = []
            for i, s3uri in enumerate(keys, start=1):
                try:
                    bucket, key = _parse_s3_uri(s3uri)
                    # Preserve S3 structure locally
                    dest = Path(dest_dir) / key
                    path = await asyncio.to_thread(pd._download_one, s3uri, dest, False)
                    downloaded.append(path)

                    # Update progress
                    frac = float(i) / float(total) if total else 1.0
                    try:
                        self.progress.set_value(frac)
                    except Exception:
                        self.progress.value = frac
                    self.progress_label.text = f"{frac * 100:.1f}% ({i} / {total})"
                    self._add_activity(f"✅ Downloaded {Path(path).name}")
                except Exception as ex:
                    self._add_activity(f"❌ Failed to download {s3uri}: {ex}")

            self._add_activity(f"✅ Download completed: {len(downloaded)} of {total} files")
            ui.notify(
                f"Download finished: {len(downloaded)} files",
                position="top",
                type="positive",
            )
        except Exception as e:
            self._add_activity(f"❌ Download error: {e}")
            ui.notify(f"Download failed: {e}", position="top", type="negative")
