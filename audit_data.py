"""Audit the cached GitHub search pages and reconcile them with the final CSV.

Reads every page in data/raw_all, rebuilds the list of leaf search windows and checks that
  * the leaf windows tile the whole collection period without gaps (a gap = a failed request
    that was silently skipped),
  * every leaf window has all the pages GitHub said it would have (max 10 pages = 1000 results),
  * no two windows overlap and none was truncated by the 1,000-result search limit,
  * GitHub did not flag any page as `incomplete_results`,
  * the CSV holds exactly the repository IDs found in the cache (same ID set, no duplicates on either side).

Writes docs/data_audit.json and prints a short summary. Exit status 1 when any integrity problem is
found (see `find_problems`); documented warnings (a small shortfall against GitHub's reported totals)
do not fail the audit. Usage: python audit_data.py
"""
import datetime as dt
import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

RAW = Path("data/raw_all")
CSV = Path("data/github_repos_all.csv")
OUT = Path("docs/data_audit.json")
FMT = "%Y-%m-%dT%H%M%SZ"
PAGE_RE = re.compile(r"^w_(?P<a>\d{4}-\d\d-\d\dT\d{6}Z)_(?P<b>\d{4}-\d\d-\d\dT\d{6}Z)_p(?P<n>\d+)\.json\.gz$")
START = dt.datetime(2007, 10, 1)
END = dt.datetime(2026, 10, 11)       # exclusive, same as collect_all.py


MAX_REPORTED_SHORTFALL = 1     # items missing vs GitHub's reported totals tolerated as a documented warning


def find_problems(audit):
    """Return (problems, warnings) as lists of strings; any problem makes the audit fail."""
    problems, warnings = [], []
    if audit["uncovered_gaps"]:
        problems.append(f"{len(audit['uncovered_gaps'])} uncovered gap(s) between windows")
    if audit["overlapping_windows"]:
        problems.append(f"{len(audit['overlapping_windows'])} overlapping window(s)")
    if audit["windows_with_missing_pages"]:
        problems.append(f"{len(audit['windows_with_missing_pages'])} window(s) with missing pages")
    if audit["windows_truncated_by_1000_limit"]:
        problems.append(f"{len(audit['windows_truncated_by_1000_limit'])} window(s) truncated by the 1,000-result limit")
    if audit["pages_flagged_incomplete_results"]:
        problems.append(f"{len(audit['pages_flagged_incomplete_results'])} page(s) flagged incomplete_results")
    if audit["cache_ids_missing_from_csv"]:
        problems.append(f"{audit['cache_ids_missing_from_csv']} cached repository ID(s) missing from the CSV")
    if audit["csv_ids_not_in_cache"]:
        problems.append(f"{audit['csv_ids_not_in_cache']} CSV repository ID(s) not found in the cache")
    if audit["duplicate_csv_repo_ids"]:
        problems.append(f"{audit['duplicate_csv_repo_ids']} repository ID(s) duplicated in the CSV")
    if audit["repositories_seen_more_than_once"]:
        problems.append(f"{audit['repositories_seen_more_than_once']} repository ID(s) appear more than once in the cache")
    short = audit["items_expected_from_reported_totals"] - audit["items_found_in_cache"]
    if 0 < short <= MAX_REPORTED_SHORTFALL:
        warnings.append(f"cache is {short} item(s) short of the totals GitHub reported (documented in docs/data.md)")
    elif short > MAX_REPORTED_SHORTFALL:
        problems.append(f"cache is {short} item(s) short of the totals GitHub reported")
    return problems, warnings


def main():
    windows = defaultdict(dict)       # (a, b) -> {page_no: summary}
    collected_on, queries = Counter(), set()
    incomplete_pages, ids = [], Counter()

    for f in sorted(RAW.glob("w_*_p*.json.gz")):
        m = PAGE_RE.match(f.name)
        if not m:
            continue
        a = dt.datetime.strptime(m["a"], FMT)
        b = dt.datetime.strptime(m["b"], FMT)
        with gzip.open(f, "rt", encoding="utf-8") as g:
            p = json.load(g)
        items = p.get("items", [])
        windows[(a, b)][int(m["n"])] = {"total_count": p.get("total_count", 0), "items": len(items)}
        collected_on[p.get("_collected_on", "unknown")] += 1
        if "_query" in p:
            queries.add(p["_query"].split(" ", 1)[1] if " " in p["_query"] else p["_query"])
        if p.get("incomplete_results"):
            incomplete_pages.append(f.name)
        ids.update(it["id"] for it in items)

    leaves = sorted(windows)
    # 1. tiling: every second between START and END must belong to exactly one leaf window
    gaps, overlaps, cursor = [], [], START
    for a, b in leaves:
        if a > cursor:
            gaps.append((cursor.isoformat(), (a - dt.timedelta(seconds=1)).isoformat()))
        elif a < cursor:
            overlaps.append((a.isoformat(), b.isoformat()))
        cursor = max(cursor, b + dt.timedelta(seconds=1))
    if cursor < END:
        gaps.append((cursor.isoformat(), (END - dt.timedelta(seconds=1)).isoformat()))

    # 2. pages: each leaf should hold pages 1..min(ceil(total/100), 10)
    missing_pages, truncated, expected_items, found_items, reported_total = [], [], 0, 0, 0
    for (a, b), pages in windows.items():
        total = pages[1]["total_count"] if 1 in pages else max(v["total_count"] for v in pages.values())
        want = min(math.ceil(total / 100), 10)
        absent = [n for n in range(1, want + 1) if n not in pages]
        if absent:
            missing_pages.append({"window": [a.isoformat(), b.isoformat()], "total_count": total, "missing_pages": absent})
        if total > 1000:
            truncated.append({"window": [a.isoformat(), b.isoformat()], "total_count": total})
        reported_total += total
        expected_items += min(total, 1000)
        found_items += sum(v["items"] for v in pages.values())

    df = pd.read_csv(CSV, keep_default_na=False, na_values=[""], usecols=["repo_id", "stars", "created_year"])
    csv_ids = Counter(df.repo_id.tolist())
    cache_set, csv_set = set(ids), set(csv_ids)
    missing_from_csv, not_in_cache = sorted(cache_set - csv_set), sorted(csv_set - cache_set)
    audit = {
        "audited_on": dt.date.today().isoformat(),
        "collection_dates_by_page": dict(collected_on),
        "query_template": sorted(queries)[:1] and "created:<window> stars:>=100 (sort=stars, order=desc, per_page=100)",
        "period": [START.isoformat(), (END - dt.timedelta(seconds=1)).isoformat()],
        "pages_cached": sum(len(v) for v in windows.values()),
        "leaf_windows": len(leaves),
        "split_markers": len(list(RAW.glob("*_split.txt"))),
        "uncovered_gaps": gaps,
        "overlapping_windows": overlaps,
        "windows_with_missing_pages": missing_pages,
        "windows_truncated_by_1000_limit": truncated,
        "pages_flagged_incomplete_results": incomplete_pages,
        "sum_of_reported_total_count": reported_total,
        "items_expected_from_reported_totals": expected_items,
        "items_found_in_cache": found_items,
        "unique_repositories_in_cache": len(ids),
        "repositories_seen_more_than_once": sum(1 for c in ids.values() if c > 1),
        "csv_rows": len(df),
        "csv_unique_repo_ids": int(df.repo_id.nunique()),
        "csv_rows_with_stars_below_100": int((df.stars < 100).sum()),
        "duplicate_csv_repo_ids": sum(1 for c in csv_ids.values() if c > 1),
        "cache_ids_missing_from_csv": len(missing_from_csv),
        "csv_ids_not_in_cache": len(not_in_cache),
        "example_cache_ids_missing_from_csv": missing_from_csv[:10],
        "example_csv_ids_not_in_cache": not_in_cache[:10],
        "csv_matches_cache": not missing_from_csv and not not_in_cache and len(df) == len(ids),
    }
    problems, warnings = find_problems(audit)
    audit["problems"], audit["warnings"] = problems, warnings
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(audit, indent=2, default=str))
    keys = ["pages_cached", "leaf_windows", "split_markers", "unique_repositories_in_cache", "csv_rows",
            "sum_of_reported_total_count", "items_expected_from_reported_totals", "items_found_in_cache",
            "repositories_seen_more_than_once", "duplicate_csv_repo_ids", "cache_ids_missing_from_csv",
            "csv_ids_not_in_cache", "csv_rows_with_stars_below_100"]
    for k in keys:
        print(f"{k:42s} {audit[k]}")
    print(f"{'uncovered gaps':42s} {len(gaps)}")
    print(f"{'overlapping windows':42s} {len(overlaps)}")
    print(f"{'windows with missing pages':42s} {len(missing_pages)}")
    print(f"{'windows truncated at 1000':42s} {len(truncated)}")
    print(f"{'pages flagged incomplete_results':42s} {len(incomplete_pages)}")
    print(f"{'collection dates':42s} {dict(collected_on)}")
    print(f"-> {OUT}")
    for w in warnings:
        print("WARNING:", w)
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
