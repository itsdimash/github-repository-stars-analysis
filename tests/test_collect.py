import gzip
import json
from pathlib import Path

import collect_all as c


def page(items, total, incomplete=False):
    return {"total_count": total, "incomplete_results": incomplete, "items": items}


def fake_item(i):
    return {"id": i, "full_name": f"own{i}/repo{i}", "name": f"repo{i}", "stargazers_count": 150, "forks_count": 1,
            "open_issues_count": 0, "size": 10, "language": None, "license": None, "owner": {"type": "User"},
            "has_issues": True, "has_wiki": True, "has_pages": False, "has_projects": True, "has_downloads": True,
            "fork": False, "archived": False, "homepage": "", "description": "d", "topics": [],
            "default_branch": "main", "created_at": "2020-01-01T00:00:00Z", "pushed_at": "2020-02-01T00:00:00Z",
            "updated_at": "2020-03-01T00:00:00Z"}


def setup(tmp_path, monkeypatch, api):
    monkeypatch.setattr(c, "RAW", tmp_path / "raw")
    monkeypatch.setattr(c, "OUT", tmp_path / "out.csv")
    (tmp_path / "raw").mkdir()
    monkeypatch.setattr(c, "REPORT_STATE", c.Report())
    monkeypatch.setattr(c, "api_get", api)


def test_failed_first_request_is_reported_not_silent(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, lambda session, params, tries=5: None)
    import datetime as dt
    n = c.fetch_window(None, dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 7, 23, 59, 59), "2026-10-10")
    assert n == 0
    assert len(c.REPORT_STATE.failed_windows) == 1 and not c.REPORT_STATE.ok()


def test_failed_later_page_and_incomplete_results_are_reported(tmp_path, monkeypatch):
    def api(session, params, tries=5):
        if params["page"] == 1:
            return page([fake_item(i) for i in range(100)], total=250, incomplete=True)
        if params["page"] == 2:
            return page([fake_item(100 + i) for i in range(100)], total=250)
        return None
    setup(tmp_path, monkeypatch, api)
    import datetime as dt
    n = c.fetch_window(None, dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 7, 23, 59, 59), "2026-10-10")
    assert n == 200
    assert [p["page"] for p in c.REPORT_STATE.failed_pages] == [3]
    assert len(c.REPORT_STATE.incomplete_pages) == 1


def test_oversized_unsplittable_window_is_reported(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, lambda session, params, tries=5: page([fake_item(1)], total=5000))
    import datetime as dt
    c.fetch_window(None, dt.datetime(2020, 1, 1, 0, 0, 0), dt.datetime(2020, 1, 1, 0, 0, 30), "2026-10-10")
    assert len(c.REPORT_STATE.truncated_windows) == 1


def test_build_csv_writes_names_and_is_rebuilt_from_pages(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, None)
    payload = page([fake_item(1), fake_item(2), fake_item(1)], total=3)
    payload["_collected_on"] = "2026-10-10"
    c.write_gz(c.RAW / "w_2020-01-01T000000Z_2020-01-07T235959Z_p1.json.gz", payload)
    assert c.build_csv("2026-10-10") == 2          # duplicate id removed
    header = c.OUT.read_text().splitlines()[0].split(",")
    assert header[:3] == ["repo_id", "owner", "repo_name"]
    c.OUT.write_text("stale")
    c.build_csv("2026-10-10")                       # a rerun overwrites, never reuses stale content
    assert c.OUT.read_text().startswith("repo_id,owner,repo_name")
