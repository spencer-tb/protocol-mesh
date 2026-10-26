#!/usr/bin/env bash
# Set up everything needed to work on EIPs across the spec, the tests and
# the execution clients, on macOS or Linux. Run it again any time to update.
#
#   ./setup.sh             clone or update the repos, install any missing
#                          toolchains, link the shared commands and build
#                          every client binary whose code changed
#   ./setup.sh geth besu   the same, building only the targets named
#   ./setup.sh --check     only show what is installed, missing or too old
#
# Targets: eels hive clive geth erigon besu nethermind evmone ethrex nimbus
# ethereumjs. Repos go in ~/ethereum and binaries in ~/ethereum/bin, or set
# WS_ROOT and WS_BIN.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WS_ROOT=${WS_ROOT:-$HOME/ethereum}
WS_BIN=${WS_BIN:-$WS_ROOT/bin}
LOCAL=$HOME/.local
STATE=$HOME/.config/protocol-mesh
JOBS=$(nproc 2>/dev/null || sysctl -n hw.ncpu)

GO_VERSION=1.25.7
RUST_MIN=1.93
DOTNET_MIN=10.0
JDK_MAJOR=21
UV_MIN=0.13.4
NODE_MAJOR=22
JQ_VERSION=1.7.1

# System packages the builds need, which take sudo or brew.
APT_PACKAGES=(build-essential clang libclang-dev libsnappy-dev librocksdb-dev)
BREW_PACKAGES=(rocksdb snappy)

TARGETS=(eels hive clive geth erigon besu nethermind evmone ethrex nimbus
  ethereumjs)

# Toolchain directories, in lookup order.
PATHS=("$LOCAL/bin" "$LOCAL/go/bin" "$HOME/go/bin" "$HOME/.cargo/bin"
  "$LOCAL/node/bin" "$HOME/.dotnet" "$LOCAL/jdk/bin")

case $(uname -s) in
  Linux) OS=linux ;;
  Darwin) OS=darwin ;;
  *) OS=unknown ;;
esac
case $(uname -m) in
  x86_64 | amd64) ARCH=amd64 ;;
  aarch64 | arm64) ARCH=arm64 ;;
  *) ARCH=unknown ;;
esac

say() { printf '%s\n' "$*" >&2; }
die() { say "setup: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# True when version $1 is at least $2.
version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

# Print the first version number a command reports.
version_of() {
  "$@" 2>&1 | grep -oE '[0-9]+(\.[0-9]+)+' | head -n1 || true
}

# Print the toolchain environment for sh or fish.
env_lines() {
  local joined dotnet='' jdk=''
  joined=$(IFS=:; echo "${PATHS[*]}")
  if [ -x "$HOME/.dotnet/dotnet" ]; then dotnet=$HOME/.dotnet; fi
  if [ -x "$LOCAL/jdk/bin/java" ]; then jdk=$LOCAL/jdk; fi
  if [ "${1:-sh}" = fish ]; then
    echo "set -gx PATH ${PATHS[*]} \$PATH"
    if [ -n "$dotnet" ]; then echo "set -gx DOTNET_ROOT $dotnet"; fi
    if [ -n "$jdk" ]; then echo "set -gx JAVA_HOME $jdk"; fi
  else
    echo "export PATH=\"$joined:\$PATH\""
    if [ -n "$dotnet" ]; then echo "export DOTNET_ROOT=\"$dotnet\""; fi
    if [ -n "$jdk" ]; then echo "export JAVA_HOME=\"$jdk\""; fi
  fi
}

# Pull this repo when it is clean, then rerun the new version once.
self_update() {
  if [ -n "${SETUP_UPDATED:-}" ] ||
    ! git -C "$HERE" rev-parse --git-dir >/dev/null 2>&1 ||
    [ -n "$(git -C "$HERE" status --porcelain)" ] ||
    ! git -C "$HERE" rev-parse -q --verify '@{u}' >/dev/null; then
    return 0
  fi
  local before
  before=$(git -C "$HERE" rev-parse HEAD)
  git -C "$HERE" pull -q --ff-only || return 0
  if [ "$(git -C "$HERE" rev-parse HEAD)" != "$before" ]; then
    export SETUP_UPDATED=1
    exec "$HERE/setup.sh" "$@"
  fi
}

# Print "dir url branch" for every entry in repos.txt.
manifest() {
  local dir url branch
  while read -r dir url branch; do
    case $dir in '' | '#'*) continue ;; esac
    echo "$dir $url ${branch:-}"
  done <"$HERE/repos.txt"
}

sync_repos() {
  local dir url branch path
  manifest | while read -r dir url branch; do
    path=$WS_ROOT/$dir
    if [ ! -d "$path/.git" ]; then
      say "clone $dir"
      git clone -q --filter=blob:none ${branch:+--branch "$branch"} \
        "$url" "$path" </dev/null || say "clone failed: $dir"
      continue
    fi
    if ! git -C "$path" fetch -q --all --prune </dev/null; then
      say "fetch failed: $dir"
      continue
    fi
    # Only fast-forward a clean branch, anything else may be someone's work.
    if [ -n "$(git -C "$path" status --porcelain --untracked-files=no)" ]; then
      say "local changes, fetched only: $dir"
    elif git -C "$path" rev-parse -q --verify '@{u}' >/dev/null; then
      git -C "$path" merge -q --ff-only '@{u}' 2>/dev/null ||
        say "diverged, fetched only: $dir"
    fi
  done
}

# Each installer leaves alone a tool that is already new enough.

install_uv() {
  if have uv && version_ge "$(version_of uv --version)" "$UV_MIN"; then
    return
  fi
  say "install uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
}

install_cmake() {
  if have cmake; then return; fi
  say "install cmake"
  uv tool install -q cmake
}

install_rust() {
  if have rustc && version_ge "$(version_of rustc --version)" "$RUST_MIN"; then
    return
  fi
  if have rustup; then
    say "update rust"
    rustup update stable >/dev/null
  else
    say "install rust"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
      sh -s -- -y --no-modify-path --profile minimal
  fi
}

install_go() {
  if have go && version_ge "$(version_of go env GOVERSION)" "$GO_VERSION"; then
    return
  fi
  say "install go $GO_VERSION"
  rm -rf "$LOCAL/go"
  curl -fsSL "https://go.dev/dl/go$GO_VERSION.$OS-$ARCH.tar.gz" |
    tar -xz -C "$LOCAL"
}

install_node() {
  if have node && version_ge "$(version_of node --version)" "$NODE_MAJOR"; then
    return
  fi
  local arch=x64 dist file
  if [ "$ARCH" = arm64 ]; then arch=arm64; fi
  dist=https://nodejs.org/dist/latest-v$NODE_MAJOR.x
  file=$(curl -fsSL "$dist/SHASUMS256.txt" |
    grep -oE "node-v[0-9.]+-$OS-$arch\.tar\.gz" | head -n1)
  say "install ${file%.tar.gz}"
  rm -rf "$LOCAL/node"
  mkdir -p "$LOCAL/node"
  curl -fsSL "$dist/$file" | tar -xz -C "$LOCAL/node" --strip-components=1
}

# Install the SDK nethermind's global.json asks for, or the latest 10.0.
install_dotnet() {
  local want=$DOTNET_MIN version=(--channel "$DOTNET_MIN")
  local json=$WS_ROOT/clients/nethermind/global.json
  if [ -f "$json" ]; then
    want=$(version_of grep '"version"' "$json")
    version=(--jsonfile "$json")
  fi
  if have dotnet && version_ge "$(version_of dotnet --version)" "$want"; then
    return
  fi
  say "install dotnet sdk $want"
  curl -fsSL https://dot.net/v1/dotnet-install.sh |
    bash -s -- --install-dir "$HOME/.dotnet" --no-path "${version[@]}" \
      >/dev/null
}

install_jdk() {
  if have java && version_ge "$(version_of java -version)" "$JDK_MAJOR"; then
    return
  fi
  local os=linux arch=x64 tmp
  if [ "$OS" = darwin ]; then os=mac; fi
  if [ "$ARCH" = arm64 ]; then arch=aarch64; fi
  say "install temurin jdk $JDK_MAJOR"
  tmp=$(mktemp -d)
  curl -fsSL "https://api.adoptium.net/v3/binary/latest/$JDK_MAJOR/ga/$os/$arch/jdk/hotspot/normal/eclipse" |
    tar -xz -C "$tmp" --strip-components=1
  rm -rf "$LOCAL/jdk"
  # The macOS archive keeps the JDK under Contents/Home.
  if [ -d "$tmp/Contents/Home" ]; then
    mv "$tmp/Contents/Home" "$LOCAL/jdk"
    rm -rf "$tmp"
  else
    mv "$tmp" "$LOCAL/jdk"
  fi
}

install_jq() {
  if have jq; then return; fi
  local os=$OS
  if [ "$OS" = darwin ]; then os=macos; fi
  say "install jq"
  curl -fsSL -o "$LOCAL/bin/jq" \
    "https://github.com/jqlang/jq/releases/download/jq-$JQ_VERSION/jq-$os-$ARCH"
  chmod +x "$LOCAL/bin/jq"
}

install_rg() {
  if have rg; then return; fi
  say "install ripgrep"
  cargo install -q --locked ripgrep
}

install_codex() {
  if have codex; then return; fi
  say "install codex"
  npm install -g -s --prefix "$LOCAL" @openai/codex
}

# Put the toolchains on PATH in new bash, zsh and fish shells.
install_env() {
  mkdir -p "$STATE"
  env_lines sh >"$STATE/env.sh"
  env_lines fish >"$STATE/env.fish"
  local shell rc
  for shell in bash zsh; do
    rc=$HOME/.${shell}rc
    if [ -f "$rc" ] || [ "${SHELL##*/}" = "$shell" ]; then
      if ! grep -qF "$STATE/env.sh" "$rc" 2>/dev/null; then
        printf '\n. "%s"\n' "$STATE/env.sh" >>"$rc"
      fi
    fi
  done
  if [ -d "$HOME/.config/fish" ]; then
    mkdir -p "$HOME/.config/fish/conf.d"
    ln -sf "$STATE/env.fish" "$HOME/.config/fish/conf.d/protocol-mesh.fish"
  fi
}

install_tools() {
  if [ "$OS" = unknown ] || [ "$ARCH" = unknown ]; then
    die "unsupported platform $(uname -sm)"
  fi
  mkdir -p "$LOCAL/bin"
  install_uv
  install_cmake
  install_rust
  install_go
  install_node
  install_dotnet
  install_jdk
  install_jq
  install_rg
  install_codex
  install_env
  eval "$(env_lines sh)"
}

# Point $2 at $1, leaving any real file or directory there alone.
link() {
  mkdir -p "$(dirname "$2")"
  if [ -e "$2" ] && [ ! -L "$2" ]; then
    say "kept, not a link: $2"
  else
    ln -sfn "$1" "$2"
  fi
}

link_commands() {
  local f
  for f in "$HERE"/commands/*.md; do
    link "$f" "$HOME/.claude/commands/${f##*/}"
    link "$f" "$HOME/.codex/prompts/${f##*/}"
  done
  # Every Claude session learns where things are and how to build them.
  local import="@$HERE/AGENTS.md"
  if ! grep -qxF "$import" "$HOME/.claude/CLAUDE.md" 2>/dev/null; then
    mkdir -p "$HOME/.claude"
    printf '\n%s\n' "$import" >>"$HOME/.claude/CLAUDE.md"
  fi
}

# Source checkout of each build target, under $WS_ROOT.
target_src() {
  case $1 in
    eels) echo execution-specs ;;
    hive) echo hive ;;
    clive) echo clive ;;
    geth) echo clients/go-ethereum ;;
    erigon) echo clients/erigon ;;
    besu) echo clients/besu ;;
    nethermind) echo clients/nethermind ;;
    evmone) echo clients/evmone ;;
    ethrex) echo clients/ethrex ;;
    nimbus) echo clients/nimbus-eth1 ;;
    ethereumjs) echo clients/ethereumjs-monorepo ;;
    *) return 1 ;;
  esac
}

# Make and print the directory a target's binaries go in.
out() {
  mkdir -p "$WS_BIN/$1"
  echo "$WS_BIN/$1"
}

# Each build runs in its checkout.
build_eels() { uv sync -q; }
build_hive() { go build -o hive .; }
build_clive() { go build -o "$LOCAL/bin/clive" ./cmd/hive-cli; }
build_geth() { go build -o "$(out geth)/evm" ./cmd/evm; }

build_erigon() {
  make evm.cmd
  ln -sf "$PWD/build/bin/evm" "$(out erigon)/evm"
}

build_besu() {
  ./gradlew --no-daemon -q :ethereum:evmtool:installDist
  ln -sf "$PWD/ethereum/evmtool/build/install/evmtool/bin/evmtool" \
    "$(out besu)/evmtool"
}

build_nethermind() {
  dotnet publish src/Nethermind/Nethermind.Test.Runner -c Release \
    --nologo -v q -o "$(out nethermind)"
}

build_evmone() {
  git submodule update -q --init --recursive
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DEVMONE_TESTING=ON \
    >/dev/null
  cmake --build build --parallel "$JOBS"
  local tool
  for tool in evmone evmone-t8n evmone-statetest evmone-blockchaintest; do
    if [ -x "build/bin/$tool" ]; then
      ln -sf "$PWD/build/bin/$tool" "$(out evmone)/$tool"
    fi
  done
}

build_ethrex() {
  cargo build -q --release --manifest-path tooling/Cargo.toml -p ethrex-evm
  ln -sf "$PWD/tooling/target/release/ethrex-evm" "$(out ethrex)/ethrex-evm"
}

build_nimbus() {
  make -j"$JOBS" update
  make -j"$JOBS" t8n
  ln -sf "$PWD/tools/t8n/t8n" "$(out nimbus)/t8n"
}

build_ethereumjs() {
  npm ci -s --no-audit --no-fund
  # The script finds tsx and its sources from inside the checkout.
  printf '#!/bin/sh\ncd "%s" && exec ./packages/vm/test/t8n/ethereumjs-t8ntool.sh "$@"\n' \
    "$PWD" >"$(out ethereumjs)/ethereumjs-t8ntool.sh"
  chmod +x "$WS_BIN/ethereumjs/ethereumjs-t8ntool.sh"
}

# Build one target, unless its checkout is clean and still at the commit
# it was last built from.
build_target() {
  local t=$1 dir head stamp
  dir=$(target_src "$t")
  [ -d "$WS_ROOT/$dir/.git" ] || die "$dir is not cloned"
  cd "$WS_ROOT/$dir"
  head=$(git rev-parse HEAD)
  stamp=$STATE/built/$t
  if [ -z "$(git status --porcelain --untracked-files=no)" ] &&
    [ "$(cat "$stamp" 2>/dev/null)" = "$head" ]; then
    return
  fi
  say "build $t at ${head:0:10}"
  "build_$t"
  mkdir -p "$STATE/built"
  echo "$head" >"$stamp"
}

# Build each target in a subshell, so one failure stops only that target.
build_all() {
  local t rc failed=()
  for t in "$@"; do
    set +e
    (set -e && build_target "$t")
    rc=$?
    set -e
    if [ $rc -ne 0 ]; then failed+=("$t"); fi
  done
  if [ ${#failed[@]} -gt 0 ]; then say "build failed: ${failed[*]}"; fi
}

# Print one status row, flagging a tool that is missing or older than $3.
row() {
  local state=ok
  if [ -z "$2" ]; then
    state=MISSING
  elif [ -n "${3:-}" ] && ! version_ge "$2" "$3"; then
    state=OLD
  fi
  printf '  %-12s %-8s %-14s %s\n' "$1" "$state" "${2:--}" "${3:+need $3}"
}

# Print a tool's version, or nothing when it is not installed.
tool_version() {
  if have "$1"; then version_of "$@"; fi
}

check() {
  echo "tools"
  row uv "$(tool_version uv --version)" "$UV_MIN"
  row go "$(tool_version go env GOVERSION)" "$GO_VERSION"
  row rustc "$(tool_version rustc --version)" "$RUST_MIN"
  row node "$(tool_version node --version)" "$NODE_MAJOR"
  row dotnet "$(tool_version dotnet --version)" "$DOTNET_MIN"
  row java "$(tool_version java -version)" "$JDK_MAJOR"
  local t
  for t in git curl make cmake gcc docker jq rg gh claude codex; do
    row "$t" "$(tool_version "$t" --version)"
  done
  if have docker && ! docker info >/dev/null 2>&1; then
    echo "  docker is installed but not running, hive needs it"
  fi

  echo "repos in $WS_ROOT"
  local dir url branch missing=()
  while read -r dir url branch; do
    if [ ! -d "$WS_ROOT/$dir/.git" ]; then missing+=("$dir"); fi
  done < <(manifest)
  if [ ${#missing[@]} -eq 0 ]; then
    echo "  all $(manifest | wc -l | tr -d ' ') cloned"
  else
    echo "  missing ${missing[*]}"
  fi

  echo "builds in $WS_BIN"
  local built
  for t in "${TARGETS[@]}"; do
    built=$(head -c 10 "$STATE/built/$t" 2>/dev/null || true)
    printf '  %-12s %s\n' "$t" "${built:-not built}"
  done

  local pkg need=()
  if have dpkg-query; then
    for pkg in "${APT_PACKAGES[@]}"; do
      if [[ "$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null)" != *"ok installed"* ]]; then
        need+=("$pkg")
      fi
    done
    if [ ${#need[@]} -gt 0 ]; then
      echo "system packages, install once with"
      echo "  sudo apt-get install -y ${need[*]}"
    fi
  elif have brew; then
    for pkg in "${BREW_PACKAGES[@]}"; do
      brew list --versions "$pkg" >/dev/null 2>&1 || need+=("$pkg")
    done
    if [ ${#need[@]} -gt 0 ]; then
      echo "system packages, install once with"
      echo "  brew install ${need[*]}"
    fi
  else
    echo "system packages: install a C compiler, clang, snappy and rocksdb"
  fi
  if [ "$OS" = darwin ] && ! xcode-select -p >/dev/null 2>&1; then
    echo "  xcode-select --install"
  fi
}

main() {
  case ${1:-} in
    -h | --help)
      sed -n '2,/^set -e/p' "$HERE/setup.sh" | sed '$d; s/^# \{0,1\}//'
      return
      ;;
    --check)
      check
      return
      ;;
  esac
  local t
  for t in "$@"; do
    target_src "$t" >/dev/null || die "unknown target $t, see --help"
  done
  if [ $# -eq 0 ]; then set -- "${TARGETS[@]}"; fi
  self_update "$@"
  for t in git curl tar; do
    have "$t" || die "install $t first"
  done
  sync_repos
  install_tools
  link_commands
  build_all "$@"
  check
}

eval "$(env_lines sh)"
main "$@"
