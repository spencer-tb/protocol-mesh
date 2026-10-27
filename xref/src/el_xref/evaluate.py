"""
Score `map.json` against the hand-checked answers in `eval/gold.toml`.

A gold location counts as found when a map match names the same repo
and path and its line range covers the gold line.
"""

import json
import tomllib
from collections import Counter

from .config import Config, Json
from .index import Symbol, index_path, load
from .link import CONFIDENT, load_map


def covers(match: Json, gold: Json) -> bool:
    """Return whether `match` points at the gold location."""
    return bool(
        match["repo"] == gold["repo"]
        and match["path"] == gold["path"]
        and match["line"] <= gold["line"] <= match["end_line"]
    )


def evaluate(config: Config) -> None:
    """Print retrieval recall, judge recall and judge precision."""
    gold = tomllib.loads((config.root / "eval" / "gold.toml").read_text())
    data = load_map(config)
    retrieved: dict[tuple[str, str], list[str]] = {}
    debug = config.data / "index" / "retrieved.jsonl"
    if debug.exists():
        for line in debug.read_text().splitlines():
            record = json.loads(line)
            retrieved[(record["spec"], record["client"])] = record["retrieved"]

    spans = {}
    for client in config.clients.values():
        for repo in client.repos:
            if index_path(config, repo).exists():
                for symbol in load(index_path(config, repo)):
                    spans[symbol.id] = location_of(symbol)

    tally: Counter[str] = Counter()
    for item in gold["entry"]:
        key = (item["spec"], item["client"])
        result = data["entries"].get(item["spec"], {}).get("clients", {})
        result = result.get(item["client"])
        if result is None:
            tally["not judged"] += 1
            continue
        confident = [
            m for m in result["matches"] if m["confidence"] in CONFIDENT
        ]
        if item["relation"] == "none" or not item["locations"]:
            tally["gold none"] += 1
            if confident:
                tally["false positive on none"] += 1
                print(
                    f"FP   {item['client']:<10} {item['spec']}: "
                    f"{confident[0]['qualname']}"
                )
            continue

        tally["gold pairs"] += 1
        primary: Json = item["locations"][0]
        hit_primary = any(covers(m, primary) for m in confident)
        hit_any = any(
            covers(m, g) for m in confident for g in item["locations"]
        )
        tally["primary found"] += hit_primary
        tally["any found"] += hit_any
        if retrieved.get(key):
            in_top = any(
                sid in spans and covers(spans[sid], primary)
                for sid in retrieved[key]
            )
            tally["primary retrieved"] += in_top
        for m in confident:
            tally["confident matches"] += 1
            if any(covers(m, g) for g in item["locations"]):
                tally["confident correct"] += 1
            else:
                print(
                    f"XTRA {item['client']:<10} {item['spec']}: "
                    f"{m['qualname']} ({m['relation']}, {m['confidence']})"
                )
        if not hit_any:
            got = ", ".join(m["qualname"] for m in confident) or "nothing"
            want = ", ".join(g["symbol"] for g in item["locations"])
            print(
                f"MISS {item['client']:<10} {item['spec']}: "
                f"got {got}, want {want}"
            )

    pairs = tally["gold pairs"]
    print()
    for name, value in sorted(tally.items()):
        print(f"{name:<24} {value}")
    if pairs:
        print(
            f"\nrecall (primary)         {tally['primary found'] / pairs:.0%}"
        )
        print(f"recall (any location)    {tally['any found'] / pairs:.0%}")
    if tally["confident matches"]:
        precision = tally["confident correct"] / tally["confident matches"]
        print(f"precision (per match)    {precision:.0%}")


def location_of(symbol: Symbol) -> Json:
    """Return the fields `covers` compares."""
    return {
        "repo": symbol.repo,
        "path": symbol.path,
        "line": symbol.line,
        "end_line": symbol.end_line,
    }
