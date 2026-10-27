# xref benchmark

40 spec functions x 3 clients (geth, reth, nethermind). The answer key pools every method's candidates and has a separate verifier label each one.

| Method | Client | Questions | Top-1 right | Recall | Precision | Median time | Median turns | Cost |
|---|---|---|---|---|---|---|---|---|
| A, grep agent | geth | 40 | 100% | 75% | 91% | 11.9s | 5.0 | $3.44 |
| A, grep agent | reth | 40 | 97% | 51% | 83% | 14.4s | 6.0 | $4.81 |
| A, grep agent | nethermind | 40 | 95% | 54% | 81% | 16.0s | 6.0 | $5.68 |
| B, xref find (top 5) | geth | 40 | 42% | 50% | 36% | 0.0s | - | $0.00 |
| B, xref find (top 5) | reth | 40 | 45% | 26% | 23% | 0.0s | - | $0.00 |
| B, xref find (top 5) | nethermind | 40 | 49% | 34% | 26% | 0.0s | - | $0.00 |
| C, judged map | geth | 40 | 95% | 62% | 97% | 0.0s | - | $3.49 |
| C, judged map | reth | 19 | 92% | 22% | 88% | 0.0s | - | $1.49 |
| E, step tables | geth | 40 | 88% | 79% | 44% | 19.3s | - | $11.15 |
| E, step tables | reth | 40 | 94% | 87% | 58% | 34.0s | - | $15.25 |
| E, step tables | nethermind | 40 | 97% | 89% | 51% | 38.4s | - | $18.33 |

Verifier cost: $14.25. C's cost is what judging those entries cost once, after that a lookup is free. B and C times exclude loading the index.

## Labels to spot-check

- fork.process_unchecked_system_transaction|reth: `SystemCaller.apply_beacon_root_contract_call` (alloy-evm/crates/evm/src/block/system_calls/mod.rs:123) is partial. Runs the EIP-4788 beacon-root system call and commits the resulting state. Same pattern as the spec's unchecked system call, limited to one target.
- fork.check_transaction|reth: `validate_legacy_gas_price` (revm/crates/handler/src/validation.rs:29) is partial. Checks that the gas price is at least the base fee: one of the fee-validity checks done during admission.
- fork.check_transaction|reth: `EthEvmConfig.tx_iterator_for_payload` (reth/crates/ethereum/evm/src/lib.rs:571) is partial. Decodes each transaction and recovers its sender: the recover_sender step.
- fork.check_transaction|nethermind: `TransactionProcessorBase.ValidateStatic` (nethermind/src/Nethermind/Nethermind.Evm/TransactionProcessing/TransactionProcessor.cs:987) is partial. Static admission checks: intrinsic and floor gas, EIP-8037 max total gas cap, and the per-tx block gas limit check through ValidateGas. Only part of check_transaction.
- vm.instructions.system.generic_create|reth: `JournalInner.create_account_checkpoint` (revm/crates/context/src/journal/inner.rs:538) is partial. Runs the collision check (code or nonce present means CreateCollision), which matches account_deployable in generic_create. It also marks the account created, sets its nonce to 1 and moves the value, which the spec does inside process_create. This is a sub-piece of the lifecycle.
- vm.instructions.system.generic_create|geth: `EVM.chargeAccountCreation` (go-ethereum/core/vm/evm.go:530) is partial. Covers the spec's PREFLIGHT abort (via createFramePreCheck, pushing 0 and clearing return data) and the NEW_ACCOUNT state-gas charge, which applies when the destination does not exist.
- vm.eoa_delegation.calculate_delegation_cost|reth: `JournalInner.load_account_delegated` (revm/crates/context/src/journal/inner.rs:779) is partial. Finds the delegation designation and loads the target to record whether it is cold. The caller computes the gas; this function does not.
- fork.execute_block|geth: `BlockValidator.ValidateBody` (go-ethereum/core/block_validator.go:51) is partial. Pre-execution body checks from execute_block: RLP size cap, ommers, transactions root, withdrawals root, blob gas used, BAL hash when attached.
- fork.process_unchecked_system_transaction|reth: `SystemCallEvm.system_call` (revm/crates/handler/src/system_call.rs:123) is partial. Runs and finalizes the system call with SYSTEM_ADDRESS, without committing state.
- vm.gas.allocate_evm_gas|nethermind: `TransactionProcessorBase.CalculateInitialStateReservoir` (nethermind/src/Nethermind/Nethermind.Evm/TransactionProcessing/TransactionProcessor.cs:1996) is partial. Computes only the state-reservoir part, as max(0, gasLimit - intrinsicState - cap). It does not compute the execution-gas grant.
