# PR Draft: `fix(compile): resolve .claude and .pi assets from package instead of CWD (#128)`

- **Issue:** [generative-computing/mellea-skills-compiler#128](https://github.com/generative-computing/mellea-skills-compiler/issues/128)
- **Repository:** `generative-computing/mellea-skills-compiler`
- **Local Branch:** `fix/issue-128` (commit `f4d876d`, DCO signed off)

## Root Cause

Several compiler helpers resolved `.claude/` and `.pi/` assets relative to the caller's current working directory rather than from the installed package:

1. [`src/mellea_skills_compiler/compile/claude_directives.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/claude_directives.py) defined `_RUNTIME_DEFAULTS_PATH = Path(".claude/data/runtime_defaults.json")` relative to CWD and silently fell back to `_RUNTIME_DEFAULTS_FALLBACK` without logging when the file was not found. Compiling outside the repo root either read an unrelated CWD `.claude/data/runtime_defaults.json` or quietly used built-in defaults.
2. [`src/mellea_skills_compiler/compile/backends/pi.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/backends/pi.py) defined `_PI_PROMPTS_DIR = Path(".pi/prompts")` relative to CWD, passed `Path(".claude/schemas").resolve()` to `build_pi_system_prompt`, and rewrote `` `.claude/ `` references in `_render_prompt_templates` using `Path('.claude').resolve()`. Compiling outside the repo root silently read missing or foreign CWD templates and pointed `pi` at nonexistent CWD `.claude/` paths.

## Diff Summary

- **Updated** [`src/mellea_skills_compiler/compile/claude_directives.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/claude_directives.py):
  - Added package/repo root anchors (`_PACKAGE_DIR`, `_PACKAGE_ROOT`) and `resolve_package_path(relative_path)` which resolves assets (`.claude/...`, `.pi/...`) from the installed package rather than CWD, raising a clear `FileNotFoundError` when not found.
  - Anchored `_RUNTIME_DEFAULTS_PATH` to `_PACKAGE_ROOT / ".claude/data/runtime_defaults.json"` via `_resolve_runtime_defaults_path()`, and added a `LOGGER.warning(...)` when the resolved defaults file is missing before falling back to `_RUNTIME_DEFAULTS_FALLBACK`.
- **Updated** [`src/mellea_skills_compiler/compile/backends/pi.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/backends/pi.py):
  - Removed CWD-relative `_PI_PROMPTS_DIR = Path(".pi/prompts")`.
  - Updated `PiBackend.compile()` to pass `resolve_package_path(".claude/schemas").resolve()` to `build_pi_system_prompt()`.
  - Updated `_render_prompt_templates()` to resolve `.pi/prompts` and `.claude` via `resolve_package_path()` (with optional `prompts_src` / `claude_root` keyword overrides for isolated unit tests) and raise `FileNotFoundError` if either directory is missing or contains no `mellea-fy*.md` templates.
- **Updated** [`tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py) and [`tests/mellea_skills_compiler/compile/test_pi_backend.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_pi_backend.py):
  - Added `TestResolvePackagePath`, foreign-CWD regression tests (`test_resolves_package_defaults_from_foreign_cwd`, `test_compile_from_foreign_cwd_resolves_package_claude_and_pi_dirs`), missing-asset failure tests (`test_raises_clear_error_when_asset_missing`, `test_raises_when_prompts_dir_missing_or_empty`, `test_compile_fails_clearly_when_package_assets_missing`), and warning assertion when `runtime_defaults.json` is missing.
  - Updated `TestResolveRuntimeDefaults`, `TestRenderPromptTemplates`, and `test_compile_passes_absolute_paths_to_pi` to decouple unit tests from CWD-relative `.claude`/`.pi` resolution.

## Verification

| Check | Command | Result |
|---|---|---|
| Baseline test suite (before edits) | `.venv/bin/pytest tests/` | 639 passed in 11.44s |
| Reproducing TDD tests (before fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py tests/mellea_skills_compiler/compile/test_pi_backend.py -k "foreign_cwd"` | 2 failed (confirmed CWD `.claude`/`.pi` overrides package assets) |
| Full test suite (after final edit) | `.venv/bin/pytest tests/` | **645 passed** in 10.40s |
| Ruff check | `.venv/bin/ruff check .` | Not installed in `.venv` |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler
git push -u origin fix/issue-128
gh pr create --repo generative-computing/mellea-skills-compiler \
  --title "fix(compile): resolve .claude and .pi assets from package instead of CWD (#128)" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/generative-computing__mellea-skills-compiler__issue_128_PR_DRAFT.md
```
