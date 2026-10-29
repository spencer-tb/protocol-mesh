#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""
Differential fuzzing: run the same inputs through EELS spec functions and
the geth functions xref pairs them with, and report every difference.

    uv run fuzz/run.py --eels <execution-specs clone> --geth <geth clone>

Both clones get a detached worktree under work/fuzz at the commits the
xref map is pinned to (or --eels-ref / --geth-ref), so the run never
touches your own checkouts. The pairs:

    calculate_memory_gas_cost    memoryGasCost on fresh memory
    calculate_gas_extend_memory  memoryGasCost on grown memory
    max_message_call_gas         callGas, the 63/64 rule
    calculate_blob_gas_price     BlobConfig.blobBaseFee
    calculate_excess_blob_gas    calcExcessBlobGas
    calculate_intrinsic_cost     IntrinsicGas and FloorDataGas
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MESH = HERE.parent
MAP = MESH / "xref" / "data" / "map.json"
WORK = MESH / "work" / "fuzz"

# geth package directory, the test file for it, and its functions.
PACKAGES = {
    "core/vm": "vm_test.go",
    "consensus/misc/eip4844": "eip4844_test.go",
    "core": "core_test.go",
}

# geth refuses memory sizes above this. Any EELS cost past the same size
# is far beyond a block's gas, so a refusal there counts as agreement.
GETH_MEMORY_LIMIT = 0x1FFFFFFFE0
UNAFFORDABLE = 1 << 54
PER_BLOB = 1 << 17
MAX_BLOB_GAS = 21 * PER_BLOB


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return its output."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def worktree(repo: Path, ref: str, dest: Path) -> Path:
    """Return a detached worktree of `repo` at `ref`, made once."""
    if (dest / ".git").exists():
        if git(dest, "rev-parse", "HEAD").startswith(ref[:12]):
            return dest
        git(dest, "checkout", "-q", "--detach", ref)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(dest), ref)
    return dest


# Inputs: edge values first, then random ones spread over many magnitudes.

EDGES = sorted(
    {0, 1, 2, 31, 32, 33, 63, 64, 65, 511, 512, 513, 1023, 1024}
    | {(1 << b) + d for b in (16, 31, 32, 63) for d in (-1, 0, 1)}
    | {(1 << 64) - 1}
)


def spread(rng: random.Random, top_bits: int) -> int:
    """Return a random value whose size is spread over 0 to 2**top_bits."""
    return rng.getrandbits(rng.randint(0, top_bits))


def fake_exponential(factor: int, numerator: int, denominator: int) -> int:
    """Return the EIP-4844 integer approximation of factor * e ** (n / d)."""
    i, output, accum = 1, 0, factor * denominator
    while accum > 0:
        output += accum
        accum = (accum * numerator) // (denominator * i)
        i += 1
    return output // denominator


def cases(rng: random.Random, count: int) -> list[dict[str, Any]]:
    """Return `count` random cases per function, plus the edge values."""
    out: list[dict[str, Any]] = []

    def add(fn: str, **args: Any) -> None:
        out.append({"id": len(out), "fn": fn, "args": args})

    memory_edges = [
        GETH_MEMORY_LIMIT + d for d in (-33, -32, -1, 0, 1, 32)
    ] + [e for e in EDGES if e < 1 << 64]
    for size in memory_edges + [spread(rng, 40) for _ in range(count)]:
        add("memory", size=str(size))
    for _ in range(count):
        current = 32 * rng.randint(0, 1 << 15)
        size = rng.choice([current, current + 1, spread(rng, 36)])
        add("memory_extend", current=str(current), size=str(size))
    for gas in EDGES + [spread(rng, 64) for _ in range(count)]:
        add("callgas", gas=str(gas))
    blob_edges = [e for e in EDGES if e < 1 << 31] + [(1 << 31)]
    for excess in blob_edges + [spread(rng, 31) for _ in range(count)]:
        add("blobfee", excess=str(excess))
    for _ in range(count):
        used = rng.choice(
            [PER_BLOB * rng.randint(0, 21), rng.randint(0, MAX_BLOB_GAS)]
        )
        add(
            "excess",
            excess=str(spread(rng, 40)),
            used=str(used),
            base_fee=str(spread(rng, 64)),
        )
    # EIP-7918 compares the reserve price with the blob price, and random
    # base fees almost never land on equality, so aim at it directly:
    # BLOB_BASE_COST * base_fee == PER_BLOB * price when base_fee is 16x.
    for _ in range(count // 4):
        excess = spread(rng, 31)
        used = max(0, 14 * PER_BLOB - excess) + rng.randint(0, MAX_BLOB_GAS)
        price = fake_exponential(1, excess, 11684671)
        for base_fee in (16 * price - 1, 16 * price, 16 * price + 1):
            add(
                "excess",
                excess=str(excess),
                used=str(min(used, MAX_BLOB_GAS)),
                base_fee=str(base_fee),
            )
    for _ in range(count):
        length = spread(rng, 12)
        zeros = rng.random()
        data = bytes(
            0 if rng.random() < zeros else rng.randint(1, 255)
            for _ in range(length)
        )
        to = rng.choice(["create", "self", "other"])
        add(
            "intrinsic",
            data=data.hex(),
            to=to,
            value=str(rng.choice([0, 1, (1 << 256) - 1])),
            access=[rng.randint(0, 4) for _ in range(rng.randint(0, 4))],
            auths=(
                None
                if to == "create" or rng.random() < 0.5
                else rng.randint(0, 4)
            ),
        )
    return out


def run_eels(eels: Path, source: Path, target: Path) -> None:
    """Compute the EELS results inside the spec checkout's environment."""
    subprocess.run(["uv", "sync", "-q"], cwd=eels, check=True)
    subprocess.run(
        [
            "uv", "run", "--project", str(eels), "python",
            str(HERE / "eels_side.py"), str(source), str(target),
        ],
        cwd=eels,
        check=True,
    )  # fmt: skip


def run_geth(geth: Path, source: Path, out: Path) -> None:
    """Copy the tests into the geth worktree and run them."""
    for package, test in PACKAGES.items():
        name = package.rsplit("/", 1)[-1]
        helpers = (HERE / "geth" / "helpers_test.go").read_text()
        (geth / package / "xfuzz_helpers_test.go").write_text(
            helpers.replace("package PKG", f"package {name}", 1)
        )
        shutil.copy(HERE / "geth" / test, geth / package / "xfuzz_test.go")
    subprocess.run(
        ["go", "test", "-count=1", "-run", "^TestXFuzz$"]
        + [f"./{p}" for p in PACKAGES],
        cwd=geth,
        check=True,
        env={
            **os.environ,
            "XFUZZ_IN": str(source),
            "XFUZZ_OUT": str(out),
        },
    )


def agrees(fn: str, eels: dict[str, Any], geth: dict[str, Any]) -> bool:
    """Return whether the two sides gave the same outcome."""
    if fn in ("memory", "memory_extend") and "error" in geth:
        return "ok" in eels and int(eels["ok"]) >= UNAFFORDABLE
    if "error" in eels or "error" in geth:
        return "error" in eels and "error" in geth
    left, right = eels["ok"], geth["ok"]
    if isinstance(left, list):
        return [int(v) for v in left] == [int(v) for v in right]
    return int(left) == int(right)


def report(
    all_cases: list[dict[str, Any]],
    eels: dict[int, dict[str, Any]],
    geth: dict[int, dict[str, Any]],
    refs: dict[str, str],
) -> tuple[str, int]:
    """Return the report and the number of differences."""
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    differences: dict[str, list[str]] = defaultdict(list)
    for case in all_cases:
        fn, cid = case["fn"], case["id"]
        left, right = eels.get(cid), geth.get(cid)
        tally[fn][0] += 1
        if left is None or right is None or not agrees(fn, left, right):
            tally[fn][1] += 1
            differences[fn].append(
                f"`{json.dumps(case['args'])[:300]}`: EELS "
                f"{json.dumps(left)[:200]}, geth {json.dumps(right)[:200]}"
            )
    lines = [
        "# Differential fuzz: EELS vs geth",
        "",
        f"EELS {refs['eels'][:10]}, geth {refs['geth'][:10]}.",
        "",
        "| Function | Cases | Differences |",
        "|---|---|---|",
    ]
    for fn, (total, bad) in tally.items():
        lines.append(f"| {fn} | {total} | {bad} |")
    for fn, items in differences.items():
        lines += ["", f"## {fn}", ""]
        lines += [f"- {item}" for item in items[:10]]
        if len(items) > 10:
            lines.append(f"- and {len(items) - 10} more")
    return "\n".join(lines) + "\n", sum(b for _, b in tally.values())


def main() -> int:
    """Run every case through both sides and report the differences."""
    pinned = json.loads(MAP.read_text())["repos"]
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--eels", type=Path, default=MESH / "repos/execution-specs"
    )
    parser.add_argument(
        "--geth", type=Path, default=MESH / "repos/clients/go-ethereum"
    )
    parser.add_argument("--eels-ref", default=pinned["execution-specs"]["sha"])
    parser.add_argument("--geth-ref", default=pinned["go-ethereum"]["sha"])
    parser.add_argument("--cases", type=int, default=2000, help="per function")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    eels = worktree(args.eels.expanduser(), args.eels_ref, WORK / "eels")
    geth = worktree(args.geth.expanduser(), args.geth_ref, WORK / "geth")
    out = WORK / "out"
    out.mkdir(parents=True, exist_ok=True)
    all_cases = cases(random.Random(args.seed), args.cases)
    source = out / "cases.jsonl"
    source.write_text("".join(json.dumps(c) + "\n" for c in all_cases))
    print(f"{len(all_cases)} cases", file=sys.stderr)

    run_eels(eels, source, out / "eels.jsonl")
    run_geth(geth, source, out)

    def results(path: Path) -> dict[int, dict[str, Any]]:
        return {
            r["id"]: r for r in map(json.loads, path.read_text().splitlines())
        }

    geth_results: dict[int, dict[str, Any]] = {}
    for path in out.glob("geth-*.jsonl"):
        geth_results |= results(path)
    text, differences = report(
        all_cases,
        results(out / "eels.jsonl"),
        geth_results,
        {
            "eels": git(eels, "rev-parse", "HEAD"),
            "geth": git(geth, "rev-parse", "HEAD"),
        },
    )
    (HERE / "report.md").write_text(text)
    print(text)
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
