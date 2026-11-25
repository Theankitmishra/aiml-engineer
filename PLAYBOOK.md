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

5. **Automated Fork, Push & PR Submission:**
   - Commit cleanly on a local branch (`fix/issue-<num>` or `feat/issue-<num>`).
   - Write a complete `drafts/<owner>__<repo>__issue_<num>_PR_DRAFT.md` with issue link, root cause, diff summary, and test commands/outputs.
   - Push the branch to your fork (`gh repo fork --remote=true --remote-name=fork && git push -u fork <branch>`) and open the PR (`gh pr create --repo <owner/repo> --head Theankitmishra:<branch> --title "..." --body-file ...`).
   - At the end of each run, commit and push updated tracking artifacts (`drafts/`, `PLAYBOOK.md`, `runs_log.jsonl`, `trajectories/`, `eval/`, `training_data/`) to `origin/main` (`Theankitmishra/aiml-engineer`).

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
- **Python 3.15 Native Build Fallbacks (`google-re2`):** If `pip install -e ".[dev]"` fails solely on a native extension lacking a Python 3.15 wheel (e.g. `google-re2` when `regex` is the documented fallback), install the repo with `--no-deps -e .` and install all remaining dependencies via `tomllib`.
- **Contract Test Isolation & DCO Sign-Off:** Extend stub tool registries locally with `mock.patch.object(ac, "TOOLS", ...)` instead of mutating shared contract fixtures, and check `CONTRIBUTING.md` for DCO requirements (`git commit -s`).
- **Triple-Quoted Code Generation Guards:** Never rely on `ast.literal_eval(f'"""{value}"""') == value` alone when emitting Python source literals: invalid escapes like `\p` still satisfy equality while emitting `SyntaxWarning` (and future `SyntaxError`). Reject `"\\" in value`, `'"""' in value`, and `value.endswith('"')` before checking `ast.literal_eval` round-tripping, and test under `warnings.simplefilter("error", SyntaxWarning)`.
- **GitHub Discovery & Test Path Triage:** Pass `GITHUB_TOKEN=$(gh auth token)` to `discover_github.py` when `gh` is authenticated; if `/search/issues` hits secondary rate limits, inspect bug issues directly in the discovered active repos via `gh issue list`. Check `README.md` for the intended `pytest` path (e.g. `pytest tests/`) when repo-root discovery collects unconfigured `examples/` fixtures.
- **Python Package Name Derivation (`str.isidentifier()`):** When deriving Python package names from skill slugs or directory names, `.strip("_")` can expose leading digits (e.g. `_1password_` -> `1password`) and non-hyphen punctuation (e.g. `.`) breaks `import`. Normalise characters failing `ch == "_" or f"_{ch}".isidentifier()` to `_` before `.strip("_")`, and prefix `_` whenever `not pkg.isidentifier()`.
- **Scoped Repo Greps:** Exclude `.venv`, `.git`, and `__pycache__` when running `grep -rn` from a repo root so searches stay fast and avoid virtualenv noise.
- **GitHub Issue Triage Beyond `/search/issues`:** Always check `gh pr list --search "<num> OR <symbol>" --state all` in addition to `/issues/{num}/timeline`, since contributors sometimes open PRs without linking the issue number in the body.
- **Lint Scope vs. Mirrored Companion Assets:** When a static lint checks generated package code (e.g. `lint_bundled_asset_path_resolution`), explicitly check whether helper skip predicates (`_is_skipped_path`) are shared with other lints (`lint_import_soundness`) and gate companion-folder exclusion (`scripts/`, `references/`, `assets/`) with a keyword flag so unrelated lints keep their exact scope.
- **[generative-computing/mellea-skills-compiler#110]** When adding a new export target that uses a shared constant, immediately add it to any consistency tests that enumerate targets to prevent silent coverage gaps.
- **Matching `FileNotFoundError` / `NotADirectoryError` Against Fixture Inputs (`smoke_check.py`):** Check both `exc.filename` / `exc.filename2` (normalized via `.resolve(strict=False)` relative to `run_dir`, plus `is_relative_to`) and bounded regex matches on `str(exc)` because manual `FileNotFoundError(f"...")` raises leave `exc.filename` as `None`.
- **Preventing False Skips on Existing Input Directories:** Verify the candidate input path does not exist on disk (or is not a directory for `NotADirectoryError`) before matching, so an internal missing package file inside an existing `work_dir` still fails the smoke check instead of silently skipping.
- **Cross-Provider Message Formatting & Unset `model_provider` (`langchain-google-vertexai`):** Before porting `model_provider != "anthropic"` content-block filters from `langchain-anthropic`, check whether the target chat model stamps `response_metadata["model_provider"]`. Because `ChatAnthropicVertex` leaves `model_provider` unset, filter `function_call` when `model_provider != "anthropic"`, but filter `reasoning` only when `not isinstance(message, AIMessage) or model_provider not in (None, "anthropic")` so its own v1 `reasoning` → `thinking` conversion keeps working.
- **Monorepo Subpackage Tooling (`libs/<pkg>/pyproject.toml`):** In monorepos with per-package `pyproject.toml`, install `libs/<pkg>` editable with its extras into a repo-root `.venv`, and invoke `pytest` from `libs/<pkg>` and `ruff check/format --config libs/<pkg>/pyproject.toml` so subpackage pytest markers, warning filters, and ruff rules are respected.
- **Unmerged-PR & Maintainer-Status Triage:** Before implementing a discovered issue, check open PRs and recent issue comments to skip issues whose target code lives only in unmerged stacked PRs (e.g. `EvoScientist#899`/`#900`) or whose maintainers explicitly deferred the work (`PCNtoolkit#652`).
- **Package-Anchored Asset Resolution (`__file__`) & CWD-Decoupled Tests:** Resolve compiler assets (`.claude/`, `.pi/`) from package/repo anchors (`Path(__file__).resolve().parents[...]`) and fail with `FileNotFoundError` (or log a `WARNING` on documented fallbacks) rather than silently reading CWD; update existing tests that previously relied on `monkeypatch.chdir(tmp_path)` to override module constants or pass explicit source paths.

---

## 3. Run-End Retrospective Checklist (Loop Update)

After each run, append one JSON line to `runs_log.jsonl` and add **1–3 concrete, reusable lessons** to Section 2 above if you discovered:
- A faster way to set up or test a Python AI/ML repo's `.venv`,
- A subtle bug pattern or false-positive fix caught during self-review,
- A reason a candidate issue turned out unfeasible so `discover_github.py` or future triage avoids it.
