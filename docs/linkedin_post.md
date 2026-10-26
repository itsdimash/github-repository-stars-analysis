# LinkedIn draft

All numbers come from `results/model_comparison.csv` (full data, SHA-256 `b842bc9d…a7b8`). Re-check them against that file if the analysis is re-run. Post this only after the GitHub repository and the `data-2026-10-10` Release are live and the link below resolves.

## Post

**What is associated with a GitHub repository's star count? I analysed 483,348 repositories to find out.**

I analysed a GitHub Search API snapshot (collected 2026-10-10) of 483,348 repositories with at least 100 stars and modelled `log(stars)` with regression and gradient boosting. The part I care most about is the evaluation:

→ Splits are grouped by repository owner, so an organisation's other repositories never leak into the test set
→ Model choices (polynomial degree, boosting parameters) are made on a validation set; the test set is scored once
→ A separate time holdout, with its own model selection done only on repositories created up to 2022, checks the result on repositories created from 2023

What I found:
• Forks alone explain about half of the variation in log(stars): test R² 0.515
• Gradient boosting reaches R² 0.689 (95% CI 0.677–0.701) on unseen owners; 80% of predictions land within a factor of two of the actual star count
• On the 2023+ cohort it still reaches R² 0.665
• Without forks and open issues, profile metadata alone only reach R² 0.18

Main limitation: this is a search snapshot, not a verified census of GitHub (the cache is one item short of the totals the API reported). Forks and issues are measured at the same time as stars, so this is a cross-sectional association study of repositories that already have 100+ stars. It does not show that any feature causes stars and it is not a forecast of success at creation. I also rewrote my first version, which picked the model on the test set and overstated the score.

What I built: API collection with failure logging and a cache audit that reconciles repository IDs, feature engineering, regression diagnostics (owner-clustered standard errors, leverage, VIF), a leakage-aware model comparison with owner-clustered bootstrap intervals, unit tests, and a pinned, checksum-verified dataset.

Code, notebook and write-up: https://github.com/itsdimash/github-repository-stars-analysis

#datascience #python #machinelearning #github #statistics

**Suggested visual:** `figures/03_model_comparison.png` (held-out R² by model, with the future-cohort markers).

## Project entry (Projects section)

**Title:** What Is Associated with GitHub Repository Stars? A 483k-Repository Analysis
**Skills:** Python, pandas, scikit-learn, statsmodels, regression diagnostics, REST API, data validation, pytest
**Description:** Analysis of 483,348 public GitHub repositories with 100+ stars (snapshot 2026-10-10). Owner-grouped train/validation/test evaluation with a time holdout; gradient boosting reaches test R² 0.689 (0.665 on repositories created 2023+), forks alone 0.515, profile-only models 0.18. Cross-sectional association, not causal. Includes a collection audit, tests and a checksum-pinned dataset.
**Link:** https://github.com/itsdimash/github-repository-stars-analysis
