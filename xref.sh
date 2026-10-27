#!/usr/bin/env bash
# Where each execution client implements an EELS spec function. Runs the
# xref/ project with uv, see xref/README.md or ./xref.sh --help.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec uv run --quiet --project "$here/xref" el-xref "$@"
