"""apply_movie_conv_threads.py - movie frames are converted to the game's format on several threads (AP_MOVIE_CONV_THREADS).
Apply apply_movie_hw.py, apply_movie_hw_wait.py, apply_movie_hw_solo.py and apply_movie_audio_queue.py first.
v2: if the first version of this file was applied, undo it with THAT file first ("python apply_movie_conv_threads.py
--remove" with the old file), then put this one in its place and apply it.

Why: with the hardware decoder (MediaCodec) and the audio queue fix, the prologue plays 60/60 on fast phones, but on
slower ones (seen on a Dimensity 7020 / PowerVR BXM) some frames are still dropped after ~20 s. Decoding is no longer
the problem (~30 pictures out per second); the conversion of each 1920x1080 picture from the decoder's YUV to the
RGBA texture the game wants is done with swscale on ONE thread inside the game's call. It costs 4-6 ms at best and
10-15 ms when the phone is warm, and the frames that were dropped are the ones where decode + convert took ~29 ms
instead of ~18 ms (budget: 33 ms a frame).
v1 started and joined new threads for every picture; on a Snapdragon 8 Elite with 3 threads the movie froze after 5
pictures (every movie thread idle, one picture ready that the game never took: the converting thread never came
back), with 2 threads it did not. v2 never creates threads per picture.

What it does (games/tsukihime_common/movie_core.cpp, the video conversion of every decoder, hardware or software):
  * A small pool of N-1 worker threads, started once (the first time a picture is converted) and kept for the whole
    run; they wait on a condition variable between pictures. Only one conversion uses the pool at a time; if it is
    busy, the picture is converted the old way.
  * For RGBA output (what the game asks for), the picture is cut into N horizontal bands (heights a multiple of 16).
    The calling thread prepares every band's swscale context and buffer (no allocation in the workers), then each band
    is converted on its own thread (the calling thread does the first one), with 16 rows of context above and below
    into the band's own buffer, and only its rows are copied to the output. Byte for byte the same picture as one
    single conversion (checked on 1920x1080 NV12 and YUV420P against plain sws_scale, N = 2..4).
  * N: NX_MOVIE_CONV_THREADS=<1..8>; default 3 on Android with 6 or more CPUs, 2 with fewer, 1 elsewhere (PC
    unchanged). 1 = the old single conversion. Pictures under 64*N rows, RGB or paletted sources and NV12 output keep
    the old path, and so does a picture whose band setup fails.
  * The conversion time keeps being measured as before (NX_MOVIE_TIMING "conv").
Plus one $Markers row in ../tools/recomp_tsukihime.ps1 (after the row of AP_MOVIE_AUDIO_QUEUE).

Idempotent (marker AP_MOVIE_CONV_THREADS), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_MOVIE_CONV_THREADS"
C = "games/tsukihime_common/movie_core.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# 1. includes
A1 = "#include <thread>  // AP_MOVIE_HW_WAIT\n"
N1 = (A1 +
      "#include <vector>  // AP_MOVIE_CONV_THREADS\n"
      "#include <condition_variable>  // AP_MOVIE_CONV_THREADS\n"
      "#include <functional>  // AP_MOVIE_CONV_THREADS\n")
A2 = "#include <libswscale/swscale.h>\n}\n"
N2 = ("#include <libswscale/swscale.h>\n"
      "#include <libavutil/pixdesc.h>  // AP_MOVIE_CONV_THREADS\n"
      "}\n")

# 2. NX_MOVIE_CONV_THREADS and the worker pool
A3 = "static bool is_hw_ctx(const AVCodecContext* c) {\n"
N3 = ("// AP_MOVIE_CONV_THREADS: threads for the YUV -> game format conversion of a video picture (NX_MOVIE_CONV_THREADS,\n"
      "// 1..8; default 3 on Android with >= 6 CPUs, 2 with fewer, 1 elsewhere).\n"
      "static int movie_conv_threads() {\n"
      "    static const int n = [] {\n"
      "        const char* e = std::getenv(\"NX_MOVIE_CONV_THREADS\");\n"
      "        long v = 1;\n"
      "#if defined(__ANDROID__)\n"
      "        v = std::thread::hardware_concurrency() >= 6 ? 3 : 2;\n"
      "#endif\n"
      "        if (e && *e) {\n"
      "            long x = std::strtol(e, nullptr, 10);\n"
      "            if (x >= 1 && x <= 8) v = x;\n"
      "        }\n"
      "        nx::log(\"movie: video pictures converted on %ld thread(s) (NX_MOVIE_CONV_THREADS)\", v);\n"
      "        return int(v);\n"
      "    }();\n"
      "    return n;\n"
      "}\n"
      "// AP_MOVIE_CONV_THREADS: workers started once and kept; run(job) calls job(0) here and job(1..workers) on them,\n"
      "// and returns when all are done. One run at a time (busy() lets a second caller convert the old way).\n"
      "struct MovieConvPool {\n"
      "    std::mutex use, m;\n"
      "    std::condition_variable go, done;\n"
      "    const std::function<void(int)>* job = nullptr;\n"
      "    uint64_t gen = 0;\n"
      "    int workers = 0, left = 0;\n"
      "    explicit MovieConvPool(int n) : workers(n) {\n"
      "        for (int i = 0; i < n; i++) std::thread([this, i] { loop(i + 1); }).detach();\n"
      "    }\n"
      "    void loop(int idx) {\n"
      "        uint64_t seen = 0;\n"
      "        std::unique_lock<std::mutex> lk(m);\n"
      "        for (;;) {\n"
      "            go.wait(lk, [&] { return gen != seen; });\n"
      "            seen = gen;\n"
      "            const std::function<void(int)>* j = job;\n"
      "            lk.unlock();\n"
      "            (*j)(idx);\n"
      "            lk.lock();\n"
      "            if (--left == 0) done.notify_one();\n"
      "        }\n"
      "    }\n"
      "    void run(const std::function<void(int)>& j) {\n"
      "        {\n"
      "            std::lock_guard<std::mutex> lk(m);\n"
      "            job = &j;\n"
      "            left = workers;\n"
      "            gen++;\n"
      "        }\n"
      "        go.notify_all();\n"
      "        j(0);\n"
      "        std::unique_lock<std::mutex> lk(m);\n"
      "        done.wait(lk, [&] { return left == 0; });\n"
      "    }\n"
      "};\n"
      "static MovieConvPool* movie_conv_pool() {\n"
      "    static MovieConvPool* p = movie_conv_threads() > 1 ? new MovieConvPool(movie_conv_threads() - 1) : nullptr;\n"
      "    return p;  // never freed: the workers live as long as the process\n"
      "}\n"
      + A3)

# 3. members: one swscale context and one buffer per band, and the banded conversion
A4 = "    SwsContext* sws = nullptr;\n"
N4 = (A4 +
      "    std::vector<SwsContext*> sws_bands;  // AP_MOVIE_CONV_THREADS: one context per band\n"
      "    std::vector<std::vector<uint8_t>> conv_tmp;  // AP_MOVIE_CONV_THREADS: one buffer per band\n"
      "    // AP_MOVIE_CONV_THREADS: converts f into planes/strides in bands on the pool; false = not done (the caller\n"
      "    // converts the whole picture the old way).\n"
      "    bool conv_bands(const AVFrame* f, int w, int h, AVPixelFormat target, uint8_t* const planes[4], const int strides[4]) {\n"
      "        const int n = movie_conv_threads();\n"
      "        const AVPixelFormat sf = AVPixelFormat(f->format);\n"
      "        const AVPixFmtDescriptor* sd = av_pix_fmt_desc_get(sf);\n"
      "        if (n < 2 || target != AV_PIX_FMT_RGBA || !sd || (sd->flags & (AV_PIX_FMT_FLAG_RGB | AV_PIX_FMT_FLAG_PAL)) ||\n"
      "            h < 64 * n)\n"
      "            return false;  // NV12 output is a cheap copy; RGBA is the expensive one (and the game's)\n"
      "        MovieConvPool* pool = movie_conv_pool();\n"
      "        if (!pool) return false;\n"
      "        std::unique_lock<std::mutex> use(pool->use, std::try_to_lock);\n"
      "        if (!use.owns_lock()) return false;  // another picture is being converted on the pool\n"
      "        const int band = ((h + n - 1) / n + 15) / 16 * 16;\n"
      "        const int nb = std::min((h + band - 1) / band, pool->workers + 1);\n"
      "        if (nb * band < h) return false;\n"
      "        if (int(sws_bands.size()) < nb) sws_bands.resize(size_t(nb), nullptr);\n"
      "        if (int(conv_tmp.size()) < nb) conv_tmp.resize(size_t(nb));\n"
      "        // Everything that allocates is done here, on the calling thread: each band covers rows y0..y0+hb, read\n"
      "        // with 16 rows of context above and below (the scaler's chroma filter reads neighbouring rows).\n"
      "        for (int b = 0; b < nb; b++) {\n"
      "            const int y0 = b * band, hb = std::min(band, h - y0);\n"
      "            const int s0 = std::max(0, y0 - 16), sh = std::min(h, y0 + hb + 16) - s0;\n"
      "            SwsContext*& c = sws_bands[size_t(b)];\n"
      "            c = sws_getCachedContext(c, w, sh, sf, w, sh, target, SWS_BILINEAR, nullptr, nullptr, nullptr);\n"
      "            if (!c) return false;\n"
      "            std::vector<uint8_t>& tmp = conv_tmp[size_t(b)];\n"
      "            if (tmp.size() < size_t(strides[0]) * size_t(sh)) tmp.resize(size_t(strides[0]) * size_t(sh));\n"
      "        }\n"
      "        const int sp = av_pix_fmt_count_planes(sf);\n"
      "        std::atomic<bool> ok{true};\n"
      "        const std::function<void(int)> one = [&](int b) {\n"
      "            if (b >= nb) return;\n"
      "            const int y0 = b * band, hb = std::min(band, h - y0);\n"
      "            const int s0 = std::max(0, y0 - 16), sh = std::min(h, y0 + hb + 16) - s0;\n"
      "            const uint8_t* src[4] = {};\n"
      "            for (int p = 0; p < sp && p < 4; p++)\n"
      "                src[p] = f->data[p] + ptrdiff_t(s0 >> ((p == 1 || p == 2) ? sd->log2_chroma_h : 0)) * f->linesize[p];\n"
      "            uint8_t* t[4] = {conv_tmp[size_t(b)].data()};\n"
      "            if (sws_scale(sws_bands[size_t(b)], src, f->linesize, 0, sh, t, strides) != sh) {\n"
      "                ok = false;\n"
      "                return;\n"
      "            }\n"
      "            std::memcpy(planes[0] + ptrdiff_t(y0) * strides[0], t[0] + ptrdiff_t(y0 - s0) * strides[0],\n"
      "                        size_t(hb) * size_t(strides[0]));\n"
      "        };\n"
      "        pool->run(one);\n"
      "        return ok;\n"
      "    }\n")

# 4. close(): free the band contexts
A5 = ("        sws_freeContext(sws);\n"
      "        sws = nullptr;\n")
N5 = (A5 +
      "        for (SwsContext*& c : sws_bands) sws_freeContext(c);  // AP_MOVIE_CONV_THREADS\n"
      "        sws_bands.clear();\n"
      "        conv_tmp.clear();\n")

# 5. the conversion
A6 = "            sws_scale(sws, frame->data, frame->linesize, 0, h, planes, strides);\n"
N6 = ("            if (!conv_bands(frame, w, h, target, planes, strides))  // AP_MOVIE_CONV_THREADS\n"
      "                sws_scale(sws, frame->data, frame->linesize, 0, h, planes, strides);\n")

# 6. $Markers row
A7 = "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_AUDIO_QUEUE', 'apply_movie_audio_queue.py', $false),\n"
N7 = (A7 + "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_CONV_THREADS', 'apply_movie_conv_threads.py', $false),\n")

EDITS = [(C, A1, N1), (C, A2, N2), (C, A3, N3), (C, A4, N4), (C, A5, N5), (C, A6, N6), (RECO, A7, N7)]

run(MARK, EDITS, __doc__)
