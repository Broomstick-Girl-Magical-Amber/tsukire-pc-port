// hle_hooks_tsuki.cpp - Versiones nativas de dos funciones calientes del juego (Tsukihime). AP_RECOMP_HOOKS
// (archivo nuevo del repo; entra por el GLOB de tsukihime_common.cmake; necesita apply_recomp_hooks.py).
//
// Las dos se llaman con bl directo desde codigo recompilado, asi que NX_HLE no las alcanza: el recompilador las emite
// como <nombre>_orig mas un stub que pregunta por un NX_HOOK (lista NX_RECOMP_HOOKS de regen_tsuki.ps1).
//
// tsuki_lookup = main+0xa90c0 (edicion japonesa; el recompilador comprueba tamano 0x1278 y primera instruccion).
//   Busqueda lineal de un nombre de archivo en las tablas del juego. Era ~52 % del stall de ~1,6 s al cargar la
//   pantalla de titulo (perfil 2026-11-05; memcmp, ~16 %, es el otro hook).
//   Lo que hace la original (sacado del codigo generado de main_000A90C0):
//     - nombre = lo que sigue a la ultima '\' de los primeros 256 bytes de x0 (o hasta el primer 0);
//     - clave = 28 bytes del nombre, a..z en mayusculas salvo detras de un byte >= 0x80 (Shift-JIS);
//     - tabla i = *(main+0x193eb48 + i*0x68): hasta 24000 entradas de 32 bytes, nombre en los 28 primeros, termina en
//       la primera entrada que empieza con 0. Coincide la PRIMERA entrada igual a la clave hasta un 0 o un '.' (la
//       extension no cuenta) o los 28 bytes;
//     - prueba las tablas (w1 == 1 ? 4 : 3), (w1 == 1 ? 6 : 5), 8 y 7; las dos de en medio se saltan si son nulas.
//       En main+0x18c0514 (u32) deja la ranura de la ultima tabla probada: 2/0, 3/1, 5 y 4 (0 al entrar);
//     - devuelve el indice en w0, o -1 si no esta en ninguna.
//   Aqui se hace lo mismo en nativo (mismo recorrido lineal, sin cache: no hay estado que se pueda quedar viejo).
//   Si la tabla 3/4 o la 7 es nula (la original las lee sin mirar), llama a la original.
// memcmp = sdk memcmp (por simbolo: vale para las dos ediciones). Devuelve la resta de los primeros bytes distintos
//   (sin signo), 0 si son iguales, como la del sdk.
//
// Variables (se leen una vez):
//   NX_TSUKI_LOOKUP=0      la original (recompilada); =check corre las dos, el juego recibe lo de la original, y
//                          cualquier diferencia (retorno o ranura) va al log como "hook tsuki_lookup: DIFERENCIA"
//   NX_TSUKI_LOOKUP_STATS=0 sin la linea "hook tsuki_lookup: N llamadas ..." (cada 2 s mientras hay llamadas)
//   NX_TSUKI_MEMCMP=0      la original; =check igual que arriba ("hook memcmp: DIFERENCIA")
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#ifndef TSUKI_HOOKS_SOLO_NUCLEO
#include "kernel.h"  // CPU, log, NX_HOOK
#endif

namespace tsuki_hooks {

// ============================================================================================== busqueda (nucleo)
constexpr uint64_t kLookupEntry = 0xa90c0;  // main+
constexpr uint64_t kTables = 0x193eb48;     // main+: descriptores de tabla, el puntero a las entradas al inicio
constexpr uint64_t kTableStride = 0x68;
constexpr uint64_t kSlot = 0x18c0514;  // main+: u32, ranura de la ultima tabla probada
constexpr uint32_t kEntries = 24000, kEntrySize = 32, kNameBytes = 28;

struct Key {
    uint8_t b[kNameBytes];
};

inline Key make_key(const uint8_t* path) {
    const uint8_t* name = path;
    for (int j = 0; j < 256; j++) {
        const uint8_t ch = path[j];
        if (ch == 0) break;
        if (ch == '\\') name = path + j + 1;
    }
    Key k;
    for (uint32_t i = 0; i < kNameBytes; i++) {  // como la original: lee los 28 bytes aunque el nombre acabe antes
        const uint8_t ch = name[i];
        const bool lower = uint8_t(ch - 'a') < 26;
        const bool after_sjis = i > 0 && int8_t(k.b[i - 1]) < 0;
        k.b[i] = lower && !after_sjis ? uint8_t(ch - 0x20) : ch;
    }
    return k;
}

// Indice de la primera entrada que coincide, o -1. `scanned` suma las entradas recorridas.
inline int32_t scan(const uint8_t* table, const Key& k, uint64_t& scanned) {
    const uint8_t first = k.b[0];
    for (uint32_t n = 0; n < kEntries; n++) {
        const uint8_t* e = table + uint64_t(n) * kEntrySize;
        const uint8_t e0 = e[0];
        if (e0 == 0) {
            scanned += n + 1;
            return -1;
        }
        if (e0 != first) continue;
        bool hit = true;
        for (uint32_t i = 0; i < kNameBytes; i++) {
            if (e[i] != k.b[i]) {
                hit = false;
                break;
            }
            if (e[i] == 0 || e[i] == '.') break;
        }
        if (hit) {
            scanned += n + 1;
            return int32_t(n);
        }
    }
    scanned += kEntries;
    return -1;
}

inline const uint8_t* table_at(uint64_t base, uint32_t i) {
    uint64_t p;
    std::memcpy(&p, reinterpret_cast<const void*>(base + kTables + uint64_t(i) * kTableStride), 8);
    return reinterpret_cast<const uint8_t*>(p);
}

// La busqueda entera. false = hay que llamar a la original (una tabla que ella lee sin comprobar es nula).
inline bool lookup(uint64_t base, const uint8_t* path, uint32_t mode, uint32_t& ret, uint32_t& slot,
                   uint64_t& scanned) {
    const Key k = make_key(path);
    const bool m1 = mode == 1;
    struct Step {
        uint32_t table, slot;
        bool may_be_null;
    };
    const Step steps[4] = {{m1 ? 4u : 3u, m1 ? 2u : 0u, false}, {m1 ? 6u : 5u, m1 ? 3u : 1u, true}, {8, 5, true}, {7, 4, false}};
    for (const Step& s : steps) {
        const uint8_t* t = table_at(base, s.table);
        if (!t) {
            if (!s.may_be_null) return false;
            slot = s.slot;
            continue;
        }
        slot = s.slot;
        const int32_t r = scan(t, k, scanned);
        if (r >= 0) {
            ret = uint32_t(r);
            return true;
        }
    }
    ret = 0xffffffffu;
    return true;
}

// ============================================================================================== memcmp (nucleo)
inline int32_t memcmp_sdk(const uint8_t* a, const uint8_t* b, uint64_t n) {
    uint64_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t x, y;
        std::memcpy(&x, a + i, 8);
        std::memcpy(&y, b + i, 8);
        if (x != y) break;
    }
    for (; i < n; i++)
        if (a[i] != b[i]) return int32_t(a[i]) - int32_t(b[i]);
    return 0;
}

}  // namespace tsuki_hooks

#ifndef TSUKI_HOOKS_SOLO_NUCLEO
namespace {

using nx::CPU;
using namespace tsuki_hooks;

enum class Mode { Original, Native, Check };

Mode read_mode(const char* var) {
    const char* e = std::getenv(var);
    if (e && std::strcmp(e, "0") == 0) return Mode::Original;
    if (e && std::strcmp(e, "check") == 0) return Mode::Check;
    return Mode::Native;
}

const char* mode_name(Mode m) { return m == Mode::Original ? "original" : m == Mode::Check ? "check" : "nativa"; }

uint64_t now_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}

// ---------------------------------------------------------------------------------------------- tsuki_lookup
std::atomic<uint64_t> g_calls{0}, g_scanned{0}, g_ns{0}, g_diffs{0}, g_last_log{0};

void lookup_stats(Mode m, uint64_t t0, uint64_t scanned) {
    static const bool on = [] {
        const char* e = std::getenv("NX_TSUKI_LOOKUP_STATS");
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
        nx::log("hook tsuki_lookup (original): %llu llamadas, %.1f ms en %.1f s", (unsigned long long)calls,
                ns / 1e6, (t1 - last) / 1e9);
    else
        nx::log("hook tsuki_lookup (%s): %llu llamadas, %llu entradas recorridas, %.1f ms en %.1f s, %llu diferencias",
                mode_name(m), (unsigned long long)calls, (unsigned long long)sc, ns / 1e6, (t1 - last) / 1e9,
                (unsigned long long)g_diffs.load());
}

void LookupHook(CPU& c, void (*orig)(CPU&), uint64_t entry) {
    static const Mode mode = [] {
        const Mode m = read_mode("NX_TSUKI_LOOKUP");
        nx::log("hook tsuki_lookup: %s (NX_TSUKI_LOOKUP)", mode_name(m));
        return m;
    }();
    const uint64_t t0 = now_ns();
    if (mode == Mode::Original) {
        orig(c);
        lookup_stats(mode, t0, 0);
        return;
    }
    const uint64_t base = entry - kLookupEntry;
    const uint64_t path = c.x[0];
    const uint32_t arg = uint32_t(c.x[1]);
    uint32_t ret = 0, slot = 0;
    uint64_t scanned = 0;
    if (!lookup(base, reinterpret_cast<const uint8_t*>(path), arg, ret, slot, scanned)) {
        orig(c);
        lookup_stats(mode, t0, scanned);
        return;
    }
    uint32_t* slot_ptr = reinterpret_cast<uint32_t*>(base + kSlot);
    if (mode == Mode::Check) {
        orig(c);
        const uint32_t want_ret = uint32_t(c.x[0]), want_slot = *slot_ptr;
        if (want_ret != ret || want_slot != slot) {
            const uint64_t n = g_diffs.fetch_add(1) + 1;
            if (n <= 20) {
                char name[64] = {};
                std::memcpy(name, reinterpret_cast<const void*>(path), sizeof name - 1);
                nx::log("hook tsuki_lookup: DIFERENCIA \"%s\" w1=%u: original %d ranura %u, nativa %d ranura %u", name,
                        arg, int32_t(want_ret), want_slot, int32_t(ret), slot);
            }
        }
        lookup_stats(mode, t0, scanned);
        return;
    }
    *slot_ptr = slot;
    c.x[0] = ret;  // w0: los 32 bits altos a 0, como la original
    lookup_stats(mode, t0, scanned);
}

// ---------------------------------------------------------------------------------------------- memcmp
std::atomic<uint64_t> g_mc_calls{0}, g_mc_diffs{0};

void MemcmpHook(CPU& c, void (*orig)(CPU&), uint64_t) {
    static const Mode mode = [] {
        const Mode m = read_mode("NX_TSUKI_MEMCMP");
        nx::log("hook memcmp: %s (NX_TSUKI_MEMCMP)", mode_name(m));
        return m;
    }();
    if (mode == Mode::Original) {
        orig(c);
        return;
    }
    const int32_t r = memcmp_sdk(reinterpret_cast<const uint8_t*>(c.x[0]), reinterpret_cast<const uint8_t*>(c.x[1]),
                                 c.x[2]);
    if (mode == Mode::Check) {
        const uint64_t a = c.x[0], b = c.x[1], n = c.x[2];
        orig(c);
        const uint64_t calls = g_mc_calls.fetch_add(1) + 1;
        if (int32_t(uint32_t(c.x[0])) != r) {
            const uint64_t d = g_mc_diffs.fetch_add(1) + 1;
            if (d <= 20)
                nx::log("hook memcmp: DIFERENCIA %llx %llx n=%llu: original %d, nativa %d", (unsigned long long)a,
                        (unsigned long long)b, (unsigned long long)n, int32_t(uint32_t(c.x[0])), r);
        }
        if (calls % 100000 == 0)
            nx::log("hook memcmp (check): %llu llamadas, %llu diferencias", (unsigned long long)calls,
                    (unsigned long long)g_mc_diffs.load());
        return;
    }
    c.x[0] = uint32_t(r);  // w0, como la del sdk
}

}  // namespace

NX_HOOK("tsuki_lookup", LookupHook);
NX_HOOK("memcmp", MemcmpHook);
#endif