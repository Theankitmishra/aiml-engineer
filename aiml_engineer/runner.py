"""Autonomous Red -> Green -> Review -> Distill runner for standalone execution."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_tools.discover_github import load_attempted_keys, search_candidate_issues
from aiml_engineer.config import AgentConfig
from aiml_engineer.providers import BaseProvider, build_provider, parse_action_response
from aiml_engineer.tools import TOOL_DESCRIPTIONS, RepoToolkit
from aiml_engineer.trajectory import TrajectoryRecord, TrajectoryStore

_SYSTEM_PROMPT = f"""You are an autonomous AI/ML Python software engineer.
Your job is to fix a GitHub issue cleanly, following strict engineering discipline:
1. Explore the codebase using list_files, read_file, and search_code.
2. Reproduce the reported issue FIRST by adding/running a minimal failing test before editing production code.
3. Implement the minimal, clean root-cause fix (never silence errors or relax assertions).
4. Re-run pytest and ruff to verify everything passes.
5. Inspect git_diff before calling finish.

{TOOL_DESCRIPTIONS}
"""

_REVIEW_PROMPT = """You are an adversarial senior code reviewer.
Inspect the issue description, git diff, and test output below.
Respond ONLY with JSON:
{
  "verdict": "approved" or "changes_requested",
  "critique": "concise explanation of any remaining bug, band-aid, missing edge case, or weak test"
}
Reject patches that swallow exceptions, weaken assertions, or miss the root cause.
"""


def _python_candidates() -> list[str]:
    """Return candidate Python executables, preferring 3.12/3.13/3.11 then sys.executable."""
    candidates: list[str] = []
    for name in ("python3.12", "python3.13", "python3.11"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    if sys.executable not in candidates:
        candidates.append(sys.executable)
    return candidates


def _trim_messages(
    messages: list[dict[str, str]],
    max_tail: int = 8,
    max_chars: int = 14000,
) -> list[dict[str, str]]:
    """Keep system + initial task + recent turns bounded by both count and character budget."""
    if len(messages) <= 2:
        return messages
    head = messages[:2]
    budget = max(max_chars - sum(len(m["content"]) for m in head), 2000)
    tail: list[dict[str, str]] = []
    used = 0
    for msg in reversed(messages[2:][-max_tail:]):
        cost = len(msg["content"])
        if tail and used + cost > budget:
            break
        tail.append(msg)
        used += cost
    tail.reverse()
    return [*head, *tail]


def _git_author_configured(repo_dir: Path) -> bool:
    name = subprocess.run(
        ["git", "config", "user.name"], check=False, cwd=repo_dir, capture_output=True, text=True
    )
    email = subprocess.run(
        ["git", "config", "user.email"], check=False, cwd=repo_dir, capture_output=True, text=True
    )
    return bool(name.stdout.strip() and email.stdout.strip())


def prepare_repo_workspace(
    clone_url: str,
    repo_dir: Path,
    branch_name: str,
    base_commit: str | None = None,
) -> str:
    """Clone or reset a repository, checkout a working branch, and create an isolated .venv."""
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        subprocess.run(["git", "clone", clone_url, str(repo_dir)], check=True)
    else:
        subprocess.run(["git", "fetch", "--all", "--prune"], cwd=repo_dir, check=True)
        subprocess.run(["git", "reset", "--hard"], cwd=repo_dir, check=True)
        subprocess.run(["git", "clean", "-fd", "-e", ".venv"], cwd=repo_dir, check=True)

    target_ref = base_commit or "HEAD"
    subprocess.run(
        ["git", "checkout", "-B", branch_name, target_ref],
        cwd=repo_dir,
        check=True,
    )
    resolved_base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
    ).strip()

    venv_dir = repo_dir / ".venv"
    if not (venv_dir / "bin" / "python").exists():
        created = False
        for py_exe in _python_candidates():
            proc = subprocess.run([py_exe, "-m", "venv", str(venv_dir)], check=False)
            if proc.returncode == 0 and (venv_dir / "bin" / "pip").exists():
                created = True
                break
            shutil.rmtree(venv_dir, ignore_errors=True)
        if not created:
            raise RuntimeError(f"Failed to create .venv using any Python candidate: {_python_candidates()}")
        pip = str(venv_dir / "bin" / "pip")
        # Try editable install with common test extras, falling back cleanly
        for spec in (".[dev]", ".[test]", "."):
            proc = subprocess.run([pip, "install", "-e", spec], check=False, cwd=repo_dir)
            if proc.returncode == 0:
                break
        if not (venv_dir / "bin" / "pytest").exists():
            subprocess.run([pip, "install", "pytest"], cwd=repo_dir, check=False)

    return resolved_base


def _format_few_shot_examples(examples: list[TrajectoryRecord]) -> str:
    if not examples:
        return ""
    blocks = ["## Reference Solved Examples (from Teacher/Past Runs)"]
    for idx, ex in enumerate(examples, 1):
        blocks.append(
            f"### Example {idx}: {ex.issue_key} — {ex.issue_title}\n"
            f"- Root cause: {ex.messages[-1]['content'][:400] if ex.messages else ''}\n"
            f"- Lessons: {'; '.join(ex.lessons_learned[:3])}"
        )
    return "\n\n".join(blocks)


def _commit_changes(repo_dir: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    cmd = ["git"]
    if not _git_author_configured(repo_dir):
        cmd.extend(["-c", "user.name=AIML Engineer Draft", "-c", "user.email=draft@local"])
    cmd.extend(["commit", "-m", message])
    subprocess.run(cmd, cwd=repo_dir, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()


def _write_pr_draft(
    drafts_dir: Path,
    issue: dict[str, Any],
    repo_dir: Path,
    branch_name: str,
    fix_commit: str,
    summary: str,
    root_cause: str,
    test_output: str,
) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    safe_repo = issue["repo"].replace("/", "__")
    draft_path = drafts_dir / f"{safe_repo}__issue_{issue['issue_number']}_PR_DRAFT.md"
    content = (
        f"# PR Draft: {issue['issue_title']}\n\n"
        f"- **Target Repo:** {issue['repo_url']}\n"
        f"- **Issue:** {issue['issue_url']}\n"
        f"- **Local Repo:** `{repo_dir}`\n"
        f"- **Branch:** `{branch_name}` (commit `{fix_commit[:7]}`)\n\n"
        f"## Summary\n{summary}\n\n"
        f"## Root Cause\n{root_cause}\n\n"
        f"## Verification Output\n```\n{test_output[:2000]}\n```\n\n"
        f"## Push & PR Commands (Review Before Submitting)\n"
        f"```bash\n"
        f"cd {repo_dir}\n"
        f"# Option A (GitHub CLI shortcut):\n"
        f"gh repo fork --remote=true --remote-name=fork\n"
        f"git push -u fork {branch_name}\n"
        f'gh pr create --repo {issue["repo"]} --title "{issue["issue_title"]}" --body-file {draft_path} --draft\n'
        f"```\n"
    )
    draft_path.write_text(content, encoding="utf-8")
    return draft_path


def _append_playbook_lessons(playbook_path: Path, issue_key: str, lessons: list[str]) -> None:
    if not lessons or not playbook_path.exists():
        return
    text = playbook_path.read_text(encoding="utf-8")
    marker = "## 3. Run-End Retrospective Checklist"
    new_bullets = "\n".join(f"- **[{issue_key}]** {lesson}" for lesson in lessons[:3])
    if marker in text:
        updated = text.replace(marker, f"{new_bullets}\n\n{marker}", 1)
    else:
        updated = text.rstrip() + "\n" + new_bullets + "\n"
    playbook_path.write_text(updated, encoding="utf-8")


def run_issue_loop(
    config: AgentConfig,
    issue: dict[str, Any],
    repo_dir: Path,
    provider: BaseProvider | None = None,
    base_commit: str | None = None,
    skip_repo_setup: bool = False,
) -> TrajectoryRecord:
    """Execute the Red -> Green -> Adversarial Review -> Distill loop on a repo."""
    llm = provider or build_provider(config)
    store = TrajectoryStore(config.trajectories_dir)
    branch_name = f"fix/issue-{issue['issue_number']}"

    if skip_repo_setup:
        resolved_base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
        ).strip()
    else:
        resolved_base = prepare_repo_workspace(
            clone_url=issue["clone_url"],
            repo_dir=repo_dir,
            branch_name=branch_name,
            base_commit=base_commit,
        )

    toolkit = RepoToolkit(repo_dir)
    playbook_text = (
        config.playbook_path.read_text(encoding="utf-8")[:1200]
        if config.playbook_path.exists()
        else ""
    )
    examples = store.retrieve_similar(
        f"{issue['repo']} {issue['issue_title']} {issue.get('body_excerpt', '')}",
        k=config.top_k_examples,
    )
    user_prompt = (
        f"Repository: {issue['repo']}\n"
        f"Issue #{issue['issue_number']}: {issue['issue_title']}\n"
        f"URL: {issue['issue_url']}\n\n"
        f"Issue Details:\n{issue.get('body_excerpt', '')}\n\n"
        f"Playbook Excerpt:\n{playbook_text}\n\n"
        f"{_format_few_shot_examples(examples)}"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    repro_failed_before_fix = False
    verification_passed = False
    review_verdict = "pending"
    rejected_attempts: list[dict[str, Any]] = []
    review_rounds = 0
    finish_payload: dict[str, Any] = {}
    last_test_output = ""

    for _step in range(config.max_steps):
        trimmed = _trim_messages(messages)
        try:
            raw_reply = llm.complete(trimmed)
        except RuntimeError:
            if provider is not None:
                raise
            # Dynamically fail over to any other configured cloud provider
            raw_reply = ""
            for alt_name in ("groq", "huggingface", "openai", "gemini"):
                if alt_name == llm.config.provider:
                    continue
                alt_cfg = AgentConfig(
                    workspace_dir=config.workspace_dir,
                    provider=alt_name,
                    github_token=config.github_token,
                    temperature=config.temperature,
                )
                if not alt_cfg.api_key:
                    continue
                try:
                    alt_llm = build_provider(alt_cfg)
                    raw_reply = alt_llm.complete(trimmed)
                    llm = alt_llm
                    break
                except RuntimeError:
                    continue
            if not raw_reply:
                raise
        messages.append({"role": "assistant", "content": raw_reply})

        try:
            action = parse_action_response(raw_reply)
        except (ValueError, TypeError) as exc:
            messages.append({"role": "user", "content": f"Invalid action format: {exc}"})
            continue

        result = toolkit.execute(action.tool, action.args)
        if action.tool == "run_tests" and not result.ok:
            repro_failed_before_fix = True

        if not result.finished:
            messages.append({"role": "user", "content": f"Tool result ({action.tool}): {result.output[:2500]}"})
            continue

        # Independent verification gate when the agent calls finish
        finish_payload = result.payload or {}
        diff_res = toolkit.git_diff()
        if not diff_res.output or diff_res.output == "(no diff)":
            feedback = "Cannot finish with an empty git diff. Implement a tested fix first."
            rejected_attempts.append(
                {
                    "prompt_messages": list(messages[:-1]),
                    "rejected_response": raw_reply,
                    "chosen_response": "",
                    "failure_reason": feedback,
                }
            )
            messages.append({"role": "user", "content": feedback})
            continue

        test_res = toolkit.run_tests(str(finish_payload.get("verification_command", "")))
        lint_res = toolkit.run_lint()
        last_test_output = f"{test_res.output}\n{lint_res.output}".strip()

        if not (test_res.ok and lint_res.ok):
            feedback = f"Verification failed:\n{last_test_output}"
            rejected_attempts.append(
                {
                    "prompt_messages": list(messages[:-1]),
                    "rejected_response": raw_reply,
                    "chosen_response": "",
                    "failure_reason": "test_or_lint_failure",
                }
            )
            messages.append({"role": "user", "content": feedback})
            continue

        # Independent adversarial diff review
        review_messages = [
            {"role": "system", "content": _REVIEW_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Issue: {issue['issue_title']}\n{issue.get('body_excerpt', '')[:1200]}\n\n"
                    f"Diff:\n{diff_res.output[:3500]}\n\n"
                    f"Verification:\n{last_test_output[:1500]}"
                ),
            },
        ]
        try:
            review_raw = llm.complete(review_messages)
            review_obj = json.loads(
                re.sub(r"^```(?:json)?|```$", "", review_raw.strip(), flags=re.MULTILINE)
            )
            verdict = str(review_obj.get("verdict", "approved"))
            critique = str(review_obj.get("critique", ""))
        except (ValueError, json.JSONDecodeError, RuntimeError):
            verdict = "approved"
            critique = ""

        if verdict != "approved" and review_rounds < config.max_review_rounds:
            review_rounds += 1
            feedback = f"Adversarial review requested changes: {critique}"
            rejected_attempts.append(
                {
                    "prompt_messages": list(messages[:-1]),
                    "rejected_response": raw_reply,
                    "chosen_response": "",
                    "failure_reason": f"review_rejected: {critique}",
                }
            )
            messages.append({"role": "user", "content": feedback})
            continue

        verification_passed = True
        review_verdict = "approved"
        # Fill chosen_response on prior rejected DPO attempts with the passing finish turn
        for pair in rejected_attempts:
            pair["chosen_response"] = raw_reply
        break

    patch_diff = toolkit.git_diff().output
    fix_commit = resolved_base
    lessons = list(finish_payload.get("lessons_learned") or [])
    if verification_passed and patch_diff and patch_diff != "(no diff)" and not config.dry_run:
        fix_commit = _commit_changes(
            repo_dir,
            f"Fix {issue['repo']}#{issue['issue_number']}: {issue['issue_title'][:60]}",
        )
        _write_pr_draft(
            drafts_dir=config.drafts_dir,
            issue=issue,
            repo_dir=repo_dir,
            branch_name=branch_name,
            fix_commit=fix_commit,
            summary=str(finish_payload.get("summary", "")),
            root_cause=str(finish_payload.get("root_cause", "")),
            test_output=last_test_output,
        )

    record = TrajectoryRecord(
        issue_key=issue["issue_key"],
        repo=issue["repo"],
        issue_number=int(issue["issue_number"]),
        issue_title=issue["issue_title"],
        issue_url=issue["issue_url"],
        issue_body=issue.get("body_excerpt", ""),
        source="standalone",
        provider=config.provider,
        model=config.model,
        base_commit=resolved_base,
        fix_commit=fix_commit,
        repro_failed_before_fix=repro_failed_before_fix,
        verification_passed=verification_passed,
        review_verdict=review_verdict,
        patch_diff=patch_diff,
        messages=messages,
        rejected_attempts=[p for p in rejected_attempts if p.get("chosen_response")],
        lessons_learned=lessons,
    )
    if not config.dry_run:
        store.save(record)
        log_entry = {
            "date": datetime.now(timezone.utc).isoformat(),
            "issue_key": record.issue_key,
            "issue_url": record.issue_url,
            "status": "pr_draft_ready" if verification_passed else "failed",
            "root_cause": str(finish_payload.get("root_cause", "")),
            "verification_passed": verification_passed,
            "lessons_learned": lessons,
        }
        with config.runs_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_entry) + "\n")
        if lessons:
            _append_playbook_lessons(config.playbook_path, record.issue_key, lessons)

    return record


def discover_and_run_one(config: AgentConfig) -> TrajectoryRecord | None:
    """Discover the top unattempted AI/ML Python issue and solve it."""
    attempted = load_attempted_keys(config.runs_log_path)
    candidates, _warning = search_candidate_issues(
        token=config.github_token,
        attempted=attempted,
        min_stars=config.min_stars,
        max_stars=config.max_stars,
        active_days=config.active_days,
        limit=5,
    )
    if not candidates:
        return None
    issue = candidates[0]
    repo_name = issue["repo"].split("/")[-1]
    return run_issue_loop(
        config=config,
        issue=issue,
        repo_dir=config.repos_dir / repo_name,
    )
