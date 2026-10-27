"""
Benchmark ways of finding where a client implements a spec function.

    uv run --project xref python xref/bench/bench.py select
    uv run --project xref python xref/bench/bench.py run a|b|c
    uv run --project xref python xref/bench/bench.py adjudicate
    uv run --project xref python xref/bench/bench.py score

A is an agent with Read, Grep and Glob and no map. B is `xref find`,
keyword ranking only. C is `xref show`, the judged map. The answer key is
pooled: every candidate any method returns is labelled by a separate
verifier that sees the code, so no method is graded on its own answers.
"""

import argparse
import json
import random
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any

from el_xref.config import Config, load
from el_xref.index import Symbol, index_path, read_body
from el_xref.index import load as load_symbols
from el_xref.link import CONFIDENT, load_map
from el_xref.retrieve import Corpus, spec_query

Json = dict[str, Any]

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
CONFIG = HERE.parent / "xref.toml"
CLIENTS = ("geth", "reth", "nethermind")
MODEL = "claude-opus-5-5"
METHODS = ("a", "b", "c", "e")
WORKERS = 4

# Questions per spec module, in proportion to the judged map.
PER_MODULE = {
    "vm.gas": 12,
    "transactions": 8,
    "fork": 8,
    "vm.instructions.system": 5,
    "vm.eoa_delegation": 3,
    "vm.interpreter": 2,
    "vm.instructions.storage": 2,
}


def save(name: str, data: Any) -> None:
    """Write `data` to results/`name`.json."""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1))


def read(name: str) -> Any:
    """Read results/`name`.json."""
    return json.loads((OUT / f"{name}.json").read_text())


def spec_by_id(config: Config) -> dict[str, Symbol]:
    """Return the spec functions by id."""
    path = index_path(config, config.spec.repo)
    return {s.id: s for s in load_symbols(path)}


def client_symbols(config: Config, client: str) -> list[Symbol]:
    """Return every indexed function of `client`."""
    symbols: list[Symbol] = []
    for repo in config.clients[client].repos:
        symbols.extend(load_symbols(index_path(config, repo)))
    return symbols


def key(item: Json) -> tuple[str, str, str]:
    """Return the identity of a candidate function."""
    return (item["repo"], item["path"], item["qualname"])


def candidate(symbol: Symbol) -> Json:
    """Describe a client function as a candidate."""
    return {
        "repo": symbol.repo,
        "path": symbol.path,
        "qualname": symbol.qualname,
        "line": symbol.line,
        "end_line": symbol.end_line,
    }


def select(config: Config) -> None:
    """Pick the questions, spread evenly through each module."""
    entries = load_map(config)["entries"]
    specs = spec_by_id(config)
    questions = []
    for module, count in PER_MODULE.items():
        ids = sorted(
            i for i in entries if i.rsplit(".", 1)[0] == module and i in specs
        )
        step = len(ids) / count
        questions += [ids[int(n * step)] for n in range(count)]
    save("questions", questions)
    print(f"{len(questions)} questions")


# B and C need no model, so they run first and fast.


def run_b(config: Config, questions: list[str]) -> Json:
    """Rank each client's functions by keywords, the top 5 per question."""
    specs = spec_by_id(config)
    out: Json = {}
    for client in CLIENTS:
        started = time.monotonic()
        corpus = Corpus.build(config, config.clients[client])
        built = time.monotonic() - started
        for spec_id in questions:
            started = time.monotonic()
            hits = corpus.search(spec_query(specs[spec_id]), 5)
            out[f"{spec_id}|{client}"] = {
                "answers": [candidate(s) for s, _ in hits],
                "seconds": time.monotonic() - started,
                "setup_seconds": built,
            }
    return out


def run_c(config: Config, questions: list[str]) -> Json:
    """Read each answer from the judged map, as `xref show` does."""
    entries = load_map(config)["entries"]
    out: Json = {}
    for spec_id in questions:
        for client in CLIENTS:
            result = entries.get(spec_id, {}).get("clients", {}).get(client)
            if result is None:
                out[f"{spec_id}|{client}"] = {"judged": False, "answers": []}
                continue
            matches = [
                m for m in result["matches"] if m["confidence"] in CONFIDENT
            ]
            out[f"{spec_id}|{client}"] = {
                "judged": True,
                "answers": [
                    {k: m[k] for k in ("repo", "path", "qualname", "line")}
                    | {"end_line": m["end_line"]}
                    for m in matches
                ],
                "cost_usd": result.get("cost_usd", 0.0),
            }
    return out


def run_e(config: Config, questions: list[str]) -> Json:
    """Read each answer from the step tables `xref build` wrote."""
    tree = json.loads((config.data / "tree.json").read_text())["entries"]
    out: Json = {}
    for spec_id in questions:
        for client in CLIENTS:
            table = tree.get(spec_id, {}).get("clients", {}).get(client)
            if not table or "matches" not in table:
                out[f"{spec_id}|{client}"] = {"error": "not built"}
                continue
            out[f"{spec_id}|{client}"] = {
                "answers": [
                    {k: m[k] for k in ("repo", "path", "qualname", "line")}
                    | {"end_line": m["end_line"]}
                    for m in table["matches"]
                    if m["confidence"] in CONFIDENT
                ],
                "seconds": table.get("search_seconds", 0.0),
                "cost_usd": table.get("cost_usd", 0.0),
            }
    return out


# A: an agent answering from the client code alone.

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "qualname": {"type": "string"},
                    "line": {"type": "integer"},
                    "relation": {"enum": ["equivalent", "partial"]},
                },
                "required": ["repo", "path", "qualname", "line", "relation"],
            },
        }
    },
    "required": ["matches"],
}

AGENT_PROMPT = """\
Find the functions in the {client} execution client that implement this \
function from the Ethereum execution specs (EELS, the {fork} fork).

The client's code is in these directories of the current folder: {repos}. \
Search only there.

Answer with the client functions that do the same job, best match first. \
`equivalent` means the function does what the spec function does, \
`partial` means it does part of it or the logic is inlined in a larger \
function. `repo` is the directory name, `path` is relative to it, \
`qualname` is the function or Type.method name and `line` is where it \
starts. Return an empty list if the client has no counterpart.

Spec function {spec_id} ({path}:{line}):

```python
{body}
```
"""


def ask_agent(config: Config, spec: Symbol, client: str) -> Json:
    """Run one agent question and time it."""
    repos = [r.name for r in config.clients[client].repos]
    prompt = AGENT_PROMPT.format(
        client=client,
        fork=config.spec.fork,
        repos=", ".join(f"{r}/" for r in repos),
        spec_id=spec.id,
        path=spec.path,
        line=spec.line,
        body=read_body(config, spec),
    )
    command = [
        "claude", "-p", "--model", MODEL, "--effort", "medium",
        "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
        "--permission-mode", "dontAsk", "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json",
        "--json-schema", json.dumps(AGENT_SCHEMA),
    ]  # fmt: skip
    started = time.monotonic()
    result = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=900,
        cwd=config.repo_cache,
        check=False,
    )
    seconds = time.monotonic() - started
    try:
        reply = json.loads(result.stdout)
        answers = reply["structured_output"]["matches"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"error": result.stderr[-300:], "seconds": seconds}
    return {
        "answers": answers,
        "seconds": seconds,
        "turns": reply.get("num_turns"),
        "cost_usd": reply.get("total_cost_usd"),
    }


def run_a(config: Config, questions: list[str]) -> Json:
    """Ask the agent every question, a few at a time."""
    specs = spec_by_id(config)
    out: Json = read("a") if (OUT / "a.json").exists() else {}
    todo = [
        (q, c)
        for q in questions
        for c in CLIENTS
        if "answers" not in out.get(f"{q}|{c}", {})
    ]

    def one(item: tuple[str, str]) -> tuple[str, Json]:
        spec_id, client = item
        return f"{spec_id}|{client}", ask_agent(config, specs[spec_id], client)

    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (name, answer) in enumerate(pool.map(one, todo), 1):
            out[name] = answer
            save("a", out)
            print(f"{done}/{len(todo)} {name} {answer.get('seconds', 0):.0f}s")
    return out


# The answer key: a verifier labels every pooled candidate.

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relation": {
                        "enum": ["equivalent", "partial", "unrelated"]
                    },
                    "note": {"type": "string"},
                },
                "required": ["id", "relation", "note"],
            },
        }
    },
    "required": ["labels"],
}

VERIFY_SYSTEM = """\
You check whether execution client functions implement a function from \
the Ethereum execution specs. For each numbered candidate decide: \
`equivalent` if it does the spec function's job, `partial` if it does a \
real part of it (the logic is split across functions or inlined in a \
larger one), `unrelated` otherwise, including functions that only call \
it, sit next to it, or do the opposite (a refund is not a charge). Judge \
from the code, not the names. Label every candidate."""


def resolve(symbols: list[Symbol], answer: Json) -> Symbol | None:
    """Match an agent's answer to an indexed client function."""
    in_file = [
        s
        for s in symbols
        if s.repo == answer["repo"] and s.path == answer["path"]
    ]
    for s in in_file:
        if s.line <= answer.get("line", 0) <= s.end_line:
            return s
    name = answer["qualname"].split(".")[-1]
    named = [s for s in in_file if s.name == name]
    return named[0] if named else None


def pooled(config: Config, questions: list[str]) -> Json:
    """Gather every method's candidates per question, as indexed functions."""
    runs = {m: read(m) for m in METHODS}
    pool: Json = {}
    for client in CLIENTS:
        symbols = client_symbols(config, client)
        by_key = {(s.repo, s.path, s.qualname): s for s in symbols}
        for spec_id in questions:
            name = f"{spec_id}|{client}"
            found: dict[tuple[str, str, str], Symbol] = {}
            for run in runs.values():
                for answer in run.get(name, {}).get("answers", []):
                    symbol = by_key.get(key(answer)) or resolve(
                        symbols, answer
                    )
                    if symbol is not None:
                        found[(symbol.repo, symbol.path, symbol.qualname)] = (
                            symbol
                        )
            pool[name] = [candidate(s) for s in found.values()]
    return pool


def adjudicate(config: Config, questions: list[str]) -> None:
    """Label every pooled candidate with the verifier."""
    specs = spec_by_id(config)
    pool = pooled(config, questions)
    labels: Json = read("gold") if (OUT / "gold.json").exists() else {}

    def fresh(name: str) -> list[Json]:
        seen = {key(c) for c in labels.get(name, {}).get("candidates", [])}
        return [c for c in pool[name] if key(c) not in seen]

    todo = [n for n in pool if fresh(n)]
    symbols = {c: client_symbols(config, c) for c in CLIENTS}

    def one(name: str) -> tuple[str, Json]:
        new = fresh(name)
        spec_id, client = name.split("|")
        spec = specs[spec_id]
        by_key = {(s.repo, s.path, s.qualname): s for s in symbols[client]}
        parts = [
            f"Spec function {spec.id} ({spec.path}:{spec.line}):",
            f"```python\n{read_body(config, spec)}\n```",
            f"Candidates from the {client} client:",
        ]
        for i, item in enumerate(new):
            body = read_body(config, by_key[key(item)])
            parts.append(
                f"[{i}] {item['qualname']} - {item['repo']}/{item['path']}:"
                f"{item['line']}\n```\n{body[:6000]}\n```"
            )
        command = [
            "claude", "-p", "--model", MODEL, "--effort", "high",
            "--tools", "", "--strict-mcp-config",
            "--no-session-persistence", "--output-format", "json",
            "--system-prompt", VERIFY_SYSTEM,
            "--json-schema", json.dumps(VERIFY_SCHEMA),
        ]  # fmt: skip
        result = subprocess.run(
            command,
            input="\n\n".join(parts),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        try:
            reply = json.loads(result.stdout)
            got = {
                label["id"]: label
                for label in reply["structured_output"]["labels"]
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            # Keep earlier labels when the verifier call fails.
            return name, {
                **labels.get(name, {}),
                "error": result.stderr[-300:],
            }
        before = labels.get(name, {})
        return name, {
            "candidates": before.get("candidates", [])
            + [
                item
                | {
                    "relation": got.get(i, {}).get("relation", "unlabelled"),
                    "note": got.get(i, {}).get("note", ""),
                }
                for i, item in enumerate(new)
            ],
            "cost_usd": float(before.get("cost_usd") or 0)
            + float(reply.get("total_cost_usd") or 0),
        }

    with ThreadPoolExecutor(WORKERS) as runner:
        for done, (name, label) in enumerate(runner.map(one, todo), 1):
            labels[name] = label
            save("gold", labels)
            print(f"{done}/{len(todo)} {name}")
    for name in pool:
        labels.setdefault(name, {"candidates": []})
    save("gold", labels)


# Scoring.


def score(questions: list[str]) -> None:
    """Write results/report.md comparing the methods."""
    gold = read("gold")
    runs = {m: read(m) for m in METHODS}
    lines = [
        "# xref benchmark",
        "",
        f"{len(questions)} spec functions x {len(CLIENTS)} clients "
        f"({', '.join(CLIENTS)}). The answer key pools every method's "
        "candidates and has a separate verifier label each one.",
        "",
        "| Method | Client | Questions | Top-1 right | Recall | "
        "Precision | Median time | Median turns | Cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for method, label in (
        ("a", "A, grep agent"),
        ("b", "B, xref find (top 5)"),
        ("c", "C, judged map"),
        ("e", "E, step tables"),
    ):
        for client in CLIENTS:
            tally: Counter[str] = Counter()
            seconds: list[float] = []
            turns: list[int] = []
            cost = 0.0
            for spec_id in questions:
                name = f"{spec_id}|{client}"
                result = runs[method].get(name, {})
                if method == "c" and not result.get("judged"):
                    continue
                if "answers" not in result:
                    tally["errors"] += 1
                    continue
                relevant = {
                    key(c)
                    for c in gold.get(name, {}).get("candidates", [])
                    if c["relation"] in ("equivalent", "partial")
                }
                answers = [key(a) for a in result["answers"]]
                hits = [a for a in answers if a in relevant]
                tally["questions"] += 1
                if relevant:
                    tally["with answer"] += 1
                    tally["top1"] += bool(answers) and answers[0] in relevant
                    tally["found"] += len(set(hits))
                    tally["relevant"] += len(relevant)
                tally["returned"] += len(answers)
                tally["correct"] += len(hits)
                seconds.append(result.get("seconds", 0.0))
                turns.append(result.get("turns") or 0)
                cost += float(result.get("cost_usd") or 0.0)
            if not tally["questions"]:
                continue

            def pct(part: int, whole: int) -> str:
                return f"{100 * part / whole:.0f}%" if whole else "-"

            lines.append(
                f"| {label} | {client} | {tally['questions']} | "
                f"{pct(tally['top1'], tally['with answer'])} | "
                f"{pct(tally['found'], tally['relevant'])} | "
                f"{pct(tally['correct'], tally['returned'])} | "
                f"{median(seconds):.1f}s | "
                f"{median(turns) if method == 'a' else '-'} | "
                f"${cost:.2f} |"
            )
    verify_cost = sum(float(g.get("cost_usd") or 0) for g in gold.values())
    sample = [
        (name, c)
        for name, g in sorted(gold.items())
        for c in g.get("candidates", [])
        if c["relation"] != "unrelated"
    ]
    random.Random(1).shuffle(sample)
    lines += [
        "",
        f"Verifier cost: ${verify_cost:.2f}. C's cost is what judging those "
        "entries cost once, after that a lookup is free. B and C times "
        "exclude loading the index.",
        "",
        "## Labels to spot-check",
        "",
    ]
    for name, c in sample[:10]:
        lines.append(
            f"- {name}: `{c['qualname']}` ({c['repo']}/{c['path']}:"
            f"{c['line']}) is {c['relation']}. {c['note']}"
        )
    (OUT / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    """Run one stage of the benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["select", "run", "adjudicate", "score"]
    )
    parser.add_argument("method", nargs="?", choices=list(METHODS))
    args = parser.parse_args()
    config = load(CONFIG)
    if args.stage == "select":
        select(config)
        return
    questions = read("questions")
    if args.stage == "run":
        runner = {"a": run_a, "b": run_b, "c": run_c, "e": run_e}[args.method]
        save(args.method, runner(config, questions))
    elif args.stage == "adjudicate":
        adjudicate(config, questions)
    else:
        score(questions)


if __name__ == "__main__":
    main()
