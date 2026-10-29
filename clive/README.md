# clive and hapi

Query the hive results CI publishes at https://hive.ethpandaops.io. hapi is
a small API server that fetches and caches each group's runs, and clive is
the CLI that queries it. Both are Go with no dependencies outside the
standard library.

    ./setup.sh clive    # builds ~/.local/bin/clive and ~/.local/bin/hapi

Start hapi once in the background, then query:

    hapi &                      # every group, refreshed every 5 minutes
    hapi --groups heze,bogota   # only these groups, faster to start
    clive groups
    clive summary glamsterdam-quick

hapi listens on `:8080` by default. Pick another port with `--addr` and
point clive at it with `--api=URL` or `HIVE_API_URL`.

Ported from https://github.com/spencer-tb/clive at `f2881d2`.

## Commands

| Command | Shows |
|---|---|
| `clive groups` | the result groups |
| `clive summary <group>` | pass and fail counts per client and simulator |
| `clive fails <group>` | fail counts and rates per client |
| `clive passes <group>` | pass counts and rates per client |
| `clive tests <group>` | failing tests, or passing with `--status=pass` |
| `clive detail <group>` | one test's error, client log and fill, consume and hive commands |
| `clive diff <group>` | regressions and fixes between the latest two runs |
| `clive search <group> <query>` | tests whose name matches |

Flags: `--client=X`, `--simulator=Y`, `--filter=REGEX` (case-insensitive,
on test names), `--status=fail|pass`, `--limit=N`, `--full-log` (the whole
client log, not the last 30 lines) and `--json`, which every command takes.

    clive fails bal-quick --client=besu
    clive tests bal-quick --client=reth --simulator=consume-engine
    clive detail bal-quick --client=reth --filter="test_bal_gas_limit"
    clive detail bal-quick <suite_file> <test_index>
    clive search bal-quick eip7702 --client=besu --status=fail
    clive tests bal-quick --json | jq -r '.tests[].pytest_id'

Each test in `tests --json`, `search --json` and `diff --json` carries
`client`, `simulator`, `fork`, `eels_test_module`, `eels_test_function`,
`pytest_id`, `eels_test_url`, `suite_file`, `hive_suite_test_index` and
`hive_url`, the test's page in the hive UI with its description, error and
client logs. `detail --json` adds `fill_command`, `consume_command`,
`hive_command`, `error_log`, `detail_log` and `client_log`.

## API

| Path | Returns |
|---|---|
| `/api/v1` | the endpoint list |
| `/api/v1/groups` | the groups |
| `/api/v1/groups/{group}/summary` | counts per client and simulator |
| `/api/v1/groups/{group}/fails` | fail rates, `?client=` `?simulator=` |
| `/api/v1/groups/{group}/passes` | pass rates, `?client=` `?simulator=` |
| `/api/v1/groups/{group}/tests` | tests, `?status=` `?client=` `?simulator=` `?filter=` `?limit=` `?offset=` |
| `/api/v1/groups/{group}/tests/lookup` | the first test matching `?filter=`, with detail |
| `/api/v1/groups/{group}/tests/{file}/{testID}` | one test's detail, `?full_log=true` |
| `/api/v1/groups/{group}/suites` | the latest suite runs |
| `/api/v1/groups/{group}/suites/{file}` | one suite's run metadata and tests, `?status=fail` |
| `/api/v1/groups/{group}/diff` | regressions and fixes between the latest two runs, `?counts=true` for totals only |
| `/api/v1/groups/{group}/search` | tests matching `?q=` |

## How it works

The `ethpandaops/hive-tests` workflows upload each run to
hive.ethpandaops.io. hapi reads the groups from `discovery.json`, then each
group's `listing.jsonl`, one line per suite run, and keeps the latest run
per client and simulator. It refreshes every group in parallel on
`--refresh`.

Suite files are up to about 40 MB, mostly test descriptions. hapi streams
them and keeps only what queries need:

- per suite, the run metadata (client repo and branch, fixtures) and the
  name of its details log
- per test, its number, its name without the client suffix, whether it
  passed, its source link and where it is in the file

Names and links are stored once and shared by every client that ran the
test. For runs without failures it reads only the run metadata at the top
of the file until a query needs their tests. `detail` fetches that one
test's bytes and its logs by range. Every test links to its page in the hive
UI, `hive_url`, for everything else.

With every group loaded that is about 100 MB of memory, where keeping
whole suite files took over 3 GB.

## Changing it

    cd clive && go vet ./... && go test ./...

- `main.go`: flags and the server.
- `handlers.go`: the routes and handlers.
- `store.go`: fetching, caching and the refresh loop.
- `parser.go`: client versions and test names.
- `types.go`: hive's data and the API responses.
- `cmd/hive-cli/main.go`: clive.

A new endpoint needs its response type in `types.go`, a handler registered
in `RegisterRoutes`, a line in `handleIndex` and a clive command.

Each client formats its version differently. A new client needs entries in
`commitPatterns` and `versionPatterns` in `parser.go`, and
`NormalizeClientName` strips the `_nametag` suffix (`reth_default` is
`reth`).

Hive test names look like
`module::function[fork_Fork-test_type-params]-client_nametag`.
`SplitTestName`, `ExtractPytestID`, `ExtractFork` and `ExtractSourceURL`
take them apart.
