// input_tsuki.cpp - teclado, mando Xbox y botones del mouse -> Npad, configurables con input.ini.
//
// Requiere apply_pad_source.py en el framework: plat::pad_source reemplaza la lectura fija de read_pad
// (XInput indice 0 + teclas fijas). Sin input.ini (o sin una seccion) quedan los mapeos de siempre.
//
// input.ini: NX_INPUT_INI si esta definida; si no, junto al exe; si no, en NX_DATA_ROOT (<root>\data\tsuki).
//   [teclado]   <boton del Npad> = <tecla>, <tecla>, ...
//   [mando]     <boton del Npad> = <boton Xbox>, ...
//   [mouse]     <boton del Npad> = Derecho | Medio | RuedaArriba | RuedaAbajo, ...
// Por defecto (sin input.ini) el teclado y el mouse usan el layout de Fate/stay night (ver defaults_keys); el mando, el de Nintendo.
// Una seccion presente reemplaza todos los mapeos por defecto de ese dispositivo (una seccion vacia lo deja sin
// botones). Comentarios con ; o #. Mayusculas indistintas. Un error queda en el log con su linea y se salta.
// Los sticks analogicos del mando van siempre a los del Npad (zona muerta 7849, como antes).
// F11 y Alt+Enter siguen siendo pantalla completa: F11 no se puede asignar y Enter no cuenta con Alt apretado.
//
// Mando (AP_SDL_PAD, apply_sdl_pad.py): por SDL3 (pad_sdl.cpp): Xbox, DualShock 4, DualSense, Pro Controller y
// Joy-Con por USB y Bluetooth; manda el ultimo usado; botones por posicion fisica (los nombres de [mando] son las
// posiciones del Xbox: A = abajo, B = derecha; alias Sur/Este/Oeste/Norte, Cruz/Circulo/..., L1/R1/..., L/R/ZL/ZR).
// NX_SDL_PAD=0, o sin SDL3.dll, vuelve a XInput:
// Mando XInput: el primer indice XInput conectado (0..3). Si se desconecta, o si no hay ninguno, se busca un indice cada
// 250 ms (cada indice se consulta como mucho una vez por segundo: XInputGetState de un indice vacio no es gratis).
// NX_INPUT_TRACE=1: el mapeo cargado y lo que tarda XInputGetState en un indice vacio.
#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <xinput.h>
#endif

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstring>  // AP_HUD_GLYPHS
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <vector>

#include <nx/runtime.h>

#include "input_tsuki.h"
#include "pad_sdl.h"  // AP_SDL_PAD
#include "hud_glyphs.h"  // AP_HUD_GLYPHS
#include "platform.h"

#if defined(_WIN32)
namespace nx {
extern HWND g_window;  // platform_win.cpp
}
#endif

namespace nx::tsuki {

using plat::kStickMax;

void Target::add(const Target& t) {
    bits |= t.bits;
    if (t.lx) lx = t.lx;
    if (t.ly) ly = t.ly;
    if (t.rx) rx = t.rx;
    if (t.ry) ry = t.ry;
}

void press(plat::PadState& p, const Target& t) {
    p.buttons |= t.bits;
    if (t.lx) p.lx = t.lx;
    if (t.ly) p.ly = t.ly;
    if (t.rx) p.rx = t.rx;
    if (t.ry) p.ry = t.ry;
}

namespace {

std::string upper(std::string s) {
    for (char& ch : s) ch = char(std::toupper(static_cast<unsigned char>(ch)));
    return s;
}

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

bool trace_on() {
    static const bool on = [] {
        const char* e = std::getenv("NX_INPUT_TRACE");
        return e && *e && *e != '0';
    }();
    return on;
}

// ---- nombres ------------------------------------------------------------------------------------------------
struct NpadName {
    const char* name;
    Target t;
};
const std::vector<NpadName>& npad_names() {
    using namespace plat;
    static const std::vector<NpadName> v = {
        {"A", {kA}}, {"B", {kB}}, {"X", {kX}}, {"Y", {kY}}, {"L", {kL}}, {"R", {kR}}, {"ZL", {kZL}}, {"ZR", {kZR}},
        {"PLUS", {kPlus}}, {"+", {kPlus}}, {"MINUS", {kMinus}}, {"-", {kMinus}},
        {"UP", {kUp}}, {"DOWN", {kDown}}, {"LEFT", {kLeft}}, {"RIGHT", {kRight}},
        {"STICKL", {kStickL}}, {"STICKR", {kStickR}},
        {"SUP", {0, 0, kStickMax}}, {"SDOWN", {0, 0, -kStickMax}}, {"SLEFT", {0, -kStickMax, 0}},
        {"SRIGHT", {0, kStickMax, 0}},
        {"RSUP", {0, 0, 0, 0, kStickMax}}, {"RSDOWN", {0, 0, 0, 0, -kStickMax}}, {"RSLEFT", {0, 0, 0, -kStickMax, 0}},
        {"RSRIGHT", {0, 0, 0, kStickMax, 0}},
    };
    return v;
}

// Codigos de tecla virtual de Windows (VK_*), escritos como numeros para que el lector compile en cualquier lado.
constexpr int kVkF11 = 0x7A, kVkReturn = 0x0D, kVkMenu = 0x12;
int key_code(const std::string& n) {  // n en mayusculas; 0 si no existe
    if (n.size() == 1 && ((n[0] >= 'A' && n[0] <= 'Z') || (n[0] >= '0' && n[0] <= '9'))) return n[0];
    if (n.size() >= 2 && n.size() <= 3 && n[0] == 'F' && std::isdigit(static_cast<unsigned char>(n[1]))) {
        int k = std::atoi(n.c_str() + 1);
        if (k >= 1 && k <= 24 && std::to_string(k) == n.substr(1)) return 0x70 + k - 1;
    }
    if (n.size() == 4 && n.compare(0, 3, "NUM") == 0 && std::isdigit(static_cast<unsigned char>(n[3]))) return 0x60 + (n[3] - '0');
    static const struct {
        const char* name;
        int vk;
    } kKeys[] = {
        {"SPACE", 0x20},    {"ESPACIO", 0x20},   {"ENTER", 0x0D},    {"RETURN", 0x0D},  {"INTRO", 0x0D},
        {"ESC", 0x1B},      {"ESCAPE", 0x1B},    {"TAB", 0x09},      {"BACKSPACE", 0x08},
        {"SHIFT", 0x10},    {"LSHIFT", 0xA0},    {"RSHIFT", 0xA1},   {"CTRL", 0x11},    {"CONTROL", 0x11},
        {"LCTRL", 0xA2},    {"RCTRL", 0xA3},     {"ALT", 0x12},      {"LALT", 0xA4},    {"RALT", 0xA5},
        {"UP", 0x26},       {"ARRIBA", 0x26},    {"DOWN", 0x28},     {"ABAJO", 0x28},   {"LEFT", 0x25},
        {"IZQUIERDA", 0x25}, {"RIGHT", 0x27},    {"DERECHA", 0x27},  {"PAGEUP", 0x21},  {"PAGEDOWN", 0x22},
        {"HOME", 0x24},     {"INICIO", 0x24},    {"END", 0x23},      {"FIN", 0x23},     {"INSERT", 0x2D},
        {"DELETE", 0x2E},   {"SUPR", 0x2E},      {"COMMA", 0xBC},    {"PERIOD", 0xBE},
    };
    for (const auto& k : kKeys)
        if (n == k.name) return k.vk;
    return 0;
}

// Botones de XInput (XINPUT_GAMEPAD_*), mas dos bits propios para los gatillos (> 100 de 255, como antes).
constexpr uint32_t kSrcLT = 1u << 16, kSrcRT = 1u << 17;
uint32_t pad_source_mask(const std::string& n) {
    static const struct {
        const char* name;
        uint32_t mask;
    } kPad[] = {
        {"A", 0x1000},      {"B", 0x2000},       {"X", 0x4000},       {"Y", 0x8000},       {"LB", 0x0100},
        {"RB", 0x0200},     {"LT", kSrcLT},      {"RT", kSrcRT},      {"START", 0x0010},   {"MENU", 0x0010},
        {"BACK", 0x0020},   {"VIEW", 0x0020},    {"LS", 0x0040},      {"RS", 0x0080},      {"DPADUP", 0x0001},
        {"DPADDOWN", 0x0002}, {"DPADLEFT", 0x0004}, {"DPADRIGHT", 0x0008},
        // AP_SDL_PAD: botones que solo da SDL (pad_sdl.h) y alias. Son posiciones: Sur = Cruz = A del Xbox, etc.
        // Las letras A B X Y siguen siendo las del Xbox (en un mando de Switch, su A es "B" o "Este").
        {"GUIDE", kSrcGuide}, {"HOME", kSrcGuide}, {"PS", kSrcGuide}, {"TOUCHPAD", kSrcTouchpad},
        {"PANEL", kSrcTouchpad}, {"CAPTURA", kSrcMisc1}, {"CAPTURE", kSrcMisc1}, {"MIC", kSrcMisc1},
        {"SUR", 0x1000}, {"SOUTH", 0x1000}, {"ESTE", 0x2000}, {"EAST", 0x2000}, {"OESTE", 0x4000},
        {"WEST", 0x4000}, {"NORTE", 0x8000}, {"NORTH", 0x8000},
        {"CRUZ", 0x1000}, {"CROSS", 0x1000}, {"CIRCULO", 0x2000}, {"CIRCLE", 0x2000}, {"CUADRADO", 0x4000},
        {"SQUARE", 0x4000}, {"TRIANGULO", 0x8000}, {"TRIANGLE", 0x8000},
        {"L1", 0x0100}, {"R1", 0x0200}, {"L2", kSrcLT}, {"R2", kSrcRT}, {"L3", 0x0040}, {"R3", 0x0080},
        {"OPTIONS", 0x0010}, {"SHARE", 0x0020}, {"CREATE", 0x0020},
        {"L", 0x0100}, {"R", 0x0200}, {"ZL", kSrcLT}, {"ZR", kSrcRT}, {"PLUS", 0x0010}, {"+", 0x0010},
        {"MINUS", 0x0020}, {"-", 0x0020},
    };
    for (const auto& k : kPad)
        if (n == k.name) return k.mask;
    return 0;
}

int mouse_source(const std::string& n) {  // MouseSource, -1 si no existe
    if (n == "DERECHO" || n == "RIGHT") return kMouseRight;
    if (n == "MEDIO" || n == "MIDDLE") return kMouseMiddle;
    if (n == "RUEDAARRIBA" || n == "WHEELUP") return kWheelUp;
    if (n == "RUEDAABAJO" || n == "WHEELDOWN") return kWheelDown;
    return -1;
}

}  // namespace

bool parse_target(const std::string& name, Target& out) {
    const std::string n = upper(trim(name));
    for (const NpadName& k : npad_names())
        if (n == k.name) {
            out = k.t;
            return true;
        }
    return false;
}

std::string describe_buttons(uint64_t changed, uint64_t now) {
    std::string s;
    for (const NpadName& n : npad_names())
        if (n.t.bits && (changed & n.t.bits) && n.name[0] != '+' && n.name[0] != '-')
            s += std::string(now & n.t.bits ? " +" : " -") + n.name;
    return s;
}

// ---- configuracion -------------------------------------------------------------------------------------------
namespace {

struct KeyBind {
    int vk;
    Target t;
};
struct PadBind {
    uint32_t mask;
    Target t;
};
struct Config {
    std::vector<KeyBind> keys;
    std::vector<PadBind> pad;
    Target mouse[kMouseSources];
};

void bind(std::vector<KeyBind>& v, const char* target, std::initializer_list<int> keys) {
    Target t;
    parse_target(target, t);
    for (int k : keys) v.push_back({k, t});
}
void bind(std::vector<PadBind>& v, const char* target, uint32_t mask) {
    Target t;
    parse_target(target, t);
    v.push_back({mask, t});
}

// Mapeo por defecto del teclado: el layout de Fate/stay night [Realta Nua] (el mismo que input.ini.fsn).
//   Espacio / Enter = A (avanza)   Abajo = DOWN (avanza en el texto)   Arriba = UP (mini menu)
//   A = L (auto)   R = ZL (retrocede)   F = R (avance rapido)   Ctrl = ZR (acelera mientras se mantiene)
//   Escape, S, L, C = Y (menu de sistema)   Tab = X (menu de dialogos)   Backspace = B (oculta el texto / atras)
//   RePag / AvPag = SUP / SDOWN (pagina por pagina en listados y flowchart)
// Sin stick izquierdo en el teclado ni PLUS/MINUS: como en el input.ini.fsn. Con un input.ini, [teclado] lo reemplaza.
void defaults_keys(std::vector<KeyBind>& v) {
    v.clear();
    bind(v, "A", {0x20, 0x0D});  // Space, Enter
    bind(v, "UP", {0x26});
    bind(v, "DOWN", {0x28});
    bind(v, "LEFT", {0x25});
    bind(v, "RIGHT", {0x27});
    bind(v, "L", {'A'});
    bind(v, "ZL", {'R'});
    bind(v, "R", {'F'});
    bind(v, "ZR", {0x11});  // Ctrl
    bind(v, "Y", {0x1B, 'S', 'L', 'C'});  // Escape, S, L, C
    bind(v, "X", {0x09});  // Tab
    bind(v, "B", {0x08});  // Backspace
    bind(v, "SUP", {0x21});  // PageUp
    bind(v, "SDOWN", {0x22});  // PageDown
}
void defaults_pad(std::vector<PadBind>& v) {  // layout de Nintendo: B del Xbox (derecha) = A del Npad, etc.
    v.clear();
    bind(v, "A", 0x2000), bind(v, "B", 0x1000), bind(v, "X", 0x8000), bind(v, "Y", 0x4000);
    bind(v, "L", 0x0100), bind(v, "R", 0x0200), bind(v, "ZL", kSrcLT), bind(v, "ZR", kSrcRT);
    bind(v, "PLUS", 0x0010), bind(v, "MINUS", 0x0020), bind(v, "STICKL", 0x0040), bind(v, "STICKR", 0x0080);
    bind(v, "UP", 0x0001), bind(v, "DOWN", 0x0002), bind(v, "LEFT", 0x0004), bind(v, "RIGHT", 0x0008);
}
void defaults_mouse(Target* m) {  // layout FSN: derecho = menu de sistema, medio = menu de dialogos, rueda = siguiente / anterior
    for (int k = 0; k < kMouseSources; k++) m[k] = Target{};
    parse_target("Y", m[kMouseRight]);
    parse_target("X", m[kMouseMiddle]);
    parse_target("DOWN", m[kWheelDown]);
    parse_target("ZL", m[kWheelUp]);
}

std::vector<std::filesystem::path> ini_candidates() {
    std::vector<std::filesystem::path> v;
    if (const char* e = std::getenv("NX_INPUT_INI"); e && *e) {
        v.push_back(std::filesystem::u8path(e));
        return v;
    }
#if defined(_WIN32)
    wchar_t exe[MAX_PATH] = {};
    if (GetModuleFileNameW(nullptr, exe, MAX_PATH)) v.push_back(std::filesystem::path(exe).parent_path() / "input.ini");
#endif
    v.push_back(std::filesystem::u8path(plat::data_root()) / "input.ini");
    return v;
}

enum Section { kNone = -1, kKeys, kPad, kMouse };

Config load_config() {
    Config c;
    defaults_keys(c.keys);
    defaults_pad(c.pad);
    defaults_mouse(c.mouse);

    std::filesystem::path path;
    std::ifstream in;
    for (const auto& p : ini_candidates()) {
        in.open(p);
        if (in) {
            path = p;
            break;
        }
        in.clear();
    }
    if (path.empty()) {
        std::string where;
        for (const auto& p : ini_candidates()) where += " " + p.string();
        log("input: sin input.ini (buscado en%s): mapeos por defecto", where.c_str());
        return c;
    }
    const std::string file = path.string();
    int errors = 0;
    auto error = [&](int line, const std::string& what) {
        errors++;
        log("input: %s:%d: %s", file.c_str(), line, what.c_str());
    };
    Section sec = kNone;
    bool replaced[3] = {false, false, false};
    std::string raw;
    for (int line = 1; std::getline(in, raw); line++) {
        if (line == 1 && raw.compare(0, 3, "\xEF\xBB\xBF") == 0) raw.erase(0, 3);  // BOM
        if (size_t k = raw.find_first_of(";#"); k != std::string::npos) raw.erase(k);
        const std::string s = trim(raw);
        if (s.empty()) continue;
        if (s.front() == '[') {
            const std::string name = upper(trim(s.substr(1, s.find(']') == std::string::npos ? std::string::npos : s.find(']') - 1)));
            if (s.back() != ']') error(line, "falta ] en la seccion");
            sec = name == "TECLADO" || name == "KEYBOARD" ? kKeys
                  : name == "MANDO" || name == "PAD" || name == "GAMEPAD" ? kPad
                  : name == "MOUSE" || name == "RATON" ? kMouse
                                                        : kNone;
            if (sec == kNone) {
                error(line, "seccion desconocida [" + name + "] (validas: teclado, mando, mouse); sus lineas se ignoran");
            } else if (!replaced[sec]) {  // la primera vez: reemplaza los mapeos por defecto de ese dispositivo
                replaced[sec] = true;
                if (sec == kKeys) c.keys.clear();
                if (sec == kPad) c.pad.clear();
                if (sec == kMouse)  // AP_MOUSE_INI: [mouse] parte vacio (antes la rueda conservaba el default)
                    for (Target& t : c.mouse) t = Target{};
            }
            continue;
        }
        const size_t eq = s.find('=');
        if (eq == std::string::npos) {
            error(line, "falta '=' (formato: <boton del Npad> = <tecla o boton>, ...)");
            continue;
        }
        if (sec == kNone) {
            error(line, "fuera de una seccion valida, se ignora");
            continue;
        }
        const std::string tname = trim(s.substr(0, eq));
        Target t;
        if (!parse_target(tname, t)) {
            error(line, "\"" + tname + "\" no es un boton del Npad (A B X Y L R ZL ZR PLUS MINUS UP DOWN LEFT RIGHT "
                        "STICKL STICKR SUP SDOWN SLEFT SRIGHT RSUP RSDOWN RSLEFT RSRIGHT)");
            continue;
        }
        std::string list = s.substr(eq + 1);
        size_t pos = 0;
        while (pos <= list.size()) {
            size_t comma = list.find(',', pos);
            const std::string item = trim(list.substr(pos, comma == std::string::npos ? std::string::npos : comma - pos));
            pos = comma == std::string::npos ? list.size() + 1 : comma + 1;
            if (item.empty()) continue;
            const std::string n = upper(item);
            if (sec == kKeys) {
                int vk = key_code(n);
                if (vk == kVkF11)
                    error(line, "F11 es pantalla completa: no se puede asignar");
                else if (!vk)
                    error(line, "tecla desconocida \"" + item + "\" (A-Z, 0-9, F1-F24, Num0-Num9, Space, Enter, Escape, "
                                "Tab, Backspace, Shift, Ctrl, Alt, Up, Down, Left, Right, PageUp, PageDown, Home, End...)");
                else
                    c.keys.push_back({vk, t});
            } else if (sec == kPad) {
                uint32_t m = pad_source_mask(n);
                if (!m)
                    error(line, "boton de mando desconocido \"" + item + "\" (A B X Y LB RB LT RT Start Back LS RS "
                                "DPadUp DPadDown DPadLeft DPadRight Guide Touchpad Captura; alias: Sur Este Oeste Norte, "
                                "Cruz Circulo Cuadrado Triangulo L1 R1 L2 R2 L3 R3 Options Share, L R ZL ZR Plus Minus)");  // AP_SDL_PAD
                else
                    c.pad.push_back({m, t});
            } else {
                int ms = mouse_source(n);
                if (ms < 0)
                    error(line, "boton de mouse desconocido \"" + item + "\" (Derecho, Medio, RuedaArriba, RuedaAbajo; "
                                "el izquierdo es el tactil)");
                else
                    c.mouse[ms].add(t);
            }
        }
    }
    log("input: %s: %zu teclas, %zu botones del mando%s%s%s, %d errores", file.c_str(), c.keys.size(), c.pad.size(),
        replaced[kKeys] ? "" : " (teclado por defecto)", replaced[kPad] ? "" : " (mando por defecto)",
        replaced[kMouse] ? "" : " (mouse por defecto)", errors);
    return c;
}

// NX_MOUSE_RIGHT / MIDDLE / WHEEL_UP / WHEEL_DOWN (las de antes) tienen prioridad sobre input.ini.
void mouse_env(Target* m) {
    static const char* const kEnv[kMouseSources] = {"NX_MOUSE_RIGHT", "NX_MOUSE_MIDDLE", "NX_MOUSE_WHEEL_UP",
                                                    "NX_MOUSE_WHEEL_DOWN"};
    for (int k = 0; k < kMouseSources; k++) {
        const char* e = std::getenv(kEnv[k]);
        if (!e) continue;
        Target t;
        if (!*e)
            m[k] = Target{};
        else if (parse_target(e, t))
            m[k] = t;
        else
            log("input: %s=%s no es un boton del Npad, se ignora", kEnv[k], e);
    }
}

const Config& config() {
    static const Config c = [] {
        Config x = load_config();
        mouse_env(x.mouse);
        if (trace_on()) {
            for (const KeyBind& k : x.keys)
                log("input: tecla %#x ->%s (stick %d,%d %d,%d)", k.vk, describe_buttons(k.t.bits, k.t.bits).c_str(),
                    k.t.lx, k.t.ly, k.t.rx, k.t.ry);
            for (const PadBind& p : x.pad)
                log("input: mando %#x ->%s (stick %d,%d %d,%d)", p.mask, describe_buttons(p.t.bits, p.t.bits).c_str(),
                    p.t.lx, p.t.ly, p.t.rx, p.t.ry);
            for (int k = 0; k < kMouseSources; k++)
                log("input: mouse %d ->%s (stick %d,%d)", k, describe_buttons(x.mouse[k].bits, x.mouse[k].bits).c_str(),
                    x.mouse[k].lx, x.mouse[k].ly);
        }
        return x;
    }();
    return c;
}

}  // namespace

const Target& mouse_target(MouseSource s) { return config().mouse[s]; }

// AP_HUD_GLYPHS: la primera linea de [teclado] (en orden) cuyo boton es exactamente ese.
int first_key(const std::string& npad_name) {
    Target want;
    if (!parse_target(npad_name, want)) return 0;
    for (const KeyBind& k : config().keys)
        if (k.t.bits == want.bits && k.t.lx == want.lx && k.t.ly == want.ly && k.t.rx == want.rx && k.t.ry == want.ry)
            return k.vk;
    return 0;
}

// ---- lectura (Windows) -----------------------------------------------------------------------------------------
#if defined(_WIN32)
namespace {

#pragma comment(lib, "xinput.lib")

// El primer indice conectado; sin mando, un indice cada 250 ms (0, 1, 2, 3, 0...).
bool read_xinput(XINPUT_GAMEPAD& g) {
    static std::mutex m;
    static int slot = -1, next = 0, timed = 0;
    static uint64_t next_scan = 0;
    std::lock_guard lk(m);
    XINPUT_STATE xs{};
    if (slot >= 0) {
        if (XInputGetState(DWORD(slot), &xs) == ERROR_SUCCESS) {
            g = xs.Gamepad;
            return true;
        }
        log("input: mando del indice %d desconectado", slot);
        next = slot, slot = -1, next_scan = 0;
    }
    const uint64_t now = plat::monotonic_ns();
    if (now < next_scan) return false;
    next_scan = now + 250000000ull;
    const int k = next;
    next = (next + 1) % 4;
    const uint64_t t0 = plat::monotonic_ns();
    if (XInputGetState(DWORD(k), &xs) == ERROR_SUCCESS) {
        slot = k;
        log("input: mando XInput en el indice %d", k);
        g = xs.Gamepad;
        return true;
    }
    if (trace_on() && timed < 8)
        timed++, log("input: XInputGetState(%d) sin mando: %.1f us", k, double(plat::monotonic_ns() - t0) / 1000.0);
    return false;
}

int32_t dead_zone(int v) { return (v > -7849 && v < 7849) ? 0 : std::max(-kStickMax, std::min(kStickMax, int32_t(v))); }

// AP_SDL_PAD: el mando por SDL (pad_sdl.cpp) o, con NX_SDL_PAD=0 o sin SDL3.dll, por XInput como antes. Mientras SDL
// inicializa (una vez, al arrancar) no hay mando.
struct MandoLeido {
    uint32_t held = 0;  // bits de XInput + kSrcLT/kSrcRT + kSrcGuide/kSrcTouchpad/kSrcMisc1
    int lx = 0, ly = 0, rx = 0, ry = 0;
};
bool leer_mando(MandoLeido& m) {
    switch (sdl_pad_mode()) {
    case SdlPadMode::kPending: return false;
    case SdlPadMode::kSdl: {
        SdlPad s;
        if (!sdl_pad_read(s)) return false;
        m.held = s.buttons | (s.lt > 100 ? kSrcLT : 0) | (s.rt > 100 ? kSrcRT : 0);
        m.lx = s.lx, m.ly = s.ly, m.rx = s.rx, m.ry = s.ry;
        return true;
    }
    case SdlPadMode::kXInput: break;
    }
    XINPUT_GAMEPAD g{};
    if (!read_xinput(g)) return false;
#ifdef NX_HUD_GLYPHS
    {   // AP_HUD_GLYPHS: un boton nuevo, o un stick o gatillo pasado la mitad, es entrada real del mando (Xbox)
        static uint16_t antes = 0;
        const uint16_t nuevos = uint16_t(g.wButtons & ~antes);
        antes = g.wButtons;
        auto mitad = [](int v) { return v > 16384 || v < -16384; };
        if (nuevos || mitad(g.sThumbLX) || mitad(g.sThumbLY) || mitad(g.sThumbRX) || mitad(g.sThumbRY) ||
            g.bLeftTrigger > 100 || g.bRightTrigger > 100)
            hud::note_input(hud::Device::kXbox);
    }
#endif
    m.held = g.wButtons | (g.bLeftTrigger > 100 ? kSrcLT : 0) | (g.bRightTrigger > 100 ? kSrcRT : 0);
    m.lx = g.sThumbLX, m.ly = g.sThumbLY, m.rx = g.sThumbRX, m.ry = g.sThumbRY;
    return true;
}

void tsuki_pad_source(plat::PadState& p) {
    const Config& c = config();
    MandoLeido g;
    if (leer_mando(g)) {
        p.lx = dead_zone(g.lx), p.ly = dead_zone(g.ly);
        p.rx = dead_zone(g.rx), p.ry = dead_zone(g.ry);
        for (const PadBind& b : c.pad)
            if (g.held & b.mask) press(p, b.t);
    }
#ifdef NX_HUD_GLYPHS
    // AP_HUD_GLYPHS: una tecla asignada que se aprieta ahora (no mientras se mantiene) es entrada del teclado
    static uint8_t teclas_antes[256];
    uint8_t teclas_ahora[256] = {};
    struct Guardar {
        uint8_t* a;
        const uint8_t* b;
        ~Guardar() { std::memcpy(a, b, 256); }
    } guardar{teclas_antes, teclas_ahora};
#endif
    if (g_window && GetForegroundWindow() == g_window) {
        const bool alt = GetAsyncKeyState(kVkMenu) & 0x8000;  // Alt+Enter: pantalla completa, no el boton de Enter
        for (const KeyBind& k : c.keys)
            if (!(alt && k.vk == kVkReturn) && (GetAsyncKeyState(k.vk) & 0x8000)) {
                press(p, k.t);
#ifdef NX_HUD_GLYPHS
                if (!(teclas_antes[k.vk & 0xFF] & 1)) hud::note_input(hud::Device::kKeyboard);  // AP_HUD_GLYPHS
                teclas_ahora[k.vk & 0xFF] = 1;
#endif
            }
    }
}

const bool g_pad_source_set = [] {
    plat::pad_source = tsuki_pad_source;  // AP_PAD_SOURCE (apply_pad_source.py)
    return true;
}();

}  // namespace
#endif

}  // namespace nx::tsuki