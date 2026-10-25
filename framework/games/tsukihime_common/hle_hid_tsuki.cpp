// hle_hid_tsuki.cpp - nn::hid que Tsukihime importa y runtime/src/hle_hid.cpp no cubre (Wonder no las usa).
//
// Va en games/tsukihime/ (el CMake del framework compila games/<juego>/*.cpp).
// Sin esto, nn::hid::InitializeTouchScreen cae al codigo real del sdk, que habla por IPC con un servicio 'hid'
// que no existe, no recibe memoria compartida y lee un puntero nulo (crash en SharedMemoryAccessor::Activate).
//
// Lo que es software puro del sdk (constructores de las clases de vibracion, ControllerSupportArg::SetDefault,
// SixAxisSensorState::GetQuaternion) no se intercepta: se enlaza al sdk recompilado y corre tal cual.
// Solo se reemplazan las funciones que tocarian el servicio hid.
//
// Mouse (Windows, requiere apply_mouse.py y apply_aspect.py en el framework):
//   clic izquierdo = tocar la pantalla en ese punto; mantener y arrastrar = mantener el toque. El punto se convierte
//   con plat::picture_rect(), el mismo rectangulo en que el blit dibuja la imagen (con o sin franjas).
//   clic derecho / medio / rueda = botones del mando: input.ini [mouse] o NX_MOUSE_RIGHT / MIDDLE / WHEEL_UP /
//   WHEEL_DOWN (input_tsuki.cpp). Por defecto derecho = B, rueda = stick derecho arriba/abajo (listas y
//   flowchart, item por item), medio nada.
//   NX_MOUSE=0 lo apaga todo. NX_INPUT_TRACE=1 deja en el log las lecturas del tactil, cada toque y cada boton
//   del mando que cambia (para ver que hace el juego con cada uno).
#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>

#include <nx/runtime.h>

#include "input_tsuki.h"
#include "platform.h"

namespace nx {
namespace {

std::atomic<int64_t> g_touch_sampling{0};

void ok(CPU& c) { c.x[0] = 0; }      // Result 0 / void
void no(CPU& c) { c.x[0] = 0; }      // bool false

// Reusa una HLE del framework por nombre (hle_hid.cpp), para no duplicar la lectura del mando.
void forward(CPU& c, const char* name) {
    HleFn fn = hle_lookup(name);
    if (!fn) fatal("hid: falta la HLE %s", name);
    fn(c);
}

bool env_on(const char* name, bool fallback) {
    const char* e = std::getenv(name);
    return e && *e ? e[0] != '0' : fallback;
}
bool mouse_on() {
    static const bool on = env_on("NX_MOUSE", true);
    return on;
}
bool trace_on() {
    static const bool on = env_on("NX_INPUT_TRACE", false);
    return on;
}

// ---- Npad -------------------------------------------------------------------------------------------------------
// NpadJoyLeftState / NpadJoyRightState tienen el mismo layout que NpadFullKeyState: se usa la misma lectura.
void GetNpadStateJoy(CPU& c) { forward(c, "_ZN2nn3hid12GetNpadStateEPNS0_16NpadFullKeyStateERKj"); }

void GetNpadJoyHoldType(CPU& c) { c.x[0] = 1; }  // NpadJoyHoldType_Horizontal (el valor por defecto del sistema)

// NpadControllerColor: {u32 main; u32 sub} (RGBA). Colores de un Pro Controller generico.
void put_color(uint64_t p) {
    if (!p) return;
    uint32_t col[2] = {0xFF2D2D2Du, 0xFFE6E6E6u};
    std::memcpy(reinterpret_cast<void*>(p), col, sizeof col);
}
void GetNpadControllerColor(CPU& c) {  // (NpadControllerColor*, const u32& id) -> Result
    put_color(c.x[0]);
    c.x[0] = 0;
}
void GetNpadControllerColorLR(CPU& c) {  // (NpadControllerColor* left, NpadControllerColor* right, const u32& id)
    put_color(c.x[0]);
    put_color(c.x[1]);
    c.x[0] = 0;
}

// ---- Mouse -> botones del mando (plat::pad_hook) ----------------------------------------------------------------
#if defined(_WIN32)
constexpr uint64_t kMs = 1000000;

void mouse_pad(plat::PadState& p) {
    if (!mouse_on()) return;
    const tsuki::Target* side[3] = {nullptr, &tsuki::mouse_target(tsuki::kMouseRight),
                                    &tsuki::mouse_target(tsuki::kMouseMiddle)};
    const tsuki::Target& wheel_up = tsuki::mouse_target(tsuki::kWheelUp);
    const tsuki::Target& wheel_down = tsuki::mouse_target(tsuki::kWheelDown);
    static std::mutex m;
    std::lock_guard lk(m);
    plat::PointerState ptr;
    plat::read_pointer(ptr);
    const uint64_t now = plat::monotonic_ns();
    static bool init = false;
    static uint32_t seen[3];
    static uint64_t until[3];
    static int32_t wheel_used;
    static int pending = 0;  // muescas de rueda por convertir: > 0 arriba, < 0 abajo
    static uint64_t pulse_end = 0;
    static int pulse_dir = 0;
    if (!init) {
        init = true;
        for (int k = 0; k < 3; k++) seen[k] = ptr.downs[k], until[k] = 0;
        wheel_used = ptr.wheel;
    }
    // Derecho y medio: apretados mientras se mantienen, y al menos 80 ms desde el clic (un clic mas corto que un
    // frame igual se ve).
    for (int k = 1; k < 3; k++) {
        if (ptr.downs[k] != seen[k]) seen[k] = ptr.downs[k], until[k] = now + 80 * kMs;
        if ((ptr.buttons >> k & 1) || now < until[k]) tsuki::press(p, *side[k]);
    }
    // Rueda: cada muesca (120) es una pulsacion de 60 ms seguida de 40 ms suelto; como mucho 5 en cola.
    while (ptr.wheel - wheel_used >= 120) wheel_used += 120, pending = std::min(pending + 1, 5);
    while (ptr.wheel - wheel_used <= -120) wheel_used -= 120, pending = std::max(pending - 1, -5);
    if (now >= pulse_end + 40 * kMs && pending != 0) {
        pulse_dir = pending > 0 ? 1 : -1;
        pending -= pulse_dir;
        pulse_end = now + 60 * kMs;
    }
    if (now < pulse_end) tsuki::press(p, pulse_dir > 0 ? wheel_up : wheel_down);
}
#endif

// Traza: cada boton del mando que cambia (teclado, mando y mouse juntos), para ver que hace el juego con cada uno.
void trace_pad(const plat::PadState& p) {
    static uint64_t last = 0;
    static std::mutex m;
    std::lock_guard lk(m);
    uint64_t now = p.buttons & 0xFFFF;  // botones y cruceta (sin las direcciones de stick que agrega hle_hid)
    if (now == last) return;
    log("hid: botones%s (stick %d,%d)", tsuki::describe_buttons(now ^ last, now).c_str(), p.lx, p.ly);
    last = now;
}

void tsuki_pad_hook(plat::PadState& p) {
#if defined(_WIN32)
    mouse_pad(p);
#endif
    if (trace_on()) trace_pad(p);
}

const bool g_pad_hook_set = [] {
    plat::pad_hook = tsuki_pad_hook;
    return true;
}();

// ---- Pantalla tactil ----------------------------------------------------------------------------------------
// TouchScreenState<16> (0x290): {s64 samplingNumber; s32 count; u32 reserved; TouchState touches[16]}.
// TouchState (0x28): {s64 deltaTime; u32 attributes (bit 0 Start, bit 1 End); s32 fingerId; s32 x, y;
// s32 diameterX, diameterY; s32 rotationAngle; u32 reserved}. Coordenadas de la pantalla: 1280x720, arriba-izquierda.
constexpr uint64_t kStateSize = 0x290;
constexpr int32_t kTouchW = 1280, kTouchH = 720;
constexpr uint32_t kAttrStart = 1, kAttrEnd = 2;

struct TouchSample {
    int64_t sampling = 0, delta = 0;
    int32_t count = 0;
    uint32_t attr = 0;
    int32_t x = 0, y = 0;
};

#if defined(_WIN32)
// Ventana -> pantalla del juego: el rectangulo donde el framework presenta la imagen (plat::picture_rect,
// apply_aspect.py): con NX_FIXED_ASPECT la imagen con franjas, sin ella el area cliente entera (estirada).
struct ScreenPoint {
    bool inside;
    int32_t x, y;
};
ScreenPoint to_screen(const plat::PointerState& p) {
    if (!p.w || !p.h) return {false, 0, 0};
    const plat::PictureRect r = plat::picture_rect(p.w, p.h);
    if (!r.w || !r.h) return {false, 0, 0};
    double u = double(p.x - r.x) / r.w, v = double(p.y - r.y) / r.h;
    return {u >= 0 && u < 1 && v >= 0 && v < 1, std::clamp(int32_t(u * kTouchW), 0, kTouchW - 1),
            std::clamp(int32_t(v * kTouchH), 0, kTouchH - 1)};
}
#endif

std::mutex g_touch_m;
std::deque<TouchSample> g_touch_history;  // la mas nueva primero, como las devuelve hid
bool g_touching = false;
uint32_t g_touch_seen_downs = 0;
bool g_touch_init = false;
uint64_t g_touch_last_ns = 0;
int32_t g_touch_x = 0, g_touch_y = 0;

// Un toque por clic izquierdo: Start en la primera muestra, sin atributos mientras se mantiene (siguiendo al mouse),
// End en la muestra en que se suelta (con el toque todavia presente) y despues ninguno. Un clic que empieza y
// termina entre dos lecturas igual da una muestra con Start y la siguiente con End.
TouchSample next_touch_sample() {
    TouchSample s;
    s.sampling = ++g_touch_sampling;
    const uint64_t now = plat::monotonic_ns();
    s.delta = g_touch_last_ns ? int64_t(now - g_touch_last_ns) : 0;
    g_touch_last_ns = now;
#if defined(_WIN32)
    if (!mouse_on()) return s;
    plat::PointerState p;
    plat::read_pointer(p);
    if (!g_touch_init) g_touch_init = true, g_touch_seen_downs = p.downs[0];
    const ScreenPoint pt = to_screen(p);
    const bool held = p.buttons & 1, new_down = p.downs[0] != g_touch_seen_downs;
    if (!g_touching) {
        if (new_down) {
            g_touch_seen_downs = p.downs[0];
            if (pt.inside) {
                g_touching = true;
                g_touch_x = pt.x, g_touch_y = pt.y;
                s.count = 1, s.attr = kAttrStart;
                if (trace_on()) log("hid: toque en %d,%d (ventana %d,%d de %ux%u)", pt.x, pt.y, p.x, p.y, p.w, p.h);
            } else if (trace_on()) {
                log("hid: clic fuera de la imagen (ventana %d,%d de %ux%u), ignorado", p.x, p.y, p.w, p.h);
            }
        }
    } else if (held && !new_down) {
        g_touch_x = pt.x, g_touch_y = pt.y;  // arrastre: sigue al mouse, limitado a la pantalla
        s.count = 1;
    } else {
        // Se solto (o se solto y se volvio a apretar entre dos lecturas: este termina, la proxima empieza otro).
        g_touching = false;
        s.count = 1, s.attr = kAttrEnd;
        if (trace_on()) log("hid: fin del toque en %d,%d", g_touch_x, g_touch_y);
    }
    s.x = g_touch_x, s.y = g_touch_y;
#endif
    return s;
}

void write_touch_state(uint64_t at, const TouchSample& s) {
    auto* p = reinterpret_cast<uint8_t*>(at);
    std::memset(p, 0, kStateSize);
    std::memcpy(p, &s.sampling, 8);
    std::memcpy(p + 8, &s.count, 4);
    if (!s.count) return;
    uint8_t* t = p + 0x10;
    const int32_t finger = 0, diameter = 15, angle = 0;
    std::memcpy(t, &s.delta, 8);
    std::memcpy(t + 0x08, &s.attr, 4);
    std::memcpy(t + 0x0C, &finger, 4);
    std::memcpy(t + 0x10, &s.x, 4);
    std::memcpy(t + 0x14, &s.y, 4);
    std::memcpy(t + 0x18, &diameter, 4);
    std::memcpy(t + 0x1C, &diameter, 4);
    std::memcpy(t + 0x20, &angle, 4);
}

// GetTouchScreenStates<16>(TouchScreenState<16>* out, int count) -> int escritos, la mas nueva primero. Como el hid
// real, un historial: cada llamada agrega una muestra y devuelve hasta `count` de las ultimas 16, cada una con su
// samplingNumber, asi un juego que lee varias y se queda con las nuevas no pierde un Start ni un End.
void GetTouchScreenStates16(CPU& c) {
    uint64_t out = c.x[0];
    int n = int32_t(c.x[1]);
    if (trace_on()) {
        static std::atomic<uint64_t> calls{0};
        uint64_t k = ++calls;
        if (k <= 5 || k % 3000 == 0)
            log("hid: GetTouchScreenStates #%llu count %d (lr %s)", (unsigned long long)k, n, describe(c.x[30]).c_str());
    }
    if (!out || n <= 0) {
        c.x[0] = 0;
        return;
    }
    std::lock_guard lk(g_touch_m);
    g_touch_history.push_front(next_touch_sample());
    if (g_touch_history.size() > 16) g_touch_history.pop_back();
    int written = std::min<int>(n, int(g_touch_history.size()));
    for (int k = 0; k < written; k++) write_touch_state(out + kStateSize * uint64_t(k), g_touch_history[k]);
    c.x[0] = uint64_t(written);
}

// ---- Sensores de movimiento -----------------------------------------------------------------------------------
// SixAxisSensorState (0x60): deltaTime, samplingNumber, acceleration[3], angularVelocity[3], angle[3],
// direction[3][3] (en +0x34), attributes. En reposo: todo 0 salvo direction = identidad (cuaterniones validos).
void GetSixAxisSensorState(CPU& c) {  // (SixAxisSensorState*, const SixAxisSensorHandle&)
    auto* p = reinterpret_cast<uint8_t*>(c.x[0]);
    if (!p) return;
    std::memset(p, 0, 0x60);
    const float ident[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    std::memcpy(p + 0x34, ident, sizeof ident);
}

// ---- Vibracion (se descarta, igual que en hle_hid.cpp) ------------------------------------------------------
// VibrationValue: {f32 amplitudeLow, frequencyLow, amplitudeHigh, frequencyHigh}; en reposo 0 / 160 Hz / 0 / 320 Hz.
void GetActualVibrationValue(CPU& c) {  // (VibrationValue*, const VibrationDeviceHandle&)
    if (c.x[0]) {
        const float v[4] = {0.0f, 160.0f, 0.0f, 320.0f};
        std::memcpy(reinterpret_cast<void*>(c.x[0]), v, sizeof v);
    }
    c.x[0] = 0;
}

}  // namespace

NX_HLE("_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyLeftStateERKj", GetNpadStateJoy);
NX_HLE("_ZN2nn3hid12GetNpadStateEPNS0_17NpadJoyRightStateERKj", GetNpadStateJoy);
NX_HLE("_ZN2nn3hid18GetNpadJoyHoldTypeEv", GetNpadJoyHoldType);
NX_HLE("_ZN2nn3hid22GetNpadControllerColorEPNS0_19NpadControllerColorERKj", GetNpadControllerColor);
NX_HLE("_ZN2nn3hid22GetNpadControllerColorEPNS0_19NpadControllerColorES2_RKj", GetNpadControllerColorLR);

NX_HLE("_ZN2nn3hid21InitializeTouchScreenEv", ok);
NX_HLE("_ZN2nn3hid20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi", GetTouchScreenStates16);

NX_HLE("_ZN2nn3hid21GetSixAxisSensorStateEPNS0_18SixAxisSensorStateERKNS0_19SixAxisSensorHandleE", GetSixAxisSensorState);
NX_HLE("_ZN2nn3hid17StopSixAxisSensorERKNS0_19SixAxisSensorHandleE", ok);
NX_HLE("_ZN2nn3hid25EnableSixAxisSensorFusionERKNS0_19SixAxisSensorHandleEb", ok);
NX_HLE("_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE", ok);
NX_HLE("_ZN2nn3hid34ResetSixAxisSensorFusionParametersERKNS0_19SixAxisSensorHandleE", ok);
NX_HLE("_ZN2nn3hid41IsFirmwareUpdateAvailableForSixAxisSensorERKNS0_19SixAxisSensorHandleE", no);

// Vibracion: lo que llegaria al hardware se descarta; las clases (Player, Mixer, Writer...) corren del sdk.
NX_HLE("_ZN2nn3hid13VibrationNode6UpdateEv", ok);
NX_HLE("_ZN2nn3hid15VibrationTarget24SetVibrationDeviceHandleERKNS0_21VibrationDeviceHandleE", ok);
NX_HLE("_ZN2nn3hid23GetActualVibrationValueEPNS0_14VibrationValueERKNS0_21VibrationDeviceHandleE", GetActualVibrationValue);

}  // namespace nx