"""Generate a static benchmark chart image for the README.

Produces ``docs/bench_chart.png`` — a grouped bar chart comparing
toomar vs stdlib on throughput (logs/sec), which is the metric that
actually matters for a logger.

Run:  python3 make_chart.py
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# data (median of 7 runs, 500k iterations each — see bench_stable.py)
LABELS = ["toomar (async)", "stdlib (sync)"]
THROUGHPUT = [553_902, 107_721]  # logs / sec (median)
COLORS = [(34, 197, 94), (234, 179, 8)]  # green, yellow

W, H = 720, 420
MARGIN_LEFT, MARGIN_RIGHT = 180, 120
MARGIN_TOP, MARGIN_BOTTOM = 80, 100

BAR_H = 70
GAP = 60


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGB", (W, H), (24, 24, 27))  # dark background
    draw = ImageDraw.Draw(img)

    font_title = _load_font(24)
    font_label = _load_font(18)
    font_value = _load_font(20)
    font_axis = _load_font(14)

    # title
    draw.text((W // 2, 20), "toomar vs stdlib logging",
              fill=(255, 255, 255), font=font_title, anchor="mt")
    draw.text((W // 2, 52), "throughput — logs / sec (higher is better)",
              fill=(160, 160, 160), font=font_axis, anchor="mt")

    chart_left = MARGIN_LEFT
    chart_right = W - MARGIN_RIGHT
    chart_top = MARGIN_TOP
    chart_bottom = H - MARGIN_BOTTOM
    chart_w = chart_right - chart_left

    max_val = max(THROUGHPUT) * 1.15

    for i, (label, value, color) in enumerate(zip(LABELS, THROUGHPUT, COLORS)):
        y = chart_top + i * (BAR_H + GAP)
        bar_w = int((value / max_val) * chart_w)

        # bar
        draw.rounded_rectangle(
            [chart_left, y, chart_left + bar_w, y + BAR_H],
            radius=10, fill=color,
        )

        # label (left of bar)
        draw.text((chart_left - 20, y + BAR_H // 2), label,
                  fill=(220, 220, 220), font=font_label, anchor="rm")

        # value (right of bar)
        draw.text((chart_left + bar_w + 12, y + BAR_H // 2),
                  f"{value:,} logs/sec",
                  fill=(255, 255, 255), font=font_value, anchor="lm")

    # x-axis ticks
    tick_count = 5
    for i in range(tick_count + 1):
        frac = i / tick_count
        x = chart_left + int(frac * chart_w)
        val = frac * max_val
        draw.line([(x, chart_bottom), (x, chart_bottom + 6)], fill=(120, 120, 120))
        draw.text((x, chart_bottom + 14), f"{val / 1000:.0f}k",
                  fill=(140, 140, 140), font=font_axis, anchor="mt")

    out = Path(__file__).parent / "docs" / "bench_chart.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    print(f"wrote {out}  ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()