// Tsukihime -A piece of blue glass moon-, edicion global (010057A01E4D4000): lo que el runtime necesita saber del juego.
// Graficos por NVN (como Wonder). Video por movie:: (hle_movie.cpp + movie_core.cpp, FFmpeg).
// Shaders compilados en ejecucion por glslc: pendiente (por ahora los imports glslc* caen en stubs con log).
#include <nx/game.h>
#include <nx/runtime.h>

namespace nx {

const game::Info game::kInfo = {"tsukihime_global", "TSUKIHIME -A piece of blue glass moon- (Global)", 0x010057A01E4D4000ull,
                                "io.github.local.tsukihimeglobalrecomp",
                                "../../../data/tsuki_global",  // AP_DATA_ROOT_EXE: relativo a la carpeta del exe (build\<juego>\ -> port\data\...); NX_DATA_ROOT tiene prioridad
                                0x0 /* main.npdm: system resource size (0 tambien en la global) */,
                                0xb15700, 0xa1 /* tabla de formatos NVN del SDK 17.5.4 (find_constant.py, misma tabla que 0xc62028 del SDK 11) */,
                                0x2d8 /* AudioRenderSystem::GetExecutionMode, SDK 17.5.4 (sdk+0x210388: ldr w0, [x0, #0x2d8]) */
                                };

// Titulo de la ventana (NX_CUSTOM_WINDOW_TITLE en game.cmake, apply_game_window.py). extern: un const de namespace
// tiene enlace interno y platform_win.cpp no lo veria.
namespace game {
extern const wchar_t* const kWindowTitle = L"TSUKIHIME -A piece of blue glass moon- (Global)";
}

void game::after_init() {}
void game::renderer_start() {}
void game::first_frame() {}

}  // namespace nx
