# PR Draft: `fix(compile): ensure derived package name is a valid Python identifier (#120)`

- **Issue:** [generative-computing/mellea-skills-compiler#120](https://github.com/generative-computing/mellea-skills-compiler/issues/120)
- **Repository:** `generative-computing/mellea-skills-compiler`
- **Local Branch:** `fix/issue-120` (commit `d363900`, DCO signed off)

## Root Cause

`_derive_mellea_package_name` in [`src/mellea_skills_compiler/compile/compiler.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/compiler.py#L120-L141) previously only lowercased the raw skill/directory name, replaced `-` and spaces with `_`, collapsed `__`, and stripped leading/trailing underscores before appending `_mellea`. For a skill named `1password` (or any name starting with a digit, e.g. `2fa-helper`, `_1password_`, or YAML integer `123`), this produced `1password_mellea`, which is not a valid Python identifier. As a result, `lint_parseable` (`python -c "import 1password_mellea.pipeline"`) failed the compile with `SyntaxError: invalid decimal literal`.

## Diff Summary

- **Updated** [`src/mellea_skills_compiler/compile/compiler.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/src/mellea_skills_compiler/compile/compiler.py#L120-L141):
  - Normalised characters that are not valid Python identifier-continue characters to `_`, collapsed repeated underscores, and stripped leading/trailing underscores with `"skill"` fallback.
  - Checked `package_name.isidentifier()` on `f"{name}_mellea"` and prefixed `"_"` when invalid (such as names starting with a digit), turning `1password` into `_1password_mellea`.
- **Updated** [`tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler/tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py#L81-L115):
  - Added parametrized regression tests covering digit-prefixed names (`"1password"`, `"2fa-helper"`, `"_1password_"`, `123`, `"1.0"`), dot/punctuation normalisation (`"v1.0-skill"`, `"---"`), and an end-to-end check pairing directory/spec fallback with `lint_parseable`.

## Verification

| Check | Command | Result |
|---|---|---|
| Helper regression tests (before fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py -k "digit" -v` | 5 failed (reproduced `1password_mellea` instead of `_1password_mellea`) |
| Helper test file (after fix) | `.venv/bin/pytest tests/mellea_skills_compiler/compile/test_mellea_skills_helpers.py -v` | **48 passed** in 2.85s |
| Full test suite (after fix) | `.venv/bin/pytest tests/` | **632 passed** in 9.35s |
| Linter check | `ruff` | Not installed in `.venv` |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/mellea-skills-compiler
git push -u origin fix/issue-120
gh pr create --repo generative-computing/mellea-skills-compiler \
  --title "fix(compile): ensure derived package name is a valid Python identifier (#120)" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/generative-computing__mellea-skills-compiler__issue_120_PR_DRAFT.md
```
