# EELS to client map

Where each execution client implements each EELS function. Grep it,
never read a file whole:

    rg calculate_intrinsic_cost xref/map/     every client, one call
    rg IntrinsicGas xref/map/                 which EELS function a client function is

`eels.tsv` lists each EELS function's steps once:

| eels | step | what it does |
|---|---|---|

Each client has its own file, one row per client function:

| eels | steps | role | location | function |
|---|---|---|---|---|

- `steps` are ids from `eels.tsv`.
- `role` is `entry` (the main function), `part` (does some steps),
  `inlined` (the steps sit inside a function doing other work),
  `delegated` (the code belongs to another EELS function, `location` says
  which, look that one up) or `missing` (the client has no code for these
  steps).
- `location` is a path from the protocol-mesh root and a line range.

Built by `./xref.sh build` and written by `./xref.sh tsv`, from
`xref/data/tree.json`, which also holds each match's quoted line, note and
confidence. Line numbers are from the commits in `tree.json`, so open the
file before trusting them.
