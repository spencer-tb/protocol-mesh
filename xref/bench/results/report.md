# xref benchmark

40 spec functions x 4 clients (geth, reth, nethermind, besu). The answer key pools every method's candidates and has a separate verifier label each one.

| Method | Client | Questions | Top-1 right | Recall | Precision | Median time | Median turns | Cost |
|---|---|---|---|---|---|---|---|---|
| A, grep agent | geth | 40 | 100% | 74% | 91% | 11.9s | 5.0 | $3.44 |
| A, grep agent | reth | 40 | 97% | 51% | 83% | 14.4s | 6.0 | $4.81 |
| A, grep agent | nethermind | 40 | 95% | 54% | 81% | 16.0s | 6.0 | $5.68 |
| A, grep agent | besu | 40 | 92% | 60% | 84% | 12.2s | 6.0 | $4.02 |
| B, xref find (top 5) | geth | 40 | 42% | 50% | 36% | 0.0s | - | $0.00 |
| B, xref find (top 5) | reth | 40 | 45% | 26% | 23% | 0.0s | - | $0.00 |
| B, xref find (top 5) | nethermind | 40 | 49% | 33% | 26% | 0.0s | - | $0.00 |
| B, xref find (top 5) | besu | 40 | 32% | 30% | 24% | 0.0s | - | $0.00 |
| C, judged map | geth | 40 | 95% | 62% | 97% | 0.0s | - | $3.49 |
| C, judged map | reth | 19 | 92% | 22% | 88% | 0.0s | - | $1.49 |
| E, step tables | geth | 40 | 95% | 70% | 84% | 0.0s | - | $12.53 |
| E, step tables | reth | 40 | 100% | 55% | 92% | 0.0s | - | $10.60 |
| E, step tables | nethermind | 40 | 95% | 71% | 81% | 0.0s | - | $12.03 |
| E, step tables | besu | 40 | 95% | 87% | 89% | 30.5s | - | $24.71 |

Verifier cost: $18.03. C's cost is what judging those entries cost once, after that a lookup is free. B and C times exclude loading the index.

## Labels to spot-check

- transactions.calculate_max_gas_fee|besu: `Transaction.getMaxUpfrontGasCost` (besu/ethereum/core/src/main/java/org/hyperledger/besu/ethereum/core/Transaction.java:666) is unlabelled. 
- fork.execute_block|reth: `EthEvmConfig.context_for_block` (reth/crates/ethereum/evm/src/lib.rs:456) is partial. Copies block header and body fields (parent beacon root, withdrawals, ommers, slot number) into the execution context, matching the spec's block_env setup inside execute_block. No execution or validation.
- transactions.calculate_max_gas_fee|nethermind: `TransactionProcessorBase.BuyGas` (nethermind/src/Nethermind/Nethermind.Evm/TransactionProcessing/TransactionProcessor.cs:1192) is partial. Inline balanceCheck = GasLimit*MaxFeePerGas (1559) or GasLimit*effectiveGasPrice is the spec computation, embedded in the full buy-gas/balance logic.
- fork.process_general_purpose_requests|geth: `processRequestsSystemCall` (go-ethereum/core/state_processor.go:469) is partial. Shared helper for one system call. Checks the contract has code, calls it and adds the type-prefixed return data to requests if it isn't empty. This covers one request step of the spec function.
- vm.gas.charge_gas_from_meter|besu: `EVM.runToHaltV2` (besu/evm/src/main/java/org/hyperledger/besu/evm/EVM.java:511) is partial. Not in the snippet, which is cut off: assumed from besu's usual V2 loop that it checks gas left against the cost and decrements or halts after each operation. That would be the charge logic inlined.
- vm.gas.charge_gas_from_meter|reth: `Gas.record_cost_unsafe` (revm/crates/interpreter/src/gas.rs:292) is equivalent. Compares remaining gas with cost and subtracts it, returning an out-of-gas flag. The caller turns that flag into the error. It always subtracts (wrapping) even on out-of-gas, but execution halts on that path, so the effect matches the spec.
- fork.apply_body|besu: `BlockAccessList.BlockAccessListBuilder.createPostExecutionAccessLocationTracker` (besu/ethereum/core/src/main/java/org/hyperledger/besu/ethereum/mainnet/block/access/list/BlockAccessList.java:200) is partial. Sets the post-execution access index to the transaction count + 1, the EIP-7928 step that apply_body does inline.
- vm.instructions.system.delegatecall|besu: `TangerineWhistleGasCalculator.callOperationStaticGasCost` (besu/evm/src/main/java/org/hyperledger/besu/evm/gascalculator/TangerineWhistleGasCalculator.java:60) is partial. Static call cost: base cost plus memory expansion over the input and output regions. This is part of the delegatecall charge computation.
- vm.gas.settle_transaction_gas|nethermind: `IGasPolicy.GetPreRefundGas` (nethermind/src/Nethermind/Nethermind.Evm/GasPolicy/IGasPolicy.cs:49) is partial. tx_gas - gas_left - state_gas_left (gas_used_before_refund).
- fork.make_receipt|besu: `MainnetProtocolSpecs.BerlinTransactionReceiptFactory.create` (besu/ethereum/core/src/main/java/org/hyperledger/besu/ethereum/mainnet/MainnetProtocolSpecs.java:1473) is equivalent. Builds the receipt for an executed tx: status from success, cumulative gas, logs and tx type; bloom presumably built by the public constructor. Encoding happens elsewhere in besu.
