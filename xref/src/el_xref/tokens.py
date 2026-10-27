"""Split identifiers and prose into comparable tokens."""

import re
from collections.abc import Iterable
from itertools import pairwise

SUBWORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
EIP = re.compile(r"\beip[-_ ]?(\d{3,5})\b|(?<=[a-z_])eip(\d{3,5})", re.I)

STOPWORDS = frozenset(
    """
    a an the of to in is if or and for on by be it as at with from this that
    self ctx err nil none null true false return let mut var val new fn func
    def pub const static void int uint long ulong bool string str u8 u16 u32
    u64 u128 u256 usize i64 result ok some get set use using impl type mod
    """.split()
)


def eips(text: str) -> set[str]:
    """Return the EIP numbers mentioned in `text`, as `eipNNNN` tokens."""
    return {f"eip{a or b}" for a, b in EIP.findall(text)}


def split_identifier(name: str) -> list[str]:
    """
    Split `name` into lowercase subwords plus adjacent-pair joins.

    The joins let `SStore` (`s`, `store`) meet `sstore`, and `BlobGas`
    meet `blobgas`.
    """
    parts = [p.lower() for p in SUBWORD.findall(name.replace("_", " "))]
    joins = [a + b for a, b in pairwise(parts)]
    return [t for t in parts + joins if t not in STOPWORDS and len(t) > 1]


def tokenize(text: str) -> list[str]:
    """Tokenize source or prose: subwords of every word, plus EIP tags."""
    out: list[str] = []
    for word in WORD.findall(text):
        out.extend(split_identifier(word))
    out.extend(eips(text))
    return out


def bag(*weighted: tuple[Iterable[str], int]) -> list[str]:
    """Concatenate token lists, repeating each list by its weight."""
    out: list[str] = []
    for tokens, weight in weighted:
        out.extend(list(tokens) * weight)
    return out
