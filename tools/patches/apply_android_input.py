"""apply_android_input.py - Android touch screen as Tsukihime's own touch screen (AP_ANDROID_INPUT).

Phase 1 of the Android port. On Windows the mouse becomes the Switch touch screen
(AP_MOUSE + hle_hid_tsuki.cpp: a left click is a touch, a drag a held touch); the framework's Android input instead
drew a virtual stick and buttons (Wonder, Hades). For Tsukihime the finger is the touch screen itself:
  - runtime/src/platform.h: read_pointer is declared for Android too (a separate declaration: the _WIN32 one belongs
    to apply_mouse.py).
  - runtime/src/platform_android_input.cpp: "pointer mode" for games whose id starts with "tsukihime": the first finger
    is kept as a left-button mouse (screen pixels, landscape) and touches do not become pad buttons; read_pointer hands
    it over. Other games (Wonder, Hades) keep the drawn controls.
  - games/tsukihime_common/hle_hid_tsuki.cpp: the touch path (window -> game screen with plat::picture_rect, Start /
    held / End samples, the click on the title logo of AP_TITLE_CLICK) also runs on Android. A touch on the black bars
    counts too, moved to the nearest edge of the picture (NX_TOUCH_BARS=0: ignored, as on PC). The mouse buttons and
    wheel (mouse_pad) stay Windows only.
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
     '''#if defined(_WIN32)
    if (!mouse_on()) return s;
''',
     '''#if defined(_WIN32) || defined(__ANDROID__)  // AP_ANDROID_INPUT
    if (!mouse_on()) return s;
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
