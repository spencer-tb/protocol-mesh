---
name: xref
description: Find where each execution client (geth, reth and revm, nethermind, besu, erigon, ethrex, nimbus) implements an EELS spec function, which spec function a client function implements, or which client code a spec change lands on. Use before grepping client repos for cross-client questions, audits and scoping EIP changes.
---

# xref

`./xref.sh` keeps a map from every function of the anchor EELS fork
(amsterdam) to the client functions that implement it, with
commit-pinned links. Run it from the protocol-mesh directory.

## Start with the map

`rg <EELS function or client function> xref/map/` answers in one call for
every client: the client functions behind each step of the EELS function,
the steps a client lacks, and code that belongs to another EELS function.
`xref/map/README.md` explains the columns. Never read a map file whole.
Open the listed lines before claiming anything about them.

## Commands

- `./xref.sh show <spec fn>`: client locations from the shared map.
  Accepts the full id (`vm.gas.charge_state_gas`), the bare name or a
  substring. `--clients geth,reth` narrows it, `--low` adds weak guesses.
- `./xref.sh where <client fn>`: the spec functions a client function or
  path is mapped to, for example `where IntrinsicGas`.
- `./xref.sh find <spec fn>`: free and local. Ranks each client's
  functions by keyword match against the spec function and returns the
  top candidates, from `repos/clients/` when cloned, else from the cached
  upstream copy. Needs `./xref.sh sync` and `./xref.sh index` once
  (about 40 seconds).
- `./xref.sh diff <client> <base> [head]`: functions that changed between
  two refs of a client, `--spec <fn>` to keep only code behind one spec
  function.
- `./xref.sh touch <git range> --repo repos/execution-specs`: the spec
  functions a range changed and the client code behind each. Use it to
  scope an EIP branch.
- `./xref.sh link`: judges matches with Claude, about $0.09 a call. Ask
  your user before running it, and use `--dry-run` to count calls first.

## How far to trust it

- The judged map covers geth fully and reth partly so far. Other clients
  have only `find`.
- Accuracy is not measured yet. Treat every match as the place to start
  reading, never as proof, and open the link before claiming a client
  does or does not implement something.
- `find` is keyword ranking with no judgment. It often surfaces related
  functions (a refund next to a charge) before the exact one.
- Links are pinned to the commits in `xref/data/map.json`, so line
  numbers drift as clients move.
- "no match" is weak evidence. Grep the client before concluding the
  logic is missing.
