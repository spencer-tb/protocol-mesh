"""Read the latest saved map without parsing or searching client repos."""

import json
import os
from pathlib import Path

from .config import Config, Json


def load_map(config: Config) -> Json:
    """Prefer step tables; use the legacy map only when no tree exists."""
    for name in ("tree.json", "map.json"):
        path = config.data / name
        if path.exists():
            data: Json = json.loads(path.read_text())
            return data
    return {"entries": {}}


def find_ids(data: Json, query: str) -> list[str]:
    """Match a spec id, bare function name, or substring, in that order."""
    ids = sorted(data.get("entries", {}))
    for test in (
        lambda sid: sid == query,
        lambda sid: sid.endswith("." + query),
        lambda sid: query in sid,
    ):
        hits = [sid for sid in ids if test(sid)]
        if hits:
            return hits
    return []


def matches(table: Json, low: bool = False) -> list[Json]:
    """Return usable rows, with low-confidence rows only when requested."""
    return [
        m
        for m in table.get("matches", [])
        if low or m["confidence"] in {"high", "medium"}
    ]


def role(match: Json) -> str:
    """Describe a row from either generation of the map."""
    return str(match.get("role", match.get("relation", "match")))


def revision(data: Json, repo: str) -> str:
    """Read a recorded commit from either generation of the map."""
    info = data.get("repos", {}).get(repo, "")
    return str(info.get("sha", "") if isinstance(info, dict) else info)


def location(
    config: Config, data: Json, match: Json, relative: bool = False
) -> str:
    """Use an existing local file, cached source, or a pinned upstream URL."""
    name = match["repo"]
    roots = []
    if local := config.checkouts.get(name):
        roots.append(config.root / Path(local).expanduser())
    roots.append(config.repo_cache / name)
    line, end = match["line"], match["end_line"]
    for root in roots:
        path = root / match["path"]
        if path.is_file():
            shown = (
                Path(os.path.relpath(path, config.root.parent))
                if relative
                else path.resolve()
            )
            return f"{shown}:{line}-{end}"
    repos = {r.name: r for r in config.all_repos()}
    sha = revision(data, name)
    if name in repos and sha:
        return f"{repos[name].url}/blob/{sha}/{match['path']}#L{line}-L{end}"
    return f"{name}/{match['path']}:{line}-{end} (source unavailable)"


def print_entry(
    config: Config,
    data: Json,
    sid: str,
    clients: list[str],
    low: bool = False,
    limit: int = 0,
    details: bool = False,
) -> None:
    """Print a spec function's saved mappings and explicit coverage gaps."""
    entry = data.get("entries", {}).get(sid)
    print(sid)
    if entry is None:
        print("  not mapped\n")
        return
    if details:
        for step in entry.get("steps", []):
            print(f"  step {step['id']}: {step['text']}")
    for name in clients or list(config.clients):
        table = entry.get("clients", {}).get(name)
        if table is None:
            print(f"  {name:<10} not built")
            continue
        if "error" in table:
            print(f"  {name:<10} mapping failed")
            continue
        rows = matches(table, low)
        selected = rows[:limit] if limit else rows
        commits = dict.fromkeys(
            f"{m['repo']}@{revision(data, m['repo'])[:9]}" for m in selected
        )
        print(
            f"  {name:<10} mapped at {', '.join(commits) or 'recorded commit'}"
        )
        for m in selected:
            steps = ",".join(map(str, m.get("steps", [])))
            votes = f", {m['votes']}/2 votes" if "votes" in m else ""
            label = f"{role(m)}, {m['confidence']}{votes}"
            if steps:
                label += f", steps {steps}"
            print(f"    {m['qualname']}  [{label}]")
            print(f"      {location(config, data, m)}")
            if details and m.get("note"):
                print(f"      {m['note']}")
        if len(selected) < len(rows):
            print(f"    +{len(rows) - len(selected)} more; use show {sid}")
        for delegated in table.get("delegated", []):
            print(
                f"    delegated: {delegated['qualname']}"
                f" -> {delegated['spec']}"
            )
        if missing := table.get("missing"):
            print(f"    unmapped steps: {','.join(map(str, missing))}")
        if not rows and not table.get("delegated"):
            print("    no confident mapped match")
    print()
