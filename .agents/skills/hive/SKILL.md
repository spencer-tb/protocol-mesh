---
name: hive
description: Run hive locally against one or more clients, including EELS consume simulators and devnet client builds, and read the results, locally or from CI through clive. Hive runs are heavy, ask the user first.
---

# Hive

Hive is built by `./setup.sh hive` at `repos/hive/hive`. It needs
docker running. A run builds client and simulator images and starts many
containers, so ask the user before starting one, and run one at a time.

## Run

From `repos/hive`:

```
./hive --sim ethereum/eels/consume-engine --client go-ethereum,reth \
  --sim.parallelism 4 --sim.limit "<test regex>"
```

- EELS simulators: `ethereum/eels/consume-engine`, `consume-rlp`,
  `consume-sync`, `execute-blobs`.
- Fixtures: `--sim.buildarg fixtures=<release>`. The default is
  `stable@latest`. Devnet releases are listed by the `devnet` skill.
- Clients from devnet branches: `--client-file
  ../hive-tests/.github/configs/hive/<file>.yaml`. Files that use
  `${DEVNET}` need it filled in first, for example with
  `sed 's/${DEVNET}/heze-devnet-3/g' <file> > /tmp/clients.yaml`.
- `--docker.output` shows container output while debugging.

## Read local results

Results land in `workspace/logs/`, one JSON file per suite, plus client
logs.

```
f=$(ls -t workspace/logs/*.json | head -1)
jq -r '.testCases[] | select(.summaryResult.pass | not) | .name' "$f"
```

For a browser view, `go run ./cmd/hiveview -serve -addr 127.0.0.1:8090`
(the default port 8080 clashes with hapi).

## Read CI results

`clive` queries `hapi`, which mirrors https://hive.ethpandaops.io. Both
live in `clive/` in this repo and `./setup.sh clive` builds them. Start
`hapi` once in the background unless `clive groups` already answers, then:

```
clive groups
clive summary <group>
clive diff <group>
clive fails <group> --client=reth
clive tests <group> --client=reth --filter="<regex>"
clive detail <group> --client=reth --filter="<test>"
```

Add `--json` for machine-readable output. `clive/README.md` lists every
command, flag and JSON field. Every test has a `hive_url`, its page in the
hive UI with the description, error and client logs. Give it to the user
when they want to see more of a test.
