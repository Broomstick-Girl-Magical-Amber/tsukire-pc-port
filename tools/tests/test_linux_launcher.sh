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

out="$(L status)"; echo "$out" | grep -q 'Japanese (cartridge): ready' && echo "$out" | grep -q 'saves: 3 file' && ok "status: ready, 3 saves" || bad "status: $out"
out="$(L status -e global)"; echo "$out" | grep -q 'Global (eShop): not built' && ok "status: global not built" || bad "status global"
out="$(L play --gpu-hang 5000 -- a b)"; echo "$out" | grep -q 'gh=5000 args=2' && ok "play: env and args reach the game" || bad "play: $out"
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
[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
