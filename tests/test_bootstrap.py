"""Tests for bootstrap.py, using local git repos and no network."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import bootstrap
from bootstrap import Repo, Target, Workspace

GH = "https://github.com"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """Return a workspace that lives under a temporary home."""
    home = tmp_path / "home"
    root = home / "ethereum"
    return Workspace(
        root=root, bin=root / "bin", home=home, system="darwin", arch="arm64"
    )


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run git with a fixed identity and none of the user's config."""
    config = tmp_path / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


def git(path: Path, *args: str) -> str:
    """Run git in `path` and return its output."""
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(path: Path, name: str) -> None:
    """Commit a new file called `name`."""
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", name)


@pytest.fixture
def upstream(tmp_path: Path, git_env: None) -> Path:
    """Return a repo with one commit, to clone from."""
    path = tmp_path / "upstream"
    path.mkdir()
    git(path, "init", "-q")
    commit(path, "first")
    return path


def test_manifest_skips_comments_and_reads_branches(tmp_path: Path) -> None:
    manifest = tmp_path / "repos.txt"
    manifest.write_text(
        "# heading\n\n"
        f"specs  {GH}/ethereum/execution-specs.git\n"
        f"clients/geth  {GH}/ethereum/go-ethereum  devnet  # note\n"
        f"archive  {GH}/ethereum/eth-rnd-archive.git  shallow\n"
    )
    assert bootstrap.read_manifest(manifest) == [
        Repo("specs", f"{GH}/ethereum/execution-specs.git"),
        Repo("clients/geth", f"{GH}/ethereum/go-ethereum", "devnet"),
        Repo("archive", f"{GH}/ethereum/eth-rnd-archive.git", shallow=True),
    ]


def test_manifest_rejects_a_line_without_a_url(tmp_path: Path) -> None:
    manifest = tmp_path / "repos.txt"
    manifest.write_text("specs\n")
    with pytest.raises(SystemExit, match="line 1"):
        bootstrap.read_manifest(manifest)


@pytest.mark.parametrize(
    "url",
    [
        f"{GH}/etherium/go-ethereum.git",
        "git@github.com:ethereum/go-ethereum.git",
        "http://github.com/ethereum/go-ethereum.git",
        "https://gitlab.com/ethereum/go-ethereum.git",
        "ext::sh%20-c%20id",
    ],
)
def test_manifest_rejects_untrusted_urls(tmp_path: Path, url: str) -> None:
    manifest = tmp_path / "repos.txt"
    manifest.write_text(f"geth {url}\n")
    with pytest.raises(SystemExit, match="trusted owner"):
        bootstrap.read_manifest(manifest)


def test_repos_txt_has_every_build_source_once() -> None:
    dirs = [repo.dir for repo in bootstrap.read_manifest()]
    assert len(dirs) == len(set(dirs))
    for target in bootstrap.TARGETS.values():
        assert target.source in dirs


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ('openjdk version "21.0.5" 2024-10-15', (21, 0, 5)),
        ('openjdk version "21" 2023-09-19', (21,)),
        ("go1.25.7", (1, 25, 7)),
        ("jq-1.7.1", (1, 7, 1)),
        ("v22.23.4", (22, 23, 4)),
        ("gcc (Debian 14.2.0-19) 14.2.0", (14, 2, 0)),
        ("Unable to locate a Java Runtime.", None),
    ],
)
def test_parse_version(output: str, version: tuple[int, ...] | None) -> None:
    assert bootstrap.parse_version(output) == version


@pytest.mark.parametrize(
    ("found", "minimum", "expected"),
    [
        ((1, 25, 7), "1.25.7", True),
        ((1, 24, 4), "1.25.7", False),
        ((1, 85, 0), "1.93", False),
        ((21, 0, 5), "21", True),
        ((10, 0, 300), "10.0", True),
        (None, "1.0", False),
    ],
)
def test_at_least(
    found: tuple[int, ...] | None, minimum: str, expected: bool
) -> None:
    assert bootstrap.at_least(found, minimum) is expected


def test_sync_clones_then_fast_forwards(
    upstream: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "ws" / "repo"
    repo = Repo("repo", upstream.as_uri())
    assert bootstrap.sync_repo(checkout, repo) == "cloned"
    assert bootstrap.sync_repo(checkout, repo) == "up to date"
    commit(upstream, "second")
    assert bootstrap.sync_repo(checkout, repo) == "updated"
    assert (checkout / "second").exists()


def test_sync_leaves_local_changes_alone(
    upstream: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "repo"
    repo = Repo("repo", upstream.as_uri())
    bootstrap.sync_repo(checkout, repo)
    (checkout / "first").write_text("edited")
    commit(upstream, "second")
    result = bootstrap.sync_repo(checkout, repo)
    assert result == "has local changes, fetched only"
    assert (checkout / "first").read_text() == "edited"
    assert not (checkout / "second").exists()


def test_sync_leaves_a_diverged_branch_alone(
    upstream: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "repo"
    repo = Repo("repo", upstream.as_uri())
    bootstrap.sync_repo(checkout, repo)
    commit(checkout, "mine")
    commit(upstream, "theirs")
    assert bootstrap.sync_repo(checkout, repo) == "diverged, fetched only"
    assert (checkout / "mine").exists()


def test_sync_checks_out_the_listed_branch(
    upstream: Path, tmp_path: Path
) -> None:
    git(upstream, "checkout", "-q", "-b", "devnet")
    commit(upstream, "devnet-only")
    git(upstream, "checkout", "-q", "main")
    checkout = tmp_path / "repo"
    bootstrap.sync_repo(checkout, Repo("repo", upstream.as_uri(), "devnet"))
    assert git(checkout, "branch", "--show-current") == "devnet"


def test_sync_reports_a_failed_clone(tmp_path: Path, git_env: None) -> None:
    missing = Repo("repo", (tmp_path / "missing").as_uri())
    result = bootstrap.sync_repo(tmp_path / "repo", missing)
    assert result.startswith("clone failed")


def test_shallow_sync_keeps_only_the_newest_commit(
    upstream: Path, tmp_path: Path
) -> None:
    commit(upstream, "second")
    checkout = tmp_path / "archive"
    repo = Repo("archive", upstream.as_uri(), shallow=True)
    assert bootstrap.sync_repo(checkout, repo) == "cloned"
    assert git(checkout, "rev-list", "--count", "HEAD") == "1"
    assert bootstrap.sync_repo(checkout, repo) == "up to date"
    commit(upstream, "third")
    assert bootstrap.sync_repo(checkout, repo) == "updated"
    assert (checkout / "third").exists()
    assert (checkout / ".git" / "shallow").exists()


def test_shallow_sync_keeps_local_commits(
    upstream: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "archive"
    repo = Repo("archive", upstream.as_uri(), shallow=True)
    bootstrap.sync_repo(checkout, repo)
    commit(checkout, "mine")
    commit(upstream, "theirs")
    result = bootstrap.sync_repo(checkout, repo)
    assert result == "has local commits, fetched only"
    assert (checkout / "mine").exists()


def test_installers_leave_new_enough_tools_alone(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(bootstrap, "have", lambda name: True)
    monkeypatch.setattr(bootstrap, "tool_version", lambda *args: (999,))
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(bootstrap, "fetch", lambda *a: calls.append(a))
    for install in bootstrap.INSTALLERS:
        install(ws)
    assert calls == []


def test_go_downloads_the_build_for_this_machine(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []

    def fetch(url: str, name: str) -> Path:
        urls.append(url)
        return ws.home / name

    monkeypatch.setattr(bootstrap, "tool_version", lambda *args: (1, 24))
    monkeypatch.setattr(bootstrap, "fetch", fetch)
    monkeypatch.setattr(bootstrap, "unpack", lambda archive, dest: None)
    bootstrap.install_go(ws)
    assert urls == [
        f"https://go.dev/dl/go{bootstrap.GO_VERSION}.darwin-arm64.tar.gz"
    ]


def test_dotnet_follows_nethermind_global_json(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = ws.root / "clients" / "nethermind" / "global.json"
    pinned.parent.mkdir(parents=True)
    pinned.write_text('{"sdk": {"version": "10.0.300"}}')
    calls: list[list[str]] = []
    monkeypatch.setattr(bootstrap, "fetch", lambda *a: Path("dotnet.sh"))
    monkeypatch.setattr(
        bootstrap, "run", lambda args, **k: calls.append(list(map(str, args)))
    )

    monkeypatch.setattr(bootstrap, "tool_version", lambda *a: (10, 0, 300))
    bootstrap.install_dotnet(ws)
    assert calls == []

    monkeypatch.setattr(bootstrap, "tool_version", lambda *a: (10, 0, 100))
    bootstrap.install_dotnet(ws)
    assert calls[0][-2:] == ["--jsonfile", str(pinned)]


def test_env_script_sets_homes_only_once_installed(ws: Workspace) -> None:
    assert "JAVA_HOME" not in bootstrap.env_script(ws, "sh")
    java = ws.local / "jdk" / "bin" / "java"
    java.parent.mkdir(parents=True)
    java.touch()
    jdk = ws.local / "jdk"
    assert f'export JAVA_HOME="{jdk}"' in bootstrap.env_script(ws, "sh")
    assert f"set -gx JAVA_HOME {jdk}" in bootstrap.env_script(ws, "fish")


def test_install_env_adds_one_line_per_shell(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    ws.home.mkdir(parents=True)
    (ws.home / ".bashrc").write_text("# mine\n")
    bootstrap.install_env(ws)
    bootstrap.install_env(ws)
    for rc in (".bashrc", ".zshrc"):
        assert (ws.home / rc).read_text().count("protocol-mesh/env.sh") == 1
    assert (ws.home / ".bashrc").read_text().startswith("# mine")


def test_find_clones_matches_forks_by_name(
    upstream: Path, tmp_path: Path
) -> None:
    mine = tmp_path / "code" / "geth"
    git(upstream, "clone", "-q", str(upstream), str(mine))
    git(mine, "remote", "set-url", "origin", f"{GH}/someone/go-ethereum.git")
    clones = bootstrap.find_clones([tmp_path / "code"])
    assert clones["someone/go-ethereum"] == mine
    assert clones["go-ethereum"] == mine


def test_reuse_clones_a_separate_copy(upstream: Path, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    git(upstream, "clone", "-q", str(upstream), str(mine))
    checkout = tmp_path / "repos" / "repo"
    repo = Repo("repo", upstream.as_uri())
    assert bootstrap.sync_repo(checkout, repo, reference=mine) == "cloned"
    assert not (checkout / ".git" / "objects" / "info" / "alternates").exists()
    assert (checkout / "first").exists()


def test_reuse_falls_back_when_the_reference_is_unusable(
    upstream: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "repos" / "repo"
    repo = Repo("repo", upstream.as_uri())
    missing = tmp_path / "missing"
    assert bootstrap.sync_repo(checkout, repo, reference=missing) == "cloned"


def test_needs_build_after_a_new_commit_or_local_changes(
    ws: Workspace, upstream: Path
) -> None:
    assert bootstrap.needs_build(ws, "geth", upstream)
    bootstrap.record_build(ws, "geth", upstream)
    assert not bootstrap.needs_build(ws, "geth", upstream)
    (upstream / "first").write_text("edited")
    assert bootstrap.needs_build(ws, "geth", upstream)
    git(upstream, "checkout", "-q", "first")
    commit(upstream, "second")
    assert bootstrap.needs_build(ws, "geth", upstream)


def test_a_failed_build_does_not_stop_the_others(
    ws: Workspace, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(upstream, "clone", "-q", str(upstream), str(ws.root / "src"))
    built: list[str] = []

    def broken(ws: Workspace, src: Path) -> None:
        raise subprocess.CalledProcessError(2, ["make"])

    def working(ws: Workspace, src: Path) -> None:
        built.append(src.name)

    monkeypatch.setattr(
        bootstrap,
        "TARGETS",
        {"a": Target("src", broken), "b": Target("src", working)},
    )
    assert bootstrap.build_targets(ws, ["a", "b"]) == ["a"]
    assert built == ["src"]
    assert (ws.stamps / "b").exists()
    assert not (ws.stamps / "a").exists()


def test_link_binary_fails_when_nothing_was_built(tmp_path: Path) -> None:
    with pytest.raises(bootstrap.BuildError):
        bootstrap.link_binary(tmp_path / "missing", tmp_path / "evm")


def test_an_unknown_target_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        bootstrap.main(["nope"])
    assert "unknown target nope" in capsys.readouterr().err


def test_profile_picks_its_repos_in_manifest_order(tmp_path: Path) -> None:
    repos = [Repo("a", f"{GH}/ethereum/a"), Repo("b", f"{GH}/ethereum/b")]
    (tmp_path / "small.txt").write_text("# comment\nb\n\na  # note\n")
    picked = bootstrap.read_profile("small", repos, tmp_path)
    assert [repo.dir for repo in picked] == ["a", "b"]


def test_profile_rejects_repos_the_manifest_lacks(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("ghost\n")
    with pytest.raises(SystemExit, match="ghost"):
        bootstrap.read_profile("small", [], tmp_path)


def test_a_missing_profile_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no profile"):
        bootstrap.read_profile("nope", [], tmp_path)


def test_the_review_profile_matches_repos_txt() -> None:
    picked = bootstrap.read_profile("review", bootstrap.read_manifest())
    assert "execution-specs" in [repo.dir for repo in picked]
    assert not any(repo.dir.startswith("clients/") for repo in picked)
