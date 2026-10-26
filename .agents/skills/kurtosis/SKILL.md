---
name: kurtosis
description: Spin up a local multi-client devnet with Kurtosis and the ethpandaops ethereum-package, from a devnet's own config or a custom one, inspect it and tear it down. Heavy, ask the user first.
---

# Kurtosis

A network runs many docker containers. Ask the user before starting one,
run one at a time, and remove it when you are done.

## Prerequisites

- docker running.
- The Kurtosis CLI, `kurtosis version`. On macOS:
  `brew install kurtosis-tech/tap/kurtosis-cli`. Elsewhere see
  https://docs.kurtosis.com/install.

## Start

From a devnet's own config, using the checked-out package:

```
kurtosis run --enclave heze3 repos/ethereum-package \
  --args-file repos/heze-devnets/kurtosis/devnet-3-local.yaml
```

Use `github.com/ethpandaops/ethereum-package` instead of the local path for
the released package.

A custom network starts from a copy of one of those files or of the
examples in `repos/ethereum-package/.github/tests/`. Set the clients
in `participants` or `participants_matrix` (`el_type`, `el_image`,
`cl_type`, `cl_image`) and the forks in `network_params`, for example
`gloas_fork_epoch: 0`. Every option and its default is in
`repos/ethereum-package/network_params.yaml`.

To test a local client change, build its image and point `el_image` at it:
`docker build -t geth:local repos/clients/go-ethereum`.

## Inspect

```
kurtosis enclave inspect heze3            # services and ports
kurtosis service logs heze3 <service> -f
```

Add `dora` to `additional_services` for a block explorer, and `spamoor`
for transaction load.

## Tear down

```
kurtosis enclave rm -f heze3
kurtosis clean -a                         # every enclave
```
