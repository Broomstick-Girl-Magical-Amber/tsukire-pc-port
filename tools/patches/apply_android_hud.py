"""apply_android_hud.py - button icons of the HUD by controller on Android (AP_ANDROID_HUD).

Pulido of the Android port. On PC the HUD icons follow the last device with real input (AP_HUD_GLYPHS: Xbox, PlayStation,
Switch, keyboard; pad_sdl.cpp and input_tsuki.cpp call hud::note_input). On Android nobody called it: the HUD stayed on
its start device (the keyboard icons, or the original Switch ones when the icons were not on the phone).

- runtime/src/platform.h: `plat::input_note` (Android only): a hook a game sets, called with the kind of the last real
  input: 0 finger, 1 Xbox (or a generic controller), 2 PlayStation, 3 Switch. Null by default: nothing changes.
- runtime/src/platform_android_input.cpp: the NDK gives no name or vendor for a device, so the first time a controller
  id sends input, InputDevice.getDevice(id) (getName, getVendorId, getProductId) is asked through JNI and the answer is
  kept per id (log: `hud: controller id 5 "DualSense Wireless Controller" (VID:PID 054c:0ce6): PlayStation icons`).
  Microsoft (0x045E or "xbox" in the name) = Xbox; Sony (0x054C, or DualSense / DualShock / PlayStation in the name) =
  PlayStation; Nintendo
  (0x057E, or Pro Controller / Joy-Con / Nintendo) = Switch; everything else = Xbox, as pad_sdl.cpp does. Real input: a
  mapped button down from a controller source (gamepad, joystick or dpad, device id >= 0: the system Back gesture comes
  from the virtual keyboard, id -1, and is not a controller), a stick, the hat or a trigger past half (drift does not
  count), a finger down (pointer mode).
  Version 2 (2026-11-12): v1 took the Back gesture for an Xbox controller ("Virtual", id -1); remove v1 with its own
  file (--remove) before applying this one.
  on_input (UI thread) only writes the kind down; read_pad (game thread) delivers it to the hook, so hud::note_input and
  its log never run on the UI thread. The first call of read_pad says "finger" unless a controller already spoke.
- games/tsukihime_common/hud_android.cpp (new file, in the repo): sets the hook; finger = Switch icons (NX_HUD_TOUCH=
  switch|xbox|ps in config.txt changes it); the others map one to one. NX_HUD_GLYPHS in config.txt still wins.
- Not done (pending): a physical keyboard (Android does not read keyboard keys yet).

Needs apply_android_input.py (the finger is detected with its Pointer counter, g_ptr.downs). Idempotent (marker
AP_ANDROID_HUD), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as they were. Does not
touch lines that apply_android_input.py inserts (the anchors are original lines) and can be removed before or after it.
Also edits ../tools/recomp_tsukihime.ps1 ($Markers rows, anchored after the AP_ANDROID_ROMFS row, which no other
patcher uses). hud_android.cpp is a new file: copy it to games\\tsukihime_common.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_hud.py
"""
from _patch import run

MARK = "AP_ANDROID_HUD"
PLATFORM_H = "runtime/src/platform.h"
INPUT = "runtime/src/platform_android_input.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ platform.h
    (PLATFORM_H,
     '''// Audio: s16 stereo 48 kHz. `done` is called (on any thread) when a submitted buffer has been played.
''',
     '''#if defined(__ANDROID__)  // AP_ANDROID_HUD (apply_android_hud.py)
// The kind of device the last real input came from, for a game's own HUD (Tsukihime: the button icons): 0 finger,
// 1 Xbox (or a generic controller), 2 PlayStation, 3 Switch. Called on the game thread from read_pad; the first call
// says 0 unless a controller already spoke. Null (the default): nothing.
using InputNote = void (*)(int kind);
extern InputNote input_note;  // platform_android_input.cpp
#endif

// Audio: s16 stereo 48 kHz. `done` is called (on any thread) when a submitted buffer has been played.
'''),

    # ------------------------------------------------------------------------------------------------ platform_android_input.cpp
    (INPUT,
     '''std::vector<Touch> g_touches;
float g_w = 1, g_h = 1;
''',
     '''std::vector<Touch> g_touches;
float g_w = 1, g_h = 1;

// AP_ANDROID_HUD (apply_android_hud.py): the kind of device that gave the last real input, for the game's HUD icons
// (plat::input_note, platform.h): 0 finger, 1 Xbox (or generic), 2 PlayStation, 3 Switch. on_input only writes it down;
// read_pad delivers it on the game thread.
JavaVM* g_vm = nullptr;                           // set by on_input
int g_hud_kind = -1;                              // the last kind not delivered yet (-1: none)
std::vector<std::pair<int32_t, int>> g_hud_pads;  // device id -> kind (InputDevice is asked once per id)

bool name_has(const std::string& lower, const char* word) { return lower.find(word) != std::string::npos; }

int pad_kind_of(int vendor, std::string name) {
    for (char& c : name)
        if (c >= 'A' && c <= 'Z') c = char(c + 32);
    if (vendor == 0x045E || name_has(name, "xbox")) return 1;  // before PlayStation: "Xbox Wireless Controller"
    if (vendor == 0x054C || name_has(name, "dualsense") || name_has(name, "dualshock") || name_has(name, "playstation") ||
        name == "wireless controller")  // the DualShock 4 name when the vendor id is not known
        return 2;
    if (vendor == 0x057E || name_has(name, "pro controller") || name_has(name, "joy-con") || name_has(name, "nintendo"))
        return 3;
    return 1;
}

int pad_kind(int32_t id) {
    for (const auto& d : g_hud_pads)
        if (d.first == id) return d.second;
    int vendor = 0, product = 0;
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
                jmethodID get = env->GetStaticMethodID(cls, "getDevice", "(I)Landroid/view/InputDevice;");
                jmethodID vid = env->GetMethodID(cls, "getVendorId", "()I");
                jmethodID pid = env->GetMethodID(cls, "getProductId", "()I");
                jmethodID nam = env->GetMethodID(cls, "getName", "()Ljava/lang/String;");
                if (get && vid && pid && nam) {
                    if (jobject dev = env->CallStaticObjectMethod(cls, get, jint(id))) {
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
    const int kind = pad_kind_of(vendor, name);
    nx::log("hud: controller id %d \\"%s\\" (VID:PID %04x:%04x): %s icons", int(id), name.c_str(), unsigned(vendor),
            unsigned(product), kind == 2 ? "PlayStation" : kind == 3 ? "Switch" : "Xbox");
    g_hud_pads.emplace_back(id, kind);
    return kind;
}

void hud_note_pad(AInputEvent* e) {
    if (input_note) g_hud_kind = pad_kind(AInputEvent_getDeviceId(e));
}
'''),
    (INPUT,
     '''int32_t on_input(android_app*, AInputEvent* e) {
    std::lock_guard lk(g_in);
''',
     '''int32_t on_input(android_app* app, AInputEvent* e) {
    std::lock_guard lk(g_in);
    if (app && app->activity) g_vm = app->activity->vm;  // AP_ANDROID_HUD
'''),
    (INPUT,
     '''        g_gamepad = true;
        if (AKeyEvent_getAction(e) == AKEY_EVENT_ACTION_DOWN)
            g_pad_buttons.buttons |= b;
''',
     '''        g_gamepad = true;
        if (AKeyEvent_getAction(e) == AKEY_EVENT_ACTION_DOWN && AInputEvent_getDeviceId(e) >= 0 &&
            ((src & AINPUT_SOURCE_GAMEPAD) == AINPUT_SOURCE_GAMEPAD ||
             (src & AINPUT_SOURCE_JOYSTICK) == AINPUT_SOURCE_JOYSTICK || (src & AINPUT_SOURCE_DPAD) == AINPUT_SOURCE_DPAD))
            hud_note_pad(e);  // AP_ANDROID_HUD: only a controller (the system Back gesture comes from the virtual keyboard)
        if (AKeyEvent_getAction(e) == AKEY_EVENT_ACTION_DOWN)
            g_pad_buttons.buttons |= b;
'''),
    (INPUT,
     '''            g_pad_buttons.buttons |= kZR;
        return 1;
''',
     '''            g_pad_buttons.buttons |= kZR;
        if (input_note) {  // AP_ANDROID_HUD: a stick, the hat or a trigger past half is real input (drift does not count)
            const int32_t axes[] = {AMOTION_EVENT_AXIS_X,        AMOTION_EVENT_AXIS_Y,      AMOTION_EVENT_AXIS_Z,
                                    AMOTION_EVENT_AXIS_RZ,       AMOTION_EVENT_AXIS_HAT_X,  AMOTION_EVENT_AXIS_HAT_Y,
                                    AMOTION_EVENT_AXIS_LTRIGGER, AMOTION_EVENT_AXIS_RTRIGGER, AMOTION_EVENT_AXIS_BRAKE,
                                    AMOTION_EVENT_AXIS_GAS};
            bool real = false;
            for (int32_t a : axes) real = real || std::fabs(AMotionEvent_getAxisValue(e, a, 0)) > 0.5f;
            if (real) hud_note_pad(e);
        }
        return 1;
'''),
    (INPUT,
     '''void read_pad(PadState& p) {
    std::lock_guard lk(g_in);
''',
     '''InputNote input_note = nullptr;  // AP_ANDROID_HUD (platform.h): null until the game sets it

namespace {
// AP_ANDROID_HUD: hands the kind of the last real input to the game's hook, on the game thread. A finger is seen
// through the counter of presses of pointer mode; the first call says "finger" unless a controller already spoke.
void hud_note_deliver() {
    if (!input_note) return;
    int kind;
    {
        std::lock_guard lk(g_in);
        static bool first = true;
        static uint32_t seen = g_ptr.downs;
        if (g_ptr.downs != seen) seen = g_ptr.downs, g_hud_kind = 0;
        if (first) {
            first = false;
            if (g_hud_kind < 0) g_hud_kind = 0;
        }
        kind = g_hud_kind;
        g_hud_kind = -1;
    }
    if (kind >= 0) input_note(kind);
}
}  // namespace

void read_pad(PadState& p) {
    hud_note_deliver();  // AP_ANDROID_HUD
    std::lock_guard lk(g_in);
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANDROID_ROMFS', 'apply_android_romfs.py', $false),
""",
     """    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANDROID_ROMFS', 'apply_android_romfs.py', $false),
    @('both', 'framework\\runtime\\src\\platform.h', 'AP_ANDROID_HUD', 'apply_android_hud.py', $false),
    @('both', 'framework\\runtime\\src\\platform_android_input.cpp', 'AP_ANDROID_HUD', 'apply_android_hud.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\hud_android.cpp', 'AP_ANDROID_HUD', 'hud_android.cpp (copy it to games\\tsukihime_common)', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
