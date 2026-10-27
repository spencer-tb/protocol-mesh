"""Load `xref.toml`."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Json = dict[str, Any]


@dataclass(frozen=True)
class Repo:
    """One git repo checked out sparsely at one ref."""

    name: str
    url: str
    ref: str
    paths: tuple[str, ...]
    lang: str
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Client:
    """An execution client, possibly spread over several repos."""

    name: str
    repos: tuple[Repo, ...]


@dataclass(frozen=True)
class Spec:
    """The EELS fork used as the anchor."""

    repo: Repo
    fork: str


@dataclass
class Config:
    """Parsed `xref.toml` plus the directories derived from it."""

    root: Path
    spec: Spec
    clients: dict[str, Client] = field(default_factory=dict)
    checkouts: dict[str, str] = field(default_factory=dict)

    @property
    def repo_cache(self) -> Path:
        """Return where repos are cloned."""
        return self.root / "cache" / "repos"

    @property
    def data(self) -> Path:
        """Return where indexes and the map are written."""
        return self.root / "data"

    def all_repos(self) -> list[Repo]:
        """Return the spec repo followed by every client repo."""
        repos = [self.spec.repo]
        for client in self.clients.values():
            repos.extend(client.repos)
        return repos

    def repo_path(self, repo: Repo) -> Path:
        """Return the checkout of `repo`."""
        return self.repo_cache / repo.name


def load(path: Path) -> Config:
    """Read `path` into a `Config`."""
    raw = tomllib.loads(path.read_text())

    def repo(entry: Json, lang: str) -> Repo:
        return Repo(
            name=entry["name"],
            url=entry["url"],
            ref=entry["ref"],
            paths=tuple(entry["paths"]),
            lang=entry.get("lang", lang),
            exclude=tuple(entry.get("exclude", ())),
        )

    spec = Spec(repo=repo(raw["spec"], "python"), fork=raw["spec"]["fork"])
    clients = {
        name: Client(
            name=name,
            repos=tuple(repo(r, body.get("lang", "")) for r in body["repos"]),
        )
        for name, body in raw["clients"].items()
    }
    return Config(
        root=path.parent,
        spec=spec,
        clients=clients,
        checkouts=dict(raw.get("checkouts", {})),
    )
