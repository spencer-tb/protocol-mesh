# protocol-mesh

One place for the tooling our agents and teams use to build EIPs together,
for Hegota, I* and older forks.

## How it works

This is a draft that we are still planning.

1. Agents and people work on the same EIP across the spec, the tests and
   every client.
2. An agent that finds a problem opens an issue here, tagged with the EIP and
   the team that owns the fix.
3. That team, or its agent, picks the issue up and fixes it in its own repo,
   linking the PR back.
4. The issue closes when the fix merges.

## Setup

On macOS or Linux:

    git clone https://github.com/spencer-tb/protocol-mesh
    ./protocol-mesh/setup.sh

That one command:

- clones every repo in `repos.txt` into `~/ethereum`
- installs any missing toolchain (uv, cmake, Rust, Go, Node, .NET, JDK 21,
  jq, ripgrep, Codex) into `~/.local`, with no sudo, leaving tools you
  already have alone
- puts them on PATH for new bash, zsh and fish shells
- links the shared commands for Claude Code and Codex
- builds the client binaries into `~/ethereum/bin`
- prints what is installed, missing or too old

Run it again any time. It pulls this repo first, fast-forwards clean
checkouts and rebuilds only the clients whose code changed. The first run
builds every client, so it takes a while.

| Command | What it does |
|---|---|
| `./setup.sh` | everything above |
| `./setup.sh geth besu` | the same, building only those targets |
| `./setup.sh --check` | only print the status |

Once per machine, install what `--check` lists under system packages
(a C compiler, clang, snappy and rocksdb, through apt or brew), start
docker for hive, and log in to `gh`, `claude` and `codex`.

## Build targets

| Target | Checkout | Output |
|---|---|---|
| `eels` | `execution-specs` | `uv sync` |
| `hive` | `hive` | `hive/hive` |
| `clive` | `clive` | `~/.local/bin/clive` |
| `geth` | `clients/go-ethereum` | `bin/geth/evm` |
| `erigon` | `clients/erigon` | `bin/erigon/evm` |
| `besu` | `clients/besu` | `bin/besu/evmtool` |
| `nethermind` | `clients/nethermind` | `bin/nethermind/nethtest` |
| `evmone` | `clients/evmone` | `bin/evmone/evmone*` |
| `ethrex` | `clients/ethrex` | `bin/ethrex/ethrex-evm` |
| `nimbus` | `clients/nimbus-eth1` | `bin/nimbus/t8n` |
| `ethereumjs` | `clients/ethereumjs-monorepo` | `bin/ethereumjs/ethereumjs-t8ntool.sh` |

reth has no t8n in EELS, so it runs through hive, which builds every client
in docker. [AGENTS.md](AGENTS.md) shows which binary goes with `fill` and
which with `consume`.

## Adding things

- A repo: add a line to `repos.txt`. Public repos only.
- A command: add it to `commands/`.
- A build: add a `build_<target>` function to `setup.sh`, and the target to
  `TARGETS` and `target_src`.
