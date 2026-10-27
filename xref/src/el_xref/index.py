"""
Extract function-level symbols from the spec and from client repos.

The spec is parsed with `ast`, clients with tree-sitter. Both produce
`Symbol` records written as JSON lines under `data/index/`.
"""

import ast
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from .config import Config, Repo
from .tokens import eips

EXTENSIONS = {
    "go": (".go",),
    "rust": (".rs",),
    "csharp": (".cs",),
    "java": (".java",),
    "nim": (".nim",),
    "cpp": (".cpp", ".hpp"),
    "python": (".py",),
}

FUNCTIONS = {
    "go": {"function_declaration", "method_declaration"},
    "rust": {"function_item"},
    "csharp": {
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "operator_declaration",
    },
    "java": {"method_declaration", "constructor_declaration"},
    "nim": {"routine"},
    "cpp": {"function_definition"},
}

CONTAINERS = {
    "rust": {"impl_item", "trait_item"},
    "csharp": {
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "interface_declaration",
    },
    "java": {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    },
    "cpp": {"class_specifier", "struct_specifier"},
}

CALL = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:::)?(?:<[\w\s,<>:&']*>)?\s*\("
)
NOT_CALLS = frozenset(
    """
    if for while switch return func fn match catch sizeof typeof nameof
    default new and or not in case when using lock foreach elif proc
    """.split()
)


@dataclass
class Symbol:
    """One function or method, located by repo, path and line range."""

    id: str
    repo: str
    path: str
    qualname: str
    name: str
    line: int
    end_line: int
    lang: str
    hash: str
    calls: list[str] = field(default_factory=list)
    eips: list[str] = field(default_factory=list)
    doc: str = ""
    tags: list[str] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)

    @property
    def lines(self) -> int:
        """Return the number of source lines."""
        return self.end_line - self.line + 1


def digest(text: str) -> str:
    """Return a short content hash."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def calls_in(text: str) -> list[str]:
    """Return the names called in `text`, in first-seen order."""
    seen: dict[str, None] = {}
    for name in CALL.findall(text):
        if name not in NOT_CALLS:
            seen.setdefault(name, None)
    return list(seen)


def source_files(root: Path, repo: Repo) -> Iterator[tuple[Path, str]]:
    """Yield files of `repo.lang` under the sparse paths, minus excludes."""
    suffixes = EXTENSIONS[repo.lang]
    for top in repo.paths:
        base = root / top
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in files:
            rel = path.relative_to(root).as_posix()
            if path.suffix not in suffixes or not path.is_file():
                continue
            if any(fnmatch(rel, pattern) for pattern in repo.exclude):
                continue
            yield path, rel


# Spec


def spec_module(rel: Path) -> str:
    """Return the dotted module path of a file relative to the fork dir."""
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def import_aliases(tree: ast.Module, package: str) -> dict[str, str]:
    """Map names bound by relative imports in `package` to spec paths."""
    aliases = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = package.split(".") if package else []
        if node.level > 1:
            base = base[: len(base) - (node.level - 1)]
        if node.module:
            base = base + node.module.split(".")
        for alias in node.names:
            aliases[alias.asname or alias.name] = ".".join(base + [alias.name])
    return aliases


def spec_tags(fork_dir: Path) -> dict[str, list[str]]:
    """Tag spec functions with the opcode or precompile they implement."""
    tags: dict[str, list[str]] = {}

    instructions = fork_dir / "vm" / "instructions" / "__init__.py"
    tree = ast.parse(instructions.read_text())
    aliases = import_aliases(tree, "vm.instructions")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Attribute)
                and isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
            ):
                module = aliases.get(value.value.id, value.value.id)
                fid = f"{module}.{value.attr}"
                tags.setdefault(fid, []).append(f"opcode:{key.attr}")

    mapping = fork_dir / "vm" / "precompiled_contracts" / "mapping.py"
    tree = ast.parse(mapping.read_text())
    aliases = import_aliases(tree, "vm.precompiled_contracts")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Name) and isinstance(value, ast.Name):
                fid = aliases.get(value.id, value.id)
                name = key.id.removesuffix("_ADDRESS")
                tags.setdefault(fid, []).append(f"precompile:{name}")
    return tags


def parse_spec(config: Config) -> list[Symbol]:
    """Extract every function and method of the anchor fork."""
    repo = config.spec.repo
    root = config.repo_path(repo)
    fork_dir = root / repo.paths[0]
    tags = spec_tags(fork_dir)
    symbols: list[Symbol] = []
    for path, rel in source_files(root, repo):
        module = spec_module(path.relative_to(fork_dir))
        text = path.read_text()
        tree = ast.parse(text)
        lines = text.splitlines()
        for node, owner in walk_python(tree):
            qualname = f"{owner}.{node.name}" if owner else node.name
            sid = f"{module}.{qualname}" if module else qualname
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            doc = ast.get_docstring(node) or ""
            symbols.append(
                Symbol(
                    id=sid,
                    repo=repo.name,
                    path=rel,
                    qualname=qualname,
                    name=node.name,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    lang="python",
                    hash=digest(body),
                    calls=calls_in(body),
                    eips=sorted(eips(body)),
                    doc=doc,
                    tags=tags.get(sid, []),
                    constants=sorted(
                        {
                            n.id
                            for n in ast.walk(node)
                            if isinstance(n, ast.Name)
                            and n.id.isupper()
                            and len(n.id) > 2
                        }
                    ),
                )
            )
    return symbols


def walk_python(
    tree: ast.Module,
) -> Iterator[tuple[ast.FunctionDef, str]]:
    """Yield top-level functions and methods with their class name."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield node, ""
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    yield item, node.name


# Clients


def cpp_declared(node: Node) -> str:
    """Return a C++ function definition's name, `Type::name` when qualified."""
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None:
        return ""
    name = declarator.child_by_field_name("declarator")
    return text_of(name) if name else ""


def node_name(node: Node, lang: str) -> str:
    """Return the declared name of a function or container node."""
    if lang == "cpp" and node.type == "function_definition":
        return cpp_declared(node).split("::")[-1]
    if lang == "nim":
        for child in node.named_children:
            if child.type == "symbol":
                return text_of(child).strip("`")
        return ""
    if node.type == "impl_item":
        target = node.child_by_field_name("type")
        return base_type(target) if target else ""
    name = node.child_by_field_name("name")
    return text_of(name) if name else ""


def base_type(node: Node) -> str:
    """Return the outermost type identifier, without generics or `*`."""
    if node.type in {"type_identifier", "identifier"}:
        return text_of(node)
    for child in node.named_children:
        found = base_type(child)
        if found:
            return found
    return ""


def go_receiver(node: Node) -> str:
    """Return the receiver type of a Go method declaration."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return ""
    for param in receiver.named_children:
        kind = param.child_by_field_name("type")
        if kind is not None:
            return base_type(kind)
    return ""


def text_of(node: Node) -> str:
    """Decode the source text of `node`."""
    return node.text.decode(errors="replace") if node.text else ""


def leading_comments(node: Node) -> str:
    """Return comments directly above `node`."""
    out = []
    prev = node.prev_sibling
    line = node.start_point[0]
    while prev is not None and "comment" in prev.type:
        if prev.end_point[0] < line - 1:
            break
        out.append(text_of(prev))
        line = prev.start_point[0]
        prev = prev.prev_sibling
    return "\n".join(reversed(out))


def walk_functions(
    node: Node, lang: str, owners: tuple[str, ...] = ()
) -> Iterator[tuple[Node, tuple[str, ...]]]:
    """Yield function nodes with the names of their enclosing types."""
    for child in node.children:
        if child.type in FUNCTIONS[lang]:
            yield child, owners
            # Nested functions (Go closures, C# local functions) are kept
            # with their parent. Nim nests routines inside templates.
            if lang in {"csharp", "nim"}:
                yield from walk_functions(
                    child, lang, owners + (node_name(child, lang),)
                )
        elif child.type in CONTAINERS.get(lang, ()):
            yield from walk_functions(
                child, lang, owners + (node_name(child, lang),)
            )
        else:
            yield from walk_functions(child, lang, owners)


def parse_source(
    lang: str, repo: str, rel: str, source: bytes
) -> list[tuple[Symbol, str]]:
    """Extract the functions of one client file with their bodies."""
    tree = get_parser(lang).parse(source)
    out = []
    for node, owners in walk_functions(tree.root_node, lang):
        name = node_name(node, lang)
        if not name:
            continue
        if lang == "go" and node.type == "method_declaration":
            owners = (go_receiver(node),)
        if lang == "cpp" and "::" in cpp_declared(node):
            # A method defined outside its class, such as `Host::call`.
            owners = owners + tuple(cpp_declared(node).split("::")[:-1])
        qualname = ".".join(o for o in (*owners, name) if o)
        body = text_of(node)
        doc = leading_comments(node)
        line = node.start_point[0] + 1
        symbol = Symbol(
            id=f"{repo}:{rel}:{qualname}:{line}",
            repo=repo,
            path=rel,
            qualname=qualname,
            name=name,
            line=line,
            end_line=node.end_point[0] + 1,
            lang=lang,
            hash=digest(body),
            calls=calls_in(body),
            eips=sorted(eips(doc + "\n" + body)),
            doc=doc,
        )
        out.append((symbol, body))
    return out


def parse_repo(config: Config, repo: Repo) -> list[Symbol]:
    """Extract every function and method of one client repo."""
    root = config.repo_path(repo)
    symbols: list[Symbol] = []
    for path, rel in source_files(root, repo):
        parsed = parse_source(repo.lang, repo.name, rel, path.read_bytes())
        symbols.extend(symbol for symbol, _ in parsed)
    return symbols


# Storage


def index_path(config: Config, repo: Repo) -> Path:
    """Return the JSON-lines index file of `repo`."""
    return config.data / "index" / f"{repo.name}.jsonl"


def save(path: Path, symbols: list[Symbol]) -> None:
    """Write `symbols` as JSON lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as out:
        for symbol in symbols:
            out.write(json.dumps(asdict(symbol)) + "\n")


def load(path: Path) -> list[Symbol]:
    """Read symbols written by `save`."""
    with path.open() as f:
        return [Symbol(**json.loads(line)) for line in f]


def build(config: Config, only: set[str] | None = None) -> None:
    """Index the spec and every client repo."""
    spec = config.spec.repo
    if not only or spec.name in only:
        symbols = parse_spec(config)
        save(index_path(config, spec), symbols)
        print(f"{spec.name:<16} {len(symbols):>6} symbols")
    for client in config.clients.values():
        for repo in client.repos:
            if only and repo.name not in only:
                continue
            symbols = parse_repo(config, repo)
            save(index_path(config, repo), symbols)
            print(f"{repo.name:<16} {len(symbols):>6} symbols")


def read_body(config: Config, symbol: Symbol) -> str:
    """Return the source lines of `symbol` from its checkout."""
    repo_root = config.repo_cache / symbol.repo
    lines = (repo_root / symbol.path).read_text(errors="replace").splitlines()
    return "\n".join(lines[symbol.line - 1 : symbol.end_line])
