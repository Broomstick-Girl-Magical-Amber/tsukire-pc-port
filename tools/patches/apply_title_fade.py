"""apply_title_fade.py - F5 / F6 on the title screen: fade out, the other video (and music) at black, fade in (AP_TITLE_FADE).
Apply apply_title_live.py and apply_title_live_opus.py first (and the title_bg.cpp that goes with this patch).
Do not undo them afterwards without undoing this one first.

What it does, with title_bg.cpp deciding the timing (title.ini: fade = <milliseconds>, default 500, 0 = no fade):
  1. F5 / F6: the video and, if the music is going to change, the music fade out to black / silence;
  2. at black the other video is swapped in AT ONCE (not at the loop any more) and the music of that video starts from
     its beginning, so both start together;
  3. after a short moment for the first frames to reach the screen, both fade in.
Where it is wired:
  - games/tsukihime_common/hle_movie.cpp: Extractor::GetTrackIndexForAvailableData (called for every sample) asks
    tsuki_title::take_swap and swaps the file; write_frame_to_texture scales the colours of each decoded frame by
    tsuki_title::video_gain() (1 = untouched, so a video that is not fading costs nothing);
  - games/tsukihime_common/movie_core.cpp/.h: after a swap the new video's times continue from where the old one was, as
    one timeline, so the game does not see time jumping back (a seek, like the loop of the game, starts it from 0 again);
  - games/tsukihime_common/hle_codec.cpp: the decoded sound of a title track is scaled by the fade gain (only the tracks
    that change: videos 1-3 share a track, so between them the music does not fade);
  - a row in $Markers of ../tools/recomp_tsukihime.ps1 for each of the four code files and title_bg.cpp.
All of it under NX_TITLE_LIVE where it touches the runtime (Wonder unchanged).

Idempotent (marker AP_TITLE_FADE), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_TITLE_FADE"

# ---- movie_core.cpp: one timeline after a swap
OFF_FIELD_A = "    bool have = false, eof = false;\n"
OFF_FIELD_N = OFF_FIELD_A + "    int64_t off_us = 0, last_us = 0;  // AP_TITLE_FADE: time added after replace_memory; last time given to the game\n"

TOUS_A = "        return av_rescale_q(ts, fmt->streams[pkt->stream_index]->time_base, kMicros);\n"
TOUS_N = "        return av_rescale_q(ts, fmt->streams[pkt->stream_index]->time_base, kMicros) + off_us;\n"

ST_A = "    *time_us = p_->to_us(p_->cur);\n    return kOk;\n"
ST_N = "    *time_us = p_->to_us(p_->cur);\n    p_->last_us = *time_us;  // AP_TITLE_FADE\n    return kOk;\n"

SEEK_A = ("    if (av_seek_frame(p_->fmt, -1, av_rescale_q(time_us, kMicros, AV_TIME_BASE_Q), AVSEEK_FLAG_BACKWARD) < 0)\n"
          "        return kIoError;\n")
SEEK_N = SEEK_A + "    p_->off_us = 0;  // AP_TITLE_FADE: a seek is an absolute time again\n"

REPL_A = ("    av_packet_unref(p_->raw);\n"
          "    p_->have = false;\n"
          "    p_->eof = false;\n"
          "    return kOk;\n"
          "}\n")
REPL_N = ("    av_packet_unref(p_->raw);\n"
          "    p_->have = false;\n"
          "    p_->eof = false;\n"
          "    // AP_TITLE_FADE: the new video goes on from the time the old one had reached (30 fps)\n"
          "    const int64_t before = p_->last_us;\n"
          "    p_->off_us = 0;\n"
          "    p_->fill();\n"
          "    if (p_->have) p_->off_us = before + 33333 - p_->to_us(p_->cur);\n"
          "    return kOk;\n"
          "}\n")

# ---- hle_movie.cpp
DECL_A = "bool take_swap(const void* key, std::string* rel, std::string* data);\n}  // namespace tsuki_title\n"
DECL_N = ("bool take_swap(const void* key, std::string* rel, std::string* data);\n"
          "// AP_TITLE_FADE (apply_title_fade.py): 1 = the frame as it is, 0 = black.\n"
          "float video_gain();\n}  // namespace tsuki_title\n")

TRK_A = ("void Extractor_GetTrackIndexForAvailableData(nx::CPU& c) {\n"
         "    ret(c, ext(c.x[0])->track_index_for_available_data(reinterpret_cast<uint64_t*>(c.x[1])));\n"
         "}\n")
TRK_N = ("void Extractor_GetTrackIndexForAvailableData(nx::CPU& c) {\n"
         "    {  // AP_TITLE_FADE: at black the video chosen with F5 / F6 takes the place of the one that is playing\n"
         "        std::string swap_rel, swap_data;\n"
         "        if (tsuki_title::take_swap(ext(c.x[0]), &swap_rel, &swap_data)) {\n"
         "            const Status r = ext(c.x[0])->replace_memory(std::move(swap_data));\n"
         "            if (r == kOk) nx::log(\"title: video changed to %s\", swap_rel.c_str());\n"
         "            else nx::log(\"title: %s could not replace the video that is playing (status %d): it stays\", swap_rel.c_str(), int(r));\n"
         "        }\n"
         "    }\n"
         "    ret(c, ext(c.x[0])->track_index_for_available_data(reinterpret_cast<uint64_t*>(c.x[1])));\n"
         "}\n")

FRAME_A = "    for (uint32_t y = 0; y < ch; y++) std::memcpy(tw.data.data() + size_t(y) * cw * 4, rgba + size_t(y) * w * 4, cw * 4);\n"
FRAME_N = (FRAME_A +
           "#ifdef NX_TITLE_LIVE\n"
           "    if (const float g = tsuki_title::video_gain(); g < 0.999f) {  // AP_TITLE_FADE: towards black\n"
           "        const uint32_t k = uint32_t(g > 0 ? g * 256.0f : 0.0f);\n"
           "        uint8_t* px = tw.data.data();\n"
           "        for (size_t i = 0, n = size_t(cw) * ch; i < n; i++, px += 4)\n"
           "            px[0] = uint8_t(px[0] * k >> 8), px[1] = uint8_t(px[1] * k >> 8), px[2] = uint8_t(px[2] * k >> 8);\n"
           "    }\n"
           "#endif\n")

# ---- hle_codec.cpp
CODEC_DECL_A = "bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out);\n"
CODEC_DECL_N = CODEC_DECL_A + "bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out, float* gain);  // AP_TITLE_FADE\n"

CODEC_CALL_A = ("    std::vector<uint8_t> chosen;\n"
                "    if (tsuki_title::music_packet(in, len, &chosen)) pkt = chosen.data(), pkt_len = uint32_t(chosen.size());\n")
CODEC_CALL_N = ("    std::vector<uint8_t> chosen;\n"
                "    float title_gain = 1.0f;  // AP_TITLE_FADE: the fade of a title track that changes\n"
                "    if (tsuki_title::music_packet(in, len, &chosen, &title_gain)) pkt = chosen.data(), pkt_len = uint32_t(chosen.size());\n")

CODEC_GAIN_A = "    if (n < 0) return fail(kOpusBrokenData, opus_strerror(n));\n"
CODEC_GAIN_N = (CODEC_GAIN_A +
                "#ifdef NX_TITLE_LIVE\n"
                "    if (title_gain < 0.999f)  // AP_TITLE_FADE\n"
                "        for (int i = 0, m = n * d->channels; i < m; i++) out[i] = int16_t(out[i] * title_gain);\n"
                "#endif\n")

MARK_A = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE_OPUS', "
          "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_N = MARK_A + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_codec.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_FADE', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

CORE = "games/tsukihime_common/movie_core.cpp"
MOV = "games/tsukihime_common/hle_movie.cpp"
COD = "games/tsukihime_common/hle_codec.cpp"
EDITS = [
    (CORE, OFF_FIELD_A, OFF_FIELD_N),
    (CORE, TOUS_A, TOUS_N),
    (CORE, ST_A, ST_N),
    (CORE, SEEK_A, SEEK_N),
    (CORE, REPL_A, REPL_N),
    (MOV, DECL_A, DECL_N),
    (MOV, TRK_A, TRK_N),
    (MOV, FRAME_A, FRAME_N),
    (COD, CODEC_DECL_A, CODEC_DECL_N),
    (COD, CODEC_CALL_A, CODEC_CALL_N),
    (COD, CODEC_GAIN_A, CODEC_GAIN_N),
    ("../tools/recomp_tsukihime.ps1", MARK_A, MARK_N),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
