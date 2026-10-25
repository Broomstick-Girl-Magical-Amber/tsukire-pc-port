#!/usr/bin/env bash
# install_requirements_linux.sh - Gets a Linux PC ready to recompile Tsukihime: the same install_requirements.ps1 of
# Windows, with PowerShell 7 (downloaded and verified the first time) and the Linux tools
# (tools/linux/install_requirements_linux.ps1).
#
#   bash tools/linux/install_requirements_linux.sh                    # everything (steps 0..11)
#   bash tools/linux/install_requirements_linux.sh -Check             # only checks: installs, downloads and creates nothing
#   bash tools/linux/install_requirements_linux.sh -From 5            # resume
#   bash tools/linux/install_requirements_linux.sh -Edition global    # options of install_requirements.ps1, same spelling
#
# The only thing that may ask for sudo is step 1 (system programs), and it asks first. You accept the Microsoft license
# of the Windows CRT/SDK (xwin, step 2) when xwin asks. Log in <root>/_prepare/.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[ "$(uname -s)" = Linux ] || { echo "ERROR: this is for Linux (on Windows: tools\\install_requirements.ps1)"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "ERROR: x86_64 is needed (this is $(uname -m))"; exit 1; }
check=0; for a in "$@"; do case "${a,,}" in -check) check=1 ;; esac; done
if [ "$check" = 1 ] && ! pwsh_ok; then
    echo "MISSING: PowerShell $PWSH_VER in $PWSH_DIR (without -Check it installs itself). Cannot check the rest without it."
    exit 1
fi
install_pwsh
run_ps1 "$ROOT/tools/install_requirements.ps1" "$@"
