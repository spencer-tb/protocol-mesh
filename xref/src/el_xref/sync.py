"""Shallow, sparse, blobless clones of the spec and client repos."""

import subprocess
from pathlib import Path

from .config import Config, Repo


def git(dest: Path, *args: str) -> str:
    """Run a git command in `dest` and return its stdout."""
    result = subprocess.run(
        ["git", "-C", str(dest), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sync_repo(cache: Path, repo: Repo) -> str:
    """Fetch `repo.ref` at depth 1, check out only `repo.paths`, return sha."""
    dest = cache / repo.name
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        git(dest, "init", "-q")
        git(dest, "remote", "add", "origin", repo.url)
    git(dest, "sparse-checkout", "set", "--cone", *repo.paths)
    git(
        dest,
        "fetch",
        "-q",
        "--depth",
        "1",
        "--filter=blob:none",
        "origin",
        repo.ref,
    )
    git(dest, "checkout", "-q", "--detach", "-f", "FETCH_HEAD")
    return git(dest, "rev-parse", "HEAD")


def sync_all(config: Config, only: set[str] | None = None) -> dict[str, str]:
    """Sync every repo in `config` and return a name to sha map."""
    shas = {}
    for repo in config.all_repos():
        if only and repo.name not in only:
            continue
        shas[repo.name] = sync_repo(config.repo_cache, repo)
        print(f"{repo.name:<16} {shas[repo.name][:10]}  {repo.ref}")
    return shas
