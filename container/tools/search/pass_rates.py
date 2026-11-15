"""Pass rates of a search test run, from its raw results (run3/part_*.jsonl).

Usage (from container/): python3 tools/search/pass_rates.py [run_dir]
  default run_dir: reports/search-test/run3

top50: the source page is among the links shown (rank 1..50).
top5 : the source page is among the first five links.
top1 : the source page is the first link.
"""

import glob
import json
import os
import sys
from collections import defaultdict


def load_rows(run_dir: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(run_dir, "part_*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    return rows


def rate(part: list[dict], limit: int) -> tuple[int, float]:
    hits = sum(1 for r in part if r["rank"] is not None and r["rank"] <= limit)
    return hits, 100.0 * hits / len(part) if part else 0.0


def main() -> None:
    container = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(container, "reports", "search-test", "run3")
    rows = load_rows(run_dir)
    print(f"queries {len(rows)}")
    for label, limit in (("top1", 1), ("top5", 5), ("top50", 50)):
        hits, pct = rate(rows, limit)
        print(f"{label:6} {hits:5}  {pct:6.2f}%")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["origin"]].append(r)
    for origin in sorted(groups):
        part = groups[origin]
        print(f"{origin:12} {len(part):5}  top50 {rate(part, 50)[1]:6.1f}%  top5 {rate(part, 5)[1]:6.1f}%")


if __name__ == "__main__":
    main()
