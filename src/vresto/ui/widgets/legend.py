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
