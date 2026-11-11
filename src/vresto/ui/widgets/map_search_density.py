"""MGRS grid and per-tile product counts for the Map Search tab."""

import asyncio
from typing import Optional

from loguru import logger
from nicegui import background_tasks, ui

from vresto.services.mgrs_grid import MIN_ZOOM_FOR_GRID, compute_visible_tiles_geojson, compute_visible_zones_geojson
from vresto.services.tile_density import (
    INDEX_SOURCE,
    RATE_LIMIT_COOLDOWN_S,
    STATS_SOURCE,
    TileDensity,
    count_products_per_tile,
    count_products_per_zone,
    count_whole_months,
    rate_limited,
)
from vresto.ui.widgets.map_grid import GRID_DENSITY_STYLE, GRID_EMPTY_STYLE, grid_density_legend

# Seconds to wait after the last map move or date change before counting products per tile.
DENSITY_DEBOUNCE_S = 0.5


# After the whole-month colours, wait this much longer for the map to settle before the exact
# counts, which take seconds, so panning across a region does not queue one exact count per stop.
EXACT_COUNT_DELAY_S = 1.0


# Notes shown under the product-count legend.
DENSITY_PARTIAL_TEXT = "Partial count: zoom in or narrow the dates for exact numbers."


DENSITY_RATE_LIMITED_TEXT = "The catalogue is busy (rate limit); tile colours will return in about a minute."


def _density_caption_text(density: TileDensity) -> str:
    """Say where the tile counts came from and which period they cover."""
    if density.source == INDEX_SOURCE:
        return "Selected dates, Earth Search index on AWS Open Data."
    if density.source == STATS_SOURCE:
        return f"Whole months ({density.period}), Earth Search index on AWS Open Data."
    return "Selected dates, CDSE catalogue."


class GridDensityMixin:
    """Draws the MGRS grid and colours tiles by product count, mixed into MapSearchTab."""

    def _create_density_legend(self) -> None:
        """Build the legend for the product-count colours. It is shown only while grid tiles are coloured."""
        with ui.column().classes("w-full gap-0.5") as legend:
            with ui.row().classes("w-full items-center gap-x-2 gap-y-1 flex-wrap"):
                ui.label("Products per tile per month").classes("text-[11px] text-gray-500 dark:text-gray-400")
                self._legend_swatch("0", GRID_EMPTY_STYLE["fillColor"], GRID_EMPTY_STYLE["color"], dashed=True)
                for label, colour in grid_density_legend():
                    self._legend_swatch(label, colour, GRID_DENSITY_STYLE["color"])
            self._density_caption = ui.label("").classes("text-[11px] text-gray-500 dark:text-gray-400")
            self._density_note = ui.label(DENSITY_PARTIAL_TEXT).classes("text-[11px] text-amber-600 dark:text-amber-400 italic")
        self._density_legend = legend
        self._sync_density_legend()

    @staticmethod
    def _legend_swatch(label: str, fill: str, border: str, dashed: bool = False) -> None:
        """Add one colour swatch with its label to the density legend."""
        with ui.row().classes("items-center gap-1 no-wrap"):
            border_style = "dashed" if dashed else "solid"
            ui.element("div").classes("w-3 h-3 rounded-sm").style(f"background:{fill};border:1px {border_style} {border}")
            ui.label(label).classes("text-[11px] text-gray-600 dark:text-gray-300")

    def _toggle_grid(self, e):
        """Toggle the MGRS grid overlay on/off."""
        self._grid_enabled = e.value
        if self._grid_enabled:
            # Trigger initial grid render by emitting a fake moveend
            if self.map_widget_obj and self.map_widget_obj._map:
                map_id = self.map_widget_obj._map.id
                js = f"""
                (function() {{
                    const el = getElement({map_id});
                    if (el && el.map) el.map.fire('moveend');
                }})();
                """
                ui.run_javascript(js)
        else:
            self._hide_grid()

    def _toggle_hires(self, e) -> None:
        """Switch between 60 m (fast preview) and 10 m (full detail) TCI."""
        self._tci_resolution = "10m" if e.value else "60m"
        logger.info(f"TCI resolution set to {self._tci_resolution}")

    def _handle_moveend(self, bbox: tuple, zoom: int):
        """Handle map moveend: redraw the MGRS grid if enabled, then recount its products."""
        if not self._grid_enabled:
            return

        # Below the tile zoom, show the ~1,200 grid zones (6 by 8 degrees) so the whole world is visible.
        zones = zoom < MIN_ZOOM_FOR_GRID
        geojson = compute_visible_zones_geojson(bbox) if zones else compute_visible_tiles_geojson(bbox, zoom)
        self._grid_zones = zones
        if geojson and self.map_widget_obj:
            self.map_widget_obj.set_grid_layer(geojson)
            self._grid_bbox = bbox
            self._grid_codes = [f["properties"]["mgrs_code"] for f in geojson.get("features", []) if f.get("properties", {}).get("mgrs_code")]
            self._forget_density()  # the new grid is plain until its counts arrive
            self._schedule_density_refresh()
        elif self.map_widget_obj:
            self._hide_grid()

    def _hide_grid(self) -> None:
        """Remove the MGRS grid, along with any product counts still on their way."""
        self._grid_bbox = None
        self._forget_density()
        if self.map_widget_obj:
            self.map_widget_obj.clear_grid_layer()

    def _toggle_density(self, e) -> None:
        """Switch the product-count colouring of the grid tiles on or off."""
        self._density_enabled = bool(e.value)
        if self._density_enabled:
            self._sync_density_legend()
            self._schedule_density_refresh()
        else:
            self._forget_density()
            if self.map_widget_obj:
                self.map_widget_obj.clear_grid_density()

    def _schedule_density_refresh(self) -> None:
        """Count products per visible tile in the background, then colour the grid with the result.

        Each request supersedes the one before it, so a burst of map moves or date changes costs one search.
        """
        if not (self._density_enabled and self._grid_bbox is not None and self.map_widget is not None and self.map_widget_obj):
            return
        self._density_generation += 1
        background_tasks.create(
            self._refresh_density(self._density_generation, self._grid_bbox, self.map_widget.client),
            name="mgrs-density",
        )

    async def _refresh_density(self, generation: int, bbox: tuple, client) -> None:
        """Count products for ``bbox`` after the debounce, and colour the grid if this request is still current.

        A request is stale once a newer one has been made (by a map move, a date change, or the switch
        being turned off). Stale requests return without touching the map, both while they wait and
        when their search finishes. Failures leave the plain grid in place.
        """
        await asyncio.sleep(DENSITY_DEBOUNCE_S)
        if generation != self._density_generation:
            return

        date_range = self.current_state["date_range"]
        quick: Optional[TileDensity] = None
        if not self._grid_zones:
            # Whole-month counts come from small cached files and arrive almost at once: show them
            # while the exact counts for the selected dates, which take seconds, are read.
            try:
                quick = await asyncio.to_thread(count_whole_months, date_range["from"], date_range["to"], list(self._grid_codes))
            except Exception as exc:
                logger.debug(f"Whole-month tile counts failed: {exc}")
            if generation != self._density_generation:
                return
            if quick is not None:
                try:
                    with client:
                        self._apply_density(quick, refining=True)
                except Exception as exc:
                    logger.debug(f"Could not colour the grid by product count: {exc}")
                await asyncio.sleep(EXACT_COUNT_DELAY_S)
                if generation != self._density_generation:
                    return
        try:
            if self._grid_zones:
                density = await asyncio.to_thread(count_products_per_zone, date_range["from"], date_range["to"], list(self._grid_codes))
            else:
                density = await asyncio.to_thread(count_products_per_tile, bbox, date_range["from"], date_range["to"], tile_codes=list(self._grid_codes))
        except Exception as exc:
            logger.debug(f"Tile density lookup failed: {exc}")
            return
        if generation != self._density_generation:
            return
        if density is None and quick is not None:
            density = quick  # exact counts unavailable: keep the whole-month colours, without the "refining" note
        if density is None:
            logger.debug("Tile density unavailable; the grid stays plain")
            if rate_limited():
                try:
                    with client:
                        self._density_rate_limited = True
                        self._sync_density_legend()
                except Exception as exc:
                    logger.debug(f"Could not show the rate-limit note: {exc}")
                    return
                # Try once more when the pause is over, unless the view or dates changed meanwhile.
                await asyncio.sleep(RATE_LIMIT_COOLDOWN_S + 1)
                if generation == self._density_generation:
                    with client:
                        self._schedule_density_refresh()
            return

        try:
            with client:
                self._apply_density(density)
        except Exception as exc:
            logger.debug(f"Could not colour the grid by product count: {exc}")

    def _apply_density(self, density: TileDensity, refining: bool = False) -> None:
        """Colour the grid tiles with a density result, and show the partial-count note when needed."""
        self._density_truncated = density.truncated
        self._density_rate_limited = False
        if self._density_caption is not None:
            caption = _density_caption_text(density)
            if refining:
                caption += " Refining to the selected dates…"
            self._density_caption.set_text(caption)
        months = density.months if density.months > 0 else 1.0
        payload = {}
        for code, count in density.counts.items():
            tiles = density.tiles.get(code)  # grid zones: average over the zone's tiles with products
            rate = count / months / (tiles or 1)
            payload[code] = {"count": count, "min_cloud": density.min_cloud.get(code), "rate": round(rate, 2)}
            if tiles:
                payload[code]["tiles"] = tiles
        if self.map_widget_obj:
            self.map_widget_obj.update_grid_density(payload, truncated=density.truncated)
        self._sync_density_legend()

    def _forget_density(self) -> None:
        """Drop the current counts: a request still in flight is ignored and the partial-count note hides."""
        self._density_generation += 1
        self._density_truncated = False
        self._density_rate_limited = False
        self._sync_density_legend()

    def _sync_density_legend(self) -> None:
        """Show the legend, and its partial-count note, only while grid tiles are shown and coloured."""
        shown = self._density_enabled and self._grid_bbox is not None
        if self._density_legend is not None:
            self._density_legend.set_visibility(shown)
        if self._density_note is not None:
            if self._density_rate_limited:
                self._density_note.set_text(DENSITY_RATE_LIMITED_TEXT)
            elif self._density_truncated:
                self._density_note.set_text(DENSITY_PARTIAL_TEXT)
            self._density_note.set_visibility(shown and (self._density_truncated or self._density_rate_limited))
