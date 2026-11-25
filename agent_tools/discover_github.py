#!/usr/bin/env python3
"""Discover active AI/ML Python repositories and candidate issues on GitHub.

Uses only the Python standard library so it runs anywhere without extra installs.
Set GITHUB_TOKEN in the environment for higher API rate limits.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
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

FALLBACK_REPOS = (
    "generative-computing/mellea-skills-compiler",
    "huggingface/smolagents",
    "stanfordnlp/dspy",
    "optuna/optuna",
    "Cygnux-Labs/Tracekit",
    "secfoo-com/secfoo",
)

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

NON_CODE_HINTS = (
    "report back",
    "field-report",
    "field report",
    "open to many",
    "try tracekit on",
)


def _is_non_code_issue(item: dict[str, Any]) -> bool:
    """Return True if an issue looks like a field-report or non-code call for feedback."""
    text = f"{item.get('title', '')}\n{item.get('body') or ''}".lower()
    if any(hint in text for hint in NON_CODE_HINTS):
        return True
    labels = [lbl.get("name", "").lower() for lbl in item.get("labels", []) if isinstance(lbl, dict)]
    return any(hint in label for label in labels for hint in NON_CODE_HINTS)


def github_get(url: str, token: str | None, max_retries: int = 2) -> Any:
    """Perform a GET request to the GitHub REST API and return parsed JSON."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "antigravity-aiml-contributor/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    retryable_codes = {403, 429, 502, 503, 504}
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in retryable_codes and attempt < max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(max(float(retry_after), 0.0), 15.0) if retry_after is not None else float(4 * (attempt + 1))
                except ValueError:
                    delay = float(4 * (attempt + 1))
                time.sleep(delay)
                continue
            raise RuntimeError(f"GitHub API HTTP {exc.code} for {url}: {body[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as exc:
            if attempt < max_retries:
                time.sleep(float(2**attempt))
                continue
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"Network error for {url}: {reason}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


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
    """Return True if the issue has a cross-referenced PR or an open PR mentioning it."""
    url = f"{GITHUB_API}/repos/{repo_full}/issues/{issue_number}/timeline?per_page=30"
    try:
        events = github_get(url, token)
    except RuntimeError:
        events = []
    for ev in events:
        if ev.get("event") == "cross-referenced":
            issue_obj = (ev.get("source") or {}).get("issue") or {}
            if issue_obj.get("pull_request"):
                return True

    pulls_url = f"{GITHUB_API}/repos/{repo_full}/pulls?state=open&per_page=30"
    try:
        pulls = github_get(pulls_url, token)
    except RuntimeError:
        pulls = []
    issue_ref_re = re.compile(rf"(?:#|issue[-_/]){issue_number}\b", re.IGNORECASE)
    for pr in pulls:
        head_ref = ((pr.get("head") or {}).get("ref")) or ""
        pr_text = f"{pr.get('title', '')}\n{pr.get('body') or ''}\n{head_ref}"
        if issue_ref_re.search(pr_text):
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


def _collect_eligible_candidate(
    item: dict[str, Any],
    repo_full: str,
    token: str | None,
    attempted: set[str],
    seen_keys: set[str],
    repo_cache: dict[str, dict[str, Any]],
    repo_lookups: int,
    max_repo_lookups: int,
    min_stars: int,
    max_stars: int,
    cutoff: datetime,
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Validate an issue item and its repo metadata, returning (candidate, updated_lookups, warning)."""
    if item.get("pull_request"):
        return None, repo_lookups, None
    if item.get("assignee") or item.get("assignees"):
        return None, repo_lookups, None
    if item.get("comments", 0) > 15:
        return None, repo_lookups, None
    if _is_non_code_issue(item):
        return None, repo_lookups, None

    text = f"{item.get('title', '')}\n{item.get('body') or ''}".lower()
    if any(hint in text for hint in GPU_ONLY_HINTS):
        return None, repo_lookups, None

    issue_key = f"{repo_full}#{item['number']}"
    if issue_key in attempted or issue_key in seen_keys:
        return None, repo_lookups, None
    seen_keys.add(issue_key)

    repo = repo_cache.get(repo_full)
    if repo is None:
        if repo_lookups >= max_repo_lookups:
            return None, repo_lookups, None
        try:
            repo = github_get(f"{GITHUB_API}/repos/{repo_full}", token)
        except RuntimeError as exc:
            return None, repo_lookups, str(exc)
        repo_lookups += 1
        repo_cache[repo_full] = repo

    if repo.get("archived") or repo.get("disabled") or repo.get("fork"):
        return None, repo_lookups, None
    if repo.get("language") != "Python":
        return None, repo_lookups, None
    stars = int(repo.get("stargazers_count", 0))
    if not (min_stars <= stars <= max_stars):
        return None, repo_lookups, None

    pushed_at_str = repo.get("pushed_at", "")
    try:
        pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
    except ValueError:
        return None, repo_lookups, None
    if pushed_at < cutoff:
        return None, repo_lookups, None

    cand = {
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
    return cand, repo_lookups, None


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
            if "secondary rate limit" in warning.lower():
                break
            continue

        for item in data.get("items", []):
            repo_full = parse_repo_full_name(item["repository_url"])
            cand, repo_lookups, repo_err = _collect_eligible_candidate(
                item=item,
                repo_full=repo_full,
                token=token,
                attempted=attempted,
                seen_keys=seen_keys,
                repo_cache=repo_cache,
                repo_lookups=repo_lookups,
                max_repo_lookups=max_repo_lookups,
                min_stars=min_stars,
                max_stars=max_stars,
                cutoff=cutoff,
            )
            if repo_err:
                warning = repo_err
                break
            if cand is not None:
                candidates.append(cand)

    candidates.sort(key=lambda c: (c["score"], c["stars"]), reverse=True)
    filtered: list[dict[str, Any]] = []
    for cand in candidates:
        if len(filtered) >= limit:
            break
        if has_linked_pr(cand["repo"], cand["issue_number"], token):
            continue
        filtered.append(cand)

    if len(filtered) < limit:
        fallback_candidates: list[dict[str, Any]] = []
        for repo_full in FALLBACK_REPOS:
            if len(fallback_candidates) >= limit * 2 or repo_lookups >= max_repo_lookups:
                break
            url = f"{GITHUB_API}/repos/{repo_full}/issues?state=open&labels=bug&per_page=10"
            try:
                items = github_get(url, token)
            except RuntimeError as exc:
                warning = str(exc)
                continue
            for item in items:
                cand, repo_lookups, repo_err = _collect_eligible_candidate(
                    item=item,
                    repo_full=repo_full,
                    token=token,
                    attempted=attempted,
                    seen_keys=seen_keys,
                    repo_cache=repo_cache,
                    repo_lookups=repo_lookups,
                    max_repo_lookups=max_repo_lookups,
                    min_stars=min_stars,
                    max_stars=max_stars,
                    cutoff=cutoff,
                )
                if repo_err:
                    warning = repo_err
                    break
                if cand is not None:
                    fallback_candidates.append(cand)

        fallback_candidates.sort(key=lambda c: (c["score"], c["stars"]), reverse=True)
        for cand in fallback_candidates:
            if len(filtered) >= limit:
                break
            if has_linked_pr(cand["repo"], cand["issue_number"], token):
                continue
            filtered.append(cand)
        filtered.sort(key=lambda c: (c["score"], c["stars"]), reverse=True)

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
