"""apply_android_double_tap.py - Android: a double tap presses A, confirm (AP_ANDROID_DOUBLE_TAP).

Asked by Alberto (2026-11-13): in the Android game (not the launcher, not Windows), a double tap = confirm. The first
tap goes to the game as always, at once (no wait: a single tap is not delayed). If a second tap goes down within
NX_DOUBLE_TAP_MS (300 ms) of the end of the first one and near it (5 % of the screen height), the game does not get
that second touch: when the finger is lifted, A (confirm) is pressed for 120 ms instead. So in a menu the first tap
chooses and the second confirms; reading, a double tap advances twice, as before. The second finger only counts if
it is a tap: if it moves it is a drag and if it stays NX_LONG_PRESS_MS it is the long press (Y, or the title logo),
both as before. A third quick tap is a normal tap again (it starts a new pair).
NX_DOUBLE_TAP_MS=0 (config.txt) turns it off; NX_TOUCH_GESTURES=0 turns it off with the other gestures.

Only games/tsukihime_common/hle_hid_tsuki.cpp, Android path (AP_ANDROID_INPUT): the touch state machine of
next_touch_sample and touch_pad (the press). Windows and controllers do not change. Also edits
../tools/recomp_tsukihime.ps1 (a $Markers row, after the one of apply_carga_cache.py). Needs apply_android_input.py
(version 2). Idempotent (marker AP_ANDROID_DOUBLE_TAP), all or nothing, keeps CRLF and BOM, --remove leaves the
files byte for byte as they were. Then rebuild the game library for Android (tools\\android\\build_apk.ps1 -Edition
<edition>, and a new Android pack for a release).
Test: python tools/tests/test_android_double_tap.py
Version 2 (2026-11-13): also logs, once, whether the double tap is on (and its NX_DOUBLE_TAP_MS), and with
NX_INPUT_TRACE=1, for every finger after a tap, how many ms and px it is from that tap and whether it was taken. To
update from version 1: run the version 1 file with --remove first, then apply this one.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_double_tap.py
"""
from _patch import run

MARK = "AP_ANDROID_DOUBLE_TAP"
HID = "games/tsukihime_common/hle_hid_tsuki.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ touch_pad
    (HID,
     '''std::atomic<bool> g_long_press{false};
''',
     '''std::atomic<bool> g_long_press{false};
std::atomic<bool> g_double_tap{false};  // AP_ANDROID_DOUBLE_TAP: the second tap of a double tap = A (confirm)
'''),
    (HID,
     '''    static uint64_t until[6] = {};  // long press, tap, swipe up, down, left, right
    constexpr uint64_t kBits[6] = {plat::kY, 0, 0, 0, plat::kX, 0};
''',
     '''    static uint64_t until[7] = {};  // long press, tap, swipe up, down, left, right, double tap (AP_ANDROID_DOUBLE_TAP)
    constexpr uint64_t kBits[7] = {plat::kY, 0, 0, 0, plat::kX, 0, plat::kA};
'''),
    (HID,
     '''    if (g_long_press.exchange(false, std::memory_order_relaxed)) until[0] = now + kPress;
''',
     '''    if (g_long_press.exchange(false, std::memory_order_relaxed)) until[0] = now + kPress;
    if (g_double_tap.exchange(false, std::memory_order_relaxed)) until[6] = now + kPress;  // AP_ANDROID_DOUBLE_TAP
'''),
    (HID,
     '''    for (int k = 0; k < 6; k++)
''',
     '''    for (int k = 0; k < 7; k++)  // AP_ANDROID_DOUBLE_TAP: 7
'''),

    # ------------------------------------------------------------------------------------------------ next_touch_sample
    (HID,
     '''    static uint64_t t0 = 0, replay_end = 0;  // kReplay: a tap given to the game as it was pressed, until replay_end
''',
     '''    static uint64_t t0 = 0, replay_end = 0;  // kReplay: a tap given to the game as it was pressed, until replay_end
    // AP_ANDROID_DOUBLE_TAP: a finger that goes down within NX_DOUBLE_TAP_MS (300; 0 = off) of the end of the last
    // tap, and near it (5 % of the screen height), is the second tap of a double tap: lifted, it presses A (confirm)
    // and the game gets no touch. Moved it is a drag, kept still the long press, as any other finger.
    static uint64_t tap_end = 0;  // when the game got the End of the last tap (0: no tap to pair with)
    static int32_t tap_x = 0, tap_y = 0;
    static bool second = false;  // the finger in kPending is the second tap of a pair
    static const uint64_t double_ns = [] {
        const char* e = std::getenv("NX_DOUBLE_TAP_MS");
        return uint64_t(e && *e ? std::clamp(std::atoi(e), 0, 1000) : 300) * 1000000;
    }();
'''),
    (HID,
     '''                sx = pt.x, sy = pt.y, wx = p.x, wy = p.y, t0 = plat::monotonic_ns();
                state = kPending;
''',
     '''                sx = pt.x, sy = pt.y, wx = p.x, wy = p.y, t0 = plat::monotonic_ns();
                state = kPending;
                // AP_ANDROID_DOUBLE_TAP (v2 also logs: once that it is on, and with NX_INPUT_TRACE=1 how far in time
                // and place each finger is from the last tap, to see why a double tap was or was not taken)
                const uint64_t gap = tap_end ? t0 - tap_end : 0;
                const float dist = std::hypot(float(sx - tap_x), float(sy - tap_y));
                second = gestures && double_ns && tap_end && gap <= double_ns && dist <= 0.05f * kTouchH;
                static bool said = false;
                if (!said) {
                    said = true;
                    log("hid: doble toque = A: %s (NX_DOUBLE_TAP_MS %llu, hasta %.0f px)",
                        gestures && double_ns ? "activo" : "apagado", (unsigned long long)(double_ns / 1000000),
                        0.05f * kTouchH);
                }
                if (tap_end && trace_on())
                    log("hid: dedo en %d,%d a %llu ms y %.0f px del toque anterior: %s", sx, sy,
                        (unsigned long long)(gap / 1000000), dist, second ? "segundo toque (doble)" : "no es doble");
                tap_end = 0;
'''),
    (HID,
     '''        if (!held || new_down) {
            // A tap: the game gets it as it was, pressed for as long as the finger was (at least 80 ms, at most
''',
     '''        if ((!held || new_down) && second) {
            // AP_ANDROID_DOUBLE_TAP: the second tap of a double tap: A (touch_pad), the game gets no touch. A finger
            // already down again (new_down) is seen in kIdle, as a new touch.
            second = false;
            state = kIdle;
            g_double_tap.store(true, std::memory_order_relaxed);
            if (trace_on()) log("hid: doble toque en %d,%d: A", sx, sy);
        } else if (!held || new_down) {
            // A tap: the game gets it as it was, pressed for as long as the finger was (at least 80 ms, at most
'''),
    (HID,
     '''            s.count = 1;  // still pressed, where it went down
        } else {
            g_touching = false;
            state = kIdle;
''',
     '''            s.count = 1;  // still pressed, where it went down
        } else {
            g_touching = false;
            state = kIdle;
            tap_end = plat::monotonic_ns(), tap_x = sx, tap_y = sy;  // AP_ANDROID_DOUBLE_TAP: a tap to pair with
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_CARGA_CACHE', 'apply_carga_cache.py', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_CARGA_CACHE', 'apply_carga_cache.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_ANDROID_DOUBLE_TAP', 'apply_android_double_tap.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
