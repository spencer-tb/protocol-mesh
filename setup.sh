#!/usr/bin/env bash
# Install uv if it is missing, pull this repo, then run bootstrap.py with
# uv, which brings its own Python. Arguments pass through, see --help.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "install uv" >&2
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi

# Run the newest setup when this checkout is clean.
if [ -z "$(git -C "$here" status --porcelain 2>/dev/null)" ]; then
  GIT_TERMINAL_PROMPT=0 git -C "$here" pull -q --ff-only 2>/dev/null || true
fi

exec uv run --quiet --script "$here/bootstrap.py" "$@"
