"""Command line entry point."""

import argparse
from pathlib import Path

from . import config as config_module
from . import index
from .evaluate import evaluate
from .link import Settings, link
from .lookup import diff, find
from .render import render_markdown, show, where
from .sync import sync_all
from .touch import touch


def main() -> None:
    """Dispatch `el-xref <command>`."""
    parser = argparse.ArgumentParser(prog="el-xref")
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).parents[2] / "xref.toml"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="fetch the spec and client repos")
    sync.add_argument("repos", nargs="*", help="repo names, default all")

    build = commands.add_parser("index", help="extract symbols")
    build.add_argument("repos", nargs="*", help="repo names, default all")

    linker = commands.add_parser("link", help="judge spec-to-client matches")
    linker.add_argument("--clients", default="", help="comma separated")
    linker.add_argument("--modules", default="", help="spec module prefixes")
    linker.add_argument("--only", default="", help="exact spec ids")
    linker.add_argument("--model", default="claude-opus-5-5")
    linker.add_argument("--effort", default="medium")
    linker.add_argument("--jobs", type=int, default=4)
    linker.add_argument("--retry-none", action="store_true")
    linker.add_argument(
        "--max-calls", type=int, default=0, help="cap judge calls, 0 = none"
    )
    linker.add_argument("--dry-run", action="store_true")

    shower = commands.add_parser("show", help="where clients implement X")
    shower.add_argument("query", help="spec id, suffix or substring")
    shower.add_argument("--clients", default="", help="comma separated")
    shower.add_argument("--low", action="store_true", help="include low")

    finder = commands.add_parser("where", help="spec function of a client fn")
    finder.add_argument("query", help="client qualname or path substring")

    toucher = commands.add_parser(
        "touch", help="client code hit by a spec git range"
    )
    toucher.add_argument("revisions", help="git range, e.g. base...head")
    toucher.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="execution-specs clone"
    )
    toucher.add_argument("--fork", default="", help="default: anchor fork")

    finder2 = commands.add_parser(
        "find", help="each client's code for a spec fn, free and local"
    )
    finder2.add_argument("query", help="spec id, suffix or substring")
    finder2.add_argument("--clients", default="", help="comma separated")
    finder2.add_argument("-n", type=int, default=4, help="candidates each")
    add_checkout_options(finder2)

    differ = commands.add_parser(
        "diff", help="functions changed between two refs of a client"
    )
    differ.add_argument("target", help="repo name, or client with one repo")
    differ.add_argument("base", help="base ref")
    differ.add_argument(
        "head", nargs="?", default="", help="default: worktree"
    )
    differ.add_argument("--spec", default="", help="only code behind this fn")
    differ.add_argument(
        "-n", type=int, default=4, help="candidates for --spec"
    )
    differ.add_argument(
        "--merge-base",
        action="store_true",
        help="diff from the fork point, i.e. what head's branch changed",
    )
    add_checkout_options(differ)

    commands.add_parser("render", help="write data/MAP.md")
    commands.add_parser("eval", help="score the map against eval/gold.toml")

    args = parser.parse_args()
    config = config_module.load(args.config)

    if args.command == "sync":
        sync_all(config, set(args.repos) or None)
    elif args.command == "index":
        index.build(config, set(args.repos) or None)
    elif args.command == "link":
        link(
            config,
            split(args.clients),
            split(args.modules),
            split(args.only),
            Settings(
                args.model,
                args.effort,
                args.jobs,
                args.retry_none,
                args.max_calls,
            ),
            dry_run=args.dry_run,
        )
    elif args.command == "show":
        show(config, args.query, split(args.clients), args.low)
    elif args.command == "where":
        where(config, args.query)
    elif args.command == "touch":
        touch(config, args.repo, args.revisions, args.fork)
    elif args.command == "find":
        find(config, args.query, split(args.clients), checkouts(args), args.n)
    elif args.command == "diff":
        diff(
            config,
            args.target,
            args.base,
            args.head,
            args.spec,
            checkouts(args),
            args.n,
            args.merge_base,
        )
    elif args.command == "render":
        render_markdown(config)
    elif args.command == "eval":
        evaluate(config)


def split(value: str) -> list[str]:
    """Split a comma separated option."""
    return [v.strip() for v in value.split(",") if v.strip()]


def add_checkout_options(parser: argparse.ArgumentParser) -> None:
    """Add `--at` and `--upstream`."""
    parser.add_argument(
        "--at",
        action="append",
        default=[],
        metavar="REPO=PATH[@REF]",
        help="read REPO from PATH, at REF if given (repeatable)",
    )
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="read the cached upstream clones, not local ones",
    )


def checkouts(args: argparse.Namespace) -> dict[str, str]:
    """Collect `--at` overrides into a repo to location map."""
    at = {}
    for item in args.at:
        name, _, where = item.partition("=")
        at[name] = where
    if args.upstream:
        at["upstream"] = "1"
    return at
