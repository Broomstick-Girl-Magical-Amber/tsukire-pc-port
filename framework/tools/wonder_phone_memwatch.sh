#!/bin/bash
# Memory of the running Wonder app on a phone, every 20 s, until it exits: the app's total (dumpsys meminfo TOTAL
# PSS, GPU memory included), its Graphics part, its native heap, and the phone's available RAM (kB).
# usage: tools/wonder_phone_memwatch.sh <serial> <out file>
S=$1; OUT=$2
PKG=io.github.belmantegu.wonderrecomp
echo "t_s total_pss_kb graphics_kb native_kb mem_available_kb swap_pss_kb" > "$OUT"
t=0
while adb -s "$S" shell pidof "$PKG" > /dev/null 2>&1; do
    M=$(adb -s "$S" shell dumpsys meminfo "$PKG" 2>/dev/null)
    TOTAL=$(echo "$M" | grep -m1 "TOTAL PSS:" | awk '{print $3}')
    GFX=$(echo "$M" | grep -m1 "Graphics:" | awk '{print $2}')
    NAT=$(echo "$M" | grep -m1 "Native Heap:" | awk '{print $3}')
    AVAIL=$(adb -s "$S" shell grep MemAvailable /proc/meminfo | awk '{print $2}')
    SWAP=$(echo "$M" | grep -m1 "TOTAL SWAP PSS:" | awk '{print $10}')
    echo "$t $TOTAL $GFX $NAT $AVAIL $SWAP" >> "$OUT"
    sleep 20
    t=$((t + 20))
done
