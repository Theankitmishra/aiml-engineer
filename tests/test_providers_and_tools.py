"""Tests for multi-provider action parsing and sandboxed RepoToolkit."""

from __future__ import annotations

import pytest

from aiml_engineer.config import AgentConfig
from aiml_engineer.providers import MockProvider, build_provider, parse_action_response
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
    for name in ("gemini", "openai", "ollama", "mlx", "mock"):
        prov = build_provider(AgentConfig(provider=name))
        assert prov.config.provider == name
    assert isinstance(build_provider(AgentConfig(provider="mock")), MockProvider)
    with pytest.raises(ValueError, match="Unsupported provider"):
        build_provider(AgentConfig(provider="unknown"))


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
