# protocol-mesh

Shared setup for agents working on EIPs across the spec, the tests and the
execution clients. `setup.sh --check` in this repo shows what this machine
has.

## Where things are

- Repos live in `~/ethereum/<dir>`, with clients under `~/ethereum/clients/`.
  The list is `repos.txt` in this repo.
- `setup.sh` fetches every repo and fast-forwards only clean checkouts, so do
  your own work in a worktree, for example
  `git -C ~/ethereum/clients/go-ethereum worktree add ~/ethereum/wt/geth-<topic>`.
- Client binaries are in `~/ethereum/bin/<client>/`. After switching a
  client's branch, rebuild it with `setup.sh <client>`.
- Shared commands: `/review-eip`, `/review-tests`. The EELS skills load when
  you work inside `~/ethereum/execution-specs`.

## Run tests against a client

From `~/ethereum/execution-specs`:

    uv run fill --fork <Fork> --evm-bin ~/ethereum/bin/geth/evm tests/<path>
    uv run consume direct --bin ~/ethereum/bin/nethermind/nethtest --input <fixtures>

| Client | `fill --evm-bin` | `consume direct --bin` |
|---|---|---|
| geth | `geth/evm` | `geth/evm` |
| besu | `besu/evmtool` | `besu/evmtool` |
| evmone | `evmone/evmone` | `evmone/evmone-statetest`, `evmone/evmone-blockchaintest` |
| ethrex | `ethrex/ethrex-evm` | |
| nimbus | `nimbus/t8n` | |
| ethereumjs | `ethereumjs/ethereumjs-t8ntool.sh` | |
| erigon | | `erigon/evm` |
| nethermind | | `nethermind/nethtest` |

reth and every client's engine API run through hive:
`cd ~/ethereum/hive && ./hive --sim <sim> --client <client>`. Read results
with `clive`.

## Rules

- Run one fill, consume, hive run or client build at a time per machine.
- Delete fill output directories when you are done, they fill the disk.
- Only public repos go in `repos.txt`. Never copy private findings here.
