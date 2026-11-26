# Fix exporters using generic descriptions and dropping optional `run.sh` arguments

Fixes https://github.com/generative-computing/mellea-skills-compiler/issues/126

## Root Cause

1. **Generic / missing skill description:** `claude_code.py`, `pi.py`, and `mcp.py` each duplicated a private `_get_description(manifest)` helper that looked up `manifest["runtime_metadata"]["c1_identity"]["entries"][0]["description"]` or `manifest["categories_resolved"]["c1_identity"]["entries"][0]["description"]`. Modern `melleafy.json` manifests store `categories_resolved` as a list of category codes (e.g. `["C1", "C2", "C6", "C8"]`) rather than a nested dict with `c1_identity`, so all three targets always fell back to generic defaults (`"A Mellea pipeline skill."` / `"Invokes the <package> Mellea pipeline."`) instead of reading the bundled `SKILL.md` frontmatter description. Additionally, `claude_code.py` and `pi.py` interpolated `description` unquoted into YAML frontmatter, which breaks YAML parsing when descriptions contain `: `, `#`, or quotes, and `mcp.py` interpolated `description` unescaped into a `"""..."""` docstring.
2. **Dropped / misordered `run.sh` arguments:** `_run_sh_synchronous_oneshot` and `_run_sh_streaming` (duplicated across `claude_code.py` and `pi.py`) bypassed the bundled package's `__main__` CLI and called `run_pipeline(...)` directly with positional `sys.argv[2..]` slices (`_invocation_args` / `_streaming_call`), dropping optional `--flag` arguments and breaking whenever flags or optional parameters were passed out of positional order.

## Diff Summary

- **`src/mellea_skills_compiler/export/exporter.py`**
  - Added shared `get_skill_description(loaded=..., *, manifest=..., default=...)` and `_extract_description_from_skill_md(...)` helpers that read the `description` from the bundled `SKILL.md` frontmatter (`loaded.python_package_dir / "SKILL.md"`, falling back to `loaded.package_source_dir / "SKILL.md"`), then legacy manifest `c1_identity` entries, then `default`.
  - Updated `_build_export_notes` next-steps text for `claude-code` and `pi` to reflect `python -m <package>` delegation.
- **`src/mellea_skills_compiler/toolkit/file_utils.py`**
  - Updated `parse_spec_file` frontmatter regex to also match frontmatter-only `SKILL.md` files without a trailing markdown body.
- **`src/mellea_skills_compiler/export/targets/_shell.py`**
  - Extracted shared `run.sh` rendering and Guardian inline snippet helpers used by both `claude_code.py` and `pi.py`.
  - Updated `synchronous_oneshot` and `streaming` `run.sh` scripts to delegate to `exec python [-u] -m <package> "$@"` (and, when `policy_manifest.json` is bundled, run `runpy.run_module('<package>', run_name='__main__', alter_sys=True)` with `sys.argv = ['<package>', *sys.argv[2:]]` inside Guardian's `try/finally` lifecycle).
- **`src/mellea_skills_compiler/export/targets/claude_code.py` & `pi.py`**
  - Replaced duplicated `_get_description` and `run.sh` helpers with imports from `exporter.py` and `_shell.py`.
  - Serialized frontmatter `description` with `json.dumps(description)` so descriptions containing `: `, `#`, or quotes produce valid YAML.
- **`src/mellea_skills_compiler/export/targets/mcp.py`**
  - Replaced local `_get_description` with `get_skill_description(loaded, default=...)` and escaped `description` safely inside the `@mcp.tool()` docstring.
- **Tests**
  - Updated `tests/mellea_skills_compiler/export/test_export_claude_code.py` and `tests/mellea_skills_compiler/export/test_pi_target.py` for `python -m <package> "$@"` delegation.
  - Added `tests/mellea_skills_compiler/export/test_export_shared_description_and_run_sh.py` covering `claude-code`, `pi`, and `mcp` `SKILL.md` description resolution, fallback precedence, special-character YAML/AST safety, and end-to-end `bash scripts/run.sh` execution with optional flags (both with and without the Guardian `runpy` wrapper).

## Verification

- `.venv/bin/pytest tests/ -q` → `660 passed in 12.02s`
