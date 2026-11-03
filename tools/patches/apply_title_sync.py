"""apply_title_sync.py - the F5 / F6 video change shows right after the fade, in step with the music (AP_TITLE_SYNC).
Apply apply_title_fade.py first (with the title_bg.cpp that goes with this patch).

The recording of the fade showed the new video appearing about 2.3 s AFTER the fade in, while the music was already the new
one. Cause: the host decoder keeps up to 64 compressed packets (pending_limit, ~2 s of video) in front of the decoder, and the
game shows each frame at its time: the new video, queued behind them and with times that continue from the extractor's
position (2 s ahead of what is on screen), waited its turn. Now, at the swap:
  - the packets of the old video that are still waiting in the video decoder are dropped (Decoder::drop_pending, with the
    decoder's internal frames flushed);
  - the new video's times continue from the frame the game took last (Decoder::last_output_us), not from the extractor's
    position (Extractor::replace_memory(data, base_us));
so the first new frames are only the few already decoded (about 0.4 s) away. title.ini  swap_delay = <ms>  (default 450)
is how long the fade in waits after the swap for them; raise it if you still see the old video after the fade in.
It also adds nothing else to the runtime: movie_core.h/.cpp, hle_movie.cpp, a row per file in $Markers of
../tools/recomp_tsukihime.ps1.

Idempotent (marker AP_TITLE_SYNC), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_TITLE_SYNC"
H = "games/tsukihime_common/movie_core.h"
C = "games/tsukihime_common/movie_core.cpp"
M = "games/tsukihime_common/hle_movie.cpp"

H1_A = "    Status replace_memory(std::string data);\n"
H1_N = "    Status replace_memory(std::string data, int64_t base_us = -1);  // AP_TITLE_SYNC: base_us = time the new video goes on from\n"
H2_A = "    bool has_free_input() const;\n"
H2_N = (H2_A + "    // AP_TITLE_SYNC (apply_title_sync.py): at a video swap the packets waiting for the decoder are dropped\n"
        "    void drop_pending();\n"
        "    bool is_video() const;\n"
        "    int64_t last_output_us() const;  // time of the last frame the game took (-1 = none)\n")

C1_A = "Status Extractor::replace_memory(std::string data) {\n"
C1_N = "Status Extractor::replace_memory(std::string data, int64_t base_us) {\n"
C2_A = "    const int64_t before = p_->last_us;\n"
C2_N = "    const int64_t before = base_us >= 0 ? base_us : p_->last_us;  // AP_TITLE_SYNC\n"
C3_A = "    bool started = false, eos_in = false, drain_sent = false, eos_out = false;\n"
C3_N = C3_A + "    int64_t last_out_us = -1;  // AP_TITLE_SYNC: time of the last output the game acquired\n"
C4_A = "    *time_us = p_->out[o].time_us;\n    *flags = p_->out[o].flags;\n    return kOk;\n"
C4_N = "    *time_us = p_->out[o].time_us;\n    *flags = p_->out[o].flags;\n    if (p_->kind == DecoderKind::Video) p_->last_out_us = p_->out[o].time_us;  // AP_TITLE_SYNC\n    return kOk;\n"
C5_A = "bool Decoder::has_free_input() const {\n"
C5_N = ("// AP_TITLE_SYNC (apply_title_sync.py)\n"
        "void Decoder::drop_pending() {\n"
        "    std::lock_guard<std::recursive_mutex> lk(p_->m);\n"
        "    for (auto* q : p_->pending) av_packet_free(&q);\n"
        "    p_->pending.clear();\n"
        "    if (p_->ctx) avcodec_flush_buffers(p_->ctx);\n"
        "    p_->drain_sent = false;\n"
        "}\n"
        "bool Decoder::is_video() const { return p_->kind == DecoderKind::Video; }\n"
        "int64_t Decoder::last_output_us() const {\n"
        "    std::lock_guard<std::recursive_mutex> lk(p_->m);\n"
        "    return p_->last_out_us;\n"
        "}\n\n" + C5_A)

M1_A = "void wake_track_input(nx::CPU& c, Extractor* e);\n"
M1_N = (M1_A + "// AP_TITLE_SYNC (apply_title_sync.py): the video decoders at a swap of the title video.\n"
        "void title_video_clock(int64_t* base_us);\nvoid title_video_drop();\n")
M2_A = "            const Status r = ext(c.x[0])->replace_memory(std::move(swap_data));\n"
M2_N = ("            int64_t base = -1;  // AP_TITLE_SYNC: go on from the frame on screen, not from what is queued\n"
        "            title_video_clock(&base);\n"
        "            const Status r = ext(c.x[0])->replace_memory(std::move(swap_data), base);\n"
        "            if (r == kOk) title_video_drop();\n")
M3_A = "std::vector<DecoderSlot*> g_slots;\n"
M3_N = (M3_A + "\n// AP_TITLE_SYNC\n"
        "void title_video_clock(int64_t* base_us) {\n"
        "    std::lock_guard<std::mutex> lk(g_slots_m);\n"
        "    for (DecoderSlot* sl : g_slots)\n"
        "        if (sl->d->is_video()) *base_us = std::max(*base_us, sl->d->last_output_us());\n"
        "}\n"
        "void title_video_drop() {\n"
        "    std::lock_guard<std::mutex> lk(g_slots_m);\n"
        "    for (DecoderSlot* sl : g_slots)\n"
        "        if (sl->d->is_video()) sl->d->drop_pending();\n"
        "}\n")

MARK_A = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_FADE', "
          "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_N = MARK_A + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_SYNC', 'apply_title_sync.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_TITLE_SYNC', 'apply_title_sync.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_SYNC', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    (H, H1_A, H1_N), (H, H2_A, H2_N),
    (C, C1_A, C1_N), (C, C2_A, C2_N), (C, C3_A, C3_N), (C, C4_A, C4_N), (C, C5_A, C5_N),
    (M, M1_A, M1_N), (M, M2_A, M2_N), (M, M3_A, M3_N),
    ("../tools/recomp_tsukihime.ps1", MARK_A, MARK_N),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
