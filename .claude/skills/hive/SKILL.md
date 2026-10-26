---
name: hive
description: Run hive locally against one or more clients, including EELS consume simulators and devnet client builds, and read the results, locally or from CI through clive. Hive runs are heavy, ask the user first.
---

# Hive

Hive is built by `setup.sh hive` at `~/ethereum/hive/hive`. It needs
docker running. A run builds client and simulator images and starts many
containers, so ask the user before starting one, and run one at a time.

## Run

From `~/ethereum/hive`:

```
./hive --sim ethereum/eels/consume-engine --client go-ethereum,reth \
  --sim.parallelism 4 --sim.limit "<test regex>"
```

- EELS simulators: `ethereum/eels/consume-engine`, `consume-rlp`,
  `consume-sync`, `execute-blobs`.
- Fixtures: `--sim.buildarg fixtures=<release>`. The default is
  `stable@latest`. Devnet releases are listed by the `devnet` skill.
- Clients from devnet branches: `--client-file
  ~/ethereum/hive-tests/.github/configs/hive/<file>.yaml`. Files that use
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

`clive` queries `hapi`, which mirrors https://hive.ethpandaops.io. Start
`hapi` once in the background, then:

```
clive groups
clive fails <group> --client=reth
clive detail <group> --client=reth --filter="<test>"
clive diff <group>
```

Add `--json` for machine-readable output.
