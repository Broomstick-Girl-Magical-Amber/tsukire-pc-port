#!/usr/bin/env bash
# android_deps.sh - Builds the Android libraries (FFmpeg, opus, libjpeg-turbo, libpng, zlib) with vcpkg (tools/android/android_deps.ps1).
# Linux: the same options as the .ps1, e.g.  bash tools/android/android_deps.sh
# PowerShell 7 is the pinned one of tools/linux (installed in NX_TOOLS_DIR the first time if missing).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../linux" && pwd)/common.sh"
[ "$(uname -s)" = Linux ] || { echo "ERROR: this is for Linux (on Windows: tools\\android\\android_deps.ps1)"; exit 1; }
install_pwsh
run_ps1 "$ROOT/tools/android/android_deps.ps1" "$@"
