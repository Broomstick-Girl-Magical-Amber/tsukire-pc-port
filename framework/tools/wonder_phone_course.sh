#!/bin/bash
# Scripted course 1-1 run on a phone (the Mac's private/wonder/course.sh, on Android): the same timed inputs through
# NX_INPUT_SCRIPT, frames every 600, a 10 s CPU profile of the game threads in the course (profile.txt), and the
# results in private/wonder/phone_<name>/ with a summary (tools/wonder_run_summary.py).
# usage: tools/wonder_phone_course.sh <serial> <name> [NX_X=Y ...]   (extra config.txt lines)
# Switches (docs/nvn/android-perf/PHASE4.md §2.3):
#   REPLAY=<file>   a script made by tools/wonder_replay_script.py from a recorded play, instead of the blind one
#   MEMWATCH=1      dumpsys meminfo every 20 s (the app's total, its Graphics part, native heap, MemAvailable, swap);
#                   0: none (performance runs: a full smaps read stalls the game 100-300 ms); end: once at the end
#   THERMAL=1       wait until the phone is cool first (tools/phone_thermal_wait.py); 0: only record a reading
#   PROFILE_AT=200  when the 10 s profile starts (s, a multiple of 20)
#   GPUWATCH=1      the GPU clock and busy % from sysfs every 20 s (gpu.txt)
#   BURST="200 240" frame bursts at those times (s, multiples of 20): BURST_N consecutive frames each (default 60),
#                   for glitches that last a frame or two (exploded vertices, flicker)
S=$1; N=$2; shift 2
cd "$(dirname "$0")/.."
PKG=io.github.belmantegu.wonderrecomp
D="/storage/emulated/0/Super Mario Bros. Wonder"
OUT=private/wonder/phone_$N
MEMWATCH=${MEMWATCH:-1}; THERMAL=${THERMAL:-1}; PROFILE_AT=${PROFILE_AT:-200}; GPUWATCH=${GPUWATCH:-0}
rm -rf "$OUT"; mkdir -p "$OUT/frames"
SC="25:A:0.5;40:A:1.0;60:A:1.0;95:RIGHT:0.3;105:A:1.0;118:A:1.0;150:A:3.0;160:A:0.1;160.5:A:0.1;161:A:0.1;161.5:A:0.1;162:A:0.1;170:A:0.05;175:A:0.3;176:A:0.3;177:A:0.3;"
for t in $(seq 185 3 380); do SC="$SC$t:A:0.3;"; done; SC="${SC}185:RIGHT:195;"
[ -n "$REPLAY" ] && SC=$(cat "$REPLAY")
adb -s "$S" shell am force-stop "$PKG"
{
    echo "# scripted course run ($N)"
    echo "NX_HANDHELD=1"
    echo "NX_MEM_LIMIT_GB=5"
    echo "NX_DUMP_FRAMES=600"
    echo "NX_SHOW_WHEN_LOCKED=1"
    echo "NX_INPUT_SCRIPT=$SC"
    for x in "$@"; do echo "$x"; done
} > "$OUT/config.txt"
if [ "$THERMAL" = 1 ]; then
    python3 tools/phone_thermal_wait.py "$S" | tee "$OUT/thermal_start.txt" | tail -1
    [ "${PIPESTATUS[0]}" = 2 ] && echo "thermal: still warm" && echo "# WARM START" >> "$OUT/config.txt"
else
    python3 tools/phone_thermal_wait.py "$S" --once > "$OUT/thermal_start.txt"
fi
# The build that runs, so its profile can be symbolized later (android_profile_report.py checks it).
BIN=$(ls -d ~/Library/Android/sdk/ndk/*/toolchains/llvm/prebuilt/*/bin | tail -1)
"$BIN/llvm-readelf" -n private/wonder/ba/libwonder.so | grep "Build ID" > "$OUT/build_id.txt"
"$BIN/llvm-nm" -n --defined-only private/wonder/ba/libwonder.so | grep -v " main_\| sdk_" | gzip > "$OUT/libwonder.syms.gz"
adb -s "$S" push "$OUT/config.txt" "$D/config.txt" > /dev/null
adb -s "$S" shell rm -rf "'$D/frames'" "'$D/profile.txt'"
adb -s "$S" shell mkdir -p "'$D/frames'"
adb -s "$S" logcat -c
adb -s "$S" shell input keyevent KEYCODE_WAKEUP
adb -s "$S" shell am start -n "$PKG/android.app.NativeActivity" > /dev/null
memsample() {
    M=$(adb -s "$S" shell dumpsys meminfo "$PKG" 2>/dev/null)
    TOTAL=$(echo "$M" | grep -m1 "TOTAL PSS:" | awk '{print $3}')
    [ -z "$TOTAL" ] && TOTAL=$(echo "$M" | grep -m1 "^ *TOTAL " | awk '{print $2}')
    GFX=$(echo "$M" | grep -m1 "Graphics:" | awk '{print $2}')
    NAT=$(echo "$M" | grep -m1 "Native Heap:" | awk '{print $3}')
    AVAIL=$(adb -s "$S" shell grep MemAvailable /proc/meminfo | awk '{print $2}')
    SWAP=$(echo "$M" | grep -m1 "TOTAL SWAP PSS:" | awk '{print $10}')
    echo "$1 $TOTAL $GFX $NAT $AVAIL $SWAP" >> "$OUT/memory.txt"
}
echo "t_s total_pss_kb graphics_kb native_kb mem_available_kb swap_pss_kb" > "$OUT/memory.txt"
for t in $(seq 0 20 300); do
    sleep 20
    [ "$MEMWATCH" = 1 ] && memsample $((t + 20))
    [ "$GPUWATCH" = 1 ] && echo "$((t + 20)) $(adb -s "$S" shell cat /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage 2>/dev/null | tr '\n' ' ')" >> "$OUT/gpu.txt"
    [ "$t" = "$PROFILE_AT" ] && adb -s "$S" shell "echo 10 > '$D/profile'"
    for b in $BURST; do [ "$t" = "$b" ] && adb -s "$S" shell "echo ${BURST_N:-60} 1 > '$D/dump_frames'"; done
done
[ "$MEMWATCH" = end ] && memsample 320
adb -s "$S" logcat -d -v time 'wonder:I' '*:S' > "$OUT/logcat.txt"
adb -s "$S" shell pidof "$PKG" > /dev/null && echo "running at the end" || echo "NOT running at the end"
adb -s "$S" pull "$D/nx.log" "$OUT/nx.log" > /dev/null
adb -s "$S" pull "$D/profile.txt" "$OUT/profile.txt" > /dev/null 2>&1
adb -s "$S" pull "$D/frames/." "$OUT/frames/" > /dev/null 2>&1
adb -s "$S" shell am force-stop "$PKG"
python3 tools/phone_thermal_wait.py "$S" --once > "$OUT/thermal_end.txt"
python3 tools/wonder_run_summary.py "$OUT"
