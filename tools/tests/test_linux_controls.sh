#!/usr/bin/env bash
# test_linux_controls.sh - Tests of "launcher.sh controls | title | options" (tools/linux/controls.py) with a fake
# repository in a temporary folder. Needs python3. Nothing outside the temporary folder is touched.
#   bash tools/tests/test_linux_controls.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../linux"
command -v python3 >/dev/null 2>&1 || { echo "SKIP: no python3"; exit 0; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }

mkdir -p "$W/tools/linux" "$W/data/tsuki" "$W/data/tsuki_global" "$W/framework/games/tsukihime_common" "$W/framework/build/tsukihime" "$W/framework/build/tsukihime_global"
cp "$SRC/launcher.sh" "$SRC/controls.py" "$SRC/vulkan_check.sh" "$W/tools/linux/"
cp "$HERE/../../framework/games/tsukihime_common/title.ini.example" "$W/framework/games/tsukihime_common/" 2>/dev/null \
    || printf '; example\n[title]\n; video = auto\n; hotkeys = on\n' > "$W/framework/games/tsukihime_common/title.ini.example"
printf 'x already in the environment (kept) y' > "$W/framework/build/tsukihime/tsukihime"; chmod +x "$W/framework/build/tsukihime/tsukihime"
printf 'not reading config' > "$W/framework/build/tsukihime_global/tsukihime_global"; chmod +x "$W/framework/build/tsukihime_global/tsukihime_global"
cd "$W" || exit 1
L() { bash tools/linux/launcher.sh "$@" 2>&1; }

out="$(L controls)"; echo "$out" | grep -qE '^  A +Advance / accept +Space, Enter$' && echo "$out" | grep -q 'defaults of the port' && ok "controls show: defaults without input.ini" || bad "show defaults: $out"
out="$(L controls bind keyboard A Space,Enter,Z)"; echo "$out" | grep -q 'Saved: data/tsuki/input.ini and data/tsuki_global/input.ini' && ok "bind: saved in both editions" || bad "bind: $out"
cmp -s data/tsuki/input.ini data/tsuki_global/input.ini && grep -q '^\[teclado\]' data/tsuki/input.ini && grep -qE '^A +\= Space, Enter, Z$' data/tsuki/input.ini && grep -qE '^SDOWN +\= PageDown$' data/tsuki/input.ini && ok "bind: the whole section is written (it replaces the defaults), both files equal" || bad "bind content: $(cat data/tsuki/input.ini)"
! grep -q '^\[mouse\]' data/tsuki/input.ini && ok "bind: the mouse keeps its defaults (no section)" || bad "mouse section appeared"
out="$(L controls bind keyboard B Escape)"; echo "$out" | grep -q 'already used by Y' && grep -qE '^B +\= Backspace$' data/tsuki/input.ini && ok "bind: a key used by another action is refused" || bad "clash: $out"
L controls bind keyboard B Escape --move >/dev/null; grep -qE '^B +\= Escape$' data/tsuki/input.ini && grep -qE '^Y +\= S, L, C$' data/tsuki/input.ini && ok "bind --move: taken from the other action" || bad "move: $(cat data/tsuki/input.ini)"
L controls bind keyboard X Escape --share >/dev/null; grep -qE '^X +\= Escape$' data/tsuki/input.ini && grep -qE '^B +\= Escape$' data/tsuki/input.ini && ok "bind --share: both keep it" || bad "share"
L controls bind keyboard A Q --add >/dev/null; grep -qE '^A +\= Space, Enter, Z, Q$' data/tsuki/input.ini && ok "bind --add: appended" || bad "add"
out="$(L controls bind keyboard UP nokey)"; echo "$out" | grep -q "does not know the key 'nokey'" && ok "bind: unknown key" || bad "unknown key: $out"
out="$(L controls bind keyboard A F11)"; echo "$out" | grep -q "does not know" && ok "bind: F11 cannot be assigned" || bad "f11: $out"
out="$(L controls bind keyboard NOPE Space)"; echo "$out" | grep -q "unknown action 'NOPE'" && ok "bind: unknown action" || bad "unknown action: $out"
out="$(L controls bind mouse Y Right,WheelUp --share)"; grep -q '^\[mouse\]' data/tsuki/input.ini && grep -qE '^Y +\= Right, WheelUp$' data/tsuki/input.ini && grep -qE '^DOWN +\= WheelDown$' data/tsuki/input.ini && ok "bind mouse: its section is written with the other defaults" || bad "mouse: $(cat data/tsuki/input.ini)"
L controls unbind keyboard A >/dev/null; ! grep -qE '^A +\=' data/tsuki/input.ini && grep -q '^\[teclado\]' data/tsuki/input.ini && ok "unbind: the action has no key (section stays)" || bad "unbind"
L controls reset mouse >/dev/null; ! grep -q '^\[mouse\]' data/tsuki/input.ini && grep -q '^\[teclado\]' data/tsuki/input.ini && ok "reset mouse: its section is removed" || bad "reset mouse"
L controls clear keyboard >/dev/null; sed -n '/^\[teclado\]/,$p' data/tsuki/input.ini | grep -qE '^[A-Z]+ +=' && bad "clear" || ok "clear: the section is empty"
L controls reset all >/dev/null; ! grep -q '^\[' data/tsuki/input.ini && ok "reset all: no section left" || bad "reset all"

# hand-made file: CRLF, comments, [mando] and unknown lines survive
printf '; my header\r\n\r\n[teclado]\r\n; comment kept\r\nA = Space\r\nweird line\r\n\r\n[mando]\r\nA = B\r\n' > data/tsuki/input.ini
cp data/tsuki/input.ini data/tsuki_global/input.ini
L controls bind keyboard B Backspace,X >/dev/null
python3 - <<'P' && ok "a hand-made file keeps CRLF, comments, unknown lines and [mando]" || bad "hand-made file"
import sys
t = open("data/tsuki/input.ini", "rb").read()
assert t.count(b"\r\n") == t.count(b"\n"), "mixed line endings"
for need in (b"; my header", b"; comment kept", b"weird line", b"[mando]", b"A = B", b"B      = Backspace, X", b"A      = Space"):
    assert need in t, need
assert t.index(b"[teclado]") < t.index(b"[mando]")
P

out="$(L controls --tsv)"; echo "$out" | grep -q "^action	A	Advance / accept	A$" && echo "$out" | grep -q "^bind	kb	A	Space	custom$" && echo "$out" | grep -q "^device	mouse	default$" && echo "$out" | grep -q "^file	input.ini	.*	1	both$" && ok "controls --tsv" || bad "controls tsv: $out"
out="$(L controls keys --tsv)"; echo "$out" | grep -q "^key	Space$" && echo "$out" | grep -q "^key	F12$" && echo "$out" | grep -q "^mouse	WheelUp	" && ok "controls keys --tsv" || bad "keys tsv"

# the file of an edition: the binary's folder wins over the data folder, and NX_INPUT_INI wins over both
rm -f data/*/input.ini; printf '[teclado]\nA = Z\n' > framework/build/tsukihime/input.ini
out="$(L controls bind keyboard B Backspace)"; echo "$out" | grep -q 'framework/build/tsukihime/input.ini' && ok "input.ini next to the binary is the one used" || bad "beside exe: $out"
rm -f framework/build/tsukihime/input.ini data/*/input.ini
out="$(NX_INPUT_INI=other.ini L controls bind keyboard B Backspace)"; [ -f other.ini ] && echo "$out" | grep -q 'Saved: other.ini' && ok "NX_INPUT_INI is used (one file for both editions)" || bad "env: $out"
rm -f other.ini

# title.ini
out="$(L title)"; echo "$out" | grep -q 'video  *auto  *(default)' && ok "title show: defaults" || bad "title show: $out"
out="$(L title set video 3)"; echo "$out" | grep -q 'Saved: data/tsuki/title.ini and data/tsuki_global/title.ini' && grep -q '^video = 3$' data/tsuki/title.ini && grep -q '^\[title\]' data/tsuki/title.ini && cmp -s data/tsuki/title.ini data/tsuki_global/title.ini && ok "title set: created from the example, both editions" || bad "title set: $out"
out="$(L title set video 9)"; echo "$out" | grep -q 'ERROR: video:' && ok "title set: invalid value" || bad "title invalid: $out"
L title set pool "4,2" >/dev/null; grep -q '^pool = 4, 2$' data/tsuki/title.ini && ok "title set pool" || bad "pool"
L title set hotkeys off >/dev/null; grep -q '^hotkeys = off$' data/tsuki/title.ini && ok "title set hotkeys off (the commented example line is used)" || bad "hotkeys: $(grep hotkeys data/tsuki/title.ini)"
out="$(L title set fade 6000)"; echo "$out" | grep -q 'ERROR: fade:' && ok "title set fade: range" || bad "fade: $out"
out="$(L title --tsv)"; echo "$out" | grep -q "^title	video	3	set	" && echo "$out" | grep -q "^title	music	follow	default	" && ok "title --tsv" || bad "title tsv: $out"
L title reset >/dev/null; grep -q '^video = auto$' data/tsuki/title.ini && grep -q '^hotkeys = on$' data/tsuki/title.ini && ok "title reset: defaults written" || bad "title reset"

# options (config.txt of the edition)
out="$(L options)"; echo "$out" | grep -q 'File: data/tsuki/config.txt (not created yet' && ok "options show: no file yet" || bad "options show: $out"
L options set fullscreen on >/dev/null; L options set fps 30 >/dev/null; L options set gpu "GeForce 3060" >/dev/null; L options set button-icons PlayStation >/dev/null; L options set discord off >/dev/null; L options set window-size 1600x900 >/dev/null
grep -qx 'NX_FULLSCREEN=1' data/tsuki/config.txt && grep -qx 'NX_FPS_CAP=30' data/tsuki/config.txt && grep -qx 'NX_GPU=GeForce 3060' data/tsuki/config.txt && grep -qx 'NX_HUD_GLYPHS=ps' data/tsuki/config.txt && grep -qx 'NX_DISCORD=0' data/tsuki/config.txt && grep -qx 'NX_RESOLUTION=1600x900' data/tsuki/config.txt && ok "options set: the NX_ lines of config.txt" || bad "config: $(cat data/tsuki/config.txt)"
[ ! -f data/tsuki_global/config.txt ] && ok "options: per edition (the other has no file)" || bad "global config appeared"
out="$(L options set render-res 100x100)"; echo "$out" | grep -q 'ERROR: render-res' && ok "options set: invalid size" || bad "render-res: $out"
out="$(L options set mute maybe)"; echo "$out" | grep -q 'ERROR: mute: on or off' && ok "options set: invalid switch" || bad "mute: $out"
L options set fullscreen off >/dev/null; ! grep -q NX_FULLSCREEN data/tsuki/config.txt && ok "options set off: the line is removed" || bad "fullscreen off"
out="$(L options --tsv)"; echo "$out" | grep -q "^option	fps	30	set	NX_FPS_CAP	" && echo "$out" | grep -q "^option	discord	off	set	NX_DISCORD	" && echo "$out" | grep -q "^option	button-icons	ps	set	" && ! echo "$out" | grep -q '^warn' && ok "options --tsv" || bad "options tsv: $out"
printf '; mine\nNX_FOO=1\n' >> data/tsuki/config.txt; L options reset >/dev/null; grep -qx 'NX_FOO=1' data/tsuki/config.txt && ! grep -q 'NX_FPS_CAP\|NX_GPU' data/tsuki/config.txt && grep -qx '; mine' data/tsuki/config.txt && ok "options reset: only the options of the page, other lines stay" || bad "reset: $(cat data/tsuki/config.txt)"
L --edition both options set mute on >/dev/null 2>&1; L options set mute on -e both >/dev/null; grep -qx 'NX_MUTE=1' data/tsuki/config.txt && grep -qx 'NX_MUTE=1' data/tsuki_global/config.txt && ok "options -e both: writes both" || bad "both"
out="$(L options --tsv -e global)"; echo "$out" | grep -q "^warn	" && ok "options: warns when the binary does not read config.txt" || bad "warn: $out"
out="$(L options set gpu auto -e global)"; echo "$out" | grep -q 'Saved' && ! grep -q NX_GPU data/tsuki_global/config.txt && ok "options set gpu auto: removes NX_GPU" || bad "gpu auto: $out"
out="$(L options gpus)"; ok "options gpus runs (lists $(echo "$out" | grep -c .) card(s))"

# no data folder
rm -rf data; out="$(L controls bind keyboard A Z)"; echo "$out" | grep -q 'no data folder yet' && ok "no data folder: clear message" || bad "no data: $out"
out="$(L options set mute on)"; echo "$out" | grep -q 'no data folder' && ok "options: no data folder" || bad "options no data: $out"

[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
