"""Product Analysis tab widget for inspecting locally downloaded products."""

import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from loguru import logger
from nicegui import ui

from vresto.products.downloader import _BAND_RE
from vresto.ui.visualization.helpers import (
    PREVIEW_MAX_DIM,
    compute_preview_shape,
    create_scl_plotly_figure,
    resize_array_to_preview,
)


class ProductAnalysisTab:
    """Encapsulates the Product Analysis tab for inspecting local products.

    Usage:
        tab_widget = ProductAnalysisTab()
        tab_content = tab_widget.create()
    """

    def __init__(self):
        """Initialize the Product Analysis tab."""
        self.messages_column = None
        self.products_column = None
        self.products_search_input = None
        self.preview_area = None
        self.folder_input = None
        self.filter_input = None
        self.scan_btn = None
        self.scanned_products = {}
        self.all_product_cards = {}
        self.search_value = ""
        self._last_search_value = ""
        self._search_timer = None
        self.map_widget = None

    def create(self):
        """Create and return the Product Analysis tab UI."""
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full gap-6"):
                # Left: folder selector and product list
                with ui.column().classes("w-80"):
                    self._create_controls_panel()
                    self._create_products_panel()

                # Right: preview and bands
                self._create_preview_panel()

        return {
            "messages_column": self.messages_column,
        }

    def _create_controls_panel(self):
        """Create the left panel with folder selection."""
        with ui.card().classes("w-full"):
            ui.label("Downloaded Products").classes("text-lg font-semibold mb-3")

            self.folder_input = ui.input(
                label="Download folder",
                value=str(Path.home() / "vresto_downloads"),
            ).classes("w-full mb-3")

            async def _on_scan():
                await self._scan_folder()

            self.scan_btn = ui.button("🔎 Scan folder", on_click=_on_scan).classes("w-full")

            ui.label("Filter (substring)").classes("text-sm text-gray-600 mt-3")
            self.filter_input = ui.input(placeholder="partial product name...").classes("w-full mb-2")

    def _create_products_panel(self):
        """Create the middle panel with product list."""
        with ui.card().classes("w-full"):
            ui.label("Products").classes("text-lg font-semibold mb-3")
            self.products_search_input = ui.input(placeholder="Search products...").classes("w-full mb-2")

            with ui.scroll_area().classes("w-full h-72"):
                self.products_column = ui.column().classes("w-full gap-2")

            # Store previous search value to detect changes
            self._last_search_value = ""

            # Create a timer to watch for search input changes
            def _check_search_input():
                current_value = self.products_search_input.value or ""
                if current_value != self._last_search_value:
                    self._last_search_value = current_value
                    self._filter_and_display_products()

            # Use a timer to check for changes frequently
            ui.timer(0.1, _check_search_input)

    def _create_preview_panel(self):
        """Create the right panel with preview controls."""
        with ui.column().classes("flex-1"):
            with ui.card().classes("w-full h-fit"):
                ui.label("Preview & Bands").classes("text-lg font-semibold mb-3")
                self.preview_area = ui.column().classes("w-full")

    def _filter_and_display_products(self):
        """Filter products based on search input and display matching ones."""
        search_text = (self.products_search_input.value or "").strip().lower()
        self.products_column.clear()

        matching_products = []
        if search_text:
            # Filter products by search text
            for name, path in self.scanned_products.items():
                if search_text in name.lower():
                    matching_products.append((name, path))
        else:
            # Show all products if search is empty
            matching_products = list(self.scanned_products.items())

        # Display matching products as cards
        for name, path in matching_products:
            with self.products_column:
                with ui.card().classes("w-full p-2 bg-gray-50"):
                    ui.label(name).classes("text-xs font-mono break-all")
                    with ui.row().classes("w-full gap-2 mt-2"):

                        async def _on_inspect(pp=path):
                            await self._inspect_local_product(pp)

                        ui.button("🔍 Inspect", on_click=_on_inspect).classes("text-xs")

    async def _scan_folder(self):
        """Scan folder for downloaded products."""

        def add_message(text: str):
            if self.messages_column:
                with self.messages_column:
                    ui.label(text).classes("text-sm text-gray-700 break-words")

        root = self.folder_input.value or ""
        root = os.path.expanduser(root)
        self.products_column.clear()
        self.scanned_products.clear()

        if not root or not os.path.exists(root):
            ui.notify("⚠️ Folder does not exist", position="top", type="warning")
            add_message("⚠️ Scan failed: folder does not exist")
            return

        # Show loading state
        self.scan_btn.enabled = False
        original_text = getattr(self.scan_btn, "text", "🔎 Scan folder")
        self.scan_btn.text = "⏳ Scanning..."
        add_message(f"🔎 Scanning folder: {root}")

        # Discover .SAFE directories
        found_set = set()
        for dirpath, dirnames, filenames in os.walk(root):
            for d in list(dirnames):
                if d.endswith(".SAFE"):
                    found_set.add(os.path.join(dirpath, d))

            if "IMG_DATA" in dirnames:
                img_dir = os.path.join(dirpath, "IMG_DATA")
                product_root = os.path.abspath(os.path.join(img_dir, "..", ".."))
                cur = product_root
                found_safe = False
                while cur and cur != os.path.dirname(cur):
                    if cur.endswith(".SAFE"):
                        found_set.add(cur)
                        found_safe = True
                        break
                    cur = os.path.dirname(cur)
                if not found_safe:
                    found_set.add(product_root)

        found = sorted(found_set)

        # Apply filter
        flt = (self.filter_input.value or "").strip().lower()
        if flt:
            found = [p for p in found if flt in os.path.basename(p).lower()]

        if not found:
            add_message("ℹ️ No products found in folder")
            ui.notify("No products found", position="top", type="info")
            self.scan_btn.enabled = True
            self.scan_btn.text = original_text
            return

        add_message(f"✅ Found {len(found)} products")

        names = []
        for p in sorted(found):
            pname = os.path.basename(p)
            display_name = pname[:-5] if pname.upper().endswith(".SAFE") else pname
            names.append(display_name)
            self.scanned_products[display_name] = p

        # Display products (filtered or all)
        if names:
            # Clear search input
            self.products_search_input.value = ""
            # Display filtered products
            self._filter_and_display_products()
            # Auto-inspect first product
            await self._inspect_local_product(self.scanned_products[names[0]])

        self.scan_btn.enabled = True
        self.scan_btn.text = original_text

    async def _inspect_local_product(self, path: str):
        """Inspect a local product and show band information."""
        self.preview_area.clear()
        try:
            # Find IMG_DATA directory
            img_root = self._find_img_data(path)

            if not img_root:
                with self.preview_area:
                    ui.label("No image bands found locally").classes("text-sm text-gray-600")
                return

            # List available bands
            bands_map = self._list_available_bands(img_root)

            # Determine all unique resolutions
            all_resolutions = sorted(list(set().union(*bands_map.values())))

            with self.preview_area:
                ui.label(f"Product: {os.path.basename(path)}").classes("text-sm font-semibold")
                ui.label(f"IMG_DATA: {img_root}").classes("text-xs text-gray-600 mb-2")

                ui.label("Available bands and resolutions:").classes("text-sm text-gray-600 mt-1")
                with ui.card().classes("w-full p-2 bg-gray-50 mb-4"):
                    with ui.grid(columns=len(all_resolutions) + 1).classes("w-full gap-2 items-center"):
                        # Header
                        ui.label("Band").classes("font-bold text-xs")
                        for res in all_resolutions:
                            ui.label(f"{res}m").classes("font-bold text-xs text-center")

                        # Rows
                        for band in sorted(bands_map.keys()):
                            ui.label(band).classes("text-xs font-mono")
                            res_set = bands_map[band]
                            for res in all_resolutions:
                                if res in res_set:
                                    ui.icon("check_circle", color="success").classes("text-xs mx-auto")
                                else:
                                    ui.label("-").classes("text-gray-300 text-xs text-center")

                # Preview controls
                # Default to SCL if available, otherwise use first band
                default_band = "SCL" if "SCL" in bands_map else (sorted(bands_map.keys())[0] if bands_map else None)
                single_band_select = ui.select(
                    options=sorted(bands_map.keys()),
                    label="Single band to preview",
                    value=default_band,
                ).classes("w-48 mb-2")

                ui.label("Note: 'RGB composite' composes three bands (e.g. B04,B03,B02) to create an approximate natural-color image.").classes("text-xs text-gray-600 mb-2")

                RES_NATIVE_LABEL = "Native (best available per band)"
                with ui.row().classes("w-full gap-2 mt-2 mb-2"):
                    resolution_select = ui.select(options=["60", RES_NATIVE_LABEL], value="60").classes("w-48")
                    mode_select = ui.select(
                        options=["Single band", "RGB composite", "All bands"],
                        value="Single band",
                    ).classes("w-48")

                ui.label("Important: Browser previews only support 60m resolution (or Native downsampled). For high-resolution (10m/20m) inspection, use the 'Hi-Res Tiler' tab which utilizes a local tile server.").classes(
                    "text-xs text-blue-600 mb-2 font-semibold"
                )

                preview_btn = ui.button("▶️ Preview").classes("text-sm mb-2")
                preview_display = ui.column().classes("w-full mt-2")

                async def _show_preview():
                    original_text = getattr(preview_btn, "text", "▶️ Preview")
                    try:
                        preview_btn.text = "⏳ Previewing..."
                    except Exception:
                        pass
                    preview_btn.enabled = False

                    try:
                        import asyncio

                        await asyncio.sleep(0.05)
                    except Exception:
                        pass

                    try:
                        mode = mode_select.value
                        res_raw = resolution_select.value
                        resolution = "native" if res_raw == RES_NATIVE_LABEL else int(res_raw)

                        if mode == "RGB composite":
                            rgb_bands = self._default_rgb(bands_map)
                            await self._build_and_show_rgb(rgb_bands, img_root, resolution, preview_display)
                        elif mode == "Single band":
                            band = single_band_select.value
                            if not band:
                                ui.notify(
                                    "⚠️ No band selected for single-band preview",
                                    position="top",
                                    type="warning",
                                )
                            else:
                                await self._build_and_show_single(band, img_root, resolution, preview_display)
                        else:  # All bands
                            all_bands = sorted(bands_map.keys())
                            if not all_bands:
                                ui.notify(
                                    "⚠️ No bands available to show",
                                    position="top",
                                    type="warning",
                                )
                            else:
                                await self._build_and_show_all(all_bands, img_root, resolution, preview_display)
                    finally:
                        try:
                            preview_btn.text = original_text
                        except Exception:
                            pass
                        preview_btn.enabled = True

                preview_btn.on_click(lambda: _show_preview())

        except Exception as e:
            logger.error(f"Error inspecting local product: {e}")
            self.preview_area.clear()
            with self.preview_area:
                ui.label(f"Error: {e}").classes("text-sm text-red-600")

    def _find_img_data(self, path: str) -> Optional[str]:
        """Find IMG_DATA directory in product."""
        if path.endswith(".SAFE"):
            granule = os.path.join(path, "GRANULE")
            if os.path.isdir(granule):
                for g in os.scandir(granule):
                    img = os.path.join(g.path, "IMG_DATA")
                    if os.path.isdir(img):
                        return img
        else:
            granule = os.path.join(path, "GRANULE")
            if os.path.isdir(granule):
                for g in os.scandir(granule):
                    img = os.path.join(g.path, "IMG_DATA")
                    if os.path.isdir(img):
                        return img

        # Fallback: search recursively for jp2 files
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.lower().endswith(".jp2"):
                    return os.path.dirname(os.path.join(root, f))

        return None

    def _list_available_bands(self, img_root: str) -> dict:
        """List available bands in IMG_DATA directory."""
        bands_map = {}
        # Support recursive search in case of nested resolution folders or different structures
        for root, dirs, files in os.walk(img_root):
            for f in files:
                m = _BAND_RE.search(f)
                if not m:
                    # Special case for TCI in some versions where regex might not perfectly match
                    if "TCI" in f.upper() and f.lower().endswith((".jp2", ".tif")):
                        res = None
                        if "10m" in root:
                            res = 10
                        elif "20m" in root:
                            res = 20
                        elif "60m" in root:
                            res = 60
                        # Try to find a default resolution if none found in path
                        if res is None:
                            res = 10
                        bands_map.setdefault("TCI", set()).add(res)
                    continue
                band = m.group("band").upper()
                # Handle both L2A (with resolution) and L1C (without resolution) formats
                res_str = m.group("res")
                if res_str:
                    # L2A format: resolution is in the filename
                    res = int(res_str)
                else:
                    # L1C format: use native resolution lookup
                    # Import the L1C band resolutions mapping
                    from vresto.products.downloader import _L1C_BAND_RESOLUTIONS

                    res = _L1C_BAND_RESOLUTIONS.get(band, 10)  # default to 10m if unknown
                bands_map.setdefault(band, set()).add(res)
        return bands_map

    def _default_rgb(self, bands_map: dict) -> Tuple[str, str, str]:
        """Choose default RGB bands."""
        for combo in [("B04", "B03", "B02")]:
            if all(b in bands_map for b in combo):
                return combo
        band_names = sorted(bands_map.keys())
        return tuple(band_names[:3]) if len(band_names) >= 3 else tuple(band_names)

    def _find_band_file(self, band_name: str, img_root: str, preferred_resolution: str = "native") -> Optional[str]:
        """Find band file with preferred resolution."""
        matches = []
        # Support recursive search in case of nested resolution folders or different structures
        # Use a case-insensitive match for the band name in the filename
        for rootp, dirs, files in os.walk(img_root):
            for f in files:
                m = _BAND_RE.search(f)
                if not m:
                    # Special case for TCI in some versions where regex might not perfectly match
                    # but file contains TCI
                    if band_name.upper() == "TCI" and "TCI" in f.upper() and f.lower().endswith((".jp2", ".tif")):
                        # try to extract resolution from folder name if not in filename
                        res = None
                        if "10m" in rootp:
                            res = 10
                        elif "20m" in rootp:
                            res = 20
                        elif "60m" in rootp:
                            res = 60
                        matches.append((res, os.path.join(rootp, f)))
                    continue

                b = m.group("band").upper()
                if b != band_name.upper():
                    continue
                try:
                    r = int(m.group("res"))
                except Exception:
                    # Try to extract from folder name
                    if "10m" in rootp:
                        r = 10
                    elif "20m" in rootp:
                        r = 20
                    elif "60m" in rootp:
                        r = 60
                    else:
                        r = None
                matches.append((r, os.path.join(rootp, f)))

        if not matches:
            return None

        if preferred_resolution != "native":
            try:
                pref = int(preferred_resolution)
                # First try exact match
                for r, p in matches:
                    if r == pref:
                        return p
                # Then try any match if exact preferred not found
            except Exception:
                pass

        # Return best available (lowest resolution/native)
        valid = [m for m in matches if m[0] is not None]
        if valid:
            return min(valid, key=lambda x: x[0])[1]
        return matches[0][1] if matches else None

    async def _build_and_show_rgb(
        self,
        bands_tuple: Tuple[str, str, str],
        img_root: str,
        resolution: str | int,
        preview_display,
    ):
        """Build and display RGB composite."""
        try:
            try:
                import rasterio
                from rasterio.enums import Resampling
            except Exception:
                with preview_display:
                    ui.label("Rasterio not installed; cannot build RGB composite").classes("text-sm text-gray-600 mt-2")
                return

            # Find band files
            band_files = {}
            for band in bands_tuple:
                band_file = self._find_band_file(band, img_root, str(resolution) if isinstance(resolution, int) else "native")
                if band_file:
                    band_files[band] = band_file

            if not all(b in band_files for b in bands_tuple):
                with preview_display:
                    ui.label("Requested bands not fully available locally").classes("text-sm text-gray-600 mt-2")
                return

            srcs = {b: rasterio.open(band_files[b]) for b in bands_tuple}
            resolutions_map = {b: abs(s.transform.a) for b, s in srcs.items()}
            ref_band = min(resolutions_map, key=resolutions_map.get)
            ref = srcs[ref_band]

            out_h, out_w = compute_preview_shape(ref.height, ref.width)

            arrs = []
            for b in bands_tuple:
                s = srcs[b]
                try:
                    data = s.read(1, out_shape=(out_h, out_w), resampling=Resampling.bilinear)
                except Exception:
                    data = s.read(1)
                    data = resize_array_to_preview(data, PREVIEW_MAX_DIM)
                arrs.append(data)

            rgb = np.stack(arrs, axis=-1)
            p1 = np.percentile(rgb, 2)
            p99 = np.percentile(rgb, 98)
            rgb = (rgb - p1) / max((p99 - p1), 1e-6)
            rgb = (np.clip(rgb, 0.0, 1.0) * 255).astype("uint8")

            tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmpf.close()

            wrote = False
            try:
                from PIL import Image

                Image.fromarray(rgb).save(tmpf.name, quality=85)
                wrote = True
            except Exception:
                try:
                    import imageio

                    imageio.imwrite(tmpf.name, rgb)
                    wrote = True
                except Exception:
                    pass

            preview_display.clear()
            with preview_display:
                if wrote:
                    ui.image(source=tmpf.name).classes("w-full rounded-lg mt-2")
                else:
                    ui.label("Cannot write preview image; install Pillow or imageio (e.g. `pip install Pillow imageio`)").classes("text-sm text-gray-600 mt-2")

            for s in srcs.values():
                try:
                    s.close()
                except Exception:
                    pass

        except Exception as e:
            logger.exception("Error building RGB: %s", e)
            with preview_display:
                ui.label(f"Error building RGB preview: {e}").classes("text-sm text-red-600 mt-2")

    async def _build_and_show_single(
        self,
        band: str,
        img_root: str,
        resolution: str | int,
        preview_display,
    ):
        """Build and display single band."""
        try:
            try:
                import rasterio
            except Exception:
                with preview_display:
                    ui.label("Rasterio not installed; cannot render band").classes("text-sm text-gray-600 mt-2")
                return

            band_file = self._find_band_file(band, img_root, str(resolution) if isinstance(resolution, int) else "native")

            if not band_file:
                with preview_display:
                    ui.label("Band file not found locally").classes("text-sm text-gray-600 mt-2")
                return

            s = rasterio.open(band_file)
            out_h, out_w = compute_preview_shape(s.height, s.width)
            try:
                data = s.read(1, out_shape=(out_h, out_w), resampling=rasterio.enums.Resampling.bilinear)
            except Exception:
                data = s.read(1)
                data = resize_array_to_preview(data, PREVIEW_MAX_DIM)

            # Check if this is the SCL band
            is_scl = band.upper() == "SCL"

            if is_scl:
                # For SCL band, render using interactive SCL color palette in Plotly
                scl_data = data.astype(np.uint8)

                preview_display.clear()
                with preview_display:
                    # Create and display interactive SCL figure
                    scl_fig = create_scl_plotly_figure(scl_data)
                    if scl_fig:
                        ui.plotly(scl_fig).classes("w-full rounded-lg mt-2")
                        ui.label(f"renderer: SCL plotly (interactive)  •  shape={data.shape}").classes("text-xs text-gray-600 mt-1")
                    else:
                        ui.label("Could not render interactive SCL preview").classes("text-sm text-gray-600 mt-2")

                try:
                    s.close()
                except Exception:
                    pass
                return

            vmin = float(np.nanmin(data))
            vmax = float(np.nanmax(data))
            denom = vmax - vmin if (vmax - vmin) != 0 else 1.0
            normalized = (data - vmin) / denom

            # Try plotly for interactive heatmap
            try:
                import plotly.graph_objects as go

                fig = go.Figure(go.Heatmap(z=normalized, colorscale="Viridis"))
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), width=700, height=400)
                fig.update_yaxes(rangemode="tozero", scaleanchor="x", scaleratio=1)

                preview_display.clear()
                with preview_display:
                    ui.plotly(fig).classes("w-full rounded-lg mt-2")
                    ui.label(f"renderer: plotly (interactive)  •  min={vmin:.3f} max={vmax:.3f}  •  shape={data.shape}").classes("text-xs text-gray-600 mt-1")
                try:
                    s.close()
                except Exception:
                    pass
                return
            except Exception:
                pass

            preview_display.clear()
            with preview_display:
                ui.label("Could not render interactive preview; install plotly (`pip install plotly`)").classes("text-sm text-gray-600 mt-2")

            try:
                s.close()
            except Exception:
                pass

        except Exception as e:
            logger.exception("Error building single-band: %s", e)
            with preview_display:
                ui.label(f"Error building band preview: {e}").classes("text-sm text-red-600 mt-2")

    async def _build_and_show_all(
        self,
        bands_list: list,
        img_root: str,
        resolution: str | int,
        preview_display,
    ):
        """Build and display all bands as grid."""
        try:
            import math

            try:
                import rasterio
            except Exception:
                with preview_display:
                    ui.label("Rasterio not installed; cannot build band grid").classes("text-sm text-gray-600 mt-2")
                return

            bands_list = bands_list[:64]

            # Build thumbnails
            thumbs = []
            for band in bands_list:
                band_file = self._find_band_file(band, img_root, str(resolution) if isinstance(resolution, int) else "native")
                if not band_file:
                    thumbs.append(None)
                    continue

                s = rasterio.open(band_file)
                try:
                    p_h, p_w = compute_preview_shape(s.height, s.width)
                    data_preview = s.read(1, out_shape=(p_h, p_w), resampling=rasterio.enums.Resampling.bilinear)
                except Exception:
                    data_preview = s.read(1)
                    data_preview = resize_array_to_preview(data_preview, PREVIEW_MAX_DIM)

                try:
                    native_res = int(round(abs(s.transform.a)))
                except Exception:
                    native_res = None

                try:
                    orig_shape = (s.height, s.width)
                except Exception:
                    orig_shape = None

                try:
                    p1 = np.percentile(data_preview, 2)
                    p99 = np.percentile(data_preview, 98)
                    img = (np.clip((data_preview - p1) / max((p99 - p1), 1e-6), 0, 1) * 255).astype("uint8")
                    tile_rgb = np.stack([img, img, img], axis=-1)
                    tile_small = resize_array_to_preview(tile_rgb, max_dim=128)
                    thumbs.append({
                        "img": tile_small,
                        "res_m": native_res,
                        "shape": orig_shape,
                    })
                except Exception:
                    thumbs.append(None)

                try:
                    s.close()
                except Exception:
                    pass

            # Render grid
            pairs = [(band, thumbs[i] if i < len(thumbs) else None) for i, band in enumerate(bands_list)]

            try:
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots

                n = len(pairs)
                cols = int(math.ceil(math.sqrt(n)))
                rows = int(math.ceil(n / cols))

                titles = []
                for name, t in pairs:
                    if t is None:
                        titles.append(name)
                        continue
                    shape = t.get("shape") if isinstance(t, dict) else None
                    resm = t.get("res_m") if isinstance(t, dict) else None
                    shape_str = f"{shape[1]}x{shape[0]} px" if shape else "- px"
                    res_str = f"{resm}m" if resm else "-m"
                    titles.append(f"{name}\n{shape_str}\n{res_str}")

                col_w = [1.0 / cols] * cols
                row_h = [1.0 / rows] * rows
                fig = make_subplots(
                    rows=rows,
                    cols=cols,
                    subplot_titles=titles,
                    column_widths=col_w,
                    row_heights=row_h,
                    horizontal_spacing=0.01,
                    vertical_spacing=0.02,
                    shared_xaxes=True,
                    shared_yaxes=True,
                )

                for idx, (_name, t) in enumerate(pairs):
                    r = idx // cols + 1
                    c = idx % cols + 1
                    if t is None:
                        tile = np.zeros((128, 128, 3), dtype="uint8") + 80
                    else:
                        t_img = t.get("img") if isinstance(t, dict) else t
                        if getattr(t_img, "dtype", None) != np.uint8:
                            t_img = (np.clip(t_img, 0, 1) * 255).astype("uint8") if t_img.max() <= 1 else t_img.astype("uint8")
                        tile = t_img

                    trace = go.Image(z=tile)
                    fig.add_trace(trace, row=r, col=c)

                fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
                fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)

                tile_px = 280
                width = min(3000, cols * tile_px)
                height = min(3000, rows * tile_px)
                fig.update_layout(margin=dict(l=6, r=6, t=30, b=6), width=width, height=height, showlegend=False)

                preview_display.clear()
                with preview_display:
                    ui.plotly(fig).classes("w-full rounded-lg mt-2")

            except Exception as e:
                logger.exception("Error rendering band grid: %s", e)
                with preview_display:
                    ui.label(f"Could not render band grid interactively: {e}").classes("text-sm text-gray-600 mt-2")

        except Exception as e:
            logger.exception("Error building all-bands grid: %s", e)
            with preview_display:
                ui.label(f"Error building band grid: {e}").classes("text-sm text-red-600 mt-2")
