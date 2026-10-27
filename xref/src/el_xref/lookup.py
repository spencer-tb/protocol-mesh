"""
Free, local lookups for debugging: `find` and `diff`.

Both read the checkouts you are working in (local clones, any branch, the
working tree by default) and use keyword retrieval only, so they make no
model calls. Answers already judged into `map.json` are shown first.
"""

import difflib
import subprocess
from collections import Counter
from collections.abc import Callable

from .checkout import Checkout, git, parsed, resolve
from .config import Client, Config, Json, Repo
from .index import Symbol, index_path, load, read_body
from .link import CONFIDENT, load_map
from .retrieve import Corpus, spec_query, symbol_tokens
from .tokens import tokenize

DIFF_LINES = 60
NEW_LINES = 30

# Maps a client function and its body to up to k likely spec ids.
SpecGuesser = Callable[[Symbol, str, int], list[str]]


def spec_symbols(config: Config) -> list[Symbol]:
    """Return the indexed spec functions."""
    return load(index_path(config, config.spec.repo))


def match_specs(specs: list[Symbol], query: str) -> list[Symbol]:
    """Return spec functions equal to, ending with, or containing query."""
    for test in (
        lambda s: s.id == query,
        lambda s: s.id.endswith("." + query),
        lambda s: query in s.id,
    ):
        hits = [s for s in specs if test(s)]
        if hits:
            return hits
    return []


def shown_path(checkout: Checkout, symbol: Symbol) -> str:
    """Return a clickable path for a worktree, `ref:path` otherwise."""
    if checkout.ref:
        return f"{checkout.ref}:{symbol.path}:{symbol.line}"
    return f"{checkout.root / symbol.path}:{symbol.line}"


def client_index(
    config: Config, client: Client, at: dict[str, str]
) -> tuple[list[Checkout], list[Symbol], dict[str, str]]:
    """Parse every repo of `client` at its chosen checkout."""
    checkouts = []
    symbols: list[Symbol] = []
    bodies: dict[str, str] = {}
    for repo in client.repos:
        checkout = resolve(config, repo, at)
        repo_symbols, repo_bodies = parsed(config, checkout)
        checkout.close()
        checkouts.append(checkout)
        symbols.extend(repo_symbols)
        bodies.update(repo_bodies)
    return checkouts, symbols, bodies


def judged(data: Json, spec: Symbol, client: str) -> list[Json]:
    """Return the confident map matches for a spec function and client."""
    entry = data.get("entries", {}).get(spec.id, {})
    result = entry.get("clients", {}).get(client)
    if not result:
        return []
    return [m for m in result["matches"] if m["confidence"] in CONFIDENT]


def find(
    config: Config,
    query: str,
    clients: list[str],
    at: dict[str, str],
    count: int,
) -> None:
    """Print each client's candidates for the spec functions matching."""
    specs = match_specs(spec_symbols(config), query)
    if not specs:
        print(f"no spec function matches {query!r}")
        return
    if len(specs) > 3:
        print(f"{len(specs)} spec functions match, narrow it down:")
        for spec in specs:
            print(f"  {spec.id}")
        return
    data = load_map(config)
    names = clients or list(config.clients)
    indexes = {
        name: client_index(config, config.clients[name], at) for name in names
    }
    for spec in specs:
        print(f"{spec.id}  ({spec.path}:{spec.line})")
        for name in names:
            checkouts, symbols, bodies = indexes[name]
            by_repo = {c.repo.name: c for c in checkouts}
            labels = ", ".join(c.label() for c in checkouts)
            print(f"\n  {name}  [{labels}]")
            by_key = {(s.repo, s.path, s.qualname): s for s in symbols}
            seen = set()
            for match in judged(data, spec, name):
                key = (match["repo"], match["path"], match["qualname"])
                current = by_key.get(key)
                if current is None:
                    continue
                seen.add(current.id)
                print(
                    f"    judged  {current.qualname}  "
                    f"[{match['relation']}, {match['confidence']}]\n"
                    f"            "
                    f"{shown_path(by_repo[current.repo], current)}"
                )
            corpus = Corpus.of(
                symbols, [symbol_tokens(s, bodies[s.id]) for s in symbols]
            )
            rank = 0
            for symbol, _ in corpus.search(
                spec_query(spec), count + len(seen)
            ):
                if symbol.id in seen or rank >= count:
                    continue
                rank += 1
                print(
                    f"    {rank:>6}  {symbol.qualname}\n"
                    f"            {shown_path(by_repo[symbol.repo], symbol)}"
                )
        print()


# Branch diff


def pick_repo(config: Config, target: str) -> Repo:
    """Return the repo named `target`, or the only repo of that client."""
    for client in config.clients.values():
        for repo in client.repos:
            if repo.name == target:
                return repo
    if target not in config.clients:
        raise SystemExit(f"unknown repo or client {target!r}")
    client = config.clients[target]
    if len(client.repos) > 1:
        names = ", ".join(r.name for r in client.repos)
        raise SystemExit(f"{target} spans {names}, name one of them")
    return client.repos[0]


def changed_files(base: Checkout, head: Checkout) -> list[str]:
    """Return indexed files that differ between the two checkouts."""
    paths = ["--", *base.repo.paths]
    if head.ref:
        out = git(
            base.root, "diff", "--name-only", "-z", base.ref, head.ref, *paths
        )
        names = out.split("\0")
    else:
        out = git(base.root, "diff", "--name-only", "-z", base.ref, *paths)
        names = out.split("\0")
        untracked = git(
            base.root,
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
            *paths,
        )
        names += untracked.split("\0")
    return sorted({n for n in names if n and base.wanted(n)})


def keyed(symbols: list[Symbol]) -> dict[tuple[str, str, int], Symbol]:
    """Key functions by path, qualname and overload ordinal."""
    seen: Counter[tuple[str, str]] = Counter()
    out = {}
    for symbol in symbols:
        pair = (symbol.path, symbol.qualname)
        out[(*pair, seen[pair])] = symbol
        seen[pair] += 1
    return out


def spec_corpus(config: Config) -> Corpus:
    """Index spec functions so client functions can be mapped back."""
    specs = spec_symbols(config)
    documents = [spec_query(s) + tokenize(read_body(config, s)) for s in specs]
    return Corpus.of(specs, documents)


def unified(before: str, after: str, label: str) -> list[str]:
    """Return a trimmed unified diff of two function bodies."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            f"base {label}",
            f"head {label}",
            lineterm="",
            n=2,
        )
    )
    if len(lines) > DIFF_LINES:
        cut = len(lines) - DIFF_LINES
        lines = lines[:DIFF_LINES] + [f"... {cut} more diff lines"]
    return lines


def diff(
    config: Config,
    target: str,
    base_ref: str,
    head_ref: str,
    spec_filter: str,
    at: dict[str, str],
    count: int,
    merge_base: bool,
) -> None:
    """Show what changed between two refs, mapped to spec functions."""
    repo = pick_repo(config, target)
    root = resolve(config, repo, at).root
    if merge_base:
        base_ref = git(
            root, "merge-base", base_ref, head_ref or "HEAD"
        ).strip()
    base = Checkout(repo=repo, root=root, ref=base_ref)
    head = Checkout(repo=repo, root=root, ref=head_ref)
    try:
        files = changed_files(base, head)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip()) from error
    before, before_bodies = base.parse(files)
    after, after_bodies = head.parse(files)
    base.close()
    head.close()
    old, new = keyed(before), keyed(after)

    changes: list[tuple[str, Symbol, str, str]] = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a and b and a.hash == b.hash:
            continue
        status = "new" if a is None else "removed" if b is None else "changed"
        symbol = b or a
        assert symbol is not None
        changes.append(
            (
                status,
                symbol,
                before_bodies.get(a.id, "") if a else "",
                after_bodies.get(b.id, "") if b else "",
            )
        )

    specs = spec_corpus(config)

    def spec_guesses(symbol: Symbol, body: str, k: int) -> list[str]:
        hits = specs.search(symbol_tokens(symbol, body), k)
        return [s.id for s, _ in hits]

    head_label = head_ref or "worktree"
    print(
        f"{repo.name} {base_ref}..{head_label} at {root}: "
        f"{len(changes)} functions changed in {len(files)} files\n"
    )

    if spec_filter:
        targets = match_specs(spec_symbols(config), spec_filter)
        if not targets:
            raise SystemExit(f"no spec function matches {spec_filter!r}")
        show_spec_diff(config, head, targets, changes, spec_guesses, count)
        return

    areas: Counter[str] = Counter()
    for status, symbol, old_body, new_body in changes:
        body = new_body or old_body
        guesses = spec_guesses(symbol, body, 2)
        areas.update({g.rsplit(".", 1)[0] for g in guesses[:1]})
        delta = churn(old_body, new_body)
        eip_note = f"  {' '.join(symbol.eips)}" if symbol.eips else ""
        print(
            f"{status:<8} {symbol.qualname}  {symbol.path}:{symbol.line}  "
            f"{delta}{eip_note}\n         ~ {', '.join(guesses)}"
        )
    if areas:
        print("\nspec areas (by closest spec function):")
        for module, n in areas.most_common():
            print(f"  {module:<36} {n}")


def churn(before: str, after: str) -> str:
    """Return `+added -removed` line counts."""
    added = removed = 0
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return f"+{added} -{removed}"


def show_spec_diff(
    config: Config,
    head: Checkout,
    targets: list[Symbol],
    changes: list[tuple[str, Symbol, str, str]],
    spec_guesses: SpecGuesser,
    count: int,
) -> None:
    """Print how the client code behind `targets` changed."""
    symbols, bodies = parsed(config, head)
    head.close()
    corpus = Corpus.of(
        symbols, [symbol_tokens(s, bodies[s.id]) for s in symbols]
    )
    changed = {(c[1].path, c[1].qualname): c for c in changes}
    for spec in targets:
        print(f"{spec.id}")
        top = [s for s, _ in corpus.search(spec_query(spec), count)]
        related = [
            c[1]
            for c in changes
            if spec.id in spec_guesses(c[1], c[3] or c[2], 3)
        ]
        shown = set()
        for symbol in top + related:
            key = (symbol.path, symbol.qualname)
            if key in shown:
                continue
            shown.add(key)
            change = changed.get(key)
            if change is None:
                print(f"  same     {symbol.qualname}  {symbol.path}")
                continue
            status, _, old_body, new_body = change
            print(f"  {status:<8} {symbol.qualname}  {symbol.path}")
            if status == "new":
                lines = new_body.splitlines()
                for line in lines[:NEW_LINES]:
                    print(f"    + {line}")
                if len(lines) > NEW_LINES:
                    print(f"    ... {len(lines) - NEW_LINES} more lines")
            else:
                for line in unified(old_body, new_body, symbol.qualname):
                    print(f"    {line}")
        print()
