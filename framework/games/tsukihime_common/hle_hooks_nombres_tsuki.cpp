// hle_hooks_nombres_tsuki.cpp - Version nativa de main+0x56720 (Tsukihime, edicion japonesa). AP_HOOK_NAME_TABLE
// (archivo nuevo del repo; entra por el GLOB de tsukihime_common.cmake; necesita apply_recomp_hooks.py y
// apply_hook_name_table.py, que lo anade a NX_RECOMP_HOOKS con tamano 0xa88 y primera instruccion 0xd10143ff).
//
// tsuki_name_table = main+0x56720: busca un nombre en una tabla de entradas de 32 bytes llamando a memcmp (por la PLT,
// main+0x152680) una vez por entrada. Era ~2,4 M llamadas a memcmp en la carga del titulo (perfil 2026-11-05: la
// mayor parte de lo que quedaba del stall de 689 ms era el salto main -> nx::call -> memcmp por cada entrada).
// Lo que hace la original (sacado del codigo generado de main_00056720):
//   - x0 = nombre, x1 = tabla, w2 = numero de entradas (int16), w3 & 0xffff != 0 = pasar a..z a mayusculas;
//   - clave = el nombre hasta el primer 0 o '.', como mucho 32 bytes; si los 32 son validos, la longitud queda en 0
//     (asi lo hace el juego);
//   - devuelve en w0 el indice de la primera entrada con memcmp(entrada, clave, longitud) == 0 y entrada[longitud]
//     < 0x21, o 0xffff si no hay (o si w2 < 1).
// Aqui se hace igual en nativo. Antes de la primera llamada se comprueba que main+0x152680 es la entrada de la PLT
// de memcmp (adrp/ldr/add/br hacia la GOT, y la GOT apunta al memcmp del sdk); si no, se usa siempre la original.
//
// Variables (se leen una vez):
//   NX_TSUKI_NAMES=0       la original (recompilada); =check corre las dos, el juego recibe lo de la original, y
//                          cualquier diferencia va al log como "hook tsuki_name_table: DIFERENCIA"
//   NX_TSUKI_NAMES_STATS=0 sin la linea "hook tsuki_name_table: N llamadas ..." (cada 2 s mientras hay llamadas)
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#ifndef TSUKI_HOOKS_SOLO_NUCLEO
#include "kernel.h"  // CPU, log, find_symbol, NX_HOOK
#endif

namespace tsuki_nombres {

constexpr uint64_t kEntry = 0x56720;    // main+
constexpr uint64_t kMemcmpPlt = 0x152680;  // main+: entrada de la PLT que llama la original
constexpr uint32_t kKeyMax = 32, kEntrySize = 32, kNotFound = 0xffff;

// Indice de la entrada o 0xffff. `scanned` suma las entradas recorridas.
inline uint32_t find(const uint8_t* name, const uint8_t* table, uint32_t w2, uint32_t w3, uint64_t& scanned) {
    const bool upper = (w3 & 0xffff) != 0;
    uint8_t key[kKeyMax];
    uint32_t len = 0;
    for (uint32_t i = 0; i < kKeyMax; i++) {
        const uint8_t ch = name[i];
        if (ch == 0 || ch == '.') {
            len = i;
            break;
        }
        key[i] = upper && uint8_t(ch - 'a') < 26 ? uint8_t(ch - 0x20) : ch;
        if (i == kKeyMax - 1) len = 0;  // 32 bytes sin terminador: el juego compara 0 bytes
    }
    const int32_t count = int16_t(uint16_t(w2));
    if (count < 1) return kNotFound;
    for (int32_t i = 0; i < count; i++) {
        const uint8_t* e = table + uint64_t(i) * kEntrySize;
        if ((len == 0 || std::memcmp(e, key, len) == 0) && e[len] < 0x21) {
            scanned += uint64_t(i) + 1;
            return uint32_t(i);
        }
    }
    scanned += uint64_t(count);
    return kNotFound;
}

// adrp x16 / ldr x17, [x16, #n] / add x16, x16, #n / br x17: direccion de la entrada de la GOT, o 0.
inline uint64_t plt_got(uint64_t stub) {
    uint32_t w[4];
    std::memcpy(w, reinterpret_cast<const void*>(stub), sizeof w);
    if ((w[0] & 0x9f00001fu) != 0x90000010u) return 0;           // adrp x16
    if ((w[1] & 0xffc003ffu) != 0xf9400211u) return 0;           // ldr x17, [x16, #imm]
    if ((w[2] & 0xffc003ffu) != 0x91000210u) return 0;           // add x16, x16, #imm
    if (w[3] != 0xd61f0220u) return 0;                           // br x17
    int64_t imm = int64_t(((w[0] >> 5) & 0x7ffff) << 2 | ((w[0] >> 29) & 3));
    if (imm & (int64_t(1) << 20)) imm -= int64_t(1) << 21;
    const uint64_t page = (stub & ~uint64_t(0xfff)) + uint64_t(imm << 12);
    return page + uint64_t((w[1] >> 10) & 0xfff) * 8;
}

}  // namespace tsuki_nombres

#ifndef TSUKI_HOOKS_SOLO_NUCLEO
namespace {

using nx::CPU;
using namespace tsuki_nombres;

enum class Mode { Original, Native, Check };

uint64_t now_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}

const char* mode_name(Mode m) { return m == Mode::Original ? "original" : m == Mode::Check ? "check" : "nativa"; }

Mode resolve_mode(uint64_t entry) {
    const char* e = std::getenv("NX_TSUKI_NAMES");
    Mode m = Mode::Native;
    if (e && std::strcmp(e, "0") == 0) m = Mode::Original;
    if (e && std::strcmp(e, "check") == 0) m = Mode::Check;
    if (m != Mode::Original) {
        const uint64_t base = entry - kEntry;
        const uint64_t got = plt_got(base + kMemcmpPlt);
        uint64_t target = 0;
        if (got) std::memcpy(&target, reinterpret_cast<const void*>(got), 8);
        const uint64_t memcmp_at = nx::find_symbol("memcmp");
        if (!got || !memcmp_at || target != memcmp_at) {
            nx::log("hook tsuki_name_table: main+0x152680 no es la PLT de memcmp (GOT %llx -> %llx, memcmp %llx): "
                    "se usa la original", (unsigned long long)got, (unsigned long long)target,
                    (unsigned long long)memcmp_at);
            m = Mode::Original;
        }
    }
    nx::log("hook tsuki_name_table: %s (NX_TSUKI_NAMES)", mode_name(m));
    return m;
}

std::atomic<uint64_t> g_calls{0}, g_scanned{0}, g_ns{0}, g_diffs{0}, g_last_log{0};

void stats(Mode m, uint64_t t0, uint64_t scanned) {
    static const bool on = [] {
        const char* e = std::getenv("NX_TSUKI_NAMES_STATS");
        return !(e && std::strcmp(e, "0") == 0);
    }();
    if (!on) return;
    const uint64_t t1 = now_ns();
    g_calls.fetch_add(1, std::memory_order_relaxed);
    g_scanned.fetch_add(scanned, std::memory_order_relaxed);
    g_ns.fetch_add(t1 - t0, std::memory_order_relaxed);
    uint64_t last = g_last_log.load(std::memory_order_relaxed);
    if (last == 0) {
        g_last_log.compare_exchange_strong(last, t1);
        return;
    }
    if (t1 - last < 2000000000ull || !g_last_log.compare_exchange_strong(last, t1)) return;
    const uint64_t calls = g_calls.exchange(0), sc = g_scanned.exchange(0), ns = g_ns.exchange(0);
    if (m == Mode::Original)
        nx::log("hook tsuki_name_table (original): %llu llamadas, %.1f ms en %.1f s", (unsigned long long)calls,
                ns / 1e6, (t1 - last) / 1e9);
    else
        nx::log("hook tsuki_name_table (%s): %llu llamadas, %llu entradas recorridas, %.1f ms en %.1f s, "
                "%llu diferencias",
                mode_name(m), (unsigned long long)calls, (unsigned long long)sc, ns / 1e6, (t1 - last) / 1e9,
                (unsigned long long)g_diffs.load());
}

void NameTableHook(CPU& c, void (*orig)(CPU&), uint64_t entry) {
    static const Mode mode = resolve_mode(entry);
    const uint64_t t0 = now_ns();
    if (mode == Mode::Original) {
        orig(c);
        stats(mode, t0, 0);
        return;
    }
    const uint64_t name = c.x[0], table = c.x[1];
    const uint32_t w2 = uint32_t(c.x[2]), w3 = uint32_t(c.x[3]);
    uint64_t scanned = 0;
    const uint32_t r = find(reinterpret_cast<const uint8_t*>(name), reinterpret_cast<const uint8_t*>(table), w2, w3,
                            scanned);
    if (mode == Mode::Check) {
        orig(c);
        const uint32_t want = uint32_t(c.x[0]);
        if (want != r) {
            const uint64_t n = g_diffs.fetch_add(1) + 1;
            if (n <= 20) {
                char s[40] = {};
                std::memcpy(s, reinterpret_cast<const void*>(name), sizeof s - 1);
                nx::log("hook tsuki_name_table: DIFERENCIA \"%s\" n=%d w3=%x: original %u, nativa %u", s,
                        int(int16_t(uint16_t(w2))), w3 & 0xffff, want, r);
            }
        }
        stats(mode, t0, scanned);
        return;
    }
    c.x[0] = r;  // w0
    stats(mode, t0, scanned);
}

}  // namespace

NX_HOOK("tsuki_name_table", NameTableHook);
#endif