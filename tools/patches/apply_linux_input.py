r"""apply_linux_input.py - native Linux: keyboard, mouse and controllers as on Windows (AP_LINUX_INPUT).

platform_linux.cpp had a fixed mapping (Space = A, Shift = B, Esc = -, arrows / WASD, the first SDL controller) and no
mouse; input.ini and the Windows input of Tsukihime (input_tsuki.cpp, hle_hid_tsuki.cpp, pad_sdl.cpp) were Windows only.
Now, on native Linux:
- keyboard: platform_linux.cpp keeps the keys by Windows virtual-key code (plat::read_keys, as on Android), so
  [teclado] of input.ini and its defaults (the Fate/stay night layout) work as on Windows; a press shorter than a frame
  still counts (50 ms), keys are let go when the window loses focus, and Alt+Enter is full screen, not Enter.
- mouse: plat::read_pointer (client pixels, buttons, presses, wheel in 120 steps), so the left click is a touch (and
  the title logo click changes the video), right / middle / wheel go through [mouse], and the right click goes back in
  a submenu, all as on Windows (the same code of hle_hid_tsuki.cpp).
- controllers: pad_sdl.cpp without its own thread (SDL already runs on the main thread here): the main thread hands it
  every SDL event (plat::sdl_event_hook); the last used controller rules, [mando] of input.ini, LED, logs, and the HUD
  icons by controller type, as on Windows. NX_SDL_PAD=0: no controller.
  The default [mando] mapping had only ZL and ZR on Linux: bind(v, "A", 0x2000) picked std::bind (argument-dependent
  lookup, with libstdc++'s <functional> in view); an int overload of bind fixes it. NX_INPUT_TRACE=1 also logs every
  controller button and stick as SDL sees it.
- plat::pad_source (keyboard + controller through input.ini) replaces the fixed mapping of platform_linux.cpp.
Windows and Android do not change (every change is for Linux without Android, or in platform_linux.cpp).
"""
from _patch import run

MARK = "AP_LINUX_INPUT"
PH = "runtime/src/platform.h"
PL = "runtime/src/platform_linux.cpp"
HID = "games/tsukihime_common/hle_hid_tsuki.cpp"
INPUT = "games/tsukihime_common/input_tsuki.cpp"
INPUT_H = "games/tsukihime_common/input_tsuki.h"
PAD = "games/tsukihime_common/pad_sdl.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------ platform.h: what Linux now offers
    (PH,
     '''}  // namespace nx::plat
''',
     '''#if defined(__linux__) && !defined(__ANDROID__)  // AP_LINUX_INPUT (apply_linux_input.py)
// The keyboard by Windows virtual-key code ('A'-'Z', '0'-'9', 0x20 Space, 0x0D Enter...; 0x10/0x11/0x12 Shift/Ctrl/Alt
// are either side), for a game that reads it itself (Tsukihime: input.ini). held: down now; downs: presses so far (it
// wraps). Released when the window loses focus. platform_linux.cpp, any thread.
struct KeyState {
    uint8_t held[256];
    uint8_t downs[256];
};
void read_keys(KeyState& k);
void read_pointer(PointerState& p);  // the mouse over the window (PointerState above), client pixels
// Every SDL event the main thread takes (const SDL_Event*), and a null one after each round of events: for a game that
// reads SDL itself (Tsukihime: the controllers of pad_sdl.cpp). Main thread only. Null (the default): nothing.
using SdlEventHook = void (*)(const void* event);
extern SdlEventHook sdl_event_hook;
#endif

}  // namespace nx::plat
'''),
    # ------------------------------------------------------------------ platform_linux.cpp: keyboard and mouse state
    (PL,
     '''static void sample_input() {
    PadState p;
''',
     '''// AP_LINUX_INPUT (apply_linux_input.py): the keyboard by Windows virtual-key code and the mouse, from the SDL events of
// the main thread, for the game's own reading (plat::read_keys / read_pointer; Tsukihime: input.ini).
SdlEventHook sdl_event_hook = nullptr;
namespace {
std::mutex g_input_lock;
KeyState g_keys{};
PointerState g_pointer{};

int key_vk(const SDL_KeyboardEvent& k) {
    // Letters and digits follow the keyboard layout, as Windows' virtual keys; the rest by position.
    if (k.key >= SDLK_A && k.key <= SDLK_Z) return 'A' + int(k.key - SDLK_A);
    if (k.key >= SDLK_0 && k.key <= SDLK_9) return '0' + int(k.key - SDLK_0);
    const SDL_Scancode s = k.scancode;
    if (s >= SDL_SCANCODE_F1 && s <= SDL_SCANCODE_F12) return 0x70 + int(s - SDL_SCANCODE_F1);
    if (s >= SDL_SCANCODE_F13 && s <= SDL_SCANCODE_F24) return 0x7C + int(s - SDL_SCANCODE_F13);
    if (s >= SDL_SCANCODE_KP_1 && s <= SDL_SCANCODE_KP_9) return 0x61 + int(s - SDL_SCANCODE_KP_1);
    switch (s) {
    case SDL_SCANCODE_KP_0: return 0x60;
    case SDL_SCANCODE_SPACE: return 0x20;
    case SDL_SCANCODE_RETURN:
    case SDL_SCANCODE_KP_ENTER: return 0x0D;
    case SDL_SCANCODE_ESCAPE: return 0x1B;
    case SDL_SCANCODE_TAB: return 0x09;
    case SDL_SCANCODE_BACKSPACE: return 0x08;
    case SDL_SCANCODE_LSHIFT: return 0xA0;
    case SDL_SCANCODE_RSHIFT: return 0xA1;
    case SDL_SCANCODE_LCTRL: return 0xA2;
    case SDL_SCANCODE_RCTRL: return 0xA3;
    case SDL_SCANCODE_LALT: return 0xA4;
    case SDL_SCANCODE_RALT: return 0xA5;
    case SDL_SCANCODE_LEFT: return 0x25;
    case SDL_SCANCODE_UP: return 0x26;
    case SDL_SCANCODE_RIGHT: return 0x27;
    case SDL_SCANCODE_DOWN: return 0x28;
    case SDL_SCANCODE_PAGEUP: return 0x21;
    case SDL_SCANCODE_PAGEDOWN: return 0x22;
    case SDL_SCANCODE_END: return 0x23;
    case SDL_SCANCODE_HOME: return 0x24;
    case SDL_SCANCODE_INSERT: return 0x2D;
    case SDL_SCANCODE_DELETE: return 0x2E;
    case SDL_SCANCODE_COMMA: return 0xBC;
    case SDL_SCANCODE_PERIOD: return 0xBE;
    default: return 0;
    }
}

void set_key(int vk, bool down) {  // g_input_lock held
    if (down && !g_keys.held[vk]) g_keys.downs[vk]++;
    g_keys.held[vk] = down;
}

void input_event(const SDL_Event& e) {
    switch (e.type) {
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP: {
        const bool down = e.type == SDL_EVENT_KEY_DOWN;
        if (down && e.key.repeat) break;
        const int vk = key_vk(e.key);
        if (!vk || (down && vk == 0x0D && (e.key.mod & SDL_KMOD_ALT))) break;  // Alt+Enter: full screen, not Enter
        std::lock_guard lk(g_input_lock);
        set_key(vk, down);
        const int either = vk == 0xA0 || vk == 0xA1 ? 0x10 : vk == 0xA2 || vk == 0xA3 ? 0x11 : vk == 0xA4 || vk == 0xA5 ? 0x12 : 0;
        if (either) set_key(either, down || g_keys.held[vk ^ 1]);
        break;
    }
    case SDL_EVENT_WINDOW_FOCUS_LOST: {
        std::lock_guard lk(g_input_lock);
        std::memset(g_keys.held, 0, sizeof g_keys.held);
        g_pointer.buttons = 0;
        break;
    }
    case SDL_EVENT_MOUSE_MOTION:
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: {
        // SDL gives window coordinates; the client pixels are those times the pixel density (HiDPI / Wayland scale).
        const float density = g_window ? SDL_GetWindowPixelDensity(g_window) : 1.0f;
        const bool motion = e.type == SDL_EVENT_MOUSE_MOTION;
        const float x = motion ? e.motion.x : e.button.x, y = motion ? e.motion.y : e.button.y;
        std::lock_guard lk(g_input_lock);
        g_pointer.x = int32_t(x * (density > 0 ? density : 1.0f)), g_pointer.y = int32_t(y * (density > 0 ? density : 1.0f));
        if (motion) break;
        const int b = e.button.button == SDL_BUTTON_LEFT ? 0 : e.button.button == SDL_BUTTON_RIGHT ? 1
                    : e.button.button == SDL_BUTTON_MIDDLE ? 2 : -1;
        if (b < 0) break;
        if (e.type == SDL_EVENT_MOUSE_BUTTON_DOWN)
            g_pointer.buttons |= 1u << b, g_pointer.downs[b]++;
        else
            g_pointer.buttons &= ~(1u << b);
        break;
    }
    case SDL_EVENT_MOUSE_WHEEL: {  // whole notches, 120 each, positive away from the user (as Windows' WHEEL_DELTA)
        const int n = e.wheel.direction == SDL_MOUSEWHEEL_FLIPPED ? -e.wheel.integer_y : e.wheel.integer_y;
        std::lock_guard lk(g_input_lock);
        g_pointer.wheel += n * 120;
        break;
    }
    default:
        break;
    }
}
}  // namespace

void read_keys(KeyState& k) {
    std::lock_guard lk(g_input_lock);
    k = g_keys;
}

void read_pointer(PointerState& p) {
    std::lock_guard lk(g_input_lock);
    p = g_pointer;
    p.w = g_pixel_w, p.h = g_pixel_h;
}

static void sample_input() {
    if (pad_source) return;  // AP_LINUX_INPUT: the game reads keyboard and controllers itself (read_pad below)
    PadState p;
'''),
    (PL,
     '''void read_pad(PadState& p) {
    {
''',
     '''void read_pad(PadState& p) {
    if (pad_source) {  // AP_LINUX_INPUT: the game's own reading (Tsukihime: input.ini), as on Windows
        pad_source(p);
        input_script(p);
        return;
    }
    {
'''),
    (PL,
     '''        while (SDL_PollEvent(&e)) {
''',
     '''        while (SDL_PollEvent(&e)) {
            input_event(e);  // AP_LINUX_INPUT: keyboard and mouse state; then the game's own reader of SDL events
            if (sdl_event_hook) sdl_event_hook(&e);
'''),
    (PL,
     '''        std::deque<std::function<void()>> work;
''',
     '''        if (sdl_event_hook) sdl_event_hook(nullptr);  // AP_LINUX_INPUT: a round of events is over
        std::deque<std::function<void()>> work;
'''),
    # ------------------------------------------------------------------ hle_hid_tsuki.cpp: the Windows mouse code
    (HID,
     '''#if defined(_WIN32)
constexpr uint64_t kMs = 1000000;
''',
     '''#if defined(_WIN32) || (defined(__linux__) && !defined(__ANDROID__))  // AP_LINUX_INPUT: native Linux too
constexpr uint64_t kMs = 1000000;
'''),
    (HID,
     '''    touch_pad(p);  // AP_ANDROID_INPUT: the touch gestures
#endif
''',
     '''    touch_pad(p);  // AP_ANDROID_INPUT: the touch gestures
#endif
#if defined(__linux__) && !defined(__ANDROID__)
    mouse_pad(p);  // AP_LINUX_INPUT: right / middle buttons and wheel (input.ini [mouse]), as on Windows
#endif
'''),
    (HID,
     '''std::mutex g_touch_m;
''',
     '''#if defined(__linux__) && !defined(__ANDROID__)  // AP_LINUX_INPUT: the same as on Windows (picture only, no bars)
struct ScreenPoint {
    bool inside;
    int32_t x, y;
};
ScreenPoint to_screen(const plat::PointerState& p) {
    if (!p.w || !p.h) return {false, 0, 0};
    const plat::PictureRect r = plat::picture_rect(p.w, p.h);
    if (!r.w || !r.h) return {false, 0, 0};
    const double u = double(p.x - r.x) / r.w, v = double(p.y - r.y) / r.h;
    return {u >= 0 && u < 1 && v >= 0 && v < 1, std::clamp(int32_t(u * kTouchW), 0, kTouchW - 1),
            std::clamp(int32_t(v * kTouchH), 0, kTouchH - 1)};
}

// True if title_bg.cpp took this click (the logo of the title menu): the game does not get it.
bool title_logo_click(const ScreenPoint& pt) {
#ifdef NX_TITLE_CLICK
    return tsuki_title::logo_click((pt.x + 0.5f) / kTouchW, (pt.y + 0.5f) / kTouchH);
#else
    (void)pt;
    return false;
#endif
}
#endif

std::mutex g_touch_m;
'''),
    (HID,
     '''#if defined(_WIN32)
    if (!mouse_on()) return s;
''',
     '''#if defined(_WIN32) || (defined(__linux__) && !defined(__ANDROID__))  // AP_LINUX_INPUT: native Linux too
    if (!mouse_on()) return s;
'''),
    # ------------------------------------------------------------------ input_tsuki: keyboard and controller reading
    (INPUT_H,
     '''int first_key(const std::string& npad_name);
''',
     '''int first_key(const std::string& npad_name);

#if defined(__linux__) && !defined(__ANDROID__)  // AP_LINUX_INPUT (apply_linux_input.py)
// The keyboard (plat::read_keys) through [teclado] of input.ini or the defaults, as on Windows; also switches the HUD to
// the keyboard icons when an assigned key goes down. From the pad source (input_tsuki.cpp).
void keyboard_pad(plat::PadState& p);
#endif
'''),
    (INPUT,
     '''// ---- lectura (Windows) -----------------------------------------------------------------------------------------
#if defined(_WIN32)
namespace {
''',
     '''// ---- AP_LINUX_INPUT (apply_linux_input.py): keyboard and controllers on native Linux -------------------------------
// platform_linux.cpp keeps the keys by Windows virtual-key code (plat::read_keys), so [teclado] and the defaults work as
// on Windows, keyboard_tiles.bin included. A held key presses its button; a key that goes down presses it for at least
// 50 ms (a tap shorter than a frame still reaches the game) and, if it is assigned, switches the HUD to the keyboard
// icons. Controllers: pad_sdl.cpp (the SDL events of the main thread) through [mando], as on Windows.
#if defined(__linux__) && !defined(__ANDROID__)
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
int32_t linux_dead_zone(int v) { return (v > -7849 && v < 7849) ? 0 : std::max(-kStickMax, std::min(kStickMax, int32_t(v))); }

void linux_pad_source(plat::PadState& p) {
    const Config& c = config();
    SdlPad s;
    if (sdl_pad_mode() == SdlPadMode::kSdl && sdl_pad_read(s)) {
        const uint32_t held = s.buttons | (s.lt > 100 ? kSrcLT : 0) | (s.rt > 100 ? kSrcRT : 0);
        p.lx = linux_dead_zone(s.lx), p.ly = linux_dead_zone(s.ly);
        p.rx = linux_dead_zone(s.rx), p.ry = linux_dead_zone(s.ry);
        for (const PadBind& b : c.pad)
            if (held & b.mask) press(p, b.t);
    }
    keyboard_pad(p);
}

const bool g_linux_pad_source_set = [] {
    plat::pad_source = linux_pad_source;  // read_pad of platform_linux.cpp calls it instead of its fixed mapping
    return true;
}();
}  // namespace
#endif

// ---- lectura (Windows) -----------------------------------------------------------------------------------------
#if defined(_WIN32)
namespace {
'''),
    # ------------------------------------------------------------------ input_tsuki.cpp: std::bind took the defaults
    (INPUT,
     '''void bind(std::vector<PadBind>& v, const char* target, uint32_t mask) {
    Target t;
    parse_target(target, t);
    v.push_back({mask, t});
}
''',
     '''void bind(std::vector<PadBind>& v, const char* target, uint32_t mask) {
    Target t;
    parse_target(target, t);
    v.push_back({mask, t});
}
// AP_LINUX_INPUT: with libstdc++ (Linux) <functional> comes in through other headers, and bind(v, "A", 0x2000) with an
// int literal found std::bind by argument-dependent lookup (std::vector): an exact match that beat the uint32_t one,
// so the call made a bind object and dropped it, and the default controller mapping was only ZL and ZR. An int
// overload is an exact match too and, not being a template, wins.
void bind(std::vector<PadBind>& v, const char* target, int mask) { bind(v, target, uint32_t(mask)); }
'''),
    # ------------------------------------------------------------------ pad_sdl.cpp: no thread of its own on Linux
    (PAD,
     '''#include "hud_glyphs.h"  // AP_HUD_GLYPHS
''',
     '''#include "hud_glyphs.h"  // AP_HUD_GLYPHS
#include "platform.h"    // AP_LINUX_INPUT: sdl_event_hook
'''),
    (PAD,
     '''}  // namespace

SdlPadMode sdl_pad_mode() {
''',
     '''#if defined(__linux__) && !defined(__ANDROID__)
// AP_LINUX_INPUT (apply_linux_input.py): on native Linux SDL already runs on the main thread (platform_linux.cpp: the
// window, the gamepads, its event loop), so there is no thread of our own: the main thread hands every SDL event to
// Mandos (plat::sdl_event_hook) and a null one after each round, to publish the snapshot. The controllers already
// connected arrive as SDL_EVENT_GAMEPAD_ADDED with the first events.
// NX_INPUT_TRACE=1: every controller button as SDL sees it (raw joystick and mapped gamepad) and every stick or
// trigger past half, to tell a controller that sends nothing from one that is read but mapped wrong.
void traza_linux(const SDL_Event& e) {
    switch (e.type) {
    case SDL_EVENT_JOYSTICK_BUTTON_DOWN:
    case SDL_EVENT_JOYSTICK_BUTTON_UP:
        log("input: SDL joystick id %u, boton %d %s", unsigned(e.jbutton.which), int(e.jbutton.button),
            e.jbutton.down ? "abajo" : "arriba");
        break;
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
    case SDL_EVENT_GAMEPAD_BUTTON_UP: {
        const char* n = SDL_GetGamepadStringForButton(SDL_GamepadButton(e.gbutton.button));
        log("input: SDL mando id %u, boton %s %s", unsigned(e.gbutton.which), n ? n : "?", e.gbutton.down ? "abajo" : "arriba");
        break;
    }
    case SDL_EVENT_GAMEPAD_AXIS_MOTION: {
        static int8_t lado[64][SDL_GAMEPAD_AXIS_COUNT];  // -1, 0 or 1 by id (mod 64) and axis
        const int v = e.gaxis.value;
        const int8_t ahora = v > 16384 ? 1 : v < -16384 ? -1 : 0;
        int8_t& antes = lado[e.gaxis.which % 64][e.gaxis.axis % SDL_GAMEPAD_AXIS_COUNT];
        if (ahora != antes) {
            const char* n = SDL_GetGamepadStringForAxis(SDL_GamepadAxis(e.gaxis.axis));
            log("input: SDL mando id %u, eje %s %d", unsigned(e.gaxis.which), n ? n : "?", v);
        }
        antes = ahora;
        break;
    }
    default:
        break;
    }
}

void evento_linux(const void* e) {
    static Mandos* mandos = [] {
        const int v = SDL_GetVersion();
        log("input: mandos por SDL %d.%d.%d (eventos del hilo principal; NX_SDL_PAD=0 los apaga)", SDL_VERSIONNUM_MAJOR(v),
            SDL_VERSIONNUM_MINOR(v), SDL_VERSIONNUM_MICRO(v));
        return new Mandos;  // never destroyed, like Estado
    }();
    if (e) {
        const SDL_Event& ev = *static_cast<const SDL_Event*>(e);
        if (trace_on()) traza_linux(ev);
        mandos->evento(ev);
    } else {
        mandos->publicar();
        if (trace_on()) {  // what the game will read: the buttons of the active controller when they change
            static uint32_t antes = 0;
            Estado& s = st();
            std::lock_guard lk(s.m);
            const uint32_t ahora = s.hay ? s.pad.buttons : 0;
            if (ahora != antes) log("input: mando activo, botones %#x (hay %d)", unsigned(ahora), int(s.hay));
            antes = ahora;
        }
    }
}

const bool g_evento_linux_set = [] {
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");  // before SDL_Init (main): read without focus too
    st().mode = int(SdlPadMode::kSdl);
    plat::sdl_event_hook = evento_linux;
    return true;
}();
#endif

}  // namespace

SdlPadMode sdl_pad_mode() {
'''),
    (PAD,
     '''        std::thread(hilo).detach();
''',
     '''#if defined(__linux__) && !defined(__ANDROID__)
        (void)&hilo;  // AP_LINUX_INPUT: no thread of its own on native Linux (evento_linux, above)
#else
        std::thread(hilo).detach();
#endif
'''),
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\input_tsuki.cpp', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\input_tsuki.cpp', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),
    @('both', 'framework\\runtime\\src\\platform_linux.cpp', 'AP_LINUX_INPUT', 'apply_linux_input.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_LINUX_INPUT', 'apply_linux_input.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
