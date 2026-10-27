---
name: xref
description: Find where each execution client (geth, reth with evm2, nethermind, besu, erigon, ethrex, nimbus, and the evmone EVM) implements an EELS spec function, which spec function a client function implements, or which client code a spec change lands on. Use before grepping client repos for cross-client questions, audits and scoping EIP changes.
---

# xref

`./xref.sh` keeps a map from every function of the anchor EELS fork
(amsterdam) to the client functions that implement it, step by step.
Run it from the protocol-mesh directory.

## Start with the map

`rg <EELS function or client function> xref/map/` answers in one call for
every client: the client functions behind each step of the EELS function,
the steps a client lacks, and code that belongs to another EELS function.
`xref/map/README.md` explains the columns. Never read a map file whole.
Open the listed lines before claiming anything about them.

## Commands

Free and local:

- `./xref.sh find <spec fn>`: ranks each client's functions by keyword
  match against the spec function. Needs `./xref.sh sync` and
  `./xref.sh index` once (about 40 seconds).
- `./xref.sh diff <client> <base> [head]`: functions that changed between
  two refs of a client, `--spec <fn>` to keep only code behind one spec
  function.
- `./xref.sh touch <git range> --repo repos/execution-specs`: the spec
  functions a range changed and the client code behind each. Use it to
  scope an EIP branch.
- `./xref.sh tsv`: writes `xref/map/` from `xref/data/tree.json`.

These call Claude and cost money, so ask your user first:

- `./xref.sh build`: maps spec functions to clients, about $0.35 a pair.
  Built pairs are kept, `--only`, `--clients` and `--max-usd` narrow it.
- `./xref.sh refresh`: syncs the repos, moves rows whose code moved,
  rechecks rows whose code changed and rebuilds only stale pairs. A few
  dollars a night, `--max-usd` caps it.

## How far to trust it

- Measured on besu against a separately labelled answer key of 40
  functions: the top match is right 95% of the time, 87% of the right
  functions are found and 89% of the rows are right. See
  `xref/bench/results/report.md`.
- Treat every row as the place to start reading, never as proof.
- A `missing` row is weak evidence. The client may keep that code in a
  folder `xref.toml` does not index. Grep the client before concluding
  the logic is absent. evmone is only an EVM, so block-level steps are
  missing there by design.
- Line numbers are from the commits in `xref/data/tree.json` and drift as
  clients move until the next `refresh`.
