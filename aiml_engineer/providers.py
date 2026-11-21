"""Multi-provider LLM backend adapters with a uniform JSON action protocol.

Using a single structured JSON action envelope across Gemini, OpenAI-compatible,
Ollama, and local Apple Silicon MLX models ensures trajectories recorded from
any teacher model can directly train and evaluate smaller student models.
"""

from __future__ import annotations

import http.client
import json
import random
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


def _redact_url(url: str) -> str:
    return re.sub(r"([?&]key=)[^&]+", r"\1***", url)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return min(2 ** (attempt + 1), 30) + random.uniform(0.0, 1.5)


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    max_retries: int = 5,
    timeout: int = 180,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    transient_codes = {429, 500, 502, 503, 504}
    safe_url = _redact_url(url)
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in transient_codes and attempt < max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(_retry_delay(attempt, retry_after))
                continue
            raise RuntimeError(f"HTTP {exc.code} from {safe_url}: {body[:400]}") from exc
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as exc:
            if attempt < max_retries:
                time.sleep(_retry_delay(attempt))
                continue
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"Connection error to {safe_url}: {reason}") from exc
    raise RuntimeError(f"Exhausted retries for {safe_url}")


class BaseProvider(ABC):
    """Abstract chat completion provider."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    @abstractmethod
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Generate the next assistant text given OpenAI-style messages."""


class GeminiProvider(BaseProvider):
    """Google Gemini Interactions REST API adapter (no external SDK required)."""

    INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.config.api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required for GeminiProvider.")

        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_parts.append(text)
            else:
                step_type = "model_output" if role == "assistant" else "user_input"
                turns.append({"type": step_type, "content": [{"type": "text", "text": text}]})

        body: dict[str, Any] = {
            "model": self.config.model,
            "input": turns,
            "generation_config": {
                "temperature": self.config.temperature,
                "thinking_level": "medium",
                "max_output_tokens": 4096,
            },
        }
        if system_parts:
            body["system_instruction"] = "\n\n".join(system_parts)

        resp = _post_json(
            self.INTERACTIONS_URL,
            body,
            headers={"x-goog-api-key": self.config.api_key},
        )
        texts: list[str] = []
        for step in resp.get("steps") or []:
            if step.get("type") == "model_output":
                for item in step.get("content") or []:
                    if item.get("type") == "text" and item.get("text"):
                        texts.append(item["text"])
        if texts:
            return "".join(texts)

        # Fallback for alternate interaction output payloads
        for item in resp.get("outputs") or []:
            if item.get("type") == "text" and item.get("text"):
                texts.append(item["text"])
        if texts:
            return "".join(texts)
        raise RuntimeError(f"Gemini Interactions API returned no text output: {resp}")


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
