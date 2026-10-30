// hud_glyphs.h - iconos de botones del HUD segun el ultimo dispositivo usado (AP_HUD_GLYPHS, hud_glyphs.cpp).
#pragma once

namespace nx::tsuki::hud {

enum class Device { kSwitch, kXbox, kPs, kKeyboard };

// Una entrada real de ese dispositivo: un boton o tecla que se aprieta, un clic o la rueda del mouse, un stick o un
// gatillo pasado la mitad (el ruido del stick no cuenta). Si cambia el dispositivo, los atlas de iconos se suben de
// nuevo con los del nuevo. Barato si no cambia nada: se puede llamar en cada evento, desde cualquier hilo.
void note_input(Device d);

}  // namespace nx::tsuki::hud
