#!/usr/bin/env bash
# package_gui.sh - Builds the graphical launcher as a portable release binary and leaves it in tools/linux/bin/.
#
#   bash tools/linux/package_gui.sh
#
# Run it on the OLDEST system you want to support (the release is built in an Ubuntu 22.04 container): a binary only
# runs on systems with the same glibc or newer. It builds with build_gui.sh --static (libstdc++ and libgcc inside),
# strips it, and checks that it depends only on glibc and that it needs at most glibc NX_MAX_GLIBC (default 2.35).
# Output (ignored by git, it travels in the release zip through tools\dev\pack_test_zip.ps1):
#   tools/linux/bin/tsukihime_launcher
#   tools/linux/bin/THIRD_PARTY/imgui-LICENSE.txt   (Dear ImGui, MIT: the notice has to travel with the binary)
#   tools/linux/bin/THIRD_PARTY/SDL3-LICENSE.txt    (SDL3, zlib; taken from vcpkg when it has it)
# Needs the same as build_gui.sh (bash tools/linux/install_requirements_linux.sh -Target linux -To 5).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MAXG="${NX_MAX_GLIBC:-2.35}"
BIN="$ROOT/framework/build/launcher/tsukihime_launcher"
DEST="$ROOT/tools/linux/bin"
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "this is for Linux"
[ "$(uname -m)" = x86_64 ] || die "x86_64 only"
bash "$HERE/build_gui.sh" --clean --static
[ -x "$BIN" ] || die "no binary at $BIN"
cp -f "$BIN" "$BIN.stripped"
if command -v strip >/dev/null 2>&1; then strip --strip-unneeded "$BIN.stripped"; fi

# 1) only glibc: any other shared library would have to exist on the user's system
bad="$(ldd "$BIN.stripped" | awk '{print $1}' | grep -vE '^(linux-vdso\.so\.1|/lib64/ld-linux-x86-64\.so\.2|ld-linux-x86-64\.so\.2|lib(c|m|dl|pthread|rt)\.so\.[0-9]+)$' || true)"
[ -z "$bad" ] || { rm -f "$BIN.stripped"; die "the binary depends on more than glibc:$(echo; echo "$bad")"; }

# 2) not newer than the glibc we promise
need="$(objdump -T "$BIN.stripped" | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' | sort -Vu | tail -n 1)"
top="$(printf '%s\n%s\n' "$need" "$MAXG" | sort -V | tail -n 1)"
[ "$top" = "$MAXG" ] || { rm -f "$BIN.stripped"; die "it needs glibc $need, more than $MAXG: build it on an older system (or raise NX_MAX_GLIBC)"; }

IM="$(ls -d "$ROOT"/_prepare/imgui-*/ 2>/dev/null | head -n 1)"
[ -n "$IM" ] && [ -f "${IM}LICENSE.txt" ] || { rm -f "$BIN.stripped"; die "Dear ImGui license not found in _prepare/imgui-*/"; }

rm -rf "$DEST"; mkdir -p "$DEST/THIRD_PARTY"
mv -f "$BIN.stripped" "$DEST/tsukihime_launcher"; chmod 755 "$DEST/tsukihime_launcher"
cp -f "${IM}LICENSE.txt" "$DEST/THIRD_PARTY/imgui-LICENSE.txt"
sdl=""
for R in "${NX_VCPKG_ROOT:-}" "${VCPKG_ROOT:-}" "$HOME/dev/vcpkg"; do
    [ -n "$R" ] && [ -f "$R/installed/x64-linux/share/sdl3/copyright" ] && { sdl="$R/installed/x64-linux/share/sdl3/copyright"; break; }
done
if [ -n "$sdl" ]; then cp -f "$sdl" "$DEST/THIRD_PARTY/SDL3-LICENSE.txt"
else echo "WARNING: SDL3 license (vcpkg share/sdl3/copyright) not found: add THIRD_PARTY/SDL3-LICENSE.txt by hand"; fi

echo "OK: tools/linux/bin/tsukihime_launcher   $(du -h "$DEST/tsukihime_launcher" | cut -f1), needs glibc $need (limit $MAXG), only libc/libm"
sha256sum "$DEST/tsukihime_launcher"
echo "Next (Windows): tools\\dev\\pack_test_zip.ps1 -Release  (takes tools/linux/bin/ into the zip)"
