#!/usr/bin/env python3
"""Discover active AI/ML Python repositories and candidate issues on GitHub.

Uses only the Python standard library so it runs anywhere without extra installs.
Set GITHUB_TOKEN in the environment for higher API rate limits.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GITHUB_API = "https://api.github.com"

# Note: GitHub's /search/issues endpoint allows at most 5 AND/OR/NOT operators per query.
# Keep each query to <= 4 OR terms, and split across focused queries.
ISSUE_QUERIES = [
    'language:python (pytorch OR transformers OR scikit-learn OR optuna OR mlflow) is:issue is:open label:"good first issue" no:assignee',
    'language:python (langchain OR langgraph OR llama-index OR dspy OR pydantic-ai) is:issue is:open label:"good first issue" no:assignee',
    'language:python (pytorch OR transformers OR scikit-learn OR optuna OR mlflow) is:issue is:open label:"help wanted" no:assignee',
    'language:python (langchain OR langgraph OR dspy OR scikit-learn OR optuna) is:issue is:open label:bug no:assignee',
]

GPU_ONLY_HINTS = (
    "h100",
    "a100",
    "b200",
    "rocm",
    "nccl",
    "nvcc",
    "multi-gpu",
    "cuda out of memory",
    "vllm-omni",
)


def github_get(url: str, token: str | None) -> Any:
    """Perform a GET request to the GitHub REST API and return parsed JSON."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "antigravity-aiml-contributor/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code} for {url}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def load_attempted_keys(log_path: Path) -> set[str]:
    """Read previously attempted 'owner/repo#number' keys from runs_log.jsonl."""
    attempted: set[str] = set()
    if not log_path.exists():
        return attempted
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = entry.get("issue_key")
        if isinstance(key, str) and key:
            attempted.add(key)
    return attempted


def parse_repo_full_name(repository_url: str) -> str:
    """Extract 'owner/repo' from a GitHub API repository_url."""
    prefix = f"{GITHUB_API}/repos/"
    if repository_url.startswith(prefix):
        return repository_url[len(prefix) :]
    parts = repository_url.rstrip("/").split("/")
    return "/".join(parts[-2:])


def has_linked_pr(repo_full: str, issue_number: int, token: str | None) -> bool:
    """Return True if the issue already has a cross-referenced pull request."""
    url = f"{GITHUB_API}/repos/{repo_full}/issues/{issue_number}/timeline?per_page=30"
    try:
        events = github_get(url, token)
    except RuntimeError:
        return False
    for ev in events:
        if ev.get("event") == "cross-referenced":
            issue_obj = (ev.get("source") or {}).get("issue") or {}
            if issue_obj.get("pull_request"):
                return True
    return False


def score_candidate(issue: dict[str, Any], repo: dict[str, Any]) -> int:
    """Score how suitable an issue is for local CPU/Mac reproduction and clean fixing."""
    score = 0
    labels = {lbl["name"].lower() for lbl in issue.get("labels", [])}
    if "good first issue" in labels:
        score += 40
    if "help wanted" in labels:
        score += 30
    if "bug" in labels:
        score += 15

    body = (issue.get("body") or "").lower()
    title = (issue.get("title") or "").lower()
    combined = f"{title}\n{body}"

    if "```python" in body or "traceback" in body or "reproduc" in body:
        score += 20
    if issue.get("comments", 0) <= 5:
        score += 10
    if any(hint in combined for hint in GPU_ONLY_HINTS):
        score -= 60
    if repo.get("language") == "Python":
        score += 10
    return score


def search_candidate_issues(
    token: str | None,
    attempted: set[str],
    min_stars: int,
    max_stars: int,
    active_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search GitHub for open AI/ML Python issues in active repositories."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=active_days)
    repo_cache: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    warning: str | None = None

    # Keep per-run API budget low when running unauthenticated (60 req/hr limit).
    per_page = "15" if token else "6"
    max_repo_lookups = 40 if token else 12
    repo_lookups = 0

    for idx, raw_query in enumerate(ISSUE_QUERIES):
        if len(candidates) >= limit * 2 or repo_lookups >= max_repo_lookups:
            break
        if idx > 0:
            time.sleep(1.5)
        encoded = urllib.parse.urlencode(
            {"q": raw_query, "sort": "updated", "order": "desc", "per_page": per_page}
        )
        try:
            data = github_get(f"{GITHUB_API}/search/issues?{encoded}", token)
        except RuntimeError as exc:
            warning = str(exc)
            break

        for item in data.get("items", []):
            if item.get("pull_request"):
                continue
            if item.get("comments", 0) > 15:
                continue
            text = f"{item.get('title', '')}\n{item.get('body') or ''}".lower()
            if any(hint in text for hint in GPU_ONLY_HINTS):
                continue

            repo_full = parse_repo_full_name(item["repository_url"])
            issue_key = f"{repo_full}#{item['number']}"
            if issue_key in attempted or issue_key in seen_keys:
                continue
            seen_keys.add(issue_key)

            repo = repo_cache.get(repo_full)
            if repo is None:
                if repo_lookups >= max_repo_lookups:
                    break
                try:
                    repo = github_get(f"{GITHUB_API}/repos/{repo_full}", token)
                except RuntimeError as exc:
                    warning = str(exc)
                    break
                repo_lookups += 1
                repo_cache[repo_full] = repo

            if repo.get("archived") or repo.get("disabled") or repo.get("fork"):
                continue
            if repo.get("language") != "Python":
                continue
            stars = int(repo.get("stargazers_count", 0))
            if not (min_stars <= stars <= max_stars):
                continue

            pushed_at_str = repo.get("pushed_at", "")
            try:
                pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if pushed_at < cutoff:
                continue

            candidates.append(
                {
                    "score": score_candidate(item, repo),
                    "issue_key": issue_key,
                    "repo": repo_full,
                    "repo_url": repo.get("html_url"),
                    "clone_url": repo.get("clone_url"),
                    "default_branch": repo.get("default_branch", "main"),
                    "stars": stars,
                    "language": repo.get("language"),
                    "topics": repo.get("topics", []),
                    "license": (repo.get("license") or {}).get("spdx_id"),
                    "pushed_at": pushed_at_str,
                    "issue_number": item["number"],
                    "issue_title": item["title"],
                    "issue_url": item["html_url"],
                    "labels": [lbl["name"] for lbl in item.get("labels", [])],
                    "comments": item.get("comments", 0),
                    "body_excerpt": (item.get("body") or "")[:1500],
                }
            )

    candidates.sort(key=lambda c: (c["score"], c["stars"]), reverse=True)
    filtered: list[dict[str, Any]] = []
    for cand in candidates:
        if len(filtered) >= limit:
            break
        if has_linked_pr(cand["repo"], cand["issue_number"], token):
            continue
        filtered.append(cand)
    return filtered, warning


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "runs_log.jsonl",
        help="Path to runs_log.jsonl for deduplication.",
    )
    parser.add_argument("--min-stars", type=int, default=50)
    parser.add_argument("--max-stars", type=int, default=80000)
    parser.add_argument("--active-days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or None
    attempted = load_attempted_keys(args.log)

    candidates, warning = search_candidate_issues(
        token=token,
        attempted=attempted,
        min_stars=args.min_stars,
        max_stars=args.max_stars,
        active_days=args.active_days,
        limit=args.limit,
    )

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attempted_count": len(attempted),
        "authenticated": bool(token),
        "candidates": candidates,
    }
    if warning:
        payload["warning"] = warning

    print(json.dumps(payload, indent=2))
    return 0 if (candidates or not warning) else 1


if __name__ == "__main__":
    sys.exit(main())
