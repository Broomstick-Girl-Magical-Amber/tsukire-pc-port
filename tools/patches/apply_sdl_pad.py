"""apply_sdl_pad.py - PlayStation, Switch and Xbox controllers through SDL3 in Tsukihime (both editions). Marker AP_SDL_PAD.

Requires games\\tsukihime_common\\pad_sdl.cpp and pad_sdl.h (new files, copied by hand) and vcpkg:
    vcpkg install sdl3 --triplet x64-windows

Edits (framework and tools; Wonder and runtime\\ unchanged):
- games/tsukihime_common/input_tsuki.cpp: leer_mando() (SDL if active; otherwise the usual read_xinput), include of
  pad_sdl.h, button aliases for [mando] of input.ini (Sur/Este/Oeste/Norte, Cruz/Circulo/Cuadrado/Triangulo,
  L1/R1/L2/R2/L3/R3, Options/Share, L/R/ZL/ZR/Plus/Minus) and new buttons (Guide, Touchpad, Captura).
- games/tsukihime_common/tsukihime_common.cmake: find_package(SDL3), /DELAYLOAD:SDL3.dll (NX_SDL_DELAYLOAD) and copy of
  SDL3.dll next to the exe.
- ../tools/recomp_tsukihime.ps1: 3 rows in $Markers.

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_sdl_pad.py [--remove]
After: cmake --build build\\tsukihime and build\\tsukihime_global (the .cmake changes: ninja reconfigures by itself).
Turned off without rebuilding with NX_SDL_PAD=0 (back to XInput).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_SDL_PAD"
INPUT = "games/tsukihime_common/input_tsuki.cpp"
CMAKE = "games/tsukihime_common/tsukihime_common.cmake"
RECOMP = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # --- input_tsuki.cpp: header comment
    (INPUT,
     "// Mando: el primer indice XInput conectado (0..3).",
     "// Mando (AP_SDL_PAD, apply_sdl_pad.py): por SDL3 (pad_sdl.cpp): Xbox, DualShock 4, DualSense, Pro Controller y\n"
     "// Joy-Con por USB y Bluetooth; manda el ultimo usado; botones por posicion fisica (los nombres de [mando] son las\n"
     "// posiciones del Xbox: A = abajo, B = derecha; alias Sur/Este/Oeste/Norte, Cruz/Circulo/..., L1/R1/..., L/R/ZL/ZR).\n"
     "// NX_SDL_PAD=0, o sin SDL3.dll, vuelve a XInput:\n"
     "// Mando XInput: el primer indice XInput conectado (0..3)."),
    # --- include
    (INPUT,
     '#include "input_tsuki.h"\n#include "platform.h"\n',
     '#include "input_tsuki.h"\n#include "pad_sdl.h"  // AP_SDL_PAD\n#include "platform.h"\n'),
    # --- aliases and new buttons of [mando]
    (INPUT,
     '        {"DPADDOWN", 0x0002}, {"DPADLEFT", 0x0004}, {"DPADRIGHT", 0x0008},\n    };\n',
     '        {"DPADDOWN", 0x0002}, {"DPADLEFT", 0x0004}, {"DPADRIGHT", 0x0008},\n'
     '        // AP_SDL_PAD: botones que solo da SDL (pad_sdl.h) y alias. Son posiciones: Sur = Cruz = A del Xbox, etc.\n'
     '        // Las letras A B X Y siguen siendo las del Xbox (en un mando de Switch, su A es "B" o "Este").\n'
     '        {"GUIDE", kSrcGuide}, {"HOME", kSrcGuide}, {"PS", kSrcGuide}, {"TOUCHPAD", kSrcTouchpad},\n'
     '        {"PANEL", kSrcTouchpad}, {"CAPTURA", kSrcMisc1}, {"CAPTURE", kSrcMisc1}, {"MIC", kSrcMisc1},\n'
     '        {"SUR", 0x1000}, {"SOUTH", 0x1000}, {"ESTE", 0x2000}, {"EAST", 0x2000}, {"OESTE", 0x4000},\n'
     '        {"WEST", 0x4000}, {"NORTE", 0x8000}, {"NORTH", 0x8000},\n'
     '        {"CRUZ", 0x1000}, {"CROSS", 0x1000}, {"CIRCULO", 0x2000}, {"CIRCLE", 0x2000}, {"CUADRADO", 0x4000},\n'
     '        {"SQUARE", 0x4000}, {"TRIANGULO", 0x8000}, {"TRIANGLE", 0x8000},\n'
     '        {"L1", 0x0100}, {"R1", 0x0200}, {"L2", kSrcLT}, {"R2", kSrcRT}, {"L3", 0x0040}, {"R3", 0x0080},\n'
     '        {"OPTIONS", 0x0010}, {"SHARE", 0x0020}, {"CREATE", 0x0020},\n'
     '        {"L", 0x0100}, {"R", 0x0200}, {"ZL", kSrcLT}, {"ZR", kSrcRT}, {"PLUS", 0x0010}, {"+", 0x0010},\n'
     '        {"MINUS", 0x0020}, {"-", 0x0020},\n'
     '    };\n'),
    # --- error message with the new names
    (INPUT,
     '"DPadUp DPadDown DPadLeft DPadRight)");',
     '"DPadUp DPadDown DPadLeft DPadRight Guide Touchpad Captura; alias: Sur Este Oeste Norte, "\n'
     '                                "Cruz Circulo Cuadrado Triangulo L1 R1 L2 R2 L3 R3 Options Share, L R ZL ZR Plus Minus)");'
     '  // AP_SDL_PAD'),
    # --- reading: SDL or XInput
    (INPUT,
     "void tsuki_pad_source(plat::PadState& p) {\n"
     "    const Config& c = config();\n"
     "    XINPUT_GAMEPAD g{};\n"
     "    if (read_xinput(g)) {\n"
     "        p.lx = dead_zone(g.sThumbLX), p.ly = dead_zone(g.sThumbLY);\n"
     "        p.rx = dead_zone(g.sThumbRX), p.ry = dead_zone(g.sThumbRY);\n"
     "        const uint32_t held = g.wButtons | (g.bLeftTrigger > 100 ? kSrcLT : 0) | (g.bRightTrigger > 100 ? kSrcRT : 0);\n"
     "        for (const PadBind& b : c.pad)\n"
     "            if (held & b.mask) press(p, b.t);\n"
     "    }\n",
     "// AP_SDL_PAD: el mando por SDL (pad_sdl.cpp) o, con NX_SDL_PAD=0 o sin SDL3.dll, por XInput como antes. Mientras SDL\n"
     "// inicializa (una vez, al arrancar) no hay mando.\n"
     "struct MandoLeido {\n"
     "    uint32_t held = 0;  // bits de XInput + kSrcLT/kSrcRT + kSrcGuide/kSrcTouchpad/kSrcMisc1\n"
     "    int lx = 0, ly = 0, rx = 0, ry = 0;\n"
     "};\n"
     "bool leer_mando(MandoLeido& m) {\n"
     "    switch (sdl_pad_mode()) {\n"
     "    case SdlPadMode::kPending: return false;\n"
     "    case SdlPadMode::kSdl: {\n"
     "        SdlPad s;\n"
     "        if (!sdl_pad_read(s)) return false;\n"
     "        m.held = s.buttons | (s.lt > 100 ? kSrcLT : 0) | (s.rt > 100 ? kSrcRT : 0);\n"
     "        m.lx = s.lx, m.ly = s.ly, m.rx = s.rx, m.ry = s.ry;\n"
     "        return true;\n"
     "    }\n"
     "    case SdlPadMode::kXInput: break;\n"
     "    }\n"
     "    XINPUT_GAMEPAD g{};\n"
     "    if (!read_xinput(g)) return false;\n"
     "    m.held = g.wButtons | (g.bLeftTrigger > 100 ? kSrcLT : 0) | (g.bRightTrigger > 100 ? kSrcRT : 0);\n"
     "    m.lx = g.sThumbLX, m.ly = g.sThumbLY, m.rx = g.sThumbRX, m.ry = g.sThumbRY;\n"
     "    return true;\n"
     "}\n"
     "\n"
     "void tsuki_pad_source(plat::PadState& p) {\n"
     "    const Config& c = config();\n"
     "    MandoLeido g;\n"
     "    if (leer_mando(g)) {\n"
     "        p.lx = dead_zone(g.lx), p.ly = dead_zone(g.ly);\n"
     "        p.rx = dead_zone(g.rx), p.ry = dead_zone(g.ry);\n"
     "        for (const PadBind& b : c.pad)\n"
     "            if (g.held & b.mask) press(p, b.t);\n"
     "    }\n"),
    # --- tsukihime_common.cmake
    (CMAKE,
     "    endif()\nendforeach()\n",
     "    endif()\nendforeach()\n"
     "# AP_SDL_PAD (apply_sdl_pad.py): SDL3 solo para los mandos (pad_sdl.cpp): PlayStation, Switch y Xbox por USB y\n"
     "# Bluetooth. vcpkg: sdl3 para x64-windows. SDL3.dll va con /DELAYLOAD: sin la DLL el exe arranca y usa XInput.\n"
     "find_package(SDL3 CONFIG REQUIRED)\n"
     "target_link_libraries(${NX_GAME} PRIVATE SDL3::SDL3)\n"
     "if(MSVC)\n"
     "    target_link_options(${NX_GAME} PRIVATE \"/DELAYLOAD:SDL3.dll\")\n"
     "    target_compile_definitions(${NX_GAME} PRIVATE NX_SDL_DELAYLOAD)\n"
     "endif()\n"
     "add_custom_command(TARGET ${NX_GAME} POST_BUILD\n"
     "    COMMAND ${CMAKE_COMMAND} -E copy_if_different \"$<TARGET_FILE:SDL3::SDL3>\" \"$<TARGET_FILE_DIR:${NX_GAME}>\")\n"),
    # --- recomp_tsukihime.ps1: markers
    (RECOMP,
     "    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_CARGA', "
     "'hle_carga.cpp (copy it to games\\tsukihime_common)', $false),\n",
     "    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_CARGA', "
     "'hle_carga.cpp (copy it to games\\tsukihime_common)', $false),\n"
     "    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),\n"
     "    @('both', 'framework\\games\\tsukihime_common\\input_tsuki.cpp', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),\n"
     "    @('both', 'framework\\games\\tsukihime_common\\pad_sdl.cpp', 'AP_SDL_PAD', "
     "'pad_sdl.cpp and pad_sdl.h (copy them to games\\tsukihime_common)', $false),\n"),
]

if __name__ == "__main__":
    # Before touching anything: without pad_sdl.cpp/.h the build would break (input_tsuki.cpp includes them).
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--root", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework"))
    pre.add_argument("--remove", action="store_true")
    a, _ = pre.parse_known_args()
    if not a.remove:
        common = os.path.join(a.root, "games", "tsukihime_common")
        missing = [f for f in ("pad_sdl.cpp", "pad_sdl.h") if not os.path.isfile(os.path.join(common, f))]
        if missing:
            sys.exit(f"ERROR: {', '.join(missing)} missing in {common}: copy them first. Nothing touched.")
    run(MARK, EDITS, __doc__)