"""Tile click, product choice, TCI streaming and date compare for the Map Search tab."""

import asyncio
import time
from typing import Optional

from loguru import logger
from nicegui import ui

from vresto.api import ProductInfo
from vresto.api.auth import get_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.services import public_s2
from vresto.services.sentinel_stream import (
    sentinel_stream_service,
)
from vresto.services.tiles import tile_pool
from vresto.ui.widgets.credentials_menu import STREAM_CREDENTIALS_HINT

# Maximum number of latest products surfaced in the tile-click chooser dialog.
TILE_PRODUCT_CHOICES = 5


def _scene_keys(product: ProductInfo) -> tuple[str, str]:
    """Return a product's sensing day (``YYYYMMDD``, the TCI cache key) and its timestamp (``YYYYMMDDhhmmss``)."""
    digits = "".join(ch for ch in str(product.sensing_date) if ch.isdigit())
    return digits[:8], digits[:14] or digits[:8]


def _pretty_day(day: str) -> str:
    """``20250612`` -> ``2025-06-12``."""
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day


class TileStreamingMixin:
    """Streams a Sentinel-2 tile picked on the grid, mixed into MapSearchTab."""

    async def _handle_tile_click(self, tile_code: str):
        """Handle click on an MGRS grid tile.

        Looks up the top-N latest matching products for the tile and date range,
        then either streams directly (single match) or opens a chooser dialog so
        the user picks which product to stream (multiple matches).
        """
        previous_tile_code = self._streaming_tile_code
        # Credentials may have been added in Settings since the controls were built.
        self._sync_stream_source_note()
        if hasattr(self, "_tile_status_label"):
            self._tile_status_label.set_text(f"Loading {tile_code}...")
        self._add_message(f"🛰️ Clicked tile: {tile_code}")
        ui.notify(f"Loading tile {tile_code}...", position="top", type="info", spinner=True)

        # Visual feedback on the map: yellow-highlight the clicked tile while
        # candidates are fetched / the chooser is open.
        if self.map_widget_obj:
            self.map_widget_obj.highlight_tile(tile_code)

        try:
            products = await self._get_tile_candidates(tile_code, limit=TILE_PRODUCT_CHOICES)
        except Exception as e:  # pragma: no cover - defensive, catalog errors already logged
            logger.warning(f"Tile candidate lookup failed for {tile_code}: {e}")
            products = []

        if not products:
            self._add_message(f"⚠️ No S2 L2A product found for tile {tile_code} in selected date range.")
            ui.notify("No product found for this tile in the date range.", position="top", type="warning")
            if hasattr(self, "_tile_status_label"):
                tile_label = self._streaming_tile_code or "No tile selected"
                if self._streaming_tile_code:
                    tile_label = f"Selected tile: {self._streaming_tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                self._tile_status_label.set_text(tile_label)
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()
            return

        if len(products) == 1:
            await self._commit_tile_selection(tile_code, products[0], previous_tile_code)
            return

        self._add_message(f"📄 {len(products)} candidates for {tile_code} — pick one to stream.")
        if hasattr(self, "_tile_status_label"):
            self._tile_status_label.set_text(f"Select product for {tile_code}...")
        self._show_tile_product_chooser(tile_code, products, previous_tile_code)

    async def _commit_tile_selection(
        self,
        tile_code: str,
        product: ProductInfo,
        previous_tile_code: Optional[str],
    ):
        """Commit a chosen product: wire overlay state, stream TCI, refresh overlays."""
        self._streaming_tile_code = tile_code
        self._stop_compare()
        if hasattr(self, "_overlay_status_label"):
            self._overlay_status_label.set_text("Pick an overlay. It follows tile changes automatically once streamed.")
        if self.map_widget_obj:
            self.map_widget_obj.highlight_tile(tile_code)
        self._set_overlay_controls_enabled(True)

        # Phase 1: hide the how-to card on first successful tile commit.
        if self._how_to_card is not None:
            self._how_to_card.set_visibility(False)

        try:
            if previous_tile_code and previous_tile_code != tile_code:
                self._remove_overlay_layers(previous_tile_code)

            streamed = await self._stream_product(tile_code, product)
            if streamed:
                if self.map_widget_obj:
                    self.map_widget_obj.set_streamed_tile(tile_code)
                if hasattr(self, "_tile_status_label"):
                    tile_label = f"Selected tile: {tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                    self._tile_status_label.set_text(tile_label)
                self._push_url(tile=tile_code, date=self._streaming_date)
                self._set_control_enabled(self._compare_button, True)
                self._update_tcd_overlay_availability()
                await self._refresh_lst_time_options()
                await self._reload_enabled_overlays()
                self._activate_pending_overlay()
        finally:
            # Always reset the highlight, even if streaming raised.
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()

    def _show_tile_product_chooser(
        self,
        tile_code: str,
        products: list,
        previous_tile_code: Optional[str],
    ) -> None:
        """Open a modal dialog letting the user pick which product to stream.

        Each card shows sensing date, cloud cover, and size with a single
        "Stream this tile" button. Cancel (or dismissal) clears the tile
        highlight and restores the previous status label.
        """
        selected = {"committed": False}

        def _reset_after_dismiss() -> None:
            if selected["committed"]:
                return
            if self.map_widget_obj:
                self.map_widget_obj.clear_tile_highlight()
            if hasattr(self, "_tile_status_label"):
                if self._streaming_tile_code:
                    tile_label = f"Selected tile: {self._streaming_tile_code}"
                    if self._streaming_date:
                        tile_label += f" ({self._streaming_date})"
                    self._tile_status_label.set_text(tile_label)
                else:
                    self._tile_status_label.set_text("No tile selected")

        with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-full"):
            ui.label(f"Select a product for tile {tile_code}").classes("text-base font-semibold")
            ui.label(f"Latest {len(products)} matches in the selected date range (cloud cover ≤ 50%).").classes("text-xs text-gray-500 mb-2")
            if any(p.source == public_s2.PUBLIC_SOURCE for p in products):
                ui.label(public_s2.PUBLIC_ATTRIBUTION).classes("text-xs text-gray-500 mb-2")

            with ui.column().classes("w-full gap-2"):
                for idx, product in enumerate(products, 1):
                    # AWS Open Data products show the thumbnail the catalogue offers, not a CDSE quicklook.
                    is_public = product.source == public_s2.PUBLIC_SOURCE
                    with ui.card().classes("w-full p-2 bg-gray-50 dark:bg-slate-800 shadow-sm rounded-md"):
                        display = getattr(product, "display_name", product.name)
                        ui.label(f"{idx}. {display}").classes("text-xs font-mono break-all")
                        if is_public and product.thumbnail_url:
                            ui.image(product.thumbnail_url).props("fit=contain").classes("w-full h-40 rounded")
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600 dark:text-gray-300")
                            if product.cloud_cover is not None:
                                ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600 dark:text-gray-300")
                            if not is_public:
                                ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600 dark:text-gray-300")
                            ui.space()

                            if not is_public:

                                def _make_quicklook_handler(p=product):
                                    async def _on_quicklook():
                                        # Preview the candidate without dismissing the chooser, so
                                        # the user can compare multiple scenes before committing.
                                        result = self.on_quicklook(p, self.messages_column)
                                        if asyncio.iscoroutine(result):
                                            await result

                                    return _on_quicklook

                                ui.button("Quicklook", on_click=_make_quicklook_handler()).props("flat size=sm").classes("text-xs")

                            def _make_stream_handler(p=product):
                                async def _on_stream():
                                    selected["committed"] = True
                                    dialog.close()
                                    await self._commit_tile_selection(tile_code, p, previous_tile_code)

                                return _on_stream

                            ui.button("Stream this tile", on_click=_make_stream_handler()).props("size=sm color=primary").classes("text-xs")

            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat size=sm")

        dialog.on("hide", lambda _e: _reset_after_dismiss())
        dialog.open()

    async def _stream_product(self, tile_code: str, product: ProductInfo) -> bool:
        """Stream TCI for the given (tile, product) and show it on the map: cache hit first, then full-res."""
        t_e2e = time.perf_counter()
        date, timestamp = _scene_keys(product)
        self._streaming_date = date
        self._streaming_timestamp = timestamp
        cog_path = await self._fetch_tci(tile_code, product)
        if not cog_path:
            return False
        await self._display_tci_layer(cog_path, tile_code)
        logger.info(f"[perf] _stream_product '{tile_code}' end-to-end {(time.perf_counter() - t_e2e) * 1000:.0f} ms")
        return True

    async def _fetch_tci(self, tile_code: str, product: ProductInfo) -> Optional[str]:
        """Return the path of the product's cached TCI for ``tile_code``, streaming it first if needed.

        Returns None (after saying why in the activity log) when it cannot be streamed.
        """
        date, _ = _scene_keys(product)
        resolution = self._tci_resolution
        is_public = product.source == public_s2.PUBLIC_SOURCE

        self._add_message(f"📡 Streaming TCI ({resolution}) for {tile_code} ({product.sensing_date})...")

        cached = sentinel_stream_service.get_cached_tci_path(tile_code, date, resolution)
        if cached:
            self._add_message(f"⚡ Cache hit ({resolution}) for {tile_code}")
            if is_public:
                self._add_message(f"ℹ️ {public_s2.PUBLIC_ATTRIBUTION}")
            return cached

        if is_public:
            t_stream = time.perf_counter()
            result = await asyncio.to_thread(sentinel_stream_service.stream_public_tci, product, tile_code, date, resolution)
            logger.info(f"[perf] _fetch_tci '{tile_code}': stream_public_tci {(time.perf_counter() - t_stream) * 1000:.0f} ms")
            if not result:
                self._add_message(f"❌ Failed to stream TCI for {tile_code} from AWS Open Data")
                ui.notify("TCI streaming failed", position="top", type="negative")
                return None
            self._add_message(f"✅ TCI ready for {tile_code}")
            self._add_message(f"ℹ️ {public_s2.PUBLIC_ATTRIBUTION}")
            return result

        # CDSE streaming reads S3 with static keys only, so it cannot run without them.
        if not CopernicusConfig().has_static_s3_credentials():
            self._add_message(f"⚠️ {STREAM_CREDENTIALS_HINT}")
            ui.notify(STREAM_CREDENTIALS_HINT, position="top", type="warning")
            return None

        # Need to find exact TCI path on S3
        if not product.s3_path:
            self._add_message(f"❌ No S3 path for product {product.name}")
            return None

        t_tci = time.perf_counter()
        tci_path = await asyncio.to_thread(
            sentinel_stream_service.find_tci_path_in_product,
            product.s3_path,
            tile_code,
            resolution,
        )
        logger.info(f"[perf] _fetch_tci '{tile_code}': find_tci_path_in_product {(time.perf_counter() - t_tci) * 1000:.0f} ms")
        if not tci_path:
            self._add_message(f"❌ Could not locate TCI band in {product.name}")
            return None

        # Stream and cache — pass the exact TCI path to avoid wildcard issues
        t_stream = time.perf_counter()
        result = await asyncio.to_thread(
            sentinel_stream_service.stream_tci,
            product.s3_path,
            tile_code,
            date,
            tci_path,
            resolution,
        )
        logger.info(f"[perf] _fetch_tci '{tile_code}': stream_tci (S3 + JP2 decode + write) {(time.perf_counter() - t_stream) * 1000:.0f} ms")
        if result:
            self._add_message(f"✅ TCI ready for {tile_code}")
            return result

        self._add_message(f"❌ Failed to stream TCI for {tile_code}")
        ui.notify("TCI streaming failed", position="top", type="negative")
        return None

    async def _display_tci_layer(self, cog_path: str, tile_code: str):
        """Display a cached TCI COG as a tile layer on the map."""
        layer_name = f"tci_{tile_code}"

        if self.map_widget_obj:
            # Remove any previous TCI layer before adding the new one
            for existing in list(self.map_widget_obj._tile_layers.keys()):
                if existing.startswith("tci_"):
                    tile_pool.remove(self._pool_name(existing))
                    self.map_widget_obj.remove_tile_layer(existing)

        # First-time TileClient bootstrap can take ~1–2 s; run off-loop.
        t_pool = time.perf_counter()
        url = await asyncio.to_thread(tile_pool.get_or_create, self._pool_name(layer_name), cog_path)
        logger.info(f"[perf] _display_tci_layer '{tile_code}': tile_pool.get_or_create {(time.perf_counter() - t_pool) * 1000:.0f} ms")
        if url and self.map_widget_obj:
            self.map_widget_obj.add_tile_layer(url, name=layer_name)

            # Zoom the map to the raster bounds
            bounds = tile_pool.get_bounds(self._pool_name(layer_name))
            if bounds and self._fit_streamed_tile:
                min_lat, min_lon, max_lat, max_lon = bounds
                self.map_widget_obj.fit_bounds(bounds)
            ui.notify(f"✅ Tile {tile_code} rendered", position="top", type="positive")

    def _find_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Return cached search-results products whose names match the MGRS tile.

        Newest sensing date first; capped at `limit`. Only returns products that
        explicitly contain the tile code in their name — the previous loose
        "first S2 product" fallback is intentionally dropped so the chooser
        always shows tile-specific candidates.
        """
        search_code = tile_code if tile_code.startswith("T") else f"T{tile_code}"
        matches = [p for p in self.current_state.get("products", []) if search_code in p.name]
        matches.sort(key=lambda p: p.sensing_date, reverse=True)
        return matches[:limit]

    async def _search_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Query the CDSE catalog for the latest N S2 L2A products matching the tile and date range."""
        date_range = self.current_state.get("date_range", {})
        start_date = date_range.get("from", "2020-01-01")
        end_date = date_range.get("to", start_date)

        search_code = tile_code if tile_code.startswith("T") else f"T{tile_code}"

        def _query():
            import requests

            config = CopernicusConfig()
            # Reuse the process-wide CopernicusAuth so a single bearer token
            # (and its refresh token) is shared across every tile-hover and
            # streaming call. Constructing a fresh CopernicusAuth here would
            # open a brand-new Keycloak session per click and quickly trip
            # CDSE's concurrent-session cap.
            auth = get_shared_auth(config=config)
            # Catalogue search is anonymous on CDSE; a bearer token is only added when credentials are set.
            headers = auth.get_search_headers()

            # Build targeted OData filter: tile code + L2A + date range
            filters = [
                "Collection/Name eq 'SENTINEL-2'",
                f"contains(Name, '{search_code}')",
                "contains(Name, 'MSIL2A')",
                f"ContentDate/Start ge {start_date}T00:00:00.000Z",
                f"ContentDate/Start le {end_date}T23:59:59.999Z",
                "Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le 50.0)",
            ]
            filter_string = " and ".join(filters)
            url = f"{config.ODATA_BASE_URL}/Products"
            params = {
                "$filter": filter_string,
                "$top": limit,
                "$orderby": "ContentDate/Start desc",
                "$expand": "Attributes",
            }
            resp = requests.get(url, params=params, headers=headers, timeout=60)
            if resp.status_code != 200:
                return []

            from datetime import datetime as _dt

            products: list = []
            for item in resp.json().get("value", []):
                s3_path = item.get("S3Path", "")
                if not s3_path:
                    continue
                cloud_cover = None
                for attr in item.get("Attributes", []):
                    if attr.get("Name") == "cloudCover":
                        cloud_cover = attr.get("Value")
                        break
                sensing = item.get("ContentDate", {}).get("Start", "")
                if sensing:
                    try:
                        sensing = _dt.fromisoformat(sensing.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                size_mb = item.get("ContentLength", 0) / (1024 * 1024)
                products.append(
                    ProductInfo(
                        id=item.get("Id", ""),
                        name=item.get("Name", ""),
                        collection="SENTINEL-2",
                        sensing_date=sensing,
                        size_mb=size_mb,
                        s3_path=s3_path,
                        cloud_cover=cloud_cover,
                    )
                )
            return products

        try:
            products = await asyncio.to_thread(_query)
            if products:
                logger.info(f"Catalog returned {len(products)} candidate(s) for tile {tile_code}")
            return products
        except Exception as e:
            logger.warning(f"Catalog search for tile {tile_code} failed: {e}")
            return []

    async def _search_public_products_for_tile(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Query AWS Open Data for the newest scenes of a tile in the selected date range. Needs no account."""
        date_range = self.current_state.get("date_range", {})
        start_date = date_range.get("from", "2020-01-01")
        end_date = date_range.get("to", start_date)
        try:
            products = await asyncio.to_thread(public_s2.search_tile, tile_code, start_date, end_date, limit=limit)
        except Exception as e:  # pragma: no cover - search_tile already logs its own failures
            logger.warning(f"AWS Open Data search for tile {tile_code} failed: {e}")
            return []
        if products:
            logger.info(f"AWS Open Data returned {len(products)} candidate(s) for tile {tile_code}")
        return products

    async def _get_tile_candidates(self, tile_code: str, limit: int = TILE_PRODUCT_CHOICES) -> list:
        """Return up to `limit` latest products for a tile.

        Without static S3 keys the candidates come from AWS Open Data, because CDSE search
        results cannot be streamed without them. Otherwise, prefer cached matches from
        `current_state["products"]` (instant), and fall back to a CDSE catalog query.
        """
        if public_s2.stream_source() == "public":
            self._add_message(f"🔍 Searching AWS Open Data for tile {tile_code}...")
            t_search = time.perf_counter()
            products = await self._search_public_products_for_tile(tile_code, limit=limit)
            logger.info(f"[perf] _get_tile_candidates '{tile_code}': AWS Open Data search {(time.perf_counter() - t_search) * 1000:.0f} ms")
            return products

        cached = self._find_products_for_tile(tile_code, limit=limit)
        if cached:
            return cached

        self._add_message(f"🔍 Searching catalog for tile {tile_code}...")
        t_search = time.perf_counter()
        products = await self._search_products_for_tile(tile_code, limit=limit)
        logger.info(f"[perf] _get_tile_candidates '{tile_code}': catalog search {(time.perf_counter() - t_search) * 1000:.0f} ms")
        return products

    async def _open_compare_chooser(self) -> None:
        """Offer the other scenes of the streamed tile in the date range, to compare with the one on the map."""
        tile_code = self._streaming_tile_code
        if not tile_code:
            return
        try:
            products = await self._get_tile_candidates(tile_code, limit=TILE_PRODUCT_CHOICES + 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Compare: scene lookup failed for {tile_code}: {exc}")
            products = []
        others = [p for p in products if _scene_keys(p)[0] != self._streaming_date][:TILE_PRODUCT_CHOICES]
        if not others:
            ui.notify("No other date of this tile in the date range. Widen the dates to compare.", position="top", type="warning")
            return

        with ui.dialog() as dialog, ui.card().classes("w-[30rem] max-w-full"):
            ui.label(f"Compare {tile_code} ({_pretty_day(self._streaming_date or '')}) with").classes("text-base font-semibold")
            ui.label("The date you pick shows left of a divider you can drag across the map.").classes("text-xs text-gray-500 mb-2")
            with ui.column().classes("w-full gap-2"):
                for product in others:
                    with ui.row().classes("w-full items-center gap-3 p-2 rounded-md bg-gray-50 dark:bg-slate-800"):
                        if product.source == public_s2.PUBLIC_SOURCE and product.thumbnail_url:
                            ui.image(product.thumbnail_url).props("fit=cover").classes("w-16 h-16 rounded")
                        with ui.column().classes("gap-0 grow"):
                            ui.label(f"📅 {str(product.sensing_date)[:10]}").classes("text-sm")
                            if product.cloud_cover is not None:
                                ui.label(f"☁️ {product.cloud_cover:.0f}% cloud").classes("text-xs text-gray-500")

                        async def _pick(p=product) -> None:
                            dialog.close()
                            await self._start_compare(p)

                        ui.button("Compare", on_click=_pick).props("size=sm color=deep-orange unelevated")
            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat size=sm")
        dialog.open()

    async def _start_compare(self, product: ProductInfo) -> None:
        """Stream ``product`` for the streamed tile and show it left of a swipe divider."""
        tile_code = self._streaming_tile_code
        if not tile_code or not self.map_widget_obj:
            return
        self._stop_compare()
        ui.notify(f"Loading {str(product.sensing_date)[:10]} to compare…", position="top", type="info", spinner=True)
        cog_path = await self._fetch_tci(tile_code, product)
        if not cog_path or tile_code != self._streaming_tile_code:
            return  # failed (already reported), or the user moved to another tile meanwhile
        layer_name = f"compare_{tile_code}"
        url = await asyncio.to_thread(tile_pool.get_or_create, self._pool_name(layer_name), cog_path)
        if not url or tile_code != self._streaming_tile_code:
            return
        compare_day = _scene_keys(product)[0]
        self.map_widget_obj.add_tile_layer(url, name=layer_name)
        self.map_widget_obj.enable_swipe(layer_name, _pretty_day(compare_day), _pretty_day(self._streaming_date or ""))
        self._compare_layer, self._compare_date = layer_name, compare_day
        if self._compare_stop_button is not None:
            self._compare_stop_button.set_visibility(True)
        self._add_message(f"↔️ Comparing {tile_code}: {_pretty_day(compare_day)} (left) with {_pretty_day(self._streaming_date or '')} (right)")

    def _stop_compare(self) -> None:
        """Remove the second date and its divider."""
        if self._compare_stop_button is not None:
            self._compare_stop_button.set_visibility(False)
        if not self._compare_layer:
            return
        layer_name, self._compare_layer, self._compare_date = self._compare_layer, None, None
        tile_pool.remove(self._pool_name(layer_name))
        if self.map_widget_obj:
            self.map_widget_obj.disable_swipe()
            self.map_widget_obj.remove_tile_layer(layer_name)
