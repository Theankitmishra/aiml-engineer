"""Tests for trajectory distillation, SFT/DPO export, and LoRA training prep."""

from __future__ import annotations

import json

from aiml_engineer.training.finetune_lora import TrainConfig, run_lora_training
from aiml_engineer.training.prepare_dataset import prepare_training_datasets
from aiml_engineer.trajectory import TrajectoryRecord, TrajectoryStore


def _sample_record(issue_key: str, eligible: bool = True, with_dpo: bool = True) -> TrajectoryRecord:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": f"Fix {issue_key} nullable boolean pandas issue"},
        {"role": "assistant", "content": '{"thought": "fix", "tool": "finish", "args": {}}'},
    ]
    rejected = (
        [
            {
                "prompt_messages": msgs[:2],
                "rejected_response": '{"thought": "bad", "tool": "finish", "args": {}}',
                "chosen_response": msgs[2]["content"],
                "failure_reason": "test_failure",
            }
        ]
        if with_dpo
        else []
    )
    return TrajectoryRecord(
        issue_key=issue_key,
        repo="org/mlrepo",
        issue_number=1,
        issue_title="Nullable boolean bug",
        issue_url="https://github.com/org/mlrepo/issues/1",
        issue_body="pandas boolean column fails",
        source="antigravity",
        provider="antigravity-teacher",
        model="gemini",
        base_commit="abc",
        fix_commit="def",
        repro_failed_before_fix=eligible,
        verification_passed=eligible,
        review_verdict="approved" if eligible else "rejected",
        patch_diff="diff --git ...",
        messages=msgs,
        rejected_attempts=rejected,
        lessons_learned=["Check is_bool_dtype alongside is_numeric_dtype."],
    )


def test_trajectory_store_filtering_retrieval_and_exports(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectories")
    store.save(_sample_record("org/mlrepo#1", eligible=True, with_dpo=True))
    store.save(_sample_record("org/mlrepo#2", eligible=False, with_dpo=False))

    similar = store.retrieve_similar("pandas boolean column", k=2)
    assert [r.issue_key for r in similar] == ["org/mlrepo#1"]

    sft_openai = tmp_path / "sft_openai.jsonl"
    sft_gemini = tmp_path / "sft_gemini.jsonl"
    dpo_path = tmp_path / "dpo.jsonl"

    assert store.export_sft_jsonl(sft_openai, fmt="openai") == 1
    assert store.export_sft_jsonl(sft_gemini, fmt="gemini") == 1
    assert store.export_dpo_jsonl(dpo_path) == 1

    gemini_row = json.loads(sft_gemini.read_text(encoding="utf-8").splitlines()[0])
    assert "contents" in gemini_row
    assert gemini_row["contents"][1]["role"] == "model"


def test_prepare_dataset_and_dry_run_lora(tmp_path):
    traj_dir = tmp_path / "trajectories"
    store = TrajectoryStore(traj_dir)
    for i in range(3):
        store.save(_sample_record(f"org/mlrepo#{i}", eligible=True, with_dpo=(i == 0)))

    data_dir = tmp_path / "training_data"
    summary = prepare_training_datasets(traj_dir, data_dir, min_recommended_sft=20)
    assert summary.eligible_sft == 3
    assert summary.dpo_pairs == 1
    assert not summary.ready_for_finetuning

    plan_mlx = run_lora_training(
        TrainConfig(
            dataset_dir=data_dir,
            output_dir=tmp_path / "out_mlx",
            backend="mlx",
            mode="sft",
            dry_run=True,
        )
    )
    assert plan_mlx["status"] == "dry_run_validated"
    assert plan_mlx["sample_count"] == summary.train_sft_count

    plan_dpo = run_lora_training(
        TrainConfig(
            dataset_dir=data_dir,
            output_dir=tmp_path / "out_trl",
            backend="trl",
            mode="dpo",
            dry_run=True,
        )
    )
    assert plan_dpo["status"] == "dry_run_validated"
    assert plan_dpo["sample_count"] == 1
