"""Build validated, deduplicated SFT and DPO training splits from trajectories."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiml_engineer.trajectory import TrajectoryStore


@dataclass
class DatasetSummary:
    total_trajectories: int
    eligible_sft: int
    train_sft_count: int
    val_sft_count: int
    dpo_pairs: int
    ready_for_finetuning: bool
    recommendation: str
    output_dir: str


def prepare_training_datasets(
    trajectories_dir: Path,
    output_dir: Path,
    val_ratio: float = 0.15,
    seed: int = 42,
    min_recommended_sft: int = 20,
) -> DatasetSummary:
    """Export verified trajectories into train/val SFT and DPO JSONL datasets."""
    store = TrajectoryStore(trajectories_dir)
    records = store.list_records()
    eligible = [r for r in records if r.is_training_eligible()]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shuffle deterministically and split without dropping tiny datasets
    shuffled = list(eligible)
    random.Random(seed).shuffle(shuffled)
    if len(shuffled) <= 1:
        train_recs, val_recs = shuffled, []
    else:
        val_n = max(1, round(len(shuffled) * val_ratio))
        val_recs = shuffled[:val_n]
        train_recs = shuffled[val_n:]

    def _write_openai_sft(recs: list[Any], path: Path) -> int:
        with path.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps({"messages": r.messages, "issue_key": r.issue_key}) + "\n")
        return len(recs)

    train_count = _write_openai_sft(train_recs, output_dir / "train_sft.jsonl")
    val_count = _write_openai_sft(val_recs, output_dir / "val_sft.jsonl")
    store.export_sft_jsonl(output_dir / "gemini_sft.jsonl", fmt="gemini")
    dpo_count = store.export_dpo_jsonl(output_dir / "train_dpo.jsonl")

    ready = len(eligible) >= min_recommended_sft
    if ready:
        rec_msg = (
            f"Ready for LoRA SFT ({len(eligible)} verified trajectories"
            + (f", {dpo_count} DPO pairs)." if dpo_count else ").")
        )
    else:
        rec_msg = (
            f"Only {len(eligible)}/{min_recommended_sft} verified trajectories collected so far. "
            "Keep using frontier APIs + few-shot Trajectory RAG until >=20 verified trajectories "
            "accumulate before running LoRA weight fine-tuning."
        )

    summary = DatasetSummary(
        total_trajectories=len(records),
        eligible_sft=len(eligible),
        train_sft_count=train_count,
        val_sft_count=val_count,
        dpo_pairs=dpo_count,
        ready_for_finetuning=ready,
        recommendation=rec_msg,
        output_dir=str(output_dir),
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary.__dict__, indent=2) + "\n", encoding="utf-8"
    )
    return summary
