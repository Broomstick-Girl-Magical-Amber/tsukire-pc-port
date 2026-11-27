"""apply_movie_hw_wait.py - the MediaCodec video decoder waits a moment for its picture inside the game's call (AP_MOVIE_HW_WAIT).
Apply apply_movie_hw.py first (this builds on its text).
v2: if the first version of this file was applied, undo it with THAT file first ("python apply_movie_hw_wait.py --remove"
with the old file), then put this one in its place and apply it.

Why: with AP_MOVIE_HW the phone's hardware decoded the prologue fast (2-6 ms a frame), but the picture still stopped for
~0.3-0.5 s at the start of every second of video, and the first frame of a video took seconds. MediaCodec finishes a
picture a few ms AFTER it took the packet, outside any call of the game, and the game only learns that a frame is ready
from the events signalled inside its own calls (Send/Acquire/Release). The software decoder decoded inside those calls,
so the frame was always there; with MediaCodec the frame came out a moment later and nobody told the game, which kept
waiting until some other call happened (at the next block of the file, or a tap).

What it does (games/tsukihime_common/movie_core.cpp, only for a decoder that runs on MediaCodec):
  * produce(): when MediaCodec holds more packets than its own latency (the packets it keeps before giving a picture,
    learned from its first picture) and there is a free output slot, it waits for the picture right there, 1 ms at a time,
    up to NX_MOVIE_HW_WAIT_MS (default 40) per call, as the software decoder used to take that time decoding. Before the
    first picture it waits too, but after 3 waits that gave up it stops until the first picture (it needs input). When MediaCodec only holds its latency, it does not wait (it needs more input, and
    waiting there would slow down the thread that feeds the decoders; v1 did that and the audio fell behind).
  * With NX_MOVIE_TIMING=1, a line per second per MediaCodec decoder ("movie/hw: ...": packets in, pictures out, waits,
    in flight, latency) and one when the first picture comes out (after how many packets and ms).
  * The hardware decoder is opened with AV_CODEC_FLAG_LOW_DELAY (FFmpeg passes "low-latency" to MediaCodec where it is
    supported: a picture per packet, without holding several).
  * NX_MOVIE_HW_WAIT_MS=<0..200>: 0 = never wait (as apply_movie_hw.py alone).
  * The software decoder is not touched.
Plus one $Markers row in ../tools/recomp_tsukihime.ps1 (after the row of AP_MOVIE_HW).

Idempotent (marker AP_MOVIE_HW_WAIT), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_MOVIE_HW_WAIT"
C = "games/tsukihime_common/movie_core.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# 1. include <thread> (sleep_for)
A1 = "#include <atomic>  // AP_MOVIE_HW\n"
N1 = (A1 + "#include <thread>  // AP_MOVIE_HW_WAIT\n")

# 2. NX_MOVIE_HW_WAIT_MS
A2 = "static bool is_hw_ctx(const AVCodecContext* c) {\n"
N2 = ("// AP_MOVIE_HW_WAIT: how long one call may wait for a MediaCodec picture (NX_MOVIE_HW_WAIT_MS, 0..200, default 40).\n"
      "static int movie_hw_wait_ms() {\n"
      "    static const int ms = [] {\n"
      "        const char* e = std::getenv(\"NX_MOVIE_HW_WAIT_MS\");\n"
      "        long v = e && *e ? std::strtol(e, nullptr, 10) : 40;\n"
      "        if (v < 0 || v > 200) v = 40;\n"
      "        if (e && *e) nx::log(\"movie: NX_MOVIE_HW_WAIT_MS = %ld\", v);\n"
      "        return int(v);\n"
      "    }();\n"
      "    return ms;\n"
      "}\n"
      "static bool movie_hw_stats() {  // AP_MOVIE_HW_WAIT: NX_MOVIE_TIMING=1 also logs MediaCodec once a second\n"
      "    static const bool on = [] {\n"
      "        const char* e = std::getenv(\"NX_MOVIE_TIMING\");\n"
      "        return e && std::strcmp(e, \"0\") != 0;\n"
      "    }();\n"
      "    return on;\n"
      "}\n"
      + A2)

# 3. low delay
A3 = "    c->pkt_timebase = kMicros;\n    AVDictionary* opts = nullptr;\n"
N3 = ("    c->pkt_timebase = kMicros;\n"
      "    c->flags |= AV_CODEC_FLAG_LOW_DELAY;  // AP_MOVIE_HW_WAIT: \"low-latency\" where MediaCodec has it\n"
      "    AVDictionary* opts = nullptr;\n")

# 4. members: pictures in flight, and the wait
A4 = "    std::deque<AVPacket*> hw_sent;  // AP_MOVIE_HW: packets it took before its first picture (sent again on a fallback)\n"
N4 = (A4 +
      "    int hw_inflight = 0;   // AP_MOVIE_HW_WAIT: packets given to MediaCodec whose picture has not come out yet\n"
      "    int hw_lat = -1;       // AP_MOVIE_HW_WAIT: packets it keeps before giving a picture (-1: not known yet)\n"
      "    uint64_t hw_open_ns = 0, hw_rep_ns = 0;  // AP_MOVIE_HW_WAIT: when it was opened; last stats line\n"
      "    int hw_n_in = 0, hw_n_out = 0, hw_n_wait = 0, hw_n_gaveup = 0, hw_tot_in = 0, hw_pre_gaveup = 0;\n"
      "    uint64_t hw_wait_ns = 0;\n"
      "    // AP_MOVIE_HW_WAIT: MediaCodec finishes a picture a few ms after taking the packet, outside the game's calls, and the\n"
      "    // game is only told from inside them. If it holds more than its latency, a picture is on its way: wait for it here,\n"
      "    // 1 ms at a time, up to movie_hw_wait_ms() per call. Before the first picture the latency is unknown: wait too.\n"
      "    bool hw_should_wait() const {  // before the first picture: at most 3 waits that give up (then it needs input)\n"
      "        return hw_inflight > 0 && (hw_lat < 0 ? hw_pre_gaveup < 3 : (hw_inflight > hw_lat || drain_sent));\n"
      "    }\n"
      "    bool hw_wait(uint64_t& deadline) {\n"
      "        const uint64_t now = now_ns();\n"
      "        if (!deadline) {\n"
      "            deadline = now + uint64_t(movie_hw_wait_ms()) * 1000000ull;\n"
      "            hw_n_wait++;\n"
      "        }\n"
      "        if (now >= deadline) {\n"
      "            hw_n_gaveup++;\n"
      "            if (hw_lat < 0) hw_pre_gaveup++;\n"
      "            if (hw_lat >= 0) hw_inflight = hw_lat;  // what is left is its latency: do not wait for it again\n"
      "            return false;\n"
      "        }\n"
      "        std::this_thread::sleep_for(std::chrono::milliseconds(1));\n"
      "        hw_wait_ns += now_ns() - now;\n"
      "        return true;\n"
      "    }\n"
      "    void hw_report() {  // NX_MOVIE_TIMING: once a second\n"
      "        if (!movie_hw_stats()) return;\n"
      "        const uint64_t now = now_ns();\n"
      "        if (!hw_rep_ns) hw_rep_ns = now;\n"
      "        if (now - hw_rep_ns < 1000000000ull) return;\n"
      "        nx::log(\"movie/hw: %.1f s: %d packets in, %d pictures out, %d waits (%.0f ms, %d gave up), in flight %d, latency %d, pending %zu\",\n"
      "                double(now - hw_rep_ns) / 1e9, hw_n_in, hw_n_out, hw_n_wait, double(hw_wait_ns) / 1e6, hw_n_gaveup,\n"
      "                hw_inflight, hw_lat, pending.size());\n"
      "        hw_rep_ns = now;\n"
      "        hw_n_in = hw_n_out = hw_n_wait = hw_n_gaveup = 0;\n"
      "        hw_wait_ns = 0;\n"
      "    }\n")

# 5. resets: clear_queues, open
A5 = "        hw_forget();  // AP_MOVIE_HW\n        eos_in = drain_sent = eos_out = false;\n"
N5 = ("        hw_forget();  // AP_MOVIE_HW\n"
      "        hw_inflight = 0;  // AP_MOVIE_HW_WAIT\n"
      "        eos_in = drain_sent = eos_out = false;\n")
A6 = "        hw = hw_out = false;  // AP_MOVIE_HW\n"
N6 = ("        hw = hw_out = false;  // AP_MOVIE_HW\n"
      "        hw_inflight = 0, hw_lat = -1, hw_tot_in = 0, hw_pre_gaveup = 0, hw_open_ns = now_ns(), hw_rep_ns = 0;  // AP_MOVIE_HW_WAIT\n")

# 7. produce(): deadline per call
A7 = "        if (!ctx || !started) return kInvalidState;\n        while (ready.empty()) {\n"
N7 = ("        if (!ctx || !started) return kInvalidState;\n"
      "        uint64_t hw_deadline = 0;  // AP_MOVIE_HW_WAIT: one wait per call\n"
      "        if (hw) hw_report();\n"
      "        while (ready.empty()) {\n")

# 8. a picture came out
A8 = "                if (hw && !hw_out) {  // AP_MOVIE_HW: it works, nothing to send again any more\n"
N8 = ("                if (hw) {  // AP_MOVIE_HW_WAIT\n"
      "                    if (hw_lat < 0) {\n"
      "                        hw_lat = hw_inflight > 0 ? hw_inflight - 1 : 0;\n"
      "                        nx::log(\"movie/hw: first picture after %d packets, %.0f ms after opening; latency %d\", hw_tot_in,\n"
      "                                double(now_ns() - hw_open_ns) / 1e6, hw_lat);\n"
      "                    }\n"
      "                    if (hw_inflight > 0) hw_inflight--;\n"
      "                    hw_n_out++;\n"
      "                }\n"
      + A8)

# 9. input full: wait for a picture
A9 = "                        if (hw) return kTryAgain;  // AP_MOVIE_HW: full and no picture yet: the game asks again\n"
N9 = ("                        if (hw) {  // AP_MOVIE_HW: full and no picture yet\n"
      "                            if (hw_should_wait() && hw_wait(hw_deadline)) continue;  // AP_MOVIE_HW_WAIT\n"
      "                            return kTryAgain;  // the game asks again\n"
      "                        }\n")

# 10. a packet went in
A10 = "                    if (hw && !hw_out && sr >= 0) {  // AP_MOVIE_HW: kept until its first picture\n"
N10 = ("                    if (hw && sr >= 0) hw_inflight++, hw_n_in++, hw_tot_in++;  // AP_MOVIE_HW_WAIT\n"
       + A10)

# 11. no more input here: wait for what is in flight
A11 = ("                    drain_sent = true;\n"
       "                    continue;\n"
       "                }\n"
       "                return kTryAgain;\n")
N11 = ("                    drain_sent = true;\n"
       "                    continue;\n"
       "                }\n"
       "                if (hw && hw_should_wait() && hw_wait(hw_deadline)) continue;  // AP_MOVIE_HW_WAIT\n"
       "                return kTryAgain;\n")

# 12. drop_pending (AP_TITLE_SYNC)
A12 = "    p_->hw_forget();  // AP_MOVIE_HW\n}\n"
N12 = ("    p_->hw_forget();  // AP_MOVIE_HW\n"
       "    p_->hw_inflight = 0;  // AP_MOVIE_HW_WAIT\n"
       "}\n")

# 13. $Markers row
A13 = "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW', 'apply_movie_hw.py', $false),\n"
N13 = (A13 + "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW_WAIT', 'apply_movie_hw_wait.py', $false),\n")

EDITS = [
    (C, A1, N1), (C, A2, N2), (C, A3, N3), (C, A4, N4), (C, A5, N5), (C, A6, N6), (C, A7, N7),
    (C, A8, N8), (C, A9, N9), (C, A10, N10), (C, A11, N11), (C, A12, N12),
    (RECO, A13, N13),
]

run(MARK, EDITS, __doc__)
