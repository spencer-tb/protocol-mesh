"""
Map spec functions to client code as step tables.

For one spec function and one client:

1. Split the spec function into the steps a client has to implement. This
   is done once per spec function and shared by every client.
2. An agent searches the client for the code behind each step.
3. The candidates grow with the functions those call, two levels down, and
   the functions tagged with the same EIPs.
4. A judge reads every candidate and assigns it to steps, or rejects it.
5. A step nobody covers gets one more targeted search.

Results go to `data/tree.json`. `render_tsv` writes them to `map/` as one
file per client, for agents to grep.
"""

import fnmatch
import json
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config, Json
from .index import Symbol, index_path, load, read_body
from .link import head, select_specs

MODEL = "claude-opus-5-5"
# The search only has to surface candidates, the Opus judges decide.
SEARCH_MODEL = "claude-sonnet-5-5"
TREE = "tree.json"
MAX_CANDIDATES = 20
# Built first, and its answers seed and hint the other clients.
REFERENCE = "geth"
# Bumped when the build changes, so older tables are rebuilt.
PIPELINE = 3

# Functions in the fork that are not protocol behaviour, so the map skips
# them: exception messages, arithmetic on a gas type, hex helpers and hooks
# only tooling uses, and repackaging with no rule in it. Settled by two
# independent checks that agreed on all 13.
NOT_SPEC = (
    "exceptions.*.__init__",
    "vm.exceptions.*.__init__",
    "fork_types.StateGasPerByte.*",
    "utils.hexadecimal.*",
    "fork.apply_fork",
    "transactions.get_transaction_hash",
    "state_tracker.extract_block_diff",
    "blocks.decode_receipt",
)


def is_spec(symbol: Symbol) -> bool:
    """Return whether `symbol` is protocol behaviour worth mapping."""
    return not any(fnmatch.fnmatch(symbol.id, p) for p in NOT_SPEC)


MAX_BODY = 2500

# Calls too generic to follow.
SKIP_CALLS = {"len", "append", "make", "new", "panic", "uint64", "int"}


def claude(
    prompt: str,
    schema: Json,
    system: str = "",
    tools: str = "",
    cwd: Path | None = None,
    effort: str = "medium",
    model: str = MODEL,
) -> tuple[Json, float]:
    """Run one structured `claude -p` call and return its output and cost."""
    command = [
        "claude", "-p", "--model", model, "--effort", effort,
        "--tools", tools, "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json",
        "--json-schema", json.dumps(schema),
    ]  # fmt: skip
    if tools:
        command += ["--allowedTools", tools, "--permission-mode", "dontAsk"]
    if system:
        command += ["--system-prompt", system]
    result = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=900,
        cwd=cwd,
        check=False,
    )
    try:
        reply = json.loads(result.stdout)
        return reply["structured_output"], float(
            reply.get("total_cost_usd") or 0.0
        )
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"claude failed: {result.stderr[-300:] or result.stdout[-300:]}"
        ) from error


def numbered(body: str, first: int) -> str:
    """Return `body` with line numbers starting at `first`."""
    return "\n".join(
        f"{first + i:>5}  {line}" for i, line in enumerate(body.splitlines())
    )


# 1. Steps.

STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                },
                "required": ["text", "first", "last"],
            },
        }
    },
    "required": ["steps"],
}

STEPS_PROMPT = """\
Split this function from the Ethereum execution specs into the steps an \
execution client has to implement: 1 to 6 steps, each a short phrase \
saying what it computes, checks or changes, with its first and last spec \
line. Leave out type conversions, logging and docstrings. A function that \
does one thing is one step.

{spec_id} ({path}):

```python
{body}
```
"""


def spec_steps(
    config: Config, spec: Symbol, cache: Json
) -> tuple[list[Json], float]:
    """Return the steps of `spec`, cached while its code is unchanged."""
    cached = cache.get(spec.id)
    if cached and cached["hash"] == spec.hash:
        return cached["steps"], 0.0
    body = numbered(read_body(config, spec), spec.line)
    out, cost = claude(
        STEPS_PROMPT.format(spec_id=spec.id, path=spec.path, body=body),
        STEPS_SCHEMA,
        effort="low",
    )
    steps = [{"id": i, **step} for i, step in enumerate(out["steps"])]
    cache[spec.id] = {"hash": spec.hash, "steps": steps}
    return steps, cost


# EIP tags: which EIPs a spec function implements, for functions whose
# code never names the EIP. Merged with the tags the index reads from text.

EIPS_SCHEMA = {
    "type": "object",
    "properties": {"eips": {"type": "array", "items": {"type": "integer"}}},
    "required": ["eips"],
}

EIPS_PROMPT = """\
Which EIPs does this function from the Ethereum execution specs, the \
{fork} fork, implement? List only EIPs whose rules its own code carries \
out, as numbers. Return an empty list for generic plumbing.

{spec_id} ({path}):

```python
{body}
```
"""


def spec_eips(
    config: Config, spec: Symbol, cache: Json
) -> tuple[list[int], float]:
    """Return the EIPs `spec` implements, cached while its code holds."""
    cached = cache.get(spec.id)
    if cached and cached["hash"] == spec.hash:
        return list(cached["eips"]), 0.0
    out, cost = claude(
        EIPS_PROMPT.format(
            fork=config.spec.fork,
            spec_id=spec.id,
            path=spec.path,
            body=read_body(config, spec),
        ),
        EIPS_SCHEMA,
        effort="low",
    )
    eips = sorted(set(out["eips"]))
    cache[spec.id] = {"hash": spec.hash, "eips": eips}
    return eips, cost


def tag(
    config: Config, only: list[str], modules: list[str], workers: int
) -> None:
    """Tag spec functions with EIPs, by default every function in the map."""
    path = tree_path(config)
    data: Json = json.loads(path.read_text()) if path.exists() else {}
    every = load(index_path(config, config.spec.repo))
    specs = select_specs(every, modules, only)
    if not (only or modules):
        built = set(data.get("entries", {}))
        specs = [s for s in specs if s.id in built]
    cache = data.setdefault("eips", {})

    def one(spec: Symbol) -> tuple[Symbol, list[int], float]:
        eips, cost = spec_eips(config, spec, cache)
        return spec, eips, cost

    total = 0.0
    with ThreadPoolExecutor(workers) as pool:
        for spec, eips, cost in pool.map(one, specs):
            total += cost
            print(f"{spec.id} {', '.join(map(str, eips)) or '-'}")
    path.write_text(json.dumps(data, indent=1))
    print(f"{len(specs)} functions, ${total:.2f}")


# 2. Agent search.

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "qualname": {"type": "string"},
                    "line": {"type": "integer"},
                },
                "required": ["repo", "path", "qualname", "line"],
            },
        }
    },
    "required": ["locations"],
}

SEARCH_PROMPT = """\
Find the functions in the {client} execution client that implement this \
function from the Ethereum execution specs, the {fork} fork. The client's \
code is in these directories of the current folder: {repos}. Search only \
there.

The spec function does these steps:
{steps}

Find the client code for every step. A client often splits the work over \
several functions, inlines a step in a bigger function, or does a step \
somewhere the main function never calls, so keep looking until each step \
is accounted for or you are sure the client lacks it. List every function \
involved, the main one first. `repo` is the directory name, `path` is \
relative to it, `qualname` is the function or Type.method name and `line` \
is where it starts.{focus}

{spec_id} ({path}:{line}):

```python
{body}
```
"""


def search(
    config: Config,
    spec: Symbol,
    steps: list[Json],
    client: str,
    focus: list[Json] | None = None,
    hints: str = "",
) -> tuple[list[Json], float, float]:
    """Run the agent search and return locations, cost and seconds."""
    hint = hints
    if focus:
        hint += "\n\nA first pass found no code for these steps, look for " + (
            "them specifically: "
            + "; ".join(f"[{s['id']}] {s['text']}" for s in focus)
        )
    prompt = SEARCH_PROMPT.format(
        client=client,
        fork=config.spec.fork,
        repos=", ".join(f"{r.name}/" for r in config.clients[client].repos),
        steps="\n".join(f"[{s['id']}] {s['text']}" for s in steps),
        focus=hint,
        spec_id=spec.id,
        path=spec.path,
        line=spec.line,
        body=read_body(config, spec),
    )
    started = time.monotonic()
    out, cost = claude(
        prompt,
        SEARCH_SCHEMA,
        tools="Read,Grep,Glob",
        cwd=config.repo_cache,
        model=SEARCH_MODEL,
    )
    return out["locations"], cost, time.monotonic() - started


# 3. More candidates: the calls and the EIP tags.


class Client:
    """Every indexed function of one client, with lookups."""

    def __init__(self, config: Config, name: str) -> None:
        """Load the index of every repo of client `name`."""
        self.symbols: list[Symbol] = []
        for repo in config.clients[name].repos:
            self.symbols.extend(load(index_path(config, repo)))
        self.by_key = {(s.repo, s.path, s.qualname): s for s in self.symbols}
        self.by_name: dict[str, list[Symbol]] = defaultdict(list)
        for s in self.symbols:
            self.by_name[s.name].append(s)

    def resolve(self, location: Json) -> Symbol | None:
        """Return the indexed function an agent's answer points at."""
        exact = self.by_key.get(
            (location["repo"], location["path"], location["qualname"])
        )
        if exact:
            return exact
        in_file = [
            s
            for s in self.symbols
            if s.repo == location["repo"] and s.path == location["path"]
        ]
        for s in in_file:
            if s.line <= location.get("line", 0) <= s.end_line:
                return s
        name = location["qualname"].split(".")[-1]
        named = [s for s in in_file if s.name == name]
        return named[0] if named else None

    def callees(self, symbol: Symbol) -> list[Symbol]:
        """Return the functions `symbol` calls, when the name is specific."""
        found = []
        for name in symbol.calls:
            if name in SKIP_CALLS or name == symbol.name:
                continue
            matches = [
                s for s in self.by_name.get(name, []) if s.repo == symbol.repo
            ]
            if 0 < len(matches) <= 3:
                found.extend(matches)
        return found

    def tagged(self, eips: list[str]) -> list[Symbol]:
        """Return functions sharing EIP tags, the most shared first."""
        wanted = set(eips)
        scored = [
            (len(wanted & set(s.eips)), s)
            for s in self.symbols
            if wanted & set(s.eips)
        ]
        scored.sort(key=lambda pair: -pair[0])
        return [s for _, s in scored[:4]]


def candidates(
    client: Client, spec: Symbol, seeds: list[Symbol]
) -> list[Symbol]:
    """Return the seeds, what they call two levels down and EIP-tagged code."""
    chosen: dict[str, Symbol] = {s.id: s for s in seeds}
    frontier = list(seeds)
    for _ in range(2):
        nxt = []
        for symbol in frontier:
            for callee in client.callees(symbol):
                if callee.id not in chosen:
                    chosen[callee.id] = callee
                    nxt.append(callee)
        frontier = nxt
    for symbol in client.tagged(spec.eips):
        chosen.setdefault(symbol.id, symbol)
    return list(chosen.values())[:MAX_CANDIDATES]


# 4. Two independent judges, and a third for what they disagree on.

IMPLEMENTS = {"entry", "part", "inlined"}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "role": {
                        "enum": [
                            "entry",
                            "part",
                            "inlined",
                            "delegated",
                            "unrelated",
                        ]
                    },
                    "steps": {"type": "array", "items": {"type": "integer"}},
                    "delegated_to": {"type": "string"},
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                    "quote": {"type": "string"},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": [
                    "id",
                    "role",
                    "steps",
                    "delegated_to",
                    "first",
                    "last",
                    "quote",
                    "confidence",
                    "note",
                ],
            },
        },
        "missing": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["candidates", "missing"],
}

RULES = """\
You map a function from the Ethereum execution specs onto an execution \
client's code, judging from the code, not the names. Give every numbered \
candidate one role:

- `entry`: the client's main function for this spec function.
- `part`: its own code computes, checks or changes what one or more of \
the steps do.
- `inlined`: the steps' logic is written inside it, but it mostly does \
other work.
- `delegated`: it implements another spec function that this one calls, \
from the list given. Name that spec function in `delegated_to`.
- `unrelated`: anything else, including wrappers that only forward the \
call, callers, dispatch loops that call the logic, generic helpers, \
neighbours, and opposites such as a refund for a charge.

For entry, part and inlined, list the steps and give `first` and `last`, \
the client lines that do them, and `quote`, the most telling line. Use \
empty values otherwise. Then list in `missing` the steps no candidate \
does in its own code. Note in one sentence what the candidate does."""


def judge(
    config: Config,
    spec: Symbol,
    steps: list[Json],
    found: list[Symbol],
    callees: list[str],
    model: str = MODEL,
    effort: str = "medium",
    disputes: str = "",
) -> tuple[Json, float]:
    """Give every candidate a role and the steps it implements."""
    parts = [
        f"Spec function {spec.id} ({spec.path}:{spec.line}):",
        "```python\n" + read_body(config, spec) + "\n```",
        "Steps:",
        *(f"[{s['id']}] {s['text']}" for s in steps),
        "",
        "Spec functions it calls, for `delegated`: "
        + (", ".join(callees) or "none"),
        disputes,
        "Candidates:",
    ]
    for i, symbol in enumerate(found):
        body = numbered(read_body(config, symbol)[:MAX_BODY], symbol.line)
        parts.append(
            f"[{i}] {symbol.qualname} - {symbol.repo}/{symbol.path}:"
            f"{symbol.line}-{symbol.end_line}\n```\n{body}\n```"
        )
    return claude(
        "\n".join(parts),
        JUDGE_SCHEMA,
        system=RULES,
        effort=effort,
        model=model,
    )


def side(item: Json | None) -> str:
    """Return what a verdict says about one candidate, for comparing."""
    if item is None or item["role"] == "unrelated":
        return "unrelated"
    if item["role"] == "delegated":
        return "delegated:" + str(item["delegated_to"])
    return "implements"


def settle(
    config: Config,
    spec: Symbol,
    steps: list[Json],
    found: list[Symbol],
    callees: list[str],
) -> tuple[dict[int, Json], float, int]:
    """
    Judge twice independently and keep what either judge accepts.

    Agents read the code after the map points them at it, so a wrong row
    costs one read while a missing row costs a search. Each row records
    its votes, 2 when both judges accept it and 1 when only one does.
    """
    # The second judge sees the candidates in reverse, so neither inherits
    # the other's bias towards whatever comes first.
    first, cost_a = judge(config, spec, steps, found, callees)
    second, cost_b = judge(config, spec, steps, found[::-1], callees)
    last = len(found) - 1
    a = {c["id"]: c for c in first["candidates"]}
    b = {last - c["id"]: c for c in second["candidates"]}
    final: dict[int, Json] = {}
    split = 0
    for i in range(len(found)):
        sides = [side(a.get(i)), side(b.get(i))]
        split += sides[0] != sides[1]
        for verdict in (a.get(i), b.get(i)):
            if verdict and side(verdict) != "unrelated":
                votes = sides.count(side(verdict))
                if votes > final.get(i, {}).get("votes", 0):
                    final[i] = verdict | {"votes": votes}
    return final, cost_a + cost_b, split


def located(symbol: Symbol) -> Json:
    """Describe where a client function is."""
    return {
        "repo": symbol.repo,
        "path": symbol.path,
        "qualname": symbol.qualname,
        "line": symbol.line,
        "end_line": symbol.end_line,
        "hash": symbol.hash,
    }


def spec_callees(spec: Symbol, specs: dict[str, list[Symbol]]) -> list[str]:
    """Return the other spec functions `spec` calls."""
    found = []
    for name in spec.calls:
        for other in specs.get(name, []):
            if other.id != spec.id and other.id not in found:
                found.append(other.id)
    return found


def map_pair(
    config: Config,
    spec: Symbol,
    steps: list[Json],
    name: str,
    client: Client,
    callees: list[str],
    earlier: list[Json] | None = None,
    reference: tuple[str, list[Json]] | None = None,
) -> Json:
    """
    Build one spec function's step table for one client.

    Seeds come first from `earlier` matches and from functions named like
    the `reference` client's answers, which forks such as erigon keep. When
    those cover every step, there is no search. Otherwise the agent
    searches for the steps still uncovered, at most twice. With `earlier`
    the search is skipped entirely.
    """
    cost = seconds = 0.0
    split = 0
    seeds = [s for m in earlier or [] if (s := client.resolve(m))]
    hints = ""
    if reference and reference[1]:
        other, matches = reference
        for match in matches:
            name_only = match["qualname"].split(".")[-1]
            seeds += client.by_name.get(name_only, [])[:3]
        hints = f"\n\nIn {other}, these functions do the steps: " + "; ".join(
            f"{m['qualname']} (steps {','.join(map(str, m['steps']))})"
            for m in matches
        )
    found: list[Symbol] = []
    final: dict[int, Json] = {}

    def judge_seeds() -> None:
        nonlocal found, final, cost, split
        unique = list({s.id: s for s in seeds}.values())
        found = candidates(client, spec, unique)
        final, spent, count = settle(config, spec, steps, found, callees)
        cost += spent
        split += count

    if seeds:
        judge_seeds()
    for _ in range(0 if earlier is not None else 2):
        covered = {
            step
            for item in final.values()
            if item["role"] in IMPLEMENTS
            for step in item["steps"]
        }
        focus = [s for s in steps if s["id"] not in covered]
        if found and not focus:
            break
        locations, spent, took = search(
            config, spec, steps, name, focus if found else None, hints
        )
        cost += spent
        seconds += took
        seeds += [s for loc in locations if (s := client.resolve(loc))]
        judge_seeds()
    matches, delegated = [], []
    for i, item in final.items():
        place = located(found[i])
        if item["role"] in IMPLEMENTS:
            matches.append(
                place
                | {
                    k: item[k]
                    for k in (
                        "steps",
                        "role",
                        "confidence",
                        "note",
                        "first",
                        "last",
                        "quote",
                        "votes",
                    )
                }
            )
        elif item["role"] == "delegated":
            delegated.append(
                place | {"spec": item["delegated_to"], "votes": item["votes"]}
            )
    matches.sort(key=lambda m: (m["role"] != "entry", m["line"]))
    covered = {step for m in matches for step in m["steps"]}
    return {
        "matches": matches,
        "delegated": delegated,
        "missing": [s["id"] for s in steps if s["id"] not in covered],
        "split": split,
        "cost_usd": round(cost, 4),
        "search_seconds": round(seconds, 1),
        "model": MODEL,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def tree_path(config: Config) -> Path:
    """Return where the step tables are kept."""
    return config.data / TREE


def build(
    config: Config,
    only: list[str],
    modules: list[str],
    clients: list[str],
    workers: int,
    rejudge: bool = False,
    max_usd: float = 0.0,
) -> None:
    """
    Build step tables for the chosen spec functions and clients.

    With `rejudge`, judge existing tables again from their matches,
    without a search. With `max_usd`, start no new pair once that much is
    spent, so the pairs already running can overshoot it a little.
    """
    every = load(index_path(config, config.spec.repo))
    specs = [s for s in select_specs(every, modules, only) if is_spec(s)]
    by_name: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in every:
        by_name[symbol.name].append(symbol)
    names = clients or list(config.clients)
    path = tree_path(config)
    data: Json = json.loads(path.read_text()) if path.exists() else {}
    entries = data.setdefault("entries", {})
    steps_cache = data.setdefault("steps", {})
    data["repos"] = {r.name: head(config, r.name) for r in config.all_repos()}
    tables = {n: Client(config, n) for n in names}

    def steps_of(spec: Symbol) -> list[Json] | None:
        try:
            return spec_steps(config, spec, steps_cache)[0]
        except RuntimeError as error:
            print(f"no steps for {spec.id}: {str(error)[:200]}")
            return None

    # A function whose steps failed is left for the next run.
    with ThreadPoolExecutor(workers) as pool:
        found = list(zip(specs, pool.map(steps_of, specs), strict=True))
    specs = [spec for spec, steps in found if steps is not None]
    for spec, steps in found:
        if steps is not None:
            entries.setdefault(spec.id, {})["steps"] = steps
    path.write_text(json.dumps(data, indent=1))

    def current(spec: Symbol, n: str) -> Json:
        return dict(entries[spec.id].get("clients", {}).get(n, {}))

    if rejudge:
        todo = [(s, n) for s in specs for n in names if current(s, n)]
    else:
        todo = [
            (s, n)
            for s in specs
            for n in names
            if current(s, n).get("spec_hash") != s.hash
            or current(s, n).get("pipeline") != PIPELINE
        ]

    spent = [0.0]
    lock = threading.Lock()

    def one(item: tuple[Symbol, str]) -> tuple[Symbol, str, Json | None]:
        spec, n = item
        if max_usd and spent[0] >= max_usd:
            return spec, n, None
        earlier = current(spec, n).get("matches") if rejudge else None
        reference = None
        if n != REFERENCE:
            answers = current(spec, REFERENCE).get("matches", [])
            reference = (
                REFERENCE,
                [m for m in answers if m.get("votes", 2) == 2],
            )
        try:
            table = map_pair(
                config,
                spec,
                entries[spec.id]["steps"],
                n,
                tables[n],
                spec_callees(spec, by_name),
                earlier,
                reference,
            )
        except RuntimeError as error:
            return spec, n, {"error": str(error)[:300]}
        with lock:
            spent[0] += table.get("cost_usd", 0)
        return spec, n, table | {"spec_hash": spec.hash, "pipeline": PIPELINE}

    first = [item for item in todo if item[1] == REFERENCE]
    rest = [item for item in todo if item[1] != REFERENCE]
    done = 0
    for batch in (first, rest):
        with ThreadPoolExecutor(workers) as pool:
            for spec, n, table in pool.map(one, batch):
                done += 1
                if table is None:
                    continue
                entries[spec.id].setdefault("clients", {})[n] = table
                path.write_text(json.dumps(data, indent=1))
                print(
                    f"{done}/{len(todo)} {spec.id} {n} "
                    f"{len(table.get('matches', []))} matches "
                    f"${table.get('cost_usd', 0):.2f} "
                    f"{table.get('search_seconds', 0):.0f}s searching"
                )
    capped = (
        " (cap reached, rerun to go on)" if spent[0] >= max_usd > 0 else ""
    )
    print(f"spent ${spent[0]:.2f}{capped}")


def render_tsv(config: Config, target: Path) -> None:
    """
    Write the map as grep-able files in `target`.

    `eels.tsv` lists each EELS function's steps once. Each client gets its
    own file with one row per client function, pointing at step ids, plus
    a row for the steps it lacks.
    """
    data = json.loads(tree_path(config).read_text())
    local = {
        name: Path(where).as_posix().removeprefix("../")
        for name, where in config.checkouts.items()
    }
    target.mkdir(parents=True, exist_ok=True)
    # The EIPs each EELS function's code and docstrings name, so a grep for
    # an EIP finds its functions.
    tagged = data.get("eips", {})
    eips = {}
    for symbol in load(index_path(config, config.spec.repo)):
        numbers = {int(e.removeprefix("eip")) for e in symbol.eips}
        numbers |= set(tagged.get(symbol.id, {}).get("eips", []))
        eips[symbol.id] = ",".join(f"eip{n}" for n in sorted(numbers)) or "-"
    spec_rows = ["# eels\tstep\teips\twhat it does"]
    client_rows: dict[str, list[str]] = defaultdict(
        lambda: ["# eels\tsteps\trole\tvotes\tlocation\tfunction"]
    )
    for spec_id, entry in sorted(data["entries"].items()):
        for step in entry.get("steps", []):
            spec_rows.append(
                f"{spec_id}\t{step['id']}\t{eips.get(spec_id, '-')}\t"
                f"{step['text']}"
            )
        for client, table in sorted(entry.get("clients", {}).items()):
            rows = client_rows[client]
            for match in table.get("matches", []):
                if match["confidence"] == "low":
                    continue
                root = local.get(match["repo"], match["repo"])
                steps = ",".join(str(step) for step in match["steps"])
                rows.append(
                    f"{spec_id}\t{steps or '-'}\t{match['role']}\t"
                    f"{match.get('votes', 2)}/2\t"
                    f"{root}/{match['path']}:{match['line']}-"
                    f"{match['end_line']}\t{match['qualname']}"
                )
            for item in table.get("delegated", []):
                rows.append(
                    f"{spec_id}\t-\tdelegated\t{item.get('votes', 2)}/2\t"
                    f"-> {item['spec']}\t{item['qualname']}"
                )
            if table.get("missing"):
                missing = ",".join(str(step) for step in table["missing"])
                rows.append(f"{spec_id}\t{missing}\tmissing\t-\t-\t-")
    (target / "eels.tsv").write_text("\n".join(spec_rows) + "\n")
    for client, rows in sorted(client_rows.items()):
        (target / f"{client}.tsv").write_text("\n".join(rows) + "\n")
        print(f"{client:<12} {len(rows) - 1} rows")


def tsv_target(config: Config) -> Path:
    """Return the map folder, next to xref.toml."""
    return config.root / "map"
