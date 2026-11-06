#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""
Run blockchain or state test fixtures through each execution client's own
test runner and report pass and fail in one format.

    just blocktest <fixtures> [client ...] [--json]
    just statetest <fixtures> [client ...] [--json]

<fixtures> is a fixtures release or fill output, its blockchain_tests or
state_tests directory, or any directory or file below that. With no client
named it runs your team's client from .mesh/me.toml, or every client when
your team has none. `all` names every client. Each client's full results
and its runner's raw output go to work/blocktest/ or work/statetest/.

    blocktest   runner, built by ./setup.sh <client>      results per
    geth        evm blocktest                             test
    reth        ef-test-runner                            file
    nethermind  nethtest --blockTest                      test
    besu        evmtool block-test                        test
    erigon      evm blocktest                             test
    ethrex      ef_tests-blockchain's `all` test binary   file
    nimbus      eest_blockchain, run once per file        test

    statetest   runner, built by ./setup.sh <client>      results per
    geth        evm statetest                             test
    reth        evm2 replay, once per file, by evm2       test
    nethermind  nethtest --stateTest                      test
    besu        evmtool state-test                        test
    erigon      evm statetest                             test
    ethrex      ef_tests-statev2                          test
    nimbus      evmstate, run once per file               test
    evmone      evmone test, run once per file            file

reth has no state test runner of its own, so its state tests run on evm2,
the EVM it uses, built by ./setup.sh evm2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MESH = HERE.parent
BIN = Path(os.environ.get("WS_BIN", MESH / "bin"))
WORK = MESH / "work"
ME_FILE = MESH / ".mesh" / "me.toml"

CLIENTS = (
    "geth",
    "reth",
    "nethermind",
    "besu",
    "erigon",
    "ethrex",
    "nimbus",
    "evmone",
)

PASS, FAIL, SKIP = "pass", "fail", "skip"

# How many failures the readable report shows per client.
SHOWN = 5


@dataclass
class Result:
    """One test, or one fixture file for runners that report per file."""

    name: str
    status: str
    error: str = ""


@dataclass
class Run:
    """What one runner did with the fixtures."""

    results: list[Result] = field(default_factory=list)
    # Set when the runner itself broke, rather than a test failing.
    error: str = ""
    log: str = ""


@dataclass(frozen=True)
class Runner:
    """A client's test runner and how to read what it prints."""

    binary: str
    run: Callable[[Path, Path, int], Run]
    per: str = "test"


def execute(
    args: list[str | Path],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a runner to completion and return its output, never raising."""
    argv = [str(arg) for arg in args]
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env={**os.environ, **env} if env else None,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except OSError as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def log_of(*done: subprocess.CompletedProcess[str]) -> str:
    """Return the commands and output of finished runner processes."""
    parts = []
    for process in done:
        parts.append("$ " + " ".join(map(str, process.args)))
        parts.append(f"exit {process.returncode}")
        parts.append(process.stdout)
        parts.append(process.stderr)
    return "\n".join(parts)


def tail(text: str, lines: int = 20) -> str:
    """Return the last few non-empty lines of `text`."""
    kept = [line for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def last_json_array(text: str) -> list[Any] | None:
    """
    Return the last JSON array printed on its own lines in `text`, since
    some runners print progress before their results.
    """
    starts = [match.start() for match in re.finditer(r"^\[", text, re.M)]
    for start in reversed(starts):
        try:
            found = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(found, list):
            return found
    return None


def fixture_files(path: Path) -> list[Path]:
    """Return the fixture files at `path`, a file or a directory."""
    if path.is_file():
        return [path]
    return sorted(
        file
        for file in path.rglob("*.json")
        if not any(p.startswith(".") for p in file.relative_to(path).parts)
    )


def fixture_name(file: Path, fixtures: Path) -> str:
    """Return a fixture file's name in results, relative to `fixtures`."""
    if fixtures.is_dir():
        return str(file.relative_to(fixtures))
    return file.name


def json_results(process: subprocess.CompletedProcess[str]) -> Run:
    """
    Read the JSON array of {name, pass, error} that most runners print.
    """
    log = log_of(process)
    found = last_json_array(process.stdout)
    if found is None:
        return Run(error=tail(process.stderr or process.stdout), log=log)
    return Run(
        log=log,
        results=[
            Result(
                name=str(entry.get("name", "")),
                status=PASS if entry.get("pass") else FAIL,
                error=str(entry.get("error") or ""),
            )
            for entry in found
        ],
    )


def per_file(
    command: Callable[[Path], list[str | Path]],
    read: Callable[[str], list[Result]],
    fixtures: Path,
    workers: int,
    env: dict[str, str] | None = None,
) -> Run:
    """
    Run a runner that takes one file at a time on every fixture file,
    several at a time, and read each run's output with `read`. A result
    `read` leaves unnamed is the file's own.
    """

    def one(file: Path) -> tuple[Path, subprocess.CompletedProcess[str]]:
        return file, execute(command(file), env=env)

    results = []
    # Only the runs that failed, since there is one per file.
    logs = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for file, process in pool.map(one, fixture_files(fixtures)):
            name = fixture_name(file, fixtures)
            found = read(process.stdout)
            for result in found:
                result.name = result.name or name
            # A runner that reports nothing broke on the file when it exits
            # with an error, and otherwise ran nothing from it.
            if not found and process.returncode:
                error = tail(process.stderr or process.stdout)
                found = [Result(name, FAIL, error)]
            elif not found:
                found = [Result(name, SKIP, "no tests ran")]
            results.extend(found)
            if process.returncode:
                logs.append(log_of(process))
    return Run(results=results, log="\n".join(logs))


def json_array_results(output: str) -> list[Result]:
    """Read the results of a per-file runner that prints a JSON array."""
    found = last_json_array(output) or []
    return [
        Result(
            str(entry.get("name", "")),
            PASS if entry.get("pass") else FAIL,
            str(entry.get("error") or ""),
        )
        for entry in found
    ]


NETCORE = re.compile(r"Microsoft\.NETCore\.App \S+ \[(.+)\]")


def dotnet_root() -> dict[str, str]:
    """
    Return DOTNET_ROOT when it is unset, from where the dotnet on PATH keeps
    its runtimes, since nethtest looks only in the default place and a
    Homebrew .NET is elsewhere.
    """
    if "DOTNET_ROOT" in os.environ:
        return {}
    match = NETCORE.search(execute(["dotnet", "--list-runtimes"]).stdout)
    if match is None:
        return {}
    return {"DOTNET_ROOT": str(Path(match.group(1)).parent.parent)}


def java_home() -> dict[str, str]:
    """
    Return JAVA_HOME for the JDK ./setup.sh installs when the java on PATH
    is too old, since besu is built with it and needs it to run.
    """
    jdk = Path.home() / ".local" / "jdk"
    if (jdk / "bin" / "java").exists():
        return {"JAVA_HOME": str(jdk)}
    return {}


# Blockchain tests.


def run_geth(binary: Path, fixtures: Path, _workers: int) -> Run:
    """Run `evm blocktest`, which runs one test at a time."""
    return json_results(execute([binary, "blocktest", fixtures]))


def run_erigon(binary: Path, fixtures: Path, workers: int) -> Run:
    """Run erigon's `evm blocktest`."""
    options = ["--jsonout", f"--workers={workers}"]
    return json_results(execute([binary, "blocktest", *options, fixtures]))


def run_besu(binary: Path, fixtures: Path, workers: int) -> Run:
    """Run `evmtool block-test`."""
    options = ["--json-array", f"--workers={workers}"]
    process = execute(
        [binary, "block-test", *options, fixtures], env=java_home()
    )
    return json_results(process)


def run_nethermind(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run `nethtest --blockTest`. Current builds always print JSON, older
    ones only with --jsonout.
    """
    options = ["--jsonout", "--workers", str(workers)]
    process = execute(
        [binary, "--blockTest", "--input", fixtures, *options],
        env=dotnet_root(),
    )
    return json_results(process)


def mirror(fixtures: Path, into: Path) -> None:
    """
    Recreate the tree at `fixtures` under `into` with real directories and
    linked files, for runners that will not follow a linked directory.
    """
    for file in fixture_files(fixtures):
        link = into / fixture_name(file, fixtures)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(file)


RETH_CASE = re.compile(r"^\[(!|S)\] Case (.+?) (?:failed|skipped)(.*)$")


def run_reth(binary: Path, fixtures: Path, _workers: int) -> Run:
    """
    Run reth's ef-test-runner, which wants <suite>/blockchain_tests/<dir>,
    prints only failed and skipped files, and stops at its first failing
    directory. So every fixture goes into one directory of a mirror.
    """
    with tempfile.TemporaryDirectory(prefix="blocktest-reth-") as temp:
        suite = Path(temp).resolve()
        cases = suite / "blockchain_tests" / "fixtures"
        mirror(fixtures, cases)
        process = execute([binary, suite])
    log = log_of(process)
    if "Ran " not in process.stdout:
        return Run(error=tail(process.stderr or process.stdout), log=log)
    marked = {}
    for line in process.stdout.splitlines():
        match = RETH_CASE.match(line)
        if match is None:
            continue
        mark, path, rest = match.groups()
        name = path.removeprefix(f"{cases}/")
        error = rest.removeprefix(" (description: ").strip()
        marked[name] = (FAIL if mark == "!" else SKIP, error)
    results = []
    for file in fixture_files(fixtures):
        name = fixture_name(file, fixtures)
        status, error = marked.get(name, (PASS, ""))
        results.append(Result(name, status, error))
    return Run(results=results, log=log)


LIBTEST_LINE = re.compile(r"^test (\S+)\s+\.\.\. (ok|FAILED|ignored)")
LIBTEST_FAILURE = re.compile(r"^---- (\S+) .*----$")


def libtest_results(output: str) -> list[Result]:
    """
    Read libtest-style output, where each failure's message follows a
    `---- <test> ----` line near the end.
    """
    errors: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in output.splitlines():
        if (match := LIBTEST_FAILURE.match(line)) is not None:
            current = errors.setdefault(match.group(1), [])
        elif line == "failures:":
            current = None
        elif current is not None:
            current.append(line)
    statuses = {"ok": PASS, "FAILED": FAIL, "ignored": SKIP}
    results = []
    for line in output.splitlines():
        if (match := LIBTEST_LINE.match(line)) is None:
            continue
        test, outcome = match.groups()
        # datatest names each test <function>::<file under vectors/>.
        name = test.split("::", 1)[-1]
        error = "\n".join(errors.get(test, [])).strip()
        results.append(Result(name, statuses[outcome], error))
    return results


def run_ethrex(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run ethrex's blockchain test binary, which runs every file under
    vectors/ in its working directory and reports per file. It skips
    pre-Merge tests and passes the file.
    """
    with tempfile.TemporaryDirectory(prefix="blocktest-ethrex-") as temp:
        vectors = Path(temp) / "vectors"
        if fixtures.is_dir():
            vectors.symlink_to(fixtures)
        else:
            vectors.mkdir()
            shutil.copy(fixtures, vectors / fixtures.name)
        process = execute(
            [binary, "--color=never", f"--test-threads={workers}"],
            cwd=Path(temp),
        )
    log = log_of(process)
    results = libtest_results(process.stdout)
    if not results:
        return Run(error=tail(process.stderr or process.stdout), log=log)
    return Run(results=results, log=log)


UNITTEST_STATUS = re.compile(
    r"^\s*\[(OK|FAILED|SKIPPED)\s*\]\s*\(\s*[\d.]+s\)\s(.*)$"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def unittest2_results(output: str) -> list[Result]:
    """
    Read nimbus's unittest2 output at the VERBOSE level, where a failure's
    details sit between a line of dashes and its status line.
    """
    statuses = {"OK": PASS, "FAILED": FAIL, "SKIPPED": SKIP}
    results = []
    since: list[str] = []
    for line in ANSI.sub("", output).splitlines():
        match = UNITTEST_STATUS.match(line)
        if match is None:
            if set(line.strip()) == {"-"}:
                since = []
            else:
                since.append(line)
            continue
        outcome, name = match.groups()
        error = "\n".join(since).strip() if outcome == "FAILED" else ""
        # nimbus names each test "<test> from <file>".
        test = name.rsplit(" from ", 1)[0]
        results.append(Result(test, statuses[outcome], error))
        since = []
    return results


def run_nimbus(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run nimbus's eest_blockchain on each file. Run alone it also checks
    every block statelessly.
    """
    env = {"UNITTEST2_OUTPUT_LVL": "VERBOSE", "NIMTEST_COLOR": "never"}
    return per_file(
        lambda file: [binary, file], unittest2_results, fixtures, workers, env
    )


# State tests.


def run_geth_state(binary: Path, fixtures: Path, _workers: int) -> Run:
    """Run `evm statetest`, which runs one test at a time."""
    return json_results(execute([binary, "statetest", fixtures]))


def json_line_results(output: str) -> list[Result]:
    """Read the JSON object per test, one per line, that evm2 prints."""
    results = []
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and "test" in entry:
            status = PASS if entry.get("pass") else FAIL
            error = str(entry.get("errorMsg") or "")
            results.append(Result(str(entry["test"]), status, error))
    return results


def run_reth_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run `evm2 replay` on each file. reth has no state test runner, so this
    is the EVM it uses, evm2.
    """
    return per_file(
        lambda file: [binary, "replay", "--json-output", file],
        json_line_results,
        fixtures,
        workers,
    )


EVMONE_COUNT = re.compile(r"(\d+) (failed|passed|skipped)")
EVMONE_NOTE = re.compile(r"^(FAILED|SKIPPED) +(.+?) - (.*)$")


def evmone_results(output: str) -> list[Result]:
    """
    Read what `evmone test` prints for one file: a pytest-style summary
    line, and a line naming each fixture that failed or was skipped.
    """
    lines = output.strip().splitlines()
    if not lines or not lines[-1].startswith("="):
        return []
    counts = {kind: int(n) for n, kind in EVMONE_COUNT.findall(lines[-1])}
    notes = [m.groups() for m in map(EVMONE_NOTE.match, lines) if m]
    if counts.get("failed"):
        status, mark = FAIL, "FAILED"
    elif counts.get("passed"):
        status, mark = PASS, ""
    else:
        status, mark = SKIP, "SKIPPED"
    reasons = [f"{name}: {why}" for kind, name, why in notes if kind == mark]
    return [Result("", status, "\n".join(reasons))]


def run_evmone_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run `evmone test` on each file. It reports per file, naming only the
    fixtures in it that failed or were skipped.
    """
    return per_file(
        lambda file: [binary, "test", file], evmone_results, fixtures, workers
    )


def run_erigon_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """Run erigon's `evm statetest`."""
    options = ["--jsonout", f"--workers={workers}"]
    return json_results(execute([binary, "statetest", *options, fixtures]))


def run_besu_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """Run `evmtool state-test`."""
    options = ["--json-array", f"--workers={workers}"]
    process = execute(
        [binary, "state-test", *options, fixtures], env=java_home()
    )
    return json_results(process)


def run_nethermind_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """
    Run `nethtest --stateTest`, without the traces it writes for failures
    by default.
    """
    options = ["--jsonout", "--neverTrace", "--workers", str(workers)]
    process = execute(
        [binary, "--stateTest", "--input", fixtures, *options],
        env=dotnet_root(),
    )
    return json_results(process)


ETHREX_PASS = re.compile(r'^Test "(.*)" - Path ".*"$')
ETHREX_NAME = re.compile(r"^\|\s*Name\s*\|\s*(.*?)\s*\|\s*$")


def ethrex_reports(reports: Path) -> list[Result]:
    """
    Read the success and failure reports ef_tests-statev2 writes: a line
    per passing test, and a block of tables per failing one.
    """
    results = []
    for report in sorted(reports.glob("success_report_*.txt")):
        for line in report.read_text().splitlines():
            if (match := ETHREX_PASS.match(line)) is not None:
                results.append(Result(match.group(1), PASS))
    for report in sorted(reports.glob("failure_report_*.txt")):
        for block in report.read_text().split("═" * 20):
            lines = block.splitlines()
            names = [m.group(1) for m in map(ETHREX_NAME.match, lines) if m]
            if not names:
                continue
            reasons = [
                line.removeprefix("ERROR: ")
                for line in lines
                if line.startswith("ERROR: ")
            ]
            error = ", ".join(reasons) or "failed"
            results.append(Result(names[0], FAIL, f"{error}\n{block.strip()}"))
    return results


def run_ethrex_state(binary: Path, fixtures: Path, _workers: int) -> Run:
    """
    Run ethrex's ef_tests-statev2, which writes its results as reports
    under reports/ in its working directory.
    """
    with tempfile.TemporaryDirectory(prefix="statetest-ethrex-") as temp:
        process = execute([binary, "--path", fixtures], cwd=Path(temp))
        results = ethrex_reports(Path(temp) / "reports")
    log = log_of(process)
    if not results:
        return Run(error=tail(process.stderr or process.stdout), log=log)
    return Run(results=results, log=log)


def run_nimbus_state(binary: Path, fixtures: Path, workers: int) -> Run:
    """Run nimbus's evmstate on each file."""
    return per_file(
        lambda file: [binary, file], json_array_results, fixtures, workers
    )


@dataclass(frozen=True)
class Kind:
    """A kind of test, where a release keeps its fixtures, and its runners."""

    directory: str
    runners: dict[str, Runner]


KINDS = {
    "blocktest": Kind(
        "blockchain_tests",
        {
            "geth": Runner("geth/evm", run_geth),
            "reth": Runner("reth/ef-test-runner", run_reth, per="file"),
            "nethermind": Runner("nethermind/nethtest", run_nethermind),
            "besu": Runner("besu/evmtool", run_besu),
            "erigon": Runner("erigon/evm", run_erigon),
            "ethrex": Runner(
                "ethrex/ef-tests-blockchain", run_ethrex, per="file"
            ),
            "nimbus": Runner("nimbus/eest_blockchain", run_nimbus),
        },
    ),
    "statetest": Kind(
        "state_tests",
        {
            "geth": Runner("geth/evm", run_geth_state),
            "reth": Runner("evm2/evm2", run_reth_state),
            "nethermind": Runner("nethermind/nethtest", run_nethermind_state),
            "besu": Runner("besu/evmtool", run_besu_state),
            "erigon": Runner("erigon/evm", run_erigon_state),
            "ethrex": Runner("ethrex/ef_tests-statev2", run_ethrex_state),
            "nimbus": Runner("nimbus/evmstate", run_nimbus_state),
            "evmone": Runner("evmone/evmone", run_evmone_state, per="file"),
        },
    ),
}


def find_fixtures(path: Path, directory: str) -> Path:
    """Return the fixtures at `path`, a release or any part of one."""
    path = path.resolve()
    inner = path / directory
    return inner if inner.is_dir() else path


def default_clients(me_file: Path = ME_FILE) -> list[str]:
    """Return your team's client, or every client when it has none."""
    team = ""
    if me_file.exists():
        team = str(tomllib.loads(me_file.read_text()).get("team", ""))
    return [team] if team in CLIENTS else list(CLIENTS)


@dataclass
class Report:
    """One client's outcome, as --json prints it."""

    client: str
    state: str
    # The runner, under bin/, whose first part is its ./setup.sh target.
    binary: str = ""
    per: str = "test"
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    seconds: float = 0.0
    error: str = ""
    failures: list[dict[str, str]] = field(default_factory=list)
    results: str = ""
    log: str = ""


def count(results: list[Result], status: str) -> int:
    """Return how many results have `status`."""
    return sum(result.status == status for result in results)


def run_client(kind: str, name: str, fixtures: Path, workers: int) -> Report:
    """Run one client and save its full results under work/<kind>/."""
    runner = KINDS[kind].runners.get(name)
    if runner is None:
        return Report(name, "no runner")
    binary = BIN / runner.binary
    if not binary.exists():
        return Report(name, "not built", runner.binary, runner.per)
    started = time.monotonic()
    run = runner.run(binary, fixtures, workers)
    seconds = round(time.monotonic() - started, 1)
    out = WORK / kind
    out.mkdir(parents=True, exist_ok=True)
    results = out / f"{name}.json"
    results.write_text(
        json.dumps([asdict(result) for result in run.results], indent=1)
    )
    log = out / f"{name}.log"
    log.write_text(run.log)
    failed = [result for result in run.results if result.status == FAIL]
    return Report(
        client=name,
        state="broken" if run.error else "ran",
        binary=runner.binary,
        per=runner.per,
        passed=count(run.results, PASS),
        failed=len(failed),
        skipped=count(run.results, SKIP),
        seconds=seconds,
        error=run.error,
        failures=[{"name": r.name, "error": r.error} for r in failed],
        results=str(results),
        log=str(log) if run.log else "",
    )


def short(text: str, width: int = 160) -> str:
    """Return the first line of `text`, cut to `width`."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= width else line[: width - 1] + "…"


def readable(
    kind: str, fixtures: Path, files: int, reports: list[Report]
) -> Iterator[str]:
    """Yield the readable report, a table and then each client's failures."""
    yield f"fixtures  {fixtures}, {files} files"
    yield ""
    yield f"{'client':<11} {'passed':>7} {'failed':>7} {'skipped':>8}   time"
    for report in reports:
        if report.state == "no runner":
            yield f"{report.client:<11} has no {kind} runner"
            continue
        if report.state == "not built":
            build = f"./setup.sh {report.binary.split('/')[0]}"
            yield f"{report.client:<11} not built, run {build}"
            continue
        if report.state == "broken":
            yield f"{report.client:<11} runner failed, see below"
            continue
        per = "  per file" if report.per == "file" else ""
        yield (
            f"{report.client:<11} {report.passed:>7} {report.failed:>7} "
            f"{report.skipped:>8} {report.seconds:>6.0f}s{per}"
        )
    for report in reports:
        if report.state == "broken":
            yield ""
            yield f"{report.client}: the runner gave no results"
            yield from ("  " + line for line in report.error.splitlines())
        if report.failures:
            yield ""
            yield f"{report.client}: {report.failed} failed"
            for failure in report.failures[:SHOWN]:
                yield f"  {failure['name']}"
                if failure["error"]:
                    yield f"    {short(failure['error'])}"
            if report.failed > SHOWN:
                yield f"  and {report.failed - SHOWN} more"
        if report.results and (report.failures or report.state == "broken"):
            yield f"  all results: {report.results}"
        if report.log and (report.failures or report.state == "broken"):
            yield f"  runner output: {report.log}"


def main(argv: list[str] | None = None) -> int:
    """Run the named clients on the fixtures and return the exit code."""
    parser = argparse.ArgumentParser(
        prog="runners.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument(
        "clients",
        nargs="*",
        metavar="client",
        help=f"any of {' '.join(CLIENTS)}, or all",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="tests or files each runner runs at once, where it can",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    clients = args.clients or default_clients()
    if "all" in clients:
        clients = list(CLIENTS)
    for name in clients:
        if name not in CLIENTS:
            parser.error(
                f"unknown client {name}, pick from {' '.join(CLIENTS)}"
            )
    if not args.fixtures.exists():
        parser.error(f"no fixtures at {args.fixtures}")
    fixtures = find_fixtures(args.fixtures, KINDS[args.kind].directory)
    files = len(fixture_files(fixtures))
    if not files:
        parser.error(f"no fixture files under {fixtures}")

    # One client at a time, since each runner uses every core it is given.
    reports = []
    for name in dict.fromkeys(clients):
        if not args.json:
            print(f"running {name}", file=sys.stderr, flush=True)
        reports.append(run_client(args.kind, name, fixtures, args.workers))

    if args.json:
        print(
            json.dumps(
                {
                    "kind": args.kind,
                    "fixtures": str(fixtures),
                    "files": files,
                    "clients": [asdict(report) for report in reports],
                },
                indent=1,
            )
        )
    else:
        print("\n".join(readable(args.kind, fixtures, files, reports)))
    ran = [r for r in reports if r.state in ("ran", "broken")]
    clean = all(r.state == "ran" and not r.failed for r in ran)
    return 0 if ran and clean else 1


if __name__ == "__main__":
    sys.exit(main())
