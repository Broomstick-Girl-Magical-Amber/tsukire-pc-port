"""apply_movie_hw_solo.py - only one MediaCodec video decoder alive at a time (AP_MOVIE_HW_SOLO).
Apply apply_movie_hw.py and apply_movie_hw_wait.py first (this builds on their text).

Why: with AP_MOVIE_HW + AP_MOVIE_HW_WAIT the prologue decoded at full speed once it started (~30 packets in and ~30
pictures out per second), but its FIRST picture came 5.3 s after the decoder was opened, from a single packet. During
those seconds the game gave up waiting, restarted its audio, and from then on video and audio clocks never lined up
again (most frames dropped as late). The title videos (TM_CI, titlebg) gave their first picture in ~0.3 s.
The difference: when the prologue opens, the title video's MediaCodec decoder is still alive, kept "parked" by
AP_TITLE_SMOOTH so the title loop can reuse it. Some phone decoders (seen on MediaTek SoCs) only run one 1080p
hardware instance at full speed; a second one waits for resources. The log also shows the parked hardware decoder was
not reused by the title loop anyway (it opened a new MediaCodec decoder each time), so parking it gains nothing.

What it does (games/tsukihime_common/movie_core.cpp, only for decoders running on MediaCodec):
  * close(): a MediaCodec video decoder is freed instead of parked (the software decoder still parks, as before).
  * open(): before opening a MediaCodec decoder, a parked MediaCodec decoder (if any) is freed first.
  * With NX_MOVIE_TIMING=1, one line with how long opening the MediaCodec decoder took, and one when a parked
    hardware decoder is freed.
  * NX_MOVIE_HW_PARK=1: park MediaCodec decoders as before (to compare).
Plus one $Markers row in ../tools/recomp_tsukihime.ps1 (after the row of AP_MOVIE_HW_WAIT).

Idempotent (marker AP_MOVIE_HW_SOLO), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_MOVIE_HW_SOLO"
C = "games/tsukihime_common/movie_core.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# 1. NX_MOVIE_HW_PARK
A1 = "static bool is_hw_ctx(const AVCodecContext* c) {\n"
N1 = ("// AP_MOVIE_HW_SOLO: NX_MOVIE_HW_PARK=1 keeps parking MediaCodec decoders (default: freed, one alive at a time).\n"
      "static bool movie_hw_park() {\n"
      "    static const bool on = [] {\n"
      "        const char* e = std::getenv(\"NX_MOVIE_HW_PARK\");\n"
      "        bool v = e && std::strcmp(e, \"0\") != 0;\n"
      "        if (v) nx::log(\"movie: NX_MOVIE_HW_PARK=1 (MediaCodec decoders are parked)\");\n"
      "        return v;\n"
      "    }();\n"
      "    return on;\n"
      "}\n"
      + A1)

# 2. close(): do not park a MediaCodec decoder
A2 = "        if (ctx && is_hw_ctx(ctx) && g_hw_bad) avcodec_free_context(&ctx);  // AP_MOVIE_HW: not kept\n"
N2 = (A2 +
      "        if (ctx && is_hw_ctx(ctx) && !movie_hw_park()) avcodec_free_context(&ctx);  // AP_MOVIE_HW_SOLO: not parked\n")

# 3. open(): free a parked MediaCodec decoder before opening a new one, and time the open
A3 = ("        if (kind == DecoderKind::Video && par->codec_id == AV_CODEC_ID_H264 && movie_hw_wanted() && !g_hw_bad) {  // AP_MOVIE_HW\n"
      "            ctx = open_hw_h264(par);\n")
N3 = ("        if (kind == DecoderKind::Video && par->codec_id == AV_CODEC_ID_H264 && movie_hw_wanted() && !g_hw_bad) {  // AP_MOVIE_HW\n"
      "            {  // AP_MOVIE_HW_SOLO: one MediaCodec video decoder at a time\n"
      "                std::lock_guard<std::mutex> lk(g_park_m);\n"
      "                if (g_parked && is_hw_ctx(g_parked)) {\n"
      "                    avcodec_free_context(&g_parked);\n"
      "                    if (movie_hw_stats()) nx::log(\"movie/hw: freed the parked MediaCodec decoder before opening a new one\");\n"
      "                }\n"
      "            }\n"
      "            const uint64_t hw_t0 = now_ns();  // AP_MOVIE_HW_SOLO\n"
      "            ctx = open_hw_h264(par);\n"
      "            if (movie_hw_stats()) nx::log(\"movie/hw: opening MediaCodec took %.0f ms\", (now_ns() - hw_t0) / 1e6);  // AP_MOVIE_HW_SOLO\n")

# 4. $Markers row
A4 = "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW_WAIT', 'apply_movie_hw_wait.py', $false),\n"
N4 = (A4 + "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW_SOLO', 'apply_movie_hw_solo.py', $false),\n")

EDITS = [(C, A1, N1), (C, A2, N2), (C, A3, N3), (RECO, A4, N4)]

run(MARK, EDITS, __doc__)
