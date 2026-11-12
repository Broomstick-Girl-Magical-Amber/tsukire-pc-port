"""apply_android_input.py - Android touch screen as Tsukihime's own touch screen, plus gestures (AP_ANDROID_INPUT).

Phase 1 of the Android port (claude/tsukihime-android.md); version 2 (2026-11-11, claude/tsukihime-android-gestos.md)
adds the gestures and the long press on the title logo. To update from version 1: --remove with the version 1 file
first, then apply this one.

On Windows the mouse becomes the Switch touch screen (AP_MOUSE + hle_hid_tsuki.cpp: a left click is a touch, a drag a
held touch); the framework's Android input instead drew a virtual stick and buttons (Wonder, Hades). For Tsukihime the
finger is the touch screen itself:
  - runtime/src/platform.h: read_pointer is declared for Android too (a separate declaration: the _WIN32 one belongs
    to apply_mouse.py), and read_gestures (Android only).
  - runtime/src/platform_android_input.cpp: "pointer mode" for games whose id starts with "tsukihime": the first finger
    is kept as a left-button mouse (screen pixels, landscape) and touches do not become pad buttons; read_pointer hands
    it over. Two-finger gestures are recognised there (tap, hold, swipe up / down / left / right) and counted;
    read_gestures hands them over. The Back button / gesture of Android is B there (not -). Other games (Wonder,
    Hades) keep the drawn controls and Back = -.
  - games/tsukihime_common/hle_hid_tsuki.cpp (Android): the touch path (window -> game screen with plat::picture_rect,
    Start / held / End samples) with gestures. A finger that goes down is held back until it is clear what it is:
    lifted (a tap: the game gets it then, held for as long as the finger was, 80-300 ms), moved (a drag: the game gets it from where it went down) or kept still for
    NX_LONG_PRESS_MS (500): a long press, which the game does not get: on the logo of the title menu it changes the
    video (AP_TITLE_CLICK), anywhere else it is Y (system menu). So a tap on the title logo is a normal touch (the
    Continue button under the logo on phones works). A second finger turns it into a gesture. Gestures -> buttons
    (pad hook): two-finger swipe left = X (dialogue menu). The rest the game already does by touch (hiding the text,
    fast forward while held; rewind, auto and fast forward in its mini menu at the top): the other two-finger
    gestures press nothing. A touch on the black bars counts too, moved to the nearest edge of the picture (NX_TOUCH_BARS=0: ignored,
    as on PC). NX_TOUCH_GESTURES=0: no gestures and no long press (every touch goes to the game at once; the title
    logo does nothing on Android). The mouse buttons and wheel (mouse_pad) stay Windows only.
Controllers (Bluetooth / USB) keep working as before (platform_android_input.cpp, Nintendo layout by position).

Idempotent (marker AP_ANDROID_INPUT), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Independent of the other AP_ANDROID_* patchers. Also edits ../tools/recomp_tsukihime.ps1 ($Markers rows,
anchored after the row of apply_game_window.py for platform_win.cpp, which no other patcher uses).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_input.py
"""
from _patch import run

MARK = "AP_ANDROID_INPUT"
PLATFORM_H = "runtime/src/platform.h"
INPUT = "runtime/src/platform_android_input.cpp"
HID = "games/tsukihime_common/hle_hid_tsuki.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ platform.h
    (PLATFORM_H,
     '''void read_pointer(PointerState& p);  // platform_win.cpp
#endif
''',
     '''void read_pointer(PointerState& p);  // platform_win.cpp
#endif
#if defined(__ANDROID__)  // AP_ANDROID_INPUT (apply_android_input.py): the first finger as a left-button mouse
void read_pointer(PointerState& p);  // platform_android_input.cpp (pointer mode, Tsukihime)
// Two-finger gestures of pointer mode (platform_android_input.cpp): counters that only grow (the reader keeps the last
// values it saw), whether two fingers are being held still now, and how many fingers are down.
struct TouchGestures {
    uint32_t taps = 0;           // two-finger taps
    uint32_t swipes[4] = {};     // two-finger swipes: up, down, left, right
    bool hold = false;           // two fingers held still (NX_TWO_HOLD_MS, 400 ms) and still down
    int32_t fingers = 0;
};
void read_gestures(TouchGestures& g);
#endif
'''),

    # ------------------------------------------------------------------------------------------------ platform_android_input.cpp
    (INPUT,
     '''bool g_gamepad = false;  // the last input came from a controller: the drawn touch controls hide
''',
     '''bool g_gamepad = false;  // the last input came from a controller: the drawn touch controls hide

// AP_ANDROID_INPUT (apply_android_input.py): pointer mode (Tsukihime). The first finger is the game's own touch
// screen, not pad buttons: read_pointer hands it over as a left-button mouse (x, y in screen pixels, landscape) and
// hle_hid_tsuki.cpp turns it into hid touch samples, as it does with the mouse on Windows.
struct Pointer {
    int32_t id = -1;
    float x = 0, y = 0;
    bool down = false;
    uint32_t downs = 0;  // presses so far (a tap shorter than a frame still shows)
} g_ptr;
bool pointer_mode() {
    static const bool on = std::strncmp(game::kInfo.id, "tsukihime", 9) == 0;
    return on;
}

// Two-finger gestures (pointer mode): every finger down, and the two-finger stroke in progress. Distances are
// fractions of the screen's short side; times in ns (the events' clock, CLOCK_MONOTONIC).
struct Finger {
    int32_t id;
    float x, y;
};
std::vector<Finger> g_fingers;
struct Multi {
    bool on = false;      // two fingers went down and not all are up yet
    bool hold = false;    // held still long enough: a hold (no tap or swipe at the end)
    bool moved = false;   // went further than a tap
    int64_t t0 = 0;
    float cx0 = 0, cy0 = 0, cx = 0, cy = 0;  // centre of the first two fingers: at the start, now
} g_multi;
TouchGestures g_gest;
int64_t monotonic_now() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}
int64_t env_ms(const char* name, int64_t fallback) {
    const char* e = std::getenv(name);
    return (e && *e ? std::max<int64_t>(50, std::atoll(e)) : fallback) * 1000000;
}
void multi_update(int64_t t) {  // after each touch event, with g_fingers up to date
    const float side = std::max(1.f, std::min(g_w, g_h));
    if (g_fingers.size() >= 2) {
        const float cx = (g_fingers[0].x + g_fingers[1].x) * 0.5f / side, cy = (g_fingers[0].y + g_fingers[1].y) * 0.5f / side;
        if (!g_multi.on) g_multi = Multi{}, g_multi.on = true, g_multi.t0 = t, g_multi.cx0 = cx, g_multi.cy0 = cy;
        g_multi.cx = cx, g_multi.cy = cy;
        if (std::hypot(cx - g_multi.cx0, cy - g_multi.cy0) > 0.06f) g_multi.moved = true;
    }
    if (g_multi.on && g_fingers.empty()) {  // the stroke ends: a tap, a swipe, or nothing (a hold, a slow drag)
        static const int64_t tap_ns = env_ms("NX_TWO_TAP_MS", 350);
        const float dx = g_multi.cx - g_multi.cx0, dy = g_multi.cy - g_multi.cy0;
        if (!g_multi.hold && !g_multi.moved && t - g_multi.t0 <= tap_ns) {
            g_gest.taps++;
        } else if (!g_multi.hold && std::hypot(dx, dy) >= 0.12f) {
            const int dir = std::fabs(dy) >= std::fabs(dx) ? (dy < 0 ? 0 : 1) : (dx < 0 ? 2 : 3);
            g_gest.swipes[dir]++;
        }
        g_multi = Multi{};
    }
}
'''),
    (INPUT,
     '''    if (type == AINPUT_EVENT_TYPE_KEY) {
        uint64_t b = key_to_button(AKeyEvent_getKeyCode(e));
''',
     '''    if (type == AINPUT_EVENT_TYPE_KEY) {
        uint64_t b = key_to_button(AKeyEvent_getKeyCode(e));
        if (pointer_mode() && AKeyEvent_getKeyCode(e) == AKEYCODE_BACK) b = kB;  // AP_ANDROID_INPUT: Back = B (Tsukihime)
'''),
    (INPUT,
     '''    if ((src & AINPUT_SOURCE_TOUCHSCREEN) != AINPUT_SOURCE_TOUCHSCREEN) return 0;
''',
     '''    if ((src & AINPUT_SOURCE_TOUCHSCREEN) != AINPUT_SOURCE_TOUCHSCREEN) return 0;
    if (pointer_mode()) {  // AP_ANDROID_INPUT: touch goes to the game as touch, no virtual stick or buttons
        const int32_t pact = AMotionEvent_getAction(e);
        const int32_t pkind = pact & AMOTION_EVENT_ACTION_MASK;
        const size_t pidx =
            size_t((pact & AMOTION_EVENT_ACTION_POINTER_INDEX_MASK) >> AMOTION_EVENT_ACTION_POINTER_INDEX_SHIFT);
        if (ANativeWindow* w = android_window()) g_w = float(ANativeWindow_getWidth(w)), g_h = float(ANativeWindow_getHeight(w));
        if (pkind == AMOTION_EVENT_ACTION_DOWN || (pkind == AMOTION_EVENT_ACTION_POINTER_DOWN && !g_ptr.down)) {
            g_ptr.id = AMotionEvent_getPointerId(e, pidx);
            g_ptr.x = AMotionEvent_getX(e, pidx), g_ptr.y = AMotionEvent_getY(e, pidx);
            g_ptr.down = true;
            g_ptr.downs++;
        } else if (pkind == AMOTION_EVENT_ACTION_UP || pkind == AMOTION_EVENT_ACTION_CANCEL ||
                   (pkind == AMOTION_EVENT_ACTION_POINTER_UP && AMotionEvent_getPointerId(e, pidx) == g_ptr.id)) {
            g_ptr.down = false;
        } else if (pkind == AMOTION_EVENT_ACTION_MOVE && g_ptr.down) {
            for (size_t k = 0; k < AMotionEvent_getPointerCount(e); k++)
                if (AMotionEvent_getPointerId(e, k) == g_ptr.id) g_ptr.x = AMotionEvent_getX(e, k), g_ptr.y = AMotionEvent_getY(e, k);
        }
        // every finger, for the two-finger gestures
        if (pkind == AMOTION_EVENT_ACTION_UP || pkind == AMOTION_EVENT_ACTION_CANCEL) {
            g_fingers.clear();
            if (pkind == AMOTION_EVENT_ACTION_CANCEL) g_multi = Multi{};  // the system took the touch: no gesture
        } else {
            g_fingers.clear();
            for (size_t k = 0; k < AMotionEvent_getPointerCount(e); k++) {
                if (pkind == AMOTION_EVENT_ACTION_POINTER_UP && k == pidx) continue;
                g_fingers.push_back({AMotionEvent_getPointerId(e, k), AMotionEvent_getX(e, k), AMotionEvent_getY(e, k)});
            }
        }
        multi_update(int64_t(AMotionEvent_getEventTime(e)));
        return 1;
    }
'''),
    (INPUT,
     '''bool headless() {  // NX_HEADLESS (platform.h)
''',
     '''// AP_ANDROID_INPUT: the first finger as a left-button mouse over the screen (pointer mode, above). Touch coordinates
// are in the display's landscape space; w, h follow that convention so picture_rect gets the same aspect.
void read_pointer(PointerState& p) {
    std::lock_guard lk(g_in);
    p.x = int32_t(g_ptr.x);
    p.y = int32_t(g_ptr.y);
    p.w = uint32_t(std::max(g_w, g_h));
    p.h = uint32_t(std::min(g_w, g_h));
    p.buttons = g_ptr.down ? 1u : 0u;
    p.downs[0] = g_ptr.downs;
    p.downs[1] = p.downs[2] = 0;
    p.wheel = 0;
}

// AP_ANDROID_INPUT: the two-finger gestures (above). A hold starts once two fingers have stayed still for
// NX_TWO_HOLD_MS (checked here too: a still finger sends no events).
void read_gestures(TouchGestures& g) {
    std::lock_guard lk(g_in);
    static const int64_t hold_ns = env_ms("NX_TWO_HOLD_MS", 400);
    if (g_multi.on && !g_multi.moved && g_fingers.size() >= 2 && monotonic_now() - g_multi.t0 >= hold_ns) g_multi.hold = true;
    g = g_gest;
    g.hold = g_multi.on && g_multi.hold && g_fingers.size() >= 2;
    g.fingers = int32_t(g_fingers.size());
}

bool headless() {  // NX_HEADLESS (platform.h)
'''),

    # ------------------------------------------------------------------------------------------------ hle_hid_tsuki.cpp
    (HID,
     '''#if defined(_WIN32)
// Ventana -> pantalla del juego: el rectangulo donde el framework presenta la imagen (plat::picture_rect,
''',
     '''#if defined(_WIN32) || defined(__ANDROID__)  // AP_ANDROID_INPUT: on Android a finger is the left button
// Ventana -> pantalla del juego: el rectangulo donde el framework presenta la imagen (plat::picture_rect,
'''),
    (HID,
     '''    double u = double(p.x - r.x) / r.w, v = double(p.y - r.y) / r.h;
    return {u >= 0 && u < 1 && v >= 0 && v < 1, std::clamp(int32_t(u * kTouchW), 0, kTouchW - 1),
''',
     '''    double u = double(p.x - r.x) / r.w, v = double(p.y - r.y) / r.h;
#if defined(__ANDROID__)
    // AP_ANDROID_INPUT: a touch on the black bars counts too, moved to the nearest edge of the picture (the clamps
    // below). NX_TOUCH_BARS=0: ignored, as on PC.
    static const bool bars = env_on("NX_TOUCH_BARS", true);
#else
    constexpr bool bars = false;
#endif
    return {bars || (u >= 0 && u < 1 && v >= 0 && v < 1), std::clamp(int32_t(u * kTouchW), 0, kTouchW - 1),
'''),
    (HID,
     '''    s.x = g_touch_x, s.y = g_touch_y;
#endif
    return s;
}
''',
     '''    s.x = g_touch_x, s.y = g_touch_y;
#elif defined(__ANDROID__)
    // AP_ANDROID_INPUT (apply_android_input.py): a finger. Held back until it is clear what it is: lifted (a tap, the
    // game gets it then, pressed as long as the finger was), moved (a drag, the game gets it from where it went down) or kept still for
    // NX_LONG_PRESS_MS (a long press: the title logo changes the video, anywhere else Y; the game never sees it).
    // A second finger makes it a gesture (platform_android_input.cpp; buttons in touch_pad): a touch already sent ends.
    if (!mouse_on()) return s;
    plat::PointerState p;
    plat::read_pointer(p);
    plat::TouchGestures gs;
    plat::read_gestures(gs);
    if (!g_touch_init) g_touch_init = true, g_touch_seen_downs = p.downs[0];
    const ScreenPoint pt = to_screen(p);
    const bool held = p.buttons & 1, new_down = p.downs[0] != g_touch_seen_downs;
    enum { kIdle, kPending, kTouching, kReplay, kGesture };
    static int state = kIdle;
    static int32_t sx = 0, sy = 0, wx = 0, wy = 0;  // where it went down: game screen, window pixels
    static uint64_t t0 = 0, replay_end = 0;  // kReplay: a tap given to the game as it was pressed, until replay_end
    static const bool gestures = env_on("NX_TOUCH_GESTURES", true);
    static const uint64_t long_ns = [] {
        const char* e = std::getenv("NX_LONG_PRESS_MS");
        return uint64_t(e && *e ? std::max(150, std::atoi(e)) : 500) * 1000000;
    }();
    auto start = [&] {
        state = kTouching;
        g_touching = true;
        g_touch_x = sx, g_touch_y = sy;
        s.count = 1, s.attr = kAttrStart;
        if (trace_on()) log("hid: toque en %d,%d", sx, sy);
    };
    if (gestures && gs.fingers >= 2 && (state == kPending || state == kTouching || state == kReplay)) {
        if (state == kTouching || state == kReplay) s.count = 1, s.attr = kAttrEnd;  // a touch already sent ends where it was
        g_touching = false;
        state = kGesture;
        if (trace_on()) log("hid: segundo dedo: gesto, no toque");
    } else if (state == kIdle) {
        if (new_down) {
            g_touch_seen_downs = p.downs[0];
            if (pt.inside) {
                sx = pt.x, sy = pt.y, wx = p.x, wy = p.y, t0 = plat::monotonic_ns();
                state = kPending;
                if (!gestures) start();
            }
        }
    } else if (state == kPending) {
        const float moved = std::hypot(float(p.x - wx), float(p.y - wy)) / float(std::max(1u, p.h));
        if (!held || new_down) {
            // A tap: the game gets it as it was, pressed for as long as the finger was (at least 80 ms, at most
            // 300 ms): Start, held samples, then End. Start followed at once by End is seen (the button lights up)
            // but not acted upon.
            const uint64_t now = plat::monotonic_ns();
            replay_end = now + std::clamp<uint64_t>(now - t0, 80000000, 300000000);
            start();
            state = kReplay;
        } else if (moved > 0.03f) {
            start();  // a drag: followed from the next sample
        } else if (plat::monotonic_ns() - t0 >= long_ns) {
            state = kGesture;
            const ScreenPoint at{true, sx, sy};
            if (title_logo_click(at)) {
                if (trace_on()) log("hid: toque largo en el logo del titulo (%d,%d): cambia el video", sx, sy);
            } else {
                g_long_press.store(true, std::memory_order_relaxed);
                if (trace_on()) log("hid: toque largo en %d,%d: Y", sx, sy);
            }
        }
    } else if (state == kReplay) {
        if (plat::monotonic_ns() < replay_end) {
            s.count = 1;  // still pressed, where it went down
        } else {
            g_touching = false;
            state = kIdle;
            s.count = 1, s.attr = kAttrEnd;
            if (trace_on()) log("hid: fin del toque en %d,%d", g_touch_x, g_touch_y);
        }
    } else if (state == kTouching) {
        if (held && !new_down) {
            g_touch_x = pt.x, g_touch_y = pt.y;  // arrastre: sigue al dedo, limitado a la pantalla
            s.count = 1;
        } else {
            g_touching = false;
            state = kIdle;
            s.count = 1, s.attr = kAttrEnd;
            if (trace_on()) log("hid: fin del toque en %d,%d", g_touch_x, g_touch_y);
        }
    } else if (!held && gs.fingers == 0) {  // kGesture: over once every finger is up
        g_touch_seen_downs = p.downs[0];
        state = kIdle;
    }
    s.x = g_touch_x, s.y = g_touch_y;
#endif
    return s;
}
'''),
    (HID,
     '''void tsuki_pad_hook(plat::PadState& p) {
#if defined(_WIN32)
    mouse_pad(p);
#endif
''',
     '''void tsuki_pad_hook(plat::PadState& p) {
#if defined(_WIN32)
    mouse_pad(p);
#endif
#if defined(__ANDROID__)
    touch_pad(p);  // AP_ANDROID_INPUT: the touch gestures
#endif
'''),
    (HID,
     '''// Traza: cada boton del mando que cambia (teclado, mando y mouse juntos), para ver que hace el juego con cada uno.
''',
     '''#if defined(__ANDROID__)
// AP_ANDROID_INPUT (apply_android_input.py): gestures -> buttons of the pad. A long press (next_touch_sample) = Y;
// two fingers swiped left = X. Each one a 120 ms press. The other two-finger gestures press nothing: the game already
// does those by touch (hiding the text, fast forward while held; rewind, auto and fast forward in its mini menu).
std::atomic<bool> g_long_press{false};
bool gestures_on() {
    static const bool on = env_on("NX_TOUCH_GESTURES", true);
    return on;
}
void touch_pad(plat::PadState& p) {
    if (!mouse_on() || !gestures_on()) return;
    plat::TouchGestures g;
    plat::read_gestures(g);
    static std::mutex m;
    std::lock_guard lk(m);
    constexpr uint64_t kPress = 120000000;
    static bool init = false;
    static plat::TouchGestures seen;
    static uint64_t until[6] = {};  // long press, tap, swipe up, down, left, right
    constexpr uint64_t kBits[6] = {plat::kY, 0, 0, 0, plat::kX, 0};
    if (!init) init = true, seen = g;
    const uint64_t now = plat::monotonic_ns();
    if (g_long_press.exchange(false, std::memory_order_relaxed)) until[0] = now + kPress;
    if (g.taps != seen.taps) until[1] = now + kPress;
    for (int d = 0; d < 4; d++)
        if (g.swipes[d] != seen.swipes[d]) until[2 + d] = now + kPress;
    seen = g;
    for (int k = 0; k < 6; k++)
        if (now < until[k]) p.buttons |= kBits[k];
}
#endif

// Traza: cada boton del mando que cambia (teclado, mando y mouse juntos), para ver que hace el juego con cada uno.
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     '''    @('both', 'framework\\runtime\\src\\platform_win.cpp', 'AP_WINDOW', 'apply_game_window.py', $false),
''',
     '''    @('both', 'framework\\runtime\\src\\platform_win.cpp', 'AP_WINDOW', 'apply_game_window.py', $false),
    @('both', 'framework\\runtime\\src\\platform.h', 'AP_ANDROID_INPUT', 'apply_android_input.py', $false),
    @('both', 'framework\\runtime\\src\\platform_android_input.cpp', 'AP_ANDROID_INPUT', 'apply_android_input.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_ANDROID_INPUT', 'apply_android_input.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
