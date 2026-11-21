"""CLI entry point for `aiml-engineer`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aiml_engineer.config import AgentConfig
from aiml_engineer.eval_harness import (
    evaluate_tasks,
    load_eval_tasks,
    register_task_from_trajectory,
    shadow_compare_teacher_vs_student,
)
from aiml_engineer.runner import discover_and_run_one, run_issue_loop
from aiml_engineer.training.finetune_lora import TrainConfig, run_lora_training
from aiml_engineer.training.prepare_dataset import prepare_training_datasets
from aiml_engineer.trajectory import TrajectoryStore, import_git_commit_as_teacher


def _build_config(args: argparse.Namespace) -> AgentConfig:
    return AgentConfig(
        workspace_dir=Path(args.workspace),
        provider=getattr(args, "provider", "gemini"),
        model=getattr(args, "model", "") or "",
        base_url=getattr(args, "base_url", None),
        max_steps=getattr(args, "max_steps", 20),
        dry_run=getattr(args, "dry_run", False),
    )


def cmd_seed_teacher(args: argparse.Namespace) -> int:
    """Import verified local Antigravity-solved commits into trajectories/ and eval/tasks.jsonl."""
    cfg = _build_config(args)
    mlclean_dir = cfg.repos_dir / "mlclean"
    if not (mlclean_dir / ".git").exists():
        print(json.dumps({"error": f"Expected {mlclean_dir} to exist with a verified commit."}))
        return 1

    store = TrajectoryStore(cfg.trajectories_dir)
    draft_path = cfg.drafts_dir / "Krushil-Dhankecha__mlclean__issue_47_PR_DRAFT.md"
    issue_body = draft_path.read_text(encoding="utf-8") if draft_path.exists() else (
        "Nullable boolean column with pd.NA raises TypeError during clean() / MLCleaner.fit_transform; "
        "CleaningPipeline.apply() also lacked a per-step crash guard."
    )
    record = import_git_commit_as_teacher(
        store=store,
        repo_dir=mlclean_dir,
        repo_full="Krushil-Dhankecha/mlclean",
        issue_number=47,
        issue_title="[Bug] A nullable boolean column with nulls makes clean() and MLCleaner.fit_transform raise",
        issue_url="https://github.com/Krushil-Dhankecha/mlclean/issues/47",
        issue_body=issue_body,
        root_cause=(
            "pd.api.types.is_numeric_dtype returns True for pandas BooleanDtype, routing nullable boolean "
            "columns to float median imputation; CleaningPipeline.apply lacked a per-step crash guard."
        ),
        test_command="tests/test_cleaning.py",
        lessons_learned=[
            "Pair is_numeric_dtype with `and not pd.api.types.is_bool_dtype(series)` for pandas nullable booleans.",
            "Reset pipeline.errors at the start of apply() and only reassign out after a step succeeds.",
        ],
        commit_ref="HEAD",
    )
    tasks_path = register_task_from_trajectory(
        eval_dir=cfg.eval_dir,
        record=record,
        clone_url="https://github.com/Krushil-Dhankecha/mlclean.git",
        test_command="tests/test_cleaning.py",
    )
    print(
        json.dumps(
            {
                "seeded_trajectory": record.issue_key,
                "base_commit": record.base_commit,
                "fix_commit": record.fix_commit,
                "eval_tasks_path": str(tasks_path),
            },
            indent=2,
        )
    )
    return 0


def cmd_record_teacher(args: argparse.Namespace) -> int:
    """Record any verified Antigravity-solved repo commit into trajectories/ and eval/tasks.jsonl."""
    cfg = _build_config(args)
    repo_dir = Path(args.repo_dir).resolve()
    store = TrajectoryStore(cfg.trajectories_dir)
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    record = import_git_commit_as_teacher(
        store=store,
        repo_dir=repo_dir,
        repo_full=args.repo,
        issue_number=args.issue_number,
        issue_title=args.title,
        issue_url=args.url or f"https://github.com/{args.repo}/issues/{args.issue_number}",
        issue_body=body,
        root_cause=args.root_cause,
        test_command=args.test_cmd,
        lessons_learned=list(args.lesson or []),
        commit_ref=args.commit_ref,
    )
    tasks_path = register_task_from_trajectory(
        eval_dir=cfg.eval_dir,
        record=record,
        clone_url=f"https://github.com/{args.repo}.git",
        test_command=args.test_cmd,
    )
    print(
        json.dumps(
            {
                "recorded_teacher": record.issue_key,
                "base_commit": record.base_commit,
                "fix_commit": record.fix_commit,
                "eval_tasks_path": str(tasks_path),
            },
            indent=2,
        )
    )
    return 0


def cmd_shadow_compare(args: argparse.Namespace) -> int:
    """Run standalone app on a teacher-solved issue, compare, capture DPO pairs, and train if ready."""
    cfg = _build_config(args)
    store = TrajectoryStore(cfg.trajectories_dir)
    records = [r for r in store.list_records() if r.source == "antigravity"]
    if not records:
        print(json.dumps({"error": "No teacher trajectories found. Run seed-teacher or record-teacher first."}))
        return 1

    if args.issue:
        matches = [r for r in records if r.issue_key == args.issue]
        if not matches:
            print(json.dumps({"error": f"Teacher trajectory '{args.issue}' not found."}))
            return 1
        teacher = matches[0]
    else:
        teacher = max(records, key=lambda r: r.created_at)

    report = shadow_compare_teacher_vs_student(
        config=cfg,
        teacher=teacher,
        clone_url=f"https://github.com/{teacher.repo}.git",
        adapter_path=args.adapter_path,
        auto_train=args.auto_train,
        train_backend=args.train_backend,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["student"]["verification_passed"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    if args.issue:
        repo_part, num_str = args.issue.split("#", 1)
        repo_name = repo_part.split("/")[-1]
        issue = {
            "issue_key": args.issue,
            "repo": repo_part,
            "repo_url": f"https://github.com/{repo_part}",
            "clone_url": f"https://github.com/{repo_part}.git",
            "issue_number": int(num_str),
            "issue_title": args.title or f"Issue #{num_str}",
            "issue_url": f"https://github.com/{repo_part}/issues/{num_str}",
            "body_excerpt": args.body or "",
        }
        rec = run_issue_loop(cfg, issue, cfg.repos_dir / repo_name)
    else:
        rec = discover_and_run_one(cfg)
        if rec is None:
            print(json.dumps({"status": "no_candidates_found"}))
            return 0
    print(
        json.dumps(
            {
                "issue_key": rec.issue_key,
                "verification_passed": rec.verification_passed,
                "repro_failed_before_fix": rec.repro_failed_before_fix,
                "review_verdict": rec.review_verdict,
                "fix_commit": rec.fix_commit,
            },
            indent=2,
        )
    )
    return 0 if rec.verification_passed else 1


def cmd_export_data(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    out_dir = Path(args.out_dir) if args.out_dir else cfg.workspace_dir / "training_data"
    summary = prepare_training_datasets(
        trajectories_dir=cfg.trajectories_dir,
        output_dir=out_dir,
    )
    print(json.dumps(summary.__dict__, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    tasks = load_eval_tasks(cfg.eval_dir / "tasks.jsonl")
    if not tasks:
        print(json.dumps({"error": "No benchmark tasks in eval/tasks.jsonl. Run `aiml-engineer seed-teacher` first."}))
        return 1
    summary = evaluate_tasks(cfg, tasks, adapter_path=args.adapter_path)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    dataset_dir = Path(args.dataset_dir) if args.dataset_dir else cfg.workspace_dir / "training_data"
    out_dir = Path(args.out_dir) if args.out_dir else cfg.workspace_dir / "training_outputs"
    tcfg = TrainConfig(
        dataset_dir=dataset_dir,
        output_dir=out_dir,
        backend=args.backend,
        mode=args.mode,
        base_model=args.base_model,
        epochs=args.epochs,
        dry_run=args.dry_run,
    )
    result = run_lora_training(tcfg)
    print(json.dumps(result, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    store = TrajectoryStore(cfg.trajectories_dir)
    records = store.list_records()
    eligible = [r for r in records if r.is_training_eligible()]
    tasks = load_eval_tasks(cfg.eval_dir / "tasks.jsonl")
    print(
        json.dumps(
            {
                "workspace": str(cfg.workspace_dir),
                "total_trajectories": len(records),
                "training_eligible_sft": len(eligible),
                "dpo_pairs": sum(len(r.rejected_attempts) for r in records),
                "eval_tasks": len(tasks),
                "fine_tuning_ready": len(eligible) >= 20,
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    default_ws = str(Path(__file__).resolve().parent.parent)
    parser = argparse.ArgumentParser(prog="aiml-engineer", description="Self-improving AI/ML GitHub engineer.")
    parser.add_argument("--workspace", default=default_ws, help="Workspace root directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed-teacher", help="Import verified Antigravity runs as teacher trajectories & eval tasks.")
    p_seed.set_defaults(func=cmd_seed_teacher)

    p_rec = sub.add_parser("record-teacher", help="Record any verified Antigravity commit as a teacher trajectory.")
    p_rec.add_argument("--repo-dir", required=True)
    p_rec.add_argument("--repo", required=True, help="owner/repo")
    p_rec.add_argument("--issue-number", required=True, type=int)
    p_rec.add_argument("--title", required=True)
    p_rec.add_argument("--url", default="")
    p_rec.add_argument("--body", default="")
    p_rec.add_argument("--body-file", default=None)
    p_rec.add_argument("--root-cause", required=True)
    p_rec.add_argument("--test-cmd", default="")
    p_rec.add_argument("--commit-ref", default="HEAD")
    p_rec.add_argument("--lesson", action="append", default=[])
    p_rec.set_defaults(func=cmd_record_teacher)

    PROVIDER_CHOICES = ["gemini", "groq", "huggingface", "openai", "ollama", "mlx", "mock"]

    p_cmp = sub.add_parser("shadow-compare", help="Replay a teacher-solved issue on aiml-engineer and compare.")
    p_cmp.add_argument("--issue", default=None, help="owner/repo#number (defaults to latest teacher trajectory)")
    p_cmp.add_argument("--provider", default="gemini", choices=PROVIDER_CHOICES)
    p_cmp.add_argument("--model", default="")
    p_cmp.add_argument("--base-url", default=None)
    p_cmp.add_argument("--adapter-path", default=None)
    p_cmp.add_argument("--max-steps", type=int, default=20)
    p_cmp.add_argument("--auto-train", action="store_true")
    p_cmp.add_argument("--train-backend", default="mlx", choices=["mlx", "trl"])
    p_cmp.set_defaults(func=cmd_shadow_compare)

    p_run = sub.add_parser("run", help="Discover and solve an AI/ML Python GitHub issue.")
    p_run.add_argument("--provider", default="gemini", choices=PROVIDER_CHOICES)
    p_run.add_argument("--model", default="")
    p_run.add_argument("--base-url", default=None)
    p_run.add_argument("--max-steps", type=int, default=20)
    p_run.add_argument("--issue", default=None, help="Optional owner/repo#number target.")
    p_run.add_argument("--title", default="")
    p_run.add_argument("--body", default="")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_exp = sub.add_parser("export-data", help="Export verified SFT & DPO datasets from trajectories/.")
    p_exp.add_argument("--out-dir", default=None)
    p_exp.set_defaults(func=cmd_export_data)

    p_eval = sub.add_parser("eval", help="Evaluate a provider/model/adapter against eval/tasks.jsonl.")
    p_eval.add_argument("--provider", default="gemini", choices=PROVIDER_CHOICES)
    p_eval.add_argument("--model", default="")
    p_eval.add_argument("--base-url", default=None)
    p_eval.add_argument("--adapter-path", default=None)
    p_eval.add_argument("--max-steps", type=int, default=20)
    p_eval.set_defaults(func=cmd_eval)

    p_train = sub.add_parser("train", help="Run optional LoRA SFT or DPO fine-tuning.")
    p_train.add_argument("--backend", default="mlx", choices=["mlx", "trl"])
    p_train.add_argument("--mode", default="sft", choices=["sft", "dpo"])
    p_train.add_argument("--base-model", default="mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
    p_train.add_argument("--dataset-dir", default=None)
    p_train.add_argument("--out-dir", default=None)
    p_train.add_argument("--epochs", type=int, default=3)
    p_train.add_argument("--dry-run", action="store_true")
    p_train.set_defaults(func=cmd_train)

    p_stat = sub.add_parser("status", help="Show distillation, eval, and fine-tuning readiness.")
    p_stat.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
