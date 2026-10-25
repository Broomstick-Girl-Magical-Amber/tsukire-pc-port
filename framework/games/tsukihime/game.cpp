// Tsukihime -A piece of blue glass moon- (01001DC01486A000): lo que el runtime necesita saber del juego.
// Graficos por NVN (como Wonder). Video por movie:: (hle_movie.cpp + movie_core.cpp, FFmpeg).
// Shaders compilados en ejecucion por glslc: pendiente (por ahora los imports glslc* caen en stubs con log).
#include <nx/game.h>
#include <nx/runtime.h>

namespace nx {

const game::Info game::kInfo = {"tsukihime", "Tsukihime -A piece of blue glass moon-", 0x01001DC01486A000ull,
                                "io.github.local.tsukihimerecomp",
                                "../../../data/tsuki",  // AP_DATA_ROOT_EXE: relativo a la carpeta del exe (build\<juego>\ -> port\data\...); NX_DATA_ROOT tiene prioridad
                                0x0 /* main.npdm: system resource size (SDK 11: no usa) */,
                                0xc62028, 0xa1 /* tabla de formatos NVN del SDK 11 */,
                                0x2b8 /* AudioRenderSystem: modo de ejecucion (SDK 11) */
                                };

// Titulo de la ventana (NX_CUSTOM_WINDOW_TITLE en game.cmake, apply_game_window.py). extern: un const de namespace
// tiene enlace interno y platform_win.cpp no lo veria.
namespace game {
extern const wchar_t* const kWindowTitle = L"Tsukihime -A piece of blue glass moon-";
}

void game::after_init() {}
void game::renderer_start() {}
void game::first_frame() {}

}  // namespace nx
