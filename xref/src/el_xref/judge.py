"""
Ask Claude which retrieved client functions implement a spec function.

Calls go through the `claude` CLI in print mode, so the same code runs on
a Claude Code login locally and on `ANTHROPIC_API_KEY` in CI.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field

from .config import Json
from .index import Symbol

LANGS = {
    "go": "Go",
    "rust": "Rust",
    "csharp": "C#",
    "java": "Java",
    "nim": "Nim",
    "python": "Python",
}

FENCES = {"csharp": "cs", "nim": "nim", "go": "go", "rust": "rust"}

SYSTEM = """\
You map functions from the Ethereum execution layer specification (EELS, \
written in Python) to the code that implements the same behaviour in one \
execution client.

You get one spec function and numbered candidate functions from the client. \
The candidates come from keyword search, so most of them are wrong. Pick the \
ones that implement the spec function's behaviour:

- "equivalent": the candidate's job is the spec function's job. Names, \
signatures and structure may differ.
- "partial": the candidate implements a recognisable part of the spec \
function, or the spec logic is one clearly identifiable step inside a larger \
candidate.

Prefer the function that does the work over interface or trait declarations, \
thin wrappers, tracing, tests and bytecode builders. Several matches are fine \
when the client splits the logic, for example dynamic gas and execution of \
one opcode. Return no match rather than a weak guess. Keep each note to one \
sentence naming the evidence.

When no candidate matches with at least medium confidence, give up to three \
`search_hints`: function names or distinctive identifiers you expect this \
client to use for the logic. Otherwise leave `search_hints` empty.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "integer"},
                    "relation": {"enum": ["equivalent", "partial"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["candidate", "relation", "confidence", "note"],
                "additionalProperties": False,
            },
        },
        "search_hints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["matches", "search_hints"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    """The judge's answer for one spec function and one client."""

    matches: list[Json] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    cost: float = 0.0


def excerpt(body: str, limit: int, lang: str) -> str:
    """Return at most `limit` lines of `body`, noting what was cut."""
    lines = body.splitlines()
    if len(lines) <= limit:
        return body
    comment = "#" if lang in {"python", "nim"} else "//"
    cut = len(lines) - limit
    return "\n".join(lines[:limit] + [f"{comment} ... {cut} more lines"])


def render(
    client: str,
    lang: str,
    spec: Symbol,
    spec_body: str,
    candidates: list[tuple[Symbol, str]],
    long_lines: int,
    short_lines: int,
    long_count: int,
) -> str:
    """Build the user prompt."""
    fence = FENCES.get(lang, lang)
    parts = [
        f"Client: {client} ({LANGS.get(lang, lang)})",
        "",
        f"Spec function `{spec.id}` ({spec.path}:{spec.line}):",
        "```python",
        spec_body,
        "```",
        "",
        "Candidates:",
    ]
    for i, (symbol, body) in enumerate(candidates):
        limit = long_lines if i < long_count else short_lines
        parts += [
            "",
            f"[{i}] {symbol.qualname} - {symbol.repo}/{symbol.path}:"
            f"{symbol.line} ({symbol.lines} lines)",
            f"```{fence}",
            excerpt(body, limit, lang),
            "```",
        ]
    return "\n".join(parts)


def ask(prompt: str, model: str, effort: str, timeout: int = 600) -> Verdict:
    """Run one judge call through the `claude` CLI."""
    command = [
        "claude",
        "-p",
        "--model",
        model,
        "--effort",
        effort,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--system-prompt",
        SYSTEM,
        "--json-schema",
        json.dumps(SCHEMA),
    ]
    if os.environ.get("ANTHROPIC_API_KEY"):
        command.append("--bare")
    result = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"claude exited {result.returncode}: {result.stderr[-500:]}"
        ) from error
    if reply.get("is_error") or "structured_output" not in reply:
        raise RuntimeError(f"claude error: {str(reply.get('result'))[:500]}")
    out = reply["structured_output"]
    return Verdict(
        matches=out.get("matches", []),
        hints=out.get("search_hints", []),
        cost=float(reply.get("total_cost_usd") or 0.0),
    )
