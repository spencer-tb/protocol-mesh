"""
Link spec functions to client functions and keep the result in `map.json`.

Linking is incremental. A previous answer is reused while the spec
function and every matched client function keep their content hash, so a
nightly run only pays for what changed.
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Client, Config, Json
from .index import Symbol, index_path, load, read_body
from .judge import Verdict, ask, render
from .retrieve import Corpus, spec_query

CANDIDATES = 15
HINT_CANDIDATES = 8
KEEP_FROM_FIRST_ROUND = 4
LONG_LINES = 150
SHORT_LINES = 50
LONG_COUNT = 5
SPEC_LINES = 200
CONFIDENT = {"high", "medium"}


@dataclass
class Settings:
    """Judge settings from the command line or `xref.toml`."""

    model: str = "claude-opus-5-5"
    effort: str = "medium"
    jobs: int = 4
    retry_none: bool = False
    max_calls: int = 0


def map_path(config: Config) -> Path:
    """Return the location of the map."""
    return config.data / "map.json"


def load_map(config: Config) -> Json:
    """Read the map, or return an empty one."""
    path = map_path(config)
    if path.exists():
        data: Json = json.loads(path.read_text())
        return data
    return {"entries": {}}


def save_map(config: Config, data: Json) -> None:
    """Write the map with stable key order."""
    path = map_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def head(config: Config, repo_name: str) -> str:
    """Return the checked out commit of a cached repo."""
    return subprocess.run(
        ["git", "-C", str(config.repo_cache / repo_name), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def location(symbol: Symbol) -> Json:
    """Return the stored form of a matched client function."""
    return {
        "repo": symbol.repo,
        "path": symbol.path,
        "qualname": symbol.qualname,
        "line": symbol.line,
        "end_line": symbol.end_line,
        "hash": symbol.hash,
    }


def still_valid(
    previous: Json, spec: Symbol, by_key: dict[tuple[str, str, str], Symbol]
) -> Json | None:
    """Return `previous` with refreshed lines if nothing it rests on moved."""
    if previous.get("spec_hash") != spec.hash:
        return None
    refreshed = []
    for match in previous.get("matches", []):
        current = by_key.get((match["repo"], match["path"], match["qualname"]))
        if current is None or current.hash != match["hash"]:
            return None
        refreshed.append({**match, **location(current)})
    return {**previous, "matches": refreshed}


def hint_candidates(
    config: Config,
    client: Client,
    corpus: Corpus,
    hints: list[str],
    shown: set[str],
) -> list[Symbol]:
    """Find client functions named by, or containing, the judge's hints."""
    found: dict[str, Symbol] = {}
    spans: dict[tuple[str, str], list[Symbol]] = {}
    for symbol in corpus.symbols:
        spans.setdefault((symbol.repo, symbol.path), []).append(symbol)

    def add(symbol: Symbol) -> None:
        if symbol.id not in shown and len(found) < HINT_CANDIDATES:
            found.setdefault(symbol.id, symbol)

    for hint in hints:
        needle = hint.strip().strip("`()").split("(")[0]
        if len(needle) < 3:
            continue
        lowered = needle.lower()
        for symbol in corpus.symbols:
            if symbol.name.lower() == lowered.rsplit(".", 1)[-1]:
                add(symbol)
        for symbol in corpus.symbols:
            if lowered in symbol.qualname.lower():
                add(symbol)
        for repo in client.repos:
            grep = subprocess.run(
                [
                    "git",
                    "-C",
                    str(config.repo_path(repo)),
                    "grep",
                    "-n",
                    "-i",
                    "-F",
                    needle,
                    "--",
                    *repo.paths,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in grep.stdout.splitlines()[:200]:
                path, number, _ = line.split(":", 2)
                for symbol in spans.get((repo.name, path), []):
                    if symbol.line <= int(number) <= symbol.end_line:
                        add(symbol)
    return list(found.values())


def judge_pair(
    config: Config,
    client: Client,
    corpus: Corpus,
    spec: Symbol,
    settings: Settings,
) -> tuple[Json, list[str]]:
    """Retrieve, judge, and if needed judge again on the judge's hints."""
    lang = client.repos[0].lang
    spec_body = "\n".join(read_body(config, spec).splitlines()[:SPEC_LINES])
    ranked = [s for s, _ in corpus.search(spec_query(spec), CANDIDATES)]
    retrieved = [s.id for s in ranked]

    def run(candidates: list[Symbol]) -> Verdict:
        prompt = render(
            client.name,
            lang,
            spec,
            spec_body,
            [(s, read_body(config, s)) for s in candidates],
            LONG_LINES,
            SHORT_LINES,
            LONG_COUNT,
        )
        return ask(prompt, settings.model, settings.effort)

    candidates = ranked
    verdict = run(candidates)
    cost = verdict.cost
    rounds = 1
    confident = [m for m in verdict.matches if m["confidence"] in CONFIDENT]
    if not confident and verdict.hints:
        extra = hint_candidates(
            config, client, corpus, verdict.hints, {s.id for s in ranked}
        )
        if extra:
            candidates = ranked[:KEEP_FROM_FIRST_ROUND] + extra
            hints = verdict.hints
            verdict = run(candidates)
            verdict.hints = verdict.hints or hints
            cost += verdict.cost
            rounds = 2

    matches = []
    for match in verdict.matches:
        index = match["candidate"]
        if not 0 <= index < len(candidates):
            continue
        matches.append(
            {
                **location(candidates[index]),
                "relation": match["relation"],
                "confidence": match["confidence"],
                "note": match["note"],
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    matches.sort(key=lambda m: (order[m["confidence"]], m["relation"]))
    entry = {
        "spec_hash": spec.hash,
        "matches": matches,
        "hints": verdict.hints,
        "model": settings.model,
        "effort": settings.effort,
        "rounds": rounds,
        "cost_usd": round(cost, 4),
        "judged_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return entry, retrieved


def select_specs(
    specs: list[Symbol], modules: list[str], only: list[str]
) -> list[Symbol]:
    """Filter spec functions by module prefix or exact id."""
    chosen = specs
    if modules:
        chosen = [
            s
            for s in chosen
            if any(s.id == m or s.id.startswith(m + ".") for m in modules)
        ]
    if only:
        chosen = [s for s in chosen if s.id in only]
    return chosen


def link(
    config: Config,
    clients: list[str],
    modules: list[str],
    only: list[str],
    settings: Settings,
    dry_run: bool = False,
) -> None:
    """Fill or refresh `map.json` for the chosen spec functions and clients."""
    specs = select_specs(
        load(index_path(config, config.spec.repo)), modules, only
    )
    data = load_map(config)
    entries = data.setdefault("entries", {})
    data["spec"] = {
        "repo": config.spec.repo.name,
        "url": config.spec.repo.url,
        "sha": head(config, config.spec.repo.name),
        "fork": config.spec.fork,
    }
    data.setdefault("repos", {})[config.spec.repo.name] = {
        "url": config.spec.repo.url,
        "sha": head(config, config.spec.repo.name),
    }

    jobs = []
    corpora: dict[str, Corpus] = {}
    for name in clients or list(config.clients):
        client = config.clients[name]
        for repo in client.repos:
            data["repos"][repo.name] = {
                "url": repo.url,
                "sha": head(config, repo.name),
            }
        corpus = Corpus.build(config, client)
        corpora[name] = corpus
        by_key = {(s.repo, s.path, s.qualname): s for s in corpus.symbols}
        for spec in specs:
            entry = entries.setdefault(spec.id, {"clients": {}})
            entry.update(
                {
                    "path": spec.path,
                    "line": spec.line,
                    "end_line": spec.end_line,
                }
            )
            previous = entry["clients"].get(name)
            if previous:
                kept = still_valid(previous, spec, by_key)
                if kept and (kept["matches"] or not settings.retry_none):
                    entry["clients"][name] = kept
                    continue
            jobs.append((client, spec))

    print(
        f"{len(specs)} spec functions, {len(jobs)} judge calls needed",
        flush=True,
    )
    if settings.max_calls and len(jobs) > settings.max_calls:
        print(f"capped at {settings.max_calls}, the rest wait for next run")
        jobs = jobs[: settings.max_calls]
    if dry_run or not jobs:
        save_map(config, data)
        return

    debug = config.data / "index" / "retrieved.jsonl"
    total = 0.0
    done = 0
    with ThreadPoolExecutor(max_workers=settings.jobs) as pool:
        futures = {
            pool.submit(
                judge_pair,
                config,
                client,
                corpora[client.name],
                spec,
                settings,
            ): (client, spec)
            for client, spec in jobs
        }
        for future in as_completed(futures):
            client, spec = futures[future]
            done += 1
            try:
                entry, retrieved = future.result()
            except Exception as error:  # noqa: BLE001 - keep going
                print(
                    f"[{done}/{len(jobs)}] {client.name} {spec.id}: {error}",
                    flush=True,
                )
                continue
            total += entry["cost_usd"]
            entries[spec.id]["clients"][client.name] = entry
            best = entry["matches"][0]["qualname"] if entry["matches"] else "-"
            print(
                f"[{done}/{len(jobs)}] {client.name:<10} {spec.id:<48} "
                f"{best}  (${entry['cost_usd']:.3f})",
                flush=True,
            )
            with debug.open("a") as out:
                record = {"client": client.name, "spec": spec.id}
                out.write(
                    json.dumps({**record, "retrieved": retrieved}) + "\n"
                )
            if done % 10 == 0:
                save_map(config, data)
    save_map(config, data)
    print(f"spent ${total:.2f}")
