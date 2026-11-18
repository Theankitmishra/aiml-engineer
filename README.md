# Self-Improving AI/ML Python GitHub Engineer (`aiml-engineer`)

A standalone Python application + teacher-student distillation loop that discovers active AI/ML Python issues on GitHub, reproduces bugs first with failing tests, implements root-cause fixes, runs an adversarial diff review, prepares local branches & `PR_DRAFT.md` files (review-before-push), and exports verified SFT/DPO datasets for optional LoRA fine-tuning.

## Quick Start

```bash
cd /Users/ankitkumarmishra/Downloads/Github
source .venv/bin/activate

# 1. Check teacher trajectories, eval benchmarks, and fine-tuning readiness
aiml-engineer status

# 2. Run autonomously on GitHub (choose any provider)
GEMINI_API_KEY=... aiml-engineer run --provider gemini
# or OpenAI-compatible:
OPENAI_API_KEY=... aiml-engineer run --provider openai --model gpt-4o-mini
# or local Ollama (zero API cost):
aiml-engineer run --provider ollama --model qwen2.5-coder:14b
# or Apple Silicon MLX (with optional LoRA adapter):
aiml-engineer run --provider mlx --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit
```

## Teacher -> Student Training & Fine-Tuning Workflow

1. **Collect Teacher Runs & Shadow-Compare (Antigravity + Standalone):**
   Verified runs (where the reproduction test failed before the fix, all tests & `ruff` pass after the fix, and the adversarial review approves) are stored in `trajectories/` and `eval/tasks.jsonl`.
   Record any Antigravity-solved commit and shadow-test `aiml-engineer` on the exact same issue:
   ```bash
   aiml-engineer seed-teacher
   aiml-engineer record-teacher --repo-dir repos/<repo> --repo <owner/repo> --issue-number <num> --title "..." --root-cause "..."
   aiml-engineer shadow-compare --provider gemini --auto-train
   ```
   Comparison reports are written to `eval/comparisons/`, and any student failure against a verified teacher fix is automatically captured as a DPO training pair.
2. **Few-Shot Trajectory Distillation (Works Immediately, No Fine-Tuning Needed):**
   On every run, `runner.py` retrieves the top matching verified trajectories from `trajectories/` plus lessons from [PLAYBOOK.md](PLAYBOOK.md) so even base API/local models improve immediately.
3. **Export SFT & DPO Datasets:**
   ```bash
   aiml-engineer export-data
   ```
   Generates `training_data/train_sft.jsonl`, `val_sft.jsonl`, `gemini_sft.jsonl`, and `train_dpo.jsonl` (from reviewer/test rejections paired with the eventual passing fix).
4. **Replay Evaluation Benchmark:**
   ```bash
   aiml-engineer eval --provider ollama --model qwen2.5-coder:14b
   aiml-engineer eval --provider mlx --adapter-path training_outputs/adapter
   ```
5. **Optional LoRA SFT / DPO Fine-Tuning (Recommended once >= 20 verified trajectories):**
   - Apple Silicon MLX (`pip install -e ".[mlx]"`):
     ```bash
     aiml-engineer train --backend mlx --mode sft
     ```
   - PyTorch + PEFT + TRL (`pip install -e ".[train]"`):
     ```bash
     aiml-engineer train --backend trl --mode sft
     aiml-engineer train --backend trl --mode dpo
     ```

## Cloud Automation via GitHub Actions (Runs Even When Your Mac Is Off)

1. Push this repository to GitHub under your account (`Theankitmishra/aiml-engineer`).
2. In your GitHub repo, go to **Settings -> Secrets and variables -> Actions -> New repository secret** and add `GEMINI_API_KEY`.
3. Under **Settings -> Actions -> General -> Workflow permissions**, select **Read and write permissions** so the workflow can save new `drafts/`, `PLAYBOOK.md`, `trajectories/`, and `training_data/` back to your repo.
4. Go to **Actions -> Daily AI/ML GitHub Engineer -> Run workflow** to trigger your first cloud run anytime (otherwise it runs daily at 9:00 AM `Asia/Kolkata` / `03:30 UTC`).

## Workspace Layout
- `aiml_engineer/` — Standalone agent (`config.py`, `providers.py`, `tools.py`, `runner.py`, `trajectory.py`, `eval_harness.py`, `training/`, `cli.py`).
- `agent_tools/discover_github.py` — GitHub AI/ML issue discovery helper.
- `trajectories/` — Recorded teacher & standalone runs (with DPO rejection pairs).
- `eval/tasks.jsonl` — Replay benchmark tasks pinned to pre-fix commits.
- `drafts/` — Review-ready `*_PR_DRAFT.md` files (nothing is pushed without your approval).
- `PLAYBOOK.md` & `runs_log.jsonl` — Self-improving engineering loop log.
