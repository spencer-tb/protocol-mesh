#!/usr/bin/env bash
# Install uv if it is missing, then run bootstrap.py with uv, which brings
# its own Python. Arguments pass through, see --help.
#
# This checkout never updates itself, since one bad commit would then run
# on every machine. `./setup.sh --update` shows what changed and asks first.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Show the incoming commits, with the full diff of everything that runs or
# instructs agents, and fast-forward only after a yes.
update() {
  git -C "$here" fetch -q
  if [ -z "$(git -C "$here" log --oneline 'HEAD..@{u}')" ]; then
    echo "already up to date"
    return
  fi
  git -C "$here" --no-pager log --format='%h %an %s' 'HEAD..@{u}'
  git -C "$here" --no-pager diff --stat 'HEAD...@{u}'
  git -C "$here" --no-pager diff 'HEAD...@{u}' -- setup.sh bootstrap.py \
    pyproject.toml uv.lock repos.txt AGENTS.md .agents
  if [ "${1:-}" != --yes ]; then
    if [ ! -t 0 ]; then
      echo "review the changes above, then run: ./setup.sh --update --yes" >&2
      return 1
    fi
    read -r -p "Apply these commits? [y/N] " answer
    case $answer in
      y | Y | yes) ;;
      *) echo "not updated" >&2; return 1 ;;
    esac
  fi
  git -C "$here" merge -q --ff-only '@{u}'
  echo "updated, run ./setup.sh to apply it"
}

if [ "${1:-}" = --update ]; then
  shift
  update "$@"
  exit
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "install uv" >&2
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi

exec uv run --quiet --script "$here/bootstrap.py" "$@"
