---
name: devnet
description: Get the context for a devnet such as heze-devnet-3 or glamsterdam-devnet-8, meaning which clients have a branch for it, its network config, its kurtosis configs, its hive client file and its EELS fixtures release.
---

# Devnets

Paths are relative to the protocol-mesh directory. A devnet is named
`<fork>-devnet-<n>`.

## Branches and images

- Each client branches with the devnet's name, for example `heze-devnet-3`.
- EELS uses `devnets/<fork>/<n>`, for example `devnets/heze/3`.
- ethPandaOps images are `ethpandaops/<client>:<fork>-devnet-<n>`.

Which clients have the branch:

```
d=heze-devnet-3
for r in ethereum/go-ethereum paradigmxyz/reth NethermindEth/nethermind \
  besu-eth/besu erigontech/erigon lambdaclass/ethrex status-im/nimbus-eth1; do
  git ls-remote --exit-code --heads "https://github.com/$r" "$d" >/dev/null \
    && echo "$r yes" || echo "$r no"
done
```

## Configs

- Network config (genesis, chain config): `repos/<fork>-devnets/network-configs/devnet-<n>/`.
- Kurtosis configs, where the repo has them:
  `repos/<fork>-devnets/kurtosis/devnet-<n>-*.yaml`. Run them with the
  `kurtosis` skill.
- Hive client files: `repos/hive-tests/.github/configs/hive/`, either
  `<devnet>.yaml` or `<fork>-git.yaml`, which builds each client from its
  devnet branch.

## Fixtures

EELS releases fixtures per devnet:
`gh release list -R ethereum/execution-specs | rg <fork>-devnet`.

## Results and data

- Hive results: the `hive` skill.
- Devnet metrics, logs and nodes: ethpandaops/panda, if you have access.
