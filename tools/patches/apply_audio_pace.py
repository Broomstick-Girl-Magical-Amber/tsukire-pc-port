"""apply_audio_pace.py - only an audio update that changes the effects waits for a render (AP_AUDIO_PACE).

Why: AudioRenderSystemUpdate (hle_audio.cpp) made every update wait until our 5 ms render thread had rendered once
since the previous update (DEBUG.md #28, DEBUG-2 #74: the limiter crash at boot). The game updates from two threads (on the
phone its main thread, the one that presents, and another one), so each update waited for a render the other one had
used: on a slow phone ~220 updates per second waited 5-8 ms each on average (max ~400 ms), most of the main
thread's frame ("not running" 10-24 ms of a 14-27 ms frame in the frame profile). Measured with
apply_audio_wait_diag.py; the render thread itself was on time (late 0.12 ms on average).

What the wait protects: a new effect's "initialize" status (and any other one-shot status of an effect) travels in the
effect's parameters, in the effects section of exactly one update's input; the next render initializes the effect.
A second update before that render overwrote the status, and the render applied a limiter that was never initialized.
Only the effects section carries that, so:
  - an update whose effects section is byte for byte the previous update's (same render system) does not wait:
    it cannot overwrite a status no render has seen, it writes the same bytes again;
  - an update that changes the effects waits for a render exactly as before (same 1 s bound, same log lines);
  - an input whose header does not add up waits as before, and so does the next update after it;
  - updates run one at a time (a mutex from the check to the update), so a check is never made stale by an update
    from the other thread;
  - NX_AUDIO_PACE=all (environment or config.txt): every update waits, as before.
The input layout: a 0x40-byte header (section sizes at 0x04-0x28, the total at 0x3c), then behaviour, memory pools,
voice channel resources, voices, effects, the splitters ("SNDH", the one section the header has no size for), mixes,
sinks, performance, renderer info. The check: the sizes fit in the total and in the input, and when bytes are left
over for the splitters they start with "SNDH" right where the effects end.
What to see in nx.log:
  audio: pacing: update input N bytes (REVx), effects M bytes at +0x..: an update waits for a render only when it
         changes them                                                  (once; "not understood (...)" otherwise)
  audio: pacing: of the last 6000 updates A changed the effects (or were not understood), B of them waited for a render
                                                                       (every 6000 updates, ~30 s)
To test the crash: tools\\dev\\boot_loop.ps1 (boots the game N times and looks for CRASH in each nx.log).

Edits: runtime/src/hle_audio.cpp (after the g_renders line and the pacing block of AudioRenderSystemUpdate; the
temporary apply_audio_wait_diag.py edits other lines of it, so both apply and remove in any order) and
../tools/recomp_tsukihime.ps1 (its $Markers row, after the row of apply_audio_out.py, which no other patcher uses
as an anchor).
Idempotent (marker AP_AUDIO_PACE), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_audio_pace.py            then rebuild (PC: recomp_tsukihime.ps1 -Edition <ed> -From 9;
                                                         phone: tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_audio_pace.py --remove
"""
from _patch import run

MARK = "AP_AUDIO_PACE"

AUDIO = "runtime/src/hle_audio.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

HELPERS = r'''// AP_AUDIO_PACE (apply_audio_pace.py): only an update that changes the effects waits for a render.
// - The limiter's "initialize" status (DEBUG.md #28) is part of the effect's parameters, which the game sends in the
//   effects section of every update's input. An update whose effects are byte for byte those of the previous update
//   writes the same bytes again: it cannot overwrite a status no render has used, so it does not wait.
// - Before, every update waited. The game updates from two threads (its main thread, the one that presents, and
//   another one), so each update waited for a render the other one had used: 5-8 ms per update on a slow phone.
// - The input: a 0x40-byte header (section sizes at 0x04-0x28, the total at 0x3c), then behaviour, memory pools,
//   voice channel resources, voices, effects, the splitters ("SNDH", the one section the header has no size for),
//   mixes, sinks... An input that does not add up waits, as before. NX_AUDIO_PACE=all: every update waits.
std::mutex g_update_order;  // one update at a time, from the check to the update: a check is never stale

struct UpdatedEffects {
    uint64_t system = 0;
    bool known = false;  // false: the last update's input was not understood, the next one waits
    std::vector<uint8_t> bytes;
};
std::vector<UpdatedEffects> g_updated_effects;  // the last update's effects per render system (under g_update_order)

// The effects section of an update's input (Update's x5, x6); false and why if the header does not add up.
bool update_effects(uint64_t in, uint64_t in_size, uint64_t& at, uint32_t& len, const char*& why) {
    if (!in || in_size < 0x40) return why = "no header", false;
    uint32_t h[16];
    std::memcpy(h, reinterpret_cast<const void*>(in), sizeof h);
    uint64_t sum = 0x40;
    for (int i = 1; i <= 10; i++) sum += h[i];
    if (h[15] > in_size) return why = "total over the input's size", false;
    if (sum > h[15]) return why = "sections over the total", false;
    const uint64_t off = 0x40 + uint64_t(h[1]) + h[2] + h[4] + h[3];  // behaviour, pools, voice resources, voices
    const uint64_t splitters = h[15] - sum;
    if (splitters && (splitters < 16 || std::memcmp(reinterpret_cast<const void*>(in + off + h[5]), "SNDH", 4)))
        return why = "no splitters (SNDH) where the effects end", false;
    at = in + off, len = h[5];
    return true;
}

// Whether this update waits for a render (called under g_update_order): its effects differ from the last update's.
bool update_needs_render(const CPU& c) {
    static const bool all = [] {
        const char* e = std::getenv("NX_AUDIO_PACE");
        return e && !std::strcmp(e, "all");
    }();
    static unsigned logged = 0;  // 1: understood, 2: not understood (each logged once)
    if (all) {
        if (!logged) logged = 3, log("audio: pacing: NX_AUDIO_PACE=all: every update waits for a render");
        return true;
    }
    const uint64_t system = c.x[0], in = c.x[5], in_size = c.x[6];
    auto it = std::find_if(g_updated_effects.begin(), g_updated_effects.end(),
                           [&](const UpdatedEffects& e) { return e.system == system; });
    if (it == g_updated_effects.end()) {
        g_updated_effects.push_back({});
        it = g_updated_effects.end() - 1;
        it->system = system;
    }
    uint64_t at = 0;
    uint32_t len = 0;
    const char* why = "";
    if (!update_effects(in, in_size, at, len, why)) {
        if (!(logged & 2)) {
            logged |= 2;
            uint32_t h[16] = {};
            if (in && in_size >= sizeof h) std::memcpy(h, reinterpret_cast<const void*>(in), sizeof h);
            log("audio: pacing: update input not understood (%s; %llu bytes, header %08x %x %x %x %x %x %x %x %x %x %x "
                "total %x): it waits for a render, as before", why, (unsigned long long)in_size, h[0], h[1], h[2], h[3],
                h[4], h[5], h[6], h[7], h[8], h[9], h[10], h[15]);
        }
        it->known = false;
        return true;
    }
    if (!(logged & 1)) {
        logged |= 1;
        char rev[5] = {};
        std::memcpy(rev, reinterpret_cast<const void*>(in), 4);
        for (char& ch : rev) ch = ch && (ch < 0x20 || ch > 0x7e) ? '?' : ch;
        log("audio: pacing: update input %llu bytes (%s), effects %u bytes at +0x%llx: an update waits for a render only "
            "when it changes them", (unsigned long long)in_size, rev, len, (unsigned long long)(at - in));
    }
    const uint8_t* p = reinterpret_cast<const uint8_t*>(at);
    if (it->known && it->bytes.size() == len && (!len || !std::memcmp(it->bytes.data(), p, len))) return false;
    it->bytes.assign(p, p + len);
    it->known = true;
    return true;
}
'''

PACE_OLD = '''    {
        std::unique_lock pk(g_pace_lock);
        static uint64_t waited = 0;
        if (g_renders == g_renders_at_update && waited++ < 5) log("audio: update waits for a render");
        // 1 s, not 50 ms: at boot, with every thread loading, a render took longer and the second update went through
        // (the limiter crash again, once in ~25 Windows runs). The render thread runs every 5 ms: 1 s only bounds a stall.
        if (!g_pace_cv.wait_for(pk, std::chrono::seconds(1), [] { return g_renders != g_renders_at_update; }))
            log("audio: no render for 1 s since the last update: updating anyway");
    }
'''

PACE_NEW = '''    // AP_AUDIO_PACE: one update at a time, and only one that changes the effects waits for a render.
    std::lock_guard order(g_update_order);
    const bool need = update_needs_render(c);
    {
        std::unique_lock pk(g_pace_lock);
        static uint64_t waited = 0, updates = 0, needed = 0, waits = 0;
        const bool none = g_renders == g_renders_at_update;
        needed += need, waits += need && none;
        if (++updates % 6000 == 0)
            log("audio: pacing: of the last 6000 updates %llu changed the effects (or were not understood), %llu of "
                "them waited for a render", (unsigned long long)needed, (unsigned long long)waits), needed = waits = 0;
        if (need && none && waited++ < 5) log("audio: update waits for a render");
        // 1 s, not 50 ms: at boot, with every thread loading, a render took longer and the second update went through
        // (the limiter crash again, once in ~25 Windows runs). The render thread runs every 5 ms: 1 s only bounds a stall.
        if (need && !g_pace_cv.wait_for(pk, std::chrono::seconds(1), [] { return g_renders != g_renders_at_update; }))
            log("audio: no render for 1 s since the last update: updating anyway");
    }
'''

EDITS = [
    (AUDIO,
     "uint64_t g_renders = 0, g_renders_at_update = ~0ull;\n",
     "uint64_t g_renders = 0, g_renders_at_update = ~0ull;\n\n" + HELPERS),
    (AUDIO, PACE_OLD, PACE_NEW),
    (RECO,
     """    @('both', 'framework\\runtime\\src\\hle_audio.cpp', 'OpenDefaultAudioOutNoEvent', 'apply_audio_out.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\hle_audio.cpp', 'OpenDefaultAudioOutNoEvent', 'apply_audio_out.py', $false),
    @('both', 'framework\\runtime\\src\\hle_audio.cpp', 'AP_AUDIO_PACE', 'apply_audio_pace.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)