# runners

Runs blockchain or state test fixtures through each execution client's own
test runner and reports pass and fail in one format.

    just blocktest <fixtures> [client ...] [--json]
    just statetest <fixtures> [client ...] [--json]

`<fixtures>` is a fixtures release or fill output, its `blockchain_tests`
or `state_tests` directory, or any directory or file below that. With no
client named it runs your team's client from `.mesh/me.toml`, or every
client when your team has none. `all` names every client, and
`--workers N` caps the parallelism of runners that have it.

    just blocktest work/fixtures/tests@v21.0.2/blockchain_tests/for_osaka geth
    just statetest work/fixtures/tests@v21.0.2 all --json

The report has a row per client and the first few failures of each. Every
client's full results go to `work/blocktest/<client>.json` or
`work/statetest/<client>.json`, with its runner's raw output in
`<client>.log` next to it. The exit code is 0 only when every built client
ran and passed.

## Runners

`./setup.sh <client>` builds each one into `bin/<client>/`, and
`./setup.sh evm2` builds reth's state test runner into `bin/evm2/`.

| Client | `blocktest` | `statetest` |
|---|---|---|
| geth | `evm blocktest` | `evm statetest` |
| reth | `ef-test-runner`, per file | evm2's `evm2 replay --json-output`, once per file |
| nethermind | `nethtest --blockTest` | `nethtest --stateTest` |
| besu | `evmtool block-test --json-array` | `evmtool state-test --json-array` |
| erigon | `evm blocktest --jsonout` | `evm statetest --jsonout` |
| ethrex | the `all` test of `tooling/ef_tests/blockchain`, per file | `ef_tests-statev2` |
| nimbus | `build/eest_blockchain`, once per file | `tools/evmstate`, once per file |
| evmone | none | `evmone test`, once per file, per file |

Results are per test, except where the table says per file.

Where the runners differ:

- reth's runner takes `<suite>/blockchain_tests/<dir>`, will not follow a
  linked directory and stops at its first failing directory. So it gets a
  temporary mirror with every fixture in one directory. It prints only
  failed and skipped files, so the rest count as passed.
- reth has no state test runner of its own, so its state tests run on
  evm2, the EVM it uses. The build is from evm2's main, which can be ahead
  of the evm2 commit reth pins in its `Cargo.toml`.
- ethrex's blockchain runner is a cargo test binary that runs everything
  under `vectors/` in its working directory. It passes pre-Merge tests
  without running them. Its state runner writes report files under
  `reports/`, which are read back.
- nimbus's `eest_blockchain` takes one file and, run on its own, also
  checks every block statelessly. `evmstate` takes one file too.
- A runner that takes one file at a time and exits with an error without
  naming a failing test fails the file itself. A file it runs no test from
  counts as skipped.
- `evmone test` runs state and blockchain fixtures alike, one test per
  file, and names only the fixtures that failed or were skipped. Here it
  runs state tests only.
- nethermind's `nethtest` needs `DOTNET_ROOT` when .NET is not in its
  default place, as with Homebrew. The runner finds it from `dotnet`.

## Tests

`just test` runs `tests/`, which check the output parsers and the report
against fake runners.
