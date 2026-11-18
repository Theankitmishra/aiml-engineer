"""Replay evaluation benchmark for comparing base, prompt-distilled, and fine-tuned models."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiml_engineer.config import AgentConfig
from aiml_engineer.providers import BaseProvider, build_provider
from aiml_engineer.runner import run_issue_loop
from aiml_engineer.training.finetune_lora import TrainConfig, run_lora_training
from aiml_engineer.training.prepare_dataset import prepare_training_datasets
from aiml_engineer.trajectory import TrajectoryRecord, TrajectoryStore


@dataclass
class EvalTask:
    """A pinned historical GitHub issue reproducible at `base_commit`."""

    issue_key: str
    repo: str
    clone_url: str
    base_commit: str
    issue_number: int
    issue_title: str
    issue_url: str
    issue_body: str
    test_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalTask:
        return cls(**data)


def load_eval_tasks(tasks_path: Path) -> list[EvalTask]:
    if not tasks_path.exists():
        return []
    tasks: list[EvalTask] = []
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        tasks.append(EvalTask.from_dict(json.loads(line)))
    return tasks


def register_task_from_trajectory(
    eval_dir: Path,
    record: TrajectoryRecord,
    clone_url: str,
    test_command: str = "",
) -> Path:
    """Add or update a verified trajectory in `eval/tasks.jsonl`."""
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    tasks_path = eval_dir / "tasks.jsonl"
    existing = {t.issue_key: t for t in load_eval_tasks(tasks_path)}
    existing[record.issue_key] = EvalTask(
        issue_key=record.issue_key,
        repo=record.repo,
        clone_url=clone_url,
        base_commit=record.base_commit,
        issue_number=record.issue_number,
        issue_title=record.issue_title,
        issue_url=record.issue_url,
        issue_body=record.issue_body,
        test_command=test_command,
    )
    with tasks_path.open("w", encoding="utf-8") as fh:
        for task in existing.values():
            fh.write(json.dumps(task.to_dict()) + "\n")
    return tasks_path


def evaluate_tasks(
    config: AgentConfig,
    tasks: list[EvalTask],
    provider: BaseProvider | None = None,
    adapter_path: str | None = None,
) -> dict[str, Any]:
    """Run the agent in isolated temp workspaces against pinned historical commits."""
    eval_config = AgentConfig(
        workspace_dir=config.workspace_dir,
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        github_token=config.github_token,
        max_steps=config.max_steps,
        max_review_rounds=config.max_review_rounds,
        temperature=config.temperature,
        top_k_examples=config.top_k_examples,
        dry_run=True,
    )
    llm = provider or build_provider(eval_config, adapter_path=adapter_path)

    results: list[dict[str, Any]] = []
    for task in tasks:
        repo_name = task.repo.split("/")[-1]
        local_mirror = config.repos_dir / repo_name
        clone_source = str(local_mirror) if (local_mirror / ".git").exists() else task.clone_url

        with tempfile.TemporaryDirectory(prefix="aiml_eval_") as tmp:
            work_repo = Path(tmp) / repo_name
            issue_dict = {
                "issue_key": task.issue_key,
                "repo": task.repo,
                "repo_url": f"https://github.com/{task.repo}",
                "clone_url": clone_source,
                "issue_number": task.issue_number,
                "issue_title": task.issue_title,
                "issue_url": task.issue_url,
                "body_excerpt": task.issue_body,
            }
            rec = run_issue_loop(
                config=eval_config,
                issue=issue_dict,
                repo_dir=work_repo,
                provider=llm,
                base_commit=task.base_commit,
            )
            results.append(
                {
                    "issue_key": task.issue_key,
                    "repro_failed_before_fix": rec.repro_failed_before_fix,
                    "verification_passed": rec.verification_passed,
                    "review_verdict": rec.review_verdict,
                    "turns": len(rec.messages),
                }
            )

    total = len(results)
    passed = sum(1 for r in results if r["verification_passed"])
    repro_ok = sum(1 for r in results if r["repro_failed_before_fix"])
    summary = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "provider": config.provider,
        "model": config.model,
        "adapter_path": adapter_path,
        "total_tasks": total,
        "passed": passed,
        "resolve_rate": round(passed / total, 4) if total else 0.0,
        "repro_rate": round(repro_ok / total, 4) if total else 0.0,
        "tasks": results,
    }

    results_dir = config.eval_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"eval_{stamp}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def shadow_compare_teacher_vs_student(
    config: AgentConfig,
    teacher: TrajectoryRecord,
    clone_url: str,
    provider: BaseProvider | None = None,
    adapter_path: str | None = None,
    auto_train: bool = False,
    train_backend: str = "mlx",
) -> dict[str, Any]:
    """Replay a teacher-solved issue on the standalone app, compare, and update training data."""
    eval_config = AgentConfig(
        workspace_dir=config.workspace_dir,
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        github_token=config.github_token,
        max_steps=config.max_steps,
        max_review_rounds=config.max_review_rounds,
        temperature=config.temperature,
        top_k_examples=config.top_k_examples,
        dry_run=True,
    )
    llm = provider or build_provider(eval_config, adapter_path=adapter_path)

    repo_name = teacher.repo.split("/")[-1]
    local_mirror = config.repos_dir / repo_name
    clone_source = str(local_mirror) if (local_mirror / ".git").exists() else clone_url

    with tempfile.TemporaryDirectory(prefix="aiml_shadow_") as tmp:
        work_repo = Path(tmp) / repo_name
        issue_dict = {
            "issue_key": teacher.issue_key,
            "repo": teacher.repo,
            "repo_url": f"https://github.com/{teacher.repo}",
            "clone_url": clone_source,
            "issue_number": teacher.issue_number,
            "issue_title": teacher.issue_title,
            "issue_url": teacher.issue_url,
            "body_excerpt": teacher.issue_body,
        }
        student = run_issue_loop(
            config=eval_config,
            issue=issue_dict,
            repo_dir=work_repo,
            provider=llm,
            base_commit=teacher.base_commit,
        )

    store = TrajectoryStore(config.trajectories_dir)
    dpo_captured = False
    if teacher.verification_passed and not student.verification_passed and teacher.messages:
        student_last = next(
            (m["content"] for m in reversed(student.messages) if m["role"] == "assistant"),
            "",
        )
        if student_last and teacher.messages[-1]["role"] == "assistant":
            teacher.rejected_attempts.append(
                {
                    "prompt_messages": teacher.messages[:2],
                    "rejected_response": student_last,
                    "chosen_response": teacher.messages[-1]["content"],
                    "failure_reason": "shadow_compare_student_failed",
                }
            )
            store.save(teacher)
            dpo_captured = True

    data_dir = config.workspace_dir / "training_data"
    dataset_summary = prepare_training_datasets(
        trajectories_dir=config.trajectories_dir,
        output_dir=data_dir,
    )

    training_result: dict[str, Any] | None = None
    if auto_train and dataset_summary.ready_for_finetuning:
        training_result = run_lora_training(
            TrainConfig(
                dataset_dir=data_dir,
                output_dir=config.workspace_dir / "training_outputs",
                backend=train_backend,
                mode="sft",
                dry_run=False,
            )
        )

    status = (
        "student_matched_teacher"
        if (teacher.verification_passed and student.verification_passed)
        else "student_failed_teacher_passed"
        if teacher.verification_passed
        else "teacher_not_verified"
    )
    report: dict[str, Any] = {
        "compared_at": datetime.now(timezone.utc).isoformat(),
        "issue_key": teacher.issue_key,
        "status": status,
        "teacher": {
            "provider": teacher.provider,
            "model": teacher.model,
            "verification_passed": teacher.verification_passed,
            "diff_lines": len(teacher.patch_diff.splitlines()),
        },
        "student": {
            "provider": config.provider,
            "model": config.model,
            "adapter_path": adapter_path,
            "repro_failed_before_fix": student.repro_failed_before_fix,
            "verification_passed": student.verification_passed,
            "review_verdict": student.review_verdict,
            "turns": len(student.messages),
            "diff_lines": len(student.patch_diff.splitlines()),
        },
        "dpo_pair_captured": dpo_captured,
        "dataset_summary": dataset_summary.__dict__,
        "training_result": training_result,
    }

    comp_dir = config.eval_dir / "comparisons"
    comp_dir.mkdir(parents=True, exist_ok=True)
    safe_key = teacher.issue_key.replace("/", "__").replace("#", "__issue_")
    (comp_dir / f"{safe_key}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    md = (
        f"# Shadow Comparison: {teacher.issue_key}\n\n"
        f"- **Result:** `{status}`\n"
        f"- **Teacher ({teacher.provider}):** passed=`{teacher.verification_passed}`, "
        f"diff_lines=`{report['teacher']['diff_lines']}`\n"
        f"- **Student ({config.provider} / {config.model}):** "
        f"repro_red=`{student.repro_failed_before_fix}`, passed=`{student.verification_passed}`, "
        f"review=`{student.review_verdict}`, turns=`{len(student.messages)}`, "
        f"diff_lines=`{report['student']['diff_lines']}`\n"
        f"- **DPO Pair Captured:** `{dpo_captured}`\n"
        f"- **Dataset Status:** {dataset_summary.recommendation}\n"
    )
    (comp_dir / f"{safe_key}.md").write_text(md, encoding="utf-8")
    return report
