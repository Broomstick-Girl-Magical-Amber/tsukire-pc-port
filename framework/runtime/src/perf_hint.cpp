// Performance hints (docs/nvn/ANDROID-PORT.md A5, DEBUG-2.md #84): on Android the threads that make a frame (the
// game's render thread, which also replays NVN, and the presenting one) join an ADPF session (APerformanceHint,
// Android 13+) with a 16.7 ms target, and each frame's duration is reported. The system then raises the big cores'
// clocks when a frame runs late instead of guessing from load, which on phones keeps a steady 60 fps cooler and faster
// than the default governor. The API is looked up with dlsym, so older Android versions just go without.
// NX_ADPF=0: off. Other platforms: nothing.
#include "platform.h"

#if defined(__ANDROID__)
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>

#include <cerrno>
#include <unistd.h>

#include <algorithm>
#include <cstdio>  // AP_ANDROID_ADPF
#include <cstdlib>
#include <mutex>
#include <vector>

#include <nx/runtime.h>
#endif

namespace nx::plat {

#if defined(__ANDROID__)
namespace {

using GetManager = void* (*)();
using CreateSession = void* (*)(void*, const int32_t*, size_t, int64_t);
using ReportActual = int (*)(void*, int64_t);
using CloseSession = void (*)(void*);

struct Api {
    GetManager get_manager = nullptr;
    CreateSession create = nullptr;
    ReportActual report = nullptr;
    CloseSession close = nullptr;
    bool ok = false;
};

const Api& api() {
    static const Api a = [] {
        Api x;
        const char* e = std::getenv("NX_ADPF");
        if (e && e[0] == '0') return x;
        void* lib = dlopen("libandroid.so", RTLD_NOW);
        if (!lib) return x;
        x.get_manager = reinterpret_cast<GetManager>(dlsym(lib, "APerformanceHint_getManager"));
        x.create = reinterpret_cast<CreateSession>(dlsym(lib, "APerformanceHint_createSession"));
        x.report = reinterpret_cast<ReportActual>(dlsym(lib, "APerformanceHint_reportActualWorkDuration"));
        x.close = reinterpret_cast<CloseSession>(dlsym(lib, "APerformanceHint_closeSession"));
        x.ok = x.get_manager && x.create && x.report && x.close;
        return x;
    }();
    return a;
}

std::mutex g_lock;
std::vector<int32_t> g_tids;
void* g_session = nullptr;
bool g_dirty = false;

}  // namespace

// NX_GPU_TURBO=1 (an experiment, off by default): the Adreno kernel driver's power control off, so the GPU runs at its
// highest clock (thermal limits still apply). The idea is libadrenotools' adrenotools_set_turbo (BSD-2-Clause); this
// is written from the kernel's msm_kgsl UAPI: IOCTL_KGSL_SETPROPERTY (type 0x09, nr 0x32) with KGSL_PROP_PWRCTRL (0xE)
// and the value 0. The 30-fps lock lowered the GPU clock and frames then took longer (MEASUREMENTS.md, phase 4).
// The setting outlives the process (the GPU stayed at its top clock with the game closed), so every start sets it:
// off with NX_GPU_TURBO=1, back on (the driver's normal clock control) otherwise.
void gpu_turbo() {
    const char* e = std::getenv("NX_GPU_TURBO");
    const bool turbo = e && e[0] == '1';
    struct Property {
        unsigned int type;
        void* value;
        size_t sizebytes;
    };
    int fd = open("/dev/kgsl-3d0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        log("gpu turbo: /dev/kgsl-3d0 not opened (errno %d)", errno);
        return;
    }
    unsigned int enable = turbo ? 0 : 1;
    Property p{0xE, &enable, sizeof enable};
    int r = ioctl(fd, _IOW(0x09, 0x32, Property), &p);
    log("gpu turbo: KGSL power control %s: %s (errno %d)", turbo ? "off (NX_GPU_TURBO=1)" : "on (normal)",
        r == 0 ? "done" : "refused", r == 0 ? 0 : errno);
    if (!turbo) close(fd);
    // The descriptor stays open: the driver ties the setting to it.
}

void perf_hint_thread() {
    if (!api().ok) return;
    thread_local bool joined = false;
    if (joined) return;
    joined = true;
    std::lock_guard lk(g_lock);
    int32_t tid = int32_t(syscall(SYS_gettid));
    if (g_tids.size() < 8 && std::find(g_tids.begin(), g_tids.end(), tid) == g_tids.end())
        g_tids.push_back(tid), g_dirty = true;
}

}  // namespace nx::plat
namespace nx {
std::vector<std::pair<int32_t, double>> busy_guest_tids(uint64_t frames, double min_ms, size_t max);  // AP_ANDROID_ADPF
}
namespace nx::plat {

void perf_hint_frame(int64_t ns) {
    const Api& a = api();
    if (!a.ok || ns <= 0) return;
    std::lock_guard lk(g_lock);
    // AP_ANDROID_ADPF (apply_android_adpf.py): the game's busy worker threads join the session (every 600 frames;
    // NX_ADPF_GUEST=0 turns it off); NX_ADPF_TARGET_MS=<ms>, the session's target (12 ms at 60 fps by default).
    static const bool guest = [] {  // on unless NX_ADPF_GUEST=0
        const char* e = std::getenv("NX_ADPF_GUEST");
        return !(e && e[0] == '0');
    }();
    static std::vector<int32_t> extra;
    static uint64_t frames = 0;
    if (frames == 0 && !g_session)
        log("adpf: game threads in the session %s, NX_ADPF_TARGET_MS %s", guest ? "on" : "off (NX_ADPF_GUEST=0)",
            std::getenv("NX_ADPF_TARGET_MS") ? std::getenv("NX_ADPF_TARGET_MS") : "unset (12 ms at 60 fps)");
    if (guest && ++frames % 600 == 0) {
        // A thread joins at 0.5 ms of CPU per frame and, once in, stays until it drops under 0.25 ms or ends: a thread
        // near the line does not make the session again at every check (that restarts what Android learned of it).
        std::vector<int32_t> busy;
        for (const auto& [tid, ms] : nx::busy_guest_tids(600, 0.25, 12)) {
            if (busy.size() >= 6 || std::find(g_tids.begin(), g_tids.end(), tid) != g_tids.end()) continue;
            if (ms >= 0.5 || std::find(extra.begin(), extra.end(), tid) != extra.end()) busy.push_back(tid);
        }
        std::sort(busy.begin(), busy.end());
        static int checks = 0;
        if (checks++ < 20) {
            char ids[96] = "";
            for (size_t k = 0, n = 0; k < busy.size() && n + 12 < sizeof ids; k++)
                n += size_t(std::snprintf(ids + n, sizeof ids - n, k ? " %d" : "%d", int(busy[k])));
            log("adpf: game workers (join at 0.5 ms of CPU per frame, leave under 0.25): %zu (tids %s)", busy.size(), ids);
        }
        if (busy != extra) extra = busy, g_dirty = true;
    }
    if (g_dirty && !g_tids.empty()) {  // a new thread made part of the frame: a session with all of them
        if (g_session) a.close(g_session);
        // The ADPF target is the frame period: 33.3 ms when NX_FPS_CAP holds the game at 30 fps (nvn_queue.cpp).
        const char* cap = std::getenv("NX_FPS_CAP");
        int64_t target = cap && std::atoi(cap) > 0 && std::atoi(cap) <= 30 ? 33'333'333 : 16'666'667;
        if (target == 16'666'667) target = 12'000'000;  // AP_ANDROID_ADPF: 12 ms at 60 fps (measured on the S25)
        if (const char* t = std::getenv("NX_ADPF_TARGET_MS")) {
            const double ms = std::atof(t);
            if (ms >= 4.0 && ms <= 33.4) target = int64_t(ms * 1e6);
        }
        std::vector<int32_t> all = g_tids;
        all.insert(all.end(), extra.begin(), extra.end());
        g_session = a.create(a.get_manager(), all.data(), all.size(), target);
        size_t workers = extra.size();
        if (!g_session && workers) {  // a worker that just ended: without them
            all = g_tids, workers = 0;
            g_session = a.create(a.get_manager(), all.data(), all.size(), target);
        }
        g_dirty = false;
        char ids[128] = "";
        for (size_t k = 0, n = 0; k < all.size() && n + 12 < sizeof ids; k++)
            n += size_t(std::snprintf(ids + n, sizeof ids - n, k ? " %d" : "%d", int(all[k])));
        log("adpf: %s for %zu threads (tids %s), target %.1f ms, %zu game workers",
            g_session ? "session made" : "no session", all.size(), ids, double(target) / 1e6, workers);
    }
    if (g_session) a.report(g_session, ns);
}
#else
void gpu_turbo() {}
void perf_hint_thread() {}
void perf_hint_frame(int64_t) {}
#endif

}  // namespace nx::plat
