#!/usr/bin/env bash
# test_linux_gui.sh - Smoke test of the graphical launcher (tools/linux/gui) with a fake repository in a temporary folder:
# it renders every tab to a picture (--shot) with no screen (SDL "offscreen" video driver) and runs one command through its
# background runner (--run). Needs the built launcher (bash tools/linux/build_gui.sh); without it the test is skipped.
#   bash tools/tests/test_linux_gui.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BIN="${NX_GUI_BIN:-$ROOT/framework/build/launcher/tsukihime_launcher}"
[ -x "$BIN" ] || { echo "SKIP: $BIN is not built (bash tools/linux/build_gui.sh)"; exit 0; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }

mkdir -p "$W/tools/linux" "$W/shots" "$W/data/tsuki/user/save" "$W/mods/japan/mod_a/romfs" "$W/updates"
cp "$ROOT/tools/linux/launcher.sh" "$ROOT/tools/linux/vulkan_check.sh" "$W/tools/linux/"
printf 'PFS0....' > "$W/updates/patch.nsp"

run() { SDL_VIDEO_DRIVER=offscreen timeout 60 "$BIN" --root "$W" "$@" 2>&1; }

out="$(run --shot "$W/shots")"; rc=$?
[ "$rc" -eq 0 ] && ok "renders and exits" || bad "exit code $rc: $out"
for t in play saves install mods; do
    [ -s "$W/shots/$t.bmp" ] && ok "picture of the $t tab" || bad "no picture of $t"
done
# the pictures are not blank: more than one colour in the file
n="$(od -An -tx1 -v "$W/shots/mods.bmp" | tr -s ' ' '\n' | sort -u | wc -l)"
[ "$n" -gt 8 ] && ok "the picture has content" || bad "blank picture ($n byte values)"

out="$(run --shot "$W/shots" --run "mods disable mod_a -e japan")"
[ -d "$W/mods/japan/_mod_a" ] && grep -q 'disable: mod_a -> _mod_a' "$W/shots/log.txt" && grep -q 'ended with code 0' "$W/shots/log.txt" \
    && ok "a button's command runs in the background and its log is kept" || bad "runner: $(cat "$W/shots/log.txt" 2>/dev/null)"
run --shot "$W/shots" --run "mods bogus" >/dev/null
grep -q 'ended with code 1' "$W/shots/log.txt" && ok "a failing command shows its code" || bad "failing command"

out="$(cd / && SDL_VIDEO_DRIVER=offscreen LAUNCHER_ROOT= "$BIN" --root /nonexistent 2>&1)"
echo "$out" | grep -q 'cannot find tools/linux/launcher.sh' && ok "wrong --root: clear message" || bad "bad root: $out"
[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
