"""
Rank client functions against a spec function with BM25.

Retrieval only has to put the right function somewhere in the top
candidates. Picking among them is the judge's job.
"""

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import Client, Config
from .index import Symbol, index_path, load, read_body
from .tokens import bag, split_identifier, tokenize

K1 = 1.2
B = 0.75
BODY_LINES = 300

# Calls that every spec function makes and that say nothing about it.
SPEC_NOISE = frozenset(
    """
    evm_trace int Uint U64 U256 ulen len isinstance Bytes Bytes32 Address
    ExecutionGas StateGas range print bool
    """.split()
)


def symbol_tokens(symbol: Symbol, body: str) -> list[str]:
    """Return the BM25 document tokens of a client function."""
    stem = PurePosixPath(symbol.path).name.split(".", 1)[0]
    parent = PurePosixPath(symbol.path).parent.name
    lines = body.splitlines()[:BODY_LINES]
    return bag(
        (split_identifier(symbol.qualname), 4),
        (split_identifier(stem), 2),
        (split_identifier(parent), 1),
        (tokenize(symbol.doc), 1),
        (tokenize("\n".join(lines)), 1),
        (symbol.eips, 2),
    )


def spec_query(spec: Symbol) -> list[str]:
    """Return the BM25 query tokens of a spec function."""
    module = spec.id.rsplit(".", 1)[0].split(".")[-1]
    tags = [t.split(":", 1)[1] for t in spec.tags]
    calls = [c for c in spec.calls if c not in SPEC_NOISE and c != spec.name]
    doc = spec.doc.split("\n\n", 1)[0]
    return bag(
        (split_identifier(spec.name), 4),
        ([t for tag in tags for t in split_identifier(tag)], 4),
        (split_identifier(module), 1),
        (tokenize(doc), 1),
        ([t for call in calls for t in split_identifier(call)], 1),
        ([t for c in spec.constants for t in split_identifier(c)], 1),
        (spec.eips, 2),
    )


@dataclass
class Corpus:
    """BM25 postings over every function of one client."""

    symbols: list[Symbol]
    postings: dict[str, list[tuple[int, int]]]
    lengths: list[int]
    average: float

    @classmethod
    def build(cls, config: Config, client: Client) -> "Corpus":
        """Index every function of `client` across its repos."""
        symbols: list[Symbol] = []
        for repo in client.repos:
            symbols.extend(load(index_path(config, repo)))
        return cls.of(
            symbols, [symbol_tokens(s, read_body(config, s)) for s in symbols]
        )

    @classmethod
    def of(cls, symbols: list[Symbol], documents: list[list[str]]) -> "Corpus":
        """Index `symbols`, each described by its token list."""
        postings: dict[str, list[tuple[int, int]]] = {}
        lengths = []
        for i, tokens in enumerate(documents):
            counts = Counter(tokens)
            lengths.append(sum(counts.values()))
            for token, tf in counts.items():
                postings.setdefault(token, []).append((i, tf))
        average = sum(lengths) / max(len(lengths), 1)
        return cls(symbols, postings, lengths, average)

    def search(self, query: list[str], k: int) -> list[tuple[Symbol, float]]:
        """Return the top `k` functions for `query`."""
        n = len(self.symbols)
        scores: dict[int, float] = {}
        for token, weight in Counter(query).items():
            posting = self.postings.get(token)
            if not posting:
                continue
            idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
            for i, tf in posting:
                norm = K1 * (1 - B + B * self.lengths[i] / self.average)
                gain = idf * tf * (K1 + 1) / (tf + norm)
                scores[i] = scores.get(i, 0.0) + weight * gain
        ranked = sorted(scores.items(), key=lambda item: -item[1])[:k]
        return [(self.symbols[i], score) for i, score in ranked]

    def by_name(self, name: str) -> list[Symbol]:
        """Return functions whose name matches `name` ignoring case."""
        wanted = name.lower()
        return [s for s in self.symbols if s.name.lower() == wanted]
