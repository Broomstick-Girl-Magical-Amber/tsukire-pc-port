#!/usr/bin/env python3
"""Test of AP_ANDROID_KEYBOARD (apply_android_keyboard.py): a physical keyboard on Android.

Two harnesses, built for the host with __ANDROID__ defined and the NDK and JNI stubbed (headers written here):
1. Platform: runtime/src/platform_android_input.cpp with fake input events and a fake InputDevice (JNI). Devices: the
   two interfaces of the S25 test keyboard (SINO WEALTH, id 10 and id 11 "System Control" with a joystick source), a
   DualSense, a controller of alphabetic type with a gamepad source, a virtual keyboard and the volume keys. Checks
   which events are taken, which go to the system, read_keys, read_pad (the old path) and the HUD hook.
2. Game: games/tsukihime_common/input_tsuki.cpp (keyboard_pad) with read_keys and the clock stubbed: the defaults (FSN
   layout), a short tap, a held key, Ctrl, keys without a binding, an input.ini, the HUD calls and first_key.
Needs g++ or clang++. Run from anywhere: python tools/tests/test_android_keyboard.py
The platform side needs a POSIX host for some headers it only includes (Linux, WSL, MSYS2); elsewhere it is skipped.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "framework"
SRC = FW / "runtime" / "src"
GAME = FW / "games" / "tsukihime_common"
INPUT = SRC / "platform_android_input.cpp"
TSUKI = GAME / "input_tsuki.cpp"

# ---- stubbed NDK and JNI headers (only what the two files use) ------------------------------------------------------
STUBS = {
    "android/log.h": "#pragma once\n",
    "android/window.h": "#pragma once\n",
    "aaudio/AAudio.h": "#pragma once\n",
    "vulkan/vulkan.h": "#pragma once\n",
    "android/native_window.h": r'''#pragma once
#include <cstdint>
struct ANativeWindow;
int32_t ANativeWindow_getWidth(ANativeWindow*);
int32_t ANativeWindow_getHeight(ANativeWindow*);
''',
    "android/keycodes.h": r'''#pragma once
enum {  // values of the NDK's android/keycodes.h
    AKEYCODE_BACK = 4, AKEYCODE_0 = 7, AKEYCODE_9 = 16, AKEYCODE_DPAD_UP = 19, AKEYCODE_DPAD_DOWN = 20,
    AKEYCODE_DPAD_LEFT = 21, AKEYCODE_DPAD_RIGHT = 22, AKEYCODE_VOLUME_UP = 24, AKEYCODE_A = 29, AKEYCODE_Z = 54,
    AKEYCODE_COMMA = 55, AKEYCODE_PERIOD = 56, AKEYCODE_ALT_LEFT = 57, AKEYCODE_ALT_RIGHT = 58, AKEYCODE_SHIFT_LEFT = 59,
    AKEYCODE_SHIFT_RIGHT = 60, AKEYCODE_TAB = 61, AKEYCODE_SPACE = 62, AKEYCODE_ENTER = 66, AKEYCODE_DEL = 67,
    AKEYCODE_MINUS = 69, AKEYCODE_PAGE_UP = 92, AKEYCODE_PAGE_DOWN = 93, AKEYCODE_BUTTON_A = 96, AKEYCODE_BUTTON_B = 97,
    AKEYCODE_BUTTON_X = 99, AKEYCODE_BUTTON_Y = 100, AKEYCODE_BUTTON_L1 = 102, AKEYCODE_BUTTON_R1 = 103,
    AKEYCODE_BUTTON_L2 = 104, AKEYCODE_BUTTON_R2 = 105, AKEYCODE_BUTTON_THUMBL = 106, AKEYCODE_BUTTON_THUMBR = 107,
    AKEYCODE_BUTTON_START = 108, AKEYCODE_BUTTON_SELECT = 109, AKEYCODE_BUTTON_MODE = 110, AKEYCODE_ESCAPE = 111,
    AKEYCODE_FORWARD_DEL = 112, AKEYCODE_CTRL_LEFT = 113, AKEYCODE_CTRL_RIGHT = 114, AKEYCODE_META_LEFT = 117,
    AKEYCODE_MOVE_HOME = 122, AKEYCODE_MOVE_END = 123, AKEYCODE_INSERT = 124, AKEYCODE_F1 = 131, AKEYCODE_F12 = 142,
    AKEYCODE_NUMPAD_0 = 144, AKEYCODE_NUMPAD_9 = 153, AKEYCODE_NUMPAD_ENTER = 160, AKEYCODE_BUTTON_1 = 188,
    AKEYCODE_BUTTON_16 = 203
};
''',
    "android/input.h": r'''#pragma once
#include <cstddef>
#include <cstdint>
#include "keycodes.h"
struct AInputEvent;
enum { AINPUT_EVENT_TYPE_KEY = 1, AINPUT_EVENT_TYPE_MOTION = 2 };
enum {
    AINPUT_SOURCE_CLASS_BUTTON = 0x1, AINPUT_SOURCE_CLASS_POINTER = 0x2, AINPUT_SOURCE_CLASS_JOYSTICK = 0x10,
    AINPUT_SOURCE_KEYBOARD = 0x100 | 0x1, AINPUT_SOURCE_DPAD = 0x200 | 0x1, AINPUT_SOURCE_GAMEPAD = 0x400 | 0x1,
    AINPUT_SOURCE_TOUCHSCREEN = 0x1000 | 0x2, AINPUT_SOURCE_MOUSE = 0x2000 | 0x2, AINPUT_SOURCE_JOYSTICK = 0x01000000 | 0x10
};
enum { AKEY_EVENT_ACTION_DOWN = 0, AKEY_EVENT_ACTION_UP = 1, AKEY_EVENT_ACTION_MULTIPLE = 2 };
enum {
    AMOTION_EVENT_ACTION_MASK = 0xff, AMOTION_EVENT_ACTION_POINTER_INDEX_MASK = 0xff00,
    AMOTION_EVENT_ACTION_POINTER_INDEX_SHIFT = 8, AMOTION_EVENT_ACTION_DOWN = 0, AMOTION_EVENT_ACTION_UP = 1,
    AMOTION_EVENT_ACTION_MOVE = 2, AMOTION_EVENT_ACTION_CANCEL = 3, AMOTION_EVENT_ACTION_POINTER_DOWN = 5,
    AMOTION_EVENT_ACTION_POINTER_UP = 6
};
enum {
    AMOTION_EVENT_AXIS_X = 0, AMOTION_EVENT_AXIS_Y = 1, AMOTION_EVENT_AXIS_Z = 11, AMOTION_EVENT_AXIS_RZ = 14,
    AMOTION_EVENT_AXIS_HAT_X = 15, AMOTION_EVENT_AXIS_HAT_Y = 16, AMOTION_EVENT_AXIS_LTRIGGER = 17,
    AMOTION_EVENT_AXIS_RTRIGGER = 18, AMOTION_EVENT_AXIS_GAS = 22, AMOTION_EVENT_AXIS_BRAKE = 23
};
int32_t AInputEvent_getType(const AInputEvent*);
int32_t AInputEvent_getSource(const AInputEvent*);
int32_t AInputEvent_getDeviceId(const AInputEvent*);
int32_t AKeyEvent_getKeyCode(const AInputEvent*);
int32_t AKeyEvent_getAction(const AInputEvent*);
int32_t AKeyEvent_getScanCode(const AInputEvent*);
int32_t AKeyEvent_getRepeatCount(const AInputEvent*);
int32_t AKeyEvent_getFlags(const AInputEvent*);
int32_t AKeyEvent_getMetaState(const AInputEvent*);
int32_t AMotionEvent_getAction(const AInputEvent*);
int32_t AMotionEvent_getPointerId(const AInputEvent*, size_t);
float AMotionEvent_getX(const AInputEvent*, size_t);
float AMotionEvent_getY(const AInputEvent*, size_t);
size_t AMotionEvent_getPointerCount(const AInputEvent*);
float AMotionEvent_getAxisValue(const AInputEvent*, int32_t, size_t);
int64_t AMotionEvent_getEventTime(const AInputEvent*);
''',
    "jni.h": r'''#pragma once
#include <cstdint>
typedef int32_t jint;
typedef uint8_t jboolean;
typedef void* jobject;
typedef jobject jclass;
typedef jobject jstring;
struct _jmethodID {
    int unused;
};
typedef _jmethodID* jmethodID;
#define JNI_OK 0
#define JNI_ERR (-1)
#define JNI_FALSE 0
#define JNI_TRUE 1
#define JNI_VERSION_1_6 0x00010006
struct JNIEnv {
    jclass FindClass(const char* name);
    jmethodID GetStaticMethodID(jclass, const char* name, const char* sig);
    jmethodID GetMethodID(jclass, const char* name, const char* sig);
    jobject CallStaticObjectMethod(jclass, jmethodID, ...);
    jobject CallObjectMethod(jobject, jmethodID, ...);
    jint CallIntMethod(jobject, jmethodID, ...);
    jboolean CallBooleanMethod(jobject, jmethodID, ...);
    const char* GetStringUTFChars(jstring, jboolean*);
    void ReleaseStringUTFChars(jstring, const char*);
    void DeleteLocalRef(jobject);
    jboolean ExceptionCheck();
    void ExceptionClear();
};
struct JavaVM {
    jint GetEnv(void** env, jint version);
    jint AttachCurrentThread(JNIEnv** env, void* args);
    jint DetachCurrentThread();
};
''',
    "android_native_app_glue.h": r'''#pragma once
#include <jni.h>
#include <android/input.h>
struct ANativeActivity {
    JavaVM* vm;
    void* clazz;
    const char* internalDataPath;
    const char* externalDataPath;
};
struct android_app {
    ANativeActivity* activity;
};
''',
}
# Only used when the host has none of its own (-idirafter): the platform file includes them but the tested code does
# not call them.
POSIX_FALLBACK = {
    "dlfcn.h": "#pragma once\n",
    "sys/mman.h": "#pragma once\n",
    "sched.h": "#pragma once\n",
    "unistd.h": "#pragma once\n",
    "pthread.h": "#pragma once\n",
}

PLATFORM_HARNESS = r'''
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#if defined(_WIN32) && !defined(__MINGW32__)
#include <time.h>
#define CLOCK_MONOTONIC 1
static int clock_gettime(int, timespec* ts) { return timespec_get(ts, TIME_UTC) ? 0 : -1; }
#endif
#include "platform_android_input.cpp"

// ---- fake events ---------------------------------------------------------------------------------------------------
struct AInputEvent {
    int32_t type, source, device, code, action, repeat;
};
int32_t AInputEvent_getType(const AInputEvent* e) { return e->type; }
int32_t AInputEvent_getSource(const AInputEvent* e) { return e->source; }
int32_t AInputEvent_getDeviceId(const AInputEvent* e) { return e->device; }
int32_t AKeyEvent_getKeyCode(const AInputEvent* e) { return e->code; }
int32_t AKeyEvent_getAction(const AInputEvent* e) { return e->action; }
int32_t AKeyEvent_getScanCode(const AInputEvent*) { return 0; }
int32_t AKeyEvent_getRepeatCount(const AInputEvent* e) { return e->repeat; }
int32_t AKeyEvent_getFlags(const AInputEvent*) { return 8; }
int32_t AKeyEvent_getMetaState(const AInputEvent*) { return 0; }
int32_t AMotionEvent_getAction(const AInputEvent*) { return 0; }
int32_t AMotionEvent_getPointerId(const AInputEvent*, size_t) { return 0; }
float AMotionEvent_getX(const AInputEvent*, size_t) { return 0; }
float AMotionEvent_getY(const AInputEvent*, size_t) { return 0; }
size_t AMotionEvent_getPointerCount(const AInputEvent*) { return 0; }
float AMotionEvent_getAxisValue(const AInputEvent*, int32_t, size_t) { return 0; }
int64_t AMotionEvent_getEventTime(const AInputEvent*) { return 0; }
int32_t ANativeWindow_getWidth(ANativeWindow*) { return 2340; }
int32_t ANativeWindow_getHeight(ANativeWindow*) { return 1080; }

// ---- fake InputDevice (JNI) ----------------------------------------------------------------------------------------
struct FakeDevice {
    int id;
    std::string name;
    int vendor, product, type, sources;
    bool is_virtual;
};
static std::vector<FakeDevice> g_devices = {
    {10, "SINO WEALTH Gaming KB ", 0x258a, 0x002a, 2, 0x101, false},
    {11, "SINO WEALTH Gaming KB  System Control", 0x258a, 0x002a, 2, 0x1000311, false},
    {5, "DualSense Wireless Controller", 0x054c, 0x0ce6, 1, 0x1000511, false},
    {6, "Odd Pad", 0x1234, 0x5678, 2, 0x501, false},  // alphabetic type but a gamepad source: not a keyboard
    {-1, "Virtual", 0, 0, 2, 0x301, true},
    {3, "Virtual keys", 0, 0, 2, 0x101, true},
    {2, "gpio-keys", 0, 0, 1, 0x101, false},
};
static int g_jni_calls = 0;
static struct _jmethodID kGet, kType, kSources, kVirtual, kVendor, kProduct, kName;
static int kInputDeviceClass;
jclass JNIEnv::FindClass(const char* n) { return std::strcmp(n, "android/view/InputDevice") == 0 ? &kInputDeviceClass : nullptr; }
jmethodID JNIEnv::GetStaticMethodID(jclass, const char* n, const char*) { return std::strcmp(n, "getDevice") == 0 ? &kGet : nullptr; }
jmethodID JNIEnv::GetMethodID(jclass, const char* n, const char*) {
    const std::string s = n;
    return s == "getKeyboardType" ? &kType : s == "getSources" ? &kSources : s == "isVirtual" ? &kVirtual
         : s == "getVendorId" ? &kVendor : s == "getProductId" ? &kProduct : s == "getName" ? &kName : nullptr;
}
jobject JNIEnv::CallStaticObjectMethod(jclass, jmethodID m, ...) {
    va_list a;
    va_start(a, m);
    const int id = va_arg(a, int);
    va_end(a);
    g_jni_calls++;
    for (auto& d : g_devices)
        if (d.id == id) return &d;
    return nullptr;
}
jobject JNIEnv::CallObjectMethod(jobject o, jmethodID m, ...) { return m == &kName ? &static_cast<FakeDevice*>(o)->name : nullptr; }
jint JNIEnv::CallIntMethod(jobject o, jmethodID m, ...) {
    const FakeDevice* d = static_cast<FakeDevice*>(o);
    return m == &kType ? d->type : m == &kSources ? d->sources : m == &kVendor ? d->vendor : m == &kProduct ? d->product : 0;
}
jboolean JNIEnv::CallBooleanMethod(jobject o, jmethodID m, ...) { return m == &kVirtual && static_cast<FakeDevice*>(o)->is_virtual; }
const char* JNIEnv::GetStringUTFChars(jstring s, jboolean*) { return static_cast<std::string*>(s)->c_str(); }
void JNIEnv::ReleaseStringUTFChars(jstring, const char*) {}
void JNIEnv::DeleteLocalRef(jobject) {}
jboolean JNIEnv::ExceptionCheck() { return JNI_FALSE; }
void JNIEnv::ExceptionClear() {}
static JNIEnv g_env;
jint JavaVM::GetEnv(void** env, jint) { *env = &g_env; return JNI_OK; }
jint JavaVM::AttachCurrentThread(JNIEnv** env, void*) { *env = &g_env; return JNI_OK; }
jint JavaVM::DetachCurrentThread() { return JNI_OK; }

// ---- the rest of the platform the file calls -----------------------------------------------------------------------
static std::vector<std::string> g_logs;
namespace nx {
void log(const char* fmt, ...) {
    char b[512];
    va_list a;
    va_start(a, fmt);
    std::vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    g_logs.push_back(b);
    if (std::getenv("TEST_LOG")) std::fprintf(stderr, "%s\n", b);
}
namespace game { const Info kInfo = {"tsukihime_global", "Tsukihime", 0, "", ""}; }
namespace plat {
ANativeWindow* android_window() { return nullptr; }
const TouchButton* touch_buttons(size_t& n) { n = 0; return nullptr; }
bool touch_enabled() { return true; }
bool touch_mirrored() { return false; }
float touch_opacity_scale() { return 1; }
uint64_t touch_hit(float, float, float) { return 0; }
void input_script(PadState&) {}
}  // namespace plat
}  // namespace nx

using namespace nx::plat;
static JavaVM g_fake_vm;
static ANativeActivity g_activity = {&g_fake_vm, nullptr, nullptr, nullptr};
static android_app g_app = {&g_activity};
static std::vector<int> g_notes;
static void note(int kind) { g_notes.push_back(kind); }
static int fails = 0;
static void check(bool ok, const char* what) {
    fails += !ok;
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
}
static int32_t key(int dev, int src, int code, int action, int repeat = 0) {
    AInputEvent e = {AINPUT_EVENT_TYPE_KEY, src, dev, code, action, repeat};
    return on_input(&g_app, &e);
}
static int32_t press(int dev, int src, int code) { return key(dev, src, code, AKEY_EVENT_ACTION_DOWN); }
static int32_t release(int dev, int src, int code) { return key(dev, src, code, AKEY_EVENT_ACTION_UP); }
static KeyState keys() { KeyState k; read_keys(k); return k; }
static PadState pad() { PadState p; read_pad(p); return p; }
static int count_logs(const char* text) {
    int n = 0;
    for (const auto& l : g_logs) n += l.find(text) != std::string::npos;
    return n;
}

int main(int argc, char** argv) {
    const std::string sc = argc > 1 ? argv[1] : "";
    input_note = note;
    pad();  // the first delivery (finger)
    g_notes.clear();
    if (sc == "before") {  // no game asks for the keyboard: as before the patch
        keys_wanted = false;
        check(press(10, 0x101, AKEYCODE_SPACE) == 0, "keys_wanted false: Space goes to the system");
        release(10, 0x101, AKEYCODE_SPACE);
        check(press(10, 0x101, AKEYCODE_DPAD_UP) == 1 && (pad().buttons & kUp), "keys_wanted false: an arrow is the d-pad");
        release(10, 0x101, AKEYCODE_DPAD_UP);
        check(!(pad().buttons & kUp), "keys_wanted false: the arrow is released");
        check(g_jni_calls == 0, "keys_wanted false: no InputDevice lookup for the keyboard");
    } else if (sc == "off") {  // NX_KEYBOARD=0
        keys_wanted = true;
        check(press(10, 0x101, AKEYCODE_SPACE) == 0, "NX_KEYBOARD=0: Space goes to the system");
        check(press(10, 0x101, AKEYCODE_DPAD_UP) == 1 && (pad().buttons & kUp), "NX_KEYBOARD=0: an arrow is the d-pad");
        check(keys().held[0x20] == 0 && keys().held[0x26] == 0, "NX_KEYBOARD=0: read_keys stays empty");
    } else if (sc == "on") {
        keys_wanted = true;
        check(press(10, 0x101, AKEYCODE_SPACE) == 1, "Space from the keyboard (id 10) is taken");
        KeyState k = keys();
        check(k.held[0x20] == 1 && k.downs[0x20] == 1, "read_keys: Space (0x20) held, one press");
        check(key(10, 0x101, AKEYCODE_SPACE, AKEY_EVENT_ACTION_DOWN, 1) == 1 && keys().downs[0x20] == 1,
              "a repeat (rep 1) is not a new press");
        check(release(10, 0x101, AKEYCODE_SPACE) == 1 && keys().held[0x20] == 0 && keys().downs[0x20] == 1,
              "Space up: not held, the press stays counted");
        check(press(10, 0x101, AKEYCODE_DPAD_UP) == 1, "an arrow is taken");
        check(!(pad().buttons & kUp) && keys().held[0x26] == 1, "the arrow is VK_UP (0x26), no longer the d-pad");
        release(10, 0x101, AKEYCODE_DPAD_UP);
        check(press(11, 0x301, AKEYCODE_A + ('J' - 'A')) == 1 && keys().held['J'] == 1, "the second interface (id 11, joystick source) is a keyboard too");
        check(release(10, 0x101, AKEYCODE_A + ('J' - 'A')) == 1 && keys().held['J'] == 0, "down on id 11, up on id 10: released");
        check(press(11, 0x301, AKEYCODE_DPAD_DOWN) == 1 && !(pad().buttons & kDown), "an arrow of id 11 (src 0x301) is not the d-pad");
        release(11, 0x301, AKEYCODE_DPAD_DOWN);
        pad();
        check(g_notes.empty(), "keyboard keys (also from id 11 with a dpad source) never tell the HUD it is a controller");
        press(10, 0x101, AKEYCODE_CTRL_RIGHT);
        k = keys();
        check(k.held[0xA3] && k.held[0x11] && k.downs[0x11] == 1, "Right Ctrl: VK_RCONTROL and VK_CONTROL (0x11)");
        press(10, 0x101, AKEYCODE_CTRL_LEFT);
        release(10, 0x101, AKEYCODE_CTRL_RIGHT);
        k = keys();
        check(k.held[0x11] && k.downs[0x11] == 2, "Left Ctrl still down: VK_CONTROL held, two presses");
        release(10, 0x101, AKEYCODE_CTRL_LEFT);
        check(keys().held[0x11] == 0, "both Ctrl up: VK_CONTROL released");
        press(10, 0x101, AKEYCODE_ENTER);
        press(10, 0x101, AKEYCODE_NUMPAD_ENTER);
        release(10, 0x101, AKEYCODE_ENTER);
        check(keys().held[0x0D] == 1, "Enter up while the keypad's Enter is down: VK_RETURN still held");
        release(10, 0x101, AKEYCODE_NUMPAD_ENTER);
        check(keys().held[0x0D] == 0, "both Enter keys up: released");
        const struct { int code, vk; const char* name; } map[] = {
            {AKEYCODE_A, 'A', "A"}, {AKEYCODE_Z, 'Z', "Z"}, {AKEYCODE_0, '0', "0"}, {AKEYCODE_9, '9', "9"},
            {AKEYCODE_F1, 0x70, "F1"}, {AKEYCODE_F12, 0x7B, "F12"}, {AKEYCODE_NUMPAD_0, 0x60, "Num0"},
            {AKEYCODE_NUMPAD_9, 0x69, "Num9"}, {AKEYCODE_ESCAPE, 0x1B, "Escape"}, {AKEYCODE_TAB, 0x09, "Tab"},
            {AKEYCODE_DEL, 0x08, "Backspace (DEL)"}, {AKEYCODE_FORWARD_DEL, 0x2E, "Delete"}, {AKEYCODE_SHIFT_LEFT, 0xA0, "LShift"},
            {AKEYCODE_SHIFT_RIGHT, 0xA1, "RShift"}, {AKEYCODE_ALT_LEFT, 0xA4, "LAlt"}, {AKEYCODE_ALT_RIGHT, 0xA5, "RAlt"},
            {AKEYCODE_PAGE_UP, 0x21, "PageUp"}, {AKEYCODE_PAGE_DOWN, 0x22, "PageDown"}, {AKEYCODE_MOVE_HOME, 0x24, "Home"},
            {AKEYCODE_MOVE_END, 0x23, "End"}, {AKEYCODE_INSERT, 0x2D, "Insert"}, {AKEYCODE_COMMA, 0xBC, "comma"},
            {AKEYCODE_PERIOD, 0xBE, "period"}, {AKEYCODE_DPAD_LEFT, 0x25, "Left"}, {AKEYCODE_DPAD_RIGHT, 0x27, "Right"},
        };
        bool all = true;
        for (const auto& m : map) {
            const bool ok = press(10, 0x101, m.code) == 1 && keys().held[m.vk] == 1;
            release(10, 0x101, m.code);
            if (!ok) all = false, std::printf("     %s does not give VK %#x\n", m.name, m.vk);
        }
        check(all, "every key input.ini can name gives its VK code");
        check(press(10, 0x101, AKEYCODE_SHIFT_LEFT) == 1 && keys().held[0x10], "Left Shift: VK_SHIFT (0x10) too");
        release(10, 0x101, AKEYCODE_SHIFT_LEFT);
        check(press(10, 0x101, AKEYCODE_MINUS) == 0 && press(10, 0x101, AKEYCODE_META_LEFT) == 0,
              "keys without a code (minus, Meta) go to the system");
        check(press(10, 0x101, 600) == 0, "a key code out of range goes to the system");
        check(press(-1, 0x301, AKEYCODE_BACK) == 1 && (pad().buttons & kB), "the Back gesture (virtual keyboard, id -1) is still B");
        release(-1, 0x301, AKEYCODE_BACK);
        check(press(10, 0x101, AKEYCODE_BACK) == 1 && (pad().buttons & kB), "a Back key on the keyboard keeps its path (B)");
        release(10, 0x101, AKEYCODE_BACK);
        g_notes.clear();
        check(press(5, 0x501, AKEYCODE_BUTTON_B) == 1 && (pad().buttons & kA), "DualSense: its right button is A, as before");
        check(g_notes.size() == 1 && g_notes[0] == 2, "DualSense: the HUD still gets PlayStation");
        release(5, 0x501, AKEYCODE_BUTTON_B);
        check(press(5, 0x501, AKEYCODE_DPAD_UP) == 1 && (pad().buttons & kUp), "DualSense: its d-pad is still the d-pad");
        release(5, 0x501, AKEYCODE_DPAD_UP);
        check(press(6, 0x501, AKEYCODE_SPACE) == 0 && press(6, 0x501, AKEYCODE_DPAD_LEFT) == 1 && (pad().buttons & kLeft),
              "alphabetic type with a gamepad source: a controller, not a keyboard");
        release(6, 0x501, AKEYCODE_DPAD_LEFT);
        check(press(3, 0x101, AKEYCODE_SPACE) == 0, "a virtual keyboard (id >= 0) is not a keyboard");
        check(press(2, 0x101, AKEYCODE_VOLUME_UP) == 0, "the volume keys (non-alphabetic) go to the system");
        check(count_logs("keyboard: device id 10 \"SINO WEALTH Gaming KB\" (VID:PID 258a:002a): a keyboard") == 1 &&
              count_logs("keyboard: device id 11 ") == 1 && count_logs("keyboard: device id ") == 2,
              "one log line per keyboard, the name without its trailing space; none for the others");
        const int calls = g_jni_calls;
        press(10, 0x101, AKEYCODE_A), release(10, 0x101, AKEYCODE_A);
        check(g_jni_calls == calls, "InputDevice is asked once per id");
    } else {
        return 3;
    }
    std::printf("%s\n", fails ? "FAILED" : "PASSED");
    return fails ? 1 : 0;
}
'''

GAME_HARNESS = r'''
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "input_tsuki.cpp"

namespace nx {
void log(const char* fmt, ...) {
    if (!std::getenv("TEST_LOG")) return;
    va_list a;
    va_start(a, fmt);
    std::vfprintf(stderr, fmt, a);
    va_end(a);
    std::fputc('\n', stderr);
}
namespace plat {
bool keys_wanted = false;
static KeyState g_keys = {};
static uint64_t g_now = 1000000000ull;
void read_keys(KeyState& k) { k = g_keys; }
uint64_t monotonic_ns() { return g_now; }
std::string data_root() { return std::getenv("TEST_DATA") ? std::getenv("TEST_DATA") : "."; }
}  // namespace plat
namespace tsuki::hud {
static int g_notes = 0, g_last = -1;
void note_input(Device d) { g_notes++, g_last = int(d); }
}  // namespace tsuki::hud
}  // namespace nx

using namespace nx;
using namespace nx::plat;
static int fails = 0;
static void check(bool ok, const char* what) {
    fails += !ok;
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
}
static PadState frame() {
    g_now += 16666667;
    PadState p;
    tsuki::keyboard_pad(p);
    return p;
}
static void down(int vk) { g_keys.held[vk] = 1, g_keys.downs[vk]++; }
static void up(int vk) { g_keys.held[vk] = 0; }
static void tap(int vk) { g_keys.downs[vk]++; }  // down and up between two frames
static void idle(int n = 4) { for (int i = 0; i < n; i++) frame(); }  // past the 50 ms of a press
static int notes() { int n = tsuki::hud::g_notes; tsuki::hud::g_notes = 0; return n; }

int main(int argc, char** argv) {
    const std::string sc = argc > 1 ? argv[1] : "";
    check(keys_wanted, "input_tsuki.cpp sets plat::keys_wanted");
    frame();
    if (sc == "defaults") {
        down(0x20);
        PadState p = frame();
        check((p.buttons & kA) && notes() == 1 && tsuki::hud::g_last == int(tsuki::hud::Device::kKeyboard),
              "Space down: A, and the HUD switches to the keyboard");
        bool held = true;
        for (int i = 0; i < 10; i++) held = held && (frame().buttons & kA);
        check(held && notes() == 0, "Space held 10 frames: A all along, no more HUD calls");
        up(0x20);
        check(!(frame().buttons & kA), "Space up: A released");
        tap(0x0D);
        int frames = 0;
        for (int i = 0; i < 6; i++) frames += (frame().buttons & kA) ? 1 : 0;
        check(frames == 3 && notes() == 1, "a tap shorter than a frame (Enter): A for 50 ms (3 frames), one HUD call");
        down(0x11);
        check((frame().buttons & kZR) != 0, "Ctrl (either side, VK 0x11): ZR");
        up(0x11);
        idle();
        notes();
        down(0x1B);
        check((frame().buttons & kY) != 0, "Escape: Y (system menu), as on PC");
        up(0x1B);
        idle();
        down(0x08);
        check((frame().buttons & kB) != 0, "Backspace: B");
        up(0x08);
        idle();
        down(0x21);
        p = frame();
        check(p.ly == kStickMax, "PageUp: left stick up (SUP)");
        up(0x21);
        idle();
        notes();
        down('Z');
        p = frame();
        check(!p.buttons && !p.lx && !p.ly && notes() == 0, "a key without a binding (Z): nothing, the HUD stays");
        up('Z');
        idle();
        down(0x26);
        check((frame().buttons & kUp) && notes() == 1, "Up arrow: UP (mini menu), HUD keyboard");
        check(tsuki::first_key("A") == 0x20 && tsuki::first_key("Y") == 0x1B, "first_key (HUD icons): A = Space, Y = Escape");
    } else if (sc == "ini") {  // NX_INPUT_INI with [teclado] A = Q, Y = Escape
        down(0x20);
        check(!(frame().buttons & kA) && notes() == 0, "input.ini: Space is no longer A");
        up(0x20);
        idle();
        down('Q');
        check((frame().buttons & kA) && notes() == 1, "input.ini: Q = A, the HUD switches to the keyboard");
        up('Q');
        idle();
        down(0x0D);
        check(!(frame().buttons & kA), "input.ini: Enter is not assigned");
        check(tsuki::first_key("A") == 'Q', "first_key: A = Q (the HUD icon)");
    } else {
        return 3;
    }
    std::printf("%s\n", fails ? "FAILED" : "PASSED");
    return fails ? 1 : 0;
}
'''


def write_tree(base, files):
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def run_cases(exe, cases):
    fails = 0
    for sc, env in cases:
        e = {k: v for k, v in os.environ.items() if k not in ("NX_KEYBOARD", "NX_INPUT_INI", "TEST_LOG", "TEST_DATA")}
        e.update(env)
        r = subprocess.run([str(exe), sc], capture_output=True, text=True, env=e)
        out = r.stdout.strip()
        print("\n".join("  " + line for line in out.splitlines() if line not in ("PASSED", "FAILED")))
        if r.returncode != 0 or not out.endswith("PASSED"):
            fails += 1
            print(f"FAIL: case {sc} {env or ''} (exit {r.returncode}) {r.stderr[-1000:]}")
    return fails


def main():
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        print("skip: no g++/clang++")
        return 0
    for f in (INPUT, TSUKI):
        if "AP_ANDROID_KEYBOARD" not in f.read_text(encoding="utf-8"):
            print(f"FAIL: apply_android_keyboard.py is not applied to {f}")
            return 1
    fails = 0
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        write_tree(work / "stubs", STUBS)
        write_tree(work / "posix", POSIX_FALLBACK)
        base = [cxx, "-std=c++20", "-O1", "-D__ANDROID__", f"-I{work / 'stubs'}", f"-I{FW / 'runtime' / 'include'}",
                f"-I{SRC}", f"-I{GAME}"]

        print("platform (platform_android_input.cpp):")
        (work / "platform.cpp").write_text(PLATFORM_HARNESS)
        exe = work / "keyboard_platform_test"
        r = subprocess.run(base + ["-idirafter", str(work / "posix"), str(work / "platform.cpp"), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            if "clock_gettime" in r.stderr or "CLOCK_MONOTONIC" in r.stderr:
                print("  skip: this host has no clock_gettime (run it on Linux, WSL or MSYS2)")
            else:
                print(r.stderr[-4000:])
                print("FAIL: the platform harness does not compile")
                fails += 1
        else:
            fails += run_cases(exe, [("before", {}), ("on", {}), ("off", {"NX_KEYBOARD": "0"})])

        print("game (input_tsuki.cpp):")
        (work / "game.cpp").write_text(GAME_HARNESS)
        exe = work / "keyboard_game_test"
        r = subprocess.run(base + ["-DNX_HUD_GLYPHS", str(work / "game.cpp"), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-4000:])
            print("FAIL: the game harness does not compile")
            fails += 1
        else:
            ini = work / "input.ini"
            ini.write_text("[teclado]\nA = Q\nY = Escape\n")
            fails += run_cases(exe, [("defaults", {"TEST_DATA": str(work / "nothing")}),
                                     ("ini", {"NX_INPUT_INI": str(ini)})])
    print("all passed" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
