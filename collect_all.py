"""Collect every GitHub repository with >= 100 stars via the search API and build the CSV.

Usage:
    export GITHUB_TOKEN=...        # token without scopes is enough
    python collect_all.py                  # collect (resumable) + build data/github_repos_all.csv
    python collect_all.py --rebuild-only   # rebuild the CSV from the cached pages, no network

The period is cut into 7-day windows by creation date; windows with more than 1,000 matches
(the search API cap) are split in half recursively. Failures are logged to data/collection_report.json.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import requests

API = "https://api.github.com"
START = dt.datetime(2007, 10, 1)     # earliest repos on GitHub are from ~2007
END = dt.datetime(2026, 10, 11)      # exclusive; last window ends 2026-10-10 23:59:59
MIN_STARS = 100
WINDOW_DAYS = 7
MIN_SPLIT_SECONDS = 60               # never split a window shorter than this
SEARCH_SLEEP = 2.0                   # seconds between requests (>= 1 s required)

RAW = Path("data/raw_all")
OUT = Path("data/github_repos_all.csv")
REPORT = Path("data/collection_report.json")


class Report:
    """Everything that went wrong (or was capped) during a run, so gaps are never silent."""

    def __init__(self):
        self.failed_windows = []        # first request of a window failed -> window has no data
        self.failed_pages = []          # a later page failed -> window only partly collected
        self.incomplete_pages = []      # GitHub flagged `incomplete_results` (search timed out)
        self.truncated_windows = []     # > 1000 matches in a window that cannot be split further

    def ok(self):
        return not (self.failed_windows or self.failed_pages or self.incomplete_pages or self.truncated_windows)

    def as_dict(self):
        return {"complete": self.ok(), "failed_windows": self.failed_windows,
                "failed_pages": self.failed_pages, "incomplete_pages": self.incomplete_pages,
                "truncated_windows": self.truncated_windows}


REPORT_STATE = Report()


def make_session():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GITHUB_TOKEN first (a token with no scopes is enough).")
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-repository-stars-analysis",
    })
    return s


def api_get(session, params, tries=5):
    """Search request with pause + rate-limit handling. Returns parsed JSON or None."""
    for attempt in range(tries):
        time.sleep(SEARCH_SLEEP)
        try:
            r = session.get(f"{API}/search/repositories", params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  network error ({e}); retrying")
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (404, 422, 451):
            return None
        if r.status_code in (403, 429):
            wait = int(r.headers.get("Retry-After", 0))
            reset = r.headers.get("X-RateLimit-Reset")
            if not wait and reset:
                wait = max(int(reset) - int(time.time()), 0) + 2
            wait = max(wait, 30)
            print(f"  rate limited, sleeping {wait}s")
            time.sleep(wait)
            continue
        time.sleep(2 ** attempt)
    return None


def fmt(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_gz(path, obj):
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f)
    tmp.replace(path)                       # atomic: no half-written files if interrupted


def get_page(session, q, tag, n, collected_on):
    f = RAW / f"{tag}_p{n}.json.gz"
    if f.exists():
        p = read_gz(f)
    else:
        p = api_get(session, {"q": q, "sort": "stars", "order": "desc", "per_page": 100, "page": n})
        if p is None:
            return None
        p["_collected_on"] = collected_on
        p["_query"] = q
        write_gz(f, p)
    if p.get("incomplete_results"):
        REPORT_STATE.incomplete_pages.append(f.name)
    return p


def fetch_window(session, a, b, collected_on):
    """Fetch ALL repos with stars>=MIN_STARS created in [a, b]. Returns number of repos."""
    q = f"created:{fmt(a)}..{fmt(b)} stars:>={MIN_STARS}"
    tag = f"w_{fmt(a)}_{fmt(b)}".replace(":", "")
    seconds = int((b - a).total_seconds())
    marker = RAW / f"{tag}_split.txt"

    def split():
        mid = a + dt.timedelta(seconds=seconds // 2)
        return (fetch_window(session, a, mid, collected_on) +
                fetch_window(session, mid + dt.timedelta(seconds=1), b, collected_on))

    if marker.exists():                      # decided to split in an earlier run
        return split()
    first = get_page(session, q, tag, 1, collected_on)
    if first is None:
        print(f"  {fmt(a)}: request failed, window NOT collected")
        REPORT_STATE.failed_windows.append({"window": [fmt(a), fmt(b)], "query": q})
        return 0
    total = first.get("total_count", 0)
    if total > 1000 and seconds > MIN_SPLIT_SECONDS:
        (RAW / f"{tag}_p1.json.gz").unlink(missing_ok=True)
        marker.write_text("split")
        return split()
    if total > 1000:
        print(f"  WARNING {fmt(a)}: {total} matches in <= {MIN_SPLIT_SECONDS}s, keeping top 1000")
        REPORT_STATE.truncated_windows.append({"window": [fmt(a), fmt(b)], "total_count": total})
    n_items = len(first.get("items", []))
    for n in range(2, min(math.ceil(total / 100), 10) + 1):
        p = get_page(session, q, tag, n, collected_on)
        if p is None:
            print(f"  {fmt(a)}: page {n} failed, window only partly collected")
            REPORT_STATE.failed_pages.append({"window": [fmt(a), fmt(b)], "page": n, "total_count": total})
            break
        n_items += len(p.get("items", []))
    return n_items


def collect(session, collected_on):
    RAW.mkdir(parents=True, exist_ok=True)
    t0, total_rows = time.time(), 0
    a = START
    while a < END:
        b = min(a + dt.timedelta(days=WINDOW_DAYS) - dt.timedelta(seconds=1),
                END - dt.timedelta(seconds=1))
        got = fetch_window(session, a, b, collected_on)
        total_rows += got
        print(f"{a.date()}: {got} repos (running total {total_rows}, "
              f"{(time.time() - t0) / 60:.0f} min elapsed)")
        a += dt.timedelta(days=WINDOW_DAYS)
    return total_rows


def ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def flatten(item, collected_on):
    """One raw search item -> feature row (public repository metadata only)."""
    created = ts(item["created_at"])
    pushed = ts(item["pushed_at"]) if item.get("pushed_at") else created
    updated = ts(item["updated_at"])
    now = dt.datetime.strptime(collected_on, "%Y-%m-%d")
    lic = item.get("license") or {}
    owner, name = item["full_name"].split("/", 1)
    return {
        "repo_id": item["id"],
        "owner": owner,
        "repo_name": name,
        "stars": item["stargazers_count"],           # target (use log)
        "forks": item["forks_count"],
        "open_issues": item["open_issues_count"],
        "size_kb": item["size"],
        "language": item.get("language") or "None",
        "license": lic.get("spdx_id") or "None",
        "owner_type": item["owner"]["type"],
        "has_issues": int(item["has_issues"]),
        "has_wiki": int(item["has_wiki"]),
        "has_pages": int(item["has_pages"]),
        "has_projects": int(item["has_projects"]),
        "has_downloads": int(item["has_downloads"]),
        "has_discussions": int(item.get("has_discussions", False)),
        "is_fork": int(item["fork"]),
        "archived": int(item["archived"]),
        "is_template": int(item.get("is_template", False)),
        "allow_forking": int(item.get("allow_forking", True)),
        "has_homepage": int(bool(item.get("homepage"))),
        "has_description": int(bool(item.get("description"))),
        "description_len": len(item.get("description") or ""),
        "name_len": len(item["name"]),
        "n_topics": len(item.get("topics") or []),
        "default_branch_main": int(item.get("default_branch") in ("main", "master")),
        "age_days": (now - created).days,
        "days_since_push": (now - pushed).days,
        "days_since_update": (now - updated).days,
        "created_year": created.year,
        "created_month": created.month,
        "collected_on": collected_on,
    }


def build_csv(collected_on):
    """Stream every saved page into one CSV (deduplicated by repo id), written atomically.

    Always rebuilt from the raw pages, so a rerun can never mix in a stale intermediate file.
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    seen, stars, earliest = set(), [], None
    writer = None
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        for f in sorted(RAW.glob("w_*_p*.json.gz")):
            payload = read_gz(f)
            col = payload.get("_collected_on", collected_on)
            for it in payload.get("items", []):
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                row = flatten(it, col)
                if writer is None:
                    writer = csv.DictWriter(fh, fieldnames=list(row.keys()), lineterminator="\n")
                    writer.writeheader()
                writer.writerow(row)
                stars.append(row["stars"])
                c = ts(it["created_at"])
                earliest = c if earliest is None or c < earliest else earliest
    tmp.replace(OUT)
    print(f"\nSaved {len(seen)} rows -> {OUT}")
    if stars:
        print(f"stars: min {min(stars)}, median {statistics.median(stars)}, max {max(stars)}")
        print(f"earliest repo created: {earliest}")
    return len(seen)


def write_report(collected_on, n_rows):
    info = REPORT_STATE.as_dict()
    info.update({"collected_on": collected_on, "csv_rows": n_rows,
                 "period": [fmt(START), fmt(END - dt.timedelta(seconds=1))],
                 "min_stars": MIN_STARS, "api_version": "2022-11-28"})
    REPORT.write_text(json.dumps(info, indent=2))
    if info["complete"]:
        print(f"Collection report: complete, no failed/incomplete/truncated requests -> {REPORT}")
    else:
        print(f"WARNING: collection is NOT complete, see {REPORT}\n"
              f"  failed windows {len(REPORT_STATE.failed_windows)}, failed pages {len(REPORT_STATE.failed_pages)}, "
              f"incomplete pages {len(REPORT_STATE.incomplete_pages)}, truncated windows {len(REPORT_STATE.truncated_windows)}\n"
              f"  rerun the same command to retry (finished pages are cached).")
    return info["complete"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-only", action="store_true",
                        help="skip the API and only rebuild the CSV from the cached pages in data/raw_all")
    args = parser.parse_args()

    if args.rebuild_only:
        pages = sorted(RAW.glob("w_*_p*.json.gz"))
        if not pages:
            sys.exit(f"No cached pages in {RAW}; run without --rebuild-only (needs GITHUB_TOKEN).")
        collected_on = read_gz(pages[0]).get("_collected_on", dt.date.today().isoformat())
    else:
        session = make_session()
        collected_on = dt.date.today().isoformat()
        collect(session, collected_on)
    n_rows = build_csv(collected_on)
    if not args.rebuild_only:
        sys.exit(0 if write_report(collected_on, n_rows) else 1)


if __name__ == "__main__":
    main()
