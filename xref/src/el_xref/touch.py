"""
List the client code a spec change lands on.

Given a git range in a local execution-specs checkout, find the spec
functions whose lines changed and print where each client implements
them. Forks are copies, so a change to `forks/bogota/vm/gas.py` is looked
up under the anchor fork's `vm.gas` entry.
"""

import ast
import re
import subprocess
from pathlib import Path

from .config import Config
from .index import spec_module, walk_python
from .mapped import load_map, print_entry

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def changed_lines(diff: str) -> dict[str, set[int]]:
    """Map each changed file to the new-side line numbers it touches."""
    out: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            current = None if name == "/dev/null" else name.removeprefix("b/")
            if current:
                out.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = HUNK.match(line)
            if match:
                start = int(match.group(3))
                count = int(match.group(4) or 1)
                out[current].update(range(start, start + max(count, 1)))
    return out


def functions_at(source: str, lines: set[int]) -> set[str]:
    """Return qualnames of the functions covering any of `lines`."""
    tree = ast.parse(source)
    hits = set()
    for node, owner in walk_python(tree):
        end = node.end_lineno or node.lineno
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        if any(start <= n <= end for n in lines):
            hits.add(f"{owner}.{node.name}" if owner else node.name)
    return hits


def touch(config: Config, repo: Path, revisions: str, fork: str) -> None:
    """Print client locations for every spec function changed in range."""
    fork = fork or config.spec.fork
    prefix = f"src/ethereum/forks/{fork}/"
    diff = git(repo, "diff", "-U0", revisions, "--", prefix)
    head = revisions.split("..")[-1].lstrip(".") if ".." in revisions else ""

    changed: list[str] = []
    for path, lines in sorted(changed_lines(diff).items()):
        if not path.endswith(".py"):
            continue
        if head:
            source = git(repo, "show", f"{head}:{path}")
        else:
            source = (repo / path).read_text()
        module = spec_module(Path(path).relative_to(prefix))
        for qualname in sorted(functions_at(source, lines)):
            changed.append(f"{module}.{qualname}" if module else qualname)

    data = load_map(config)
    print(f"{len(changed)} spec functions changed in {revisions} ({fork})\n")
    for sid in changed:
        print_entry(config, data, sid, [])
