#!/usr/bin/env bash
# recomp_linux.sh - From the .xci/.nsp to the Windows exe on Linux: the same recomp_tsukihime.ps1, with PowerShell 7.
# The exe is cross-compiled (clang-cl + xwin) and played with Proton. Before: tools/linux/install_requirements_linux.sh.
#
#   bash tools/linux/recomp_linux.sh -Edition global
#   bash tools/linux/recomp_linux.sh -Edition japan -From 6
#   (the same options as recomp_tsukihime.ps1)
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[ "$(uname -s)" = Linux ] || { echo "ERROR: this is for Linux (on Windows: tools\\recomp_tsukihime.ps1)"; exit 1; }
pwsh_ok || { echo "ERROR: PowerShell $PWSH_VER is missing in $PWSH_DIR: run tools/linux/install_requirements_linux.sh first"; exit 1; }
run_ps1 "$ROOT/tools/recomp_tsukihime.ps1" "$@"
