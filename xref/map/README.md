# Spec to client map

Where each execution client implements each EELS spec function. Grep it,
never read a file whole:

    rg calculate_intrinsic_cost xref/map/     every client, one call
    rg IntrinsicGas xref/map/                 which spec function a client function is

`spec.tsv` lists each spec function's steps once:

| spec | step | what it does |
|---|---|---|

Each client has its own file, one row per client function:

| spec | steps | role | location | function |
|---|---|---|---|---|

- `steps` are ids from `spec.tsv`.
- `role` is `entry` (the main function), `part` (does some steps),
  `inlined` (the steps sit inside a function doing other work),
  `delegated` (the code belongs to another spec function, `location` says
  which, look that one up) or `missing` (the client has no code for these
  steps).
- `location` is a path from the protocol-mesh root and a line range.

Built by `./xref.sh build` and written by `./xref.sh tsv`, from
`xref/data/tree.json`, which also holds each match's quoted line, note and
confidence. Line numbers are from the commits in `tree.json`, so open the
file before trusting them.
