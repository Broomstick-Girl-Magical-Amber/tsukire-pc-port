"""apply_frame_profile.py - where a frame's time goes on the thread that presents (AP_FRAME_PROFILE). A diagnostic.

NX_SVC_PROFILE=1 (kernel.cpp) tells in which kernel calls a frame thread blocks, but for Tsukihime it fell short:
- the "game main" role is only marked from a call site of another game, so only the presenting thread was measured;
- every call is reported at the sdk's own svc stub (sdk+1627xx), never at the game code that waits;
- sleeps and waits that do not go through a kernel call (HLE) are not seen at all.

- runtime/src/kernel.cpp: svc_profile_site (the call site, level 2 only) and frame_profile_threads (CPU time of each
  guest thread per frame, through the per-thread CPU clock of its host thread id).
- runtime/src/nvn/nvn_queue.cpp: frame_profile(stage), called at three points of QueuePresentTexture.

Idempotent (marker AP_FRAME_PROFILE), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Independent of the other patchers (the anchors are original lines none of them touches). Also edits
../tools/recomp_tsukihime.ps1 ($Markers rows, anchored after the nvn_vk.cpp row of apply_android_present.py, which no
other patcher uses).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_frame_profile.py
"""
from _patch import run

MARK = "AP_FRAME_PROFILE"
KERNEL = "runtime/src/kernel.cpp"
QUEUE = "runtime/src/nvn/nvn_queue.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ kernel.cpp
    (KERNEL,
     '''}  // namespace

void svc_profile_mark(int role) { t_svc_role = role; }
''',
     '''// AP_FRAME_PROFILE (apply_frame_profile.py): NX_SVC_PROFILE=2 names the game code that made a slow kernel call instead
// of the sdk's svc stub: the first return address inside the main module, from the link register and then the frame
// records above the stack pointer (the sdk keeps them; the game's own code may use x29 for something else, so the
// walk stops at anything that is not a frame record just above). No game code in the chain: the stub's caller.
uint64_t svc_profile_site(const CPU& c, uint64_t pc) {
    static const bool sites = [] {
        const char* e = std::getenv("NX_SVC_PROFILE");
        return e && std::atoi(e) >= 2;
    }();
    if (!sites) return pc;
    auto in_main = [](uint64_t a) {
        const LoadedModule* m = module_at(a);
        return m && std::strcmp(m->info->name, "main") == 0;
    };
    const uint64_t lr = c.x[30], sp = c.x[31];
    if (in_main(lr)) return lr;
    uint64_t fp = c.x[29];
    for (int k = 0; k < 8 && (fp & 7) == 0 && fp >= sp && fp - sp < 0x40000; k++) {
        const uint64_t ret = ld<uint64_t>(fp + 8), next = ld<uint64_t>(fp);
        if (in_main(ret)) return ret;
        if (!module_at(ret) || next <= fp) break;
        fp = next;
    }
    return lr;
}
}  // namespace

// AP_FRAME_PROFILE: CPU time of every guest thread per frame since the last call (nvn_queue.cpp, every 600 presents,
// on the thread that presents). Linux and Android: the CPU clock of a thread by its host thread id.
void frame_profile_threads(uint64_t frames) {
#if defined(__linux__)
    struct Row {
        uint64_t id, cpu, entry;
        bool self;
    };
    static std::map<uint64_t, uint64_t> before;  // thread id -> CPU ns at the last call
    std::vector<Row> rows;
    {
        std::lock_guard al(g_all_lock);
        for (auto& w : g_all) {
            auto t = w.lock();
            if (!t || !t->started || t->exited || t->host_tid <= 0) continue;
            timespec ts{};
            if (clock_gettime(clockid_t((~int(t->host_tid) << 3) | 6), &ts) != 0) continue;  // that thread's CPU clock
            const uint64_t cpu = uint64_t(ts.tv_sec) * 1'000'000'000ull + uint64_t(ts.tv_nsec);
            auto it = before.find(t->id);
            if (it != before.end() && cpu >= it->second) rows.push_back({t->id, cpu - it->second, t->entry, t.get() == t_self});
            before[t->id] = cpu;
        }
    }
    std::sort(rows.begin(), rows.end(), [](const Row& a, const Row& b) { return a.cpu > b.cpu; });
    for (size_t i = 0; i < rows.size() && i < 12; i++) {
        const double ms = double(rows[i].cpu) / 1e6 / double(frames);
        if (ms < 0.05 && !rows[i].self) continue;
        log("frame profile: thread %llx (%sentry %s): %.2f ms of CPU per frame", (unsigned long long)rows[i].id,
            rows[i].self ? "presents; " : "", describe(rows[i].entry).c_str(), ms);
    }
#else
    (void)frames;
#endif
}

void svc_profile_mark(int role) { t_svc_role = role; }
'''),
    (KERNEL,
     '''    if (dt >= 30000) {
        std::lock_guard lk(g_svc_lock);
''',
     '''    if (dt >= 30000) {
        pc = svc_profile_site(c, pc);  // AP_FRAME_PROFILE (NX_SVC_PROFILE=2: the game's call site)
        std::lock_guard lk(g_svc_lock);
'''),

    # ------------------------------------------------------------------------------------------------ nvn_queue.cpp
    (QUEUE,
     '''void svc_profile_report(uint64_t frames, int role);
''',
     '''void svc_profile_report(uint64_t frames, int role);
void frame_profile_threads(uint64_t frames);  // AP_FRAME_PROFILE (kernel.cpp)
'''),
    (QUEUE,
     '''void QueuePresentTexture(CPU& c) {
    // The frame's work, from the end of the last present's pacing to now (perf_hint.cpp: ADPF on Android).
''',
     '''// AP_FRAME_PROFILE (apply_frame_profile.py): NX_SVC_PROFILE=2, where the presenting thread's frame goes, per 600
// presents. Stage 0: the present starts (the game's work since the last one ends here); 1: the wait for the replay is
// over, the pacing sleep starts; 2: the present returns.
void frame_profile(int stage) {
    static const bool on = [] {
        const char* e = std::getenv("NX_SVC_PROFILE");
        return e && std::atoi(e) >= 2;
    }();
    if (!on) return;
    static uint64_t n = 0, t_end = 0, cpu_end = 0, t_in = 0, t_pace = 0, proc_from = 0;
    static uint64_t work = 0, cpu = 0, queue = 0, pace = 0, bins[6] = {};
    auto thread_cpu = [] {
        uint64_t ns = 0;
        long f[2], s[2];
        plat::thread_counters(ns, f, s);
        return ns;
    };
    auto process_cpu = []() -> uint64_t {
#if defined(_WIN32)
        return 0;  // not measured on Windows
#else
        timespec ts{};
        clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
        return uint64_t(ts.tv_sec) * 1'000'000'000ull + uint64_t(ts.tv_nsec);
#endif
    };
    const uint64_t now = plat::monotonic_ns();
    if (stage == 0) {
        t_in = now, t_pace = 0;
        if (t_end) {
            const uint64_t w = now - t_end;
            const double ms = double(w) / 1e6;
            work += w, cpu += thread_cpu() - cpu_end;
            bins[ms < 8 ? 0 : ms < 12 ? 1 : ms < 16 ? 2 : ms < 20 ? 3 : ms < 33 ? 4 : 5]++;
        }
    } else if (stage == 1) {
        t_pace = now, queue += now - t_in;
    } else {
        if (t_pace) pace += now - t_pace;
        else queue += now - t_in;
        t_end = now, cpu_end = thread_cpu();
        if (!proc_from) proc_from = process_cpu(), frame_profile_threads(1);  // the first call only takes the starting values
        if (++n % 600 == 0) {
            const double f = 600.0 * 1e6;
            const uint64_t proc = process_cpu();
            log("frame profile: per frame %.2f ms = game work %.2f (on a CPU %.2f, not running %.2f) + waiting for the "
                "replay %.2f + pacing sleep %.2f; all threads of the process %.2f ms of CPU",
                double(work + queue + pace) / f, double(work) / f, double(cpu) / f,
                double(work > cpu ? work - cpu : 0) / f, double(queue) / f, double(pace) / f, double(proc - proc_from) / f);
            log("frame profile: game work per frame: <8 ms %llu, 8-12 %llu, 12-16 %llu, 16-20 %llu, 20-33 %llu, more %llu",
                (unsigned long long)bins[0], (unsigned long long)bins[1], (unsigned long long)bins[2],
                (unsigned long long)bins[3], (unsigned long long)bins[4], (unsigned long long)bins[5]);
            frame_profile_threads(600);
            work = cpu = queue = pace = 0, proc_from = proc;
            for (auto& b : bins) b = 0;
            t_end = plat::monotonic_ns(), cpu_end = thread_cpu();  // the report itself is not the game's work
        }
    }
}

void QueuePresentTexture(CPU& c) {
    // The frame's work, from the end of the last present's pacing to now (perf_hint.cpp: ADPF on Android).
'''),
    (QUEUE,
     '''    if (work_from) plat::perf_hint_frame(int64_t(t_in - work_from));
''',
     '''    if (work_from) plat::perf_hint_frame(int64_t(t_in - work_from));
    frame_profile(0);  // AP_FRAME_PROFILE
'''),
    (QUEUE,
     '''        pace_present(w->interval);
    }
    work_from = plat::monotonic_ns();
''',
     '''        frame_profile(1);  // AP_FRAME_PROFILE
        pace_present(w->interval);
    }
    work_from = plat::monotonic_ns();
    frame_profile(2);  // AP_FRAME_PROFILE
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
    @('both', 'framework\\runtime\\src\\kernel.cpp', 'AP_FRAME_PROFILE', 'apply_frame_profile.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_queue.cpp', 'AP_FRAME_PROFILE', 'apply_frame_profile.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
