# PR Summary: Validate `melleafy.json` export fields at compile time

- **Issue**: https://github.com/generative-computing/mellea-skills-compiler/issues/125
- **Branch**: `fix/issue-125`

## Root Cause

`melleafy.json` is written by the model during compilation, and none of the Python Step 7 structural lints in `compile/lints.py` checked whether `melleafy.json` existed or contained valid `manifest_version`, `package_name`, and `entry_signature` fields before compilation succeeded. As a result, a compile could pass all lints and the smoke check and then fail later at `mellea-skills export` (or silently misparse a malformed `entry_signature` as `no_args` in `export/exporter.py:_parse_entry_signature`, or crash with `AttributeError` on non-string values).

## Changes

- **`src/mellea_skills_compiler/export/exporter.py`**
  - Extracted module-level `_ENTRY_SIG_RE` and shared validation helpers `_validate_manifest_version()` and `_validate_entry_signature_str()`.
  - Hardened `run_export()`, `stage1_validate()`, and `stage2_load()` to validate JSON object shape, string/semver `manifest_version`, valid Python identifier `package_name`, and well-formed `entry_signature` instead of silently falling back to `no_args` or blaming `'package_name'` on invalid JSON.
- **`src/mellea_skills_compiler/compile/lints.py`**
  - Added `lint_melleafy_manifest()` (`lint_id="melleafy-manifest"`) and registered it in `ALL_LINTS`.
  - Validates `<package>/melleafy.json` existence, JSON object parsing, `manifest_version` (non-empty string >= `MIN_MANIFEST_VERSION`), `package_name` (valid identifier matching `package_dir.name`), and `entry_signature` (parseable by `_validate_entry_signature_str` and consistent with `pipeline.py` function existence and parameter requirements via `_check_entry_signature_against_pipeline()`).
- **`tests/mellea_skills_compiler/compile/test_lints.py`**
  - Updated `TestRunLints.test_overall_pass_when_all_pass` synthetic compliant package to include `melleafy.json`.
  - Added `TestMelleafyManifest` covering missing/malformed `melleafy.json`, `manifest_version`, `package_name`, `entry_signature`, `pipeline.py` consistency checks, multi-failure collection, and `compiler.validate()`.
- **`tests/mellea_skills_compiler/export/test_export_manifest_validation.py`**
  - Added unit tests for `stage1_validate()`, `stage2_load()`, and `run_export()` error handling on invalid manifest inputs.

## Verification

- `.venv/bin/pytest tests/ -q` → **692 passed** in 11.25s (after final edit).
- Verified `lint_melleafy_manifest` passes on all 4 bundled example packages under `examples/*/*_mellea`.
