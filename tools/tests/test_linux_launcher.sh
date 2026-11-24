#!/usr/bin/env bash
# test_linux_launcher.sh - Tests of tools/linux/launcher.sh with a fake repository in a temporary folder and a tiny
# fake game (a C program that exits with $FAKE_RC). Needs gcc or cc. Nothing outside the temporary folder is touched.
#   bash tools/tests/test_linux_launcher.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../linux"
CC="$(command -v gcc || command -v cc || true)"; [ -n "$CC" ] || { echo "SKIP: no C compiler"; exit 0; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }

mkdir -p "$W/tools/linux" "$W/framework/build/tsukihime" "$W/data/tsuki/user/save/save00" "$W/data/tsuki/extract/romfs/shader" "$W/imp/save00"
cp "$SRC/launcher.sh" "$SRC/vulkan_check.sh" "$W/tools/linux/"
cat > "$W/fake.c" <<'X'
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
int main(int c, char **v) {
    const char *r = getenv("FAKE_RC"), *g = getenv("NX_GPU_HANG_MS");
    printf("fake game gh=%s args=%d\n", g ? g : "-", c - 1);
    if (getenv("FAKE_SLEEP")) sleep(4);
    return r ? atoi(r) : 0;
}
X
"$CC" -o "$W/framework/build/tsukihime/tsukihime" "$W/fake.c" || { echo "SKIP: cannot compile"; exit 0; }
echo A > "$W/data/tsuki/user/save/save00/slot1.bin"; echo B > "$W/data/tsuki/user/save/save00/slot2.bin"; echo X > "$W/data/tsuki/user/save/other.bin"
cd "$W" || exit 1
L() { bash tools/linux/launcher.sh "$@" 2>&1; }

out="$(L status)"; echo "$out" | grep -q 'Japanese: ready' && echo "$out" | grep -q 'saves: 3 file' && ok "status: ready, 3 saves" || bad "status: $out"
out="$(L status -e global)"; echo "$out" | grep -q 'Global: not built' && ok "status: global not built" || bad "status global"
out="$(L play --gpu-hang 5000 -- a b)"; echo "$out" | grep -q 'gh=5000 args=2' && ok "play: env and args reach the game" || bad "play: $out"
out="$(L play --gpu-hang 7000ms)"; echo "$out" | grep -q 'gh=7000 ' && ok "play: --gpu-hang accepts the ms suffix" || bad "ms suffix: $out"
out="$(L play --gpu-hang 12x)"; echo "$out" | grep -q 'needs a number' && ok "play: --gpu-hang rejects text" || bad "gpu-hang text"
out="$(FAKE_RC=6 L play)"; echo "$out" | grep -q 'code 6' && echo "$out" | grep -q 'Vulkan is not available' && ok "exit 6 explained" || bad "exit 6"
out="$(FAKE_RC=4 L play)"; echo "$out" | grep -q 'GPU stopped responding' && ok "exit 4 explained" || bad "exit 4"
FAKE_RC=5 L play >/dev/null; [ $? -eq 5 ] && ok "play returns the game's exit code" || bad "rc"
out="$(L play -e global)"; echo "$out" | grep -q 'is not built' && ok "play: edition not built" || bad "play global"
mv data/tsuki/extract data/tsuki/extract.x; out="$(L play)"; mv data/tsuki/extract.x data/tsuki/extract
echo "$out" | grep -q 'no romfs_source.bin' && ok "play: data missing" || bad "no data"

L saves backup >/dev/null; L saves backup >/dev/null
[ "$(ls save_backups/japan | wc -l)" -eq 2 ] && ok "two copies in the same second do not collide" || bad "backup collision"
echo CHANGED > data/tsuki/user/save/save00/slot1.bin
L saves restore 2 >/dev/null
[ "$(cat data/tsuki/user/save/save00/slot1.bin)" = A ] && ls save_backups/japan | grep -q _before_restore && [ ! -e data/tsuki/user/save.restoring ] \
    && ok "restore: file back, previous saves kept, no leftovers" || bad "restore"
out="$(L saves restore ../x)"; echo "$out" | grep -q 'no such copy' && ok "restore: bad name refused" || bad "restore bad name"

echo NEWB > imp/save00/slot2.bin; echo NEWX > imp/other.bin; echo Z > imp/nomatch.bin
out="$(L saves import imp/nomatch.bin)"; echo "$out" | grep -q 'cannot tell which' && ok "import: no match, nothing changed" || bad "import nomatch"
out="$(L saves import imp/save00/slot2.bin)"; echo "$out" | grep -q 'LAUNCHER_ASSUME_YES' && [ "$(cat data/tsuki/user/save/save00/slot2.bin)" = B ] && ok "import: asks, without terminal it does not go on" || bad "import no tty"
LAUNCHER_ASSUME_YES=1 L saves import imp/save00/slot2.bin >/dev/null
[ "$(cat data/tsuki/user/save/save00/slot2.bin)" = NEWB ] && [ "$(cat data/tsuki/user/save/other.bin)" = X ] && ls save_backups/japan | grep -q _before_import && ok "import: only that file replaced, copy made" || bad "import"
LAUNCHER_ASSUME_YES=1 L saves import imp/other.bin >/dev/null
[ "$(cat data/tsuki/user/save/other.bin)" = NEWX ] && ok "import: unique name found" || bad "import unique"

FAKE_SLEEP=1 framework/build/tsukihime/tsukihime >/dev/null 2>&1 & sp=$!; sleep 0.4
out="$(L play)"; echo "$out" | grep -q 'already running' && ok "play: refuses when running" || bad "running"
out="$(L saves restore 1)"; echo "$out" | grep -q 'close it first' && ok "restore: refuses when running" || bad "restore running"
kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null

[ "$(L edition)" = japan ] && L edition global >/dev/null && [ "$(L edition)" = global ] && grep -q '^edition=global' launcher.ini && ok "edition: stored in launcher.ini" || bad "edition"
L edition japan >/dev/null
out="$(L log)"; echo "$out" | grep -q 'no .*nx.log yet' && ok "log: missing is explained" || bad "log"
printf '#!/bin/bash\necho install "$@"\n' > tools/linux/install_requirements_linux.sh; printf '#!/bin/bash\necho recomp "$@"\n' > tools/linux/recomp_linux.sh
[ "$(L install -e both -AcceptLicense)" = "install -Target linux -Recomp -Edition both -AcceptLicense" ] && ok "install: -Target linux added" || bad "install"
[ "$(L recomp -e japan)" = "recomp -Target linux -Edition japan" ] && ok "recomp: -Target linux added" || bad "recomp"
L install -Target windows 2>&1 | grep -q 'already uses' && ok "install: -Target refused" || bad "install target"
out="$(L bogus)"; echo "$out" | grep -q "unknown command" && ok "unknown command" || bad "unknown"

# ---- mods and updates
M1=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
M2=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p mods/japan/tsukihimates mods/japan/_spanish mods/japan/manual1/romfs/shader data/tsuki/analysis updates
echo x > mods/japan/tsukihimates/from_update.txt; echo "$M1" > mods/japan/tsukihimates/requires_main.txt
echo x > mods/japan/_spanish/from_update.txt; echo "$M2" > mods/japan/_spanish/requires_main.txt
echo "$M1" > data/tsuki/analysis/main_source.txt
out="$(L mods list)"
echo "$out" | grep -q '\[x\] tsukihimates .*update (own main)' && echo "$out" | grep -q '\[ \] _spanish' && echo "$out" | grep -q 'manual1 .*manual, has shaders' \
    && ok "mods list: state and kind" || bad "mods list: $out"
echo "$out" | grep -q 'ok: nothing to recompile' && ok "mods check: main matches the build" || bad "check ok: $out"
out="$(L mods list --tsv)"; echo "$out" | grep -q "^layer	tsukihimates	on	update (own main)" && echo "$out" | grep -q "^check	ok	" && ok "mods list --tsv" || bad "tsv: $out"

L mods enable spanish >/dev/null
[ -d mods/japan/spanish ] && [ ! -d mods/japan/_spanish ] && ok "enable: _ removed" || bad "enable"
out="$(L mods list)"; echo "$out" | grep -q 'error: two active updates need a different main' && ok "check: two different mains is an error" || bad "conflict: $out"
L mods disable tsukihimates >/dev/null; out="$(L mods list)"
echo "$out" | grep -q 'warn: the main of the active' && ok "check: other main than the build: recompile" || bad "main change: $out"
L mods disable spanish >/dev/null; L mods enable tsukihimates >/dev/null
[ -d mods/japan/tsukihimates ] && ok "disable/enable by name round trip" || bad "round trip"
out="$(L mods enable nothere)"; echo "$out" | grep -q 'no such layer' && ok "enable: unknown layer" || bad "unknown layer"
out="$(L mods enable ../x)"; echo "$out" | grep -q 'no such layer' && ok "enable: path in the name refused" || bad "path name"

# updates: pending / done by source.txt (ticks of .NET) / ignored
printf 'PFS0....' > updates/patch.nsp; printf 'PFS0....' > updates/_old.nsp
out="$(L updates list)"; echo "$out" | grep -q 'patch.nsp .*pending' && echo "$out" | grep -q '_old.nsp .*ignored' && ok "updates list: pending, ignored" || bad "updates list: $out"
out="$(L mods list)"; echo "$out" | grep -q 'warn: 1 file(s) in updates/ not processed' && ok "check: pending update" || bad "pending check: $out"
tk="$(python3 -c "import os;print(os.stat('updates/patch.nsp').st_mtime_ns//100+621355968000000000)")"
mkdir -p data/tsuki/updates/patch; printf 'patch.nsp|8|%s|9999\n' "$tk" > data/tsuki/updates/patch/source.txt
out="$(L updates list)"; echo "$out" | grep -q 'patch.nsp .*done' && ok "updates: done when source.txt matches size and ticks" || bad "done: $out"
touch -d '2020-01-01' updates/patch.nsp; out="$(L updates list)"; echo "$out" | grep -q 'patch.nsp .*pending' && ok "updates: pending again when the file changed" || bad "changed"

# add a mod folder
mkdir -p src1/romfs/x src2/atmosphere/contents/0100/romfs/y src2/atmosphere/contents/0100/exefs src3/nothing
echo a > src1/romfs/x/f; echo b > src2/atmosphere/contents/0100/romfs/y/g; echo c > src2/atmosphere/contents/0100/exefs/e
out="$(L mods add src1)"; [ -f mods/japan/src1/romfs/x/f ] && [ ! -e src1 ] && ok "mods add: folder with romfs moved whole" || bad "add1: $out"
out="$(L mods add src2)"; [ -f mods/japan/src2/romfs/y/g ] && [ -f src2/atmosphere/contents/0100/exefs/e ] && [ ! -e src2/atmosphere/contents/0100/romfs ] \
    && ok "mods add: deep romfs moved, the rest stays" || bad "add2: $out"
out="$(L mods add src3)"; echo "$out" | grep -q "no 'romfs' folder" && [ -d src3 ] && ok "mods add: no romfs, nothing changed" || bad "add3: $out"
mkdir -p src1/romfs; out="$(L mods add src1)"; echo "$out" | grep -q 'already exists' && ok "mods add: name taken" || bad "add dup: $out"

# remove goes to the trash tool (fake gio)
mkdir -p "$W/trash" "$W/bin"
printf '#!/bin/sh\n[ "$1" = trash ] && shift; [ "$1" = -- ] && shift; mv "$1" "%s/trash/"\n' "$W" > "$W/bin/gio"
chmod +x "$W/bin/gio"
out="$(PATH="$W/bin:$PATH" L mods remove src1)"; [ ! -e mods/japan/src1 ] && [ -d "$W/trash/src1" ] && ok "mods remove: to the trash" || bad "remove: $out"
out="$(PATH="$W/bin:$PATH" L updates remove _old.nsp)"; [ ! -e updates/_old.nsp ] && [ -f "$W/trash/_old.nsp" ] && ok "updates remove: to the trash" || bad "updates remove: $out"

# updates add
printf 'PFS0xxxx' > "$W/new.nsp"; printf 'not a nsp' > "$W/bad.nsp"
out="$(L updates add "$W/bad.nsp")"; echo "$out" | grep -q 'not a valid .nsp' && [ -f "$W/bad.nsp" ] && ok "updates add: bad header refused" || bad "add bad: $out"
out="$(L updates add "$W/new.nsp")"; [ -f updates/new.nsp ] && [ ! -e "$W/new.nsp" ] && [ ! -e updates/new.nsp.partial ] && ok "updates add: moved" || bad "add nsp: $out"
FAKE_SLEEP=1 framework/build/tsukihime/tsukihime >/dev/null 2>&1 & sp=$!; sleep 0.4
out="$(L mods disable tsukihimates)"; echo "$out" | grep -q 'close it first' && ok "mods: blocked while the game runs" || bad "mods running: $out"
kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null

out="$(L status --tsv)"; echo "$out" | grep -q "^edition	japan	ready	ready" && echo "$out" | grep -q "^keys	none" && ok "status --tsv" || bad "status tsv: $out"
out="$(L saves list --tsv)"; echo "$out" | grep -q "^save	" && echo "$out" | grep -q "^copy	1	" && ok "saves list --tsv" || bad "saves tsv: $out"

# gui: the prebuilt binary of the release zip (tools/linux/bin) is used first, even without the execute bit (zips lose it)
mkdir -p tools/linux/bin
printf '#!/bin/sh\necho "prebuilt gui: $*"\n' > tools/linux/bin/tsukihime_launcher
out="$(L gui)"; echo "$out" | grep -q "^prebuilt gui: --root $W\$" && [ -x tools/linux/bin/tsukihime_launcher ] && ok "gui: prebuilt used, made executable" || bad "gui prebuilt: $out"
out="$(L gui --foo)"; echo "$out" | grep -q -- "--root $W --foo" && ok "gui: extra args go to the binary" || bad "gui args: $out"
rm -rf tools/linux/bin

# language of the graphical launcher (launcher.ini, key "language")
out="$(L language)"; [ "$out" = auto ] && ok "language: auto by default" || bad "language default: $out"
L language es >/dev/null; out="$(L language)"; [ "$out" = es ] && grep -q '^language=es' launcher.ini && ok "language: es saved in launcher.ini" || bad "language es: $out"
out="$(L language fr)"; echo "$out" | grep -q 'unknown language' && ok "language: bad value refused" || bad "language bad: $out"
L language auto >/dev/null

# vulkan: shows the state and, when it is missing, the exact command (the host decides which; both are checked)
out="$(L vulkan show --tsv)"
if echo "$out" | grep -q "^vulkan	yes"; then ok "vulkan show --tsv: present"
elif echo "$out" | grep -q "^vulkan	no" && echo "$out" | grep -q "^vulkan_cmd	"; then ok "vulkan show --tsv: missing, with the command"
else bad "vulkan show: $out"; fi
out="$(L vulkan install </dev/null)"
echo "$out" | grep -qE 'loader and driver found|needs a terminal|not known' && ok "vulkan install: no terminal, nothing is run" || bad "vulkan install: $out"
out="$(L vulkan bogus)"; echo "$out" | grep -q 'usage: launcher.sh vulkan' && ok "vulkan: bad verb refused" || bad "vulkan verb: $out"

# desktop entries: the launcher gets one with the port's icon when its binary is there
mkdir -p tools/linux/bin framework/games/tsukihime_common
cp "$SRC/make_desktop_entry.py" tools/linux/; cp "$SRC/../../framework/games/tsukihime_common/tsukihime.ico" framework/games/tsukihime_common/ 2>/dev/null
if [ -f framework/games/tsukihime_common/tsukihime.ico ] && command -v python3 >/dev/null; then
    printf '#!/bin/sh\n' > tools/linux/bin/tsukihime_launcher
    out="$(XDG_DATA_HOME="$W/xdg" L desktop add)"
    [ -f "$W/xdg/applications/tsukihime_launcher.desktop" ] && grep -q '^Icon=tsukihime_launcher' "$W/xdg/applications/tsukihime_launcher.desktop" \
        && grep -q 'launcher.sh" gui' "$W/xdg/applications/tsukihime_launcher.desktop" \
        && [ -s "$W/xdg/icons/hicolor/256x256/apps/tsukihime_launcher.png" ] && ok "desktop add: launcher entry and icon" || bad "desktop add: $out"
    XDG_DATA_HOME="$W/xdg" L desktop remove >/dev/null
    [ ! -e "$W/xdg/applications/tsukihime_launcher.desktop" ] && ok "desktop remove: entry gone" || bad "desktop remove"
else echo "skip - desktop (no icon or python3)"; fi
rm -rf tools/linux/bin

[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
