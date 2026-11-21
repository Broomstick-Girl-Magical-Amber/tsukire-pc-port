#!/usr/bin/env bash
# build_gui.sh - Builds the graphical launcher (Dear ImGui + SDL3) of the native Linux build.
#
#   bash tools/linux/build_gui.sh          # builds framework/build/launcher/tsukihime_launcher
#   bash tools/linux/build_gui.sh --clean  # from scratch
#   bash tools/linux/build_gui.sh --static # libstdc++/libgcc inside the binary (what package_gui.sh uses for releases)
#   bash tools/linux/launcher.sh gui       # builds it if it is missing, then opens it
#
# Needs what install_requirements_linux.sh already leaves: CMake and Ninja and LLVM in NX_TOOLS_DIR (default
# ~/.local/tsuki-tools; the ones of PATH if they are not there) and the SDL3 that vcpkg built for the game
# (NX_VCPKG_ROOT, default ~/dev/vcpkg; otherwise the SDL3 of the system). Dear ImGui is downloaded once into _prepare/
# at a pinned version and checked against its commit. Nothing is installed on the system.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
T="${NX_TOOLS_DIR:-$HOME/.local/tsuki-tools}"
IMGUI_TAG=v1.92.9
IMGUI_COMMIT=01380c579715e62fb9a8d6ec0502c4ea83bfde6e
OUT="$ROOT/framework/build/launcher"
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "this is for Linux"
clean=0; static=OFF
for a in "$@"; do case "$a" in --clean) clean=1 ;; --static) static=ON ;; *) die "unknown option '$a' (--clean, --static)" ;; esac; done

pick() {   # pick <tool> <glob inside NX_TOOLS_DIR>: the tool of the tools folder, else the one of PATH
    local g f
    for g in $T/$2; do [ -x "$g" ] && { echo "$g"; return 0; }; done
    command -v "$1" 2>/dev/null && return 0
    return 1
}
CMAKE="$(pick cmake 'cmake-*/bin/cmake')" || die "cmake not found: run bash tools/linux/launcher.sh install first"
NINJA="$(pick ninja 'ninja-*/ninja')" || die "ninja not found: run bash tools/linux/launcher.sh install first"
CXX="$(pick clang++ 'llvm-*/bin/clang++' || command -v g++ || true)"; [ -n "$CXX" ] || die "no C++ compiler (clang++ or g++): run bash tools/linux/launcher.sh install first"

PREFIX=""
# vcpkg keeps SDL3Config.cmake under share/sdl3 (or lib/cmake/SDL3): the header is the reliable test, as in recomp_tsukihime.ps1
for R in "${NX_VCPKG_ROOT:-}" "${VCPKG_ROOT:-}" "$HOME/dev/vcpkg"; do
    [ -n "$R" ] && [ -f "$R/installed/x64-linux/include/SDL3/SDL.h" ] && { PREFIX="$R/installed/x64-linux"; break; }
done
if [ -z "$PREFIX" ]; then
    if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists sdl3 2>/dev/null; then PREFIX=""
    else die "SDL3 not found (vcpkg x64-linux in ${NX_VCPKG_ROOT:-${VCPKG_ROOT:-$HOME/dev/vcpkg}}, or the system's): run bash tools/linux/launcher.sh install first, or set NX_VCPKG_ROOT"; fi
fi

IM="$ROOT/_prepare/imgui-${IMGUI_TAG#v}"
if [ ! -f "$IM/imgui.h" ]; then
    command -v git >/dev/null 2>&1 || die "git is needed to download Dear ImGui"
    mkdir -p "$ROOT/_prepare"; rm -rf "$IM.tmp"
    echo "   downloading Dear ImGui $IMGUI_TAG"
    git clone -q --depth 1 --branch "$IMGUI_TAG" https://github.com/ocornut/imgui.git "$IM.tmp" || die "could not download Dear ImGui"
    got="$(git -C "$IM.tmp" rev-parse HEAD)"
    [ "$got" = "$IMGUI_COMMIT" ] || { rm -rf "$IM.tmp"; die "Dear ImGui $IMGUI_TAG is commit $got, expected $IMGUI_COMMIT"; }
    rm -rf "$IM.tmp/.git"; mv "$IM.tmp" "$IM"
fi

[ "$clean" = 1 ] && rm -rf "$OUT"
args=(-S "$HERE/gui" -B "$OUT" -G Ninja -DCMAKE_MAKE_PROGRAM="$NINJA" -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="$CXX" -DNX_IMGUI_DIR="$IM" -DNX_STATIC_RUNTIME="$static")
[ -n "$PREFIX" ] && args+=(-DCMAKE_PREFIX_PATH="$PREFIX")
"$CMAKE" "${args[@]}" >/dev/null || { "$CMAKE" "${args[@]}"; die "configure failed"; }
"$CMAKE" --build "$OUT" 2>&1 | tail -n 3
[ -x "$OUT/tsukihime_launcher" ] || die "the build did not produce $OUT/tsukihime_launcher"
echo "Built: framework/build/launcher/tsukihime_launcher   (open it with: bash tools/linux/launcher.sh gui)"
