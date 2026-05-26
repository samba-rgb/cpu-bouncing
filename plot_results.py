#!/usr/bin/env python3

import csv
import pathlib
import sys
from typing import Dict, List, Union


ROOT = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CSV_PATH = RESULTS_DIR / "benchmark_results.csv"
THROUGHPUT_SVG = RESULTS_DIR / "throughput.svg"
LATENCY_SVG = RESULTS_DIR / "latency.svg"
COLORS = {
    "thread_local_reduction": "#0f766e",
    "shared_atomic_relaxed": "#b91c1c",
    "false_sharing_adjacent_atomics": "#c2410c",
    "padded_per_thread_atomics": "#1d4ed8",
}


def read_rows() -> List[Dict[str, Union[float, str]]]:
    with CSV_PATH.open() as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "name": row["name"],
                    "threads": int(row["threads"]),
                    "seconds": float(row["seconds"]),
                    "mops": float(row["mops"]),
                    "ns_per_op": float(row["ns_per_op"]),
                }
            )
    return rows


def group_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    for values in grouped.values():
        values.sort(key=lambda item: item["threads"])
    return grouped


def scale_point(x, x_min, x_max, width):
    if x_max == x_min:
        return 0
    return (x - x_min) / (x_max - x_min) * width


def build_svg(title, y_label, grouped, metric_key, output_path):
    width = 960
    height = 540
    margin_left = 90
    margin_right = 220
    margin_top = 60
    margin_bottom = 70
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    thread_values = sorted({row["threads"] for rows in grouped.values() for row in rows})
    metric_values = [row[metric_key] for rows in grouped.values() for row in rows]
    y_max = max(metric_values) * 1.1
    y_min = 0.0

    def x_pos(thread):
        return margin_left + scale_point(thread, thread_values[0], thread_values[-1], plot_width)

    def y_pos(value):
        return margin_top + plot_height - scale_point(value, y_min, y_max, plot_height)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8" />',
        f'<text x="{margin_left}" y="30" font-size="24" font-family="Helvetica, Arial, sans-serif" fill="#111827">{title}</text>',
        f'<text x="{margin_left}" y="50" font-size="12" font-family="Helvetica, Arial, sans-serif" fill="#6b7280">CPU bouncing benchmark on Apple Silicon</text>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#374151" stroke-width="1.5" />',
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" stroke="#374151" stroke-width="1.5" />',
        f'<text x="20" y="{margin_top + 10}" font-size="12" font-family="Helvetica, Arial, sans-serif" fill="#374151">{y_label}</text>',
    ]

    for index in range(6):
        value = y_max * index / 5
        y = y_pos(value)
        label = f"{value:.0f}" if metric_key == "mops" else f"{value:.1f}"
        lines.append(f'<line x1="{margin_left}" y1="{y}" x2="{margin_left + plot_width}" y2="{y}" stroke="#e5e7eb" stroke-width="1" />')
        lines.append(f'<text x="{margin_left - 10}" y="{y + 4}" text-anchor="end" font-size="12" font-family="Helvetica, Arial, sans-serif" fill="#4b5563">{label}</text>')

    for thread in thread_values:
        x = x_pos(thread)
        lines.append(f'<line x1="{x}" y1="{margin_top}" x2="{x}" y2="{margin_top + plot_height}" stroke="#f3f4f6" stroke-width="1" />')
        lines.append(f'<text x="{x}" y="{margin_top + plot_height + 24}" text-anchor="middle" font-size="12" font-family="Helvetica, Arial, sans-serif" fill="#4b5563">{thread}</text>')

    lines.append(f'<text x="{margin_left + plot_width / 2}" y="{height - 20}" text-anchor="middle" font-size="13" font-family="Helvetica, Arial, sans-serif" fill="#374151">threads</text>')

    legend_y = margin_top + 20
    legend_x = margin_left + plot_width + 30

    for name, rows in grouped.items():
        points = " ".join(f"{x_pos(row['threads']):.2f},{y_pos(row[metric_key]):.2f}" for row in rows)
        color = COLORS[name]
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{points}" />')
        for row in rows:
            lines.append(f'<circle cx="{x_pos(row["threads"]):.2f}" cy="{y_pos(row[metric_key]):.2f}" r="4" fill="{color}" />')
        label = name.replace("_", " ")
        lines.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 20}" y2="{legend_y}" stroke="{color}" stroke-width="3" />')
        lines.append(f'<text x="{legend_x + 28}" y="{legend_y + 4}" font-size="12" font-family="Helvetica, Arial, sans-serif" fill="#111827">{label}</text>')
        legend_y += 26

    lines.append("</svg>")
    output_path.write_text("\n".join(lines))


def main() -> int:
    if not CSV_PATH.exists():
        raise SystemExit(f"missing input file: {CSV_PATH}")
    RESULTS_DIR.mkdir(exist_ok=True)
    rows = read_rows()
    grouped = group_rows(rows)
    build_svg("Throughput by Thread Count", "M ops/s", grouped, "mops", THROUGHPUT_SVG)
    build_svg("Latency by Thread Count", "ns/op", grouped, "ns_per_op", LATENCY_SVG)
    print(THROUGHPUT_SVG)
    print(LATENCY_SVG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
