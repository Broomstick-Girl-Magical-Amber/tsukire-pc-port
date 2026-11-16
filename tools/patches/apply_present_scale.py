"""apply_present_scale.py - on Android the picture goes to the screen at the game's own height, and the compositor
scales it to the screen (AP_PRESENT_SCALE).

Why: the present pass (nvn_present.cpp) drew every frame at the screen's own size. On a phone with a 2316x1080 screen
that was ~6.8 ms of GPU time a frame (a Mali-G77 that the system holds at 260-377 MHz of 800), ~40 % of a text
scene's frame, to scale a 1280x720 picture. Measured with apply_gpu_pass_diag.py (NX_GPU_PROFILE=2). Phones often
cap their GPU clock (heat, power policy), and an app cannot lift that: the cheaper the frame, the better.

What changes (Android only):
  - runtime/src/platform_android.cpp:
    - When a window arrives (APP_CMD_INIT_WINDOW) its buffers get the game's height on their short side, the
      screen's aspect kept (ANativeWindow_setBuffersGeometry): 1544x720 on a 2316x1080 screen. Vulkan then reports
      that size as the surface's (currentExtent), the swapchain is made at it, and the compositor (SurfaceFlinger,
      usually the display hardware) scales it to the screen. With the bars of a 16:9 picture on a wider screen, the
      1280x720 picture is now copied 1:1.
    - The height: NX_PRESENT_HEIGHT=<pixels> (environment or config.txt); 0 = the screen's own size, as before.
      Unset: the game's own height (NX_RENDER_RES's, else 720 handheld / 1080 docked). Never more than the screen.
    - ANativeWindow_getWidth/Height give the buffers' size once they are set, while touch coordinates stay in the
      window's own pixels: the window's own size is read before the buffers are resized and kept;
      window_pixel_width/height give it, and screen_size uses it (a macro in this file and in
      platform_android_input.cpp turns the two getters into those).
    - APP_CMD_WINDOW_RESIZED (a foldable, split screen): the window's own size is read again; if it changed, the
      buffers get the new size and the swapchain is made again (as for a new window).
    - Logged when it changes: "present: the screen is WxH; the window's buffers are wxh (NX_PRESENT_HEIGHT h)...".
  - runtime/src/platform_android_input.cpp: touch uses the window's own size (the macro above), not the buffers'.
  - ../tools/recomp_tsukihime.ps1: its $Markers rows, after the row of hud_android.cpp (apply_android_hud.py), which
    no other patcher uses.
The touch HUD and the side bars are drawn by the present pass, so they are drawn at the smaller size too (a bit
softer); the game's picture is not: it was 720p already.

  python tools\\patches\\apply_present_scale.py            then rebuild the Android library
                                                           (tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_present_scale.py --remove   undoes it (byte for byte)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_PRESENT_SCALE"

PLAT = "runtime/src/platform_android.cpp"
INPUT = "runtime/src/platform_android_input.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

PLAT_INC_OLD = """#include "platform_android.h"

#ifndef MAP_FIXED_NOREPLACE
"""
PLAT_INC_NEW = """#include "platform_android.h"

// AP_PRESENT_SCALE (apply_present_scale.py): the window's buffers may be smaller than the window (NX_PRESENT_HEIGHT),
// and then ANativeWindow_getWidth/Height give the buffers' size; the window's own pixels (touch coordinates,
// screen_size) are kept below. (ANativeWindow_getWidth)(w), in parentheses, is the real getter.
namespace nx::plat {
int32_t window_pixel_width(ANativeWindow* w);
int32_t window_pixel_height(ANativeWindow* w);
}  // namespace nx::plat
#define ANativeWindow_getWidth(w) nx::plat::window_pixel_width(w)
#define ANativeWindow_getHeight(w) nx::plat::window_pixel_height(w)

#ifndef MAP_FIXED_NOREPLACE
"""

PLAT_DEF_OLD = """std::atomic<bool> g_visible{false};
}  // namespace
"""
PLAT_DEF_NEW = """std::atomic<bool> g_visible{false};
}  // namespace

// AP_PRESENT_SCALE (apply_present_scale.py): the picture goes to the screen at most at the game's own height
// (NX_PRESENT_HEIGHT=<pixels>; 0 = the screen's own size; unset: NX_RENDER_RES's height, else 720 handheld / 1080
// docked). The window's buffers get that size on their short side (ANativeWindow_setBuffersGeometry), Vulkan reports
// it as the surface's, and the compositor scales the buffers to the screen at no GPU cost: the present pass at
// 2316x1080 took ~6.8 ms a frame on a Mali-G77 held at 260-377 MHz. The window's own size is read first and kept.
namespace {
std::atomic<ANativeWindow*> g_pixels_of{nullptr};
std::atomic<int32_t> g_pixels_w{0}, g_pixels_h{0};

int32_t present_height() {
    if (const char* e = std::getenv("NX_PRESENT_HEIGHT"); e && *e) return int32_t(std::atoi(e));
    if (const char* r = std::getenv("NX_RENDER_RES")) {
        unsigned rw = 0, rh = 0;
        if (std::sscanf(r, "%ux%u", &rw, &rh) == 2 && rw && rh) return int32_t(rh);
    }
    return std::getenv("NX_HANDHELD") ? 720 : 1080;
}
}  // namespace

int32_t window_pixel_width(ANativeWindow* w) {
    return w && w == g_pixels_of.load() ? g_pixels_w.load() : (ANativeWindow_getWidth)(w);
}

int32_t window_pixel_height(ANativeWindow* w) {
    return w && w == g_pixels_of.load() ? g_pixels_h.load() : (ANativeWindow_getHeight)(w);
}

// Sizes the window's buffers (on the UI thread, before the game sees a new window). True when the window's own size
// changed since the last call for the same window (then the swapchain must be made again).
bool set_present_geometry(ANativeWindow* w) {
    if (!w) return false;
    ANativeWindow_setBuffersGeometry(w, 0, 0, 0);  // the window's own size again, to read it
    const int32_t pw = (ANativeWindow_getWidth)(w), ph = (ANativeWindow_getHeight)(w);
    const bool changed = g_pixels_of.load() == w && (pw != g_pixels_w.load() || ph != g_pixels_h.load());
    g_pixels_w = pw, g_pixels_h = ph;
    g_pixels_of = w;
    if (pw <= 0 || ph <= 0) return changed;
    const int32_t shortest = std::min(pw, ph), target = present_height();
    int32_t bw = pw, bh = ph, err = 0;
    if (target > 0 && target < shortest) {
        const double s = double(target) / double(shortest);
        bw = std::max<int32_t>(2, int32_t(double(pw) * s + 0.5) & ~1);
        bh = std::max<int32_t>(2, int32_t(double(ph) * s + 0.5) & ~1);
        err = ANativeWindow_setBuffersGeometry(w, bw, bh, 0);
        if (err) bw = pw, bh = ph;
    }
    static int32_t logged[4] = {-1, -1, -1, -1};
    if (logged[0] != pw || logged[1] != ph || logged[2] != bw || logged[3] != bh) {
        logged[0] = pw, logged[1] = ph, logged[2] = bw, logged[3] = bh;
        if (bw == pw && bh == ph)
            nx::log("present: the screen is %dx%d; the window's buffers keep that size (NX_PRESENT_HEIGHT %d%s)", pw, ph,
                    target, err ? ", refused by the window" : "");
        else
            nx::log("present: the screen is %dx%d; the window's buffers are %dx%d (NX_PRESENT_HEIGHT %d), the compositor "
                    "scales them",
                    pw, ph, bw, bh, target);
    }
    return changed;
}
"""

PLAT_CMD_OLD = """        case APP_CMD_INIT_WINDOW:
            g_window = app->window;
            g_window_gen++;
            g_visible = true;
            request_immersive();
            break;
        case APP_CMD_TERM_WINDOW:  // the window goes away once this returns
"""
PLAT_CMD_NEW = """        case APP_CMD_INIT_WINDOW:
            set_present_geometry(app->window);  // AP_PRESENT_SCALE: before the game sees the window
            g_window = app->window;
            g_window_gen++;
            g_visible = true;
            request_immersive();
            break;
        case APP_CMD_WINDOW_RESIZED:  // AP_PRESENT_SCALE: a new screen size (a foldable, split screen): new buffers
            if (app->window && app->window == g_window.load() && set_present_geometry(app->window)) g_window_gen++;
            break;
        case APP_CMD_TERM_WINDOW:  // the window goes away once this returns
"""

INPUT_OLD = """#include "platform_android.h"
#include "touch_layout.h"

"""
INPUT_NEW = """#include "platform_android.h"
#include "touch_layout.h"

// AP_PRESENT_SCALE (apply_present_scale.py): the window's buffers may be smaller than the window (NX_PRESENT_HEIGHT);
// touch coordinates are in the window's own pixels, which platform_android.cpp keeps.
namespace nx::plat {
int32_t window_pixel_width(ANativeWindow* w);
int32_t window_pixel_height(ANativeWindow* w);
}  // namespace nx::plat
#define ANativeWindow_getWidth(w) nx::plat::window_pixel_width(w)
#define ANativeWindow_getHeight(w) nx::plat::window_pixel_height(w)

"""

RECO_OLD = """    @('both', 'framework\\games\\tsukihime_common\\hud_android.cpp', 'AP_ANDROID_HUD', 'hud_android.cpp (copy it to games\\tsukihime_common)', $false),
"""
RECO_NEW = """    @('both', 'framework\\games\\tsukihime_common\\hud_android.cpp', 'AP_ANDROID_HUD', 'hud_android.cpp (copy it to games\\tsukihime_common)', $false),
    @('both', 'framework\\runtime\\src\\platform_android.cpp', 'AP_PRESENT_SCALE', 'apply_present_scale.py', $false),
    @('both', 'framework\\runtime\\src\\platform_android_input.cpp', 'AP_PRESENT_SCALE', 'apply_present_scale.py', $false),
"""

EDITS = [
    (PLAT, PLAT_INC_OLD, PLAT_INC_NEW),
    (PLAT, PLAT_DEF_OLD, PLAT_DEF_NEW),
    (PLAT, PLAT_CMD_OLD, PLAT_CMD_NEW),
    (INPUT, INPUT_OLD, INPUT_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)