#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
Set up everything needed to work on EIPs across the spec, the tests and the
execution clients, on macOS or Linux. Run it again any time.

  ./setup.sh               clone or fetch your team's repos, install the
                           toolchains its builds need and rebuild what
                           changed. The first run asks which team you are
                           on and saves it in .mesh/me.toml.
  ./setup.sh --team geth   set or change your team, see teams.toml
  ./setup.sh geth besu     the same, building only the targets named
  ./setup.sh --profile review
                           only the repos profiles/review.txt lists, with
                           no toolchains or builds
  ./setup.sh --reuse DIR   let new clones borrow objects from clones you
                           already have in DIR, so they download less but
                           stay separate from yours
  ./setup.sh --check       only show what is installed, missing or too old
  ./setup.sh --update      show what changed in this repo upstream and
                           apply it only after you say yes

Repos go in repos/ and binaries in bin/ inside this checkout, unless WS_ROOT
or WS_BIN say otherwise.
"""

from __future__ import annotations

import argparse
import atexit
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "repos.txt"
PROFILES = HERE / "profiles"
TEAMS_FILE = HERE / "teams.toml"
ME_FILE = HERE / ".mesh" / "me.toml"

# repos.txt may only point at these GitHub owners, over https.
TRUSTED_OWNERS = frozenset(
    owner.lower()
    for owner in (
        "alloy-rs",
        "besu-eth",
        "bluealloy",
        "erigontech",
        "ethereum",
        "ethereumjs",
        "ethpandaops",
        "ethspecs",
        "ipsilon",
        "lambdaclass",
        "NethermindEth",
        "paradigmxyz",
        "spencer-tb",
        "status-im",
    )
)
GITHUB_REPO = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")

GO_VERSION = "1.25.7"
RUST_MIN = "1.93"
DOTNET_MIN = "10.0"
JDK_MAJOR = "21"
UV_MIN = "0.13.4"
NODE_MAJOR = "22"
JQ_VERSION = "1.7.1"

# System packages the builds need, installed once with sudo or brew.
APT_PACKAGES = (
    "build-essential",
    "clang",
    "libclang-dev",
    "libsnappy-dev",
    "librocksdb-dev",
)
BREW_PACKAGES = ("rocksdb", "snappy")

SYSTEMS = {"Linux": "linux", "Darwin": "darwin"}
ARCHES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

JOBS = str(os.cpu_count() or 4)

Command = Sequence[str | Path]


def say(message: str) -> None:
    """Print a progress line."""
    print(message, file=sys.stderr, flush=True)


def have(name: str) -> bool:
    """Return whether a command is on PATH."""
    return shutil.which(name) is not None


def run(
    args: Command,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command with its output showing, and raise if it fails."""
    subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def capture(
    args: Command,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run a command quietly and return what happened, never raising. `env`
    adds to the current environment.
    """
    argv = [str(arg) for arg in args]
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def git(
    path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git in `path` quietly."""
    return capture(["git", "-C", path, *args], env=env)


def last_line(text: str) -> str:
    """Return the last non-empty line of `text`."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


VERSION = re.compile(r"\d+(?:\.\d+)*")


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the first version number in `text`."""
    match = VERSION.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group().split("."))


def at_least(found: tuple[int, ...] | None, minimum: str) -> bool:
    """Return whether `found` is at least the version `minimum`."""
    wanted = tuple(int(part) for part in minimum.split("."))
    return found is not None and found >= wanted


def tool_version(*args: str) -> tuple[int, ...] | None:
    """Return the version a tool reports, or None when it is missing."""
    if not have(args[0]):
        return None
    result = capture(args, timeout=60)
    return parse_version(result.stdout + result.stderr)


@dataclass(frozen=True)
class Workspace:
    """Where repos, binaries, toolchains and state live on a machine."""

    root: Path
    bin: Path
    home: Path
    system: str
    arch: str

    @classmethod
    def from_env(cls) -> Workspace:
        """Describe this machine, using WS_ROOT and WS_BIN when set."""
        system = SYSTEMS.get(platform.system())
        arch = ARCHES.get(platform.machine().lower())
        if system is None or arch is None:
            raise SystemExit(
                f"setup: unsupported platform {platform.system()} "
                f"{platform.machine()}"
            )
        return cls(
            root=Path(os.environ.get("WS_ROOT", HERE / "repos")),
            bin=Path(os.environ.get("WS_BIN", HERE / "bin")),
            home=Path.home(),
            system=system,
            arch=arch,
        )

    @property
    def local(self) -> Path:
        """Return the prefix toolchains install into."""
        return self.home / ".local"

    @property
    def state(self) -> Path:
        """Return where the shell setup files live, shared by checkouts."""
        return self.home / ".config" / "protocol-mesh"

    @property
    def stamps(self) -> Path:
        """Return where each target's last built commit is recorded."""
        return self.bin / ".built"

    def tool_dirs(self) -> list[Path]:
        """Return the toolchain directories, in lookup order."""
        return [
            self.local / "bin",
            self.local / "go" / "bin",
            self.home / "go" / "bin",
            self.home / ".cargo" / "bin",
            self.local / "node" / "bin",
            self.home / ".dotnet",
            self.local / "jdk" / "bin",
        ]

    def out(self, target: str) -> Path:
        """Create and return the directory for a target's binaries."""
        path = self.bin / target
        path.mkdir(parents=True, exist_ok=True)
        return path


@functools.cache
def scratch() -> Path:
    """Return a temporary directory for downloads, removed at exit."""
    path = Path(tempfile.mkdtemp(prefix="protocol-mesh-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def fetch(url: str, name: str) -> Path:
    """Download `url` to a temporary file called `name` and return it."""
    path = scratch() / name
    run(["curl", "-fsSL", "--retry", "3", "-o", path, url])
    return path


def unpack(archive: Path, dest: Path) -> None:
    """Replace `dest` with a .tar.gz's contents, minus its top folder."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    run(["tar", "-xzf", archive, "-C", dest, "--strip-components=1"])


# Each installer leaves alone a tool that is already new enough.


def install_uv(ws: Workspace) -> None:
    """Install uv when the one on PATH is older than UV_MIN."""
    if at_least(tool_version("uv", "--version"), UV_MIN):
        return
    say("install uv")
    script = fetch("https://astral.sh/uv/install.sh", "uv-install.sh")
    run(["sh", script], env={**os.environ, "UV_NO_MODIFY_PATH": "1"})


def install_cmake(ws: Workspace) -> None:
    """Install cmake with uv when there is none."""
    if have("cmake"):
        return
    say("install cmake")
    run(["uv", "tool", "install", "-q", "cmake"])


def install_rust(ws: Workspace) -> None:
    """Install or update Rust when rustc is older than RUST_MIN."""
    if at_least(tool_version("rustc", "--version"), RUST_MIN):
        return
    if have("rustup"):
        say("update rust")
        run(["rustup", "update", "stable"])
        return
    say("install rust")
    script = fetch("https://sh.rustup.rs", "rustup-init.sh")
    run(["sh", script, "-y", "--no-modify-path", "--profile", "minimal"])


def install_go(ws: Workspace) -> None:
    """Install Go when the one on PATH is older than GO_VERSION."""
    if at_least(tool_version("go", "env", "GOVERSION"), GO_VERSION):
        return
    say(f"install go {GO_VERSION}")
    name = f"go{GO_VERSION}.{ws.system}-{ws.arch}.tar.gz"
    unpack(fetch(f"https://go.dev/dl/{name}", name), ws.local / "go")


def install_node(ws: Workspace) -> None:
    """Install the latest Node NODE_MAJOR when node is older."""
    if at_least(tool_version("node", "--version"), NODE_MAJOR):
        return
    arch = "x64" if ws.arch == "amd64" else "arm64"
    dist = f"https://nodejs.org/dist/latest-v{NODE_MAJOR}.x"
    sums = fetch(f"{dist}/SHASUMS256.txt", "node-sums.txt").read_text()
    match = re.search(rf"node-v[\d.]+-{ws.system}-{arch}\.tar\.gz", sums)
    if match is None:
        raise SystemExit(f"setup: no node {NODE_MAJOR} for {ws.system}")
    say(f"install {match.group()}")
    archive = fetch(f"{dist}/{match.group()}", match.group())
    unpack(archive, ws.local / "node")


def install_dotnet(ws: Workspace) -> None:
    """Install the .NET SDK that nethermind's global.json asks for."""
    pinned = ws.root / "clients" / "nethermind" / "global.json"
    wanted, version = DOTNET_MIN, ["--channel", DOTNET_MIN]
    if pinned.exists():
        wanted = str(json.loads(pinned.read_text())["sdk"]["version"])
        version = ["--jsonfile", str(pinned)]
    if at_least(tool_version("dotnet", "--version"), wanted):
        return
    say(f"install dotnet sdk {wanted}")
    script = fetch("https://dot.net/v1/dotnet-install.sh", "dotnet.sh")
    install_dir = ws.home / ".dotnet"
    run(["bash", script, "--install-dir", install_dir, "--no-path", *version])


def install_jdk(ws: Workspace) -> None:
    """Install Temurin JDK_MAJOR when java is older."""
    if at_least(tool_version("java", "-version"), JDK_MAJOR):
        return
    say(f"install temurin jdk {JDK_MAJOR}")
    system = "mac" if ws.system == "darwin" else "linux"
    arch = "x64" if ws.arch == "amd64" else "aarch64"
    url = (
        f"https://api.adoptium.net/v3/binary/latest/{JDK_MAJOR}/ga/"
        f"{system}/{arch}/jdk/hotspot/normal/eclipse"
    )
    unpacked = scratch() / "jdk"
    unpack(fetch(url, "jdk.tar.gz"), unpacked)
    # The macOS archive keeps the JDK under Contents/Home.
    if (unpacked / "Contents" / "Home").is_dir():
        unpacked = unpacked / "Contents" / "Home"
    shutil.rmtree(ws.local / "jdk", ignore_errors=True)
    shutil.move(unpacked, ws.local / "jdk")


def install_jq(ws: Workspace) -> None:
    """Install jq when there is none."""
    if have("jq"):
        return
    say("install jq")
    system = "macos" if ws.system == "darwin" else "linux"
    name = f"jq-{system}-{ws.arch}"
    releases = "https://github.com/jqlang/jq/releases/download"
    binary = ws.local / "bin" / "jq"
    shutil.move(fetch(f"{releases}/jq-{JQ_VERSION}/{name}", name), binary)
    binary.chmod(0o755)


def install_ripgrep(ws: Workspace) -> None:
    """Install ripgrep with cargo when there is none."""
    if have("rg"):
        return
    say("install ripgrep")
    run(["cargo", "install", "-q", "--locked", "ripgrep"])


Installer = Callable[[Workspace], None]

# Every setup gets these. Build targets add what they need.
BASE_INSTALLERS: tuple[Installer, ...] = (install_uv, install_jq)

INSTALLERS: tuple[Installer, ...] = (
    *BASE_INSTALLERS,
    install_cmake,
    install_rust,
    install_go,
    install_node,
    install_dotnet,
    install_jdk,
    install_ripgrep,
)


def toolchain_vars(ws: Workspace) -> dict[str, str]:
    """Return DOTNET_ROOT and JAVA_HOME for toolchains installed here."""
    found = {}
    if (ws.home / ".dotnet" / "dotnet").exists():
        found["DOTNET_ROOT"] = str(ws.home / ".dotnet")
    if (ws.local / "jdk" / "bin" / "java").exists():
        found["JAVA_HOME"] = str(ws.local / "jdk")
    return found


def env_script(ws: Workspace, shell: str) -> str:
    """Return the lines that put the toolchains on PATH, for sh or fish."""
    dirs = [str(path) for path in ws.tool_dirs()]
    variables = toolchain_vars(ws).items()
    if shell == "fish":
        lines = [f"set -gx PATH {' '.join(dirs)} $PATH"]
        lines += [f"set -gx {name} {value}" for name, value in variables]
    else:
        lines = [f'export PATH="{":".join(dirs)}:$PATH"']
        lines += [f'export {name}="{value}"' for name, value in variables]
    return "\n".join(lines) + "\n"


def use_toolchains(ws: Workspace) -> None:
    """Put the toolchains on this process's PATH for every later step."""
    dirs = [str(path) for path in ws.tool_dirs()]
    rest = os.environ.get("PATH", "").split(os.pathsep)
    path = dirs + [entry for entry in rest if entry not in dirs]
    os.environ["PATH"] = os.pathsep.join(path)
    os.environ.update(toolchain_vars(ws))


def install_env(ws: Workspace) -> None:
    """Put the toolchains on PATH in new bash, zsh and fish shells."""
    ws.state.mkdir(parents=True, exist_ok=True)
    sh = ws.state / "env.sh"
    sh.write_text(env_script(ws, "sh"))
    (ws.state / "env.fish").write_text(env_script(ws, "fish"))
    login_shell = Path(os.environ.get("SHELL", "")).name
    for shell in ("bash", "zsh"):
        rc = ws.home / f".{shell}rc"
        if not rc.exists() and login_shell != shell:
            continue
        if str(sh) not in (rc.read_text() if rc.exists() else ""):
            with rc.open("a") as out:
                out.write(f'\n. "{sh}"\n')
    fish = ws.home / ".config" / "fish"
    if fish.is_dir():
        link_file(
            ws.state / "env.fish", fish / "conf.d" / "protocol-mesh.fish"
        )


def installers_for(targets: Sequence[str]) -> list[Installer]:
    """Return the installers `targets` need, the base ones first, once each."""
    chosen = list(BASE_INSTALLERS)
    for name in targets:
        for install in TARGETS[name].needs:
            if install not in chosen:
                chosen.append(install)
    return chosen


def install_tools(ws: Workspace, targets: Sequence[str]) -> None:
    """Install the toolchains `targets` need into ~/.local, if missing."""
    (ws.local / "bin").mkdir(parents=True, exist_ok=True)
    for install in installers_for(targets):
        install(ws)
    # ripgrep comes from cargo, so only where Rust is set up anyway.
    if have("cargo"):
        install_ripgrep(ws)
    install_env(ws)
    use_toolchains(ws)


def link_file(target: Path, link: Path) -> None:
    """Point `link` at `target`, keeping any real file already there."""
    if link.exists() and not link.is_symlink():
        say(f"kept, not a link: {link}")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(target)


@dataclass(frozen=True)
class Repo:
    """One line of repos.txt."""

    dir: str
    url: str
    branch: str | None = None
    shallow: bool = False


def read_profile(
    name: str, repos: list[Repo], folder: Path = PROFILES
) -> list[Repo]:
    """Return the repos that profile `name` lists, in repos.txt order."""
    path = folder / f"{name}.txt"
    if not path.is_file():
        raise SystemExit(f"setup: no profile {name} in {folder.name}/")
    wanted = {
        line.split("#", 1)[0].strip() for line in path.read_text().splitlines()
    } - {""}
    unknown = wanted - {repo.dir for repo in repos}
    if unknown:
        raise SystemExit(
            f"setup: profile {name} lists {' '.join(sorted(unknown))}, "
            "which repos.txt does not have"
        )
    return [repo for repo in repos if repo.dir in wanted]


@dataclass(frozen=True)
class Team:
    """A team from teams.toml."""

    title: str
    owns: tuple[str, ...]
    repos: tuple[str, ...]
    build: tuple[str, ...]


def read_teams(path: Path = TEAMS_FILE) -> dict[str, Team]:
    """Return the teams in `path`, each with the common repos added."""
    data = tomllib.loads(path.read_text())
    common = list(data.get("common", []))
    teams = {}
    for name, entry in data["teams"].items():
        repos = list(entry.get("repos", []))
        teams[name] = Team(
            title=entry["title"],
            owns=tuple(entry["owns"]),
            repos=("*",)
            if "*" in repos
            else tuple(dict.fromkeys(common + repos)),
            build=tuple(entry.get("build", [])),
        )
    return teams


def team_plan(team: Team, repos: list[Repo]) -> tuple[list[Repo], list[str]]:
    """Return the repos a team clones, including those it owns, and the
    targets it builds."""
    known = {repo.dir for repo in repos}
    unknown = [
        d for d in team.repos + team.owns if d != "*" and d not in known
    ]
    unknown += [t for t in team.build if t != "*" and t not in TARGETS]
    if unknown:
        raise SystemExit(
            f"setup: teams.toml names {' '.join(unknown)}, which "
            "repos.txt and the build targets do not have"
        )
    wanted = set(team.repos + team.owns)
    picked = [r for r in repos if "*" in wanted or r.dir in wanted]
    builds = list(TARGETS) if "*" in team.build else list(team.build)
    return picked, builds


def read_me(path: Path = ME_FILE) -> dict[str, Any]:
    """Return who this checkout works for, or nothing yet."""
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())


def write_me(path: Path, login: str, team: str, eips: Sequence[int]) -> None:
    """Save who this checkout works for, in a file git ignores."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Who this checkout works for. Git ignores this file, and\n"
        "# ./setup.sh --team NAME changes the team.\n"
        f'login = "{login}"\n'
        f'team = "{team}"\n'
        f"eips = [{', '.join(str(eip) for eip in eips)}]\n"
    )


def gh_login() -> str:
    """Return the GitHub login gh uses, or nothing."""
    if not have("gh"):
        return ""
    result = capture(["gh", "api", "user", "--jq", ".login"], timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def pick_team(answer: str, teams: dict[str, Team]) -> str | None:
    """Return the team an answer names, by number or name."""
    names = list(teams)
    answer = answer.strip().lower()
    if answer.isdigit() and 1 <= int(answer) <= len(names):
        return names[int(answer) - 1]
    return answer if answer in teams else None


def parse_eips(answer: str) -> list[int]:
    """Return the EIP numbers in an answer such as "8141, EIP-7805"."""
    return [int(number) for number in re.findall(r"\d+", answer)]


def ask_me(teams: dict[str, Team]) -> tuple[str, list[int]]:
    """Ask which team the user is on and which EIPs they focus on."""
    print("Which team are you on? Your agents change code only in its repos.")
    for number, (name, entry) in enumerate(teams.items(), 1):
        print(f"  {number:>2}. {name:<11} {entry.title}")
    while (team := pick_team(input("team: "), teams)) is None:
        print("pick a number or a name from the list")
    eips = parse_eips(input("EIPs you focus on, optional, like 8141 7805: "))
    return team, eips


def github_repo(url: str) -> str | None:
    """Return the lowercase owner/name of a GitHub url, or None."""
    match = GITHUB_REPO.search(url)
    if match is None:
        return None
    return f"{match[1]}/{match[2]}".lower()


def trusted(url: str) -> bool:
    """Return whether `url` is an https GitHub repo of a trusted owner."""
    repo = github_repo(url)
    return (
        url.startswith("https://github.com/")
        and repo is not None
        and repo.split("/")[0] in TRUSTED_OWNERS
    )


def read_manifest(path: Path = MANIFEST) -> list[Repo]:
    """Return the repos listed in `path`, skipping blanks and comments."""
    repos = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        options = fields[2:]
        branches = [option for option in options if option != "shallow"]
        if len(fields) < 2 or len(branches) > 1:
            raise SystemExit(
                f"setup: {path.name} line {number} needs a dir, a url "
                "and optionally a branch and `shallow`"
            )
        if not trusted(fields[1]):
            raise SystemExit(
                f"setup: {path.name} line {number}: {fields[1]} is not "
                "an https GitHub repo of a trusted owner"
            )
        repos.append(
            Repo(
                dir=fields[0],
                url=fields[1],
                branch=branches[0] if branches else None,
                shallow="shallow" in options,
            )
        )
    return repos


# Shallow repos are archives: only the newest commit, no LFS attachments.
SKIP_LFS = {"GIT_LFS_SKIP_SMUDGE": "1"}


def clone(path: Path, repo: Repo, reference: Path | None) -> str:
    """Clone `repo` to `path`, borrowing objects from `reference`."""
    env = SKIP_LFS if repo.shallow else None
    branch = ["--branch", repo.branch] if repo.branch else []
    if repo.shallow:
        copy = ["--depth", "1"]
    elif reference is not None:
        # Copy what the existing clone has, then cut the link to it.
        copy = ["--reference", str(reference), "--dissociate"]
    else:
        copy = ["--filter=blob:none"]
    result = capture(
        ["git", "clone", "-q", *copy, *branch, repo.url, path], env=env
    )
    if result.returncode and reference is not None and not repo.shallow:
        return clone(path, repo, None)
    if result.returncode:
        return f"clone failed: {last_line(result.stderr)}"
    return "cloned"


def sync_repo(path: Path, repo: Repo, reference: Path | None = None) -> str:
    """
    Clone `repo` to `path`, or fetch it and fast-forward a clean branch.

    Return what happened. A checkout with local changes, without an
    upstream branch, or diverged from it is fetched but left as it is,
    since it may hold someone's work.
    """
    if not (path / ".git").exists():
        return clone(path, repo, reference)
    env = SKIP_LFS if repo.shallow else None
    fetched = git(path, "rev-parse", "-q", "--verify", "@{u}").stdout
    depth = ["--depth", "1"] if repo.shallow else []
    result = git(path, "fetch", "-q", "--all", "--prune", *depth, env=env)
    if result.returncode:
        return f"fetch failed: {last_line(result.stderr)}"
    if git(path, "status", "--porcelain", "--untracked-files=no").stdout:
        return "has local changes, fetched only"
    if git(path, "rev-parse", "-q", "--verify", "@{u}").returncode:
        return "no upstream branch, fetched only"
    before = git(path, "rev-parse", "HEAD").stdout
    if repo.shallow:
        # There is no history to merge, so jump to the newest commit,
        # unless someone committed on top of the last one fetched.
        if before != fetched:
            return "has local commits, fetched only"
        moved = git(path, "reset", "-q", "--hard", "@{u}", env=env)
    else:
        moved = git(path, "merge", "-q", "--ff-only", "@{u}")
    if moved.returncode:
        return "diverged, fetched only"
    if git(path, "rev-parse", "HEAD").stdout != before:
        return "updated"
    return "up to date"


def find_clones(dirs: Sequence[Path]) -> dict[str, Path]:
    """
    Map the GitHub repos cloned in `dirs`, up to two levels down, to their
    paths, by owner/name and by name alone, so a fork matches too.
    """
    found: dict[str, Path] = {}
    for top in dirs:
        for path in [top, *top.glob("*"), *top.glob("*/*")]:
            if not (path / ".git").exists():
                continue
            for word in git(path, "remote", "-v").stdout.split():
                repo = github_repo(word)
                if repo is not None:
                    found.setdefault(repo, path)
                    found.setdefault(repo.split("/")[1], path)
    return found


def sync_repos(
    ws: Workspace, repos: list[Repo], clones: dict[str, Path]
) -> None:
    """Clone or update every repo, several at a time."""

    def sync(repo: Repo) -> tuple[Repo, str]:
        name = github_repo(repo.url) or ""
        reference = clones.get(name) or clones.get(name.split("/")[-1])
        return repo, sync_repo(ws.root / repo.dir, repo, reference)

    current = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for repo, result in pool.map(sync, repos):
            if result == "up to date":
                current += 1
            else:
                say(f"{repo.dir}: {result}")
    say(f"repos: {current} of {len(repos)} were already up to date")


class BuildError(Exception):
    """A build ran but did not produce what it should."""


def link_binary(built: Path, link: Path) -> None:
    """Point `link` at a binary a build produced, failing if it is not."""
    if not built.exists():
        raise BuildError(f"{built} was not built")
    link.unlink(missing_ok=True)
    link.symlink_to(built)


# Each build runs in the target's checkout, `src`.


def build_eels(ws: Workspace, src: Path) -> None:
    """Create the environment `uv run fill` uses."""
    run(["uv", "sync", "-q"], cwd=src)


def build_hive(ws: Workspace, src: Path) -> None:
    """Build ./hive in the hive checkout."""
    run(["go", "build", "-o", "hive", "."], cwd=src)


def build_clive(ws: Workspace, src: Path) -> None:
    """
    Build clive, the hive results CLI, and hapi, the API it queries,
    from clive/ in this repo.
    """
    bin_dir = ws.local / "bin"
    run(["go", "build", "-o", bin_dir / "clive", "./cmd/hive-cli"], cwd=src)
    run(["go", "build", "-o", bin_dir / "hapi", "."], cwd=src)


def build_geth(ws: Workspace, src: Path) -> None:
    """Build geth's evm tool."""
    run(["go", "build", "-o", ws.out("geth") / "evm", "./cmd/evm"], cwd=src)


def build_erigon(ws: Workspace, src: Path) -> None:
    """Build erigon's evm tool."""
    run(["make", "evm.cmd"], cwd=src)
    link_binary(src / "build" / "bin" / "evm", ws.out("erigon") / "evm")


def build_besu(ws: Workspace, src: Path) -> None:
    """Build besu's evmtool."""
    run(
        ["./gradlew", "--no-daemon", "-q", ":ethereum:evmtool:installDist"],
        cwd=src,
    )
    built = src / "ethereum/evmtool/build/install/evmtool/bin/evmtool"
    link_binary(built, ws.out("besu") / "evmtool")


def build_nethermind(ws: Workspace, src: Path) -> None:
    """Publish nethermind's nethtest runner."""
    run(
        ["dotnet", "publish", "src/Nethermind/Nethermind.Test.Runner"]
        + ["-c", "Release", "--nologo", "-v", "q"]
        + ["-o", ws.out("nethermind")],
        cwd=src,
    )


def build_evmone(ws: Workspace, src: Path) -> None:
    """Build evmone and its state and blockchain test runners."""
    run(["git", "submodule", "update", "-q", "--init", "--recursive"], cwd=src)
    run(
        ["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"]
        + ["-DEVMONE_TESTING=ON"],
        cwd=src,
    )
    run(["cmake", "--build", "build", "--parallel", JOBS], cwd=src)
    for tool in ("evmone", "evmone-statetest", "evmone-blockchaintest"):
        link_binary(src / "build" / "bin" / tool, ws.out("evmone") / tool)


def build_ethrex(ws: Workspace, src: Path) -> None:
    """Build ethrex's ethrex-evm tool."""
    run(
        ["cargo", "build", "-q", "--release"]
        + ["--manifest-path", "tooling/Cargo.toml", "-p", "ethrex-evm"],
        cwd=src,
    )
    built = src / "tooling" / "target" / "release" / "ethrex-evm"
    link_binary(built, ws.out("ethrex") / "ethrex-evm")


def build_nimbus(ws: Workspace, src: Path) -> None:
    """Build nimbus's t8n tool, after its own Nim and dependencies."""
    run(["make", f"-j{JOBS}", "update"], cwd=src)
    run(["make", f"-j{JOBS}", "t8n"], cwd=src)
    link_binary(src / "tools" / "t8n" / "t8n", ws.out("nimbus") / "t8n")


def build_ethereumjs(ws: Workspace, src: Path) -> None:
    """Install ethereumjs and wrap its t8n script."""
    run(["npm", "ci", "-s", "--no-audit", "--no-fund"], cwd=src)
    # The script finds tsx and its sources from inside the checkout.
    wrapper = ws.out("ethereumjs") / "ethereumjs-t8ntool.sh"
    wrapper.write_text(
        f'#!/bin/sh\ncd "{src}" && exec '
        './packages/vm/test/t8n/ethereumjs-t8ntool.sh "$@"\n'
    )
    wrapper.chmod(0o755)


@dataclass(frozen=True)
class Target:
    """
    Something to build, from a checkout under the workspace root, or
    from a directory of this repo when `here` is set.
    """

    source: str
    build: Callable[[Workspace, Path], None]
    needs: tuple[Installer, ...] = ()
    here: bool = False

    def src(self, ws: Workspace) -> Path:
        """Return the directory the build runs in."""
        return (HERE if self.here else ws.root) / self.source



TARGETS = {
    "eels": Target("execution-specs", build_eels),
    "hive": Target("hive", build_hive, (install_go,)),
    "clive": Target("clive", build_clive, (install_go,), here=True),
    "geth": Target("clients/go-ethereum", build_geth, (install_go,)),
    "erigon": Target("clients/erigon", build_erigon, (install_go,)),
    "besu": Target("clients/besu", build_besu, (install_jdk,)),
    "nethermind": Target(
        "clients/nethermind", build_nethermind, (install_dotnet,)
    ),
    "evmone": Target("clients/evmone", build_evmone, (install_cmake,)),
    "ethrex": Target("clients/ethrex", build_ethrex, (install_rust,)),
    "nimbus": Target("clients/nimbus-eth1", build_nimbus),
    "ethereumjs": Target(
        "clients/ethereumjs-monorepo", build_ethereumjs, (install_node,)
    ),
}


def source_commit(src: Path) -> str:
    """
    Return the commit `src` is at: HEAD for a clone, or the last commit
    that changed it for a directory of this repo.
    """
    if (src / ".git").exists():
        return git(src, "rev-parse", "HEAD").stdout.strip()
    return git(src, "log", "-1", "--format=%H", "--", ".").stdout.strip()


def needs_build(ws: Workspace, name: str, src: Path) -> bool:
    """Return whether `name` has a new commit or local changes."""
    stamp = ws.stamps / name
    dirty = git(
        src, "status", "--porcelain", "--untracked-files=no", "--", "."
    )
    if dirty.stdout or not stamp.exists():
        return True
    return stamp.read_text().strip() != source_commit(src)


def record_build(ws: Workspace, name: str, src: Path) -> None:
    """Remember the commit `name` was just built from."""
    stamp = ws.stamps / name
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(source_commit(src) + "\n")


def build_targets(ws: Workspace, names: Sequence[str]) -> list[str]:
    """Build each named target that changed and return those that failed."""
    failed = []
    for name in names:
        target = TARGETS[name]
        src = target.src(ws)
        if not (target.here or (src / ".git").exists()):
            say(f"{name}: {target.source} is not cloned")
            failed.append(name)
            continue
        if not needs_build(ws, name, src):
            continue
        say(f"build {name} at {source_commit(src)[:10]}")
        try:
            target.build(ws, src)
        except (subprocess.CalledProcessError, BuildError, OSError) as error:
            say(f"{name}: {error}")
            failed.append(name)
            continue
        record_build(ws, name, src)
    return failed


STATUS_TOOLS: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("uv", ("uv", "--version"), UV_MIN),
    ("go", ("go", "env", "GOVERSION"), GO_VERSION),
    ("rustc", ("rustc", "--version"), RUST_MIN),
    ("node", ("node", "--version"), NODE_MAJOR),
    ("dotnet", ("dotnet", "--version"), DOTNET_MIN),
    ("java", ("java", "-version"), JDK_MAJOR),
    ("kurtosis", ("kurtosis", "version"), None),
) + tuple(
    (name, (name, "--version"), None)
    for name in (
        "git",
        "curl",
        "make",
        "cmake",
        "gcc",
        "docker",
        "jq",
        "rg",
        "gh",
        "claude",
        "codex",
    )
)


def row(name: str, found: tuple[int, ...] | None, minimum: str | None) -> str:
    """Return one status line, flagging a missing or too old tool."""
    state = "ok"
    if found is None:
        state = "MISSING"
    elif minimum is not None and not at_least(found, minimum):
        state = "OLD"
    shown = ".".join(map(str, found)) if found else "-"
    need = f"need {minimum}" if minimum else ""
    return f"  {name:<12} {state:<8} {shown:<14} {need}".rstrip()


def missing_packages(ws: Workspace) -> list[str]:
    """Return the commands that install missing system packages."""
    commands = []
    if have("dpkg-query"):
        missing = [
            package
            for package in APT_PACKAGES
            if "ok installed"
            not in capture(
                ["dpkg-query", "-W", "-f=${Status}", package]
            ).stdout
        ]
        if missing:
            commands.append(f"sudo apt-get install -y {' '.join(missing)}")
    elif have("brew"):
        missing = [
            package
            for package in BREW_PACKAGES
            if capture(["brew", "list", "--versions", package]).returncode
        ]
        if missing:
            commands.append(f"brew install {' '.join(missing)}")
    elif ws.system == "linux":
        commands.append("a C compiler, clang, snappy and rocksdb")
    if ws.system == "darwin" and capture(["xcode-select", "-p"]).returncode:
        commands.append("xcode-select --install")
    return commands


def check_repos(ws: Workspace, repos: list[Repo]) -> None:
    """Print which of `repos` are missing."""
    print(f"repos in {ws.root}")
    missing = [r.dir for r in repos if not (ws.root / r.dir / ".git").exists()]
    if missing:
        print(f"  missing {' '.join(missing)}")
    else:
        print(f"  all {len(repos)} cloned")


def check(ws: Workspace, me: dict[str, Any], repos: list[Repo]) -> None:
    """Print who this is for and which tools, repos and builds are in place."""
    if me:
        eips = " ".join(f"EIP-{eip}" for eip in me.get("eips", []))
        print(f"you: {me.get('login') or '?'} on team {me.get('team')}")
        if eips:
            print(f"  focus {eips}")
    else:
        print("you: no team yet, run ./setup.sh --team NAME")
    print("tools")
    for name, args, minimum in STATUS_TOOLS:
        print(row(name, tool_version(*args), minimum))
    if have("docker") and capture(["docker", "info"], timeout=30).returncode:
        print("  docker is installed but not running, hive needs it")

    check_repos(ws, repos)

    print(f"builds in {ws.bin}")
    for name in TARGETS:
        stamp = ws.stamps / name
        built = stamp.read_text()[:10] if stamp.exists() else "not built"
        print(f"  {name:<12} {built}")

    commands = missing_packages(ws)
    if commands:
        print("system packages, install once")
        for command in commands:
            print(f"  {command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the setup, or only the status check, and return the exit code."""
    parser = argparse.ArgumentParser(
        prog="setup.sh",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check", action="store_true", help="only print the status"
    )
    parser.add_argument(
        "--team",
        metavar="NAME",
        help="set the team this checkout works for, see teams.toml",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="only clone the repos profiles/NAME.txt lists, without "
        "toolchains or builds",
    )
    parser.add_argument(
        "--reuse",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help="borrow objects from clones in DIR, may be repeated",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="target",
        help=f"build only these: {' '.join(TARGETS)}",
    )
    args = parser.parse_args(argv)
    for name in args.targets:
        if name not in TARGETS:
            parser.error(f"unknown target {name}, see --help")
    if args.profile and args.targets:
        parser.error("a profile builds nothing, so it takes no targets")
    if args.profile and args.team:
        parser.error("pick a profile or a team, not both")
    teams = read_teams()
    if args.team and args.team not in teams:
        parser.error(f"unknown team {args.team}, pick from {' '.join(teams)}")

    ws = Workspace.from_env()
    # Builds run their own uv projects and must never wait on a prompt.
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    use_toolchains(ws)

    # The first run asks who this checkout works for, once.
    me = read_me()
    if args.team:
        login = me.get("login") or gh_login()
        write_me(ME_FILE, login, args.team, me.get("eips", []))
        me = read_me()
    elif not (me or args.profile or args.check) and sys.stdin.isatty():
        team, eips = ask_me(teams)
        write_me(ME_FILE, gh_login(), team, eips)
        me = read_me()

    repos = read_manifest()
    builds = list(args.targets)
    if args.profile:
        repos = read_profile(args.profile, repos)
    elif me.get("team") in teams:
        repos, team_builds = team_plan(teams[me["team"]], repos)
        builds = builds or team_builds
    else:
        builds = builds or list(TARGETS)
    if args.check:
        if args.profile:
            check_repos(ws, repos)
        else:
            check(ws, me, repos)
        return 0

    for tool in ("git", "curl", "tar"):
        if not have(tool):
            raise SystemExit(f"setup: install {tool} first")
    sync_repos(ws, repos, find_clones(args.reuse))
    if args.profile:
        check_repos(ws, repos)
        return 0
    try:
        install_tools(ws, builds)
    except subprocess.CalledProcessError as error:
        command = " ".join(map(str, error.cmd))
        raise SystemExit(f"setup: {command} failed") from error
    failed = build_targets(ws, builds)
    check(ws, me, repos)
    if failed:
        say(f"build failed: {' '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
