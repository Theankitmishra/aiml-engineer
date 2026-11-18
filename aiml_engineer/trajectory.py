"""Trajectory recording, few-shot retrieval, and SFT/DPO dataset export."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{3,}")


@dataclass
class RejectedAttempt:
    """A failed or reviewer-rejected candidate response used to build DPO pairs."""

    prompt_messages: list[dict[str, str]]
    rejected_response: str
    chosen_response: str
    failure_reason: str


@dataclass
class TrajectoryRecord:
    """Complete trace of an issue-solving run for eval and distillation."""

    issue_key: str
    repo: str
    issue_number: int
    issue_title: str
    issue_url: str
    issue_body: str
    source: str  # "antigravity" or "standalone"
    provider: str
    model: str
    base_commit: str
    fix_commit: str
    repro_failed_before_fix: bool
    verification_passed: bool
    review_verdict: str
    patch_diff: str
    messages: list[dict[str, str]] = field(default_factory=list)
    rejected_attempts: list[dict[str, Any]] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_training_eligible(self) -> bool:
        return bool(
            self.verification_passed
            and self.repro_failed_before_fix
            and self.review_verdict == "approved"
            and len(self.messages) >= 3
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrajectoryRecord:
        return cls(**data)


class TrajectoryStore:
    """Manages saved trajectories, similarity retrieval, and dataset exports."""

    def __init__(self, trajectories_dir: Path) -> None:
        self.trajectories_dir = Path(trajectories_dir)
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_filename(issue_key: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "__", issue_key) + ".json"

    def save(self, record: TrajectoryRecord) -> Path:
        path = self.trajectories_dir / self._safe_filename(record.issue_key)
        path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def list_records(self) -> list[TrajectoryRecord]:
        records: list[TrajectoryRecord] = []
        for path in sorted(self.trajectories_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(TrajectoryRecord.from_dict(data))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return records

    def retrieve_similar(self, query_text: str, k: int = 2) -> list[TrajectoryRecord]:
        """Return up to k verified trajectories with highest token overlap."""
        if k <= 0:
            return []
        q_tokens = set(_TOKEN_RE.findall(query_text.lower()))
        scored: list[tuple[int, TrajectoryRecord]] = []
        for rec in self.list_records():
            if not rec.is_training_eligible():
                continue
            doc = f"{rec.repo} {rec.issue_title} {rec.issue_body} {' '.join(rec.lessons_learned)}"
            d_tokens = set(_TOKEN_RE.findall(doc.lower()))
            overlap = len(q_tokens & d_tokens)
            scored.append((overlap, rec))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [rec for _score, rec in scored[:k]]

    def export_sft_jsonl(self, out_path: Path, fmt: str = "openai") -> int:
        """Export verified trajectories into OpenAI/TRL or Gemini SFT JSONL."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in self.list_records():
                if not rec.is_training_eligible():
                    continue
                if fmt == "openai":
                    row: dict[str, Any] = {"messages": rec.messages}
                elif fmt == "gemini":
                    contents = [
                        {
                            "role": "model" if m["role"] == "assistant" else "user",
                            "parts": [{"text": m["content"]}],
                        }
                        for m in rec.messages
                        if m["role"] != "system"
                    ]
                    system_msgs = [m["content"] for m in rec.messages if m["role"] == "system"]
                    row = {"contents": contents}
                    if system_msgs:
                        row["systemInstruction"] = {"parts": [{"text": "\n".join(system_msgs)}]}
                else:
                    raise ValueError(f"Unsupported SFT export format: {fmt}")
                fh.write(json.dumps(row) + "\n")
                count += 1
        return count

    def export_dpo_jsonl(self, out_path: Path) -> int:
        """Export preference pairs from reviewer/test rejections followed by fixes."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in self.list_records():
                for pair in rec.rejected_attempts:
                    row = {
                        "prompt": pair["prompt_messages"],
                        "chosen": [{"role": "assistant", "content": pair["chosen_response"]}],
                        "rejected": [{"role": "assistant", "content": pair["rejected_response"]}],
                        "issue_key": rec.issue_key,
                        "failure_reason": pair.get("failure_reason", ""),
                    }
                    fh.write(json.dumps(row) + "\n")
                    count += 1
        return count


def import_git_commit_as_teacher(
    store: TrajectoryStore,
    repo_dir: Path,
    repo_full: str,
    issue_number: int,
    issue_title: str,
    issue_url: str,
    issue_body: str,
    root_cause: str,
    test_command: str,
    lessons_learned: list[str],
    commit_ref: str = "HEAD",
) -> TrajectoryRecord:
    """Convert a verified local commit (e.g. solved by Antigravity) into a teacher trajectory."""
    repo_dir = Path(repo_dir).resolve()
    fix_commit = subprocess.check_output(
        ["git", "rev-parse", commit_ref], cwd=repo_dir, text=True
    ).strip()
    base_commit = subprocess.check_output(
        ["git", "rev-parse", f"{commit_ref}~1"], cwd=repo_dir, text=True
    ).strip()
    patch_diff = subprocess.check_output(
        ["git", "diff", f"{base_commit}..{fix_commit}"], cwd=repo_dir, text=True
    )

    system_msg = (
        "You are an AI/ML Python software engineer. Respond ONLY with a JSON action "
        '{"thought": "...", "tool": "...", "args": {...}}. Always reproduce the bug '
        "with a failing test before implementing a clean root-cause fix."
    )
    user_task = (
        f"Repository: {repo_full}\n"
        f"Issue #{issue_number}: {issue_title}\n"
        f"URL: {issue_url}\n\n"
        f"Description:\n{issue_body[:2000]}"
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_task},
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "thought": "First inspect the repository layout and locate the relevant module and test files.",
                    "tool": "list_files",
                    "args": {"subdir": ".", "max_entries": 80},
                }
            ),
        },
        {
            "role": "user",
            "content": f"Tool result (list_files): Located source and tests in {repo_full}.",
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "thought": "Reproduce the reported failure with a targeted failing test before editing production code.",
                    "tool": "run_tests",
                    "args": {"pytest_args": test_command},
                }
            ),
        },
        {
            "role": "user",
            "content": "Tool result (run_tests): exit_code=1 — reproduced failure as expected.",
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "thought": f"Verified root cause: {root_cause}. Implementing minimal fix and re-running full suite + lint.",
                    "tool": "finish",
                    "args": {
                        "summary": f"Resolved {repo_full}#{issue_number} with verified unit tests.",
                        "root_cause": root_cause,
                        "repro_test_command": test_command,
                        "verification_command": test_command,
                        "lessons_learned": lessons_learned,
                    },
                }
            ),
        },
    ]

    record = TrajectoryRecord(
        issue_key=f"{repo_full}#{issue_number}",
        repo=repo_full,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=issue_url,
        issue_body=issue_body,
        source="antigravity",
        provider="antigravity-teacher",
        model="gemini-4-hydrogen",
        base_commit=base_commit,
        fix_commit=fix_commit,
        repro_failed_before_fix=True,
        verification_passed=True,
        review_verdict="approved",
        patch_diff=patch_diff,
        messages=messages,
        rejected_attempts=[],
        lessons_learned=lessons_learned,
    )
    store.save(record)
    return record
