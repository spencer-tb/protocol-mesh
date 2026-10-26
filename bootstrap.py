#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
Set up everything needed to work on EIPs across the spec, the tests and the
execution clients, on macOS or Linux. Run it again any time to update.

  ./setup.sh             clone or update the repos, install any missing
                         toolchains, link the shared commands and build
                         every client binary whose code changed
  ./setup.sh geth besu   the same, building only the targets named
  ./setup.sh --check     only show what is installed, missing or too old

Repos go in ~/ethereum and binaries in ~/ethereum/bin, unless WS_ROOT or
WS_BIN say otherwise.
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
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "repos.txt"

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
    args: Command, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command quietly and return what happened, never raising."""
    argv = [str(arg) for arg in args]
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `path` quietly."""
    return capture(["git", "-C", path, *args])


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
        home = Path.home()
        root = Path(os.environ.get("WS_ROOT", home / "ethereum"))
        return cls(
            root=root,
            bin=Path(os.environ.get("WS_BIN", root / "bin")),
            home=home,
            system=system,
            arch=arch,
        )

    @property
    def local(self) -> Path:
        """Return the prefix toolchains install into."""
        return self.home / ".local"

    @property
    def state(self) -> Path:
        """Return where the setup keeps its own files."""
        return self.home / ".config" / "protocol-mesh"

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


def install_codex(ws: Workspace) -> None:
    """Install the Codex CLI with npm when there is none."""
    if have("codex"):
        return
    say("install codex")
    run(["npm", "install", "-g", "-s", "--prefix", ws.local, "@openai/codex"])


INSTALLERS: tuple[Callable[[Workspace], None], ...] = (
    install_uv,
    install_cmake,
    install_rust,
    install_go,
    install_node,
    install_dotnet,
    install_jdk,
    install_jq,
    install_ripgrep,
    install_codex,
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


def install_tools(ws: Workspace) -> None:
    """Install every missing or too old toolchain into ~/.local."""
    (ws.local / "bin").mkdir(parents=True, exist_ok=True)
    for install in INSTALLERS:
        install(ws)
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


def link_commands(ws: Workspace) -> None:
    """Link the shared commands and point Claude Code at AGENTS.md."""
    for command in sorted((HERE / "commands").glob("*.md")):
        for folder in (
            ws.home / ".claude" / "commands",
            ws.home / ".codex" / "prompts",
        ):
            link_file(command, folder / command.name)
    claude_md = ws.home / ".claude" / "CLAUDE.md"
    line = f"@{HERE / 'AGENTS.md'}"
    text = claude_md.read_text() if claude_md.exists() else ""
    if line not in text.splitlines():
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        with claude_md.open("a") as out:
            out.write(f"\n{line}\n")


@dataclass(frozen=True)
class Repo:
    """One line of repos.txt."""

    dir: str
    url: str
    branch: str | None = None


def read_manifest(path: Path = MANIFEST) -> list[Repo]:
    """Return the repos listed in `path`, skipping blanks and comments."""
    repos = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) not in (2, 3):
            raise SystemExit(
                f"setup: {path.name} line {number} needs a dir, a url "
                "and optionally a branch"
            )
        repos.append(Repo(*fields))
    return repos


def sync_repo(path: Path, repo: Repo) -> str:
    """
    Clone `repo` to `path`, or fetch it and fast-forward a clean branch.

    Return what happened. A checkout with local changes, without an
    upstream branch, or diverged from it is fetched but left as it is,
    since it may hold someone's work.
    """
    if not (path / ".git").exists():
        branch = ["--branch", repo.branch] if repo.branch else []
        result = capture(
            ["git", "clone", "-q", "--filter=blob:none", *branch]
            + [repo.url, path]
        )
        if result.returncode:
            return f"clone failed: {last_line(result.stderr)}"
        return "cloned"
    result = git(path, "fetch", "-q", "--all", "--prune")
    if result.returncode:
        return f"fetch failed: {last_line(result.stderr)}"
    if git(path, "status", "--porcelain", "--untracked-files=no").stdout:
        return "has local changes, fetched only"
    if git(path, "rev-parse", "-q", "--verify", "@{u}").returncode:
        return "no upstream branch, fetched only"
    before = git(path, "rev-parse", "HEAD").stdout
    if git(path, "merge", "-q", "--ff-only", "@{u}").returncode:
        return "diverged, fetched only"
    if git(path, "rev-parse", "HEAD").stdout != before:
        return "updated"
    return "up to date"


def sync_repos(ws: Workspace, repos: list[Repo]) -> None:
    """Clone or update every repo, several at a time."""

    def sync(repo: Repo) -> tuple[Repo, str]:
        return repo, sync_repo(ws.root / repo.dir, repo)

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
    """Build clive, the hive results CLI, into ~/.local/bin."""
    binary = ws.local / "bin" / "clive"
    run(["go", "build", "-o", binary, "./cmd/hive-cli"], cwd=src)


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
    """Something to build, from a checkout under the workspace root."""

    source: str
    build: Callable[[Workspace, Path], None]


TARGETS = {
    "eels": Target("execution-specs", build_eels),
    "hive": Target("hive", build_hive),
    "clive": Target("clive", build_clive),
    "geth": Target("clients/go-ethereum", build_geth),
    "erigon": Target("clients/erigon", build_erigon),
    "besu": Target("clients/besu", build_besu),
    "nethermind": Target("clients/nethermind", build_nethermind),
    "evmone": Target("clients/evmone", build_evmone),
    "ethrex": Target("clients/ethrex", build_ethrex),
    "nimbus": Target("clients/nimbus-eth1", build_nimbus),
    "ethereumjs": Target("clients/ethereumjs-monorepo", build_ethereumjs),
}


def needs_build(ws: Workspace, name: str, checkout: Path) -> bool:
    """Return whether `name` has a new commit or local changes."""
    stamp = ws.state / "built" / name
    head = git(checkout, "rev-parse", "HEAD").stdout.strip()
    dirty = git(checkout, "status", "--porcelain", "--untracked-files=no")
    if dirty.stdout or not stamp.exists():
        return True
    return stamp.read_text().strip() != head


def record_build(ws: Workspace, name: str, checkout: Path) -> None:
    """Remember the commit `name` was just built from."""
    stamp = ws.state / "built" / name
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(git(checkout, "rev-parse", "HEAD").stdout)


def build_targets(ws: Workspace, names: Sequence[str]) -> list[str]:
    """Build each named target that changed and return those that failed."""
    failed = []
    for name in names:
        target = TARGETS[name]
        checkout = ws.root / target.source
        if not (checkout / ".git").exists():
            say(f"{name}: {target.source} is not cloned")
            failed.append(name)
            continue
        if not needs_build(ws, name, checkout):
            continue
        head = git(checkout, "rev-parse", "--short=10", "HEAD").stdout
        say(f"build {name} at {head.strip()}")
        try:
            target.build(ws, checkout)
        except (subprocess.CalledProcessError, BuildError, OSError) as error:
            say(f"{name}: {error}")
            failed.append(name)
            continue
        record_build(ws, name, checkout)
    return failed


STATUS_TOOLS: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("uv", ("uv", "--version"), UV_MIN),
    ("go", ("go", "env", "GOVERSION"), GO_VERSION),
    ("rustc", ("rustc", "--version"), RUST_MIN),
    ("node", ("node", "--version"), NODE_MAJOR),
    ("dotnet", ("dotnet", "--version"), DOTNET_MIN),
    ("java", ("java", "-version"), JDK_MAJOR),
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


def check(ws: Workspace) -> None:
    """Print which tools, repos, builds and system packages are in place."""
    print("tools")
    for name, args, minimum in STATUS_TOOLS:
        print(row(name, tool_version(*args), minimum))
    if have("docker") and capture(["docker", "info"], timeout=30).returncode:
        print("  docker is installed but not running, hive needs it")

    print(f"repos in {ws.root}")
    repos = read_manifest()
    missing = [r.dir for r in repos if not (ws.root / r.dir / ".git").exists()]
    if missing:
        print(f"  missing {' '.join(missing)}")
    else:
        print(f"  all {len(repos)} cloned")

    print(f"builds in {ws.bin}")
    for name in TARGETS:
        stamp = ws.state / "built" / name
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
        "targets",
        nargs="*",
        metavar="target",
        help=f"build only these: {' '.join(TARGETS)}",
    )
    args = parser.parse_args(argv)
    for name in args.targets:
        if name not in TARGETS:
            parser.error(f"unknown target {name}, see --help")

    ws = Workspace.from_env()
    # Builds run their own uv projects and must never wait on a prompt.
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    use_toolchains(ws)
    if args.check:
        check(ws)
        return 0

    for tool in ("git", "curl", "tar"):
        if not have(tool):
            raise SystemExit(f"setup: install {tool} first")
    sync_repos(ws, read_manifest())
    try:
        install_tools(ws)
    except subprocess.CalledProcessError as error:
        command = " ".join(map(str, error.cmd))
        raise SystemExit(f"setup: {command} failed") from error
    link_commands(ws)
    failed = build_targets(ws, args.targets or list(TARGETS))
    check(ws)
    if failed:
        say(f"build failed: {' '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
