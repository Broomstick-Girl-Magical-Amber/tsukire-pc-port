#!/usr/bin/env bash
# phone_data.sh - Puts together the port folder for the phone (tools/android/phone_data.ps1).
# Linux: the same options as the .ps1, e.g.  bash tools/android/phone_data.sh -Edition global
# PowerShell 7 is the pinned one of tools/linux (installed in NX_TOOLS_DIR the first time if missing).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../linux" && pwd)/common.sh"
[ "$(uname -s)" = Linux ] || { echo "ERROR: this is for Linux (on Windows: tools\\android\\phone_data.ps1)"; exit 1; }
install_pwsh
run_ps1 "$ROOT/tools/android/phone_data.ps1" "$@"
