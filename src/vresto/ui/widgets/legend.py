"""Legend rendering helpers for map overlays."""

from typing import List, Tuple


def build_legend_html(title: str, class_legends: List[Tuple[int, int, int, int, str]], title_color: str = "#333") -> str:
    """Generate HTML for an overlay legend.

    Args:
        title: Legend title shown at the top of the card.
        class_legends: List of tuples (class_id, R, G, B, label).
        title_color: Color for the title text.

    Returns:
        Styled HTML string for a compact legend card.
    """
    items_html = ""
    for _, red, green, blue, label in class_legends:
        color_hex = f"#{red:02x}{green:02x}{blue:02x}"
        items_html += f"""
            <div style="display: flex; align-items: center; margin-bottom: 4px;">
                <div style="
                    width: 20px;
                    height: 14px;
                    background-color: {color_hex};
                    border: 1px solid #999;
                    border-radius: 2px;
                    margin-right: 8px;
                    flex-shrink: 0;
                "></div>
                <span style="font-size: 11px; color: #555; line-height: 1.2;">{label}</span>
            </div>
        """

    return f"""
        <div style="
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 10px 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
            max-height: 320px;
            overflow-y: auto;
            min-width: 220px;
        ">
            <div style="
                font-size: 12px;
                font-weight: 600;
                color: {title_color};
                margin-bottom: 8px;
                padding-bottom: 6px;
                border-bottom: 1px solid #eee;
            ">{title}</div>
            <div style="display: flex; flex-direction: column;">
                {items_html}
            </div>
        </div>
    """


def build_continuous_legend_html(
    title: str,
    vmin: float,
    vmax: float,
    stops: List[str],
    units: str = "",
    title_color: str = "#333",
    n_ticks: int = 5,
) -> str:
    """Generate HTML for a continuous (gradient ramp) legend.

    Args:
        title: Legend title shown at the top of the card.
        vmin: Minimum data value.
        vmax: Maximum data value.
        stops: List of CSS colour strings defining the gradient (left = low, right = high).
        units: Physical unit label shown below the gradient bar.
        title_color: CSS colour for the title text.
        n_ticks: Number of tick labels to render under the gradient.

    Returns:
        Styled HTML string with a horizontal colour-ramp legend.
    """
    gradient = ", ".join(stops)
    # Evenly spaced tick labels
    ticks_html = ""
    for i in range(n_ticks):
        frac = i / (n_ticks - 1) if n_ticks > 1 else 0.0
        value = vmin + frac * (vmax - vmin)
        label = f"{value:.0f}" if (vmax - vmin) >= 10 else f"{value:.2f}"
        align = "left" if i == 0 else ("right" if i == n_ticks - 1 else "center")
        ticks_html += f'<span style="flex: 1; text-align: {align}; font-size: 10px; color: #666;">{label}</span>'

    units_html = f'<div style="font-size: 10px; color: #888; margin-top: 2px; text-align: center;">{units}</div>' if units else ""

    return f"""
        <div style="
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 10px 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
            min-width: 220px;
        ">
            <div style="
                font-size: 12px;
                font-weight: 600;
                color: {title_color};
                margin-bottom: 8px;
                padding-bottom: 6px;
                border-bottom: 1px solid #eee;
            ">{title}</div>
            <div style="
                height: 14px;
                border-radius: 3px;
                background: linear-gradient(to right, {gradient});
                border: 1px solid #ccc;
                margin-bottom: 3px;
            "></div>
            <div style="display: flex;">
                {ticks_html}
            </div>
            {units_html}
        </div>
    """
