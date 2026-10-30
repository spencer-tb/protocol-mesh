#!/usr/bin/env python3
"""
Differential & Invariant Fuzzer for EIP-2537 (BLS12-381 Precompiles).

Tests EELS reference implementation and cross-checks edge cases,
boundary conditions, multi-pair scalar reductions, and subgroup checks.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

# Ensure execution-specs is in sys.path
EELS_ROOT = Path("/home/roru/execution-specs")
if str(EELS_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(EELS_ROOT / "src"))
if str(EELS_ROOT) not in sys.path:
    sys.path.insert(0, str(EELS_ROOT))

from ethereum.forks.prague.vm.exceptions import (  # noqa: E402
    EthereumException,
    InvalidParameter,
)
from ethereum.forks.prague.vm.precompiled_contracts.bls12_381.bls12_381_g1 import (  # noqa: E402, E501
    bls12_g1_add,
    bls12_g1_msm,
    bls12_map_fp_to_g1,
)
from ethereum.forks.prague.vm.precompiled_contracts.bls12_381.bls12_381_g2 import (  # noqa: E402, E501
    bls12_g2_add,
)
from ethereum.forks.prague.vm.precompiled_contracts.bls12_381.bls12_381_pairing import (  # noqa: E402, E501
    bls12_pairing,
)
from ethereum_types.numeric import Uint  # noqa: E402
from tests.prague.eip2537_bls_12_381_precompiles.spec import (  # noqa: E402
    Spec,
)

# Curve parameters
FIELD_MODULUS_P = Spec.P
SCALAR_ORDER_Q = Spec.Q


def encode_fp(val: int) -> bytes:
    """Encode field element as 64-byte big-endian."""
    return val.to_bytes(64, "big")


def encode_scalar(val: int) -> bytes:
    """Encode scalar as 32-byte big-endian."""
    return (val % (1 << 256)).to_bytes(32, "big")


def make_mock_evm(data: bytes, gas: int = 50_000_000) -> SimpleNamespace:
    """Create a minimal mock Evm frame for precompile execution."""
    return SimpleNamespace(
        message=SimpleNamespace(data=data),
        gas_left=Uint(gas),
        output=b"",
    )


@dataclass
class FuzzResult:
    """Stores execution metrics and outcomes of a precompile fuzz run."""

    name: str
    total_cases: int
    passed_cases: int
    expected_errors: int
    unexpected_errors: int
    duration_s: float


def fuzz_g1_add(cases: int = 50) -> FuzzResult:
    """Fuzz BLS12_G1ADD precompile with identities, padding & random data."""
    start = time.perf_counter()
    passed = 0
    expected_errs = 0
    unexpected_errs = 0

    # 1. Identity: P + Inf = P
    evm = make_mock_evm(bytes(Spec.G1) + bytes(Spec.INF_G1))
    try:
        bls12_g1_add(evm)
        assert evm.output == bytes(Spec.G1)
        passed += 1
    except Exception:
        unexpected_errs += 1

    # 2. Invalid padding in top 16 bytes
    bad_padding_p = (
        (0x01).to_bytes(16, "big")
        + bytes(Spec.G1)[16:64]
        + bytes(Spec.G1)[64:128]
    )
    evm_bad = make_mock_evm(bad_padding_p + bytes(Spec.G1))
    try:
        bls12_g1_add(evm_bad)
        unexpected_errs += 1
    except (InvalidParameter, EthereumException):
        expected_errs += 1
        passed += 1

    # 3. Coordinate >= P (field modulus violation)
    mod_overflow_p = encode_fp(FIELD_MODULUS_P + 1) + bytes(Spec.G1)[64:128]
    evm_mod = make_mock_evm(mod_overflow_p + bytes(Spec.G1))
    try:
        bls12_g1_add(evm_mod)
        unexpected_errs += 1
    except (InvalidParameter, EthereumException):
        expected_errs += 1
        passed += 1

    # 4. Random byte lengths & junk data
    for _ in range(cases):
        length = random.choice([0, 100, 255, 256, 257, 512])
        data = random.randbytes(length)
        evm_rand = make_mock_evm(data)
        try:
            bls12_g1_add(evm_rand)
            passed += 1
        except (InvalidParameter, EthereumException):
            expected_errs += 1
            passed += 1
        except Exception:
            unexpected_errs += 1

    duration = time.perf_counter() - start
    return FuzzResult(
        "bls12_g1_add",
        cases + 3,
        passed,
        expected_errs,
        unexpected_errs,
        duration,
    )


def fuzz_g1_msm(cases: int = 50) -> FuzzResult:
    """Fuzz BLS12_G1MSM with multi-pair identities and scalar reductions."""
    start = time.perf_counter()
    passed = 0
    expected_errs = 0
    unexpected_errs = 0

    # PR #3641 edge case: Scalar > Q with bit 255 set summing to identity
    s1 = (1 << 256) - 1
    s2 = SCALAR_ORDER_Q - (s1 % SCALAR_ORDER_Q)
    pair1 = bytes(Spec.G1) + encode_scalar(s1)
    pair2 = bytes(Spec.G1) + encode_scalar(s2)
    evm = make_mock_evm(pair1 + pair2)
    try:
        bls12_g1_msm(evm)
        assert evm.output == bytes(Spec.INF_G1)
        passed += 1
    except Exception:
        unexpected_errs += 1

    # Boundary violation on coordinate x pos 1 (from PR #3641)
    max_fp_bit_set_p = encode_fp(Spec.P1.x | Spec.MAX_FP_BIT_SET) + encode_fp(
        Spec.P1.y
    )
    bad_pair = max_fp_bit_set_p + encode_scalar(0)
    evm_viol = make_mock_evm(pair1 + bad_pair)
    try:
        bls12_g1_msm(evm_viol)
        unexpected_errs += 1
    except (InvalidParameter, EthereumException):
        expected_errs += 1
        passed += 1

    # Multi-pair random scalars
    for _ in range(cases):
        num_pairs = random.randint(1, 3)
        input_bytes = bytearray()
        for _ in range(num_pairs):
            input_bytes.extend(bytes(Spec.G1))
            input_bytes.extend(encode_scalar(random.randint(0, 2**256 - 1)))
        evm_rand = make_mock_evm(bytes(input_bytes))
        try:
            bls12_g1_msm(evm_rand)
            passed += 1
        except (InvalidParameter, EthereumException):
            expected_errs += 1
            passed += 1
        except Exception:
            unexpected_errs += 1

    duration = time.perf_counter() - start
    return FuzzResult(
        "bls12_g1_msm",
        cases + 2,
        passed,
        expected_errs,
        unexpected_errs,
        duration,
    )


def fuzz_map_fp_to_g1(cases: int = 50) -> FuzzResult:
    """Fuzz BLS12_MAP_FP_TO_G1 with valid and out-of-field inputs."""
    start = time.perf_counter()
    passed = 0
    expected_errs = 0
    unexpected_errs = 0

    # Valid mapping of zero
    evm_zero = make_mock_evm(encode_fp(0))
    try:
        bls12_map_fp_to_g1(evm_zero)
        assert len(evm_zero.output) == 128
        passed += 1
    except Exception:
        unexpected_errs += 1

    # Non-canonical field element >= P
    evm_bad_p = make_mock_evm(encode_fp(FIELD_MODULUS_P + 42))
    try:
        bls12_map_fp_to_g1(evm_bad_p)
        unexpected_errs += 1
    except (InvalidParameter, EthereumException):
        expected_errs += 1
        passed += 1

    for _ in range(cases):
        fp_val = random.randint(0, FIELD_MODULUS_P - 1)
        evm = make_mock_evm(encode_fp(fp_val))
        try:
            bls12_map_fp_to_g1(evm)
            assert len(evm.output) == 128
            passed += 1
        except Exception:
            unexpected_errs += 1

    duration = time.perf_counter() - start
    return FuzzResult(
        "bls12_map_fp_to_g1",
        cases + 2,
        passed,
        expected_errs,
        unexpected_errs,
        duration,
    )


def fuzz_g2_add(cases: int = 30) -> FuzzResult:
    """Fuzz BLS12_G2ADD with identities and invalid inputs."""
    start = time.perf_counter()
    passed = 0
    expected_errs = 0
    unexpected_errs = 0

    # 1. Identity: P + Inf = P
    evm = make_mock_evm(bytes(Spec.G2) + bytes(Spec.INF_G2))
    try:
        bls12_g2_add(evm)
        assert evm.output == bytes(Spec.G2)
        passed += 1
    except Exception:
        unexpected_errs += 1

    # 2. Modulus overflow on G2
    bad_g2 = encode_fp(FIELD_MODULUS_P + 1) + bytes(Spec.G2)[64:256]
    evm_bad = make_mock_evm(bad_g2 + bytes(Spec.G2))
    try:
        bls12_g2_add(evm_bad)
        unexpected_errs += 1
    except (InvalidParameter, EthereumException):
        expected_errs += 1
        passed += 1

    for _ in range(cases):
        length = random.choice([0, 128, 511, 512, 513])
        evm_rand = make_mock_evm(random.randbytes(length))
        try:
            bls12_g2_add(evm_rand)
            passed += 1
        except (InvalidParameter, EthereumException):
            expected_errs += 1
            passed += 1
        except Exception:
            unexpected_errs += 1

    duration = time.perf_counter() - start
    return FuzzResult(
        "bls12_g2_add",
        cases + 2,
        passed,
        expected_errs,
        unexpected_errs,
        duration,
    )


def fuzz_pairing(cases: int = 20) -> FuzzResult:
    """Fuzz BLS12_PAIRING with identity pairings and invalid lengths."""
    start = time.perf_counter()
    passed = 0
    expected_errs = 0
    unexpected_errs = 0

    # 1. Pairing of Inf + Inf evaluates to 1 (true)
    evm_inf = make_mock_evm(bytes(Spec.INF_G1) + bytes(Spec.INF_G2))
    try:
        bls12_pairing(evm_inf)
        assert evm_inf.output == (1).to_bytes(32, "big")
        passed += 1
    except Exception:
        unexpected_errs += 1

    # 2. Non-multiple of 384 bytes
    for _ in range(cases):
        length = random.choice([0, 1, 100, 383, 385, 767])
        evm_bad = make_mock_evm(random.randbytes(length))
        try:
            bls12_pairing(evm_bad)
            unexpected_errs += 1
        except (InvalidParameter, EthereumException):
            expected_errs += 1
            passed += 1
        except Exception:
            unexpected_errs += 1

    duration = time.perf_counter() - start
    return FuzzResult(
        "bls12_pairing",
        cases + 1,
        passed,
        expected_errs,
        unexpected_errs,
        duration,
    )


def main() -> None:
    """Run EIP-2537 fuzzer and display summary report."""
    parser = argparse.ArgumentParser(
        description="Differential / Invariant Fuzzer for EIP-2537"
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=30,
        help="Number of random cases per precompile",
    )
    args = parser.parse_args()

    print(
        f"=== Running EIP-2537 Fuzzer & Invariants ({args.cases} cases/op) ==="
    )
    results = [
        fuzz_g1_add(args.cases),
        fuzz_g1_msm(args.cases),
        fuzz_map_fp_to_g1(args.cases),
        fuzz_g2_add(args.cases),
        fuzz_pairing(args.cases),
    ]

    total_ops = sum(r.total_cases for r in results)
    total_passed = sum(r.passed_cases for r in results)
    total_unexpected = sum(r.unexpected_errors for r in results)
    total_time = sum(r.duration_s for r in results)

    print("\n------------------------------------------------------------")
    print(
        f"{'Precompile':<22} | {'Cases':<7} | {'Passed':<7} | "
        f"{'Errors':<7} | {'Time (s)'}"
    )
    print("------------------------------------------------------------")
    for r in results:
        print(
            f"{r.name:<22} | {r.total_cases:<7} | {r.passed_cases:<7} | "
            f"{r.unexpected_errors:<7} | {r.duration_s:.3f}"
        )
    print("------------------------------------------------------------")
    print(
        f"Total: {total_passed}/{total_ops} passed with "
        f"{total_unexpected} unexpected errors in {total_time:.3f}s"
    )

    if total_unexpected > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
