#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""
Print what changed since your last session, from live sources: forks, ACD
decisions, EIP stage changes, EELS branches and releases, and hive results
for your team's client, or for every client on the EELS side.

  just latest               since the last run, saved in .mesh/latest.toml
  just latest --since 7d    or a date such as 2026-10-01, not saved
  just latest --json        the same, as JSON

Every source is fetched at once with a short timeout. A source that fails
or is slow says so on its line, the rest still print.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ME_FILE = HERE / ".mesh" / "me.toml"
TEAMS_FILE = HERE / "teams.toml"
STATE_FILE = HERE / ".mesh" / "latest.toml"

FORKCAST = "https://forkcast.org"
EELS = ("ethereum", "execution-specs")
ACD_CALLS = ("acde", "acdc", "acdt")
TIMEOUT = 5.0
# At most this many lines from one source, so the digest stays short.
MOST = 4

Fetch = Callable[[str], Any]


def fetch_json(url: str) -> Any:
    """Return the JSON at `url`, or None when it does not exist."""
    request = urllib.request.Request(url, headers={"User-Agent": "latest"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def gh(*args: str) -> Any:
    """Return the JSON output of `gh api`, using the user's own login."""
    result = subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=True,
    )
    return json.loads(result.stdout)


def parse_since(text: str, now: datetime) -> datetime:
    """Return the time `text` names: a number of days such as 7d, or a date."""
    if match := re.fullmatch(r"(\d+)d", text):
        return now - timedelta(days=int(match[1]))
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def read_last_run(path: Path, now: datetime) -> datetime:
    """Return when this last ran, or a week ago the first time."""
    if path.exists():
        return datetime.fromisoformat(tomllib.loads(path.read_text())["last"])
    return now - timedelta(days=7)


def save_last_run(path: Path, now: datetime) -> None:
    """Remember this run, in a file git ignores."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'last = "{now.isoformat(timespec="seconds")}"\n')


def read_me(me_file: Path, teams_file: Path) -> dict[str, Any]:
    """Return the user's login, team, EIPs and the team's hive client."""
    me = tomllib.loads(me_file.read_text()) if me_file.exists() else {}
    teams = tomllib.loads(teams_file.read_text())["teams"]
    team = teams.get(me.get("team", ""), {})
    return {
        "login": me.get("login", "?"),
        "team": me.get("team", "none"),
        "eips": me.get("eips", []),
        "client": team.get("hive_client"),
    }


def forks(fetch: Fetch) -> list[str]:
    """Return the live fork and the upcoming ones with their status."""
    upgrades = fetch(f"{FORKCAST}/api/upgrades.json")["upgrades"]
    live = [u for u in upgrades if u["status"] == "Live"]
    parts = (
        [f"{live[-1]['name'].removesuffix(' Upgrade')} live"] if live else []
    )
    for upgrade in upgrades:
        if upgrade["status"] == "Live":
            continue
        name = upgrade["name"].removesuffix(" Upgrade")
        part = f"{name} {upgrade['status'].lower()}"
        if upgrade.get("projectedActivation"):
            part += f", est. {upgrade['projectedActivation']}"
        parts.append(part)
    return [" · ".join(parts)]


def decisions(fetch: Fetch, since: datetime) -> list[str]:
    """Return the key decisions of ACD calls since `since`, or the last one."""
    calls = [
        c
        for c in fetch(f"{FORKCAST}/api/calls.json")["calls"]
        if c["type"] in ACD_CALLS
    ]
    calls.sort(key=lambda c: c["date"])
    new = [c for c in calls if c["date"] >= since.date().isoformat()]
    lines = []
    for call in new or calls[-1:]:
        label = (
            f"{call['type'].upper()} #{int(call['number'])} ({call['date']})"
        )
        path = f"{call['type']}/{call['date']}_{call['number']}"
        found = fetch(f"{FORKCAST}/artifacts/{path}/key_decisions.json")
        texts = [
            d["original_text"] for d in (found or {}).get("key_decisions", [])
        ]
        if not new:
            label += ", the last call"
        if not texts:
            lines.append(f"{label}: no decisions published yet")
            continue
        lines.append(f"{label}: " + "; ".join(texts[:3]))
    return lines[-MOST:]


def stages(fetch: Fetch, since: datetime) -> list[str]:
    """Return the EIP inclusion stage changes since `since`."""
    eips = fetch(f"{FORKCAST}/api/eip-stage-changes.json")["eips"]
    day = since.date().isoformat()
    changed = [e for e in eips if (e.get("lastStageChange") or "") >= day]
    if not changed:
        return [f"none since {day}"]
    shown = [
        f"EIP-{e['id']} {e['currentStage']} for {e['lastStageChangeFork']}"
        for e in changed[:6]
    ]
    more = f", +{len(changed) - 6} more" if len(changed) > 6 else ""
    return [", ".join(shown) + more]


def eels(query: Callable[..., Any], since: datetime) -> list[str]:
    """Return new commits per EELS fork branch and new fixtures releases."""
    owner, name = EELS
    when = since.isoformat(timespec="seconds")
    data = query(
        "graphql",
        "-f",
        f'query=query {{ repository(owner: "{owner}", name: "{name}") {{ '
        'refs(refPrefix: "refs/heads/forks/", first: 50) { nodes { name '
        f'target {{ ... on Commit {{ history(since: "{when}") '
        "{ totalCount } } } } } } }",
    )
    refs = data["data"]["repository"]["refs"]["nodes"]
    active = sorted(
        (r for r in refs if r["target"]["history"]["totalCount"]),
        key=lambda r: -r["target"]["history"]["totalCount"],
    )
    branches = ", ".join(
        f"forks/{r['name']} +{r['target']['history']['totalCount']}"
        for r in active
    )
    releases = query(f"repos/{owner}/{name}/releases?per_page=10")
    fresh = [r["tag_name"] for r in releases if r["published_at"] >= when]
    lines = [f"commits: {branches or 'none'}"]
    if fresh:
        lines.append(f"releases: {', '.join(fresh)}")
    elif releases:
        last = releases[0]
        lines.append(
            f"releases: none new, last {last['tag_name']} "
            f"({last['published_at'][:10]})"
        )
    return lines


def hive(fetch: Fetch, api: str, client: str | None) -> list[str]:
    """Return each group's failures and regressions, for `client` or all."""
    groups = fetch(f"{api}/api/v1/groups")["groups"]

    def group_line(group: str) -> str | None:
        try:
            summary = fetch(f"{api}/api/v1/groups/{group}/summary")
        except OSError:
            return f"{group}: still loading"
        if summary is None:
            return None
        try:
            diff = fetch(f"{api}/api/v1/groups/{group}/diff?counts=true")
        except OSError:
            diff = None
        return hive_line(group, summary, diff, client)

    with ThreadPoolExecutor() as pool:
        lines = [line for line in pool.map(group_line, groups) if line]
    return lines


def hive_line(
    group: str,
    summary: dict[str, Any],
    diff: dict[str, Any] | None,
    client: str | None,
) -> str | None:
    """Return one group's line: its failures and how many are new."""
    when = summary["last_run"][5:16].replace("T", " ")
    clients = summary["clients"]
    regressions = (diff or {}).get("regressions", {})
    new = {name: entry["total_fails"] for name, entry in regressions.items()}
    if client:
        if client not in clients:
            return None
        fails = clients[client]["fail"]
        added = new.get(client, 0) if diff else "? (still loading)"
        return f"{group} ({when}): {fails} failing, {added} new"
    fails = sum(c["fail"] for c in clients.values())
    if not diff:
        return f"{group} ({when}): {fails} failing, ? new (still loading)"
    worst = sorted(new.items(), key=lambda kv: -kv[1])[:3]
    detail = ", ".join(f"{name} {count}" for name, count in worst)
    line = f"{group} ({when}): {fails} failing, {sum(new.values())} new"
    return f"{line} ({detail})" if detail else line


def gather(me: dict[str, Any], since: datetime) -> dict[str, list[str]]:
    """Fetch every source at once and return its lines, or why it failed."""
    api = os.environ.get("HIVE_API_URL", "http://localhost:8080").rstrip("/")
    sources: dict[str, Callable[[], list[str]]] = {
        "Forks": lambda: forks(fetch_json),
        "Decisions": lambda: decisions(fetch_json, since),
        "EIP stages": lambda: stages(fetch_json, since),
        "EELS": lambda: eels(gh, since),
        "Hive": lambda: hive(fetch_json, api, me["client"]),
    }
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {name: pool.submit(run) for name, run in sources.items()}
    out = {}
    for name, future in futures.items():
        try:
            out[name] = future.result()
        except urllib.error.URLError as error:
            hint = ", start hapi" if name == "Hive" else ""
            out[name] = [f"unavailable ({error.reason}{hint})"]
        except Exception as error:  # noqa: BLE001, one source must not stop the rest
            out[name] = [f"unavailable ({type(error).__name__}: {error})"]
    return out


def render(
    me: dict[str, Any], since: datetime, out: dict[str, list[str]]
) -> str:
    """Return the digest as short lines, one block per source."""
    who = f"{me['login']} ({me['team']}"
    who += f", {me['client']})" if me["client"] else ")"
    lines = [f"Latest since {since:%Y-%m-%d %H:%M} UTC, for {who}"]
    for name, items in out.items():
        for i, item in enumerate(items):
            label = name if i == 0 else ""
            lines.append(f"  {label:<11} {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the digest and remember this run."""
    parser = argparse.ArgumentParser(
        prog="latest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--since", help="7d, or a date such as 2026-10-01")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    if args.since:
        since = parse_since(args.since, now)
    else:
        since = read_last_run(STATE_FILE, now)
    me = read_me(ME_FILE, TEAMS_FILE)
    out = gather(me, since)
    if args.json:
        print(json.dumps({"since": since.isoformat(), "me": me, **out}))
    else:
        print(render(me, since, out))
    if not args.since:
        save_last_run(STATE_FILE, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
