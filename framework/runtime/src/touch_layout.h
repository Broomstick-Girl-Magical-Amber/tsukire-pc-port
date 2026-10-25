// Drawn touch controls: the per-game button layout (touch_layout.cpp).
#pragma once

#include <cstddef>
#include <cstdint>

#include "platform.h"

namespace nx::plat {

struct TouchButton {
    float x, y, r;  // centre across the landscape screen (0..1), radius as a fraction of its height
    uint64_t button;
};
// The game's layout (count 0: the game handles touch its own way).
const TouchButton* touch_buttons(size_t& count);
// Launcher settings (config.txt NX_TOUCH_*): drawn and accepted at all, stick on the right half, brightness scale.
bool touch_enabled();
bool touch_mirrored();
float touch_opacity_scale();
// The button under (x, y) (0..1 across the screen; aspect = width / height), or 0.
uint64_t touch_hit(float x, float y, float aspect);

}  // namespace nx::plat
