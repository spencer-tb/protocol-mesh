"""
Read client source at any git ref, or from a working tree.

A `Checkout` is a repo root plus a ref. With no ref it reads the working
tree, including uncommitted and untracked files, which is what you want
mid-debug. With a ref it reads git objects, so two branches of the same
clone can be compared without touching either.
"""

import hashlib
import pickle
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import IO

from .config import Config, Repo
from .index import EXTENSIONS, Symbol, parse_source

# Bump when parsing changes so cached parses are rebuilt.
PARSE_VERSION = "1"


def git(root: Path, *args: str) -> str:
    """Run git in `root` and return stdout."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@dataclass
class Checkout:
    """One repo at one ref, or at its working tree when `ref` is empty."""

    repo: Repo
    root: Path
    ref: str = ""
    _reader: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    def commit(self) -> str:
        """Return the commit the ref (or HEAD) points at."""
        return git(self.root, "rev-parse", f"{self.ref or 'HEAD'}^{{commit}}")[
            :-1
        ]

    def label(self) -> str:
        """Describe the checkout: branch or ref, commit, date, dirty."""
        commit = self.commit()
        date = git(self.root, "log", "-1", "--format=%cs", commit).strip()
        if self.ref:
            return f"{self.repo.name} {self.ref} {commit[:9]} {date}"
        branch = git(self.root, "branch", "--show-current").strip()
        dirty = " +uncommitted" if self.dirty_files() else ""
        return (
            f"{self.repo.name} {branch or 'detached'} {commit[:9]} "
            f"{date} worktree{dirty}"
        )

    def dirty_files(self) -> list[str]:
        """Return changed or untracked files under the indexed paths."""
        out = git(
            self.root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *self.repo.paths,
        )
        return [entry[3:] for entry in out.split("\0") if len(entry) > 3]

    def wanted(self, rel: str) -> bool:
        """Return whether `rel` is a source file this repo indexes."""
        if not rel.endswith(EXTENSIONS[self.repo.lang]):
            return False
        return not any(fnmatch(rel, p) for p in self.repo.exclude)

    def files(self) -> list[str]:
        """Return the indexed source files, repo-relative."""
        if self.ref:
            out = git(
                self.root,
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                self.ref,
                "--",
                *self.repo.paths,
            )
            names = out.split("\0")
        else:
            out = git(
                self.root,
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                *self.repo.paths,
            )
            names = [
                n for n in out.split("\0") if n and (self.root / n).is_file()
            ]
        return sorted(n for n in names if n and self.wanted(n))

    def read(self, rel: str) -> bytes:
        """Return the content of `rel` at this checkout."""
        if not self.ref:
            return (self.root / rel).read_bytes()
        if self._reader is None:
            self._reader = subprocess.Popen(
                ["git", "-C", str(self.root), "cat-file", "--batch"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        stdin: IO[bytes] = self._reader.stdin  # type: ignore[assignment]
        stdout: IO[bytes] = self._reader.stdout  # type: ignore[assignment]
        stdin.write(f"{self.ref}:{rel}\n".encode())
        stdin.flush()
        header = stdout.readline().split()
        if len(header) < 3 or header[1] != b"blob":
            return b""
        data = stdout.read(int(header[2]))
        stdout.read(1)
        return data

    def state(self) -> str:
        """Return a key that changes whenever the indexed source changes."""
        parts = [PARSE_VERSION, str(self.root), self.commit()]
        parts += [self.repo.lang, *self.repo.paths, *self.repo.exclude]
        if not self.ref:
            for rel in sorted(self.dirty_files()):
                path = self.root / rel
                stat = path.stat() if path.exists() else None
                parts.append(f"{rel}:{stat.st_mtime_ns if stat else '-'}")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]

    def parse(
        self, only: list[str] | None = None
    ) -> tuple[list[Symbol], dict[str, str]]:
        """Return every function in this checkout, or in `only` files."""
        symbols: list[Symbol] = []
        bodies: dict[str, str] = {}
        for rel in only if only is not None else self.files():
            if not self.wanted(rel):
                continue
            source = self.read(rel)
            for symbol, body in parse_source(
                self.repo.lang, self.repo.name, rel, source
            ):
                symbols.append(symbol)
                bodies[symbol.id] = body
        return symbols, bodies

    def close(self) -> None:
        """Stop the background `git cat-file` reader."""
        if self._reader is not None:
            self._reader.communicate()
            self._reader = None


def parsed(
    config: Config, checkout: Checkout
) -> tuple[list[Symbol], dict[str, str]]:
    """Parse a whole checkout, cached by its source state."""
    cache = config.root / "cache" / "parsed"
    path = cache / f"{checkout.repo.name}-{checkout.state()}.pkl"
    if path.exists():
        with path.open("rb") as f:
            data: tuple[list[Symbol], dict[str, str]] = pickle.load(f)
            return data
    result = checkout.parse()
    cache.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(result, f)
    return result


def resolve(config: Config, repo: Repo, at: dict[str, str]) -> Checkout:
    """
    Pick the checkout for `repo`.

    `at[repo]` may be a path, `path@ref`, or `@ref` (a ref in the default
    root). The default root is the `[checkouts]` path from `xref.toml` when
    it exists, else the cached upstream clone.
    """
    spec = at.get(repo.name, "")
    path, _, ref = spec.partition("@")
    if path:
        root = Path(path).expanduser()
    else:
        local = config.checkouts.get(repo.name)
        # Relative paths are from xref.toml, absolute ones stay as they are.
        local_path = config.root / Path(local).expanduser() if local else None
        if local_path and local_path.exists() and not at.get("upstream"):
            root = local_path
        else:
            root = config.repo_path(repo)
    return Checkout(repo=repo, root=root, ref=ref)
