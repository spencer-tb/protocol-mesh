# TODO

Work for this workspace, most important first. Say which item a PR closes
and tick it when it merges.

## Now: the fast EIP loop

Fast feedback on any EIP at any time: spec and tests on an EIP branch,
fixtures filled locally, every client run on them, holes found, the EIP
updated, and round again. Nothing waits for an EELS release.

- [ ] **Worktrees for any branch.** One command creates
  `work/<topic>/<repo>` worktrees on a given branch for EELS and each client
  and builds the client binaries from them into `bin/<topic>/<client>`.
  Devnets use the same command with the branch names they already follow
  (clients `<fork>-devnet-<n>`, EELS `devnets/<fork>/<n>`) and list the
  clients that lack the branch.
- [ ] **Fill locally.** One command fills an EIP's tests from an EELS
  worktree into `work/<topic>/fixtures/`.
- [ ] **Run each client's own test runner on local fixtures.** One command
  per client runs its blockchain and state test runner straight on the
  fixtures directory and reports pass and fail in a shared format. This
  replaces `consume direct`, so the `AGENTS.md` table moves to these
  runners when it lands.

  | Client | Runner |
  |---|---|
  | geth | `evm blocktest`, `evm statetest` |
  | erigon | `cmd/evm` block, state and enginex runners |
  | besu | `evmtool block-test`, `evmtool state-test` |
  | nethermind | `nethtest` in blocktest mode |
  | reth | `testing/ef-tests` crate, pointed at the local fixtures instead of a downloaded release |
  | ethrex | `tooling/ef_tests/` blockchain, state_v2 and engine runners |
  | nimbus | the `make test` EEST suite, `tools/evmstate` |
  | evmone | `evmone-blockchaintest`, `evmone-statetest` |
  | ethereumjs | `packages/vm` `test:blockchain` and `test:state` |

- [ ] **Local fixtures into hive.** Pack the filled fixtures and run
  `consume-engine`, `enginex` and `rlp` with `--sim.buildarg
  fixtures=/fixtures`, with clients built from their worktrees.

## Next

- [ ] Release lookup in the `devnet` skill: the latest `tests@` and
  `tests-<fork>-devnet@` releases, and what the nightly fill built.
- [ ] tx-fuzz and spamoor in the `kurtosis` skill, to run before devnets.
- [ ] An `eip-loop` skill that runs every level for one EIP and reports
  pass and fail per client and level.
- [ ] A local results mode for clive (`hapi --logdir`), for local hive runs.
- [ ] The first real `./setup.sh` run, on the server, to check the erigon,
  nimbus, besu, evmone and ethereumjs builds.

## Later

- [ ] Move the repo to an org or GitHub Pro, protect `main`, and require
  review through CODEOWNERS for `setup.sh`, `bootstrap.py`, `repos.txt`,
  `AGENTS.md` and `.agents/`.
- [ ] Coordination: findings as issues, assigned to the teams that fix them.
- [ ] Help fuzzers.
- [ ] Confirm Codex and other agents load `.agents/skills`.
