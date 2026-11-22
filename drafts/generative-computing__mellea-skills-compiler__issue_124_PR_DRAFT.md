# PR Draft: `fix(lints): skip copied companion directories in bundled-asset-path-resolution (#124)`

- **Issue:** [generative-computing/mellea-skills-compiler#124](https://github.com/generative-computing/mellea-skills-compiler/issues/124)
- **Repository:** `generative-computing/mellea-skills-compiler`
- **Local Branch:** `fix/issue-124` (commit `3a9b9d5`, DCO signed off)

## Root Cause

During compilation, companion directories from the skill root (`scripts/`, `references/`, `assets/`) are mirrored into the compiled package directory via `mirror_dir_contents_to_target` (Rule OUT-6). `lint_bundled_asset_path_resolution` in [`src/mellea_skills_compiler/compile/lints.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/lints.py#L243-L253) collected all `*.py` files under `package_dir` and only skipped `fixtures/`, `intermediate/`, and `__pycache__` via `_is_skipped_path`. As a result, upstream Python scripts copied from the skill's companion directories (e.g., `scripts/fetch.py` resolving `Path(__file__).parent.parent / 'assets'`) were linted as if they were Step 5 generated modules and falsely failed `bundled-asset-path-resolution`.

## Diff Summary

- **Updated** [`src/mellea_skills_compiler/compile/lints.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/lints.py#L178-L253):
  - Extended `_is_skipped_path` with an optional keyword-only `skip_bundled: bool = False` parameter that excludes files inside top-level `_BUNDLED_DIRS` (`scripts/`, `references/`, `assets/`) when `len(parts) > 1 and parts[0] in _BUNDLED_DIRS`.
  - Updated `lint_bundled_asset_path_resolution` to pass `skip_bundled=True` so copied companion directories are skipped (and excluded from `files_checked`), while leaving `lint_import_soundness` and top-level generated modules unchanged.
- **Updated** [`tests/mellea_skills_compiler/compile/test_lints.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_lints.py#L287-L345):
  - Added `test_skips_copied_companion_directories` covering `scripts/`, nested `scripts/nested/`, `references/`, and `assets/` alongside generated `tools.py`.
  - Added `test_still_flags_generated_code_when_companion_dirs_skipped` confirming bad path resolution in generated Step 5 files (`tools.py`) is still flagged when companion directories are skipped.

## Verification

| Check | Command | Result |
|---|---|---|
| Baseline test suite (before edits) | `.venv/bin/pytest tests/` | 624 passed in 20.69s |
| Reproducing tests (before fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_lints.py -k TestBundledAssetPathResolution` | 2 failed (reproduced `Path(__file__).parent.parent` / `assets` failure on `scripts/fetch.py`), 15 passed |
| Focused lint tests (after fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_lints.py -k TestBundledAssetPathResolution` | **17 passed** in 1.06s |
| Full test suite (after fix) | `.venv/bin/pytest tests/` | **626 passed** in 8.85s |
| Import sort check (after fix) | `.venv/bin/isort --check src/mellea_skills_compiler/compile/lints.py tests/mellea_skills_compiler/compile/test_lints.py` | Passed (exit 0) |
| Ruff check | `.venv/bin/ruff` | Not installed in `.venv` |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler
git push -u origin fix/issue-124
gh pr create --repo generative-computing/mellea-skills-compiler \
  --title "fix(lints): skip copied companion directories in bundled-asset-path-resolution (#124)" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/generative-computing__mellea-skills-compiler__issue_124_PR_DRAFT.md
```
