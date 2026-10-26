# protocol-mesh

One command to set up a workspace where agents work on EIPs across the spec,
the tests and every execution client.

## Setup

On macOS or Linux:

    git clone https://github.com/spencer-tb/protocol-mesh
    cd protocol-mesh
    ./setup.sh

That one command:

- clones every repo in `repos.txt` into `repos/`, inside this checkout and
  ignored by git, so agents never touch your own clones
- installs any missing toolchain (uv, cmake, Rust, Go, Node, .NET, JDK 21,
  jq, ripgrep, Codex) into `~/.local`, with no sudo, leaving tools you
  already have alone
- puts them on PATH for new bash, zsh and fish shells
- builds the client binaries into `bin/`
- prints what is installed, missing or too old

Run it again any time. It fetches the repos, fast-forwards clean checkouts
and rebuilds only the clients whose code changed. The first run builds
every client, so it takes a while.

| Command | What it does |
|---|---|
| `./setup.sh` | everything above |
| `./setup.sh geth besu` | the same, building only those targets |
| `./setup.sh --reuse ~/code` | new clones borrow objects from your clones in `~/code`, so they download less but stay separate |
| `./setup.sh --check` | only print the status |
| `./setup.sh --update` | show what changed in this repo upstream, apply only after you say yes |

Once per machine, install what `--check` lists under system packages
(a C compiler, clang, snappy and rocksdb, through apt or brew), start
docker for hive, and log in to `gh` and your agents.

## Updates

This checkout never updates itself, because a bad commit would then run on
every machine. `./setup.sh --update` lists the incoming commits and shows
the full diff of everything that runs or instructs agents (`setup.sh`,
`bootstrap.py`, `repos.txt`, `AGENTS.md`, `.agents/`), then asks before
fast-forwarding. Read it before saying yes.

`repos.txt` only accepts https GitHub repos from a fixed list of owners in
`bootstrap.py`.

## Agents

`AGENTS.md` is the instructions every agent reads, and `.agents/skills/`
holds the skills. `CLAUDE.md` and `.claude/skills` link to them for Claude
Code. `sources.md` lists where each kind of context lives.

## Build targets

| Target | Checkout | Output |
|---|---|---|
| `eels` | `repos/execution-specs` | `uv sync` |
| `hive` | `repos/hive` | `repos/hive/hive` |
| `clive` | `repos/clive` | `~/.local/bin/clive`, `~/.local/bin/hapi` |
| `geth` | `repos/clients/go-ethereum` | `bin/geth/evm` |
| `erigon` | `repos/clients/erigon` | `bin/erigon/evm` |
| `besu` | `repos/clients/besu` | `bin/besu/evmtool` |
| `nethermind` | `repos/clients/nethermind` | `bin/nethermind/nethtest` |
| `evmone` | `repos/clients/evmone` | `bin/evmone/evmone*` |
| `ethrex` | `repos/clients/ethrex` | `bin/ethrex/ethrex-evm` |
| `nimbus` | `repos/clients/nimbus-eth1` | `bin/nimbus/t8n` |
| `ethereumjs` | `repos/clients/ethereumjs-monorepo` | `bin/ethereumjs/ethereumjs-t8ntool.sh` |

reth has no t8n in EELS, so it runs through hive, which builds every client
in docker.

## Changing the setup

- A repo: add a line to `repos.txt`, from a trusted owner.
- A skill: add `.agents/skills/<name>/SKILL.md`.
- A build: add a `build_<target>` function and a `TARGETS` entry in
  `bootstrap.py`.

`setup.sh` only installs uv and hands over to `bootstrap.py`, which uv runs
with its own Python. The tests need no network:

    uv run pytest
    uv run ruff check
    uv run mypy
