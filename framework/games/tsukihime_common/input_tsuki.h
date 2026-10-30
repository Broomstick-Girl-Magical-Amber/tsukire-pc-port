// input_tsuki.h - mapeo de teclado, mando Xbox y botones del mouse a botones del Npad (input_tsuki.cpp).
#pragma once

#include <cstdint>
#include <string>

#include "platform.h"

namespace nx::tsuki {

// Lo que un boton fisico aprieta en el Npad: bits de NpadButton y/o un stick llevado al extremo.
struct Target {
    uint64_t bits = 0;
    int32_t lx = 0, ly = 0, rx = 0, ry = 0;
    bool any() const { return bits || lx || ly || rx || ry; }
    void add(const Target& t);
};
void press(plat::PadState& p, const Target& t);

// Nombre del Npad (A B X Y L R ZL ZR PLUS MINUS UP DOWN LEFT RIGHT STICKL STICKR, SUP SDOWN SLEFT SRIGHT del stick
// izquierdo, RSUP RSDOWN RSLEFT RSRIGHT del derecho; sin distinguir mayusculas) -> Target. false si no existe.
bool parse_target(const std::string& name, Target& out);
// " +A -B" para los bits de `changed`, con + si estan en `now`: para la traza de hle_hid_tsuki.cpp.
std::string describe_buttons(uint64_t changed, uint64_t now);

// Botones del mouse que van al Npad (el clic izquierdo es el tactil, hle_hid_tsuki.cpp).
enum MouseSource { kMouseRight, kMouseMiddle, kWheelUp, kWheelDown, kMouseSources };
// Lo que aprieta cada uno: NX_MOUSE_RIGHT / MIDDLE / WHEEL_UP / WHEEL_DOWN si estan, si no input.ini [mouse], si no
// el de siempre (derecho = B, rueda = stick derecho arriba/abajo, medio nada).
const Target& mouse_target(MouseSource s);

// AP_HUD_GLYPHS: la primera tecla (codigo VK de Windows) que input.ini, o el mapeo por defecto, le da a ese boton
// del Npad (el mismo nombre que en input.ini: A, UP, SUP...); 0 si ninguna. Para los iconos del HUD (hud_glyphs.cpp).
int first_key(const std::string& npad_name);

}  // namespace nx::tsuki
