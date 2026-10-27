# Differential fuzzing

Runs the same inputs through an EELS spec function and the client function
`./xref.sh` pairs it with, and reports every difference. A pair that agrees
on thousands of edge and random inputs is strong evidence the client does
what the spec says. A difference is a bug on one side or a wrong pairing.

    uv run fuzz/run.py --eels <execution-specs clone> --geth <geth clone>

It makes detached worktrees under `work/fuzz/` at the commits the xref map
is pinned to, so your own checkouts are untouched. `--cases` sets how many
random cases each function gets on top of the edge values. The report goes
to `fuzz/report.md`, and the exit code is 1 when anything differs.

## Pairs

| EELS (amsterdam) | geth |
|---|---|
| `calculate_memory_gas_cost` | `memoryGasCost` on fresh memory |
| `calculate_gas_extend_memory` | `memoryGasCost` on grown memory |
| `max_message_call_gas` | `callGas`, the 63/64 rule |
| `calculate_blob_gas_price` | `BlobConfig.blobBaseFee` |
| `calculate_excess_blob_gas` | `calcExcessBlobGas` |
| `calculate_intrinsic_cost` | `IntrinsicGas` and `FloorDataGas` |

geth refuses memory sizes above `0x1FFFFFFFE0`, which EELS prices instead.
That counts as agreement when the EELS cost is beyond any block's gas. The
blob schedule is set to the EELS Amsterdam constants (target 14, max 21),
so this checks the formulas, not geth's chain config.

## Checked that it catches bugs

Two bugs planted in a throwaway geth worktree:

- charging value cost on self-transfers: 52 differences
- `>` changed to `>=` in the EIP-7918 reserve price check: missed by random
  inputs, so the cases now aim at the exact boundary, which catches it

## Adding a pair

Add a case generator in `run.py`, the EELS call in `eels_side.py` and the
client call in a test file under `geth/`. The tests run inside the client's
own packages, so they can call unexported functions.
