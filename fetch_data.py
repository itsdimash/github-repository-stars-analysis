"""Fetch (or pack) the exact analysis input and verify it against the published SHA-256.

The headline numbers in README.md come from one immutable file: data/github_repos_all.csv as collected
on 2026-10-10 (483,349 rows). It is distributed as a gzip asset of a GitHub Release and checked against
data/SHA256SUMS, so a changed or re-collected file is rejected.

Usage
    python fetch_data.py                   download the release asset, verify, write data/github_repos_all.csv
    python fetch_data.py --from-file F.gz  same, but from a local copy of the asset
    python fetch_data.py --verify          only check an existing data/github_repos_all.csv
    python fetch_data.py --pack            (maintainer) build dist/<asset> from data/github_repos_all.csv
"""
import argparse
import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from stars_lib import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parent
CSV = ROOT / "data" / "github_repos_all.csv"
SUMS = ROOT / "data" / "SHA256SUMS"
ASSET_NAME = "github_repos_all_2026-10-10.csv.gz"
ASSET_URL = ("https://github.com/itsdimash/github-repository-stars-analysis/releases/download/"
             f"data-2026-10-10/{ASSET_NAME}")


def read_sums(path=None):
    """Parse `<sha256>  <name>` lines into {name: sha256}."""
    out = {}
    for line in Path(path or SUMS).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split(None, 1)
            out[name.strip().lstrip("*")] = digest
    return out


def verify(path, name, sums=None):
    """Return True when the file's SHA-256 equals the published one for `name`."""
    expected = (sums or read_sums())[name]
    actual = sha256_file(path)
    ok = actual == expected
    print(f"{'OK      ' if ok else 'MISMATCH'} {name}\n  expected {expected}\n  actual   {actual}")
    return ok


def unpack(asset, dest):
    """Decompress `asset` into a temporary file beside `dest`, verify it, then move it into place."""
    tmp = Path(str(dest) + ".part")
    with gzip.open(asset, "rb") as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out)
    if not verify(tmp, dest.name):
        tmp.unlink()
        sys.exit("Checksum mismatch: refusing to use this file.")
    tmp.replace(dest)
    print(f"wrote {dest}")


def pack(csv, out_dir):
    """Deterministic gzip (no timestamp, no file name) so the asset is reproducible."""
    if not verify(csv, csv.name):
        sys.exit("data/github_repos_all.csv does not match data/SHA256SUMS; refusing to pack it.")
    out_dir.mkdir(exist_ok=True)
    asset = out_dir / ASSET_NAME
    with open(csv, "rb") as src, open(asset, "wb") as raw, gzip.GzipFile("", "wb", 9, raw, mtime=0) as gz:
        shutil.copyfileobj(src, gz)
    print(f"wrote {asset} (sha256 {sha256_file(asset)}, {asset.stat().st_size / 1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", type=Path, help="use a local copy of the gzip asset instead of downloading")
    ap.add_argument("--verify", action="store_true", help="only verify the existing CSV")
    ap.add_argument("--pack", action="store_true", help="build the release asset from the existing CSV")
    ap.add_argument("--url", default=ASSET_URL)
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify(CSV, CSV.name) else 1)
    if args.pack:
        return pack(CSV, ROOT / "dist")
    asset = args.from_file
    if asset is None:
        asset = ROOT / "dist" / ASSET_NAME
        asset.parent.mkdir(exist_ok=True)
        print(f"downloading {args.url}")
        try:
            urllib.request.urlretrieve(args.url, asset)
        except OSError as e:
            sys.exit(f"Download failed ({e}). The release asset may not be published yet; use --from-file.")
    unpack(asset, CSV)


if __name__ == "__main__":
    main()
