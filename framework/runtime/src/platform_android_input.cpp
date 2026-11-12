#include <cstdlib>
// Android host (NativeActivity): memory (mmap at fixed addresses), pthreads, the app's window (ANativeWindow),
// game controllers + touch, AAudio, the Vulkan loader and android surface, a signal crash handler and
// android_main. Game data lives in the app's external files dir (see docs/ANDROID.md):
//   <files>/analysis/{main,sdk}.bin   module images (tools/nso2bin.py)
//   <files>/extract/romfs/...         the RomFS
//   <files>/user/...                  saves
//   <files>/config.txt                optional: resolution=WxH
#include <aaudio/AAudio.h>
#include <android/log.h>
#include <android/native_window.h>
#include <android/window.h>
#include <android_native_app_glue.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <jni.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <vector>

#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_ANDROID_KHR
#include <vulkan/vulkan.h>

#include <nx/game.h>
#include <nx/runtime.h>

#include "kernel.h"
#include "platform.h"
#include "platform_android.h"
#include "touch_layout.h"

// Android input: game controllers and touch (split from platform_android.cpp).
namespace nx::plat {

// ---- input: game controllers + touch --------------------------------------------------------------------------
// Touch: left half = virtual stick (relative to where the finger went down); right half = four buttons in a
// diamond around the thumb (Y attack up-left, X special up-right, B dash down-left, A interact down-right), top
// corners L / R, top centre +.

namespace {
std::mutex g_in;
PadState g_pad_buttons;  // controller state
struct Touch {
    int32_t id;
    float x0, y0, x, y;
    bool left;
    uint64_t button;
};
std::vector<Touch> g_touches;
float g_w = 1, g_h = 1;
bool g_gamepad = false;  // the last input came from a controller: the drawn touch controls hide

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

uint64_t key_to_button(int32_t k) {
    switch (k) {
        case AKEYCODE_BUTTON_A: return kB;  // bottom face button = Nintendo B
        case AKEYCODE_BUTTON_B: return kA;  // right face button = Nintendo A
        case AKEYCODE_BUTTON_X: return kY;
        case AKEYCODE_BUTTON_Y: return kX;
        case AKEYCODE_BUTTON_L1: return kL;
        case AKEYCODE_BUTTON_R1: return kR;
        case AKEYCODE_BUTTON_L2: return kZL;
        case AKEYCODE_BUTTON_R2: return kZR;
        case AKEYCODE_BUTTON_START: return kPlus;
        case AKEYCODE_BUTTON_SELECT: return kMinus;
        case AKEYCODE_BUTTON_THUMBL: return kStickL;
        case AKEYCODE_BUTTON_THUMBR: return kStickR;
        case AKEYCODE_DPAD_LEFT: return kLeft;
        case AKEYCODE_DPAD_UP: return kUp;
        case AKEYCODE_DPAD_RIGHT: return kRight;
        case AKEYCODE_DPAD_DOWN: return kDown;
        case AKEYCODE_BACK: return kMinus;
        default: return 0;
    }
}

uint64_t touch_button(float x, float y) {
    float nx_ = x / g_w, ny = y / g_h;
    if (ny < 0.2f) return nx_ < 0.6f ? (nx_ < 0.45f ? kL : kPlus) : (nx_ > 0.85f ? kR : kZR);
    bool up = ny < 0.6f, right = nx_ > 0.8f;
    if (up) return right ? kX : kY;
    return right ? kA : kB;
}

}  // namespace

int32_t on_input(android_app*, AInputEvent* e) {
    std::lock_guard lk(g_in);
    int32_t type = AInputEvent_getType(e);
    int32_t src = AInputEvent_getSource(e);
    if (type == AINPUT_EVENT_TYPE_KEY) {
        uint64_t b = key_to_button(AKeyEvent_getKeyCode(e));
        if (pointer_mode() && AKeyEvent_getKeyCode(e) == AKEYCODE_BACK) b = kB;  // AP_ANDROID_INPUT: Back = B (Tsukihime)
        if (!b) return 0;
        g_gamepad = true;
        if (AKeyEvent_getAction(e) == AKEY_EVENT_ACTION_DOWN)
            g_pad_buttons.buttons |= b;
        else if (AKeyEvent_getAction(e) == AKEY_EVENT_ACTION_UP)
            g_pad_buttons.buttons &= ~b;
        return 1;
    }
    if (type != AINPUT_EVENT_TYPE_MOTION) return 0;
    if ((src & AINPUT_SOURCE_JOYSTICK) == AINPUT_SOURCE_JOYSTICK) {
        g_gamepad = true;
        auto axis = [&](int32_t a) {
            float v = AMotionEvent_getAxisValue(e, a, 0);
            return std::fabs(v) < 0.15f ? 0 : int32_t(std::clamp(v, -1.f, 1.f) * kStickMax);
        };
        g_pad_buttons.lx = axis(AMOTION_EVENT_AXIS_X);
        g_pad_buttons.ly = -axis(AMOTION_EVENT_AXIS_Y);
        g_pad_buttons.rx = axis(AMOTION_EVENT_AXIS_Z);
        g_pad_buttons.ry = -axis(AMOTION_EVENT_AXIS_RZ);
        float hx = AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_HAT_X, 0);
        float hy = AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_HAT_Y, 0);
        g_pad_buttons.buttons &= ~(kLeft | kRight | kUp | kDown | kZL | kZR);
        if (hx < -0.5f) g_pad_buttons.buttons |= kLeft;
        if (hx > 0.5f) g_pad_buttons.buttons |= kRight;
        if (hy < -0.5f) g_pad_buttons.buttons |= kUp;
        if (hy > 0.5f) g_pad_buttons.buttons |= kDown;
        if (AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_LTRIGGER, 0) > 0.4f ||
            AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_BRAKE, 0) > 0.4f)
            g_pad_buttons.buttons |= kZL;
        if (AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_RTRIGGER, 0) > 0.4f ||
            AMotionEvent_getAxisValue(e, AMOTION_EVENT_AXIS_GAS, 0) > 0.4f)
            g_pad_buttons.buttons |= kZR;
        return 1;
    }
    if ((src & AINPUT_SOURCE_TOUCHSCREEN) != AINPUT_SOURCE_TOUCHSCREEN) return 0;
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
    if (!touch_enabled()) return 0;  // NX_TOUCH_SHOW=0
    int32_t action = AMotionEvent_getAction(e);
    int32_t kind = action & AMOTION_EVENT_ACTION_MASK;
    size_t index = size_t((action & AMOTION_EVENT_ACTION_POINTER_INDEX_MASK) >> AMOTION_EVENT_ACTION_POINTER_INDEX_SHIFT);
    if (ANativeWindow* w = android_window()) {
        g_w = float(ANativeWindow_getWidth(w));
        g_h = float(ANativeWindow_getHeight(w));
    }
    if (kind == AMOTION_EVENT_ACTION_DOWN || kind == AMOTION_EVENT_ACTION_POINTER_DOWN) {
        float x = AMotionEvent_getX(e, index), y = AMotionEvent_getY(e, index);
        g_gamepad = false;
        size_t drawn = 0;
        touch_buttons(drawn);
        if (drawn) {  // a drawn layout (touch_layout.cpp): its buttons first, the rest of the left half is the stick
            uint64_t b = touch_hit(x / g_w, y / g_h, g_w / g_h);
            bool left = !b && (touch_mirrored() ? x > g_w * 0.5f : x < g_w * 0.5f);
            if (b || left) g_touches.push_back({AMotionEvent_getPointerId(e, index), x, y, x, y, left, b});
        } else {
            bool left = x < g_w * 0.5f;
            g_touches.push_back({AMotionEvent_getPointerId(e, index), x, y, x, y, left, left ? 0 : touch_button(x, y)});
        }
    } else if (kind == AMOTION_EVENT_ACTION_UP || kind == AMOTION_EVENT_ACTION_POINTER_UP ||
               kind == AMOTION_EVENT_ACTION_CANCEL) {
        int32_t id = AMotionEvent_getPointerId(e, index);
        if (kind == AMOTION_EVENT_ACTION_UP || kind == AMOTION_EVENT_ACTION_CANCEL) g_touches.clear();
        g_touches.erase(std::remove_if(g_touches.begin(), g_touches.end(), [&](const Touch& t) { return t.id == id; }),
                        g_touches.end());
    } else if (kind == AMOTION_EVENT_ACTION_MOVE) {
        for (size_t k = 0; k < AMotionEvent_getPointerCount(e); k++) {
            int32_t id = AMotionEvent_getPointerId(e, k);
            for (auto& t : g_touches)
                if (t.id == id) {
                    t.x = AMotionEvent_getX(e, k);
                    t.y = AMotionEvent_getY(e, k);
                }
        }
    }
    return 1;
}

// AP_ANDROID_INPUT: the first finger as a left-button mouse over the screen (pointer mode, above). Touch coordinates
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
    static const bool on = std::getenv("NX_HEADLESS") != nullptr;
    return on;
}

void read_pad(PadState& p) {
    std::lock_guard lk(g_in);
    p.buttons |= g_pad_buttons.buttons;
    if (g_pad_buttons.lx || g_pad_buttons.ly) {
        p.lx = g_pad_buttons.lx;
        p.ly = g_pad_buttons.ly;
    }
    p.rx = g_pad_buttons.rx;
    p.ry = g_pad_buttons.ry;
    float radius = std::max(1.f, g_h * 0.12f);
    size_t drawn = 0;
    touch_buttons(drawn);
    for (const auto& t : g_touches) {
        if (t.left) {
            float dx = (t.x - t.x0) / radius, dy = (t.y - t.y0) / radius;
            p.lx = int32_t(std::clamp(dx, -1.f, 1.f) * kStickMax);
            p.ly = int32_t(std::clamp(-dy, -1.f, 1.f) * kStickMax);
            // A drawn layout's stick pushed nearly all the way holds Y too (Wonder: dash), so running needs no second
            // thumb on the right side.
            if (drawn && std::abs(dx) > 0.85f) p.buttons |= kY;
        } else {
            p.buttons |= t.button;
        }
    }
    input_script(p);  // NX_INPUT_SCRIPT (config.txt): scripted runs on the phone, as on the Mac
}

// The drawn layout's buttons (brighter while pressed) and the stick under the thumb; nothing while a controller is
// in use or for games without a drawn layout.
size_t touch_overlay(OverlayShape* out, size_t max) {
    size_t n = 0, k = 0;
    const TouchButton* b = touch_buttons(n);
    if (!n) return 0;
    std::lock_guard lk(g_in);
    if (g_gamepad || !touch_enabled()) return 0;
    const float shade = touch_opacity_scale();
    const Touch* stick = nullptr;
    uint64_t held = 0;
    for (const auto& t : g_touches) {
        if (t.left) stick = &t;
        held |= t.button;
    }
    for (size_t i = 0; i < n && k < max; i++) out[k++] = {b[i].x, b[i].y, b[i].r, std::min(1.0f, (held & b[i].button ? 0.5f : 0.2f) * shade)};
    if (stick && k + 2 <= max) {
        float r = 0.12f, dx = (stick->x - stick->x0) / g_h, dy = (stick->y - stick->y0) / g_h;
        float len = std::hypot(dx, dy), s = len > r ? r / len : 1.f;
        out[k++] = {stick->x0 / g_w, stick->y0 / g_h, r, std::min(1.0f, 0.14f * shade)};
        out[k++] = {(stick->x0 / g_h + dx * s) * g_h / g_w, stick->y0 / g_h + dy * s, 0.055f, std::min(1.0f, 0.45f * shade)};
    } else if (k < max) {
        out[k++] = {touch_mirrored() ? 0.85f : 0.15f, 0.72f, 0.12f, std::min(1.0f, 0.08f * shade)};  // where the stick goes
    }
    return k;
}

}  // namespace nx::plat
