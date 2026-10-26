import gzip
import hashlib

import pytest

import fetch_data as f


def test_unpack_accepts_matching_asset_and_rejects_tampered_one(tmp_path, monkeypatch):
    body = b"repo_id,stars\n1,150\n"
    dest = tmp_path / "github_repos_all.csv"
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"# comment\n{hashlib.sha256(body).hexdigest()}  github_repos_all.csv\n")
    monkeypatch.setattr(f, "SUMS", sums)
    good = tmp_path / "good.gz"
    good.write_bytes(gzip.compress(body))
    f.unpack(good, dest)
    assert dest.read_bytes() == body

    dest.unlink()
    bad = tmp_path / "bad.gz"
    bad.write_bytes(gzip.compress(body + b"2,999\n"))
    with pytest.raises(SystemExit):
        f.unpack(bad, dest)
    assert not dest.exists() and not (tmp_path / "github_repos_all.csv.part").exists()


def test_published_checksum_file_lists_the_analysis_csv():
    sums = f.read_sums()
    assert len(sums["github_repos_all.csv"]) == 64
