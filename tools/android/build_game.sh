#!/usr/bin/env bash
# build_game.sh - Builds the game's library for Android without the NDK (tools/android/build_game.ps1, with PowerShell 7).
# Linux: the same options as the .ps1, e.g.  bash tools/android/build_game.sh -Edition global
# PowerShell 7 is the pinned one of tools/linux (installed in NX_TOOLS_DIR the first time if missing).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../linux" && pwd)/common.sh"
[ "$(uname -s)" = Linux ] || { echo "ERROR: this is for Linux (on Windows: tools\\android\\build_game.ps1)"; exit 1; }
install_pwsh
run_ps1 "$ROOT/tools/android/build_game.ps1" "$@"
