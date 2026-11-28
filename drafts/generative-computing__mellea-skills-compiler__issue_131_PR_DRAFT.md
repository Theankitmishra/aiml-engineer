# PR Draft: Enforce fail-closed scope gates on parse failures (`generative-computing/mellea-skills-compiler#131`)

- **Issue:** https://github.com/generative-computing/mellea-skills-compiler/issues/131
- **Branch:** `fix/issue-131`

## Root Cause

`mellea-fy-generate.md` illustrated `query_type="out_of_scope"` in its P2 `_safe_parse_with_fallback(...)` example, but no prompt requirement or Step 7 static lint enforced fail-closed fallbacks or defaults on scope-gate schemas. As a result, generated pipelines (such as `examples/clawdefender/clawdefender_mellea/pipeline.py`) could pass an in-scope fallback (e.g. `query_type="validate"`) or define an in-scope schema default, causing unparseable or incomplete model responses to bypass the scope gate and fail open.

## Summary of Changes

1. **Step 7 static lint (`scope-gate-fail-closed`, KB13) in `src/mellea_skills_compiler/compile/lints.py`:**
   - Added `lint_scope_gate_fail_closed` and registered it in `ALL_LINTS`.
   - Identifies scope-gate fields across runtime `.py` files:
     - `Literal[...]` / `Enum` fields containing `"out_of_scope"` (or `"in_scope"` paired with a refusal sentinel).
     - Boolean scope flags (`in_scope`, `is_in_scope`, `within_scope`, `is_within_scope`, `out_of_scope`, `is_out_of_scope`, and scope-named class allow/deny booleans).
     - String-typed fields on Pydantic models inferred from comparisons against `"out_of_scope"`.
   - Verifies that schema defaults do not fail open, `_safe_parse_with_fallback(...)` and `except`-handler instantiations supply (or inherit) a fail-closed value, and `_parse_llm_json` dict fallbacks (`parsed.get(...)`, `if parsed is None:`) and `except` variable assignments fail closed.
2. **Prompt & documentation rules (`.claude/commands/`, `.pi/prompts/`, `.bob/skills/`):**
   - Added the explicit **Scope gate fail-closed rule (REQUIRED)** to `mellea-fy-generate`.
   - Added **KB13** (`Scope-gate fallbacks and defaults must fail closed`) to `mellea-fy-behaviours`.
   - Documented `scope-gate-fail-closed` in `mellea-fy-validate` (including the repairable Tier 2 list) and `mellea-fy-repair`.
3. **Tests (`tests/mellea_skills_compiler/compile/test_lints.py`):**
   - Added `TestRunLints.test_scope_gate_fail_open_caught_by_runner` and `TestScopeGateFailClosed` covering `Literal`, `Enum`, `in_scope`/`out_of_scope` booleans, schema defaults, omitted required scope fields, string-typed inferred scope fields, `except` instantiations, `_parse_llm_json` dict/`None` fallbacks, non-scope model non-interference, and prompt-template enforcement.

## Verification

- `.venv/bin/pytest tests/` -> **704 passed in 14.82s** (0 failures).
- Verified `lint_scope_gate_fail_closed` against `examples/*/*_mellea` directly:
  - `examples/clawdefender/clawdefender_mellea` -> `fail` (flags line 213 `ScanIntent.query_type='validate'`).
  - `examples/weather/weather_mellea` -> `pass`.
  - `examples/sentry-find-bugs/sentry_find_bugs_mellea` -> `pass`.
  - `examples/superpowers-systematic-debugging/superpowers_systematic_debugging_mellea` -> `pass`.
