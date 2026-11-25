# PR Draft: `fix(smoke-check): skip missing or placeholder fixture input paths (#123)`

- **Issue:** [generative-computing/mellea-skills-compiler#123](https://github.com/generative-computing/mellea-skills-compiler/issues/123)
- **Repository:** `generative-computing/mellea-skills-compiler`
- **Local Branch:** `fix/issue-123` (commit `9394060`, DCO signed off)

## Root Cause

When smoke-checking a file-input skill, `_run_one_fixture` in [`src/mellea_skills_compiler/compile/smoke_check.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/smoke_check.py#L399-L433) executes the compiled pipeline with `fixture.context`, which frequently contains placeholder or unbundled paths (such as `/path/to/test/data`). The pipeline raises `FileNotFoundError` or `NotADirectoryError` for that input path, but `_classify_exception` did not receive `fixture.context` or `run_dir` and had no rule to distinguish missing fixture input paths from missing bundled package assets. As a result, `run_smoke_check` marked the fixture `"failed"` (exit code 12) and failed compilation of an otherwise valid package.

## Diff Summary

- **Updated** [`src/mellea_skills_compiler/compile/smoke_check.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/smoke_check.py#L137-L310):
  - Added `_looks_like_path_input`, `_iter_fixture_path_candidates`, and `_match_missing_fixture_input_path` to extract plausible file/directory path candidates recursively from fixture inputs (`dict`, `list`, `tuple`, `set`, `Path`, `str`, or `Fixture`), verify the input path does not actually exist as expected on disk, and match `FileNotFoundError` / `NotADirectoryError` via `exc.filename` / `exc.filename2` (resolved relative to `run_dir`) or bounded error-message matching.
  - Extended `_classify_exception` with optional `fixture_inputs`, `fixture`, and `run_dir` keyword arguments (keeping backwards compatibility with existing callers) to classify matching errors as `"skipped"` with a clear message naming the placeholder/unbundled fixture input path.
  - Updated `_run_one_fixture` to pass `fixture_inputs=fixture.context` and `run_dir=run_dir` to `_classify_exception`.
- **Updated** [`tests/mellea_skills_compiler/compile/test_smoke_check.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_smoke_check.py#L368-L522):
  - Added `TestMissingFixtureInputPathClassification` (11 tests) covering `FileNotFoundError` with and without `.filename`, child paths under nonexistent fixture directories, `NotADirectoryError` on placeholders and regular files, nested lists and positional string inputs, `run_dir`-relative paths, end-to-end `run_smoke_check` (`overall_verdict == "skipped"`, `exit_code == 0`, `step_7b_report.json`), and negative cases (missing internal files, plain string substring matches, existing fixture directories with missing internal files, `fixture_inputs=None`).

## Verification

| Check | Command | Result |
|---|---|---|
| Baseline smoke-check tests (before edits) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_smoke_check.py tests/mellea_skills_compiler/compile/test_smoke_check_deprecation.py` | 43 passed in 2.42s |
| Reproducing TDD tests (before fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_smoke_check.py` | 11 failed (reproduced `overall_verdict == 'failed'` on `/path/to/test/data`), 36 passed |
| Smoke-check tests (after fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_smoke_check.py tests/mellea_skills_compiler/compile/test_smoke_check_deprecation.py` | **54 passed** in 2.43s |
| Full test suite (after fix) | `.venv/bin/pytest tests/` | **635 passed** in 7.57s |
| Import sort check (after fix) | `.venv/bin/isort --check src/mellea_skills_compiler/compile/smoke_check.py tests/mellea_skills_compiler/compile/test_smoke_check.py` | Passed (exit 0) |
| Ruff check | `.venv/bin/ruff check .` | Not installed in `.venv` |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler
git push -u origin fix/issue-123
gh pr create --repo generative-computing/mellea-skills-compiler \
  --title "fix(smoke-check): skip missing or placeholder fixture input paths (#123)" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/generative-computing__mellea-skills-compiler__issue_123_PR_DRAFT.md
```
