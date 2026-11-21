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
            headers={"Content-Type": "application/json", "User-Agent": "aiml-engineer/0.1", **headers},
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
    FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash")

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

        candidate_models = [self.config.model]
        if self.config.model == "gemini-3.8-flash":
            for fb in self.FALLBACK_MODELS:
                if fb not in candidate_models:
                    candidate_models.append(fb)

        last_exc: RuntimeError | None = None
        resp: dict[str, Any] | None = None
        for model_name in candidate_models:
            body: dict[str, Any] = {
                "model": model_name,
                "input": turns,
                "generation_config": {
                    "temperature": self.config.temperature,
                    "thinking_level": "medium",
                    "max_output_tokens": 4096,
                },
            }
            if system_parts:
                body["system_instruction"] = "\n\n".join(system_parts)

            try:
                resp = _post_json(
                    self.INTERACTIONS_URL,
                    body,
                    headers={"x-goog-api-key": self.config.api_key},
                    max_retries=2,
                    timeout=60,
                )
                break
            except RuntimeError as exc:
                last_exc = exc
                msg_lower = str(exc).lower()
                is_transient = any(
                    marker in msg_lower
                    for marker in ("timed out", "http 429", "http 500", "http 502", "http 503", "http 504")
                )
                if not is_transient:
                    raise
        if resp is None:
            assert last_exc is not None
            raise last_exc
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


def _get_json(
    url: str,
    headers: dict[str, str],
    timeout: int = 30,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "aiml-engineer/0.1", **headers},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


_NON_CHAT_MODEL_HINTS = ("whisper", "tts", "orpheus", "guard", "safeguard", "embed", "rerank")
_PREFERRED_MODEL_HINTS = ("gpt-oss-120b", "qwen", "llama-3.3-70b", "llama-4", "deepseek", "gpt-oss-20b", "llama-3.1-8b")


def _discover_chat_models(base: str, headers: dict[str, str], exclude: str) -> list[str]:
    try:
        payload = _get_json(f"{base}/models", headers=headers)
    except (
        urllib.error.URLError,
        TimeoutError,
        http.client.HTTPException,
        ConnectionError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return []
    raw_ids = [
        str(item.get("id", ""))
        for item in (payload.get("data") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    candidates = [
        mid
        for mid in raw_ids
        if mid != exclude and not any(h in mid.lower() for h in _NON_CHAT_MODEL_HINTS)
    ]

    def _rank(mid: str) -> tuple[int, str]:
        low = mid.lower()
        for idx, hint in enumerate(_PREFERRED_MODEL_HINTS):
            if hint in low:
                return (idx, mid)
        return (len(_PREFERRED_MODEL_HINTS), mid)

    return sorted(candidates, key=_rank)


_TRY_AGAIN_RE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)", re.IGNORECASE)


def _shrink_messages(messages: list[dict[str, str]], max_chars: int) -> list[dict[str, str]]:
    """Aggressively compact messages to fit a tight token/byte limit."""
    if len(messages) <= 2:
        return [
            {"role": m["role"], "content": m["content"][: max(max_chars // 2, 1000)]}
            for m in messages
        ]
    head = [
        messages[0],
        {"role": messages[1]["role"], "content": messages[1]["content"][: max(max_chars // 3, 1200)]},
    ]
    tail = [
        {"role": m["role"], "content": m["content"][:1000]}
        for m in messages[2:][-2:]
    ]
    return [*head, *tail]


class OpenAICompatibleProvider(BaseProvider):
    """Adapter for OpenAI, Groq, Hugging Face Router, OpenRouter, Together, vLLM, or LM Studio."""

    def complete(self, messages: list[dict[str, str]]) -> str:
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        def _call(model_name: str, msgs: list[dict[str, str]]) -> dict[str, Any]:
            body: dict[str, Any] = {
                "model": model_name,
                "messages": msgs,
                "temperature": self.config.temperature,
            }
            if self.config.provider in ("openai", "groq"):
                body["response_format"] = {"type": "json_object"}
            return _post_json(f"{base}/chat/completions", body, headers=headers)

        active_msgs = messages
        try:
            resp = _call(self.config.model, active_msgs)
        except RuntimeError as exc:
            err_lower = str(exc).lower()
            is_413 = "http 413" in err_lower or "request too large" in err_lower or "rate_limit_exceeded" in err_lower
            is_404 = "http 404" in err_lower or "model_not_found" in err_lower
            if not (is_404 or is_413):
                raise

            if is_413:
                wait_match = _TRY_AGAIN_RE.search(str(exc))
                if wait_match:
                    val = float(wait_match.group(1))
                    delay = val / 1000.0 if wait_match.group(2).lower() == "ms" else val
                    time.sleep(min(max(delay, 1.0), 65.0))
                active_msgs = _shrink_messages(active_msgs, max_chars=6000)

            discovered = _discover_chat_models(base, headers, exclude=self.config.model)
            candidates = discovered[:4] if is_404 else [self.config.model, *discovered[:3]]
            if not candidates:
                raise
            resp = None
            last_exc: RuntimeError = exc
            for candidate in candidates:
                try:
                    resp = _call(candidate, active_msgs)
                    self.config.model = candidate
                    break
                except RuntimeError as inner_exc:
                    last_exc = inner_exc
                    if "http 413" in str(inner_exc).lower():
                        active_msgs = _shrink_messages(active_msgs, max_chars=4000)
            if resp is None:
                raise last_exc

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
    if config.provider in ("openai", "groq", "huggingface"):
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
