"""End-to-end integration test of Red -> Green -> Review -> Commit -> Eval loop."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aiml_engineer.cli import main as cli_main
from aiml_engineer.config import AgentConfig
from aiml_engineer.eval_harness import EvalTask, evaluate_tasks, shadow_compare_teacher_vs_student
from aiml_engineer.providers import MockProvider
from aiml_engineer.runner import run_issue_loop
from aiml_engineer.trajectory import TrajectoryStore


def _init_mini_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "calc.py").write_text("def multiply(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (repo_dir / "test_calc.py").write_text(
        "from calc import multiply\n\ndef test_zero():\n    assert multiply(0, 0) == 0\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-m", "initial"],
        cwd=repo_dir,
        check=True,
    )
    # Symlink pytest's python env into .venv/bin/pytest so RepoToolkit finds pytest cleanly
    venv_bin = repo_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    pytest_exe = Path(sys.executable).parent / "pytest"
    if pytest_exe.exists():
        (venv_bin / "pytest").symlink_to(pytest_exe)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()


def test_full_red_green_review_loop_and_dpo_capture(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "PLAYBOOK.md").write_text("# Playbook\n## 3. Run-End Retrospective Checklist\n", encoding="utf-8")
    repo_dir = ws / "repos" / "minicalc"
    base_commit = _init_mini_repo(repo_dir)

    scripted = [
        # 1. Write failing repro test
        json.dumps(
            {
                "thought": "Add a failing test reproducing multiply(3, 4) == 12.",
                "tool": "write_file",
                "args": {
                    "path": "test_calc.py",
                    "content": (
                        "from calc import multiply\n\n"
                        "def test_zero():\n    assert multiply(0, 0) == 0\n\n"
                        "def test_multiply():\n    assert multiply(3, 4) == 12\n"
                    ),
                },
            }
        ),
        # 2. Run pytest -> observe Red failure
        json.dumps({"thought": "Verify reproduction fails before fix.", "tool": "run_tests", "args": {}}),
        # 3. Fix calc.py
        json.dumps(
            {
                "thought": "Fix multiply operator.",
                "tool": "edit_file",
                "args": {"path": "calc.py", "target": "return a + b", "replacement": "return a * b"},
            }
        ),
        # 4. Call finish (first attempt)
        json.dumps(
            {
                "thought": "Done fixing.",
                "tool": "finish",
                "args": {
                    "summary": "Fixed multiply.",
                    "root_cause": "Used + instead of *.",
                    "verification_command": "",
                    "lessons_learned": ["Always test non-zero inputs."],
                },
            }
        ),
        # 5. Adversarial reviewer rejects first attempt (simulating missing negative test!)
        json.dumps({"verdict": "changes_requested", "critique": "Add negative-number edge case test."}),
        # 6. Coder adds negative-number edge case test
        json.dumps(
            {
                "thought": "Add negative-number edge case as requested by review.",
                "tool": "edit_file",
                "args": {
                    "path": "test_calc.py",
                    "target": "assert multiply(3, 4) == 12",
                    "replacement": "assert multiply(3, 4) == 12\n    assert multiply(-2, 5) == -10",
                },
            }
        ),
        # 7. Call finish (second attempt)
        json.dumps(
            {
                "thought": "Edge case test added.",
                "tool": "finish",
                "args": {
                    "summary": "Fixed multiply and tested negative inputs.",
                    "root_cause": "Used + instead of *.",
                    "verification_command": "",
                    "lessons_learned": ["Always test negative edge cases too."],
                },
            }
        ),
        # 8. Reviewer approves second attempt
        json.dumps({"verdict": "approved", "critique": ""}),
    ]

    cfg = AgentConfig(workspace_dir=ws, provider="mock", max_steps=12)
    provider = MockProvider(cfg, responses=scripted)
    issue = {
        "issue_key": "org/minicalc#1",
        "repo": "org/minicalc",
        "repo_url": "https://github.com/org/minicalc",
        "clone_url": str(repo_dir),
        "issue_number": 1,
        "issue_title": "multiply returns sum",
        "issue_url": "https://github.com/org/minicalc/issues/1",
        "body_excerpt": "multiply(3, 4) returns 7 instead of 12.",
    }

    rec = run_issue_loop(
        config=cfg,
        issue=issue,
        repo_dir=repo_dir,
        provider=provider,
        base_commit=base_commit,
        skip_repo_setup=True,
    )

    assert rec.repro_failed_before_fix is True
    assert rec.verification_passed is True
    assert rec.review_verdict == "approved"
    assert len(rec.rejected_attempts) == 1
    assert "negative-number" in rec.rejected_attempts[0]["failure_reason"]
    assert (ws / "drafts" / "org__minicalc__issue_1_PR_DRAFT.md").exists()
    assert "Always test negative edge cases too." in (ws / "PLAYBOOK.md").read_text(encoding="utf-8")


def test_eval_harness_replay_on_pinned_commit(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    repo_dir = ws / "repos" / "minicalc"
    base_commit = _init_mini_repo(repo_dir)

    scripted = [
        json.dumps(
            {
                "thought": "Repro fail",
                "tool": "write_file",
                "args": {"path": "test_calc.py", "content": "from calc import multiply\ndef test_m(): assert multiply(3, 4) == 12\n"},
            }
        ),
        json.dumps({"thought": "Run red", "tool": "run_tests", "args": {}}),
        json.dumps(
            {
                "thought": "Fix",
                "tool": "edit_file",
                "args": {"path": "calc.py", "target": "return a + b", "replacement": "return a * b"},
            }
        ),
        json.dumps(
            {
                "thought": "Finish",
                "tool": "finish",
                "args": {"summary": "Fixed.", "root_cause": "Wrong op.", "verification_command": "", "lessons_learned": []},
            }
        ),
        json.dumps({"verdict": "approved", "critique": ""}),
    ]
    cfg = AgentConfig(workspace_dir=ws, provider="mock", max_steps=10)
    task = EvalTask(
        issue_key="org/minicalc#1",
        repo="org/minicalc",
        clone_url=str(repo_dir),
        base_commit=base_commit,
        issue_number=1,
        issue_title="multiply returns sum",
        issue_url="https://github.com/org/minicalc/issues/1",
        issue_body="multiply(3, 4) returns 7.",
    )
    summary = evaluate_tasks(cfg, [task], provider=MockProvider(cfg, responses=scripted))
    assert summary["total_tasks"] == 1
    assert summary["passed"] == 1
    assert summary["resolve_rate"] == 1.0
    assert summary["repro_rate"] == 1.0


def test_record_teacher_and_shadow_compare_dpo_capture(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    repo_dir = ws / "repos" / "minicalc"
    _init_mini_repo(repo_dir)

    # Create a verified teacher commit fixing multiply
    (repo_dir / "calc.py").write_text("def multiply(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Teacher", "-c", "user.email=t@local", "commit", "-m", "fix"],
        cwd=repo_dir,
        check=True,
    )

    rc = cli_main(
        [
            "--workspace",
            str(ws),
            "record-teacher",
            "--repo-dir",
            str(repo_dir),
            "--repo",
            "org/minicalc",
            "--issue-number",
            "1",
            "--title",
            "multiply returns sum",
            "--root-cause",
            "Used + instead of *.",
            "--lesson",
            "Always check arithmetic operators.",
        ]
    )
    assert rc == 0

    store = TrajectoryStore(ws / "trajectories")
    teacher = store.list_records()[0]
    cfg = AgentConfig(workspace_dir=ws, provider="mock", max_steps=2)

    # Student returns an invalid/failing finish without fixing anything -> captures Teacher-vs-Student DPO pair
    failing_student = MockProvider(
        cfg,
        responses=[
            json.dumps(
                {
                    "thought": "Try finishing early without fixing",
                    "tool": "finish",
                    "args": {"summary": "none", "root_cause": "none"},
                }
            )
        ],
    )
    report = shadow_compare_teacher_vs_student(
        config=cfg,
        teacher=teacher,
        clone_url=str(repo_dir),
        provider=failing_student,
    )
    assert report["status"] == "student_failed_teacher_passed"
    assert report["dpo_pair_captured"] is True
    assert report["dataset_summary"]["dpo_pairs"] == 1
    assert (ws / "eval" / "comparisons" / "org__minicalc__issue_1.md").exists()


def test_submit_fork_pr_flow(tmp_path, monkeypatch):
    from aiml_engineer.runner import _submit_fork_pr

    calls: list[list[str]] = []

    class _Proc:
        def __init__(self, returncode: int = 0, stdout: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["gh", "api", "user"]:
            return _Proc(0, "Theankitmishra\n")
        if cmd[:3] == ["gh", "pr", "list"]:
            return _Proc(0, "")
        if cmd[:3] == ["gh", "pr", "create"]:
            return _Proc(0, "https://github.com/org/minicalc/pull/42\n")
        return _Proc(0, "")

    monkeypatch.setattr("subprocess.run", fake_run)
    draft = tmp_path / "draft.md"
    draft.write_text("# PR", encoding="utf-8")
    url = _submit_fork_pr(
        repo_dir=tmp_path,
        issue={"repo": "org/minicalc", "issue_number": 1, "issue_title": "fix multiply"},
        branch_name="fix/issue-1",
        draft_path=draft,
        github_token="ghp_testtoken",
    )
    assert url == "https://github.com/org/minicalc/pull/42"
    assert any(c[:3] == ["gh", "repo", "fork"] for c in calls)
    assert any(c[:2] == ["git", "push"] for c in calls)
    assert any(c[:3] == ["gh", "pr", "create"] for c in calls)
