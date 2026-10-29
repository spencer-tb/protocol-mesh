# protocol-mesh

An agent harness for Ethereum's execution layer: a shared workspace of
tools, skills and reusable context for client developers, researchers and
their agents. It brings the spec (EELS), execution clients and EVMs, hive,
the ethPandaOps tooling and the EIPs into one place.

The aim is faster EIP development, for Hegota, I* and older forks:

- Find holes in an EIP, its spec or a client sooner, by checking against
  every EL client in minutes rather than days.
- Stop duplicating work. Today several people redo the same EIP work in
  separate places. Here it is pooled and built on.
- Build tooling that everyone can use to do things fast.

It runs on everyone's own machine and on GitHub, and joining is opt in. It is
execution layer only for now. If it works, a consensus layer version follows.

## Design priorities

Make both the tools and the way agents use them fast. Measure progress by
the time, tool calls and tokens needed to reach a correct, reproducible
result.

- Fast lookups: use local indexes, shared maps and cached results, with
  source searches for gaps. Refresh results when their inputs change.
- Focused output: return the relevant code, evidence and next action,
  with details available when needed.
- Direct workflows: skills tell agents which tools to use and in what
  order. Reuse setup, builds and fixtures across related checks.
- Shared knowledge: keep mappings and recipes in the workspace so people
  and different agents can inspect and reuse them. Record source commits
  and distinguish candidates from verified findings.
- Repeatable validation: make findings easy to reproduce and compare
  across clients, from spec changes through testing and release.

## Where it is going

First, validation that is easy to run and to repeat:

- A recipe for every kind of check, "to validate X, run Y, then Z", written
  into `AGENTS.md` and the skills.
- Fast, throwaway runs that reproduce a finding with low token use.
- Findings ranked and verified before anyone acts on them, with templated
  output so agent work stays easy to review.

Spec and test releases should be made through the same workflow: select
the source revisions and fork targets, generate fixtures, validate them
across clients, and package the artifacts with release notes and a record
of the source commits, tool versions and test results. Release tooling is
planned in `TODO.md`.

Then coordination: agents post verified findings as issues for the team
that owns the fix, and that team or its agent fixes it in its own repo.

Today it holds the setup, context skills, the spec-to-client map, a small
differential fuzzing prototype and a review profile for agents.

## Setup

On macOS or Linux:

    git clone https://github.com/spencer-tb/protocol-mesh
    cd protocol-mesh
    ./setup.sh

That one command:

- clones your team's repos from `repos.txt` into `repos/`, inside this
  checkout and ignored by git, so agents never touch your own clones
- asks once which team you are on (`teams.toml`) and saves it in
  `.mesh/me.toml`, which git ignores, so you get your team's repos and builds
- installs only the toolchains those builds need (uv, jq, and Go, Rust, Node,
  .NET, JDK 21 or cmake as needed) into `~/.local`, with no sudo, leaving
  tools you already have alone
- puts them on PATH for new bash, zsh and fish shells
- builds the client binaries into `bin/`
- prints what is installed, missing or too old

Run it again any time. It fetches the repos, fast-forwards clean checkouts
and rebuilds only the clients whose code changed. The first run builds
every client, so it takes a while.

| Command | What it does |
|---|---|
| `./setup.sh` | everything above |
| `./setup.sh --team geth` | set or change your team |
| `./setup.sh geth besu` | the same, building only those targets |
| `./setup.sh --profile review` | only the repos a reviewer reads (`profiles/review.txt`), no toolchains or builds |
| `./setup.sh --reuse ~/code` | new clones borrow objects from your clones in `~/code`, so they download less but stay separate |
| `./setup.sh --check` | only print the status |
| `./setup.sh --update` | show what changed in this repo upstream, apply only after you say yes |

Once per machine, install what `--check` lists under system packages
(a C compiler, clang, snappy and rocksdb, through apt or brew), start
docker for hive, log in to `gh`, and bring your own agent (Claude Code,
Codex or another).

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

## Cross-client map

`./xref.sh` maps EELS protocol functions to the code that implements them
across eight execution clients and EVMs. `./xref.sh find
calculate_memory_gas_cost` reads the latest saved map and searches current
client source for gaps. `show` gives the full mapping, and `where` finds
the spec functions behind client code. Lookups are local and make no
model calls. See `xref/README.md`.

## Differential fuzzing

`uv run fuzz/run.py` runs the same inputs through EELS functions and the
geth functions xref pairs them with, and reports every difference. See
`fuzz/README.md`.

## Hive results

`clive/` holds hapi, which caches the CI hive results from
https://hive.ethpandaops.io, and clive, the CLI that queries it:
summaries, failing tests, logs, reproduce commands and the diff between
runs. See `clive/README.md`.

## Build targets

| Target | Checkout | Output |
|---|---|---|
| `eels` | `repos/execution-specs` | `uv sync` |
| `hive` | `repos/hive` | `repos/hive/hive` |
| `clive` | `clive/` | `~/.local/bin/clive`, `~/.local/bin/hapi` |
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
