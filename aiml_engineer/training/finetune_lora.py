"""Optional LoRA SFT / DPO fine-tuning pipeline (HuggingFace TRL or Apple Silicon MLX)."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TrainConfig:
    dataset_dir: Path
    output_dir: Path
    backend: str = "mlx"  # "mlx" (Apple Silicon) or "trl" (PyTorch + PEFT + TRL)
    mode: str = "sft"  # "sft" or "dpo"
    base_model: str = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    lora_r: int = 16
    lora_alpha: int = 32
    learning_rate: float = 2e-5
    epochs: int = 3
    batch_size: int = 1
    max_seq_length: int = 4096
    dry_run: bool = False


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def run_lora_training(cfg: TrainConfig) -> dict[str, Any]:
    """Validate dataset and run LoRA SFT or DPO via `mlx-lm` or HuggingFace `trl`."""
    dataset_file = cfg.dataset_dir / ("train_dpo.jsonl" if cfg.mode == "dpo" else "train_sft.jsonl")
    sample_count = _count_jsonl(dataset_file)
    if sample_count == 0:
        raise ValueError(
            f"No training records found in {dataset_file}. Collect verified trajectories and run "
            "`aiml-engineer export-data` first."
        )

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    plan: dict[str, Any] = {
        "backend": cfg.backend,
        "mode": cfg.mode,
        "base_model": cfg.base_model,
        "dataset_file": str(dataset_file),
        "sample_count": sample_count,
        "output_dir": str(cfg.output_dir),
        "lora_r": cfg.lora_r,
        "lora_alpha": cfg.lora_alpha,
        "learning_rate": cfg.learning_rate,
        "epochs": cfg.epochs,
        "dry_run": cfg.dry_run,
    }
    if cfg.dry_run:
        plan["status"] = "dry_run_validated"
        return plan

    if cfg.backend == "mlx":
        if cfg.mode != "sft":
            raise ValueError("MLX backend currently supports SFT (`--mode sft`); use `--backend trl` for DPO.")
        mlx_data_dir = cfg.output_dir / "mlx_data"
        mlx_data_dir.mkdir(parents=True, exist_ok=True)
        # mlx_lm.lora expects train.jsonl and valid.jsonl with {"messages": [...]}
        (mlx_data_dir / "train.jsonl").write_text(
            dataset_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        val_file = cfg.dataset_dir / "val_sft.jsonl"
        val_text = val_file.read_text(encoding="utf-8") if _count_jsonl(val_file) else dataset_file.read_text(encoding="utf-8")
        (mlx_data_dir / "valid.jsonl").write_text(val_text, encoding="utf-8")

        iters = max(20, sample_count * cfg.epochs)
        cmd = [
            sys.executable,
            "-m",
            "mlx_lm.lora",
            "--model",
            cfg.base_model,
            "--train",
            "--data",
            str(mlx_data_dir),
            "--adapter-path",
            str(cfg.output_dir / "adapter"),
            "--iters",
            str(iters),
            "--batch-size",
            str(cfg.batch_size),
            "--learning-rate",
            str(cfg.learning_rate),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"mlx_lm.lora failed (install `aiml-engineer[mlx]` on Apple Silicon):\n{proc.stderr[:500]}"
            )
        plan["status"] = "trained"
        plan["adapter_path"] = str(cfg.output_dir / "adapter")
        return plan

    if cfg.backend == "trl":
        try:
            from datasets import load_dataset
            from peft import LoraConfig
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from trl import DPOConfig, DPOTrainer, SFTConfig, SFTTrainer
        except ImportError as exc:
            raise RuntimeError(
                "TRL backend requires optional training dependencies: `pip install -e \".[train]\"`."
            ) from exc

        tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(cfg.base_model)
        peft_cfg = LoraConfig(
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        ds = load_dataset("json", data_files=str(dataset_file), split="train")

        if cfg.mode == "sft":
            sft_cfg = SFTConfig(
                output_dir=str(cfg.output_dir),
                num_train_epochs=cfg.epochs,
                per_device_train_batch_size=cfg.batch_size,
                learning_rate=cfg.learning_rate,
                max_length=cfg.max_seq_length,
                logging_steps=5,
                save_strategy="epoch",
            )
            trainer = SFTTrainer(
                model=model,
                args=sft_cfg,
                train_dataset=ds,
                peft_config=peft_cfg,
                processing_class=tokenizer,
            )
        elif cfg.mode == "dpo":
            dpo_cfg = DPOConfig(
                output_dir=str(cfg.output_dir),
                num_train_epochs=cfg.epochs,
                per_device_train_batch_size=cfg.batch_size,
                learning_rate=cfg.learning_rate,
                logging_steps=5,
            )
            trainer = DPOTrainer(
                model=model,
                args=dpo_cfg,
                train_dataset=ds,
                peft_config=peft_cfg,
                processing_class=tokenizer,
            )
        else:
            raise ValueError(f"Unsupported training mode: {cfg.mode}")

        trainer.train()
        adapter_dir = cfg.output_dir / "adapter"
        trainer.model.save_pretrained(adapter_dir)
        tokenizer.save_pretrained(adapter_dir)
        plan["status"] = "trained"
        plan["adapter_path"] = str(adapter_dir)
        return plan

    raise ValueError(f"Unsupported training backend '{cfg.backend}'. Use 'mlx' or 'trl'.")
