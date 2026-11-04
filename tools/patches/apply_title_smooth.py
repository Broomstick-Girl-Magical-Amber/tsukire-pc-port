"""apply_title_smooth.py - fewer stalls when the title video starts its loop or when you enter / leave Gallery (AP_TITLE_SMOOTH).
Apply apply_title_sync.py first.

The log showed stalls of 180-200 ms in the game's own code at those moments (the game stops, re-configures and re-opens its
video decoder, and re-reads the ~150 MB video file). This patch makes three things cheaper; it changes nothing else:
  1. runtime/src/hle_fs.cpp, nx::read_game_file: the buffer is sized once from the file length and read with one fread
     (it grew with 64 KB appends: thousands of re-allocations and copies of a 150 MB string).
  2. games/tsukihime_common/movie_core.cpp: when a H.264 video decoder is closed (stop / reset / configure) its codec
     context is flushed and parked; the next start() with the same codec, size and extradata takes it back instead of
     calling avcodec_open2 again. A different video format simply opens a new one.
  3. games/tsukihime_common/hle_movie.cpp, read_video: after a title video (movie/titlebg*) is read, a background thread
     reads the same file again, so the next open of it (back from Gallery, loop re-open) takes it from memory without
     waiting. It costs one more copy of the video (about 150 MB) while you are on the title screen; it is dropped as soon
     as another kind of video is opened. Set NX_TITLE_PREFETCH=0 to turn this one off.
Plus a row per file in $Markers of ../tools/recomp_tsukihime.ps1.

Idempotent (marker AP_TITLE_SMOOTH), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_TITLE_SMOOTH"
C = "games/tsukihime_common/movie_core.cpp"
M = "games/tsukihime_common/hle_movie.cpp"
F = "runtime/src/hle_fs.cpp"

F1_A = "    std::string s;\n    char buf[65536];\n    size_t n;\n    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);\n    std::fclose(f);\n    return s;\n}\n"
F1_N = ("    std::string s;\n"
        "    if (std::fseek(f, 0, SEEK_END) == 0) {  // AP_TITLE_SMOOTH: sized once, one read\n"
        "        const long sz = std::ftell(f);\n"
        "        std::rewind(f);\n"
        "        if (sz > 0) {\n"
        "            s.resize(size_t(sz));\n"
        "            s.resize(std::fread(&s[0], 1, s.size(), f));\n"
        "        }\n"
        "    } else {\n"
        "        std::rewind(f);\n"
        "    }\n"
        "    char buf[65536];\n    size_t n;\n    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);\n    std::fclose(f);\n    return s;\n}\n")

C1_A = "struct Decoder::Impl {\n"
C1_N = ("// AP_TITLE_SMOOTH (apply_title_smooth.py): one closed H.264 decoder kept (flushed) for the next start() with the same\n"
        "// parameters, so the game's stop / configure / start of the title video does not call avcodec_open2 again.\n"
        "static std::mutex g_park_m;\n"
        "static AVCodecContext* g_parked = nullptr;\n"
        "static bool park_matches(const AVCodecContext* c, const AVCodecParameters* p) {\n"
        "    return c && p && c->codec_id == p->codec_id && c->width == p->width && c->height == p->height &&\n"
        "           c->extradata_size == p->extradata_size &&\n"
        "           (p->extradata_size == 0 || std::memcmp(c->extradata, p->extradata, size_t(p->extradata_size)) == 0);\n"
        "}\n\n" + C1_A)
C2_A = "    void close() {\n        avcodec_free_context(&ctx);\n"
C2_N = ("    void close() {\n"
        "        if (ctx && kind == DecoderKind::Video && ctx->codec_id == AV_CODEC_ID_H264 && avcodec_is_open(ctx)) {  // AP_TITLE_SMOOTH\n"
        "            avcodec_flush_buffers(ctx);\n"
        "            std::lock_guard<std::mutex> lk(g_park_m);\n"
        "            avcodec_free_context(&g_parked);\n"
        "            g_parked = ctx;\n"
        "            ctx = nullptr;\n"
        "        }\n"
        "        avcodec_free_context(&ctx);\n")
C3_A = "        ctx = avcodec_alloc_context3(codec);\n"
C3_N = ("        {  // AP_TITLE_SMOOTH\n"
        "            std::lock_guard<std::mutex> lk(g_park_m);\n"
        "            if (g_parked && park_matches(g_parked, par)) {\n"
        "                ctx = g_parked;\n"
        "                g_parked = nullptr;\n"
        "            }\n"
        "        }\n"
        "        if (ctx) {\n"
        "            ctx->pkt_timebase = kMicros;\n"
        "            started = true;\n"
        "            return kOk;\n"
        "        }\n" + C3_A)

M0_A = "#include <chrono>\n"
M0_N = M0_A + "#include <condition_variable>  // AP_TITLE_SMOOTH\n"
M1_A = "// AP_TITLE_BG (apply_title_bg.py): reads the video, but a titlebg<N>"
M1_N = (
    "// AP_TITLE_SMOOTH (apply_title_smooth.py): a title video is read again in the background right after it was read, so the\n"
    "// next open of it (back from Gallery, loop re-open) does not wait for the 150 MB file. Dropped when another video opens.\n"
    "struct PrefetchState {\n"
    "    std::mutex m;\n"
    "    std::condition_variable cv;\n"
    "    bool done = false;\n"
    "    std::string data;\n"
    "};\n"
    "std::mutex g_pf_m;\n"
    "std::string g_pf_rel;\n"
    "std::shared_ptr<PrefetchState> g_pf;\n"
    "bool prefetch_enabled() {\n"
    "    static const bool on = [] { const char* e = std::getenv(\"NX_TITLE_PREFETCH\"); return !(e && e[0] == '0'); }();\n"
    "    return on;\n"
    "}\n"
    "bool is_title_video(const std::string& rel) { return rel.find(\"movie/titlebg\") != std::string::npos; }\n"
    "std::string prefetch_take(const std::string& rel) {\n"
    "    std::shared_ptr<PrefetchState> st;\n"
    "    {\n"
    "        std::lock_guard<std::mutex> lk(g_pf_m);\n"
    "        if (!g_pf || g_pf_rel != rel) return {};\n"
    "        st = std::move(g_pf);\n"
    "        g_pf.reset();\n"
    "        g_pf_rel.clear();\n"
    "    }\n"
    "    std::unique_lock<std::mutex> lk(st->m);\n"
    "    st->cv.wait(lk, [&] { return st->done; });\n"
    "    return std::move(st->data);\n"
    "}\n"
    "void prefetch_start(const std::string& rel) {\n"
    "    auto st = std::make_shared<PrefetchState>();\n"
    "    {\n"
    "        std::lock_guard<std::mutex> lk(g_pf_m);\n"
    "        if (g_pf && g_pf_rel == rel) return;\n"
    "        g_pf = st;\n"
    "        g_pf_rel = rel;\n"
    "    }\n"
    "    std::thread([st, rel] {\n"
    "        std::string d = nx::read_game_file(rel);\n"
    "        std::lock_guard<std::mutex> lk(st->m);\n"
    "        st->data = std::move(d);\n"
    "        st->done = true;\n"
    "        st->cv.notify_all();\n"
    "    }).detach();\n"
    "}\n"
    "std::string read_cached(const std::string& rel) {\n"
    "    if (!prefetch_enabled()) return nx::read_game_file(rel);\n"
    "    std::string data = prefetch_take(rel);\n"
    "    if (data.empty()) data = nx::read_game_file(rel);\n"
    "    if (is_title_video(rel)) {\n"
    "        if (!data.empty()) prefetch_start(rel);\n"
    "    } else {\n"
    "        std::lock_guard<std::mutex> lk(g_pf_m);\n"
    "        g_pf.reset();\n"
    "        g_pf_rel.clear();\n"
    "    }\n"
    "    return data;\n"
    "}\n\n" + M1_A)
M2_A = "        std::string data = nx::read_game_file(alt);\n"
M2_N = "        std::string data = read_cached(alt);  // AP_TITLE_SMOOTH\n"
M3_A = "    return nx::read_game_file(rel);\n}\n\n// ====="
M3_N = "    return read_cached(rel);  // AP_TITLE_SMOOTH\n}\n\n// ====="

MARK_A = "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_SYNC', 'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n"
MARK_N = MARK_A + (
    "    @('both', 'framework\\runtime\\src\\hle_fs.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),\n")

EDITS = [
    (F, F1_A, F1_N),
    (C, C1_A, C1_N), (C, C2_A, C2_N), (C, C3_A, C3_N),
    (M, M0_A, M0_N), (M, M1_A, M1_N), (M, M2_A, M2_N), (M, M3_A, M3_N),
    ("../tools/recomp_tsukihime.ps1", MARK_A, MARK_N),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
