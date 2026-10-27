# xref benchmark

40 spec functions x 3 clients (geth, reth, nethermind). The answer key pools every method's candidates and has a separate verifier label each one.

| Method | Client | Questions | Top-1 right | Recall | Precision | Median time | Median turns | Cost |
|---|---|---|---|---|---|---|---|---|
| A, grep agent | geth | 40 | 100% | 74% | 91% | 11.9s | 5.0 | $3.44 |
| A, grep agent | reth | 40 | 97% | 51% | 83% | 14.4s | 6.0 | $4.81 |
| A, grep agent | nethermind | 40 | 95% | 54% | 81% | 16.0s | 6.0 | $5.68 |
| B, xref find (top 5) | geth | 40 | 42% | 50% | 36% | 0.0s | - | $0.00 |
| B, xref find (top 5) | reth | 40 | 45% | 26% | 23% | 0.0s | - | $0.00 |
| B, xref find (top 5) | nethermind | 40 | 49% | 33% | 26% | 0.0s | - | $0.00 |
| C, judged map | geth | 40 | 95% | 62% | 97% | 0.0s | - | $3.49 |
| C, judged map | reth | 19 | 92% | 22% | 88% | 0.0s | - | $1.49 |
| E, step tables | geth | 40 | 95% | 70% | 84% | 0.0s | - | $12.53 |
| E, step tables | reth | 40 | 100% | 55% | 92% | 0.0s | - | $10.60 |
| E, step tables | nethermind | 40 | 95% | 71% | 81% | 0.0s | - | $12.03 |

Verifier cost: $14.77. C's cost is what judging those entries cost once, after that a lookup is free. B and C times exclude loading the index.

## Labels to spot-check

- vm.interpreter.process_call|nethermind: `VirtualMachine.InitializeFrameCore` (nethermind/src/Nethermind/Nethermind.Evm/VirtualMachine.ExecutionHandlers.cs:62) is partial. Credits the value to the target account at frame start (the receiving side of move_ether).
- fork.execute_block|geth: `StateProcessor.processParallel` (go-ethereum/core/state_processor_parallel.go:133) is partial. Parallel BAL-driven execution of the block body plus concurrent state root computation; the apply_body part of execute_block without header comparisons.
- transactions.check_nonce|nethermind: `TransactionProcessorBase.IncrementNonce` (nethermind/src/Nethermind/Nethermind.Evm/TransactionProcessing/TransactionProcessor.cs:1323) is partial. Inlines the spec check for regular txs (tx.Nonce vs state nonce, too high/too low errors), then also increments the nonce.
- fork.check_transaction|reth: `load_accounts` (revm/crates/handler/src/pre_execution.rs:19) is partial. Builds the access-list address and slot sets, as check_transaction does for the tx env. Also warms the coinbase and precompiles, which the spec does elsewhere.
- vm.gas.charge_state_gas_from_meter|geth: `GasBudget.charge` (go-ethereum/core/vm/gascosts.go:112) is equivalent. Holds the spec logic: takes from StateGas first, spills the rest into ExecutionGas, tracks Spilled, and returns false (out of gas) if both together are too small. Also handles execution cost, which is zero for state-only charges.
- vm.instructions.system.delegatecall|reth: `load_acc_and_calc_gas` (revm/crates/interpreter/src/instructions/contract/call_helpers.rs:61) is partial. Charges the dynamic access and delegation gas (and state gas). It then works out and charges the 63/64-capped child gas limit. This matches the spec's gas checks plus calculate_message_call_gas and charge_gas, only one part of delegatecall.
- fork.check_transaction|nethermind: `TransactionProcessorBase.Execute` (nethermind/src/Nethermind/Nethermind.Evm/TransactionProcessing/TransactionProcessor.cs:315) is partial. Runs the admission sequence (static validation, effective gas price, sender recovery, EOA check, balance check, nonce check, gas allocation), then also executes the tx. check_transaction is inlined in this larger function.
- vm.interpreter.process_call|nethermind: `EvmInstructions.CreateFullCallFrame` (nethermind/src/Nethermind/Nethermind.Evm/Instructions/EvmInstructions.Call.cs:583) is partial. Takes the snapshot and subtracts the value from the caller's balance (snapshot plus the sending side of move_ether) before the child frame runs.
- vm.eoa_delegation.calculate_delegation_cost|reth: `load_account_delegated` (revm/crates/interpreter/src/instructions/contract/call_helpers.rs:145) is partial. Includes the delegation logic (detect 7702 target, add warm read plus extra cold cost if the target is cold) inside a larger call-cost function that also charges for the account's own cold access and new-account cost.
- fork.check_transaction|reth: `InitialAndFloorGas.initial_gas_and_reservoir` (revm/crates/context/interface/src/cfg/gas.rs:560) is partial. Splits gas into a regular-gas grant capped by the tx limit and a state-gas reservoir, matching allocate_evm_gas.
