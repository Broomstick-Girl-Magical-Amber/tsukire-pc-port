"""apply_android_adpf.py - the game's worker threads in the ADPF session, and its target (AP_ANDROID_ADPF). Android.

perf_hint.cpp asks Android for higher clocks through an ADPF session (APerformanceHint): the threads that make a
frame join it and each frame's duration is reported against a 16.7 ms target. In Tsukihime (frame profile, S25,
2026-11-12) the game's main thread works ~4 ms per frame and waits ~8.5 ms for its own worker threads (two waits per
frame on a condition variable); the whole process uses ~12 ms of CPU per frame on 8 cores and ~12 % of the frames
take more than 20 ms (~54 fps). The session held only the main thread, and its reported work (~12.5 ms on average)
stayed under the target: Android had no reason to raise the clocks, and the workers it waits for were not in it.

Both on by default since version 3 (S25, 2026-11-12/13: each one alone helped little; together 53.7 -> 56.1 fps and the
frames over 20 ms 13.3 % -> 7.0 %, about the same heat as the threads alone). In config.txt:
- the game threads (NX_ADPF_GUEST=0 turns them off): every 600 frames, the guest threads that used at least 0.5 ms of
  CPU per frame (at most 6, the busiest first) join the session next to the ones already in it; once in, a thread
  stays until it drops under 0.25 ms or ends (version 4: a thread near the line made the session again 24 times in
  5 minutes, and each time Android forgets what it learned of it). The session is made again when that list changes.
  Their CPU time comes from each thread's own CPU clock (its host thread id).
- the target: 12 ms at 60 fps instead of the frame period (16.7 ms); with NX_FPS_CAP=30 it stays 33.3 ms.
  NX_ADPF_TARGET_MS=<ms> (4..33, decimals allowed) sets another one. A lower target makes Android raise the clocks
  earlier.
Log: `adpf: game threads in the session on, NX_ADPF_TARGET_MS unset (12 ms at 60 fps)` once, `adpf: game workers (join
at 0.5 ms of CPU per frame, leave under 0.25): 3 (tids ...)` at each check (the first 20), and `adpf: session made for
N threads (tids a b c ...), target 12.0 ms, 3 game workers`. If Android refuses a session with the workers (a thread
that just ended), it is made again without them.

- runtime/src/perf_hint.cpp: the options, the session with the extra threads.
- runtime/src/kernel.cpp: nx::busy_guest_tids (the host thread ids of the busy guest threads; Linux and Android).

Idempotent (marker AP_ANDROID_ADPF), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Version 4 (2026-11-13): a worker joins at 0.5 ms and leaves under 0.25 ms. Version 3: both on by default. Version 2 logged the options and each check. Remove the version
applied before with its own file (--remove) first. Independent of apply_frame_profile.py (other anchors). Also edits ../tools/recomp_tsukihime.ps1 ($Markers
rows, anchored after the nvn_present.cpp row of apply_android_present.py, which no other patcher uses).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_adpf.py
"""
from _patch import run

MARK = "AP_ANDROID_ADPF"
PERF = "runtime/src/perf_hint.cpp"
KERNEL = "runtime/src/kernel.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ perf_hint.cpp
    (PERF,
     '''void perf_hint_frame(int64_t ns) {
    const Api& a = api();
    if (!a.ok || ns <= 0) return;
    std::lock_guard lk(g_lock);
    if (g_dirty && !g_tids.empty()) {  // a new thread made part of the frame: a session with all of them
        if (g_session) a.close(g_session);
        // The ADPF target is the frame period: 33.3 ms when NX_FPS_CAP holds the game at 30 fps (nvn_queue.cpp).
        const char* cap = std::getenv("NX_FPS_CAP");
        int64_t target = cap && std::atoi(cap) > 0 && std::atoi(cap) <= 30 ? 33'333'333 : 16'666'667;
        g_session = a.create(a.get_manager(), g_tids.data(), g_tids.size(), target);
        g_dirty = false;
        log("adpf: %s for %zu threads", g_session ? "session made" : "no session", g_tids.size());
    }
''',
     '''}  // namespace nx::plat
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
'''),
    (PERF,
     '''#include <algorithm>
#include <cstdlib>
#include <mutex>
#include <vector>
''',
     '''#include <algorithm>
#include <cstdio>  // AP_ANDROID_ADPF
#include <cstdlib>
#include <mutex>
#include <vector>
'''),

    # ------------------------------------------------------------------------------------------------ kernel.cpp
    (KERNEL,
     '''        e.first += dt, e.second++;
    }
}
''',
     '''        e.first += dt, e.second++;
    }
}

// AP_ANDROID_ADPF (apply_android_adpf.py): the host thread ids of the guest threads that used at least min_ms of CPU
// per frame since the last call, with that CPU time in ms per frame (the busiest first, at most max), for the ADPF
// session (perf_hint.cpp). Linux and Android: each thread's own CPU clock by its host thread id; elsewhere none.
std::vector<std::pair<int32_t, double>> busy_guest_tids(uint64_t frames, double min_ms, size_t max) {
    std::vector<std::pair<int32_t, double>> out;
#if defined(__linux__)
    static std::map<uint64_t, uint64_t> before;  // guest thread id -> CPU ns at the last call
    std::vector<std::pair<uint64_t, int32_t>> rows;  // (CPU ns since the last call, host tid)
    {
        std::lock_guard al(g_all_lock);
        for (auto& w : g_all) {
            auto t = w.lock();
            if (!t || !t->started || t->exited || t->host_tid <= 0) continue;
            timespec ts{};
            if (clock_gettime(clockid_t((~int(t->host_tid) << 3) | 6), &ts) != 0) continue;  // that thread's CPU clock
            const uint64_t cpu = uint64_t(ts.tv_sec) * 1'000'000'000ull + uint64_t(ts.tv_nsec);
            auto it = before.find(t->id);
            if (it != before.end() && cpu >= it->second) rows.push_back({cpu - it->second, int32_t(t->host_tid)});
            before[t->id] = cpu;
        }
    }
    std::sort(rows.rbegin(), rows.rend());
    for (auto& r : rows) {
        const double ms = double(r.first) / 1e6 / double(frames ? frames : 1);
        if (out.size() < max && ms >= min_ms) out.push_back({r.second, ms});
    }
#else
    (void)frames, (void)min_ms, (void)max;
#endif
    return out;
}
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_present.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_present.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
    @('both', 'framework\\runtime\\src\\perf_hint.cpp', 'AP_ANDROID_ADPF', 'apply_android_adpf.py', $false),
    @('both', 'framework\\runtime\\src\\kernel.cpp', 'AP_ANDROID_ADPF', 'apply_android_adpf.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
