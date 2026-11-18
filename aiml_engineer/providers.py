"""Multi-provider LLM backend adapters with a uniform JSON action protocol.

Using a single structured JSON action envelope across Gemini, OpenAI-compatible,
Ollama, and local Apple Silicon MLX models ensures trajectories recorded from
any teacher model can directly train and evaluate smaller student models.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from aiml_engineer.config import AgentConfig

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass
class AgentAction:
    """Parsed tool invocation returned by an LLM turn."""

    thought: str
    tool: str
    args: dict[str, Any]
    raw_text: str


def parse_action_response(text: str) -> AgentAction:
    """Parse a JSON action object from raw model output."""
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    if match:
        stripped = match.group(1).strip()
    elif not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            stripped = stripped[start : end + 1]

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output is not valid JSON action: {text[:300]}") from exc

    if not isinstance(payload, dict) or "tool" not in payload:
        raise ValueError(f"JSON action missing required 'tool' key: {payload}")

    args = payload.get("args") or {}
    if not isinstance(args, dict):
        raise TypeError(f"JSON action 'args' must be an object, got {type(args).__name__}")

    return AgentAction(
        thought=str(payload.get("thought", "")),
        tool=str(payload["tool"]),
        args=args,
        raw_text=text,
    )


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    max_retries: int = 3,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    transient_codes = {429, 500, 502, 503, 504}
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in transient_codes and attempt < max_retries:
                time.sleep(min(2**attempt, 8))
                continue
            raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:400]}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))
                continue
            raise RuntimeError(f"Connection error to {url}: {exc.reason}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


class BaseProvider(ABC):
    """Abstract chat completion provider."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    @abstractmethod
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Generate the next assistant text given OpenAI-style messages."""


class GeminiProvider(BaseProvider):
    """Google Gemini REST API adapter (no external SDK required)."""

    API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.config.api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required for GeminiProvider.")

        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_parts.append({"text": text})
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.config.temperature,
                "responseMimeType": "application/json",
            },
        }
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}

        url = f"{self.API_ROOT}/models/{self.config.model}:generateContent?key={self.config.api_key}"
        resp = _post_json(url, body, headers={})
        candidates = resp.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {resp}")
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(part.get("text", "") for part in parts)


class OpenAICompatibleProvider(BaseProvider):
    """Adapter for OpenAI, OpenRouter, Together, vLLM, or LM Studio endpoints."""

    def complete(self, messages: list[dict[str, str]]) -> str:
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }
        resp = _post_json(f"{base}/chat/completions", body, headers=headers)
        choices = resp.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI-compatible endpoint returned no choices: {resp}")
        return choices[0]["message"]["content"] or ""


class OllamaProvider(BaseProvider):
    """Adapter for local Ollama `/api/chat` with enforced JSON output."""

    def complete(self, messages: list[dict[str, str]]) -> str:
        base = (self.config.base_url or "http://localhost:11434").rstrip("/")
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.config.temperature},
        }
        resp = _post_json(f"{base}/api/chat", body, headers={})
        message = resp.get("message") or {}
        if "content" not in message:
            raise RuntimeError(f"Ollama returned unexpected payload: {resp}")
        return message["content"]


class MLXLocalProvider(BaseProvider):
    """Adapter for local Apple Silicon inference via `mlx_lm` (supports LoRA adapters)."""

    def __init__(self, config: AgentConfig, adapter_path: str | None = None) -> None:
        super().__init__(config)
        self.adapter_path = adapter_path

    def complete(self, messages: list[dict[str, str]]) -> str:
        prompt_parts = [f"<|{m['role']}|>\n{m['content']}" for m in messages]
        prompt_parts.append("<|assistant|>\n")
        prompt = "\n".join(prompt_parts)

        cmd = [
            sys.executable,
            "-m",
            "mlx_lm.generate",
            "--model",
            self.config.model,
            "--prompt",
            prompt,
            "--max-tokens",
            "1500",
            "--temp",
            str(self.config.temperature),
        ]
        if self.adapter_path:
            cmd.extend(["--adapter-path", self.adapter_path])

        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(
                f"mlx_lm failed (exit {proc.returncode}). Install `aiml-engineer[mlx]` on Apple Silicon.\n"
                f"{proc.stderr[:400]}"
            )
        return proc.stdout.strip()


class MockProvider(BaseProvider):
    """Deterministic scripted provider for tests and offline evaluation."""

    def __init__(self, config: AgentConfig, responses: list[str] | None = None) -> None:
        super().__init__(config)
        self._responses = list(responses or [])
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append([dict(m) for m in messages])
        if not self._responses:
            return json.dumps(
                {
                    "thought": "No scripted responses left.",
                    "tool": "finish",
                    "args": {
                        "summary": "Mock run completed.",
                        "root_cause": "Mock root cause.",
                        "passed": False,
                    },
                }
            )
        return self._responses.pop(0)


def build_provider(config: AgentConfig, adapter_path: str | None = None) -> BaseProvider:
    """Instantiate the configured LLM provider."""
    if config.provider == "gemini":
        return GeminiProvider(config)
    if config.provider == "openai":
        return OpenAICompatibleProvider(config)
    if config.provider == "ollama":
        return OllamaProvider(config)
    if config.provider == "mlx":
        return MLXLocalProvider(config, adapter_path=adapter_path)
    if config.provider == "mock":
        return MockProvider(config)
    raise ValueError(
        f"Unsupported provider '{config.provider}'. Choose from: gemini, openai, ollama, mlx, mock."
    )
