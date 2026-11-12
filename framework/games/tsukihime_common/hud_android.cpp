// hud_android.cpp - button icons of the HUD by controller on Android (AP_ANDROID_HUD, apply_android_hud.py).
// Only compiled on Android with NX_HUD_GLYPHS (hud_glyphs.cpp); on the other platforms this file is empty.
//
// platform_android_input.cpp finds out which kind of device gave the last real input (a finger, an Xbox, PlayStation or
// Switch controller: InputDevice name and vendor id through JNI) and calls plat::input_note from read_pad, on the game
// thread. Here it becomes hud::note_input, the same call PC makes from pad_sdl.cpp / input_tsuki.cpp:
//   finger        -> Switch (the original icons), or NX_HUD_TOUCH=switch|xbox|ps in config.txt
//   Xbox / other  -> Xbox
//   PlayStation   -> PlayStation
//   Switch        -> Switch
// NX_HUD_GLYPHS=switch|xbox|ps|keyboard|off (config.txt) still wins over all of this (hud_glyphs.cpp).
// A physical keyboard is not read on Android yet (pending).
#if defined(__ANDROID__) && defined(NX_HUD_GLYPHS)

#include <cctype>
#include <cstdlib>
#include <string>

#include "hud_glyphs.h"
#include "platform.h"

namespace nx::tsuki {
namespace {

hud::Device touch_device() {
    const char* e = std::getenv("NX_HUD_TOUCH");
    std::string v = e ? e : "";
    for (char& ch : v) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    if (v == "xbox") return hud::Device::kXbox;
    if (v == "ps" || v == "playstation") return hud::Device::kPs;
    return hud::Device::kSwitch;
}

void on_input_note(int kind) {  // 0 finger, 1 Xbox, 2 PlayStation, 3 Switch (platform.h)
    switch (kind) {
    case 1: hud::note_input(hud::Device::kXbox); break;
    case 2: hud::note_input(hud::Device::kPs); break;
    case 3: hud::note_input(hud::Device::kSwitch); break;
    default: hud::note_input(touch_device()); break;
    }
}

// input_note is a null pointer that platform_android_input.cpp defines: constant-initialized, so setting it here is safe
// whatever the order of the static initializers.
const bool g_input_note_set = [] {
    plat::input_note = on_input_note;
    return true;
}();

}  // namespace
}  // namespace nx::tsuki

#endif
