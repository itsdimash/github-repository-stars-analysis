"""Write a small reproducible sample of the full CSV so the notebook runs without the 65 MB file.

Usage: python src/make_sample.py [n_rows]
"""
import sys

import pandas as pd

from stars_lib import FULL_CSV, SAMPLE_CSV

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
df = pd.read_csv(FULL_CSV, keep_default_na=False, na_values=[""])
SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
df.sample(n, random_state=0).sort_values("repo_id").to_csv(SAMPLE_CSV, index=False)
print(f"{n} of {len(df)} rows -> {SAMPLE_CSV}")
