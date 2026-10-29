"""Tests for latest.py, with fixed data and no network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import latest

NOW = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)


def test_since_takes_days_or_a_date() -> None:
    """7d means a week before now, and a date means its midnight UTC."""
    assert latest.parse_since("7d", NOW) == datetime(
        2026, 10, 22, 18, 0, tzinfo=UTC
    )
    assert latest.parse_since("2026-10-01", NOW) == datetime(
        2026, 10, 1, tzinfo=UTC
    )


def test_the_last_run_round_trips_and_starts_a_week_back(
    tmp_path: Path,
) -> None:
    """With no saved run it looks back a week, then from the saved time."""
    state = tmp_path / ".mesh" / "latest.toml"
    assert latest.read_last_run(state, NOW) == datetime(
        2026, 10, 22, 18, 0, tzinfo=UTC
    )
    latest.save_last_run(state, NOW)
    assert latest.read_last_run(state, NOW) == NOW


def test_a_client_team_gets_its_hive_client(tmp_path: Path) -> None:
    """The team's hive_client in teams.toml becomes the client to show."""
    me = tmp_path / "me.toml"
    me.write_text('login = "someone"\nteam = "geth"\neips = [7928]\n')
    teams = tmp_path / "teams.toml"
    teams.write_text(
        '[teams.geth]\ntitle = "go-ethereum"\nhive_client = "go-ethereum"\n'
        '[teams.specs]\ntitle = "EELS"\n'
    )
    assert latest.read_me(me, teams)["client"] == "go-ethereum"
    me.write_text('login = "someone"\nteam = "specs"\n')
    assert latest.read_me(me, teams)["client"] is None


def test_forks_show_the_live_one_and_those_coming() -> None:
    """The newest live fork is shown, then the upcoming ones' estimates."""
    upgrades = {
        "upgrades": [
            {"name": "Pectra Upgrade", "status": "Live"},
            {"name": "Fusaka Upgrade", "status": "Live"},
            {
                "name": "Glamsterdam Upgrade",
                "status": "Upcoming",
                "projectedActivation": "2026-12-02",
            },
        ]
    }
    assert latest.forks(lambda _url: upgrades) == [
        "Fusaka live · Glamsterdam upcoming, est. 2026-12-02"
    ]


def fake_calls(url: str) -> Any:
    """Serve two ACD calls, one of them without published decisions."""
    if url.endswith("/api/calls.json"):
        return {
            "calls": [
                {"type": "acde", "date": "2026-10-22", "number": "248"},
                {"type": "acdt", "date": "2026-10-26", "number": "102"},
                {"type": "aa", "date": "2026-10-27", "number": "003"},
            ]
        }
    if "acde/2026-10-22_248" in url:
        return {
            "key_decisions": [{"original_text": "A"}, {"original_text": "B"}]
        }
    return None


def test_decisions_list_each_new_call() -> None:
    """Each ACD call since then shows its decisions, or that none are out."""
    since = datetime(2026, 10, 20, tzinfo=UTC)
    assert latest.decisions(fake_calls, since) == [
        "ACDE #248 (2026-10-22): A; B",
        "ACDT #102 (2026-10-26): no decisions published yet",
    ]


def test_decisions_fall_back_to_the_last_call() -> None:
    """With no new call, the last ACD call is shown and marked as such."""
    since = datetime(2026, 10, 28, tzinfo=UTC)
    assert latest.decisions(fake_calls, since) == [
        "ACDT #102 (2026-10-26), the last call: no decisions published yet"
    ]


def test_stages_list_the_changes_since() -> None:
    """Only stage changes on or after the day are listed."""
    data = {
        "eips": [
            {
                "id": 8015,
                "currentStage": "Scheduled",
                "lastStageChangeFork": "Hegota",
                "lastStageChange": "2026-10-29",
            },
            {
                "id": 7928,
                "currentStage": "Scheduled",
                "lastStageChangeFork": "Glamsterdam",
                "lastStageChange": "2026-06-01",
            },
        ]
    }
    since = datetime(2026, 10, 28, tzinfo=UTC)
    assert latest.stages(lambda _url: data, since) == [
        "EIP-8015 Scheduled for Hegota"
    ]


def test_eels_shows_busy_branches_and_new_releases() -> None:
    """Branches with new commits come first, then releases since then."""

    def query(*args: str) -> Any:
        if args[0] == "graphql":
            nodes = [
                {
                    "name": "amsterdam",
                    "target": {"history": {"totalCount": 0}},
                },
                {"name": "bogota", "target": {"history": {"totalCount": 3}}},
            ]
            return {"data": {"repository": {"refs": {"nodes": nodes}}}}
        return [
            {"tag_name": "tests@v2", "published_at": "2026-10-29T10:00:00Z"},
            {"tag_name": "tests@v1", "published_at": "2026-10-01T10:00:00Z"},
        ]

    since = datetime(2026, 10, 28, tzinfo=UTC)
    assert latest.eels(query, since) == [
        "commits: forks/bogota +3",
        "releases: tests@v2",
    ]


SUMMARY = {
    "last_run": "2026-10-29T15:54:43Z",
    "clients": {"reth": {"fail": 56}, "besu": {"fail": 6}},
}
DIFF = {"regressions": {"reth": {"total_fails": 54}}}


def test_a_client_team_sees_only_its_client() -> None:
    """A client team's line counts only its own failures and regressions."""
    assert (
        latest.hive_line("g", SUMMARY, DIFF, "reth")
        == "g (10-29 15:54): 56 failing, 54 new"
    )
    assert latest.hive_line("g", SUMMARY, DIFF, "geth") is None


def test_the_eels_side_sees_every_client() -> None:
    """Without a client, the line totals every client and names the worst."""
    assert (
        latest.hive_line("g", SUMMARY, DIFF, None)
        == "g (10-29 15:54): 62 failing, 54 new (reth 54)"
    )
    assert (
        latest.hive_line("g", SUMMARY, None, None)
        == "g (10-29 15:54): 62 failing, ? new (still loading)"
    )
