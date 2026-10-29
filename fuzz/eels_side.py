"""
Compute the EELS results for the fuzz cases.

Runs inside an execution-specs checkout's environment:

    uv run --project <execution-specs> python eels_side.py IN OUT
"""

import json
import sys
from typing import Any, get_type_hints

from ethereum.forks.amsterdam.blocks import Header
from ethereum.forks.amsterdam.fork_types import Authorization
from ethereum.forks.amsterdam.transactions import (
    Access,
    FeeMarketTransaction,
    SetCodeTransaction,
    calculate_intrinsic_cost,
)
from ethereum.forks.amsterdam.vm.gas import (
    calculate_blob_gas_price,
    calculate_excess_blob_gas,
    calculate_gas_extend_memory,
    calculate_memory_gas_cost,
    max_message_call_gas,
)
from ethereum.state import Address
from ethereum_types.bytes import Bytes, Bytes0, Bytes32
from ethereum_types.numeric import U8, U64, U256, Uint

SENDER = Address(b"\x11" * 20)
OTHER = Address(b"\x22" * 20)


def zero(kind: Any) -> Any:
    """Return a zero value of a header field's type."""
    base = kind
    while hasattr(base, "__supertype__"):
        base = base.__supertype__
    if issubclass(base, bytes):
        length = getattr(base, "LENGTH", 0)
        return kind(b"\x00" * length)
    return kind(0)


def parent_header(excess: int, used: int, base_fee: int) -> Header:
    """Return a header with only the blob and base fee fields set."""
    fields = {
        name: zero(kind) for name, kind in get_type_hints(Header).items()
    }
    fields["excess_blob_gas"] = U64(excess)
    fields["blob_gas_used"] = U64(used)
    fields["base_fee_per_gas"] = Uint(base_fee)
    return Header(**fields)


def transaction(args: dict[str, Any]) -> Any:
    """Build the transaction a case describes."""
    to = {"create": Bytes0(b""), "self": SENDER, "other": OTHER}[args["to"]]
    access = tuple(
        Access(
            account=Address(bytes([i + 1]) * 20),
            slots=tuple(Bytes32(bytes([k + 1]) * 32) for k in range(keys)),
        )
        for i, keys in enumerate(args["access"])
    )
    common = dict(
        chain_id=U64(1),
        nonce=U64(0) if args["auths"] is not None else U256(0),
        max_priority_fee_per_gas=Uint(0),
        max_fee_per_gas=Uint(0),
        gas=Uint(0),
        to=to,
        value=U256(int(args["value"])),
        data=Bytes(bytes.fromhex(args["data"])),
        access_list=access,
        y_parity=U256(0),
        r=U256(0),
        s=U256(0),
    )
    if args["auths"] is None:
        return FeeMarketTransaction(**common)
    authorization = Authorization(
        chain_id=U256(1),
        address=OTHER,
        nonce=U64(0),
        y_parity=U8(0),
        r=U256(0),
        s=U256(0),
    )
    return SetCodeTransaction(
        **common, authorizations=(authorization,) * args["auths"]
    )


def evaluate(case: dict[str, Any]) -> Any:
    """Return the EELS result for one case."""
    fn, args = case["fn"], case["args"]
    if fn == "memory":
        return int(calculate_memory_gas_cost(Uint(int(args["size"]))))
    if fn == "memory_extend":
        memory = bytearray(int(args["current"]))
        size = U256(int(args["size"]))
        return int(calculate_gas_extend_memory(memory, [(U256(0), size)]).cost)
    if fn == "callgas":
        return int(max_message_call_gas(Uint(int(args["gas"]))))
    if fn == "blobfee":
        return int(calculate_blob_gas_price(U64(int(args["excess"]))))
    if fn == "excess":
        header = parent_header(
            int(args["excess"]), int(args["used"]), int(args["base_fee"])
        )
        return int(calculate_excess_blob_gas(header))
    if fn == "intrinsic":
        cost = calculate_intrinsic_cost(transaction(args), SENDER)
        return [int(cost.execution), int(cost.calldata_floor)]
    raise ValueError(f"unknown function {fn}")


def main() -> None:
    """Read the cases and write one result line each."""
    source, target = sys.argv[1], sys.argv[2]
    with open(source) as cases, open(target, "w") as out:
        for line in cases:
            case = json.loads(line)
            try:
                result: dict[str, Any] = {"ok": evaluate(case)}
            except Exception as error:  # noqa: BLE001
                result = {"error": f"{type(error).__name__}: {error}"[:200]}
            out.write(json.dumps({"id": case["id"], **result}) + "\n")


if __name__ == "__main__":
    main()
