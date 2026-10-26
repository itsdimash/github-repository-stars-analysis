"""Shared helpers for the GitHub-stars analysis: loading, features, leakage-safe splits, metrics.

Design rule: anything that is *learned from data* (top-language list, centring constant) lives in
`FeaturePrep.fit`, which is only ever called on the rows a model is allowed to see.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

ROOT = Path(__file__).resolve().parents[1]
FULL_CSV = ROOT / "data" / "github_repos_all.csv"
SAMPLE_CSV = ROOT / "data" / "sample" / "github_repos_sample.csv"

SEED = 42
MIN_STARS = 100
CONSTANT_COLS = ["has_downloads", "is_fork", "allow_forking", "collected_on"]   # constant in the full data

NUM = ["log_forks", "log_issues", "log_size", "age_years", "years_since_push",
       "n_topics", "log_desc_len", "name_len"]
FLAGS = ["owner_type_Organization", "has_issues", "has_wiki", "has_pages", "has_projects",
         "has_discussions", "archived", "is_template", "has_homepage", "default_branch_main"]
ENGAGEMENT = ["log_forks", "log_issues"]      # measured in the same snapshot as the stars
CATS = " + C(lang_g, Treatment('Python')) + C(lic_g, Treatment('MIT'))"


# ----------------------------------------------------------------------------- loading
def load_data(path=None):
    """Load the full CSV if present, otherwise the bundled sample. Returns (df, source_name)."""
    path = Path(path) if path else (FULL_CSV if FULL_CSV.exists() else SAMPLE_CSV)
    # keep the text "None" (no language / no license) as a real category instead of NaN
    df = pd.read_csv(path, keep_default_na=False, na_values=[""])
    return df, path.name


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def clean(df):
    """Deduplicate, enforce the stars >= 100 boundary, drop constant columns."""
    df = df.drop_duplicates("repo_id")
    df = df[df.stars >= MIN_STARS].copy()
    return df.drop(columns=[c for c in CONSTANT_COLS if c in df.columns])


def add_features(df):
    """Row-wise transforms only (no statistic is learned from the data here)."""
    df = df.copy()
    df["log_stars"] = np.log(df.stars)
    df["log_size"] = np.log1p(df.size_kb)
    df["log_forks"] = np.log1p(df.forks)
    df["log_issues"] = np.log1p(df.open_issues)
    df["has_license"] = (df.license != "None").astype(int)
    df["age_years"] = df.age_days.clip(lower=0) / 365
    df["years_since_push"] = df.days_since_push.clip(lower=0) / 365
    df["log_desc_len"] = np.log1p(df.description_len)
    df["owner_type_Organization"] = (df.owner_type == "Organization").astype(int)
    # derived features used only in the "redundant features" experiment
    df["days_active"] = (df.age_days - df.days_since_push).clip(lower=0)
    df["log_forks_per_year"] = np.log1p(df.forks / (df.age_days.clip(lower=30) / 365))
    return df


class FeaturePrep:
    """Learns the top-k language/license groups and the forks centring constant from ONE dataset."""

    def __init__(self, n_lang=12, n_lic=6):
        self.n_lang, self.n_lic = n_lang, n_lic

    def fit(self, train):
        self.top_lang_ = list(train.language.value_counts().head(self.n_lang).index)
        self.top_lic_ = list(train.license.value_counts().head(self.n_lic).index)
        self.mu_ = float(train.log_forks.mean())
        return self

    def transform(self, df):
        df = df.copy()
        df["lang_g"] = np.where(df.language.isin(self.top_lang_), df.language, "Other")
        df["lic_g"] = np.where(df.license.isin(self.top_lic_), df.license, "Other")
        df["lf"] = df.log_forks - self.mu_            # centred so that lf, lf^2, lf^3 are less collinear
        return df


# ----------------------------------------------------------------------------- splits
def split_by_owner(df, test_size, seed=SEED):
    """Random split in which all repositories of an owner land on the same side.

    Repositories of one owner (e.g. 1,900 from `microsoft`) are not independent; splitting by row
    would let the model see the owner's other repositories while being tested on one of them.
    """
    gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    a, b = next(gss.split(df, groups=df.owner))
    return df.iloc[a], df.iloc[b]


def three_way_split(df, seed=SEED):
    """60 / 20 / 20 train / validation / test, grouped by owner."""
    dev, test = split_by_owner(df, 0.20, seed)
    train, val = split_by_owner(dev, 0.25, seed + 1)
    return train, val, test


# ----------------------------------------------------------------------------- models
def formula(degree=1, engagement=True):
    """OLS formula for log(stars); `degree` is the polynomial degree in (centred) log(1 + forks)."""
    num = [c for c in NUM if c != "log_forks" and (engagement or c not in ENGAGEMENT)]
    terms = num + FLAGS
    if engagement:
        terms = terms + ["lf"] + [f"I(lf**{k})" for k in range(2, degree + 1)]
    return "log_stars ~ " + " + ".join(terms) + CATS


def ols_spec(formula_str):
    return {"kind": "ols", "formula": formula_str}


def gb_spec(engagement=True, **params):
    return {"kind": "gb", "engagement": engagement, "params": params}


class FittedModel:
    """A model bundled with the FeaturePrep learned from its own training rows."""

    def __init__(self, spec, train):
        self.spec = spec
        self.prep = FeaturePrep().fit(train)
        tr = self.prep.transform(train)
        if spec["kind"] == "ols":
            self.result = smf.ols(spec["formula"], tr).fit()
        else:
            self.cols = [c for c in NUM if spec["engagement"] or c not in ENGAGEMENT] + FLAGS + ["lang_g", "lic_g"]
            X = self._design(tr, fit=True)
            params = dict(early_stopping=True, max_iter=500, random_state=0)
            params.update(spec["params"])
            self.result = HistGradientBoostingRegressor(categorical_features="from_dtype", **params)
            self.result.fit(X, tr.log_stars)

    def _design(self, tr, fit=False):
        X = tr[self.cols].copy()
        if fit:
            self.levels_ = {c: sorted(X[c].unique()) for c in ("lang_g", "lic_g")}
        for c, lv in self.levels_.items():
            X[c] = pd.Categorical(X[c], categories=lv)
        return X

    def predict(self, df):
        d = self.prep.transform(df)
        return np.asarray(self.result.predict(d if self.spec["kind"] == "ols" else self._design(d)))


# ----------------------------------------------------------------------------- model selection
M1_FORMULA = "log_stars ~ " + " + ".join(NUM + FLAGS + ["has_license"])
DEGREES = range(1, 7)
GB_GRID = [{"learning_rate": lr, "max_leaf_nodes": leaves} for lr in (0.05, 0.1) for leaves in (31, 127)]
DEGREE_TOL = 0.002      # parsimony rule: smallest degree within 0.002 RMSE of the best validation RMSE


def select_degree(train, val, tol=DEGREE_TOL):
    """Fit degrees 1..6 on `train`, score them on `val`. Returns (chosen degree, table)."""
    rows = []
    for d in DEGREES:
        m = FittedModel(ols_spec(formula(d)), train)
        s_val = scores(val.log_stars, m.predict(val))
        rows.append({"degree": d, "R2_train": m.result.rsquared, "R2_val": s_val["R2"], "RMSE_val": s_val["RMSE"]})
    table = pd.DataFrame(rows)
    return int(table[table.RMSE_val <= table.RMSE_val.min() + tol].degree.min()), table


def select_gb(train, val, grid=GB_GRID):
    """Fit each boosting setting on `train`, score on `val`. Returns (best params, table)."""
    rows = []
    for params in grid:
        g = FittedModel(gb_spec(**params), train)
        s_val = scores(val.log_stars, g.predict(val))
        rows.append({**params, "R2_val": s_val["R2"], "RMSE_val": s_val["RMSE"], "n_iter": int(g.result.n_iter_)})
    table = pd.DataFrame(rows)
    return grid[int(table.RMSE_val.idxmin())], table


def make_specs(best_d, best_gb):
    """The seven models that are refit and scored. Labels are stable apart from the chosen degree."""
    return {
        "A  M0  forks only":            ols_spec("log_stars ~ log_forks"),
        "A  M1  multiple regression":   ols_spec(M1_FORMULA),
        "A  M2  + language, license":   ols_spec(formula(1)),
        f"A  M3  M2 + degree-{best_d} forks": ols_spec(formula(best_d)),
        "A  GB  gradient boosting":     gb_spec(**best_gb),
        "B  M2  profile-only OLS":      ols_spec(formula(1, engagement=False)),
        "B  GB  profile-only boosting": gb_spec(engagement=False, **best_gb),
    }


# ----------------------------------------------------------------------------- metrics
def scores(y, pred):
    return {"R2": r2_score(y, pred), "RMSE": float(np.sqrt(mean_squared_error(y, pred))),
            "MAE": mean_absolute_error(y, pred),
            "within_2x": float((np.abs(np.asarray(y) - np.asarray(pred)) < np.log(2)).mean())}


def cluster_bootstrap_r2(y, pred, groups, n_boot=2000, seed=0):
    """95% interval for test R^2, resampling owners (not rows) to respect within-owner dependence."""
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    codes, _ = pd.factorize(pd.Series(np.asarray(groups)))
    k = codes.max() + 1
    n_g = np.bincount(codes, minlength=k).astype(float)
    sy = np.bincount(codes, weights=y, minlength=k)
    syy = np.bincount(codes, weights=y * y, minlength=k)
    sse = np.bincount(codes, weights=(y - pred) ** 2, minlength=k)
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    for i in range(n_boot):
        w = rng.multinomial(k, np.full(k, 1.0 / k))
        n, s1, s2, e = w @ n_g, w @ sy, w @ syy, w @ sse
        out[i] = 1 - e / (s2 - s1 * s1 / n)
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))
