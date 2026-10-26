import numpy as np
import pandas as pd

from stars_lib import (FeaturePrep, FittedModel, SAMPLE_CSV, add_features, clean, cluster_bootstrap_r2,
                       formula, gb_spec, load_data, ols_spec, scores, three_way_split)


def sample_df():
    raw, _ = load_data(SAMPLE_CSV)
    return add_features(clean(raw))


def test_splits_are_disjoint_by_owner_and_cover_everything():
    df = sample_df()
    tr, va, te = three_way_split(df)
    assert len(tr) + len(va) + len(te) == len(df)
    assert not set(tr.owner) & set(va.owner)
    assert not set(tr.owner) & set(te.owner)
    assert not set(va.owner) & set(te.owner)
    assert not set(tr.repo_id) & set(te.repo_id)


def test_groups_are_learned_from_training_rows_only():
    train = pd.DataFrame({"language": ["Python"] * 5 + ["Go"] * 3, "license": ["MIT"] * 8, "log_forks": [1.0] * 8})
    test = pd.DataFrame({"language": ["Zig"] * 50, "license": ["MIT"] * 50, "log_forks": [9.0] * 50})
    prep = FeaturePrep(n_lang=1, n_lic=1).fit(train)
    assert prep.top_lang_ == ["Python"]          # test-set majority (Zig) must not leak in
    assert prep.mu_ == 1.0
    out = prep.transform(test)
    assert set(out.lang_g) == {"Other"} and (out.lf == 8.0).all()


def test_clean_enforces_star_boundary_and_dedups():
    raw, _ = load_data(SAMPLE_CSV)
    dirty = pd.concat([raw, raw.iloc[:3]]).copy()
    dirty.loc[dirty.index[0], "stars"] = 5
    out = clean(dirty)
    assert out.repo_id.is_unique and (out.stars >= 100).all()


def test_formula_degree_and_engagement():
    assert "I(lf**3)" in formula(3) and "I(lf**4)" not in formula(3)
    f = formula(3, engagement=False)
    assert "log_forks" not in f and "log_issues" not in f and "lf" not in f.split("~")[1].replace("lang_g", "")


def test_models_fit_and_predict_on_unseen_owner_rows():
    df = sample_df()
    tr, va, te = three_way_split(df)
    for spec in (ols_spec(formula(2)), gb_spec(max_iter=30)):
        pred = FittedModel(spec, tr).predict(te)
        assert pred.shape == (len(te),) and np.isfinite(pred).all()
        assert scores(te.log_stars, pred)["R2"] > 0.3


def test_cluster_bootstrap_interval_contains_point_estimate():
    rng = np.random.default_rng(1)
    y = rng.normal(size=4000); pred = y + rng.normal(scale=.7, size=4000)
    groups = rng.integers(0, 800, size=4000)
    lo, hi = cluster_bootstrap_r2(y, pred, groups, n_boot=300)
    r2 = scores(y, pred)["R2"]
    assert lo < r2 < hi


def test_time_holdout_selection_uses_only_pre_2023_rows():
    from stars_lib import make_specs, select_degree, select_gb, split_by_owner
    df = sample_df()
    past = df[df.created_year <= 2022]
    tr, va = split_by_owner(past, 0.25)
    assert (tr.created_year <= 2022).all() and (va.created_year <= 2022).all()
    assert not set(tr.owner) & set(va.owner)
    d, table = select_degree(tr, va)
    g, gtable = select_gb(tr, va, grid=[{"learning_rate": 0.1, "max_leaf_nodes": 15}])
    assert d in range(1, 7) and len(table) == 6 and len(gtable) == 1
    assert f"degree-{d}" in "".join(make_specs(d, g))
