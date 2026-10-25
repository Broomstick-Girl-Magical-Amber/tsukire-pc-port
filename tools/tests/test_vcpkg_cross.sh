#!/usr/bin/env bash
# test_vcpkg_cross.sh - ISOLATED TEST (not part of the final flow): builds on Linux, with vcpkg, the 6 libraries
# of the port for x64-windows (Windows DLLs) using clang-cl + xwin. It tells whether the cross build is viable
# before touching install_requirements.ps1 / recomp_tsukihime.ps1.
#
#   bash tools/tests/test_vcpkg_cross.sh                    # everything (asks you to accept the Microsoft license in xwin)
#   bash tools/tests/test_vcpkg_cross.sh --from 4           # resume (each step checks its own part and is skipped if done)
#   bash tools/tests/test_vcpkg_cross.sh --crt 14.50.35717 --sdk 10.0.26100   # same versions as on Windows
#   bash tools/tests/test_vcpkg_cross.sh --check            # only looks: downloads, installs and creates nothing
#
# Steps: 0 precheck | 1 tools (LLVM, CMake, Ninja, xwin: tarballs with a fixed SHA-256) | 2 xwin (Windows CRT +
#        SDK; Microsoft license: you accept it when xwin asks) | 3 vcpkg (fixed commit + bootstrap) |
#        4 triplet and toolchain | 5 vcpkg install (ffmpeg takes hours) | 6 report
#
# Where it puts things (nothing inside the repo except _prepare/, which is ignored):
#   tools         ${NX_TOOLS_DIR:-~/.local/tsuki-tools}      (llvm-*, cmake-*, ninja-*, xwin-*, xwin/, downloads/)
#   vcpkg         ${NX_VCPKG:-~/dev/vcpkg}                   (same as C:\dev\vcpkg)
#   log, triplet  <root>/_prepare/linux/
# Rules: never deletes; what does not match the expected is reported and the script stops (you set it aside by hand).
# What is downloaded is verified with SHA-256 before use. Does not read keys or game data.
set -euo pipefail

# ------------------------------------------------------------------------------------------------ pins
LLVM_VER=23.1.3
LLVM_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VER/LLVM-$LLVM_VER-Linux-X64.tar.xz"
LLVM_SHA=ca9fe385a5d9e2d88132dc0e23a00e9f87f38fa75f67207b5e457d208e71d013
CMAKE_VER=4.4.4
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v$CMAKE_VER/cmake-$CMAKE_VER-linux-x86_64.tar.gz"
CMAKE_SHA=e5bb807f7728cb60cd8b27ebc97a2edb469b68655f21e844a600c3575b76f5bb
NINJA_VER=1.13.2
NINJA_URL="https://github.com/ninja-build/ninja/releases/download/v$NINJA_VER/ninja-linux.zip"
NINJA_SHA=5749cbc4e668273514150a80e387a957f933c6ed3f5f11e03fb30955e2bbead6
XWIN_VER=0.10.0
XWIN_URL="https://github.com/Jake-Shadle/xwin/releases/download/$XWIN_VER/xwin-$XWIN_VER-x86_64-unknown-linux-musl.tar.gz"
XWIN_SHA=d870eb4b2f390878af6da1ccd3cf321d22fcb72720984853b4be732ae597fc88
XWIN_MANIFEST=18          # VS 2026 (Build Tools 18.x, as on Windows)
VCPKG_COMMIT=bf19794383e8fb95eba5aa553235bc8ec2271688
VCPKG_PORTS=(ffmpeg opus libjpeg-turbo libpng zlib sdl3)

# ------------------------------------------------------------------------------------------------ options
FROM=0; TO=6; CHECK=0; CRT=""; SDK=""
while [ $# -gt 0 ]; do
    case "$1" in
        --from) FROM="$2"; shift 2 ;;
        --to) TO="$2"; shift 2 ;;
        --crt) CRT="$2"; shift 2 ;;
        --sdk) SDK="$2"; shift 2 ;;
        --check) CHECK=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (--help)"; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"                 # <root>/tools/tests/this.sh
[ -d "$ROOT/framework" ] || { echo "cannot find <root>/framework starting from $HERE"; exit 1; }
T="${NX_TOOLS_DIR:-$HOME/.local/tsuki-tools}"
VCPKG="${NX_VCPKG:-$HOME/dev/vcpkg}"
WORK="$ROOT/_prepare/linux"
TOOLCHAIN="$ROOT/tools/linux/clangcl-xwin.cmake"

if [ "$CHECK" = 0 ]; then
    mkdir -p "$WORK"
    LOG="$WORK/test_vcpkg_$(date +%Y%m%d-%H%M%S).log"
    exec > >(tee -a "$LOG") 2>&1
else
    LOG="(no log: --check)"
fi

STEP=0; MISSING=()
step()  { STEP=$1; echo; echo "== step $1: $2"; }
info()  { echo "   $*"; }
warn()  { echo "   WARNING: $*"; }
fail()  { echo; echo "ERROR in step $STEP: $*"; echo "Resume with --from $STEP once it is fixed (log: $LOG)"; exit 1; }
problem() { if [ "$CHECK" = 1 ]; then MISSING+=("step $STEP: $*"); echo "   MISSING: $*"; else fail "$*"; fi; }
in_range() { [ "$1" -ge "$FROM" ] && [ "$1" -le "$TO" ]; }

LLVM_DIR="$T/llvm-$LLVM_VER"; CMAKE_DIR="$T/cmake-$CMAKE_VER"; NINJA_DIR="$T/ninja-$NINJA_VER"; XWIN_BIN_DIR="$T/xwin-$XWIN_VER"
XWIN_OUT="$T/xwin"; XWIN_STAMP="$XWIN_OUT/VERSIONS.txt"
export PATH="$LLVM_DIR/bin:$CMAKE_DIR/bin:$NINJA_DIR:$XWIN_BIN_DIR:$PATH"

# ------------------------------------------------------------------------------------------------ 0
step 0 precheck
info "root $ROOT; tools $T; vcpkg $VCPKG; log $LOG"
[ "$(uname -s)" = Linux ] || fail "this is for Linux"
[ "$(uname -m)" = x86_64 ] || fail "x86_64 is needed (this is $(uname -m))"
[ -r /etc/os-release ] && info "system: $(. /etc/os-release; echo "$PRETTY_NAME")"
missing_cmd=()
for c in git curl tar xz unzip zip make gcc g++ pkg-config nasm bash sha256sum; do
    command -v "$c" >/dev/null 2>&1 || missing_cmd+=("$c")
done
if [ ${#missing_cmd[@]} -gt 0 ]; then
    info "system programs missing: ${missing_cmd[*]}"
    info "  Ubuntu/Debian: sudo apt install git curl tar xz-utils unzip zip make build-essential pkg-config nasm"
    info "  Fedora:        sudo dnf install git curl tar xz unzip zip make gcc gcc-c++ pkgconf-pkg-config nasm perl-FindBin"
    info "  Arch:          sudo pacman -S --needed git curl tar xz unzip zip make base-devel pkgconf nasm"
    problem "system programs: ${missing_cmd[*]}"
fi
command -v nasm >/dev/null 2>&1 && info "nasm $(nasm -v | awk '{print $3}') (from the system; ffmpeg uses it for the x86 assembler)"
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }
d="$HOME"; [ -d "$T" ] && d="$T"
info "free space in $d: $(free_gb "$d") GB (~35 GB needed: LLVM 2 GB compressed + 12 extracted, xwin ~2, vcpkg with ffmpeg ~15)"
[ "$(free_gb "$d")" -ge 35 ] || warn "less than 35 GB free"

# ------------------------------------------------------------------------------------------------ 1
download() {  # url file sha
    local url="$1" dst="$2" sha="$3"
    if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$sha" ]; then return 0; fi
    [ -e "$dst" ] && fail "$dst exists with another SHA-256: set it aside by hand"
    info "downloading $url"
    curl -fL --retry 3 -o "$dst.partial" "$url" || fail "could not download $url"
    local h; h="$(sha256sum "$dst.partial" | cut -d' ' -f1)"
    if [ "$h" != "$sha" ]; then
        mv "$dst.partial" "$dst.sha-mismatch"
        fail "$(basename "$dst"): SHA-256 $h, expected $sha (kept as $dst.sha-mismatch)"
    fi
    mv "$dst.partial" "$dst"
    info "SHA-256 OK: $(basename "$dst")"
}
install_tarball() {  # name destination url sha type(tar|zip) [strip]
    local name="$1" dst="$2" url="$3" sha="$4" type="$5" strip="${6:-1}"
    if [ -f "$dst/.sha256" ] && [ "$(cat "$dst/.sha256")" = "$sha" ]; then info "$name OK ($dst)"; return 0; fi
    if [ "$CHECK" = 1 ]; then problem "$name is not in $dst"; return 0; fi
    [ -e "$dst" ] && fail "$dst exists and is not the expected $name: set it aside by hand"
    mkdir -p "$T/downloads"
    local arch="$T/downloads/$(basename "$url")"
    download "$url" "$arch" "$sha"
    local tmp="$dst.tmp"
    [ -e "$tmp" ] && fail "$tmp exists (from an interrupted run): set it aside by hand"
    mkdir -p "$tmp"
    if [ "$type" = zip ]; then unzip -q "$arch" -d "$tmp"; else tar -xf "$arch" -C "$tmp" --strip-components="$strip"; fi
    echo "$sha" > "$tmp/.sha256"
    mv "$tmp" "$dst"
    info "$name installed in $dst"
}
if in_range 1; then
    step 1 "tools (tarballs with a fixed SHA-256)"
    [ "$CHECK" = 1 ] || mkdir -p "$T"
    install_tarball "LLVM $LLVM_VER" "$LLVM_DIR" "$LLVM_URL" "$LLVM_SHA" tar
    install_tarball "CMake $CMAKE_VER" "$CMAKE_DIR" "$CMAKE_URL" "$CMAKE_SHA" tar
    install_tarball "Ninja $NINJA_VER" "$NINJA_DIR" "$NINJA_URL" "$NINJA_SHA" zip
    install_tarball "xwin $XWIN_VER" "$XWIN_BIN_DIR" "$XWIN_URL" "$XWIN_SHA" tar
    if [ "$CHECK" = 0 ]; then
        info "$(clang-cl --version | head -1)"
        info "$(cmake --version | head -1); ninja $(ninja --version); $(xwin --version)"
    fi
fi

# ------------------------------------------------------------------------------------------------ 2
if in_range 2; then
    step 2 "xwin: MSVC CRT/STL and Windows SDK"
    request="manifest=$XWIN_MANIFEST crt=${CRT:-latest} sdk=${SDK:-latest}"
    if [ -f "$XWIN_STAMP" ]; then
        info "already in $XWIN_OUT:"; sed 's/^/     /' "$XWIN_STAMP"
        grep -qx "request: $request" "$XWIN_STAMP" || problem "$XWIN_OUT is from another request (now: $request): set it aside by hand to download another"
    elif [ "$CHECK" = 1 ]; then
        problem "$XWIN_OUT is missing"
    else
        [ -e "$XWIN_OUT" ] && fail "$XWIN_OUT exists without $XWIN_STAMP: set it aside by hand"
        info "xwin downloads the CRT and the SDK from the Microsoft servers. It will ask whether you accept the license:"
        info "  https://go.microsoft.com/fwlink/?LinkId=2086102   (you answer: yes / no)"
        xa=(--manifest-version "$XWIN_MANIFEST" --cache-dir "$T/xwin-cache" --http-retry 3)
        [ -n "$CRT" ] && xa+=(--crt-version "$CRT")
        [ -n "$SDK" ] && xa+=(--sdk-version "$SDK")
        info "available / chosen versions (xwin may ask for the license twice: list and splat):"
        xwin "${xa[@]}" list || fail "xwin list failed (or you did not accept the license)"
        xwin "${xa[@]}" splat --use-winsysroot-style --preserve-ms-arch-notation --output "$XWIN_OUT.tmp" \
            || fail "xwin splat failed ($XWIN_OUT.tmp is left: set it aside by hand before retrying)"
        crt_done="$(ls "$XWIN_OUT.tmp/VC/Tools/MSVC" | head -1)"; sdk_done="$(ls "$XWIN_OUT.tmp/Windows Kits/10/Include" | head -1)"
        { echo "request: $request"; echo "crt: $crt_done"; echo "sdk: $sdk_done"; echo "xwin: $XWIN_VER"; echo "date: $(date -Is)"; } > "$XWIN_OUT.tmp/VERSIONS.txt"
        mv "$XWIN_OUT.tmp" "$XWIN_OUT"
        info "CRT $crt_done, SDK $sdk_done in $XWIN_OUT"
    fi
    [ -d "$XWIN_OUT/VC/Tools/MSVC" ] && [ -f "$XWIN_OUT/VC/Tools/MSVC/$(ls "$XWIN_OUT/VC/Tools/MSVC" | head -1)/lib/x64/delayimp.lib" ] \
        && info "delayimp.lib present (/DELAYLOAD:SDL3.dll needs it)" \
        || { [ -d "$XWIN_OUT" ] && warn "cannot find delayimp.lib in the xwin CRT: /DELAYLOAD:SDL3.dll will fail when linking the game"; }
fi

# ------------------------------------------------------------------------------------------------ 3
if in_range 3; then
    step 3 "vcpkg at the reference commit"
    if [ -d "$VCPKG/.git" ]; then
        head="$(git -C "$VCPKG" rev-parse HEAD)"
        [ "$head" = "$VCPKG_COMMIT" ] && info "vcpkg in $VCPKG, reference commit" \
            || problem "vcpkg in $VCPKG is at $head, not at $VCPKG_COMMIT"
    elif [ -e "$VCPKG" ]; then
        problem "$VCPKG exists and is not a git clone"
    elif [ "$CHECK" = 1 ]; then
        problem "there is no vcpkg in $VCPKG"
    else
        [ -e "$VCPKG.tmp" ] && fail "$VCPKG.tmp exists (from an interrupted run): set it aside by hand"
        mkdir -p "$(dirname "$VCPKG")"
        git clone https://github.com/microsoft/vcpkg.git "$VCPKG.tmp"
        git -C "$VCPKG.tmp" checkout -q "$VCPKG_COMMIT"
        mv "$VCPKG.tmp" "$VCPKG"
        info "vcpkg cloned ($VCPKG_COMMIT)"
    fi
    if [ -x "$VCPKG/vcpkg" ]; then info "vcpkg $("$VCPKG/vcpkg" version --disable-metrics 2>/dev/null | head -1)"
    elif [ "$CHECK" = 1 ]; then problem "vcpkg without bootstrap"
    else "$VCPKG/bootstrap-vcpkg.sh" -disableMetrics || fail "bootstrap-vcpkg.sh failed"; fi
fi

# ------------------------------------------------------------------------------------------------ 4
OV="$WORK/triplets"
if in_range 4; then
    step 4 "x64-windows overlay triplet (Release only, clang-cl + xwin)"
    [ -f "$TOOLCHAIN" ] || fail "$TOOLCHAIN is missing"
    if [ "$CHECK" = 1 ]; then
        [ -f "$OV/x64-windows.cmake" ] && info "triplet present" || problem "$OV/x64-windows.cmake is missing"
    else
        [ -f "$VCPKG/triplets/x64-windows.cmake" ] || fail "$VCPKG/triplets/x64-windows.cmake does not exist"
        mkdir -p "$OV"
        # Same name as on Windows (x64-windows): installed/x64-windows stays where the build looks for it.
        { cat "$VCPKG/triplets/x64-windows.cmake"
          echo ""
          echo "# --- tsukihime (Linux -> Windows): Release only and clang-cl + xwin toolchain"
          echo "set(VCPKG_BUILD_TYPE release)"
          echo "set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE \"$OV/vcpkg-xwin.cmake\")"
        } > "$OV/x64-windows.cmake.tmp"
        { echo "# Generated by test_vcpkg_cross.sh (paths of this PC; does not go to the repo)."
          echo "set(NX_XWIN \"$XWIN_OUT\")"
          echo "set(NX_LLVM \"$LLVM_DIR/bin\")"
          echo "include(\"$VCPKG/scripts/toolchains/windows.cmake\")"
          echo "include(\"$TOOLCHAIN\")"
        } > "$OV/vcpkg-xwin.cmake.tmp"
        mv "$OV/x64-windows.cmake.tmp" "$OV/x64-windows.cmake"
        mv "$OV/vcpkg-xwin.cmake.tmp" "$OV/vcpkg-xwin.cmake"
        info "triplet $OV/x64-windows.cmake -> $OV/vcpkg-xwin.cmake -> $TOOLCHAIN"
    fi
fi

# ------------------------------------------------------------------------------------------------ 5
list_installed() {
    "$VCPKG/vcpkg" list 2>/dev/null | sed -n 's/^\([a-z0-9-]*\)\(\[[^]]*\]\)\{0,1\}:x64-windows .*/\1/p' | sort -u
}
if in_range 5; then
    step 5 "vcpkg install ${VCPKG_PORTS[*]} --triplet x64-windows"
    if [ "$CHECK" = 1 ]; then
        [ -x "$VCPKG/vcpkg" ] && info "installed: $(list_installed | tr '\n' ' ')" || problem "no vcpkg"
    else
        export VCPKG_ROOT="$VCPKG" VCPKG_DISABLE_METRICS=1
        info "ffmpeg builds from source (1 to several hours); if it is interrupted, --from 5 continues where it stopped"
        info "if something fails, the vcpkg logs are in $VCPKG/buildtrees/<port>/*.log (send me the last lines)"
        "$VCPKG/vcpkg" install "${VCPKG_PORTS[@]}" --triplet x64-windows --host-triplet x64-linux \
            --overlay-triplets="$OV" --clean-after-build || fail "vcpkg install failed"
    fi
fi

# ------------------------------------------------------------------------------------------------ 6
if in_range 6; then
    step 6 report
    I="$VCPKG/installed/x64-windows"
    if [ -d "$I/bin" ]; then
        info "installed x64-windows: $(list_installed | tr '\n' ' ')"
        info "DLLs in $I/bin:"
        for f in "$I"/bin/*.dll; do
            [ -e "$f" ] || continue
            m="$(llvm-readobj --file-headers "$f" 2>/dev/null | awk -F': ' '/Machine:/ {print $2; exit}')"
            deps="$(llvm-readobj --coff-imports "$f" 2>/dev/null | awk -F': ' '/Name:/ && $2 ~ /\.dll$/ {print $2}' | sort -u | tr '\n' ' ')"
            printf '     %-28s %10s B  %s  imports: %s\n' "$(basename "$f")" "$(stat -c %s "$f")" "$m" "$deps"
        done
        info "what tsukihime_common.cmake looks for: opus.dll jpeg62.dll libpng16.dll z.dll SDL3.dll + avcodec/avformat/avutil/swresample/swscale"
        for f in opus.dll jpeg62.dll libpng16.dll z.dll SDL3.dll; do [ -f "$I/bin/$f" ] || warn "$f is missing"; done
        [ -f "$I/share/sdl3/SDL3Config.cmake" ] || warn "share/sdl3/SDL3Config.cmake is missing"
    else
        problem "there is no $I/bin (step 5)"
    fi
fi

echo
if [ ${#MISSING[@]} -gt 0 ]; then echo "Missing:"; printf '   %s\n' "${MISSING[@]}"; exit 1; fi
echo "Done (log: $LOG)."