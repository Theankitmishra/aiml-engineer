# PR Draft: Responsible AI tests, fixtures, tie-breaker, and `assessment list` column

**Issue:** https://github.com/secfoo-com/secfoo/issues/140  
**Branch:** `fix/issue-140`

## Root Cause & Motivation
1. **`secfoo assessment list` omitted Responsible AI status:** `RunRepository` computed `responsible_ai_risk` and `responsible_ai_status`, and `assessment show` printed them, but `assessment_list` in `src/secfoo/cli.py` had no `Responsible AI` column.
2. **Non-deterministic tie-breaking on equal timestamps:** `_responsible_ai_badges` in `src/secfoo/storage/repository.py` ordered only by `started_at DESC LIMIT 1`, so back-to-back runs within the same timestamp could resolve to the older run (`id ASC` scan order).
3. **Test suite & fixture gaps:** `tests/fixtures/reports/responsible-ai-compliance*.md` were hand-written with internal contradictions, lacked a `Critical` fixture and a fixture where overall rating differs from worst finding severity, `test_reopening_a_completed_assessment_makes_the_rating_provisional_again` did not verify the intermediate `confirmed` state, assessment-page badge checks only covered `low` with a blanket `"moderate" not in html` check, and `_stat` was brittle to extra classes or inline `info_tip` markup.

## Summary of Changes
- **`src/secfoo/storage/repository.py`**: Added `, id DESC` tie-breaker to `_responsible_ai_badges`'s `ORDER BY started_at DESC, id DESC LIMIT 1` query.
- **`src/secfoo/cli.py`**: Added the `Responsible AI` column to `assessment_list` (`<tier> (<status>)`, or `-` when unrated).
- **`tests/fixtures/reports/`**:
  - Replaced `responsible-ai-compliance.md` with the real Claude `--depth quick` report on `openai/openai-quickstart-python`.
  - Rewrote `responsible-ai-compliance-medium.md` and `responsible-ai-compliance-high.md` as coherent variants derived from the real report.
  - Added `responsible-ai-compliance-critical.md` (`Overall risk rating: Critical`, 1C 2H 2M 1L) and `responsible-ai-compliance-medium-with-high-finding.md` (`Overall risk rating: Medium` with 1 High finding).
- **`tests/test_responsible_ai_end_to_end.py`**:
  - Added `critical` tier to `TIERS` and updated `High-risk` stat assertion (`risk == "high-risk"`).
  - Added `test_overall_rating_is_independent_of_worst_finding` and `test_the_badge_breaks_equal_timestamp_ties_in_favor_of_the_latest_run`.
  - Updated `test_assessment_list_shows_the_auto_created_case_file_with_its_findings` to verify the `Responsible AI` column for both rated (`low-risk (provisional)`) and unrated (`-`) assessments.
  - Updated `test_reopening_a_completed_assessment_makes_the_rating_provisional_again` to assert both `303` redirects and the intermediate `Responsible AI: low-risk (confirmed)` state.
  - Parametrized `test_assessment_pages_show_the_tier_badge` over all tiers using exact badge element checks, and updated `_stat` to tolerate extra classes and `<details class="info-tip">` inside labels.
- **`tests/test_cli_assessment.py`**: Passed `env={"COLUMNS": "200"}` to `assessment list` invocations so Rich does not truncate project/type cells after adding the 8th column.

## Verification
- `.venv/bin/pytest tests/test_responsible_ai_end_to_end.py tests/test_cli_assessment.py -v` -> **42 passed**
- `.venv/bin/pytest tests/test_responsible_ai_end_to_end.py tests/test_cli_assessment.py tests/test_storage/test_assessments.py tests/test_web/test_assessment_routes.py -v` -> **146 passed**
- `.venv/bin/ruff check tests/test_responsible_ai_end_to_end.py tests/test_cli_assessment.py` -> **All checks passed**

## Push Instructions
```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/secfoo
git push -u origin fix/issue-140
```
Then open a PR targeting `secfoo-com/secfoo:main` referencing `Closes #140`.
