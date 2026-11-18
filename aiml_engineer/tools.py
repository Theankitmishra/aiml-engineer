"""Sandboxed repository tools for the standalone AI/ML engineering agent."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOL_DESCRIPTIONS = """Available tools (respond ONLY with a single JSON object {"thought": "...", "tool": "<name>", "args": {...}}):
- list_files(subdir=".", max_entries=120): List relative file paths inside the repository.
- read_file(path, start_line=1, end_line=150): Read lines from a repository file.
- search_code(pattern, subdir=".", max_matches=40): Regex search across Python/text files in the repository.
- write_file(path, content): Create or overwrite a file inside the repository.
- edit_file(path, target, replacement): Replace an exact substring in a repository file.
- run_tests(pytest_args=""): Run pytest using the repository's isolated .venv (e.g. "tests/test_cleaning.py").
- run_lint(): Run ruff check (if installed in .venv).
- git_status(): Show short git status.
- git_diff(): Show current unstaged + staged git diff.
- finish(summary, root_cause, repro_test_command, verification_command, lessons_learned): Finish after tests and diff review pass.
"""

_IGNORED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


@dataclass
class ToolResult:
    ok: bool
    output: str
    finished: bool = False
    payload: dict[str, Any] | None = None


class RepoToolkit:
    """Path-confined tool executor operating strictly inside `repo_dir`."""

    def __init__(self, repo_dir: Path, command_timeout: int = 120) -> None:
        self.repo_dir = Path(repo_dir).resolve()
        self.command_timeout = command_timeout

    def _safe_path(self, rel_path: str) -> Path:
        candidate = (self.repo_dir / rel_path).resolve()
        try:
            candidate.relative_to(self.repo_dir)
        except ValueError as exc:
            raise PermissionError(f"Path escapes repository sandbox: {rel_path}") from exc
        if ".git" in candidate.relative_to(self.repo_dir).parts:
            raise PermissionError(f"Direct access to .git internals is disallowed: {rel_path}")
        return candidate

    def _run_cmd(self, cmd: list[str]) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            cmd,
            check=False,
            cwd=self.repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.command_timeout,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        if len(combined) > 6000:
            combined = combined[:2500] + "\n... [truncated] ...\n" + combined[-3000:]
        return proc.returncode, combined

    def list_files(self, subdir: str = ".", max_entries: int = 120) -> ToolResult:
        root = self._safe_path(subdir)
        if not root.exists():
            return ToolResult(False, f"Directory not found: {subdir}")
        paths: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
            for name in sorted(filenames):
                rel = (Path(dirpath) / name).relative_to(self.repo_dir)
                paths.append(str(rel))
                if len(paths) >= max_entries:
                    paths.append("... [truncated]")
                    return ToolResult(True, "\n".join(paths))
        return ToolResult(True, "\n".join(paths) if paths else "(empty)")

    def read_file(self, path: str, start_line: int = 1, end_line: int = 150) -> ToolResult:
        target = self._safe_path(path)
        if not target.is_file():
            return ToolResult(False, f"File not found: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), max(start, int(end_line)))
        numbered = [f"{i}: {lines[i - 1]}" for i in range(start, end + 1)]
        header = f"Showing lines {start}-{end} of {len(lines)} in {path}:"
        return ToolResult(True, header + "\n" + "\n".join(numbered))

    def search_code(self, pattern: str, subdir: str = ".", max_matches: int = 40) -> ToolResult:
        root = self._safe_path(subdir)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(False, f"Invalid regex '{pattern}': {exc}")

        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
            for name in sorted(filenames):
                if not name.endswith((".py", ".toml", ".md", ".yaml", ".yml", ".json")):
                    continue
                fpath = Path(dirpath) / name
                rel = fpath.relative_to(self.repo_dir)
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        matches.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                        if len(matches) >= max_matches:
                            return ToolResult(True, "\n".join(matches))
        return ToolResult(True, "\n".join(matches) if matches else "No matches.")

    def write_file(self, path: str, content: str) -> ToolResult:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {len(content)} bytes to {path}")

    def edit_file(self, path: str, target: str, replacement: str) -> ToolResult:
        fpath = self._safe_path(path)
        if not fpath.is_file():
            return ToolResult(False, f"File not found: {path}")
        content = fpath.read_text(encoding="utf-8")
        count = content.count(target)
        if count == 0:
            return ToolResult(False, f"Target substring not found in {path}")
        if count > 1:
            return ToolResult(False, f"Target substring matched {count} times in {path}; be more specific")
        fpath.write_text(content.replace(target, replacement, 1), encoding="utf-8")
        return ToolResult(True, f"Edited {path}")

    def run_tests(self, pytest_args: str = "") -> ToolResult:
        venv_pytest = self.repo_dir / ".venv" / "bin" / "pytest"
        exe = str(venv_pytest) if venv_pytest.exists() else "pytest"
        extra = [tok for tok in pytest_args.split() if tok]
        try:
            code, out = self._run_cmd([exe, *extra])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"pytest execution error: {exc}")
        return ToolResult(code == 0, f"exit_code={code}\n{out}")

    def run_lint(self) -> ToolResult:
        venv_ruff = self.repo_dir / ".venv" / "bin" / "ruff"
        if not venv_ruff.exists():
            return ToolResult(True, "ruff not installed in .venv; skipped.")
        try:
            code, out = self._run_cmd([str(venv_ruff), "check", "."])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"ruff execution error: {exc}")
        return ToolResult(code == 0, f"exit_code={code}\n{out}")

    def git_status(self) -> ToolResult:
        code, out = self._run_cmd(["git", "status", "--short"])
        return ToolResult(code == 0, out or "(clean working tree)")

    def git_diff(self) -> ToolResult:
        code, out = self._run_cmd(["git", "diff"])
        return ToolResult(code == 0, out or "(no diff)")

    def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch a tool call safely by name."""
        handlers = {
            "list_files": lambda: self.list_files(**args),
            "read_file": lambda: self.read_file(**args),
            "search_code": lambda: self.search_code(**args),
            "write_file": lambda: self.write_file(**args),
            "edit_file": lambda: self.edit_file(**args),
            "run_tests": lambda: self.run_tests(**args),
            "run_lint": lambda: self.run_lint(),
            "git_status": lambda: self.git_status(),
            "git_diff": lambda: self.git_diff(),
        }
        if tool == "finish":
            return ToolResult(True, "Agent signaled completion.", finished=True, payload=args)
        handler = handlers.get(tool)
        if handler is None:
            return ToolResult(False, f"Unknown tool: {tool}")
        try:
            return handler()
        except (PermissionError, TypeError, ValueError, OSError) as exc:
            return ToolResult(False, f"Tool '{tool}' failed: {exc}")
