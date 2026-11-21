# PR Draft: `fix(writers): safely render multi-line string literals in config and fixtures writers`

- **Issue:** [generative-computing/mellea-skills-compiler#118](https://github.com/generative-computing/mellea-skills-compiler/issues/118)
- **Repository:** `generative-computing/mellea-skills-compiler`
- **Local Branch:** `fix/issue-118` (commit `2cd1047`, DCO signed off)

## Root Cause

Both `src/mellea_skills_compiler/compile/writers/config_writer.py` (`_literal`, `_value_repr`) and `src/mellea_skills_compiler/compile/writers/fixtures_writer.py` (`_value_repr`) wrapped any multi-line string in `"""..."""` whenever `'"""' not in value`. This caused four classes of bugs:

1. **Trailing double quote (`"` / `""`):** produced `""""` at the end of the literal, raising `SyntaxError` (or implicitly concatenating `""`).
2. **Trailing backslash (`\`):** escaped the opening quote of the closing `"""`, raising `SyntaxError`.
3. **Embedded escape sequences (`\n`, `\f`, etc.):** interpreted backslashes inside `"""..."""`, silently corrupting string values (e.g., `'a\\nb\nc'` -> `'a\nb\nc'`, `'C:\\path\\file\nnext'` -> `'C:\\path\x0cile\nnext'`) or emitting Python `SyntaxWarning` on unrecognized escapes like `\p`.
4. **Carriage returns (`\r`) and NUL bytes (`\x00`):** normalized `\r` to `\n` or raised `SyntaxError`.

## Diff Summary

- **Added** [`src/mellea_skills_compiler/compile/writers/_literals.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/writers/_literals.py):
  - Extracted shared `render_literal(value)` and `_safe_triple_quoted(value)` helpers so `config_writer` and `fixtures_writer` use one canonical literal renderer.
  - Emits `"""..."""` only when `"\n" in value`, `"\\" not in value`, `'"""' not in value`, `not value.endswith('"')`, and `ast.literal_eval(candidate) == value` succeeds without `SyntaxError`/`ValueError`; otherwise falls back to `repr(value)`.
- **Updated** [`src/mellea_skills_compiler/compile/writers/config_writer.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/writers/config_writer.py):
  - Delegated `_literal(value)` and `_value_repr(value, py_type)` to `render_literal(value)`.
- **Updated** [`src/mellea_skills_compiler/compile/writers/fixtures_writer.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/writers/fixtures_writer.py):
  - Delegated `_value_repr(value)` to `render_literal(value)`.
- **Updated** [`tests/mellea_skills_compiler/compile/test_config_writer.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_config_writer.py) and **Added** [`tests/mellea_skills_compiler/compile/test_fixtures_writer.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_fixtures_writer.py):
  - Added parametrized round-trip tests covering trailing `"`, trailing `""`, trailing `\`, `'a\\nb\nc'`, `'C:\\path\\file\nnext'`, `'C:\\path\nnext'`, `'a\rb\nc'`, `'a\x00b\nc'`, and `'has """triple""" quotes\nand newline'`, verifying `ast.parse` and `exec` succeed with `SyntaxWarning` treated as an error and exact value equality preserved.

## Verification

| Check | Command | Result |
|---|---|---|
| Writer regression tests (before fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_config_writer.py tests/mellea_skills_compiler/compile/test_fixtures_writer.py -v` | 12 failed, 10 passed (reproduced `SyntaxError`, `SyntaxWarning`, and backslash/CR corruption) |
| Writer regression tests (after fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_config_writer.py tests/mellea_skills_compiler/compile/test_fixtures_writer.py -v` | **26 passed** |
| Full test suite | `.venv/bin/pytest tests/ -q` | **643 passed** in 8.80s |
| Import sorting | `.venv/bin/isort --check-only --diff src/mellea_skills_compiler/compile/writers/ tests/mellea_skills_compiler/compile/test_config_writer.py tests/mellea_skills_compiler/compile/test_fixtures_writer.py` | **Exited 0** (clean) |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler
git push -u origin fix/issue-118
gh pr create --repo generative-computing/mellea-skills-compiler \
  --title "fix(writers): safely render multi-line string literals in config and fixtures writers" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/generative-computing__mellea-skills-compiler__issue_118_PR_DRAFT.md
```
