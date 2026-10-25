# common.sh - Shared part of install_requirements_linux.sh and recomp_linux.sh: pinned PowerShell 7 (tarball with
# SHA-256) in NX_TOOLS_DIR (default ~/.local/tsuki-tools). Loaded with ". common.sh"; it does not run on its own.
PWSH_VER=7.6.7
PWSH_URL="https://github.com/PowerShell/PowerShell/releases/download/v$PWSH_VER/powershell-$PWSH_VER-linux-x64.tar.gz"
PWSH_SHA=6896f9e3a6b0a354bf470835a96da76d2f801cd50dcf047ad7c4563bf93bf431
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
T="${NX_TOOLS_DIR:-$HOME/.local/tsuki-tools}"
PWSH_DIR="$T/pwsh-$PWSH_VER"
PWSH="$PWSH_DIR/pwsh"

pwsh_ok() { [ -x "$PWSH" ] && [ -f "$PWSH_DIR/.sha256" ] && [ "$(cat "$PWSH_DIR/.sha256")" = "$PWSH_SHA" ]; }

# Installs PowerShell in $PWSH_DIR (all or nothing: .tmp and mv; never deletes what it did not create).
install_pwsh() {
    pwsh_ok && return 0
    [ -e "$PWSH_DIR" ] && { echo "ERROR: $PWSH_DIR exists and is not the verified PowerShell $PWSH_VER: set it aside by hand"; exit 1; }
    for c in curl tar sha256sum; do command -v "$c" >/dev/null 2>&1 || { echo "ERROR: '$c' is missing (Ubuntu/Debian: sudo apt install curl tar coreutils)"; exit 1; }; done
    mkdir -p "$T/downloads"
    local arch="$T/downloads/powershell-$PWSH_VER-linux-x64.tar.gz"
    if [ ! -f "$arch" ] || [ "$(sha256sum "$arch" | cut -d' ' -f1)" != "$PWSH_SHA" ]; then
        [ -e "$arch" ] && { echo "ERROR: $arch exists with another SHA-256: set it aside by hand"; exit 1; }
        echo "   downloading PowerShell $PWSH_VER ($PWSH_URL)"
        curl -fL --retry 3 --progress-bar -o "$arch.partial" "$PWSH_URL" || { echo "ERROR: could not download PowerShell"; exit 1; }
        local h; h="$(sha256sum "$arch.partial" | cut -d' ' -f1)"
        if [ "$h" != "$PWSH_SHA" ]; then mv "$arch.partial" "$arch.sha-mismatch"; echo "ERROR: PowerShell with SHA-256 $h, expected $PWSH_SHA"; exit 1; fi
        mv "$arch.partial" "$arch"
    fi
    [ -e "$PWSH_DIR.tmp" ] && { echo "ERROR: $PWSH_DIR.tmp exists (from an interrupted run): set it aside by hand"; exit 1; }
    mkdir -p "$PWSH_DIR.tmp"
    tar -xzf "$arch" -C "$PWSH_DIR.tmp"
    chmod +x "$PWSH_DIR.tmp/pwsh"
    echo "$PWSH_SHA" > "$PWSH_DIR.tmp/.sha256"
    mv "$PWSH_DIR.tmp" "$PWSH_DIR"
    echo "   PowerShell $PWSH_VER in $PWSH_DIR (SHA-256 OK)"
}

# Runs a .ps1 with PowerShell 7 without the machine going to sleep (systemd-inhibit if present: long builds).
run_ps1() {
    local script="$1"; shift
    # (only if systemd-inhibit really works: in containers or without a systemd session it fails "Failed to connect to bus")
    if command -v systemd-inhibit >/dev/null 2>&1 && systemd-inhibit --what=idle:sleep --why=test true >/dev/null 2>&1; then
        exec systemd-inhibit --what=idle:sleep --who="tsukihime" --why="$(basename "$script")" "$PWSH" -NoProfile -File "$script" "$@"
    else
        exec "$PWSH" -NoProfile -File "$script" "$@"
    fi
}
