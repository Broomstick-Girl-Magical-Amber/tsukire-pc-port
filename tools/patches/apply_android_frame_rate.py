"""apply_android_frame_rate.py - on Android the window is told the game's frame rate (AP_FRAME_RATE).

Why: the game makes 60 frames a second (pace_present, nvn_queue.cpp) and the runtime never said so to Android. The
system then chooses the screen's refresh rate on its own: a 90 Hz screen left at 90 shows 60 frames unevenly (one
refresh, then two), and a 120 or 144 Hz screen may stay at its highest rate, where the panel and the compositor work
twice as often as there are new frames. With the rate declared (ANativeWindow_setFrameRate, Android 11, which is
the app's minimum), Android can pick a refresh rate the frames fit in evenly: 60 or a multiple of it. What it picks is
the system's choice; some phones keep their own rule for games.

What changes (Android only, runtime/src/platform_android.cpp):
  - vulkan_create_surface (every time a surface is made for a window: the first one and each new window after the
    home screen or the lock screen) calls request_frame_rate(window) first.
  - The rate: 60, or NX_DISPLAY_HZ's when that experimental variable is set (the game's own pace then).
    NX_FRAME_RATE=<hz> (environment or config.txt) asks for another one; NX_FRAME_RATE=0: nothing is asked, as before.
  - The function is looked up with dlsym (libnativewindow.so, then libandroid.so): nothing new to link, and a system
    without it just goes without.
  - Logged once per answer:
      present: the window is told the game runs at 60 fps (ANativeWindow_setFrameRate: 0)
      present: the frame rate is not told to the window (NX_FRAME_RATE=0)
      present: the frame rate is not told to the window (no ANativeWindow_setFrameRate in this system)
  - ../tools/recomp_tsukihime.ps1: its $Markers row, after the platform_android_input.cpp row of apply_android_hud.py
    (consolidated), which no other patcher uses as an anchor.
Windows, Linux and macOS: nothing changes.

Idempotent (marker AP_FRAME_RATE), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_android_frame_rate.py            then rebuild the Android library
                                                               (tools\\android\\build_apk.ps1 -Edition <ed>, or a new
                                                               Android pack)
  python tools\\patches\\apply_android_frame_rate.py --remove
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_FRAME_RATE"

PLAT = "runtime/src/platform_android.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

PLAT_OLD = """int vulkan_create_surface(void* instance, void* native_window, uint64_t* surface, void* gipa) {
"""
PLAT_NEW = """// AP_FRAME_RATE (apply_android_frame_rate.py): the window is told how many frames a second the game makes (60, or
// NX_DISPLAY_HZ's), so that Android can pick a refresh rate they fit in evenly: a 90 Hz screen left at 90 shows 60
// frames unevenly, and a 120 Hz one need not refresh twice per frame. ANativeWindow_setFrameRate (Android 11), found
// with dlsym. NX_FRAME_RATE=<hz>: another rate; NX_FRAME_RATE=0: nothing is asked, as before.
namespace {
void request_frame_rate(ANativeWindow* w) {
    using SetFrameRate = int32_t (*)(ANativeWindow*, float, int8_t);
    static const SetFrameRate set = [] {
        SetFrameRate f = nullptr;
        for (const char* name : {"libnativewindow.so", "libandroid.so"})
            if (void* lib = f ? nullptr : dlopen(name, RTLD_NOW | RTLD_LOCAL))
                f = reinterpret_cast<SetFrameRate>(dlsym(lib, "ANativeWindow_setFrameRate"));
        return f;
    }();
    static const float rate = [] {
        if (const char* e = std::getenv("NX_FRAME_RATE"); e && *e) return float(std::atof(e));
        if (const char* e = std::getenv("NX_DISPLAY_HZ")) {  // as display_period_ns (hle_vi.cpp)
            const int hz = std::atoi(e);
            if (hz >= 30 && hz <= 360) return float(hz);
        }
        return 60.0f;
    }();
    static int said = 0;  // 1: not asked, 2 + the answer: asked
    if (!w) return;
    if (!set || !(rate >= 1.0f)) {
        if (said != 1)
            said = 1, nx::log("present: the frame rate is not told to the window (%s)",
                              set ? "NX_FRAME_RATE=0" : "no ANativeWindow_setFrameRate in this system");
        return;
    }
    // Compatibility 0 (default): the game has no fixed source rate to keep, any multiple of it is as good.
    const int32_t r = set(w, rate, 0);
    if (said != 2 + (r != 0)) {
        said = 2 + (r != 0);
        nx::log("present: the window is told the game runs at %.0f fps (ANativeWindow_setFrameRate: %d)", double(rate),
                int(r));
    }
}
}  // namespace

int vulkan_create_surface(void* instance, void* native_window, uint64_t* surface, void* gipa) {
    request_frame_rate(static_cast<ANativeWindow*>(native_window));
"""

RECO_OLD = """    @('both', 'framework\\runtime\\src\\platform_android_input.cpp', 'AP_ANDROID_HUD', 'apply_android_hud.py', $false),
"""
RECO_NEW = """    @('both', 'framework\\runtime\\src\\platform_android_input.cpp', 'AP_ANDROID_HUD', 'apply_android_hud.py', $false),
    @('both', 'framework\\runtime\\src\\platform_android.cpp', 'AP_FRAME_RATE', 'apply_android_frame_rate.py', $false),
"""

EDITS = [
    (PLAT, PLAT_OLD, PLAT_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
