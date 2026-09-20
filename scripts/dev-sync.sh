#!/usr/bin/env bash
# Copy this repo into the Omarchy shell's plugin directory as a real folder.
# (The shell treats a symlinked plugin folder differently, so a symlink is not a faithful test.)
# Usage: scripts/dev-sync.sh          one-off copy
#        scripts/dev-sync.sh --watch  re-copy whenever a file changes (needs inotify-tools)
set -euo pipefail

src="$(cd "$(dirname "$0")/.." && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/io.github.gskrt.lapbar"

sync() {
  mkdir -p "$dest"
  rsync -a --delete \
    --exclude '.git/' --exclude '.env' --exclude 'tests/' --exclude 'scripts/' \
    --exclude '__pycache__/' --exclude '*.egg-info/' --exclude '.pytest_cache/' \
    "$src/" "$dest/"
}

sync
echo "synced to $dest"

if [[ "${1:-}" == "--watch" ]]; then
  while inotifywait -qr -e modify,create,delete,move --exclude '(\.git|__pycache__)' "$src" >/dev/null; do
    sync && echo "synced $(date +%H:%M:%S)"
  done
fi
