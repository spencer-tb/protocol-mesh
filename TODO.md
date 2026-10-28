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
- [ ] **Spec and test releases.** Drive releases from this workspace:
  select source revisions and fork targets, generate fixtures, validate
  across the selected clients, and package and publish the artifacts with
  release notes. Record source commits, tool versions, artifact hashes and
  validation results so a release can be reproduced.

## Cross-client map (xref)

- [x] Benchmark: 40 spec functions x geth and reth, grep agent vs `xref find`
  vs the judged map, answer key pooled and labelled by a separate verifier.
  Results in `xref/bench/results/report.md`.
- [ ] Spot-check the benchmark's labels, the 10 listed in the report.
- [ ] Build the map with an agent that searches the client, instead of
  keyword candidates plus a judge: the benchmark's grep agent found more of
  the right functions for about the same cost per question.
- [ ] Better candidates before the judge: gas constants and their values,
  opcode numbers, precompile addresses, EIP numbers, then the call graph.
- [ ] Execution-based mapping: run the EELS tests that hit a spec function
  through each client's runner with coverage, and diff against tests that
  do not. Use it to confirm rows for EIPs under active work.
- [ ] Query by EIP or fork: `./xref.sh eip 7928` and `./xref.sh fork
  amsterdam` list every EELS function the EIP or fork touches, with each
  client's rows. Tags from code and docstrings miss functions that never
  name their EIP, so add the functions the EIP's EELS commits changed and
  the fork-to-fork diff.
- [ ] Teams confirm their own client's rows, with a verified flag per row.
- [x] Differential fuzzing for geth (`fuzz/`): memory, memory extension,
  the 63/64 call rule, blob base fee, excess blob gas and intrinsic gas
  with the calldata floor. 13,579 cases, no differences.
- [ ] Export the map's confident pure-function pairs (spec function, client
  function, file and line) in a format fuzzing teams can feed into their
  own tools. We do not build or run a fuzzer ourselves.
- [ ] Prove equal for all inputs, with an SMT solver, the few formulas that
  matter most, such as `fake_exponential` and intrinsic gas.

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
