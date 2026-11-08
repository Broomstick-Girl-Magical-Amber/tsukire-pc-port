"""apply_movie_open_diag.py - TEMPORARY DIAGNOSTIC: time what a video open costs (AP_MOVIE_OPEN_DIAG).

Goal: the stalls of 1-2 s when a big video starts (titlebg4 139 MB: 1083 ms, op2_ciel 307 MB: 2145 ms in nx.log) match
a read of the whole mp4 into RAM. This only measures; it changes nothing the game sees. For every video it adds to nx.log:

  romfs-diag: read <N> MB in <T> ms (disk <A> ms, AES <B> ms, other <C> ms) = <X> MB/s
      time inside tsuki_romfs::read for one read of 16 MiB or more: the part waiting for the dump (read_at), the part
      decrypting (ctr_blocks) and the rest (page faults of the destination buffer, loop). Only RomFS files of the dump
      go through here (not extract/romfs, not recipes).
  movie-diag: open_memory <T> ms (avformat_open_input + avformat_find_stream_info)

Compare with the "stall: N ms without a frame" line of the same video. If disk + AES + other is about the stall, the
read is the cause; if open_memory is, it is the demuxer probe (reading the stream in 64 KiB blocks).

Edits (anchors are lines no other patcher uses, so it can be removed in any order):
  - games/tsukihime_common/romfs_direct.cpp: <chrono> include, timers in read();
  - games/tsukihime_common/movie_core.cpp: timer around the open in Extractor::open_memory.
Temporary: it is not in $Markers of recomp_tsukihime.ps1. Idempotent (marker AP_MOVIE_OPEN_DIAG), all or nothing,
keeps CRLF and BOM, refuses if an anchor is not found; --remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_movie_open_diag.py            then rebuild (the edition you test)
  play: open the game (title video), then an opening (op2_ciel) and a titlebg change; send nx.log
  python tools\\patches\\apply_movie_open_diag.py --remove   when done, then rebuild
"""
from _patch import run

MARK = "AP_MOVIE_OPEN_DIAG"

RD = "games/tsukihime_common/romfs_direct.cpp"
MC = "games/tsukihime_common/movie_core.cpp"

# ---- romfs_direct.cpp: <chrono>
INC_OLD = "#include <cerrno>\n"
INC_NEW = INC_OLD + "#include <chrono>  // AP_MOVIE_OPEN_DIAG (temporary)\n"

# ---- romfs_direct.cpp: accumulators and a destructor that logs at every return of read()
HEAD_OLD = "    const uint64_t head = nca_rel & 15;\n"
HEAD_NEW = HEAD_OLD + (
    "    // AP_MOVIE_OPEN_DIAG (temporary): time in read_at / ctr_blocks, logged when this read returns (16 MiB or more only)\n"
    "    uint64_t diag_disk_ns = 0, diag_ctr_ns = 0;\n"
    "    struct DiagEnd {\n"
    "        uint64_t n;\n"
    "        const uint64_t& disk;\n"
    "        const uint64_t& ctr;\n"
    "        std::chrono::steady_clock::time_point t0;\n"
    "        ~DiagEnd() {\n"
    "            if (n < (uint64_t(16) << 20)) return;\n"
    "            const double total = double(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
    "                                            std::chrono::steady_clock::now() - t0).count()) / 1e6;\n"
    "            const double d = double(disk) / 1e6, c = double(ctr) / 1e6;\n"
    "            const double mb = double(n) / 1048576.0;\n"
    "            nx::log(\"romfs-diag: read %.1f MB in %.0f ms (disk %.0f ms, AES %.0f ms, other %.0f ms) = %.0f MB/s\",\n"
    "                    mb, total, d, c, total - d - c, total > 0 ? mb * 1000.0 / total : 0.0);\n"
    "        }\n"
    "    } diag_end{n, diag_disk_ns, diag_ctr_ns, std::chrono::steady_clock::now()};\n")

# ---- romfs_direct.cpp: the two timed lines of the main loop
GOT_OLD = "        const uint64_t got = nx::plat::read_at(h->f, int64_t(s.nca_off + pos), out + done, chunk);\n"
GOT_NEW = ("        const auto diag_a = std::chrono::steady_clock::now();  // AP_MOVIE_OPEN_DIAG\n" + GOT_OLD +
           "        const auto diag_b = std::chrono::steady_clock::now();\n")
CTR_OLD = "        ctr_blocks(s.rk, s.upper, pos >> 4, out + done, size_t(whole >> 4));\n"
CTR_NEW = CTR_OLD + (
    "        diag_disk_ns += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(diag_b - diag_a).count());  // AP_MOVIE_OPEN_DIAG\n"
    "        diag_ctr_ns += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
    "                                    std::chrono::steady_clock::now() - diag_b).count());\n")

# ---- movie_core.cpp: the open of the demuxer
OPEN_OLD = "    if (avformat_open_input(&p_->fmt, nullptr, nullptr, nullptr) < 0) return kIoError;  // libera fmt si falla\n"
OPEN_NEW = ("    const auto diag_t0 = std::chrono::steady_clock::now();  // AP_MOVIE_OPEN_DIAG (temporary)\n" + OPEN_OLD)
PROBE_OLD = "    if (avformat_find_stream_info(p_->fmt, nullptr) < 0) return kIoError;\n    unsigned n = p_->fmt->nb_streams;\n"
PROBE_NEW = (
    "    if (avformat_find_stream_info(p_->fmt, nullptr) < 0) return kIoError;\n"
    "    std::fprintf(stderr, \"movie-diag: open_memory %.0f ms (%zu bytes)\\n\",  // AP_MOVIE_OPEN_DIAG\n"
    "                 double(std::chrono::duration_cast<std::chrono::microseconds>(\n"
    "                            std::chrono::steady_clock::now() - diag_t0).count()) / 1000.0,\n"
    "                 p_->data.size());\n"
    "    unsigned n = p_->fmt->nb_streams;\n")

EDITS = [
    (RD, INC_OLD, INC_NEW),
    (RD, HEAD_OLD, HEAD_NEW),
    (RD, GOT_OLD, GOT_NEW),
    (RD, CTR_OLD, CTR_NEW),
    (MC, OPEN_OLD, OPEN_NEW),
    (MC, PROBE_OLD, PROBE_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)