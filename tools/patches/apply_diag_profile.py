# apply_diag_profile.py - TEMPORARY DIAGNOSTIC: sampling of the host threads during stalls (Windows only).
#
# Creates games\tsukihime_common\diag_perfil.cpp (new file; it does not edit any existing file, that is why it does
# not use _patch.py). --remove deletes it only if it is still identical to what was installed: the tree ends up byte
# for byte as before. It is temporary: it is not in $Markers of recomp_tsukihime.ps1. NX_DIAG_PERFIL=0 turns it off
# without rebuilding. The C++ it installs (CONTENT) keeps its comments in Spanish.
#
# Usage (in <root>\framework):
#   private\venv\Scripts\python ..\tools\patches\apply_diag_profile.py [--framework <dir>] [--remove]
#   after: cmake --build build\tsukihime_global
import argparse
import os
import sys

NAME = "diag_perfil.cpp"
CONTENT = r'''// diag_perfil.cpp - DIAGNOSTICO TEMPORAL (apply_diag_profile.py; se quita con --remove). Solo Windows.
//
// Muestreo de todos los hilos del proceso (cada 2 ms: SuspendThread + GetThreadContext + ResumeThread) para ver en
// que funcion del host se va el tiempo cuando el juego no saca cuadros (efectos poco fluidos, 2026-10-19).
// No toca nada del juego: solo lee el RIP de cada hilo.
//
// Log (las direcciones del exe van como VA del enlazador, 0x140000000 + RVA, igual que el crash handler: se
// traducen con el .map de lld-link):
//   perfil: stall <ms> ms ...    al volver los cuadros tras > 100 ms sin cuadro: por hilo con trabajo, sus 12
//                                funciones mas vistas (inicio de la funcion por RtlLookupFunctionEntry; las muestras
//                                dentro de ntdll/KERNELBASE/kernel32/win32u cuentan como espera)
//   perfil: total ...            cada 5 stalls: las 30 funciones con mas muestras sumando todos los hilos y stalls
//   perfil: lento ...            cada 300 cuadros de 20-100 ms, por hilo, acumulado
// NX_DIAG_PERFIL=0 lo apaga sin recompilar.
#ifdef _WIN32
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <windows.h>
#include <tlhelp32.h>

#include "kernel.h"  // log, g_frame

namespace {

uint64_t now_ns() {
    static const LARGE_INTEGER f = [] {
        LARGE_INTEGER x;
        QueryPerformanceFrequency(&x);
        return x;
    }();
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return uint64_t(double(t.QuadPart) * 1e9 / double(f.QuadPart));
}

// Modulo de una direccion: base y nombre (cache por base; pocas DLL).
struct Mod {
    uint64_t base = 0;
    std::string name;
    bool wait = false;  // ntdll / KERNELBASE / win32u: el hilo esta esperando o durmiendo
};
std::map<uint64_t, Mod> g_mods;  // por base
const Mod* module_of(uint64_t pc) {
    HMODULE m = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(pc), &m))
        return nullptr;
    uint64_t base = uint64_t(m);
    auto it = g_mods.find(base);
    if (it != g_mods.end()) return &it->second;
    char path[MAX_PATH] = "?";
    GetModuleFileNameA(m, path, MAX_PATH);
    const char* n = std::max(std::strrchr(path, '\\'), std::strrchr(path, '/'));
    Mod md;
    md.base = base;
    md.name = n ? n + 1 : path;
    std::string low = md.name;
    for (char& ch : low) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    md.wait = low == "ntdll.dll" || low == "kernelbase.dll" || low == "win32u.dll" || low == "kernel32.dll";
    return &(g_mods[base] = md);
}

std::string where(uint64_t pc) {
    const Mod* m = module_of(pc);
    char b[160];
    if (!m) {
        std::snprintf(b, sizeof b, "?%llx", (unsigned long long)pc);
        return b;
    }
    if (m->base == uint64_t(GetModuleHandleW(nullptr)))
        std::snprintf(b, sizeof b, "exe+%llx", (unsigned long long)(pc - m->base + 0x140000000ull));
    else
        std::snprintf(b, sizeof b, "%s+%llx", m->name.c_str(), (unsigned long long)(pc - m->base));
    return b;
}

// Inicio de la funcion que contiene pc (tabla de unwind x64); si no tiene (hoja), el pc mismo.
uint64_t func_of(uint64_t pc) {
    DWORD64 base = 0;
    PRUNTIME_FUNCTION f = RtlLookupFunctionEntry(DWORD64(pc), &base, nullptr);
    return f ? uint64_t(base) + f->BeginAddress : pc;
}

struct Bucket {
    std::unordered_map<DWORD, std::unordered_map<uint64_t, uint32_t>> pcs;  // tid -> rip -> muestras
    std::unordered_map<DWORD, uint32_t> total;
    void clear() { pcs.clear(), total.clear(); }
};

std::unordered_map<uint64_t, uint32_t> g_total;  // funcion -> muestras con trabajo, todos los stalls
uint32_t g_stalls = 0;

void report(const char* head, Bucket& b, bool to_total = false) {
    std::string out = head;
    std::vector<std::pair<uint32_t, DWORD>> busy;
    for (auto& [tid, m] : b.pcs) {
        uint32_t work = 0;
        for (auto& [pc, n] : m) {
            const Mod* md = module_of(pc);
            if (!md || !md->wait) work += n;
        }
        if (work * 10 >= b.total[tid] && work >= 5) busy.push_back({work, tid});
    }
    std::sort(busy.rbegin(), busy.rend());
    if (busy.size() > 8) busy.resize(8);
    nx::log("%s", out.c_str());
    for (auto& [work, tid] : busy) {
        std::unordered_map<uint64_t, uint32_t> byf;
        for (auto& [pc, n] : b.pcs[tid]) {
            const Mod* md = module_of(pc);
            if (!md || !md->wait) byf[func_of(pc)] += n;
        }
        std::vector<std::pair<uint32_t, uint64_t>> v;
        for (auto& [fn, n] : byf) {
            v.push_back({n, fn});
            if (to_total) g_total[fn] += n;
        }
        std::sort(v.rbegin(), v.rend());
        std::string line;
        for (size_t k = 0; k < v.size() && k < 12; k++) {
            char c[200];
            std::snprintf(c, sizeof c, "%s %s x%u", k ? "," : "", where(v[k].second).c_str(), v[k].first);
            line += c;
        }
        nx::log("perfil:   hilo %lu: %u de %u muestras con trabajo:%s", (unsigned long)tid, work, b.total[tid],
                line.c_str());
    }
    if (to_total && ++g_stalls % 5 == 0) {
        std::vector<std::pair<uint32_t, uint64_t>> v;
        for (auto& [fn, n] : g_total) v.push_back({n, fn});
        std::sort(v.rbegin(), v.rend());
        nx::log("perfil: total de %u stalls (todas las muestras con trabajo, por funcion):", g_stalls);
        for (size_t k = 0; k < v.size() && k < 30; k++)
            nx::log("perfil:   %6u %s", v[k].first, where(v[k].second).c_str());
    }
}

void sampler() {
    const DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    // Espera al primer cuadro (el log y el juego ya estan andando).
    while (nx::g_frame.load() == 0) Sleep(50);
    nx::log("perfil: muestreo activo (diag_perfil.cpp, temporal; NX_DIAG_PERFIL=0 lo apaga)");
    std::unordered_map<DWORD, HANDLE> handles;
    uint64_t last_frame = nx::g_frame.load(), frame_at = now_ns(), slow_frames = 0;
    Bucket cur, slow;
    uint64_t last_list = 0;
    std::vector<DWORD> tids;
    for (;;) {
        Sleep(2);
        const uint64_t t = now_ns();
        // Lista de hilos cada 500 ms (aparecen y desaparecen hilos de trabajo).
        if (t - last_list > 500'000'000ull) {
            last_list = t;
            tids.clear();
            HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
            if (snap != INVALID_HANDLE_VALUE) {
                THREADENTRY32 te;
                std::memset(&te, 0, sizeof te);
                te.dwSize = sizeof te;
                for (BOOL ok = Thread32First(snap, &te); ok; ok = Thread32Next(snap, &te))
                    if (te.th32OwnerProcessID == pid && te.th32ThreadID != self) tids.push_back(te.th32ThreadID);
                CloseHandle(snap);
            }
            for (auto it = handles.begin(); it != handles.end();)
                if (std::find(tids.begin(), tids.end(), it->first) == tids.end()) CloseHandle(it->second), it = handles.erase(it);
                else ++it;
        }
        // Cuadro nuevo: cerrar el intervalo anterior.
        const uint64_t f = nx::g_frame.load();
        if (f != last_frame) {
            const uint64_t ms = (t - frame_at) / 1'000'000;
            if (ms > 100) {
                char h[128];
                std::snprintf(h, sizeof h, "perfil: stall %llu ms despues del cuadro %llu:", (unsigned long long)ms,
                              (unsigned long long)last_frame);
                report(h, cur, true);
            } else if (ms >= 20) {
                for (auto& [tid, m] : cur.pcs)
                    for (auto& [pc, n] : m) slow.pcs[tid][pc] += n;
                for (auto& [tid, n] : cur.total) slow.total[tid] += n;
                if (++slow_frames % 300 == 0) {
                    report("perfil: lento: acumulado de 300 cuadros de 20-100 ms:", slow);
                    slow.clear();
                }
            }
            cur.clear();
            last_frame = f;
            frame_at = t;
        }
        // Solo se muestrea dentro de un cuadro que ya va lento (> 16 ms): lo normal no interesa y cuesta.
        if (t - frame_at < 16'000'000ull) continue;
        for (DWORD tid : tids) {
            HANDLE& h = handles[tid];
            if (!h) h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, tid);
            if (!h) continue;
            if (SuspendThread(h) == DWORD(-1)) continue;
            CONTEXT ctx{};
            ctx.ContextFlags = CONTEXT_CONTROL;
            const bool ok = GetThreadContext(h, &ctx) != 0;
            ResumeThread(h);
            if (!ok) continue;
            cur.pcs[tid][ctx.Rip]++;  // despues de reanudar: nada de memoria mientras el hilo esta suspendido
            cur.total[tid]++;
        }
    }
}

struct Start {
    Start() {
        const char* e = std::getenv("NX_DIAG_PERFIL");
        if (e && std::strcmp(e, "0") == 0) return;
        std::thread(sampler).detach();
    }
} g_start;

}  // namespace

#endif  // _WIN32
'''
DATA = CONTENT.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def fail(msg):
    print("ERROR: " + msg)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--framework", default=os.getcwd())
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    common = os.path.join(os.path.abspath(a.framework), "games", "tsukihime_common")
    if not os.path.isdir(common):
        fail("%s does not exist (run from <root>\\framework or pass --framework)" % common)
    dest = os.path.join(common, NAME)
    if a.remove:
        if not os.path.exists(dest):
            print("It was not applied: nothing to remove.")
            return
        if open(dest, "rb").read() != DATA:
            fail("%s changed since it was installed; not deleted (check it by hand)" % dest)
        os.remove(dest)
        print("Removed: %s" % dest)
        print("Next: cmake --build build\\tsukihime_global")
        return
    if os.path.exists(dest):
        if open(dest, "rb").read() == DATA:
            print("Already applied: nothing to do.")
            return
        fail("%s already exists with other content; not touched" % dest)
    tmp = dest + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(DATA)
        os.replace(tmp, dest)
    except OSError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        fail("could not write %s: %s" % (dest, e))
    print("Applied: %s" % dest)
    print("Next: cmake --build build\\tsukihime_global")


if __name__ == "__main__":
    main()