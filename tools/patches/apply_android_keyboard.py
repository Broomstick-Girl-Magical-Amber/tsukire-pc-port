"""apply_android_keyboard.py - a physical keyboard (USB or Bluetooth) on Android: game input and HUD icons
(AP_ANDROID_KEYBOARD).

Pulido of the Android port. Before: a keyboard only moved the d-pad with its arrows (KEYCODE_DPAD_*); every other key
(Space, Enter, letters, Tab, Ctrl...) went back to the system and the game never saw it, and the HUD never showed the
keyboard icons.

- runtime/src/platform.h (Android): `plat::KeyState {held[256], downs[256]}` and `read_keys`: the keys of the physical
  keyboard by Windows virtual-key code (the codes input.ini names and keyboard_tiles.bin is drawn with); downs counts
  presses, so a tap shorter than a frame still shows. 0x10/0x11/0x12 (Shift/Ctrl/Alt) are either side.
  `plat::keys_wanted`: false by default (another game, or Tsukihime before this patch: the keyboard as before).
- runtime/src/platform_android_input.cpp: a keyboard is a device with id >= 0 (id -1 is the virtual keyboard: the
  system Back gesture), not virtual, of alphabetic type and without a gamepad source; InputDevice through JNI, once per
  id (log: `keyboard: device id 10 "SINO WEALTH Gaming KB" (VID:PID 258a:002a): a keyboard, its keys go to the game`).
  A joystick source does not rule it out: some keyboards are two devices with the same name and VID:PID, and the second
  one ("... System Control": keyboard + dpad + joystick) sends keys when many are down at once; both count, and a key is
  held by its key code whatever interface its down and up come from. With keys_wanted, a keyboard's key with a code
  (A-Z, 0-9, F1-F12, Num0-9, Space, Enter and the keypad's, Escape, Tab, Backspace, Delete, Shift/Ctrl/Alt left and
  right, arrows, PageUp/PageDown, Home/End, Insert, comma, period) is taken (no longer the d-pad, no longer a controller
  for the HUD) and the rest goes to the system (Meta, volume, media, the signs input.ini has no name for). Controller
  buttons (BUTTON_*) and the Back key keep their old path. NX_KEYBOARD=0 (config.txt): the keyboard as before.
- games/tsukihime_common/input_tsuki.cpp/.h: `keyboard_pad(p)` (Android): [teclado] of input.ini (or the defaults, the
  Fate/stay night layout) over read_keys, like the Windows loop: a held key presses its button; a key that goes down
  presses it for at least 50 ms and, if it is assigned, switches the HUD to the keyboard icons (hud::note_input, as on
  PC). Sets keys_wanted. Escape = Y (system menu) as on PC; the system Back gesture is still B.
- games/tsukihime_common/hle_hid_tsuki.cpp: the pad hook calls keyboard_pad (Android), before the trace.
- games/tsukihime_common/hud_android.cpp: its header comment (the keyboard is no longer pending).
- ../tools/tests/test_android_double_tap.py: its harness stubs keyboard_pad (it compiles hle_hid_tsuki.cpp alone).
- ../tools/recomp_tsukihime.ps1: $Markers rows, anchored after the hud_glyphs.cpp row (in recomp from the start: no
  patcher inserts it or anchors on it, so removing any other patcher first does not leave these rows without an anchor).

input.ini on the phone: <data folder of the edition>/input.ini (the folder of config.txt, e.g.
Tsukihime/data/tsuki_global/input.ini); without it, the defaults. F13-F24 do not exist on Android.
Test: python tools/tests/test_android_keyboard.py (both sides, on the host, with the NDK and JNI stubbed).

Needs apply_android_input.py, apply_android_hud.py, apply_hud_glyphs.py and apply_android_double_tap.py. Remove
apply_android_key_diag.py (--remove) first: it is a diagnostic. Idempotent (marker AP_ANDROID_KEYBOARD), all or nothing,
keeps CRLF and BOM, --remove leaves the files byte for byte as they were. The anchors are lines no other patcher
inserts next to, so it can be removed before or after the others.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_keyboard.py
"""
from _patch import run

MARK = "AP_ANDROID_KEYBOARD"
PLATFORM_H = "runtime/src/platform.h"
INPUT = "runtime/src/platform_android_input.cpp"
TSUKI_CPP = "games/tsukihime_common/input_tsuki.cpp"
TSUKI_H = "games/tsukihime_common/input_tsuki.h"
HID = "games/tsukihime_common/hle_hid_tsuki.cpp"
HUD = "games/tsukihime_common/hud_android.cpp"
TEST = "../tools/tests/test_android_double_tap.py"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------- platform.h
    (PLATFORM_H,
     '''// Vulkan: the loader's vkGetInstanceProcAddr and the surface extension of this platform.
''',
     '''#if defined(__ANDROID__)  // AP_ANDROID_KEYBOARD (apply_android_keyboard.py)
// A physical keyboard (USB or Bluetooth) for a game that reads it itself (Tsukihime: input.ini), by Windows virtual-key
// code (VK_*: 'A'-'Z', '0'-'9', 0x20 Space, 0x0D Enter...). held: down now; downs: presses so far (it wraps; a press
// shorter than a frame still changes it). 0x10/0x11/0x12 (Shift/Ctrl/Alt) are either side. Only while the game sets
// keys_wanted (and NX_KEYBOARD is not 0); otherwise a keyboard works as before (arrows = d-pad, the rest to the system).
struct KeyState {
    uint8_t held[256];
    uint8_t downs[256];
};
void read_keys(KeyState& k);    // platform_android_input.cpp; any thread
extern bool keys_wanted;        // false by default
#endif

// Vulkan: the loader's vkGetInstanceProcAddr and the surface extension of this platform.
'''),

    # ------------------------------------------------------------------------------- platform_android_input.cpp
    (INPUT,
     '''    return right ? kA : kB;
}
''',
     r'''    return right ? kA : kB;
}

// AP_ANDROID_KEYBOARD (apply_android_keyboard.py): a physical keyboard for a game that reads it itself (keys_wanted,
// platform.h). A keyboard: id >= 0 (the virtual keyboard, id -1, is the system Back gesture), not virtual, alphabetic,
// without a gamepad source (InputDevice through JNI, once per id). A joystick source does not rule it out: a keyboard
// may be two devices with the same name and VID:PID, the second one ("... System Control": keyboard + dpad + joystick)
// sending keys when many are down at once. Its keys become Windows virtual-key codes; controller buttons and Back keep
// their path; keys without a code (Meta, volume, media, signs) go to the system.
constexpr int kKeyCodes = 512;
uint8_t g_key_held[kKeyCodes];   // by AKEYCODE, whatever interface the down and the up come from
uint8_t g_key_downs[kKeyCodes];  // presses so far (wraps)
std::vector<std::pair<int32_t, bool>> g_kbd_devices;  // device id -> a keyboard

uint8_t vk_of(int32_t k) {  // AKEYCODE -> Windows virtual-key code (0: none)
    if (k >= AKEYCODE_A && k <= AKEYCODE_Z) return uint8_t('A' + (k - AKEYCODE_A));
    if (k >= AKEYCODE_0 && k <= AKEYCODE_9) return uint8_t('0' + (k - AKEYCODE_0));
    if (k >= AKEYCODE_F1 && k <= AKEYCODE_F12) return uint8_t(0x70 + (k - AKEYCODE_F1));
    if (k >= AKEYCODE_NUMPAD_0 && k <= AKEYCODE_NUMPAD_9) return uint8_t(0x60 + (k - AKEYCODE_NUMPAD_0));
    switch (k) {
        case AKEYCODE_SPACE: return 0x20;
        case AKEYCODE_ENTER: case AKEYCODE_NUMPAD_ENTER: return 0x0D;
        case AKEYCODE_ESCAPE: return 0x1B;
        case AKEYCODE_TAB: return 0x09;
        case AKEYCODE_DEL: return 0x08;  // Backspace
        case AKEYCODE_FORWARD_DEL: return 0x2E;
        case AKEYCODE_SHIFT_LEFT: return 0xA0;
        case AKEYCODE_SHIFT_RIGHT: return 0xA1;
        case AKEYCODE_CTRL_LEFT: return 0xA2;
        case AKEYCODE_CTRL_RIGHT: return 0xA3;
        case AKEYCODE_ALT_LEFT: return 0xA4;
        case AKEYCODE_ALT_RIGHT: return 0xA5;
        case AKEYCODE_DPAD_LEFT: return 0x25;
        case AKEYCODE_DPAD_UP: return 0x26;
        case AKEYCODE_DPAD_RIGHT: return 0x27;
        case AKEYCODE_DPAD_DOWN: return 0x28;
        case AKEYCODE_PAGE_UP: return 0x21;
        case AKEYCODE_PAGE_DOWN: return 0x22;
        case AKEYCODE_MOVE_END: return 0x23;
        case AKEYCODE_MOVE_HOME: return 0x24;
        case AKEYCODE_INSERT: return 0x2D;
        case AKEYCODE_COMMA: return 0xBC;
        case AKEYCODE_PERIOD: return 0xBE;
        default: return 0;
    }
}

bool keyboard_off() {  // NX_KEYBOARD=0 (config.txt)
    static const bool off = [] {
        const char* e = std::getenv("NX_KEYBOARD");
        return e && e[0] == '0';
    }();
    return off;
}

bool is_keyboard(int32_t id) {
    if (id < 0) return false;
    for (const auto& d : g_kbd_devices)
        if (d.first == id) return d.second;
    bool found = false, is_virtual = true;
    int vendor = 0, product = 0, type = 0, sources = 0;
    std::string name;
    if (g_vm) {
        JNIEnv* env = nullptr;
        bool attached = false;
        if (g_vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
            if (g_vm->AttachCurrentThread(&env, nullptr) == JNI_OK) attached = true;
            else env = nullptr;
        }
        if (env) {
            if (jclass cls = env->FindClass("android/view/InputDevice")) {
                // no JNI call with an exception pending (CheckJNI would abort): a failed lookup stops the next ones
                auto method = [&](const char* n, const char* sig) -> jmethodID {
                    return env->ExceptionCheck() ? nullptr : env->GetMethodID(cls, n, sig);
                };
                jmethodID get = env->GetStaticMethodID(cls, "getDevice", "(I)Landroid/view/InputDevice;");
                jmethodID kt = method("getKeyboardType", "()I");
                jmethodID src = method("getSources", "()I");
                jmethodID vir = method("isVirtual", "()Z");
                jmethodID vid = method("getVendorId", "()I");
                jmethodID pid = method("getProductId", "()I");
                jmethodID nam = method("getName", "()Ljava/lang/String;");
                if (env->ExceptionCheck()) {
                    env->ExceptionClear();
                } else if (get && kt && src && vir && vid && pid && nam) {
                    if (jobject dev = env->CallStaticObjectMethod(cls, get, jint(id))) {
                        found = true;
                        type = int(env->CallIntMethod(dev, kt));
                        sources = int(env->CallIntMethod(dev, src));
                        is_virtual = env->CallBooleanMethod(dev, vir) != JNI_FALSE;
                        vendor = int(env->CallIntMethod(dev, vid));
                        product = int(env->CallIntMethod(dev, pid));
                        if (jstring js = static_cast<jstring>(env->CallObjectMethod(dev, nam))) {
                            if (const char* c = env->GetStringUTFChars(js, nullptr)) {
                                name = c;
                                env->ReleaseStringUTFChars(js, c);
                            }
                            env->DeleteLocalRef(js);
                        }
                        env->DeleteLocalRef(dev);
                    }
                }
                env->DeleteLocalRef(cls);
            }
            if (env->ExceptionCheck()) env->ExceptionClear();
            if (attached) g_vm->DetachCurrentThread();
        }
    }
    constexpr int kAlphabetic = 2;  // InputDevice.KEYBOARD_TYPE_ALPHABETIC
    const bool kbd = found && !is_virtual && type == kAlphabetic &&
                     (sources & AINPUT_SOURCE_GAMEPAD) != AINPUT_SOURCE_GAMEPAD;
    while (!name.empty() && name.back() == ' ') name.pop_back();
    if (kbd)
        nx::log("keyboard: device id %d \"%s\" (VID:PID %04x:%04x): a keyboard, its keys go to the game", int(id),
                name.c_str(), unsigned(vendor), unsigned(product));
    g_kbd_devices.emplace_back(id, kbd);
    return kbd;
}

bool controller_key(int32_t k) {  // keys that keep their old path even from a keyboard
    return (k >= AKEYCODE_BUTTON_A && k <= AKEYCODE_BUTTON_MODE) || (k >= AKEYCODE_BUTTON_1 && k <= AKEYCODE_BUTTON_16) ||
           k == AKEYCODE_BACK;
}

// What on_input returns for a key of a physical keyboard (1 taken, 0 to the system); -1: not one, the old path.
int32_t keyboard_event(AInputEvent* e) {
    if (!keys_wanted || keyboard_off()) return -1;
    const int32_t code = AKeyEvent_getKeyCode(e);
    if (controller_key(code) || !is_keyboard(AInputEvent_getDeviceId(e))) return -1;
    if (code < 0 || code >= kKeyCodes || !vk_of(code)) return 0;
    const int32_t action = AKeyEvent_getAction(e);
    if (action == AKEY_EVENT_ACTION_DOWN) {
        if (!g_key_held[code]) g_key_held[code] = 1, g_key_downs[code]++;  // a repeat is not a new press
    } else if (action == AKEY_EVENT_ACTION_UP) {  // also the cancel the system sends when the app loses focus
        g_key_held[code] = 0;
    }
    return 1;
}
'''),
    (INPUT,
     '''    if (type == AINPUT_EVENT_TYPE_KEY) {
        uint64_t b = key_to_button(AKeyEvent_getKeyCode(e));
''',
     '''    if (type == AINPUT_EVENT_TYPE_KEY) {
        if (const int32_t r = keyboard_event(e); r >= 0) return r;  // AP_ANDROID_KEYBOARD: a physical keyboard
        uint64_t b = key_to_button(AKeyEvent_getKeyCode(e));
'''),
    (INPUT,
     '''    return k;
}

}  // namespace nx::plat
''',
     '''    return k;
}

bool keys_wanted = false;  // AP_ANDROID_KEYBOARD (platform.h): set by the game (Tsukihime: input_tsuki.cpp)

// AP_ANDROID_KEYBOARD: the physical keyboard by Windows virtual-key code (platform.h).
void read_keys(KeyState& k) {
    std::lock_guard lk(g_in);
    std::memset(&k, 0, sizeof k);
    for (int32_t c = 0; c < kKeyCodes; c++)
        if (const uint8_t vk = vk_of(c)) k.held[vk] |= g_key_held[c], k.downs[vk] = uint8_t(k.downs[vk] + g_key_downs[c]);
    static const uint8_t kSides[3][3] = {{0x10, 0xA0, 0xA1}, {0x11, 0xA2, 0xA3}, {0x12, 0xA4, 0xA5}};  // Shift Ctrl Alt
    for (const auto& s : kSides) k.held[s[0]] = k.held[s[1]] | k.held[s[2]], k.downs[s[0]] = uint8_t(k.downs[s[1]] + k.downs[s[2]]);
}

}  // namespace nx::plat
'''),

    # ------------------------------------------------------------------------------------------- input_tsuki.h
    (TSUKI_H,
     '''
}  // namespace nx::tsuki
''',
     '''
#if defined(__ANDROID__)  // AP_ANDROID_KEYBOARD (apply_android_keyboard.py)
// The physical keyboard (plat::read_keys) through [teclado] of input.ini or the defaults: presses its buttons in p and
// switches the HUD to the keyboard icons when an assigned key goes down. From the pad hook (hle_hid_tsuki.cpp).
void keyboard_pad(plat::PadState& p);
#endif

}  // namespace nx::tsuki
'''),

    # ----------------------------------------------------------------------------------------- input_tsuki.cpp
    (TSUKI_CPP,
     '''}  // namespace
#endif

}  // namespace nx::tsuki''',
     '''}  // namespace
#endif

// ---- AP_ANDROID_KEYBOARD (apply_android_keyboard.py): the physical keyboard on Android -----------------------------
// platform_android_input.cpp keeps the keys of a USB or Bluetooth keyboard by Windows virtual-key code, so [teclado]
// (and the defaults) work as on Windows, keyboard_tiles.bin included. A held key presses its button; a key that goes
// down presses it for at least 50 ms (a tap shorter than a frame still reaches the game) and, if it is assigned,
// switches the HUD to the keyboard icons. NX_KEYBOARD=0 (config.txt): the keyboard as before (arrows = d-pad only).
#if defined(__ANDROID__)
void keyboard_pad(plat::PadState& p) {
    constexpr uint64_t kMinPress = 50000000;  // ns
    static std::mutex m;
    std::lock_guard lk(m);
    const Config& c = config();
    plat::KeyState k;
    plat::read_keys(k);
    static uint8_t seen[256];    // downs of the last read
    static uint64_t until[256];  // a new press holds its button until then
    const uint64_t now = plat::monotonic_ns();
    bool fresh[256] = {};
    for (int vk = 0; vk < 256; vk++)
        if (k.downs[vk] != seen[vk]) fresh[vk] = true, until[vk] = now + kMinPress;
    std::memcpy(seen, k.downs, sizeof seen);
    bool note = false;
    for (const KeyBind& b : c.keys) {
        const int vk = b.vk & 0xFF;
        if (k.held[vk] || now < until[vk]) press(p, b.t);
        note = note || fresh[vk];
    }
#ifdef NX_HUD_GLYPHS
    if (note) hud::note_input(hud::Device::kKeyboard);
#else
    (void)note;
#endif
}

namespace {
const bool g_keys_wanted_set = [] {
    plat::keys_wanted = true;  // AP_ANDROID_KEYBOARD: the platform hands the keyboard over (platform.h)
    return true;
}();
}  // namespace
#endif

}  // namespace nx::tsuki'''),

    # --------------------------------------------------------------------------------------- hle_hid_tsuki.cpp
    (HID,
     '''    if (trace_on()) trace_pad(p);
}
''',
     '''#if defined(__ANDROID__)
    tsuki::keyboard_pad(p);  // AP_ANDROID_KEYBOARD: the physical keyboard (input.ini)
#endif
    if (trace_on()) trace_pad(p);
}
'''),

    # ----------------------------------------------------------------------------------------- hud_android.cpp
    (HUD,
     '''// A physical keyboard is not read on Android yet (pending).
''',
     '''// A physical keyboard does not come through here (AP_ANDROID_KEYBOARD): input_tsuki.cpp (keyboard_pad) calls
// hud::note_input(kKeyboard) itself when a key of [teclado] goes down, as on PC.
'''),

    # ---------------------------------------------------------------------------- test_android_double_tap.py
    (TEST,
     '''namespace tsuki { std::string describe_buttons(uint64_t, uint64_t) { return {}; } }
''',
     '''namespace tsuki { std::string describe_buttons(uint64_t, uint64_t) { return {}; } }
namespace tsuki { void keyboard_pad(plat::PadState&) {} }  // AP_ANDROID_KEYBOARD: the keyboard is not tested here
'''),

    # ------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     r"""    @('both', 'framework\games\tsukihime_common\hud_glyphs.cpp', 'AP_HUD_GLYPHS', 'hud_glyphs.cpp and hud_glyphs.h (in the repo)', $false),
""",
     r"""    @('both', 'framework\games\tsukihime_common\hud_glyphs.cpp', 'AP_HUD_GLYPHS', 'hud_glyphs.cpp and hud_glyphs.h (in the repo)', $false),
    @('both', 'framework\runtime\src\platform.h', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
    @('both', 'framework\runtime\src\platform_android_input.cpp', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
    @('both', 'framework\games\tsukihime_common\input_tsuki.cpp', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
    @('both', 'framework\games\tsukihime_common\input_tsuki.h', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_hid_tsuki.cpp', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
    @('both', 'framework\games\tsukihime_common\hud_android.cpp', 'AP_ANDROID_KEYBOARD', 'apply_android_keyboard.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
