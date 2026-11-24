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

mkdir -p "$W/tools/linux" "$W/shots" "$W/data/tsuki_global" "$W/data/tsuki/user/save" "$W/mods/japan/mod_a/romfs" "$W/updates"
cp "$ROOT/tools/linux/launcher.sh" "$ROOT/tools/linux/vulkan_check.sh" "$ROOT/tools/linux/controls.py" "$W/tools/linux/"
printf 'PFS0....' > "$W/updates/patch.nsp"

run() { SDL_VIDEO_DRIVER=offscreen timeout 60 "$BIN" --root "$W" "$@" 2>&1; }

out="$(run --shot "$W/shots")"; rc=$?
[ "$rc" -eq 0 ] && ok "renders and exits" || bad "exit code $rc: $out"
for t in play install mods controls mouse title options assign; do
    [ -s "$W/shots/$t.bmp" ] && ok "picture of the $t tab" || bad "no picture of $t"
done
# the Controls tab: the pictures show data (they differ from the empty-state picture of the other tabs)
cmp -s "$W/shots/controls.bmp" "$W/shots/mouse.bmp" && bad "keyboard and mouse pictures are identical" || ok "keyboard and mouse sub-tabs differ"
# the pictures are not blank: more than one colour in the file
n="$(od -An -tx1 -v "$W/shots/mods.bmp" | tr -s ' ' '\n' | sort -u | wc -l)"
[ "$n" -gt 8 ] && ok "the picture has content" || bad "blank picture ($n byte values)"

out="$(run --shot "$W/shots" --run "mods disable mod_a -e japan")"
[ -d "$W/mods/japan/_mod_a" ] && grep -q 'disable: mod_a -> _mod_a' "$W/shots/log.txt" && grep -q 'ended with code 0' "$W/shots/log.txt" \
    && ok "a button's command runs in the background and its log is kept" || bad "runner: $(cat "$W/shots/log.txt" 2>/dev/null)"
run --shot "$W/shots" --run "mods bogus" >/dev/null
grep -q 'ended with code 1' "$W/shots/log.txt" && ok "a failing command shows its code" || bad "failing command"

out="$(run --shot "$W/shots" --run "controls bind keyboard A Space,Z")"
grep -q 'Saved: data/tsuki/input.ini and data/tsuki_global/input.ini' "$W/shots/log.txt" && grep -qE '^A +\= Space, Z$' "$W/data/tsuki/input.ini" \
    && ok "a controls command from a button writes both editions" || bad "controls runner: $(cat "$W/shots/log.txt" 2>/dev/null)"

# Spanish: same pictures with other texts, and the language comes from launcher.ini or the environment
mkdir -p "$W/shots_es" "$W/shots_en"
run --shot "$W/shots_es" --lang es >/dev/null; run --shot "$W/shots_en" --lang en >/dev/null
[ -s "$W/shots_es/play.bmp" ] && ! cmp -s "$W/shots_es/play.bmp" "$W/shots_en/play.bmp" && ok "Spanish picture differs from the English one" || bad "no Spanish picture"
mkdir -p "$W/shots_ini"; printf 'language=es\n' > "$W/launcher.ini"
run --shot "$W/shots_ini" >/dev/null
cmp -s "$W/shots_ini/play.bmp" "$W/shots_es/play.bmp" && ok "language=es in launcher.ini is used" || bad "launcher.ini language"
rm -f "$W/launcher.ini"; mkdir -p "$W/shots_env" "$W/shots_env_en"
env LANG=es_CL.UTF-8 LC_ALL= LC_MESSAGES= LANGUAGE= SDL_VIDEO_DRIVER=offscreen "$BIN" --root "$W" --shot "$W/shots_env" >/dev/null 2>&1
env LANG=en_US.UTF-8 LC_ALL= LC_MESSAGES= LANGUAGE= SDL_VIDEO_DRIVER=offscreen "$BIN" --root "$W" --shot "$W/shots_env_en" >/dev/null 2>&1
! cmp -s "$W/shots_env/play.bmp" "$W/shots_env_en/play.bmp" && ok "auto follows the system language" || bad "auto language"
# every Spanish text has its English key in the source (a typo in a key would leave that text in English)
miss=0
while IFS= read -r k; do grep -qF "$k" "$ROOT/tools/linux/gui/launcher_gui.cpp" "$ROOT/tools/linux/controls.py" || { echo "   key not in launcher_gui.cpp: $k"; miss=1; }; done < <(
    python3 - "$ROOT/tools/linux/gui/strings_es.cpp" <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
skip = {"Language:", "Automatic (system)"}
for en, _ in re.findall(r'\{"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\}', t):
    if en in skip or en in ("ready", "running", "unknown"): continue
    print(en)
PY
)
[ "$miss" = 0 ] && ok "every table key is used by the source" || echo "note - some table keys are not literal in the source (see above)"
# the icon data is the port's icon
if [ -f "$ROOT/framework/games/tsukihime_common/tsukihime.ico" ]; then
    python3 "$ROOT/tools/linux/make_gui_icon.py" --check && ok "icon_data.cpp is up to date" || bad "icon_data.cpp is stale (python3 tools/linux/make_gui_icon.py)"
fi

out="$(cd / && SDL_VIDEO_DRIVER=offscreen LAUNCHER_ROOT= "$BIN" --root /nonexistent 2>&1)"
echo "$out" | grep -q 'cannot find tools/linux/launcher.sh' && ok "wrong --root: clear message" || bad "bad root: $out"
[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
