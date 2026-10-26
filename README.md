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

## Set up a machine

    gh repo clone spencer-tb/protocol-mesh ~/protocol-mesh
    ~/protocol-mesh/mesh

That one command:

- clones every repo in `repos.txt` into `~/ethereum`
- installs uv, Rust, Go, .NET, JDK 21, jq, ripgrep and Codex in `~/.local`,
  with no sudo
- builds the client binaries into `~/ethereum/bin`
- links the shared commands and skills for Claude Code and Codex
- puts the toolchains on PATH for bash, fish and systemd user services
- adds an hourly timer that keeps the repos fetched

Run it again whenever you like. It pulls this repo first and rebuilds only
the clients whose checkout moved. The first run builds every client, so it
takes a while. `mesh doctor` shows what is missing.

Once per machine, outside the script:

- devops installs the system packages `mesh doctor` lists
  (`clang libclang-dev libsnappy-dev librocksdb-dev`) and adds you to the
  docker group
- you log in to `gh`, `claude` and `codex`

## Commands

| Command | What it does |
|---|---|
| `mesh` or `mesh up` | everything above |
| `mesh sync [dir...]` | clone missing repos, fetch the rest, fast-forward clean checkouts |
| `mesh tools` | install or update the toolchains (Linux) |
| `mesh build <target...>` | rebuild now, for example after switching a client branch |
| `mesh skills` | link the shared commands and skills |
| `mesh heavy <cmd...>` | run a fill, consume or hive run, one heavy job at a time |
| `mesh env [sh\|fish]` | print the PATH setup |
| `mesh doctor` | show what is installed, missing or too old |

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

- A repo: add a line to `repos.txt`, then run `mesh sync`. Public repos only.
- A command or skill: add it to `commands/`, then run `mesh skills`.
- A build: add a `build_<target>` function to `mesh` and the target to
  `TARGETS` and `target_src`.
