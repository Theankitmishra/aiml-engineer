# PR Draft: Fix nullable boolean imputation routing and add `CleaningPipeline.apply` crash guard

- **Target Repository:** https://github.com/Krushil-Dhankecha/mlclean
- **Target Issue:** https://github.com/Krushil-Dhankecha/mlclean/issues/47
- **Local Repo:** `/Users/ankitkumarmishra/Downloads/Github/repos/mlclean`
- **Local Branch:** `fix/issue-47-nullable-boolean-and-crash-guard` (commit `0ee87d5`)
- **Status:** Verified locally (`ruff check .` passed, `55 passed` in `pytest --cov=mlclean`, 94% total coverage). Not pushed.

---

## Proposed PR Title

`Fix nullable boolean imputation routing and add CleaningPipeline.apply crash guard (#47)`

## Proposed PR Body

```markdown
Fixes #47.

### Summary
1. **Route boolean columns to mode imputation (`mlclean/cleaning.py`)**:
   `build_pipeline()` previously checked `pd.api.types.is_numeric_dtype(series)`, which is `True` for pandas `BooleanDtype` (`"boolean"`) and `bool`. As a result, nullable boolean columns with missing values were routed to median imputation (`1.0` / `0.0` float), causing `fillna()` to raise `TypeError: Invalid value '...' for dtype 'boolean'`. Updated the check to `pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)` (matching `checks/outliers.py` and `checks/sentinels.py`), so boolean columns use mode imputation, serialize cleanly to JSON `bool`, and keep their `boolean` dtype.
2. **Add step-level crash guard to `CleaningPipeline.apply()` (`mlclean/cleaning.py`)**:
   Added `errors: list[dict[str, Any]]` to `CleaningPipeline` (reset at the start of each `apply()` call). Each step runs inside a `try ... except Exception` guard so one failing or unknown step is recorded in `pipeline.errors` without mutating `out` or aborting subsequent steps—matching `audit()`'s crash-guard design.

### Verification
- Reproduced the exact `TypeError` and crash-propagation failures with 4 new tests in `tests/test_cleaning.py` before editing `mlclean/cleaning.py`.
- `.venv/bin/ruff check .` -> `All checks passed!`
- `.venv/bin/pytest --cov=mlclean` -> `55 passed in 0.91s` (94% coverage).
```

---

## Before Pushing (One-Time Git Identity Check)

Your machine did not have a global Git `user.name` / `user.email` configured yet, so this draft commit used a temporary local author. Before pushing to your fork:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
cd /Users/ankitkumarmishra/Downloads/Github/repos/mlclean
git commit --amend --reset-author --no-edit
```

Then fork `Krushil-Dhankecha/mlclean` on GitHub and push:

```bash
git remote add fork https://github.com/<YOUR-USERNAME>/mlclean.git
git push -u fork fix/issue-47-nullable-boolean-and-crash-guard
```
