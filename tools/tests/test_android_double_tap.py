#!/usr/bin/env python3
"""Test of AP_ANDROID_DOUBLE_TAP (apply_android_double_tap.py): a double tap presses A in the Android game.

Compiles games/tsukihime_common/hle_hid_tsuki.cpp for the host with __ANDROID__ defined (its Android touch path),
with the platform stubbed: a fake clock, one finger (read_pointer) and no two-finger gestures. Each "frame" asks for
a touch sample (next_touch_sample, what GetTouchScreenStates gives the game) and runs the pad hook (touch_pad).
Checks, finger by finger:
  - one tap: the game gets it (Start ... End), no A
  - a double tap at the same place: the game gets only the first touch, then A once
  - two taps far apart, or too slow: two touches, no A
  - three quick taps: touch, A, touch (the third starts a new pair)
  - a second finger that moves: a drag (a touch), no A; one kept still: the long press (Y), no A
  - NX_DOUBLE_TAP_MS=0 and NX_TOUCH_GESTURES=0: no A at all
Needs g++ or clang++. Run from anywhere: python tools/tests/test_android_double_tap.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "framework"
HID = FW / "games" / "tsukihime_common" / "hle_hid_tsuki.cpp"

HARNESS = r'''
#include "hle_hid_tsuki.cpp"
#include <cstdarg>
#include <cstdio>
#include <string>

namespace nx {
void log(const char* fmt, ...) { if (!std::getenv("TEST_LOG")) return; va_list a; va_start(a, fmt); std::vfprintf(stderr, fmt, a); va_end(a); std::fputc('\n', stderr); }
[[noreturn]] void fatal(const char* fmt, ...) { va_list a; va_start(a, fmt); std::vfprintf(stderr, fmt, a); va_end(a); std::exit(9); }
HleFn hle_lookup(const char*) { return nullptr; }
HleRegistrar::HleRegistrar(const char*, HleFn) {}
std::string describe(uint64_t) { return {}; }
namespace tsuki { std::string describe_buttons(uint64_t, uint64_t) { return {}; } }
namespace plat {
PadHook pad_hook = nullptr;
uint64_t g_now = 1000000000ull;
PointerState g_ptr;
uint64_t monotonic_ns() { return g_now; }
PictureRect picture_rect(uint32_t w, uint32_t h) { PictureRect r; r.w = w; r.h = h; return r; }
void read_pointer(PointerState& p) { p = g_ptr; }
void read_gestures(TouchGestures& g) { g = TouchGestures(); g.fingers = (g_ptr.buttons & 1) ? 1 : 0; }
}  // namespace plat
}  // namespace nx

using namespace nx;
static int starts = 0, ends = 0, a_presses = 0, y_presses = 0;
static bool a_prev = false, y_prev = false;

static void frame() {
    plat::g_now += 16666667;
    TouchSample s = next_touch_sample();
    if (s.count && (s.attr & kAttrStart)) starts++;
    if (s.count && (s.attr & kAttrEnd)) ends++;
    plat::PadState p;
    plat::pad_hook(p);
    const bool a = p.buttons & plat::kA, y = p.buttons & plat::kY;
    if (a && !a_prev) a_presses++;
    if (y && !y_prev) y_presses++;
    a_prev = a, y_prev = y;
}
static void wait(int ms) { for (int t = 0; t < ms; t += 17) frame(); }
static void down(int x, int y) { plat::g_ptr.x = x, plat::g_ptr.y = y; plat::g_ptr.buttons = 1; plat::g_ptr.downs[0]++; }
static void up() { plat::g_ptr.buttons = 0; }
static void tap(int x, int y, int hold_ms = 70) { down(x, y); wait(hold_ms); up(); }

int main(int argc, char** argv) {
    plat::g_ptr.w = 1280, plat::g_ptr.h = 720;
    const std::string sc = argc > 1 ? argv[1] : "";
    wait(100);
    if (sc == "single") { tap(640, 360); wait(800); }
    else if (sc == "double") { tap(640, 360); wait(120); tap(650, 365); wait(800); }
    else if (sc == "far") { tap(200, 200); wait(120); tap(900, 500); wait(800); }
    else if (sc == "slow") { tap(640, 360); wait(900); tap(640, 360); wait(800); }
    else if (sc == "triple") { tap(640, 360); wait(100); tap(640, 360); wait(100); tap(640, 360); wait(800); }
    else if (sc == "drag") { tap(640, 360); wait(100); down(640, 360); wait(50); plat::g_ptr.x += 120; wait(100); up(); wait(800); }
    else if (sc == "long") { tap(640, 360); wait(100); down(640, 360); wait(700); up(); wait(800); }
    else if (sc == "medium") { tap(640, 360); wait(450); tap(640, 360); wait(800); }
    else if (sc == "fast") { tap(640, 360, 20); wait(30); tap(640, 360, 20); wait(800); }
    else return 3;
    std::printf("STARTS %d ENDS %d A %d Y %d\n", starts, ends, a_presses, y_presses);
    return 0;
}
'''


def main():
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        print("skip: no g++/clang++")
        return 0
    if "AP_ANDROID_DOUBLE_TAP" not in HID.read_text(encoding="utf-8"):
        print("FAIL: apply_android_double_tap.py is not applied to " + str(HID))
        return 1
    fails = 0
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        (work / "harness.cpp").write_text(HARNESS)
        exe = work / "double_tap_test"
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-D__ANDROID__", f"-I{FW / 'runtime' / 'include'}",
                            f"-I{FW / 'runtime' / 'src'}", f"-I{HID.parent}", str(work / "harness.cpp"), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-4000:])
            print("FAIL: the harness does not compile")
            return 1

        def run(sc, **env):
            e = {k: v for k, v in os.environ.items() if k not in ("NX_DOUBLE_TAP_MS", "NX_TOUCH_GESTURES", "TEST_LOG")}
            e.update(env)
            out = subprocess.run([str(exe), sc], capture_output=True, text=True, env=e).stdout.split()
            return tuple(int(out[i]) for i in (1, 3, 5, 7)) if len(out) == 8 else None

        cases = [
            ("single", {}, (1, 1, 0, 0), "one tap: a touch, no A"),
            ("double", {}, (1, 1, 1, 0), "double tap: only the first touch, then A"),
            ("fast", {}, (1, 1, 1, 0), "a very quick double tap (20 ms each): A"),
            ("far", {}, (2, 2, 0, 0), "two taps far apart: two touches, no A"),
            ("slow", {}, (2, 2, 0, 0), "two taps 900 ms apart: two touches, no A"),
            ("triple", {}, (2, 2, 1, 0), "three quick taps: touch, A, touch"),
            ("drag", {}, (2, 2, 0, 0), "second finger moves: a drag, no A"),
            ("long", {}, (1, 1, 0, 1), "second finger kept still: long press (Y), no A"),
            ("double", {"NX_DOUBLE_TAP_MS": "0"}, (2, 2, 0, 0), "NX_DOUBLE_TAP_MS=0: two touches, no A"),
            ("double", {"NX_TOUCH_GESTURES": "0"}, (2, 2, 0, 0), "NX_TOUCH_GESTURES=0: two touches, no A"),
            ("medium", {}, (2, 2, 0, 0), "a 450 ms gap (default 300 ms): two touches, no A"),
            ("medium", {"NX_DOUBLE_TAP_MS": "600"}, (1, 1, 1, 0), "NX_DOUBLE_TAP_MS=600: the 450 ms gap is a double tap"),
        ]
        for sc, env, want, what in cases:
            got = run(sc, **env)
            ok = got == want
            fails += not ok
            print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"  (starts, ends, A, Y) = {got}, expected {want}"))
    print("all passed" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
