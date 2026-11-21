"""Tests for multi-provider action parsing and sandboxed RepoToolkit."""

from __future__ import annotations

import urllib.error

import pytest

from aiml_engineer.config import AgentConfig
from aiml_engineer.providers import (
    GeminiProvider,
    MockProvider,
    OpenAICompatibleProvider,
    _discover_chat_models,
    _post_json,
    _redact_url,
    build_provider,
    parse_action_response,
)
from aiml_engineer.tools import RepoToolkit


def test_parse_action_response_plain_and_fenced():
    plain = '{"thought": "check files", "tool": "list_files", "args": {"subdir": "."}}'
    act1 = parse_action_response(plain)
    assert act1.tool == "list_files"
    assert act1.args == {"subdir": "."}

    fenced = 'Sure:\n```json\n{"thought": "read", "tool": "read_file", "args": {"path": "a.py"}}\n```'
    act2 = parse_action_response(fenced)
    assert act2.tool == "read_file"
    assert act2.args["path"] == "a.py"


def test_parse_action_response_rejects_invalid():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_action_response("not json at all")
    with pytest.raises(ValueError, match="missing required 'tool'"):
        parse_action_response('{"thought": "oops"}')


def test_build_provider_variants():
    for name in ("gemini", "groq", "huggingface", "openai", "ollama", "mlx", "mock"):
        prov = build_provider(AgentConfig(provider=name))
        assert prov.config.provider == name
    assert AgentConfig(provider="groq").base_url == "https://api.groq.com/openai/v1"
    assert AgentConfig(provider="huggingface").base_url == "https://router.huggingface.co/v1"
    assert isinstance(build_provider(AgentConfig(provider="mock")), MockProvider)
    with pytest.raises(ValueError, match="Unsupported provider"):
        build_provider(AgentConfig(provider="unknown"))


def test_discover_chat_models_returns_empty_on_connection_error(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise urllib.error.URLError("models endpoint unavailable")

    monkeypatch.setattr("aiml_engineer.providers._get_json", fake_get)

    assert _discover_chat_models("https://example.com/v1", {}, "llama") == []


def test_repo_toolkit_sandbox_and_file_ops(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    kit = RepoToolkit(tmp_path)

    res = kit.execute("write_file", {"path": "src/mod.py", "content": "def add(a, b):\n    return a - b\n"})
    assert res.ok

    # Traversal and .git internals must be blocked
    escape = kit.execute("read_file", {"path": "../outside.txt"})
    assert not escape.ok
    assert "escapes repository sandbox" in escape.output

    git_block = kit.execute("read_file", {"path": ".git/config"})
    assert not git_block.ok
    assert ".git" in git_block.output

    # Edit and search
    edit_res = kit.execute(
        "edit_file",
        {"path": "src/mod.py", "target": "return a - b", "replacement": "return a + b"},
    )
    assert edit_res.ok

    search_res = kit.execute("search_code", {"pattern": r"return a \+ b"})
    assert search_res.ok
    assert "src/mod.py:2:" in search_res.output


def test_gemini_interactions_provider_and_redaction(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(url, payload, headers, **_kwargs):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": '{"thought":"ok","tool":"git_status","args":{}}'}],
                }
            ]
        }

    monkeypatch.setattr("aiml_engineer.providers._post_json", fake_post)
    cfg = AgentConfig(provider="gemini", api_key="secret-test-key")
    assert cfg.model == "gemini-3.8-flash"

    out = GeminiProvider(cfg).complete(
        [
            {"role": "system", "content": "You are an engineer."},
            {"role": "user", "content": "Check status."},
        ]
    )
    assert '"tool":"git_status"' in out
    assert captured["url"] == GeminiProvider.INTERACTIONS_URL
    assert captured["headers"] == {"x-goog-api-key": "secret-test-key"}
    assert captured["payload"]["model"] == "gemini-3.8-flash"
    assert captured["payload"]["system_instruction"] == "You are an engineer."
    assert captured["payload"]["generation_config"]["thinking_level"] == "medium"
    assert captured["payload"]["generation_config"]["max_output_tokens"] == 4096
    assert _redact_url("https://example.com?key=abc123&foo=bar") == "https://example.com?key=***&foo=bar"


def test_post_json_retries_on_timeout(monkeypatch):
    attempts = {"count": 0}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=0):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("The read operation timed out")
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    result = _post_json("https://example.com?key=secret", {"a": 1}, {}, max_retries=3)
    assert result == {"ok": True}
    assert attempts["count"] == 3


def test_post_json_retries_on_http_503(monkeypatch):
    import io
    import urllib.error

    attempts = {"count": 0}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"recovered": true}'

    def fake_urlopen(req, timeout=0):
        attempts["count"] += 1
        if attempts["count"] < 4:
            raise urllib.error.HTTPError(
                req.full_url,
                503,
                "Service Unavailable",
                hdrs={},
                fp=io.BytesIO(b'{"error":"high demand"}'),
            )
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    result = _post_json("https://example.com?key=secret", {"a": 1}, {}, max_retries=5)
    assert result == {"recovered": True}
    assert attempts["count"] == 4


def test_gemini_falls_back_on_transient_timeout(monkeypatch):
    seen_models: list[str] = []

    def fake_post(url, payload, headers, **_kwargs):
        seen_models.append(payload["model"])
        if payload["model"] == "gemini-3.8-flash":
            raise RuntimeError(
                "Connection error to https://generativelanguage.googleapis.com/v1/interactions: The read operation timed out"
            )
        return {
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": '{"thought":"ok","tool":"git_status","args":{}}'}],
                }
            ]
        }

    monkeypatch.setattr("aiml_engineer.providers._post_json", fake_post)
    cfg = AgentConfig(provider="gemini", api_key="secret-test-key")
    out = GeminiProvider(cfg).complete([{"role": "user", "content": "Check status."}])
    assert '"tool":"git_status"' in out
    assert seen_models == ["gemini-3.8-flash", "gemini-3.7-flash"]


def test_openai_compatible_discovers_model_on_404(monkeypatch):
    seen_models: list[str] = []

    def fake_post(url, payload, headers, **_kwargs):
        seen_models.append(payload["model"])
        if payload["model"] == "llama-3.3-70b-versatile":
            raise RuntimeError(f"HTTP 404 from {url}: model_not_found")
        return {"choices": [{"message": {"content": '{"thought":"ok","tool":"git_status","args":{}}'}}]}

    def fake_get(url, headers, **_kwargs):
        return {
            "data": [
                {"id": "whisper-large-v3"},
                {"id": "llama-3.1-8b-instant"},
                {"id": "openai/gpt-oss-120b"},
            ]
        }

    monkeypatch.setattr("aiml_engineer.providers._post_json", fake_post)
    monkeypatch.setattr("aiml_engineer.providers._get_json", fake_get)

    cfg = AgentConfig(provider="groq", api_key="groq-test-key")
    prov = OpenAICompatibleProvider(cfg)
    out = prov.complete([{"role": "user", "content": "Check status."}])
    assert '"tool":"git_status"' in out
    assert seen_models == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]
    assert cfg.model == "openai/gpt-oss-120b"


def test_openai_compatible_shrinks_and_recovers_on_413(monkeypatch):
    seen_lengths: list[int] = []

    def fake_post(url, payload, headers, **_kwargs):
        total = sum(len(m["content"]) for m in payload["messages"])
        seen_lengths.append(total)
        if total > 7000:
            raise RuntimeError(
                f"HTTP 413 from {url}: Limit 7000, Requested 7017, please try again in 1.2s"
            )
        return {"choices": [{"message": {"content": '{"thought":"ok","tool":"git_status","args":{}}'}}]}

    monkeypatch.setattr("aiml_engineer.providers._post_json", fake_post)
    monkeypatch.setattr("aiml_engineer.providers._discover_chat_models", lambda *a, **k: [])
    monkeypatch.setattr("time.sleep", lambda _s: None)

    cfg = AgentConfig(provider="groq", api_key="groq-test-key", model="qwen/qwen3.8-27b")
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task " * 1000},
        {"role": "assistant", "content": "a" * 2000},
        {"role": "user", "content": "u" * 2000},
    ]
    out = OpenAICompatibleProvider(cfg).complete(msgs)
    assert '"tool":"git_status"' in out
    assert len(seen_lengths) == 2
    assert seen_lengths[0] > 7000
    assert seen_lengths[1] < seen_lengths[0]
