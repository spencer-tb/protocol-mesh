---
name: module-tests
description: Run EELS fixtures through each execution client's own test runners, with no node, network or docker. Fill tests with a client's t8n, then run `just blocktest` and `just statetest` on geth, reth, nethermind, besu, erigon, ethrex, nimbus and evmone, and compare with hive CI. Use to check a fixtures release, an EIP's tests or a client build across clients.
---

# Module tests

Module tests run fixtures straight through each client's own blockchain
and state test runners. No node starts, so they take seconds to minutes.
System tests, which run clients as nodes, are the `hive` and `kurtosis`
skills. Paths are relative to the protocol-mesh directory.

## Start from CI

Before running anything, see how the clients do on the same tests in CI.
It costs nothing and says which branch each client was built from.

    clive groups
    clive summary <group>
    clive tests <group> --simulator=consume-rlp

`consume-rlp` imports the blockchain test blocks, so it is the CI suite
closest to `blocktest`. A client that is clean in CI but fails many tests
locally points at the local build or runner, not at the client.

## Build the runners

`./setup.sh <target>` builds each runner into `bin/`. Builds are heavy, so
ask your user first and run one at a time.

| Client | Target | blocktest | statetest |
|---|---|---|---|
| geth | `geth` | `geth/evm` | `geth/evm` |
| reth | `reth`, `evm2` | `reth/ef-test-runner` | `evm2/evm2`, the EVM reth uses |
| nethermind | `nethermind` | `nethermind/nethtest` | `nethermind/nethtest` |
| besu | `besu` | `besu/evmtool` | `besu/evmtool` |
| erigon | `erigon` | `erigon/evm` | `erigon/evm` |
| ethrex | `ethrex` | `ethrex/ef-tests-blockchain` | `ethrex/ef_tests-statev2` |
| nimbus | `nimbus` | `nimbus/eest_blockchain` | `nimbus/evmstate` |
| evmone | `evmone` | | `evmone/evmone` |

What breaks a build:

- A client's master needs a newer toolchain than `RUST_MIN` or
  `JDK_MAJOR` in `bootstrap.py`. Raise the minimum and run setup again,
  which updates the toolchain.
- The network drops mid-build. nimbus's `make update` carries on past
  failed submodule clones, so its build then does nothing. Run it again.
- A target rebuilds only when its checkout has a new commit or local
  changes. `rm bin/.built/<target>` forces a rebuild.

## Get fixtures

From a release, which is about 1 GB to download and 8 GB with both kinds
extracted:

    tag=tests@v21.0.2   # gh release list -R ethereum/execution-specs | rg '^tests@'
    mkdir -p work/fixtures/$tag
    gh release download "$tag" -R ethereum/execution-specs \
      -p fixtures.tar.gz -O work/fixtures/$tag.tar.gz
    tar -xzf work/fixtures/$tag.tar.gz -C work/fixtures/$tag \
      --strip-components=1 fixtures/blockchain_tests fixtures/state_tests
    rm work/fixtures/$tag.tar.gz

Devnet releases are listed by the `devnet` skill.

Or fill your own, from an EELS worktree or `repos/execution-specs`, with
EELS itself or with a client's t8n tool:

    uv run --directory repos/execution-specs fill --fork <Fork> \
      --evm-bin "$PWD/bin/geth/evm" --output "$PWD/work/<topic>/fixtures" \
      tests/<path>

| Client | `fill --evm-bin` |
|---|---|
| geth | `geth/evm` |
| besu | `besu/evmtool` |
| ethrex | `ethrex/ethrex-evm` |
| nimbus | `nimbus/t8n` |
| evmone | `evmone/evmone` |
| ethereumjs | `ethereumjs/ethereumjs-t8ntool.sh` |

Fill output fills the disk, so delete it when you are done.

## Run

    just blocktest <fixtures> [client ...] [--json]
    just statetest <fixtures> [client ...] [--json]

- `<fixtures>` is a release, its `blockchain_tests` or `state_tests`
  directory, or any directory or file below that.
- Start small. `for_<fork>/<fork>` holds a fork's own EIP tests, a few
  hundred files, before the whole release with thousands.
- With no client named it runs your team's client, or every client for a
  team without one. `all` names every client.
- Clients run one after another, each using every core.
- `runners/README.md` says how each runner is driven and where they
  differ.

## Read results

The report is a row per client and the first five failures of each.
`--json` prints the same for agents. Every client's full results are in
`work/<blocktest|statetest>/<client>.json`, a list of `{name, status,
error}`, with the runner's raw output in `<client>.log`.

    jq -r '.[] | select(.status == "fail") | .name' work/blocktest/geth.json

reth's and ethrex's blockchain runners and evmone report per file, so
their names are fixture paths, not test names.

To see which failures one client has alone, compare the files:

    for c in geth nethermind besu erigon; do
      jq -r --arg c "$c" '.[] | select(.status == "fail") | "\(.name) \($c)"' \
        "work/blocktest/$c.json"
    done | sort

To rerun one failure, pass its fixture file:
`just blocktest <file> <client>`. Each runner's own flags for traces are
in its client's repo.

## Report a failure

A client failure goes to the team that owns the client, with the fixtures
release or fill commit, the test name, the client commit
(`git -C repos/clients/<client> rev-parse HEAD`), the command and the
error. See Coordinate in `AGENTS.md`.

## Not yet

- ethereumjs runners, and evmone's blockchain tests.
- Engine tests (`blockchain_tests_engine`), which run through hive.
- A `system-tests` skill that runs clients as nodes through the panda CLI.
