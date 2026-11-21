# PR Draft: Pin consistency test doesn't cover the pi export target

- **Target Repo:** https://github.com/generative-computing/mellea-skills-compiler
- **Issue:** https://github.com/generative-computing/mellea-skills-compiler/issues/110
- **Local Repo:** `/home/runner/work/aiml-engineer/aiml-engineer/repos/mellea-skills-compiler`
- **Branch:** `fix/issue-110` (commit `2781836`)

## Summary
Added `src/mellea_skills_compiler/export/targets/pi.py` to the parametrized list in `test_export_targets_derive_pin_from_constant` and updated the comment from 'three' to 'four' export targets, per issue #110.

## Root Cause
The pin-consistency test's parametrized list covered only mcp.py, claude_code.py, and langgraph.py, leaving pi.py untested for hardcoded mellea pins despite it using MELLEA_PIN since #74.

## Verification Output
```
exit_code=0
============================= test session starts ==============================
platform linux -- Python 3.12.16, pytest-9.2.0, pluggy-1.7.0
rootdir: /home/runner/work/aiml-engineer/aiml-engineer/repos/mellea-skills-compiler
configfile: pyproject.toml
plugins: typeguard-4.6.1, anyio-4.16.0, platformdirs-4.14.8
collected 6 items

tests/mellea_skills_compiler/test_pin_consistency.py ......              [100%]

=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/_pytest/config/__init__.py:1636
  /home/runner/work/aiml-engineer/aiml-engineer/repos/mellea-skills-compiler/.venv/lib/python3.12/site-packages/_pytest/config/__init__.py:1636: PytestConfigWarning: Unknown config option: asyncio_mode
  
    self._warn_or_fail_if_strict(f"Unknown config option: {key}\n")

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
========================= 6 passed, 1 warning in 0.02s =========================
ruff not installed in .venv; skipped.
```

## Push & PR Commands (Review Before Submitting)
```bash
cd /home/runner/work/aiml-engineer/aiml-engineer/repos/mellea-skills-compiler
# Option A (GitHub CLI shortcut):
gh repo fork --remote=true --remote-name=fork
git push -u fork fix/issue-110
gh pr create --repo generative-computing/mellea-skills-compiler --title "Pin consistency test doesn't cover the pi export target" --body-file /home/runner/work/aiml-engineer/aiml-engineer/drafts/generative-computing__mellea-skills-compiler__issue_110_PR_DRAFT.md --draft
```
