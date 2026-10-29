# el-xref

Find where each execution client implements an EELS spec function. Run
commands from the protocol-mesh directory.

```sh
./xref.sh find calculate_memory_gas_cost
./xref.sh find calculate_intrinsic_cost --clients geth,reth -n 2
./xref.sh show calculate_memory_gas_cost --clients reth
./xref.sh where GasParams.memory_cost
```

`find`, `show`, `where` and `touch` read `data/tree.json`, including the
latest build results before a TSV export. Older checkouts without a tree
can still read `data/map.json`.

`find` uses saved matches first. It searches current client source only
for clients with failed, unbuilt or incomplete mappings. Search results
are keyword candidates and need to be checked. A complete mapping needs
no client parsing or source index, and no lookup calls a model.

Use `find --search` to search all selected clients explicitly. `--at
REPO=PATH[@REF]` and `--upstream` also use source search. This preserves
searches of working trees and branches. Source searches require `sync`
and `index` once, and cache their parsed functions.

Saved locations carry the recorded commits. Paths point to an existing
local checkout, cached source, or a pinned upstream URL. Local line
numbers may drift; open the code or use `find --search` for current
locations. A missing step means the map has not located its implementation.

## Commands

| command | does |
|---|---|
| `find <spec fn>` | saved matches, with source search for gaps; `--clients` and `-n` narrow output |
| `find <spec fn> --search` | keyword candidates from current source, including `--at REPO=PATH[@REF]` |
| `show <spec fn>` | full saved mapping, steps, evidence notes and gaps; `--low` includes low confidence |
| `where <client fn>` | reverse lookup by client function name or path |
| `touch <range> --repo <execution-specs>` | saved client mappings for changed spec functions; `--fork` selects the changed fork |
| `diff <client> <base> [head]` | changed client functions; `--spec` narrows the comparison |
| `tsv` | export the latest tree to `map/` for direct grep |
| `sync` / `index` | fetch sparse clones / extract function symbols |
| `build --max-usd <cap>` | build missing or stale step mappings with Claude |
| `refresh --max-usd <cap>` | sync, relocate rows and rebuild stale mappings |

The free grep path is also available:

```sh
rg calculate_memory_gas_cost xref/map/
```

TSVs are snapshots: run `./xref.sh tsv` after a build to include its new
results. Failed mappings have explicit `failed` rows instead of silently
disappearing. See `map/README.md` for the columns.

## Building the map

The anchor is the amsterdam EELS fork. `build` splits each spec function
into steps, searches client code, expands candidates, and uses two judges
to map the steps. It records evidence, confidence, votes and commits in
`data/tree.json`. Existing completed pairs are reused.

Supported clients: geth, reth with evm2, nethermind, besu, erigon, ethrex,
nimbus and evmone. evmone is an EVM; some block-level steps do not apply.

`build`, `refresh` and `tag` call Claude and cost money; obtain approval
before running them. `--max-usd` stops new pairs once the cap is reached;
already running pairs can overshoot it. `link`, `render` and `eval` are
legacy commands for `data/map.json`; the lookup commands prefer the tree.

## Checks

From the repo root, with the rest of the repo's checks:

```sh
just test-py xref/tests
just static
```
