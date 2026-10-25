// pad_sdl.h - mandos por SDL3 (AP_SDL_PAD; ver pad_sdl.cpp). Lo usa input_tsuki.cpp (leer_mando, apply_sdl_pad.py).
#pragma once

#include <cstdint>

namespace nx::tsuki {

// Bits del estado: los de XInput (XINPUT_GAMEPAD_*, por posicion: 0x1000 = abajo) mas estos, que XInput no tiene.
// Los bits 16 y 17 son los de los gatillos en input_tsuki.cpp (kSrcLT, kSrcRT).
constexpr uint32_t kSrcGuide = 1u << 18;     // Xbox / PS / Home
constexpr uint32_t kSrcTouchpad = 1u << 19;  // clic del panel tactil (DualShock 4, DualSense)
constexpr uint32_t kSrcMisc1 = 1u << 20;     // Captura (Switch), microfono (DualSense), Share (Xbox Series)

struct SdlPad {
    uint32_t buttons = 0;              // bits de arriba
    uint8_t lt = 0, rt = 0;            // gatillos 0..255, como XInput
    int16_t lx = 0, ly = 0, rx = 0, ry = 0;  // sticks como XInput (y positivo = arriba)
};

enum class SdlPadMode { kPending, kSdl, kXInput };

// La primera llamada arranca el hilo de SDL. kPending mientras inicializa; kXInput con NX_SDL_PAD=0 o si SDL no
// arranco (falta SDL3.dll, SDL_Init fallo): entonces se lee XInput como antes.
SdlPadMode sdl_pad_mode();

// Estado del mando activo (el ultimo usado). false si no hay ninguno conectado o si el modo no es kSdl.
bool sdl_pad_read(SdlPad& out);

}  // namespace nx::tsuki