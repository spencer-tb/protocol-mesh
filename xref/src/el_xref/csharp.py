"""
Find C# methods by matching braces, for files tree-sitter cannot read.

The tree-sitter C# grammar lags the language. One construct it does not
know, such as `ref partial struct`, can turn a whole file into an error
node and lose every method in it. `scan` reads the same file without a
grammar: it blanks comments and strings, then follows the braces.
"""

import re
from dataclasses import dataclass

# Comments, preprocessor lines, strings and character literals.
NOISE = re.compile(
    r"""
      //[^\n]*
    | /\*.*?\*/
    | ^[ \t]*\#[^\n]*
    | \$*\"\"\".*?\"\"\"+
    | \$?@\$?"(?:[^"]|"")*"
    | \$?"(?:\\.|[^"\\\n])*"
    | '(?:\\.|[^'\\\n])+'
    """,
    re.S | re.M | re.X,
)
CONTAINER = re.compile(
    r"\b(?:record\s+(?:class\s+|struct\s+)?|class\s+|struct\s+|interface\s+)"
    r"(\w+)"
)
NAMESPACE = re.compile(r"\bnamespace\s")
CALLED = re.compile(r"([A-Za-z_]\w*)\s*(?:<[^()<>]*>)?\s*\(")
TAIL = re.compile(r"\s*(where\b.*|:\s*(base|this)\s*\(.*\))?\s*", re.S)
NOT_NAMES = frozenset(
    """
    public private protected internal static readonly unsafe ref out in
    params new delegate event operator return if for foreach while switch
    using lock fixed catch typeof nameof sizeof default when checked
    unchecked stackalloc await throw
    """.split()
)


@dataclass
class Method:
    """One method, by its enclosing types and its character span."""

    owners: tuple[str, ...]
    name: str
    start: int
    end: int


def blank(source: str) -> str:
    """Replace comments and strings with spaces, keeping every offset."""
    return NOISE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), source)


def closing(text: str, opened: int) -> int:
    """Return the offset of the bracket closing the one at `opened`."""
    depth = 0
    for at in range(opened, len(text)):
        if text[at] in "([":
            depth += 1
        elif text[at] in ")]":
            depth -= 1
            if depth == 0:
                return at
    return -1


def skip_attributes(header: str) -> int:
    """Return the offset in `header` after its leading `[...]` attributes."""
    at = 0
    while True:
        at += len(header[at:]) - len(header[at:].lstrip())
        if not header.startswith("[", at):
            return at
        end = closing(header, at)
        if end < 0:
            return at
        at = end + 1


def method_name(header: str) -> str:
    """Return the method name `header` declares, or "" if it declares none."""
    rest = header[skip_attributes(header) :]
    for match in CALLED.finditer(rest):
        before = rest[: match.start()]
        if before.count("(") != before.count(")"):
            continue
        if match.group(1) in NOT_NAMES:
            continue
        end = closing(rest, match.end() - 1)
        if end < 0 or "=" in before or not TAIL.fullmatch(rest[end + 1 :]):
            return ""
        return match.group(1)
    return ""


def arrow(header: str) -> int:
    """Return the offset of a `=>` outside brackets in `header`, or -1."""
    depth = 0
    for at, char in enumerate(header):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and header.startswith("=>", at):
            return at
    return -1


def scan(source: str) -> list[Method]:
    """
    Return the methods declared directly in the types of a C# file.

    Local functions stay part of their parent. A file whose braces do not
    balance returns nothing, since every span after the slip is wrong.
    """
    text = blank(source)
    methods: list[Method] = []
    # One frame per open brace: its kind, its name and where it started.
    frames: list[tuple[str, str, int]] = []
    since = 0
    for at, char in enumerate(text):
        if char not in "{};":
            continue
        header = text[since:at]
        start = since + len(header) - len(header.lstrip())
        inside = frames[-1][0] if frames else "namespace"
        if char == "{":
            container = CONTAINER.search(header)
            if container and inside in {"namespace", "type"}:
                frames.append(("type", container.group(1), start))
            elif NAMESPACE.search(header):
                frames.append(("namespace", "", start))
            elif inside == "type" and method_name(header):
                frames.append(("method", method_name(header), start))
            else:
                frames.append(("block", "", start))
        elif char == "}":
            if not frames:
                return []
            kind, name, opened = frames.pop()
            if kind == "method":
                owners = tuple(f[1] for f in frames if f[0] == "type")
                methods.append(Method(owners, name, opened, at + 1))
        elif inside == "type" and arrow(header) > 0:
            name = method_name(header[: arrow(header)])
            if name:
                owners = tuple(f[1] for f in frames if f[0] == "type")
                methods.append(Method(owners, name, start, at + 1))
        since = at + 1
    return [] if frames else methods
