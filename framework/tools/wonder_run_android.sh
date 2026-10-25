#!/bin/bash
# Start Wonder on a device/emulator, wait $2 seconds, save the log (logcat, tag wonder) and pull the frame dumps.
# usage: tools/wonder_run_android.sh <serial> <seconds> <name>
S=${1:-emulator-5554}; T=${2:-90}; N=${3:-emu}
cd "$(dirname "$0")/.."
PKG=io.github.belmantegu.wonderrecomp
D="/storage/emulated/0/Super Mario Bros. Wonder"
adb -s "$S" shell am force-stop "$PKG"
adb -s "$S" shell rm -f "'$D/frames/'*"
adb -s "$S" logcat -c
adb -s "$S" shell am start -n "$PKG/android.app.NativeActivity" > /dev/null
sleep "$T"
adb -s "$S" logcat -d -v time 'wonder:I' 'DEBUG:*' 'libc:F' '*:S' > "private/wonder/$N.log"
adb -s "$S" shell pidof "$PKG" > /dev/null && echo "running" || echo "not running"
rm -rf "private/wonder/frames_$N"; mkdir -p "private/wonder/frames_$N"
adb -s "$S" pull "$D/frames/." "private/wonder/frames_$N/" > /dev/null 2>&1
ls "private/wonder/frames_$N" | head
grep -E "FATAL|signal|Fatal|nvn/vk: (Adr|Swift|present mode|swapchain)|per frame" "private/wonder/$N.log" | tail -8 | cut -c1-200
