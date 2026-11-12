"""Reading the sources: GitHub's REST API and local folders, turned into SourceItems.

No database here: sources in, items out. service.py stores them as documents.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from app.config import Settings
from app.documents.service import SUPPORTED_TYPES

MAX_ISSUES = 50  # most recently updated issues + pull requests per sync
MAX_COMMITS = 30  # most recent commits per sync
MAX_FOLDER_FILES = 500
_REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SourceItem:
    key: str  # stable identity across syncs, e.g. "github:owner/repo#12"
    filename: str  # shown in citations
    content: bytes
    url: str | None = None


class SourceError(Exception):
    """The source can't be read (not found, bad response, outside the allowed root)."""


class GitHubRateLimitError(SourceError):
    pass


# --- GitHub ---------------------------------------------------------------------------


def valid_repo(name: str) -> bool:
    return _REPO.match(name) is not None


def github_client(settings: Settings, transport: Any = None) -> httpx2.AsyncClient:
    """An HTTP client for the GitHub API. `transport` lets tests replace the network."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ContextOS",
    }
    if settings.github_token and settings.github_token.get_secret_value():
        headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
    return httpx2.AsyncClient(
        base_url=settings.github_api_url, headers=headers, timeout=20.0, transport=transport
    )


async def _get_json(client: httpx2.AsyncClient, path: str, params: dict) -> Any:
    response = await client.get(path, params=params)
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        reset = int(response.headers.get("x-ratelimit-reset", "0"))
        at = datetime.fromtimestamp(reset, UTC).strftime("%H:%M UTC")
        raise GitHubRateLimitError(f"GitHub rate limit reached; it resets at {at}")
    if response.status_code == 404:
        raise SourceError("Repository not found (or private without a GITHUB_TOKEN)")
    if response.status_code >= 400:
        raise SourceError(f"GitHub API error {response.status_code}")
    return response.json()


def issue_item(repo: str, issue: dict) -> SourceItem:
    kind = "Pull request" if "pull_request" in issue else "Issue"
    labels = ", ".join(label["name"] for label in issue.get("labels", [])) or "none"
    text = (
        f"# {kind} #{issue['number']}: {issue['title']}\n\n"
        f"Repository: {repo}\n"
        f"State: {issue['state']}\n"
        f"Author: {(issue.get('user') or {}).get('login', 'unknown')}\n"
        f"Created: {issue['created_at']}\n"
        f"Updated: {issue['updated_at']}\n"
        f"Labels: {labels}\n"
        f"URL: {issue['html_url']}\n\n"
        f"{issue.get('body') or '(no description)'}\n"
    )
    return SourceItem(
        key=f"github:{repo}#{issue['number']}",
        filename=f"{repo}#{issue['number']} {issue['title']}"[:150] + ".md",
        content=text.encode(),
        url=issue["html_url"],
    )


def commit_item(repo: str, commit: dict) -> SourceItem:
    message = commit["commit"]["message"]
    title = message.splitlines()[0] if message else "(no message)"
    author = commit["commit"].get("author") or {}
    text = (
        f"# Commit {commit['sha'][:7]}: {title}\n\n"
        f"Repository: {repo}\n"
        f"Author: {author.get('name', 'unknown')}\n"
        f"Date: {author.get('date', 'unknown')}\n"
        f"URL: {commit['html_url']}\n\n"
        f"{message}\n"
    )
    return SourceItem(
        key=f"github:{repo}@{commit['sha']}",
        filename=f"{repo}@{commit['sha'][:7]} {title}"[:150] + ".md",
        content=text.encode(),
        url=commit["html_url"],
    )


async def fetch_github(
    client: httpx2.AsyncClient, repo: str, since: datetime | None
) -> list[SourceItem]:
    """Issues, pull requests and commits. With `since`, only what changed after it
    (polling: we ask GitHub "what's new?" on a schedule instead of receiving webhooks,
    which would need a public URL)."""
    issue_params: dict = {"state": "all", "sort": "updated", "direction": "desc", "per_page": MAX_ISSUES}
    commit_params: dict = {"per_page": MAX_COMMITS}
    if since is not None:
        issue_params["since"] = commit_params["since"] = since.astimezone(UTC).isoformat()
    issues = await _get_json(client, f"/repos/{repo}/issues", issue_params)
    commits = await _get_json(client, f"/repos/{repo}/commits", commit_params)
    return [issue_item(repo, i) for i in issues] + [commit_item(repo, c) for c in commits]


# --- Local folder -----------------------------------------------------------------------


def resolve_folder(root: Path, relative: str) -> Path:
    """The folder `relative` inside `root`. Rejects anything that escapes it ("../..",
    absolute paths, symlinks pointing outside): resolve() follows them first."""
    root = root.resolve()
    folder = (root / relative).resolve()
    if not folder.is_relative_to(root):
        raise SourceError("Folder must be inside the configured FOLDER_ROOT")
    if not folder.is_dir():
        raise SourceError(f"Not a folder: {relative}")
    return folder


def scan_folder(root: Path, relative: str, max_bytes: int) -> list[SourceItem]:
    """Every supported file (.md, .txt, .pdf) in the folder, recursively, skipping hidden
    files. Blocking file I/O: run it in a thread."""
    folder = resolve_folder(root, relative)
    items = []
    for path in sorted(folder.rglob("*")):
        parts = path.relative_to(folder).parts
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_TYPES:
            continue
        if any(part.startswith(".") for part in parts):
            continue
        data = path.read_bytes()
        if not data or len(data) > max_bytes:
            continue
        name = "/".join(parts)
        items.append(SourceItem(key=f"folder:{relative}/{name}", filename=name, content=data))
        if len(items) >= MAX_FOLDER_FILES:
            break
    return items
