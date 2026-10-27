# el-xref

Maps every function of an EELS fork to the code that implements it in
each execution client, with commit-pinned links.

```console
$ uv run el-xref show charge_state_gas --clients geth
vm.gas.charge_state_gas
  spec  https://github.com/ethereum/execution-specs/blob/dc1bb6b.../vm/gas.py#L439-L456
  geth       Contract.chargeState  [equivalent, high]
             https://github.com/ethereum/go-ethereum/blob/d01e869.../core/vm/contract.go#L144-L153
             Contract.chargeState charges state gas through the budget and emits a gas-change trace event ...
```

## Why not a generic code graph

Tools like graphify build one graph per repo. Nothing in those graphs
says that geth's `IntrinsicGas` and EELS's `calculate_intrinsic_cost`
do the same job, and that link is the useful part for spec work. Here
the spec is the anchor: each spec function gets the matching function
in every client.

## Pipeline

1. `sync`: shallow, sparse, blobless clones of the spec and client
   repos listed in `xref.toml` (about 85 MB, 25 s for everything).
2. `index`: function-level symbols. The spec goes through `ast`,
   clients through tree-sitter (Go, Rust, C#, Java, Nim). About 10 s.
3. `link`: for each spec function and client, BM25 over identifier
   subwords, comments and EIP numbers picks 15 candidates. Claude reads
   the spec function and the candidates and says which ones implement
   it (`equivalent` or `partial`, with a confidence and a note). If none
   fits, Claude names identifiers to search for and gets a second round
   with those. Results go to `data/map.json`.
4. `render`: `data/MAP.md`, one table per spec module.

`link` is incremental. An answer is reused while the spec function and
every matched client function keep the same content hash, so a refresh
only pays for what changed.

## Commands

| command | does |
|---|---|
| `show <spec fn>` | client locations for a spec function (id, bare name or substring) |
| `where <client fn>` | spec functions mapped to a client function or path |
| `touch <range> --repo <execution-specs>` | client code behind every spec function changed in a git range, `--fork bogota` for EIP branches |
| `link --clients geth --modules vm.gas` | judge a subset, `--dry-run` counts the calls first |
| `eval` | score the map against `eval/gold.toml` |

`just nightly` runs sync, index, link (capped at 300 judge calls) and
render.

## Clients

| client | repos |
|---|---|
| geth | go-ethereum |
| reth | revm, alloy-evm, reth |
| nethermind | nethermind |
| besu | besu |
| erigon | erigon |
| ethrex | ethrex |
| nimbus | nimbus-eth1 |

## Judge calls

Calls go through `claude -p` with no tools and `--strict-mcp-config`.
Without that flag every MCP tool definition rides along, about 54k
tokens a call. Locally this uses the Claude Code login. With
`ANTHROPIC_API_KEY` set it runs `--bare` on the key, which is what the
GitHub workflow does.

Default model `claude-opus-5-5` at `medium` effort, about $0.09 a call
at list price.

## Automation

- `.github/workflows/nightly.yml`: daily refresh that commits
  `data/map.json` and `data/MAP.md`. Needs an `ANTHROPIC_API_KEY`
  secret once the repo has a remote.
- `contrib/el-xref.nightly.plist`: the same on a local launchd timer.
  Not installed.
- `.claude/skills/el-xref`: tells Claude Code when and how to query the
  map.
