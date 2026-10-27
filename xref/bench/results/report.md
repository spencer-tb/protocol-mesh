# xref benchmark

40 spec functions x 2 clients (geth, reth). The answer key pools every method's candidates and has a separate verifier label each one.

| Method | Client | Questions | Top-1 right | Recall | Precision | Median time | Median turns | Cost |
|---|---|---|---|---|---|---|---|---|
| A, grep agent | geth | 40 | 100% | 89% | 91% | 11.9s | 5.0 | $3.44 |
| A, grep agent | reth | 40 | 100% | 84% | 83% | 14.4s | 6.0 | $4.81 |
| B, xref find (top 5) | geth | 40 | 42% | 60% | 36% | 0.0s | - | $0.00 |
| B, xref find (top 5) | reth | 40 | 47% | 43% | 23% | 0.0s | - | $0.00 |
| C, judged map | geth | 40 | 95% | 74% | 97% | 0.0s | - | $3.49 |
| C, judged map | reth | 19 | 100% | 45% | 88% | 0.0s | - | $1.49 |

Verifier cost: $6.40. C's cost is what judging those entries cost once, after that a lookup is free. B and C times exclude loading the index.

## Labels to spot-check

- transactions.calculate_effective_gas_price|reth: `validate_legacy_gas_price` (revm/crates/handler/src/validation.rs:29) is partial. Does the spec's legacy check: errors when gas_price < base_fee. Supplying the price itself happens elsewhere.
- vm.instructions.storage.tload|reth: `tload` (revm/crates/interpreter/src/instructions/host.rs:319) is equivalent. The TLOAD opcode handler. It checks for Cancun, takes the key from the stack top, reads transient storage for the current target and writes the value back in place. The fixed gas cost is presumably charged through the instruction table.
- vm.instructions.system.generic_create|reth: `FrameResult.refundable_state_gas` (revm/crates/handler/src/frame_data.rs:136) is partial. Decides whether the create state-gas charge is refunded: the create failed or produced no address, and the charge was taken. This is the condition for credit_state_gas_refund in generic_create's outcome step. The actual refund is applied in return_result.
- fork.state_transition|geth: `BlockChain.insertChain` (go-ethereum/core/blockchain.go:1917) is partial. Per-block import loop that drives the transition: verifies headers, executes each block, then writes state and block. Execution and state work are delegated.
- fork.process_general_purpose_requests|geth: `PostExecution` (go-ethereum/core/state_processor.go:190) is partial. Runs the whole request sequence in spec order: deposit logs, withdrawal, consolidation, builder deposit, builder exit. It also applies withdrawals and builds the block access list, so the request logic sits inside a larger function.
- vm.eoa_delegation.resolve_delegated_code_address|reth: `load_account_delegated` (revm/crates/interpreter/src/instructions/contract/call_helpers.rs:145) is partial. Resolves the 7702 designation and charges warm plus the cold extra for the delegated account, warming it and returning its code. Also charges target access and new-account costs, so the spec logic sits inside a larger function.
- transactions.calculate_max_gas_fee|reth: `Transaction.max_balance_spending` (revm/crates/context/interface/src/transaction.rs:165) is partial. Computes gas_limit * max_fee_per_gas inline. For legacy transactions max_fee_per_gas defaults to gas_price. It then adds the value and the blob fee for a total balance check.
- vm.instructions.system.delegatecall|geth: `opDelegateCall` (go-ethereum/core/vm/instructions.go:818) is partial. Covers the opcode body only. It pops the stack, builds the child budget from the pre-deducted gas plus the whole state-gas reservoir, calls the child, pushes success, writes return data and refunds gas. The gas pricing lives in the gas function and the frame execution in EVM.DelegateCall.
- vm.gas.forfeit_remaining_gas|geth: `GasBudget.DrainExecution` (go-ethereum/core/vm/gascosts.go:169) is equivalent. Zeroes the remaining execution gas (and adds it to the used total), which is the spec's gas_left = 0.
- vm.gas.restore_state_gas|reth: `Gas.rollback_state_gas` (revm/crates/interpreter/src/gas.rs:201) is partial. Only passes the call to GasTracker.rollback_state_gas [0], so it covers the same part of the spec function.
