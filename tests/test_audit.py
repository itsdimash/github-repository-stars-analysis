import datetime as dt
import gzip
import json

import pandas as pd
import pytest

import audit_data as a


def clean_audit(**over):
    base = {"uncovered_gaps": [], "overlapping_windows": [], "windows_with_missing_pages": [],
            "windows_truncated_by_1000_limit": [], "pages_flagged_incomplete_results": [],
            "cache_ids_missing_from_csv": 0, "csv_ids_not_in_cache": 0, "duplicate_csv_repo_ids": 0,
            "repositories_seen_more_than_once": 0,
            "items_expected_from_reported_totals": 100, "items_found_in_cache": 100}
    base.update(over)
    return base


def test_clean_audit_has_no_problems():
    assert a.find_problems(clean_audit()) == ([], [])


@pytest.mark.parametrize("key,value", [
    ("uncovered_gaps", [("a", "b")]),
    ("overlapping_windows", [("a", "b")]),
    ("windows_with_missing_pages", [{"window": [], "missing_pages": [2]}]),
    ("windows_truncated_by_1000_limit", [{"window": [], "total_count": 1500}]),
    ("pages_flagged_incomplete_results", ["p1"]),
    ("cache_ids_missing_from_csv", 1),
    ("csv_ids_not_in_cache", 1),
    ("duplicate_csv_repo_ids", 1),
    ("repositories_seen_more_than_once", 1),
])
def test_each_integrity_condition_is_a_problem(key, value):
    problems, _ = a.find_problems(clean_audit(**{key: value}))
    assert len(problems) == 1


def test_one_item_reported_shortfall_is_a_warning_larger_is_a_problem():
    problems, warnings = a.find_problems(clean_audit(items_expected_from_reported_totals=101))
    assert problems == [] and len(warnings) == 1
    problems, _ = a.find_problems(clean_audit(items_expected_from_reported_totals=150))
    assert len(problems) == 1


# ---- end to end on a tiny fake cache: the process must exit non-zero
def write_page(raw, a_, b_, total, ids, n=1):
    f = raw / f"w_{a_}_{b_}_p{n}.json.gz"
    with gzip.open(f, "wt", encoding="utf-8") as g:
        json.dump({"total_count": total, "incomplete_results": False, "_collected_on": "2026-10-10",
                   "items": [{"id": i} for i in ids]}, g)


def run_audit(tmp_path, monkeypatch, windows, csv_ids=None):
    raw = tmp_path / "raw"
    raw.mkdir()
    ids = []
    for a_, b_, total, win_ids in windows:
        write_page(raw, a_, b_, total, win_ids)
        ids += win_ids
    pd.DataFrame({"repo_id": ids if csv_ids is None else csv_ids, "stars": 200, "created_year": 2020}).to_csv(tmp_path / "r.csv", index=False)
    monkeypatch.setattr(a, "RAW", raw)
    monkeypatch.setattr(a, "CSV", tmp_path / "r.csv")
    monkeypatch.setattr(a, "OUT", tmp_path / "audit.json")
    monkeypatch.setattr(a, "START", dt.datetime(2020, 1, 1))
    monkeypatch.setattr(a, "END", dt.datetime(2020, 1, 11))
    with pytest.raises(SystemExit) as e:
        a.main()
    return e.value.code, json.loads((tmp_path / "audit.json").read_text())


W1 = ("2020-01-01T000000Z", "2020-01-05T235959Z")
W2 = ("2020-01-06T000000Z", "2020-01-10T235959Z")


def test_clean_cache_exits_zero(tmp_path, monkeypatch):
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 2, [1, 2]), (*W2, 1, [3])])
    assert code == 0 and out["problems"] == []


def test_overlapping_windows_fail_the_audit(tmp_path, monkeypatch):
    overlap = ("2020-01-04T000000Z", "2020-01-10T235959Z")
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 2, [1, 2]), (*overlap, 1, [3])])
    assert code == 1 and out["overlapping_windows"]


def test_truncated_window_fails_the_audit(tmp_path, monkeypatch):
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 1500, [1, 2]), (*W2, 1, [3])])
    assert code == 1 and out["windows_truncated_by_1000_limit"]


def test_equal_count_but_different_ids_fails_the_audit(tmp_path, monkeypatch):
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 2, [1, 2]), (*W2, 1, [3])], csv_ids=[1, 2, 99])
    assert code == 1 and not out["csv_matches_cache"]
    assert out["cache_ids_missing_from_csv"] == 1 and out["example_cache_ids_missing_from_csv"] == [3]
    assert out["csv_ids_not_in_cache"] == 1 and out["example_csv_ids_not_in_cache"] == [99]


def test_duplicate_csv_ids_fail_the_audit(tmp_path, monkeypatch):
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 2, [1, 2]), (*W2, 1, [3])], csv_ids=[1, 2, 3, 3])
    assert code == 1 and out["duplicate_csv_repo_ids"] == 1


def test_repository_cached_twice_fails_the_audit(tmp_path, monkeypatch):
    code, out = run_audit(tmp_path, monkeypatch, [(*W1, 2, [1, 2]), (*W2, 2, [2, 3])], csv_ids=[1, 2, 3])
    assert code == 1 and out["repositories_seen_more_than_once"] == 1
