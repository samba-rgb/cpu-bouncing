#!/usr/bin/env python3

import csv
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
THREADS = [1, 2, 4, 8, 10]
ITERATIONS = 5_000_000


def run_once(threads: int) -> list[dict[str, str]]:
    completed = subprocess.run(
        [str(ROOT / "benchmark"), str(threads), str(ITERATIONS)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = list(csv.DictReader(completed.stdout.splitlines()))
    for row in rows:
        row["threads"] = str(threads)
    return rows


def main() -> int:
    RESULTS_DIR.mkdir(exist_ok=True)
    output_path = RESULTS_DIR / "benchmark_results.csv"
    all_rows: list[dict[str, str]] = []

    for threads in THREADS:
        all_rows.extend(run_once(threads))

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "threads",
                "operations",
                "seconds",
                "mops",
                "ns_per_op",
            ],
        )
        writer.writeheader()
        writer.writerows(all_rows)

    print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
