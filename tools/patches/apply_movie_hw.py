"""apply_movie_hw.py - H.264 videos decoded by the phone's hardware (MediaCodec) on Android (AP_MOVIE_HW).
Apply apply_movie_stream.py first. Do NOT use apply_movie_threads.py (if it was applied, undo it first with
"python apply_movie_threads.py --remove"): FFmpeg threads made things worse on phones with slow little cores.

Why: the game's videos are 1080p / 30 fps H.264. FFmpeg decoded them in software on one thread; on phones whose cores are
slow (2 big + 6 little cores, e.g. Helio G200 / Dimensity 7020) a frame of the prologue went past its 33 ms from ~10-20 s
in, the game dropped the late frames and the picture (and the text tied to it) crawled while the audio went on. Every
Android phone has a hardware H.264 decoder, so this is not tied to any model: it is used on every Android device.

What it does (games/tsukihime_common/movie_core.cpp, Decoder::Impl):
  * open(): a VIDEO decoder for H.264 on Android first tries FFmpeg's "h264_mediacodec" (NDK MediaCodec, option
    ndk_codec=1, no Java needed). It gives normal frames in memory (NV12 or the like); the existing conversion to the
    game's RGBA / NV12 output is unchanged. If it does not exist or does not open: the software decoder, as before.
  * Safety net while it runs: if the hardware decoder fails (an error, frames that cannot be converted, or no picture
    after 120 packets), the decoder switches to software on the spot (the packets it had taken before its first picture
    are sent again, so nothing is lost at the start) and hardware is not tried again until the game restarts.
  * produce(): with the hardware decoder, when it can take no more input and has no frame yet, it returns "try again"
    instead of spinning while holding the lock (the game asks again on its next poll).
  * The flushed decoder that AP_TITLE_SMOOTH keeps for the title video can be the hardware one; it is not kept if
    hardware was turned off by a failure.
  * nx.log says which decoder each video got: "movie: video 1920x1080 decoded by MediaCodec (hardware)", or why not.
  * NX_MOVIE_HW=0 (config.txt or environment): software only, as before. Windows and Linux: unchanged (software).
  * Audio decoders are not touched.
Plus one $Markers row in ../tools/recomp_tsukihime.ps1 (anchored after the last row of AP_MOVIE_STREAM).

Idempotent (marker AP_MOVIE_HW), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_MOVIE_HW"
C = "games/tsukihime_common/movie_core.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# 1. includes
A1 = "#include <mutex>\n"
N1 = ("#include <mutex>\n"
      "#include <atomic>  // AP_MOVIE_HW\n"
      "\n"
      "#include <nx/runtime.h>  // AP_MOVIE_HW: nx::log\n")

# 2. helpers, before Decoder::Impl
A2 = "struct Decoder::Impl {\n"
N2 = ("// AP_MOVIE_HW (apply_movie_hw.py): H.264 video through the device's hardware decoder (FFmpeg h264_mediacodec, NDK).\n"
      "// Android: on unless NX_MOVIE_HW=0. Other systems: off (there is no MediaCodec).\n"
      "static std::atomic<bool> g_hw_bad{false};  // a hardware decoder failed: software for the rest of the session\n"
      "static bool movie_hw_wanted() {\n"
      "    static const bool on = [] {\n"
      "#ifdef __ANDROID__\n"
      "        const char* e = std::getenv(\"NX_MOVIE_HW\");\n"
      "        const bool v = !(e && std::strcmp(e, \"0\") == 0);\n"
      "        if (!v) nx::log(\"movie: NX_MOVIE_HW=0: videos decoded in software\");\n"
      "        return v;\n"
      "#else\n"
      "        return false;\n"
      "#endif\n"
      "    }();\n"
      "    return on;\n"
      "}\n"
      "static bool is_hw_ctx(const AVCodecContext* c) {\n"
      "    return c && c->codec && c->codec->name && std::strcmp(c->codec->name, \"h264_mediacodec\") == 0;\n"
      "}\n"
      "static AVCodecContext* open_hw_h264(const AVCodecParameters* par) {\n"
      "    const AVCodec* hc = avcodec_find_decoder_by_name(\"h264_mediacodec\");\n"
      "    if (!hc) {\n"
      "        static std::atomic<bool> said{false};\n"
      "        if (!said.exchange(true)) nx::log(\"movie: this FFmpeg has no h264_mediacodec: videos decoded in software\");\n"
      "        return nullptr;\n"
      "    }\n"
      "    AVCodecContext* c = avcodec_alloc_context3(hc);\n"
      "    if (!c) return nullptr;\n"
      "    if (avcodec_parameters_to_context(c, par) < 0) {\n"
      "        avcodec_free_context(&c);\n"
      "        return nullptr;\n"
      "    }\n"
      "    c->pkt_timebase = kMicros;\n"
      "    AVDictionary* opts = nullptr;\n"
      "    av_dict_set(&opts, \"ndk_codec\", \"1\", 0);  // NDK AMediaCodec, no Java VM needed\n"
      "    const int r = avcodec_open2(c, hc, &opts);\n"
      "    av_dict_free(&opts);\n"
      "    if (r < 0) {\n"
      "        char e[96];\n"
      "        av_strerror(r, e, sizeof e);\n"
      "        nx::log(\"movie: MediaCodec did not open for %dx%d H.264 (%s): software decoder\", par->width, par->height, e);\n"
      "        avcodec_free_context(&c);\n"
      "        return nullptr;\n"
      "    }\n"
      "    nx::log(\"movie: video %dx%d decoded by MediaCodec (hardware); NX_MOVIE_HW=0 for software\", par->width, par->height);\n"
      "    return c;\n"
      "}\n"
      "\n"
      + A2)

# 3. members
A3 = "    uint64_t work_ns = 0;  // tiempo en avcodec_send_packet / receive_frame desde la ultima salida guardada\n"
N3 = (A3 +
      "    bool hw = false;                // AP_MOVIE_HW: ctx is the hardware decoder\n"
      "    bool hw_out = false;            // AP_MOVIE_HW: it already gave a picture\n"
      "    std::deque<AVPacket*> hw_sent;  // AP_MOVIE_HW: packets it took before its first picture (sent again on a fallback)\n"
      "    void hw_forget() {\n"
      "        for (auto* q : hw_sent) av_packet_free(&q);\n"
      "        hw_sent.clear();\n"
      "    }\n")

# 4. close(): do not park a hardware context once hardware is off; forget its packets
A4 = "        if (ctx && kind == DecoderKind::Video && ctx->codec_id == AV_CODEC_ID_H264 && avcodec_is_open(ctx)) {  // AP_TITLE_SMOOTH\n"
N4 = ("        hw_forget();  // AP_MOVIE_HW\n"
      "        if (ctx && is_hw_ctx(ctx) && g_hw_bad) avcodec_free_context(&ctx);  // AP_MOVIE_HW: not kept\n"
      + A4)

# 5. clear_queues(): the kept packets are stale after a flush
A5 = "        eos_in = drain_sent = eos_out = false;\n"
N5 = ("        hw_forget();  // AP_MOVIE_HW\n"
      + A5)

# 6. open(): parked context, then hardware first, then software
A6 = ("        if (ctx) {\n"
      "            ctx->pkt_timebase = kMicros;\n"
      "            started = true;\n"
      "            return kOk;\n"
      "        }\n"
      "        ctx = avcodec_alloc_context3(codec);\n"
      "        if (avcodec_parameters_to_context(ctx, par) < 0) return kInvalidArgument;\n"
      "        ctx->pkt_timebase = kMicros;\n"
      "        if (avcodec_open2(ctx, codec, nullptr) < 0) return kUnsupported;\n"
      "        started = true;\n"
      "        return kOk;\n"
      "    }\n")
N6 = ("        hw = hw_out = false;  // AP_MOVIE_HW\n"
      "        if (ctx && is_hw_ctx(ctx) && g_hw_bad) avcodec_free_context(&ctx);  // AP_MOVIE_HW\n"
      "        if (ctx) {\n"
      "            ctx->pkt_timebase = kMicros;\n"
      "            hw = is_hw_ctx(ctx);  // AP_MOVIE_HW\n"
      "            started = true;\n"
      "            return kOk;\n"
      "        }\n"
      "        if (kind == DecoderKind::Video && par->codec_id == AV_CODEC_ID_H264 && movie_hw_wanted() && !g_hw_bad) {  // AP_MOVIE_HW\n"
      "            ctx = open_hw_h264(par);\n"
      "            if (ctx) {\n"
      "                hw = true;\n"
      "                started = true;\n"
      "                return kOk;\n"
      "            }\n"
      "        }\n"
      "        return open_sw(codec);\n"
      "    }\n"
      "    Status open_sw(const AVCodec* codec) {  // AP_MOVIE_HW: the software decoder (what open() always did)\n"
      "        ctx = avcodec_alloc_context3(codec);\n"
      "        if (avcodec_parameters_to_context(ctx, par) < 0) return kInvalidArgument;\n"
      "        ctx->pkt_timebase = kMicros;\n"
      "        if (avcodec_open2(ctx, codec, nullptr) < 0) return kUnsupported;\n"
      "        started = true;\n"
      "        return kOk;\n"
      "    }\n"
      "    // AP_MOVIE_HW: the hardware decoder failed: software from here on (and for the rest of the session). Before its\n"
      "    // first picture its packets go back to the front of the queue, so the software decoder starts from the same place.\n"
      "    bool hw_fallback(const char* why) {\n"
      "        if (!hw) return false;\n"
      "        nx::log(\"movie: MediaCodec failed (%s): software decoder from now on%s\", why,\n"
      "                hw_out ? \"\" : \", its packets are decoded again\");\n"
      "        g_hw_bad = true;\n"
      "        avcodec_free_context(&ctx);\n"
      "        hw = false;\n"
      "        drain_sent = false;\n"
      "        const AVCodec* sc = avcodec_find_decoder(par->codec_id);\n"
      "        if (!sc || open_sw(sc) != kOk) {\n"
      "            hw_forget();\n"
      "            return false;\n"
      "        }\n"
      "        if (!hw_out) {\n"
      "            while (!hw_sent.empty()) {\n"
      "                pending.push_front(hw_sent.back());\n"
      "                hw_sent.pop_back();\n"
      "            }\n"
      "        }\n"
      "        hw_forget();\n"
      "        return true;\n"
      "    }\n")

# 7. produce(): picture from hardware, conversion failure
A7 = ("            if (r == 0) {\n"
      "                Status s = store(o);\n"
      "                av_frame_unref(frame);\n"
      "                if (s != kOk) return s;\n"
      "                continue;\n"
      "            }\n")
N7 = ("            if (r == 0) {\n"
      "                Status s = store(o);\n"
      "                av_frame_unref(frame);\n"
      "                if (s != kOk) {\n"
      "                    if (hw && hw_fallback(\"its frames could not be converted\")) continue;  // AP_MOVIE_HW\n"
      "                    return s;\n"
      "                }\n"
      "                if (hw && !hw_out) {  // AP_MOVIE_HW: it works, nothing to send again any more\n"
      "                    hw_out = true;\n"
      "                    hw_forget();\n"
      "                }\n"
      "                continue;\n"
      "            }\n")

# 8. produce(): sending a packet
A8 = ("                    if (sr == AVERROR(EAGAIN)) continue;  // primero hay que sacar frames\n"
      "                    pending.pop_front();\n"
      "                    av_packet_free(&q);\n"
      "                    continue;  // si sr < 0 la muestra estaba corrupta: se descarta\n")
N8 = ("                    if (sr == AVERROR(EAGAIN)) {  // primero hay que sacar frames\n"
      "                        if (hw) return kTryAgain;  // AP_MOVIE_HW: full and no picture yet: the game asks again\n"
      "                        continue;\n"
      "                    }\n"
      "                    if (sr < 0 && sr != AVERROR_EOF && hw && hw_fallback(\"it refused a packet\")) continue;  // AP_MOVIE_HW\n"
      "                    pending.pop_front();\n"
      "                    if (hw && !hw_out && sr >= 0) {  // AP_MOVIE_HW: kept until its first picture\n"
      "                        hw_sent.push_back(q);\n"
      "                        if (hw_sent.size() >= 120 && hw_fallback(\"no picture after 120 packets\")) continue;\n"
      "                        continue;\n"
      "                    }\n"
      "                    av_packet_free(&q);\n"
      "                    continue;  // si sr < 0 la muestra estaba corrupta: se descarta\n")

# 9. produce(): any other error of receive_frame
A9 = ("            return kUnknownError;\n"
      "        }\n"
      "        return kOk;\n"
      "    }\n"
      "};\n")
N9 = ("            if (hw && hw_fallback(\"decode error\")) continue;  // AP_MOVIE_HW\n"
      + A9)

# 10. drop_pending (AP_TITLE_SYNC): flushed, so the kept packets are stale
A10 = "    p_->drain_sent = false;\n}\n"
N10 = ("    p_->drain_sent = false;\n"
       "    p_->hw_forget();  // AP_MOVIE_HW\n"
       "}\n")

# 11. $Markers row
A11 = "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_MOVIE_STREAM', 'apply_movie_stream.py', $false),\n"
N11 = (A11 + "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW', 'apply_movie_hw.py', $false),\n")

EDITS = [
    (C, A1, N1),
    (C, A2, N2),
    (C, A3, N3),
    (C, A4, N4),
    (C, A5, N5),
    (C, A6, N6),
    (C, A7, N7),
    (C, A8, N8),
    (C, A9, N9),
    (C, A10, N10),
    (RECO, A11, N11),
]

run(MARK, EDITS, __doc__)
