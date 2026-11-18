# AI/ML Python Engineering Playbook (Self-Improving Loop)

This file is read at the **start** of every automated run and updated at the **end** of every run so the agent learns from successes, failures, and code-review findings.

---

## 1. Non-Negotiable Engineering Standards

1. **Feasibility Check Before Coding:**
   - Skip issues that require unavailable hardware (CUDA/ROCm GPUs, H100/A100, TPUs, multi-node clusters) or secret API keys that cannot be mocked cleanly in unit tests.
   - Prefer issues with a clear minimal reproduction, `good first issue`, `help wanted`, or pure-Python `bug` reports.

2. **Reproduce First (Red -> Green):**
   - Never start by editing production code.
   - Set up an isolated `.venv` inside the cloned repo, run existing relevant tests to verify the baseline, and write a **minimal failing test** that reproduces the exact reported bug.
   - Verify the test fails for the **right reason** (matching the issue's traceback/symptom).

3. **Fix the Root Cause the Right Way:**
   - Trace the true cause in the codebase rather than wrapping errors in broad `try/except`, adding special-case band-aids, or loosening assertions.
   - Respect the repository's existing abstractions, typing, logging, and error-handling conventions.

4. **Full Verification & Independent Diff Review:**
   - Re-run the new regression test AND relevant existing test suites.
   - Run repo linters/type-checkers (`ruff`, `mypy`, `flake8`) if installed in the repo's environment or dev dependencies.
   - Perform an adversarial self-review of `git diff`:
     - Does this actually fix the root cause for all callers and edge cases?
     - Are there any unintended side effects, duplicated logic, or leftover debug files?
     - Are test assertions strict and meaningful (no tautologies or over-mocking)?

5. **Review-Before-Push PR Safety:**
   - Never push or open a PR on someone else's repository automatically.
   - Commit cleanly on a local branch (`fix/issue-<num>-<slug>` or `feat/issue-<num>-<slug>`).
   - Write a complete `drafts/<repo>__issue_<num>_PR_DRAFT.md` with issue link, root cause, diff summary, test commands/outputs, and push instructions for human approval.

---

## 2. Learned AI/ML Python Patterns & Pitfalls

- **Async & Streaming Frameworks (`langgraph`, `langchain`, etc.):** Check task cancellation, cleanup in `finally` blocks, and exception propagation when callbacks/transformers fail mid-stream.
- **Hardware-Specific Issues (`verl`, `vllm`, `sglang`, CUDA/ROCm):** Avoid picking issues that require physical GPUs to verify; look for pure-Python orchestration, config validation, tokenizer, or CPU-testable logic if targeting those repos.
- **Reproducibility:** Seed RNGs in tests (`random`, `numpy`, `torch.manual_seed`) and avoid downloading large HuggingFace weights in unit tests (use tiny synthetic fixtures/tensors instead).
- **Pandas Nullable Dtypes (`BooleanDtype`, `Int64Dtype`):** `pd.api.types.is_numeric_dtype()` returns `True` for `BooleanDtype` and `bool`. Always pair it with `and not pd.api.types.is_bool_dtype(series)` when routing columns to numeric statistics (median/mean/outliers) vs. categorical statistics (mode).
- **GitHub Discovery & Triage:** Keep `/search/issues` queries to at most 4 `OR` operators (GitHub's 5-operator cap), check `/issues/{num}/timeline` for existing linked PRs before cloning, and minimize `/repos/` calls when `GITHUB_TOKEN` is unset (60 req/hr unauthenticated limit).
- **Local Git Commits:** Always check if `git config user.name` / `user.email` is set; if unset, commit with `-c user.name=... -c user.email=...` and note `git commit --amend --reset-author` in the PR draft so the user can attach their real GitHub identity before pushing.
- **Rich CLI Table Tests:** Adding a column to a `rich.table.Table` shrinks other columns under `CliRunner`'s 80-column default and can trigger cell truncation (`…`). Run all related CLI tests and pass `env={"COLUMNS": "200"}` when asserting full cell text.
- **SQLite Newest-Row Tie-Breaking:** Never rely on `ORDER BY started_at DESC LIMIT 1` alone; add `, id DESC` so back-to-back inserts within the same timestamp deterministically return the newest row, and test by patching `_now()`.
- **Resilient HTML Test Assertions:** Prefer targeted badge/element assertions over blanket negative substring checks (`"moderate" not in html`), and strip `<details class="info-tip...">...</details>` before matching stat-card labels.

---

## 3. Run-End Retrospective Checklist (Loop Update)

After each run, append one JSON line to `runs_log.jsonl` and add **1–3 concrete, reusable lessons** to Section 2 above if you discovered:
- A faster way to set up or test a Python AI/ML repo's `.venv`,
- A subtle bug pattern or false-positive fix caught during self-review,
- A reason a candidate issue turned out unfeasible so `discover_github.py` or future triage avoids it.
