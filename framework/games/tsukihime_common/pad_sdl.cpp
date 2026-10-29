// pad_sdl.cpp - mandos por SDL3: Xbox, DualShock 4, DualSense, Switch Pro Controller y Joy-Con, por USB y Bluetooth.
// AP_SDL_PAD (apply_sdl_pad.py engancha esto en input_tsuki.cpp y enlaza SDL3 en tsukihime_common.cmake).
//
// Solo el subsistema de mandos de SDL (SDL_INIT_GAMEPAD): sin ventana, video ni audio de SDL.
// - Un hilo propio hace SDL_Init y bombea los eventos: en Windows SDL detecta las conexiones con una ventana oculta
//   suya, que solo atiende el hilo que inicializo, y la enumeracion inicial puede tardar. pad_source (hilo del juego)
//   solo copia la ultima instantanea, con un mutex.
// - SDL reemplaza a XInput: SDL tambien lee los mandos de Xbox y no los duplica entre sus backends. NX_SDL_PAD=0, o si
//   SDL3.dll falta o no arranca: XInput como antes (read_xinput de input_tsuki.cpp). El exe enlaza SDL3.dll con
//   /DELAYLOAD (NX_SDL_DELAYLOAD), asi que arranca aunque la DLL no este.
// - Botones por posicion fisica, con los bits de XInput: abajo = A de XInput (0x1000), derecha = B (0x2000),
//   izquierda = X (0x4000), arriba = Y (0x8000). El mapeo de input.ini es el mismo para todos los mandos: en uno de
//   Switch el A (derecha) es el A del Npad; en PlayStation, circulo, como B en Xbox.
// - Varios mandos: manda el ultimo usado (un boton, un gatillo o un stick pasado la mitad). Al arrancar, el primero
//   conectado; si se desconecta el activo, el que lleva mas tiempo conectado. Un mando virtual de DS4Windows o Steam
//   junto al real no molesta: los dos mandan lo mismo.
// - Luz de DualShock 4 / DualSense: 50, 84, 141 al abrir el mando (los demas la ignoran).
// - No cambia la resolucion del temporizador de Windows (SDL_HINT_TIMER_RESOLUTION = 0).
// - Log: cada conexion y desconexion (nombre, tipo de SDL, USB/inalambrico, VID:PID) y cada cambio de mando activo;
//   tambien los joysticks que SDL no reconoce como mando (se ignoran). NX_INPUT_TRACE=1: ademas GUID y ruta de cada
//   mando y lo que tardo SDL_Init.
#include "pad_sdl.h"
#include "hud_glyphs.h"  // AP_HUD_GLYPHS

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#if defined(_MSC_VER) && defined(NX_SDL_DELAYLOAD)
#include <delayimp.h>
#pragma comment(lib, "delayimp.lib")
#endif
#endif

#include <SDL3/SDL.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <vector>

#include <nx/runtime.h>

namespace nx::tsuki {
namespace {

constexpr uint8_t kLedR = 50, kLedG = 84, kLedB = 141;
constexpr int kStickActivo = 16384;    // un stick pasado la mitad cambia de mando activo (zona muerta del juego: 7849)
constexpr int kGatilloActivo = 12850;  // ~100 de 255: el mismo umbral de los gatillos en input_tsuki.cpp
constexpr int kEsperaMs = 4;           // el hilo publica al menos cada 4 ms

bool trace_on() {
    static const bool on = [] {
        const char* e = std::getenv("NX_INPUT_TRACE");
        return e && *e && *e != '0';
    }();
    return on;
}

struct Estado {  // se crea con new y no se destruye: el hilo sigue vivo hasta que termina el proceso
    std::atomic<int> mode{int(SdlPadMode::kPending)};
    std::mutex m;
    bool hay = false;
    SdlPad pad;
};
Estado& st() {
    static Estado* s = new Estado;
    return *s;
}

struct Abierto {
    SDL_JoystickID id;
    SDL_Gamepad* gp;
};

const char* nombre(SDL_Gamepad* gp) {
    const char* n = SDL_GetGamepadName(gp);
    return n && *n ? n : "(sin nombre)";
}
const char* tipo(SDL_Gamepad* gp) {
    const char* t = SDL_GetGamepadStringForType(SDL_GetGamepadType(gp));
    return t && *t ? t : "desconocido";
}
const char* conexion(SDL_Gamepad* gp) {
    switch (SDL_GetGamepadConnectionState(gp)) {
    case SDL_JOYSTICK_CONNECTION_WIRED: return "USB";
    case SDL_JOYSTICK_CONNECTION_WIRELESS: return "inalambrico";
    default: return "conexion ?";
    }
}

int16_t invertir(int16_t v) { return v == INT16_MIN ? int16_t(INT16_MAX) : int16_t(-v); }
uint8_t gatillo(int16_t v) { return v <= 0 ? 0 : uint8_t(int(v) * 255 / 32767); }

SdlPad leer(SDL_Gamepad* gp) {
    static const struct {
        SDL_GamepadButton b;
        uint32_t bit;
    } kBotones[] = {
        {SDL_GAMEPAD_BUTTON_SOUTH, 0x1000},         {SDL_GAMEPAD_BUTTON_EAST, 0x2000},
        {SDL_GAMEPAD_BUTTON_WEST, 0x4000},          {SDL_GAMEPAD_BUTTON_NORTH, 0x8000},
        {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, 0x0100}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, 0x0200},
        {SDL_GAMEPAD_BUTTON_START, 0x0010},         {SDL_GAMEPAD_BUTTON_BACK, 0x0020},
        {SDL_GAMEPAD_BUTTON_LEFT_STICK, 0x0040},    {SDL_GAMEPAD_BUTTON_RIGHT_STICK, 0x0080},
        {SDL_GAMEPAD_BUTTON_DPAD_UP, 0x0001},       {SDL_GAMEPAD_BUTTON_DPAD_DOWN, 0x0002},
        {SDL_GAMEPAD_BUTTON_DPAD_LEFT, 0x0004},     {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, 0x0008},
        {SDL_GAMEPAD_BUTTON_GUIDE, kSrcGuide},      {SDL_GAMEPAD_BUTTON_TOUCHPAD, kSrcTouchpad},
        {SDL_GAMEPAD_BUTTON_MISC1, kSrcMisc1},
    };
    SdlPad p;
    for (const auto& k : kBotones)
        if (SDL_GetGamepadButton(gp, k.b)) p.buttons |= k.bit;
    p.lt = gatillo(SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFT_TRIGGER));
    p.rt = gatillo(SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER));
    p.lx = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTX);
    p.ly = invertir(SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTY));  // SDL: positivo = abajo
    p.rx = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_RIGHTX);
    p.ry = invertir(SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_RIGHTY));
    return p;
}

class Mandos {
public:
    void evento(const SDL_Event& e) {
        switch (e.type) {
        case SDL_EVENT_JOYSTICK_ADDED:
            if (!SDL_IsGamepad(e.jdevice.which)) {
                const char* n = SDL_GetJoystickNameForID(e.jdevice.which);
                log("input: joystick sin mapeo de mando, se ignora: %s (%04x:%04x, id %u)", n ? n : "(sin nombre)",
                    SDL_GetJoystickVendorForID(e.jdevice.which), SDL_GetJoystickProductForID(e.jdevice.which),
                    unsigned(e.jdevice.which));
            }
            break;
        case SDL_EVENT_GAMEPAD_ADDED: agregar(e.gdevice.which); break;
        case SDL_EVENT_GAMEPAD_REMOVED: quitar(e.gdevice.which); break;
        case SDL_EVENT_GAMEPAD_BUTTON_DOWN: usar(e.gbutton.which), nota(e.gbutton.which); break;  // AP_HUD_GLYPHS
        case SDL_EVENT_GAMEPAD_AXIS_MOTION: {
            const int v = e.gaxis.value;
            const bool gat = e.gaxis.axis == SDL_GAMEPAD_AXIS_LEFT_TRIGGER || e.gaxis.axis == SDL_GAMEPAD_AXIS_RIGHT_TRIGGER;
            if (gat ? v > kGatilloActivo : (v > kStickActivo || v < -kStickActivo))
                usar(e.gaxis.which), nota(e.gaxis.which);  // AP_HUD_GLYPHS
            break;
        }
        default: break;
        }
    }

    void publicar() {
        SdlPad p;
        SDL_Gamepad* gp = buscar(activo_);
        if (gp) p = leer(gp);
        Estado& s = st();
        std::lock_guard lk(s.m);
        s.hay = gp != nullptr;
        s.pad = p;
    }

private:
    std::vector<Abierto> abiertos_;  // en orden de conexion
    SDL_JoystickID activo_ = 0;

    SDL_Gamepad* buscar(SDL_JoystickID id) {
        for (const Abierto& a : abiertos_)
            if (a.id == id) return a.gp;
        return nullptr;
    }

    void agregar(SDL_JoystickID id) {
        if (buscar(id)) return;
        SDL_Gamepad* gp = SDL_OpenGamepad(id);
        if (!gp) {
            log("input: no se pudo abrir el mando id %u: %s", unsigned(id), SDL_GetError());
            return;
        }
        abiertos_.push_back({id, gp});
        log("input: mando conectado: %s (%s, %s, %04x:%04x, id %u)", nombre(gp), tipo(gp), conexion(gp),
            SDL_GetGamepadVendor(gp), SDL_GetGamepadProduct(gp), unsigned(id));
        if (trace_on()) {
            char guid[64] = {};
            SDL_GUIDToString(SDL_GetGamepadGUIDForID(id), guid, int(sizeof guid));
            const char* ruta = SDL_GetGamepadPath(gp);
            log("input:   GUID %s, ruta %s", guid, ruta ? ruta : "-");
        }
        SDL_SetGamepadLED(gp, kLedR, kLedG, kLedB);  // solo DualShock 4 / DualSense; los demas devuelven false
        if (!activo_) cambiar(id, "primero conectado");
    }

    void quitar(SDL_JoystickID id) {
        for (size_t k = 0; k < abiertos_.size(); k++) {
            if (abiertos_[k].id != id) continue;
            log("input: mando desconectado: %s (id %u)", nombre(abiertos_[k].gp), unsigned(id));
            SDL_CloseGamepad(abiertos_[k].gp);
            abiertos_.erase(abiertos_.begin() + std::ptrdiff_t(k));
            if (activo_ == id) {
                activo_ = 0;
                if (abiertos_.empty())
                    log("input: sin mandos conectados");
                else
                    cambiar(abiertos_.front().id, "el activo se desconecto");
            }
            return;
        }
    }

    // AP_HUD_GLYPHS: entrada real de este mando, para los iconos del HUD segun su tipo (por posicion; un mando
    // generico o desconocido muestra los de Xbox).
    void nota(SDL_JoystickID id) {
#ifdef NX_HUD_GLYPHS
        SDL_Gamepad* gp = buscar(id);
        if (!gp) return;
        switch (SDL_GetGamepadType(gp)) {
        case SDL_GAMEPAD_TYPE_PS3:
        case SDL_GAMEPAD_TYPE_PS4:
        case SDL_GAMEPAD_TYPE_PS5: hud::note_input(hud::Device::kPs); break;
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_PRO:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_LEFT:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_RIGHT:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_PAIR: hud::note_input(hud::Device::kSwitch); break;
        default: hud::note_input(hud::Device::kXbox); break;
        }
#else
        (void)id;
#endif
    }

    void usar(SDL_JoystickID id) {
        if (id != activo_ && buscar(id)) cambiar(id, "ultimo usado");
    }

    void cambiar(SDL_JoystickID id, const char* por) {
        activo_ = id;
        log("input: mando activo: %s (id %u, %s)", nombre(buscar(id)), unsigned(id), por);
    }
};

#if defined(_WIN32) && defined(_MSC_VER) && defined(NX_SDL_DELAYLOAD)
// Con /DELAYLOAD:SDL3.dll el exe arranca sin la DLL. Aca se carga y se resuelven todas sus funciones de una vez: si
// falta la DLL, o es una version vieja sin alguna funcion, se usa XInput en vez de fallar despues a mitad de camino.
long resolver_sdl3() {
    __try {
        return __HrLoadAllImportsForDll("SDL3.dll");
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return long(0x80004005L);  // E_FAIL
    }
}
bool cargar_sdl3() {
    if (!LoadLibraryW(L"SDL3.dll")) {
        log("input: no se encontro SDL3.dll junto al exe (error %lu): mandos por XInput", GetLastError());
        return false;
    }
    const long hr = resolver_sdl3();
    if (hr < 0) {
        log("input: SDL3.dll no tiene todas las funciones que se usan (HRESULT %#lx; version vieja?): mandos por XInput",
            static_cast<unsigned long>(hr));
        return false;
    }
    return true;
}
#endif

void hilo() {
    Estado& s = st();
#if defined(_WIN32) && defined(_MSC_VER) && defined(NX_SDL_DELAYLOAD)
    if (!cargar_sdl3()) {
        s.mode = int(SdlPadMode::kXInput);
        return;
    }
#endif
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");  // como XInput: se lee aunque la ventana no tenga foco
    SDL_SetHint(SDL_HINT_TIMER_RESOLUTION, "0");                  // no tocar timeBeginPeriod del proceso
    const auto t0 = std::chrono::steady_clock::now();
    if (!SDL_Init(SDL_INIT_GAMEPAD)) {
        log("input: SDL_Init(GAMEPAD) fallo: %s; mandos por XInput", SDL_GetError());
        s.mode = int(SdlPadMode::kXInput);
        return;
    }
    const int v = SDL_GetVersion();
    log("input: mandos por SDL %d.%d.%d (NX_SDL_PAD=0 vuelve a XInput)", SDL_VERSIONNUM_MAJOR(v), SDL_VERSIONNUM_MINOR(v),
        SDL_VERSIONNUM_MICRO(v));
    if (trace_on())
        log("input: SDL_Init %.1f ms",
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
    s.mode = int(SdlPadMode::kSdl);

    Mandos mandos;  // los mandos ya conectados llegan como SDL_EVENT_GAMEPAD_ADDED en el primer bombeo
    for (;;) {
        SDL_Event e;
        for (bool hay = SDL_WaitEventTimeout(&e, kEsperaMs); hay; hay = SDL_PollEvent(&e)) mandos.evento(e);
        mandos.publicar();
    }
}

}  // namespace

SdlPadMode sdl_pad_mode() {
    static std::once_flag once;
    std::call_once(once, [] {
        if (const char* e = std::getenv("NX_SDL_PAD"); e && *e == '0') {
            log("input: NX_SDL_PAD=0: mandos por XInput");
            st().mode = int(SdlPadMode::kXInput);
            return;
        }
        std::thread(hilo).detach();
    });
    return SdlPadMode(st().mode.load());
}

bool sdl_pad_read(SdlPad& out) {
    Estado& s = st();
    if (s.mode.load() != int(SdlPadMode::kSdl)) return false;
    std::lock_guard lk(s.m);
    if (!s.hay) return false;
    out = s.pad;
    return true;
}

}  // namespace nx::tsuki