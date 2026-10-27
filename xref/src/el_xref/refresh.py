"""
Keep the map current at almost no cost.

1. Sync and index every repo.
2. Find each mapped client function again by path and name, then by name
   in the same repo if it moved, and update its lines. Unchanged code
   needs nothing else.
3. Ask one small judge call per row whose code changed whether it still
   does its steps, and drop the rows that no longer do.
4. Rebuild only the pairs whose EELS function changed or whose client
   code could not be found again.
5. Write the map.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from .build import (
    MODEL,
    PIPELINE,
    Client,
    build,
    claude,
    is_spec,
    render_tsv,
    tree_path,
    tsv_target,
)
from .config import Config, Json
from .index import Symbol, index_path, load, read_body
from .index import build as build_index
from .link import head
from .sync import sync_all

RECHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "still": {"type": "boolean"},
        "steps": {"type": "array", "items": {"type": "integer"}},
        "note": {"type": "string"},
    },
    "required": ["still", "steps", "note"],
}

RECHECK_PROMPT = """\
A client function was mapped to these steps of a function from the \
Ethereum execution specs, and its code has changed since. Does its own \
code still do any of the steps? Answer from the code. List the steps it \
does now and note in one sentence what changed.

Spec function {spec_id}:

```python
{spec_body}
```

Steps:
{steps}

Earlier note: {note}

Client function {qualname} ({repo}/{path}:{line}-{end_line}), as it is now:

```
{body}
```
"""


def find_again(client: Client, row: Json) -> Symbol | None:
    """Return a mapped client function in the fresh index, if it is there."""
    exact = client.by_key.get((row["repo"], row["path"], row["qualname"]))
    if exact is not None:
        return exact
    name = row["qualname"].split(".")[-1]
    moved = [
        s
        for s in client.by_name.get(name, [])
        if s.repo == row["repo"] and s.qualname == row["qualname"]
    ]
    return moved[0] if len(moved) == 1 else None


def recheck(
    config: Config, spec: Symbol, steps: list[Json], row: Json, now: Symbol
) -> tuple[Json, float]:
    """Ask whether a changed client function still does its steps."""
    prompt = RECHECK_PROMPT.format(
        spec_id=spec.id,
        spec_body=read_body(config, spec),
        steps="\n".join(f"[{s['id']}] {s['text']}" for s in steps),
        note=row.get("note", ""),
        qualname=now.qualname,
        repo=now.repo,
        path=now.path,
        line=now.line,
        end_line=now.end_line,
        body=read_body(config, now),
    )
    return claude(prompt, RECHECK_SCHEMA, effort="low", model=MODEL)


def refresh(config: Config, workers: int, max_usd: float = 0.0) -> None:
    """
    Bring the map up to date with the repos, spending only on changes.

    `max_usd` caps the rebuild of stale pairs, as in `build`.
    """
    sync_all(config)
    build_index(config)
    path = tree_path(config)
    data: Json = json.loads(path.read_text())
    specs = {s.id: s for s in load(index_path(config, config.spec.repo))}
    clients = {name: Client(config, name) for name in config.clients}

    moved = changed = dropped = 0
    stale: set[tuple[str, str]] = set()
    checks: list[tuple[str, str, Json, Symbol]] = []
    for spec_id, entry in data["entries"].items():
        spec = specs.get(spec_id)
        for name, table in entry.get("clients", {}).items():
            if table.get("pipeline") != PIPELINE or name not in clients:
                continue
            if spec is None or not is_spec(spec):
                continue
            if table.get("spec_hash") != spec.hash:
                stale.add((spec_id, name))
                continue
            for row in table.get("matches", []) + table.get("delegated", []):
                now = find_again(clients[name], row)
                if now is None:
                    stale.add((spec_id, name))
                    continue
                if (now.path, now.line) != (row["path"], row["line"]):
                    shift = now.line - row["line"]
                    if row.get("first"):
                        row["first"] += shift
                        row["last"] += shift
                    row["path"] = now.path
                    row["line"], row["end_line"] = now.line, now.end_line
                    moved += 1
                if now.hash != row["hash"] and "role" in row:
                    checks.append((spec_id, name, row, now))
                row["hash"] = now.hash

    # A recheck past the cap, or one that fails, rebuilds its pair instead.
    cost = [0.0]
    lock = threading.Lock()

    def one(item: tuple[str, str, Json, Symbol]) -> Json | None:
        spec_id, _, row, now = item
        if max_usd and cost[0] >= max_usd:
            return None
        steps = data["entries"][spec_id]["steps"]
        try:
            verdict, spent = recheck(config, specs[spec_id], steps, row, now)
        except RuntimeError:
            return None
        with lock:
            cost[0] += spent
        return verdict

    with ThreadPoolExecutor(workers) as pool:
        for item, verdict in zip(checks, pool.map(one, checks), strict=True):
            spec_id, name, row, _ = item
            if verdict is None:
                stale.add((spec_id, name))
                continue
            changed += 1
            if verdict["still"] and verdict["steps"]:
                row["steps"] = verdict["steps"]
                row["note"] = verdict["note"]
            else:
                row["dropped"] = True
                dropped += 1
    for entry in data["entries"].values():
        for table in entry.get("clients", {}).values():
            if any(r.get("dropped") for r in table.get("matches", [])):
                table["matches"] = [
                    r for r in table["matches"] if not r.get("dropped")
                ]
                covered = {s for m in table["matches"] for s in m["steps"]}
                table["missing"] = [
                    s["id"]
                    for s in entry.get("steps", [])
                    if s["id"] not in covered
                ]
    for spec_id, name in stale:
        data["entries"][spec_id]["clients"][name]["pipeline"] = 0
    data["repos"] = {r.name: head(config, r.name) for r in config.all_repos()}
    path.write_text(json.dumps(data, indent=1))
    print(
        f"refresh: {moved} rows moved, {changed} rechecked, {dropped} "
        f"dropped, {len(stale)} pairs to rebuild, ${cost[0]:.2f}"
    )

    left = max_usd - cost[0] if max_usd else 0.0
    if stale and max_usd and left <= 0:
        print("cap reached, the stale pairs wait for the next run")
    elif stale:
        build(
            config,
            sorted({spec_id for spec_id, _ in stale}),
            [],
            sorted({name for _, name in stale}),
            workers,
            max_usd=left,
        )
    render_tsv(config, tsv_target(config))
