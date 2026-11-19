"""Configuration management for aiml-engineer."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5-coder:14b",
    "mlx": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
    "mock": "mock-engineer",
}


@dataclass
class AgentConfig:
    """Runtime settings for the autonomous engineer and its distillation loop."""

    workspace_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    provider: str = "gemini"
    model: str = ""
    api_key: str | None = None
    base_url: str | None = None
    github_token: str | None = None
    max_steps: int = 20
    max_review_rounds: int = 2
    min_stars: int = 50
    max_stars: int = 80000
    active_days: int = 90
    temperature: float = 0.1
    top_k_examples: int = 2
    dry_run: bool = False

    def __post_init__(self) -> None:
        self.workspace_dir = Path(self.workspace_dir).resolve()
        self.provider = self.provider.lower()
        if not self.model:
            self.model = DEFAULT_MODELS.get(self.provider, "gemini-3.8-flash")
        if not self.api_key:
            if self.provider == "gemini":
                self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            elif self.provider == "openai":
                self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.github_token:
            self.github_token = os.environ.get("GITHUB_TOKEN") or None
        if not self.base_url:
            if self.provider == "ollama":
                self.base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            elif self.provider == "openai":
                self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @property
    def repos_dir(self) -> Path:
        return self.workspace_dir / "repos"

    @property
    def drafts_dir(self) -> Path:
        return self.workspace_dir / "drafts"

    @property
    def trajectories_dir(self) -> Path:
        return self.workspace_dir / "trajectories"

    @property
    def eval_dir(self) -> Path:
        return self.workspace_dir / "eval"

    @property
    def playbook_path(self) -> Path:
        return self.workspace_dir / "PLAYBOOK.md"

    @property
    def runs_log_path(self) -> Path:
        return self.workspace_dir / "runs_log.jsonl"

    @classmethod
    def from_file(cls, path: Path, **overrides: Any) -> AgentConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workspace_dir"] = str(self.workspace_dir)
        if payload.get("api_key"):
            payload["api_key"] = "***"
        if payload.get("github_token"):
            payload["github_token"] = "***"
        return payload
