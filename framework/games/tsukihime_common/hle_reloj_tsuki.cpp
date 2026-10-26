// hle_reloj_tsuki.cpp - Hora actual para el juego (Tsukihime, las dos ediciones). AP_RELOJ
// (archivo nuevo: se copia a games/tsukihime_common; entra por el GLOB de tsukihime_common.cmake).
//
// Sintoma (2026-10-26): los guardados en un slot nuevo salen en blanco ("no save data found") y la fecha de todos los
// guardados del port es 1969-12-31 21:00:00 (hora Unix 0 en UTC-3). Con el mismo estado de partida, el slot del
// emulador y el del port son identicos salvo la fecha (+0x7138 del slot) y el "ultimo guardado" de la cabecera
// (0x161e), que el port no actualiza: el juego descarta el slot por la fecha.
// La fecha sale de main+0xf39d0 (global): si nn::time::IsInitialized, pide
// nn::time::StandardNetworkSystemClock::GetCurrentTime y, si falla, StandardUserSystemClock::GetCurrentTime, y pasa
// el resultado a nn::time::ToCalendarTime (svc_time.cpp lo convierte bien). En el port GetCurrentTime (sdk
// recompilado, que lee la memoria compartida de time) entrega 0.
// Aqui los dos GetCurrentTime devuelven la hora del PC (segundos Unix, UTC; la zona la aplica ToCalendarTime).
// Las primeras llamadas tambien corren la funcion real del sdk y escriben en el log que devolvia ("reloj: ..."),
// para dejar constancia de la causa. NX_TSUKI_RELOJ=0 deja la funcion real del sdk (como antes).
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include "kernel.h"  // CPU, log, call, find_symbol, NX_HLE

namespace {

using nx::CPU;

const char kNetwork[] = "_ZN2nn4time26StandardNetworkSystemClock14GetCurrentTimeEPNS0_9PosixTimeE";
const char kUser[] = "_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE";

bool solo_sdk() {
    static const bool v = [] {
        const char* e = std::getenv("NX_TSUKI_RELOJ");
        return e && std::strcmp(e, "0") == 0;
    }();
    return v;
}

int64_t unix_now() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

// (nn::time::PosixTime* out) -> nn::Result
void reloj(CPU& c, uint64_t real, const char* que, std::atomic<int>& avisos) {
    const uint64_t out = c.x[0];
    if (solo_sdk()) {
        if (real) nx::call(c, real);
        return;
    }
    if (real && avisos.load() < 3) {  // constancia: que entregaba el sdk
        avisos++;
        nx::call(c, real);
        int64_t sdk = out ? nx::ld<int64_t>(out) : 0;
        nx::log("reloj: %s: el sdk devolvio rc %#x, posix %lld; se entrega la hora del PC, %lld", que,
                uint32_t(c.x[0]), (long long)sdk, (long long)unix_now());
    }
    if (out) nx::st<int64_t>(out, unix_now());
    c.x[0] = 0;
}

std::atomic<int> g_avisos_red{0}, g_avisos_usuario{0};

void NetworkGetCurrentTime(CPU& c) {
    static const uint64_t real = nx::find_symbol(kNetwork);
    reloj(c, real, "StandardNetworkSystemClock::GetCurrentTime", g_avisos_red);
}

void UserGetCurrentTime(CPU& c) {
    static const uint64_t real = nx::find_symbol(kUser);
    reloj(c, real, "StandardUserSystemClock::GetCurrentTime", g_avisos_usuario);
}

}  // namespace

NX_HLE("_ZN2nn4time26StandardNetworkSystemClock14GetCurrentTimeEPNS0_9PosixTimeE", NetworkGetCurrentTime);
NX_HLE("_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE", UserGetCurrentTime);