// consola_tsuki.cpp - Tsukihime: la consola del exe.
//
// 1. Sin consola con doble clic. El exe es de consola: abierto desde el Explorador, Windows le crea una ventana
//    negra propia. Si esa consola es solo de este proceso (nadie mas la comparte: no se lanzo desde cmd/PowerShell),
//    se suelta al arrancar y la ventana se cierra (puede verse un instante). Lanzado desde una consola, se queda:
//    el log sigue a la vista y `2> log` funciona igual. El log de archivo (nx.log) no cambia.
//    NX_CONSOLE=1: la consola propia se queda tambien con doble clic.
// 2. Sin QuickEdit. En una consola que se queda, el "modo de edicion rapida" de Windows hace que un clic en ella
//    empiece una seleccion; mientras dura, toda escritura a la consola espera: el hilo que escribe una linea de log se
//    bloquea y con el el juego, hasta apretar una tecla. Se apaga solo para esta consola (Windows no cambia).
//    NX_QUICKEDIT=1: lo deja como esta.
#ifdef _WIN32
#include <windows.h>

#include <cstdio>
#include <cstdlib>

namespace {

bool env_on(const char* name) {
    const char* e = std::getenv(name);
    return e && e[0] == '1';
}

struct ConsoleSetup {
    ConsoleSetup() {
        DWORD procs[2] = {};
        const DWORD n = GetConsoleProcessList(procs, 2);  // 0: sin consola
        if (n == 0) return;
        if (n == 1 && !env_on("NX_CONSOLE")) {  // la consola es solo nuestra: la abrio el doble clic
            std::fflush(stdout), std::fflush(stderr);
            FreeConsole();
            // stdout/stderr apuntaban a la consola que ya no existe: a NUL, para que escribir no falle de forma rara
            FILE* f = nullptr;
            freopen_s(&f, "NUL", "w", stdout);
            freopen_s(&f, "NUL", "w", stderr);
            return;
        }
        if (env_on("NX_QUICKEDIT")) return;
        HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
        DWORD mode = 0;
        if (in == nullptr || in == INVALID_HANDLE_VALUE || !GetConsoleMode(in, &mode)) return;
        mode &= ~DWORD(ENABLE_QUICK_EDIT_MODE);
        mode |= ENABLE_EXTENDED_FLAGS;  // sin esto el cambio de QuickEdit no se aplica
        SetConsoleMode(in, mode);
    }
} g_console_setup;

}  // namespace
#endif