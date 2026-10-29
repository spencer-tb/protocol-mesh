set positional-arguments := true

alias help := list

# xref's own recipes: `just xref`, `just xref show <function>`, ...
mod xref

# List available recipes (default)
[default, private]
list:
    @just --list

# --- Static Analysis ---

# Auto-fix formatting and lint issues
[group('static analysis')]
fix:
    uv run ruff format
    uv run ruff check --fix
    gofmt -w clive

# Run all static checks (lint, format, mypy, go vet, lock)
[group('static analysis'), parallel]
static: lint format-check typecheck go-vet lock-check

# Lint Python with ruff
[group('static analysis')]
lint *args:
    uv run ruff check "$@"

# Check Python and Go formatting
[group('static analysis')]
format-check:
    uv run ruff format --check
    @test -z "$(gofmt -l clive)" || { gofmt -l clive; echo "run: just fix"; exit 1; }

# Type check Python with mypy
[group('static analysis')]
typecheck *args:
    uv run mypy "$@"

# Vet clive's Go code
[group('static analysis')]
go-vet:
    cd clive && go vet ./...

# Check that uv.lock matches pyproject.toml
[group('static analysis')]
lock-check:
    uv lock --check

# --- Tests ---

# Run the Python and Go tests
[group('tests')]
test: test-py test-go

# Run the Python tests, bootstrap's and xref's
[group('tests')]
test-py *args:
    uv run pytest "$@"

# Run clive's Go tests
[group('tests')]
test-go:
    cd clive && go test ./...

# --- Tools ---

# What changed since your last session: forks, decisions, EELS and hive
[group('tools')]
latest *args:
    uv run --quiet --script latest.py "$@"

# Set up this machine, see ./setup.sh --help
[group('tools')]
setup *args:
    ./setup.sh "$@"

# Fuzz EELS against geth, see fuzz/README.md
[group('tools')]
fuzz *args:
    uv run fuzz/run.py "$@"
