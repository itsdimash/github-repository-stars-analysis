# Data notes

## Source and scope
- **Source:** GitHub REST API, `GET /search/repositories` (API version `2022-11-28`).
- **Query:** `created:<window> stars:>=100`, `sort=stars`, `order=desc`, `per_page=100`.
- **Population:** public repositories created 2007-10-01 to 2026-10-10 that had at least 100 stars when collected.
- **Collection date:** 2026-10-10 (all 5,324 cached pages; the date is stored in each page and in the `collected_on` column).
- This is a **point-in-time snapshot**. Star, fork and issue counts change daily, and GitHub's search index can lag or omit repositories, so the file is not a verified census of all repositories with 100+ stars.

## How windows work
The search API returns at most 1,000 results per query. `collect_all.py` cuts the period into 7-day creation windows and splits any window with more than 1,000 matches in half, recursively, down to 60 seconds. Pages are cached as gzip JSON in `data/raw_all/` so a run can be resumed.

## Audit of the existing cache (`python audit_data.py` -> `docs/data_audit.json`)
| Check | Result |
|---|---|
| Cached pages / leaf windows | 5,324 / 998 (5 split markers) |
| Gaps or overlaps between windows over 2007-10-01..2026-10-10 | none (overlaps and gaps make the audit exit 1) |
| Windows missing pages GitHub said they had | none |
| Windows truncated by the 1,000-result cap | none (a truncated window makes the audit exit 1) |
| Pages flagged `incomplete_results` | none |
| Sum of `total_count` over windows vs items cached | 483,350 vs 483,349 (**1 repository short**, most likely a ranking shift while paginating). Classified as a documented **warning**: a shortfall of at most 1 item does not fail the audit, a larger one does |
| Duplicate repository ids (cache occurrences and CSV rows) | none; either kind makes the audit exit 1 |
| `repo_id` set in `github_repos_all.csv` vs the cache | 483,349 rows, identical ID sets (0 missing, 0 unexpected); any difference makes the audit exit 1, even when the row counts are equal |
| Rows with `stars < 100` | 1 (likely the star count changed after the search matched it); dropped in the analysis, leaving **483,348** |

The 2026-10-10 collection predates the failure logging added to `collect_all.py` afterwards, so the audit above is a reconstruction from the cached pages, not a record of live request outcomes. New runs write `data/collection_report.json` listing failed windows, failed pages, `incomplete_results` pages and truncated windows.

## Pinned analysis input
`data/github_repos_all.csv` has SHA-256 `b842bc9d48a3c5332d278e0446e1cf4fa42b1fbd2ef753380780ba92fc87a7b8` (483,349 rows). `python collect_all.py --rebuild-only` regenerates exactly this file from the raw cache (checked: byte-identical), but a new API collection yields a different snapshot.

Maintainer steps to publish the input (not done by the code; do after reviewing GitHub's terms below):
1. `python fetch_data.py --pack` builds `dist/github_repos_all_2026-10-10.csv.gz` (deterministic gzip, about 20 MB; `dist/` is git-ignored).
2. Create the GitHub Release tagged `data-2026-10-10` and attach that file. The asset name and tag are what `fetch_data.py` downloads by default.
3. On a clean clone run `python fetch_data.py`; it decompresses to a temporary file, checks the SHA-256 and only then moves it into place.

Verification status (2026-10-26): the local `dist/` asset decompresses to the pinned SHA-256, and `python fetch_data.py --verify` passes on the local CSV. **Not yet done:** the GitHub terms review, the Release publication, inspection of the committed file list (this working copy is not a Git repository) and a clean-clone run of `python fetch_data.py` plus the notebook. Re-checked 2026-10-26 in the builder session: `python fetch_data.py --verify` passes; `.gitignore` excludes `data/raw_all/`, the full CSV, the obsolete CSV and `dist/`, and keeps `archive/` out too. Git initialization, push, public visibility and Release upload were deliberately not performed in that session (restricted to local edits). Maintainer commands still needed: `git init && git add . && git status` (inspect the staged list), commit, `git push`, make the repo public, `gh release create data-2026-10-10 dist/github_repos_all_2026-10-10.csv.gz`, then a clean-clone `python fetch_data.py` plus notebook run.

Do not treat the download path as verified until a maintainer completes and records those steps here.

## Columns
`repo_id, owner, repo_name, stars, forks, open_issues, size_kb, language, license, owner_type`, the feature flags (`has_*`, `archived`, `is_template`, `default_branch_main`), `description_len, name_len, n_topics`, `age_days, days_since_push, days_since_update, created_year, created_month, collected_on`. The text value `None` in `language`/`license` means "no language / no license reported" and is a real category (read the CSV with `keep_default_na=False`).

## What is published, and why
| File | In Git? | Reason |
|---|---|---|
| `data/sample/github_repos_sample.csv` (20,000 random rows, 2.6 MB) | yes | lets the notebook and tests run out of the box |
| `data/github_repos_all.csv` (62 MB) | no | the immutable analysis input; distributed as a Release asset and verified against `data/SHA256SUMS` (see below) |
| `data/SHA256SUMS` | yes | SHA-256 of the analysis CSV |
| `data/raw_all/` (243 MB, API payloads incl. owner objects, URLs, descriptions) | no | too large; contains more fields than the analysis needs |
| `data/github_repos_all_noname.csv` | no | obsolete intermediate of the old two-step build |

The rows contain only public repository metadata (owner login and repository name included). GitHub's Terms and API rules apply to redistribution of API data; the sample is small and kept for reproducibility. Check those terms before attaching the full CSV to a release.

## Known limitations
- Selection on the outcome: only repositories with >= 100 stars are present.
- `age_days`, `days_since_push` and `days_since_update` are measured against the collection date.
- Forks and open issues are measured in the same snapshot as stars.
- `owner` is the GitHub login at collection time; renames after that are not tracked.
