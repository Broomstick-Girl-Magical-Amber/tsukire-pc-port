"""apply_title_live.py - change the title screen background video while it is up, with F5 / F6 (AP_TITLE_LIVE).
Apply apply_title_bg.py, apply_title_music.py and apply_title_scope.py first (title_bg.cpp, the new version, goes with them).

On the title screen F6 shows the next video of the pool (title.ini) and F5 the previous one. The logic is in
games/tsukihime_common/title_bg.cpp (hotkey, take_swap). This patcher wires it:
  - runtime/src/platform_win.cpp, WM_KEYDOWN: F5 / F6 (no key repeat) call tsuki_title::hotkey(-1 / +1). Under NX_TITLE_LIVE
    only: without the define the compiled code is the usual one (Wonder unchanged);
  - games/tsukihime_common/movie_core.h/.cpp: Extractor::replace_memory(data): opens the new file in a temporary demuxer and,
    only if it has the same streams (same count, codec, size), swaps it in keeping the selected tracks. If anything does not
    match it returns an error and the video that was playing stays as it was;
  - games/tsukihime_common/hle_movie.cpp, Extractor::SeekTo: if the player chose a video (take_swap), swaps it in before
    seeking. The swap therefore shows when the game loops the background video, not at the key press;
  - tsukihime_common.cmake: the define NX_TITLE_LIVE;
  - a row in $Markers of ../tools/recomp_tsukihime.ps1 for each of the four code files and title_bg.cpp.
The music does not change live (a track that is playing cannot be changed): it follows the choice the next time the title opens.

Idempotent (marker AP_TITLE_LIVE), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. After: cmake --build build\\tsukihime; the same with build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_TITLE_LIVE"

# ---- movie_core.h
H_ANCHOR = "    Status open_memory(std::string data);  // toma el contenido del archivo completo\n"
H_NEW = H_ANCHOR + ("    // AP_TITLE_LIVE: cambia el archivo por otro con las mismas pistas (mismo codec y tamano), conservando las pistas\n"
                    "    // seleccionadas. Si no encaja devuelve un error y todo queda como estaba.\n"
                    "    Status replace_memory(std::string data);\n")

# ---- movie_core.cpp
C_ANCHOR = "Status Extractor::track_count(int* n) const {\n"
C_NEW = r'''// AP_TITLE_LIVE (apply_title_live.py)
Status Extractor::replace_memory(std::string data) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    Impl n;  // temporary demuxer: it is validated before touching the one that is playing
    n.data = std::move(data);
    const int kBuf = 1 << 16;
    n.io = avio_alloc_context(static_cast<uint8_t*>(av_malloc(kBuf)), kBuf, 0, &n, &Impl::read_cb, nullptr, &Impl::seek_cb);
    n.fmt = avformat_alloc_context();
    n.fmt->pb = n.io;
    if (avformat_open_input(&n.fmt, nullptr, nullptr, nullptr) < 0) return kIoError;
    if (avformat_find_stream_info(n.fmt, nullptr) < 0) return kIoError;
    if (n.fmt->nb_streams != p_->fmt->nb_streams) return kInvalidArgument;
    for (unsigned i = 0; i < n.fmt->nb_streams; i++) {
        const AVCodecParameters* a = p_->fmt->streams[i]->codecpar;
        const AVCodecParameters* b = n.fmt->streams[i]->codecpar;
        if (a->codec_type != b->codec_type || a->codec_id != b->codec_id || a->width != b->width || a->height != b->height ||
            a->sample_rate != b->sample_rate)
            return kInvalidArgument;
    }
    n.bsf.assign(n.fmt->nb_streams, nullptr);
    for (unsigned i = 0; i < n.fmt->nb_streams; i++) {
        AVStream* st = n.fmt->streams[i];
        st->discard = p_->selected[i] ? AVDISCARD_DEFAULT : AVDISCARD_ALL;
        if (st->codecpar->codec_id == AV_CODEC_ID_H264 && st->codecpar->extradata_size && st->codecpar->extradata[0] == 1) {
            const AVBitStreamFilter* f = av_bsf_get_by_name("h264_mp4toannexb");
            AVBSFContext* b = nullptr;
            if (f && av_bsf_alloc(f, &b) == 0) {
                avcodec_parameters_copy(b->par_in, st->codecpar);
                b->time_base_in = st->time_base;
                if (av_bsf_init(b) == 0) n.bsf[i] = b;
                else av_bsf_free(&b);
            }
        }
    }
    // everything fits: the old demuxer is freed and the new one (and its data) moves in
    for (auto*& b : p_->bsf) av_bsf_free(&b);
    avformat_close_input(&p_->fmt);
    av_freep(&p_->io->buffer);
    avio_context_free(&p_->io);
    p_->data = std::move(n.data);
    p_->pos = n.pos;
    p_->io = n.io;
    p_->io->opaque = p_.get();
    p_->fmt = n.fmt;
    p_->bsf = std::move(n.bsf);
    n.io = nullptr, n.fmt = nullptr, n.bsf.clear();
    av_packet_unref(p_->cur);
    av_packet_unref(p_->raw);
    p_->have = false;
    p_->eof = false;
    return kOk;
}

''' + C_ANCHOR

# ---- hle_movie.cpp
M_DECL_ANCHOR = "void video_started(const void* key, const char* rel);\nvoid video_ended(const void* key);\n}  // namespace tsuki_title\n"
M_DECL_NEW = ("void video_started(const void* key, const char* rel);\nvoid video_ended(const void* key);\n"
              "// AP_TITLE_LIVE (apply_title_live.py): the video chosen with F5 / F6 (title_bg.cpp), taken at SeekTo.\n"
              "bool take_swap(const void* key, std::string* rel, std::string* data);\n"
              "}  // namespace tsuki_title\n")
SEEK_OLD = ("    Extractor* e = ext(c.x[0]);\n"
            "    Status s = e->seek_to(int64_t(c.x[1]));\n"
            "    wake_track_input(c, e);\n")
SEEK_NEW = ("    Extractor* e = ext(c.x[0]);\n"
            "    {  // AP_TITLE_LIVE: the loop of the title video is the moment to show the one the player chose with F5 / F6\n"
            "        std::string swap_rel, swap_data;\n"
            "        if (tsuki_title::take_swap(e, &swap_rel, &swap_data)) {\n"
            "            const Status r = e->replace_memory(std::move(swap_data));\n"
            "            if (r == kOk) nx::log(\"title: video changed to %s\", swap_rel.c_str());\n"
            "            else nx::log(\"title: %s could not replace the video that is playing (status %d): it stays\", swap_rel.c_str(), int(r));\n"
            "        }\n"
            "    }\n"
            "    Status s = e->seek_to(int64_t(c.x[1]));\n"
            "    wake_track_input(c, e);\n")

# ---- platform_win.cpp
P_DECL_ANCHOR = "namespace nx {\nHWND g_window = nullptr;\n"
P_DECL_NEW = ("#ifdef NX_TITLE_LIVE  // AP_TITLE_LIVE (apply_title_live.py): F5 / F6 change the title video (title_bg.cpp)\n"
              "namespace tsuki_title {\nvoid hotkey(int dir);\n}\n#endif\n" + P_DECL_ANCHOR)
P_KEY_OLD = "            if (w == VK_F11) toggle_fullscreen(h);\n"
P_KEY_NEW = P_KEY_OLD + ("#ifdef NX_TITLE_LIVE  // AP_TITLE_LIVE\n"
                         "            if ((w == VK_F5 || w == VK_F6) && !(l & (1 << 30))) tsuki_title::hotkey(w == VK_F6 ? 1 : -1);\n"
                         "#endif\n")

CMAKE_ANCHOR = "target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_MUSIC)\n"
CMAKE_NEW = CMAKE_ANCHOR + ("# AP_TITLE_LIVE (apply_title_live.py): F5 / F6 change the title screen video while it is up\n"
                            "target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_LIVE)\n")

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_SCOPE', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),\n"
    "    @('both', 'framework\\runtime\\src\\platform_win.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("games/tsukihime_common/movie_core.h", H_ANCHOR, H_NEW),
    ("games/tsukihime_common/movie_core.cpp", C_ANCHOR, C_NEW),
    ("games/tsukihime_common/hle_movie.cpp", M_DECL_ANCHOR, M_DECL_NEW),
    ("games/tsukihime_common/hle_movie.cpp", SEEK_OLD, SEEK_NEW),
    ("runtime/src/platform_win.cpp", P_DECL_ANCHOR, P_DECL_NEW),
    ("runtime/src/platform_win.cpp", P_KEY_OLD, P_KEY_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
