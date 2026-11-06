"""Tests for the test runners' output parsers and report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import runners


def fake(path: Path, script: str) -> Path:
    """Write an executable shell script standing in for a client runner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return path


def fixtures(
    root: Path, *names: str, directory: str = "blockchain_tests"
) -> Path:
    """Create empty fixture files under root/<directory>."""
    tests = root / directory
    for name in names:
        (tests / name).parent.mkdir(parents=True, exist_ok=True)
        (tests / name).write_text("{}")
    return tests


def test_last_json_array_skips_progress_before_it() -> None:
    """Besu prints progress lines before its JSON array."""
    output = 'Considering a\n[ignored\n[\n  {"name": "a", "pass": true}\n]\n'
    assert runners.last_json_array(output) == [{"name": "a", "pass": True}]
    assert runners.last_json_array("no results\n") is None


def test_fixture_files_skip_hidden_directories(tmp_path: Path) -> None:
    """A release's .meta files are not fixtures."""
    tests = fixtures(tmp_path, "a/x.json", ".meta/index.json", "b/notes.txt")
    assert runners.fixture_files(tests) == [tests / "a" / "x.json"]


def test_a_release_root_resolves_to_its_blockchain_tests(
    tmp_path: Path,
) -> None:
    """Passing a whole release runs only its blockchain tests."""
    tests = fixtures(tmp_path, "a/x.json")
    found = runners.find_fixtures(tests / "a", "blockchain_tests")
    assert runners.find_fixtures(tmp_path, "blockchain_tests") == tests
    assert found == tests / "a"


def test_libtest_results_carry_failure_messages() -> None:
    """The ethrex runner reports per file, with messages at the end."""
    output = "\n".join(
        [
            "running 2 tests",
            "test blockchain_runner::a/x.json      ... ok",
            "test blockchain_runner::a/y.json ... FAILED",
            "",
            "failures:",
            "",
            "---- blockchain_runner::a/y.json ----",
            "y_test: state root mismatch",
            "",
            "",
            "failures:",
            "    blockchain_runner::a/y.json",
            "",
            "test result: FAILED. 1 passed; 1 failed; 0 ignored",
        ]
    )
    assert runners.libtest_results(output) == [
        runners.Result("a/x.json", "pass"),
        runners.Result("a/y.json", "fail", "y_test: state root mismatch"),
    ]


def test_unittest2_results_carry_failure_details() -> None:
    """The nimbus runner prints a failure's details before its status."""
    output = "\n".join(
        [
            "[Suite] ",
            "  [OK     ] (  0.01s) good_test from /f/x.json",
            "========",
            "  /bin/eest_blockchain '::bad_test from /f/x.json'",
            "--------",
            "    eest_blockchain.nim(330, 9): Check failed",
            '    testResult was err("state root mismatch")',
            "  [FAILED ] (  0.02s) bad_test from /f/x.json",
            "  [SKIPPED] (  0.00s) slow_test from /f/x.json",
        ]
    )
    results = runners.unittest2_results(output)
    assert [(r.name, r.status) for r in results] == [
        ("good_test", "pass"),
        ("bad_test", "fail"),
        ("slow_test", "skip"),
    ]
    assert "state root mismatch" in results[1].error
    assert "eest_blockchain '" not in results[1].error


def test_reth_maps_its_mirror_paths_back(tmp_path: Path) -> None:
    """The reth runner gets a mirror and reports only failed files."""
    tests = fixtures(tmp_path / "f", "a/x.json", "a/y.json", "b/z.json")
    binary = fake(
        tmp_path / "ef-test-runner",
        'case="$1/blockchain_tests/fixtures"\n'
        'test -L "$case/a/y.json" || exit 2\n'
        'echo "Suite: fixtures (at $case)"\n'
        'echo "Ran 3 tests (1 passed, 1 failed, 1 skipped)"\n'
        'echo "[S] Case $case/b/z.json skipped"\n'
        'echo "[!] Case $case/a/y.json failed (description: y): bad root"\n'
        "exit 101\n",
    )
    run = runners.run_reth(binary, tests, 1)
    assert not run.error
    assert run.results == [
        runners.Result("a/x.json", "pass"),
        runners.Result("a/y.json", "fail", "y): bad root"),
        runners.Result("b/z.json", "skip"),
    ]


def test_a_runner_that_prints_nothing_is_broken(tmp_path: Path) -> None:
    """A crash is reported as a broken runner, not as zero failures."""
    tests = fixtures(tmp_path / "f", "a/x.json")
    binary = fake(
        tmp_path / "evm", "echo 'flag provided but not defined' >&2\nexit 1\n"
    )
    run = runners.run_geth(binary, tests, 1)
    assert run.results == []
    assert "flag provided" in run.error


def test_main_reports_each_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Built clients run, missing ones say how to build them."""
    tests = fixtures(tmp_path / "f", "a/x.json")
    results = [
        {"name": "t1", "pass": True, "fork": "Osaka"},
        {"name": "t2", "pass": False, "error": "bad root"},
    ]
    fake(
        tmp_path / "bin" / "geth" / "evm",
        f"echo 'INFO log' >&2\necho '{json.dumps(results)}'\n",
    )
    monkeypatch.setattr(runners, "BIN", tmp_path / "bin")
    monkeypatch.setattr(runners, "WORK", tmp_path / "work")

    code = runners.main(["blocktest", str(tests), "geth", "besu", "--json"])

    report = json.loads(capsys.readouterr().out)
    geth, besu = report["clients"]
    assert code == 1
    assert (geth["passed"], geth["failed"]) == (1, 1)
    assert geth["failures"] == [{"name": "t2", "error": "bad root"}]
    assert besu["state"] == "not built"
    saved = json.loads((tmp_path / "work/blocktest/geth.json").read_text())
    assert [entry["status"] for entry in saved] == ["pass", "fail"]


def test_default_clients_follow_your_team(tmp_path: Path) -> None:
    """A client team runs its client; other teams run every client."""
    me = tmp_path / "me.toml"
    me.write_text('team = "besu"\n')
    assert runners.default_clients(me) == ["besu"]
    me.write_text('team = "specs"\n')
    assert runners.default_clients(me) == list(runners.CLIENTS)


def test_ethrex_state_reports_name_passes_and_failures(tmp_path: Path) -> None:
    """ef_tests-statev2 writes a line per pass and tables per failure."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "success_report_1.txt").write_text(
        'Test "test_a[fork_Osaka-state_test]" - Path "/f/a.json"\n'
    )
    divider = "═" * 71
    (reports / "failure_report_1.txt").write_text(
        "+------------------+\n"
        "| Test Information |\n"
        "+------+-----------+\n"
        "| Name | test_b[fork_Osaka-state_test] |\n"
        "| Path | /f/b.json |\n"
        "+------+-----------+\n"
        "\nERROR: Root Mismatch\n"
        "| Expected | 0x01 |\n"
        f"\n{divider}\n\n"
    )
    results = runners.ethrex_reports(reports)
    assert [(r.name, r.status) for r in results] == [
        ("test_a[fork_Osaka-state_test]", "pass"),
        ("test_b[fork_Osaka-state_test]", "fail"),
    ]
    assert results[1].error.startswith("Root Mismatch\n")


def test_statetest_runs_per_file_runners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nimbus's evmstate runs once per file; reth's runner is evm2."""
    tests = fixtures(
        tmp_path / "f",
        "a/x.json",
        "a/y.json",
        "a/z.json",
        directory="state_tests",
    )
    fake(
        tmp_path / "bin" / "nimbus" / "evmstate",
        'case "$1" in\n'
        '  *x.json) echo \'[{"name": "x", "pass": true}]\' ;;\n'
        "  *y.json) echo 'cannot parse' >&2; exit 1 ;;\n"
        "esac\n",
    )
    monkeypatch.setattr(runners, "BIN", tmp_path / "bin")
    monkeypatch.setattr(runners, "WORK", tmp_path / "work")

    code = runners.main(
        ["statetest", str(tmp_path / "f"), "nimbus", "reth", "--json"]
    )

    report = json.loads(capsys.readouterr().out)
    nimbus, reth = report["clients"]
    assert code == 1
    assert report["fixtures"] == str(tests.resolve())
    assert (nimbus["passed"], nimbus["failed"], nimbus["skipped"]) == (1, 1, 1)
    failure = {"name": "a/y.json", "error": "cannot parse"}
    assert nimbus["failures"] == [failure]
    assert (reth["state"], reth["binary"]) == ("not built", "evm2/evm2")


def test_json_line_results_read_evm2() -> None:
    """evm2 prints one JSON object per state test among other lines."""
    output = "\n".join(
        [
            '{"stateRoot": "0x01", "pass": true, "errorMsg": "", "test": "a"}',
            '{"pass": false, "errorMsg": "root mismatch", "test": "b"}',
            "ok: state x.json: 2 executed, 0 skipped",
        ]
    )
    assert runners.json_line_results(output) == [
        runners.Result("a", "pass"),
        runners.Result("b", "fail", "root mismatch"),
    ]


def test_evmone_results_give_the_file_verdict() -> None:
    """`evmone test` names only fixtures that failed or were skipped."""
    failed = "\n".join(
        [
            "=================== test session starts ===================",
            "collected 1 file",
            "",
            "F                                                    [100%]",
            "",
            "======================= FAILURES ==========================",
            "___________________ osaka/x.json __________________________",
            "state root mismatch",
            "",
            "================= short test summary info =================",
            "FAILED  test_x[fork_Osaka-state_test] - state root mismatch",
            "",
            "================ 1 failed in 0.01s ========================",
        ]
    )
    passed = "==================== 1 passed in 0.00s ===================="
    assert runners.evmone_results(failed) == [
        runners.Result(
            "", "fail", "test_x[fork_Osaka-state_test]: state root mismatch"
        )
    ]
    assert runners.evmone_results(passed) == [runners.Result("", "pass")]
    assert runners.evmone_results("Segmentation fault\n") == []
