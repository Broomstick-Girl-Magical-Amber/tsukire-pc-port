"""apply_bc_fast.py - BC textures are decoded on the CPU several times faster, on several threads (AP_BC_FAST).

Why: a GPU without BC textures (textureCompressionBC = 0: every Mali, most Android phones that are not Adreno or
Xclipse) gets them decoded on the CPU when they are uploaded (nvn_bc.cpp). That decoder was written to be right, not
fast: it reads a BC7 block one bit at a time, interpolates each channel of each texel on its own and stores the texels
one by one, on one thread, inside the replay (the game's main thread waits). The game's interface textures are BC7:
the title screen load alone decodes hundreds of megabytes of texels on such a GPU (the "nvn/vk: staging: N buffers,
X MB" lines of nx.log at that load), one of the longest stalls of a session there.

What changes (runtime/src/nvn/nvn_bc.cpp only):
  - BC7 blocks go through bc7_fast(): fields are taken several bits at a time (no bit loop), the four channels of a
    texel are interpolated together, and whole rows of a block are stored at once. It gives the same texels as
    bc7(), bit for bit (tools/tests/test_bc_fast.py compares them on random blocks of every mode).
  - BC1-BC5 keep their block decoders; only the way texels are stored changes.
  - An image of 32768 blocks or more (about 720x720) is decoded by several threads, rows of blocks handed out in
    pieces: half of the CPUs, 4 at most (NX_BC_THREADS=<1..8> sets another number; 1 = no threads). The threads are
    made the first time they are needed and sleep between images.
  - bc_decode() as it was is kept as bc_decode_old():
      NX_BC_DECODE=old     the decoder as before (to compare times)
      NX_BC_DECODE=check   both; the image gets the old one's texels and a difference is logged
        nvn/vk: BC check: a WxH image (format F) differs between the old and the new decoder (N so far)
  - Logged at the first image and then every 64 MB of texels:
      nvn/vk: BC decoded on the CPU (fast, 4 threads): N images, X MB, Y ms (Z MB/s)
    A GPU with BC textures never gets here (NX_FORCE_BC_DECODE=1 on a PC forces it, to try this).
  - ../tools/recomp_tsukihime.ps1: its $Markers row, after the kernel.cpp row of apply_android_adpf.py (consolidated),
    which no other patcher uses as an anchor.

How much faster: tools/tests/test_bc_fast.py --bench prints the time of a 2048x2048 image, old and new, on the
machine it runs on (random blocks, the slowest case for both decoders).

Idempotent (marker AP_BC_FAST), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_bc_fast.py            then rebuild: tools\\android\\build_apk.ps1 -Edition <ed> (or a
                                                      new Android pack); PC: recomp_tsukihime.ps1 -Edition <ed> -From 9
  python tools\\patches\\apply_bc_fast.py --remove
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_BC_FAST"

BC = "runtime/src/nvn/nvn_bc.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

INC_OLD = """#include <algorithm>
#include <cstdlib>
#include <cstring>

#include "nvn_vk.h"
"""
INC_NEW = """#include <algorithm>
#include <atomic>  // AP_BC_FAST (apply_bc_fast.py), down to <vector> and platform.h
#include <bit>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <system_error>
#include <thread>
#include <vector>

#include "nvn_vk.h"
#include "platform.h"
"""

DEC_OLD = """void bc_decode(uint32_t nvn_format, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t slices,
               uint8_t* out) {
    Kind k = kind(nvn_format);
"""
DEC_NEW = """// ---- AP_BC_FAST (apply_bc_fast.py): the same texels, several times faster and on several threads ----
// The decoder below this block (bc_decode_old, the one that was here) reads a BC7 block bit by bit and stores texel by
// texel on the replay thread: a title screen load decodes hundreds of megabytes of texels that way on a GPU without BC.
// NX_BC_DECODE=old: that decoder; =check: both, compared, the image gets the old one's. NX_BC_THREADS=<1..8>.
static void bc_decode_old(uint32_t nvn_format, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t slices,
                          uint8_t* out);

namespace {

static_assert(std::endian::native == std::endian::little, "bc7_fast reads a block as two little-endian words");

#if defined(__GNUC__) || defined(__clang__)
#define NX_BC_INLINE inline __attribute__((always_inline))
#else
#define NX_BC_INLINE inline
#endif

// A BC7 block of mode `m` as 16 RGBA8 texels (R in the low byte): what bc7() gives, texel for texel
// (tools/tests/test_bc_fast.py). Inlined once per mode (bc7_fast), so the field widths are constants there. Fields are
// taken several bits at a time from the block's bytes (no bit loop), and the four channels of a texel are interpolated
// in one go, 16 bits apart in a 64-bit word (the largest sum, 64 * 255 + 32, fits 16 bits: nothing carries into the
// next channel).
NX_BC_INLINE void bc7_mode(const Mode& m, uint32_t mode, const uint8_t* block, uint32_t out[16]) {
    uint8_t padded[24] = {};  // the block and 8 zero bytes: peek() may load past its end
    std::memcpy(padded, block, 16);
    uint64_t hi;
    std::memcpy(&hi, block + 8, 8);
    uint32_t pos = mode + 1;
    auto peek = [&]() -> uint64_t {  // the bits from `pos` on, 57 of them at least
        uint64_t v;
        std::memcpy(&v, padded + (pos >> 3), 8);
        return v >> (pos & 7);
    };
    auto get = [&](uint32_t n) -> uint32_t {  // the next n bits (0..8), low bit first
        const uint32_t v = uint32_t(peek()) & ((1u << n) - 1u);
        pos += n;
        return v;
    };
    const uint32_t part = get(m.partition_bits), rotation = get(m.rotation_bits), select = get(m.index_select_bits);
    const uint32_t ends = m.subsets * 2u;
    uint32_t ep[6][4];
    for (uint32_t c = 0; c < 3; c++) {  // a channel of every endpoint: 30 bits at most
        uint64_t field = peek();
        for (uint32_t e = 0; e < ends; e++, field >>= m.color_bits)
            ep[e][c] = uint32_t(field) & ((1u << m.color_bits) - 1u);
        pos += ends * m.color_bits;
    }
    if (m.alpha_bits) {
        uint64_t field = peek();
        for (uint32_t e = 0; e < ends; e++, field >>= m.alpha_bits)
            ep[e][3] = uint32_t(field) & ((1u << m.alpha_bits) - 1u);
        pos += ends * m.alpha_bits;
    } else {
        for (uint32_t e = 0; e < ends; e++) ep[e][3] = 255u;
    }
    uint32_t pbit[6] = {};
    if (m.endpoint_pbits)
        for (uint32_t e = 0; e < ends; e++) pbit[e] = get(1);
    if (m.shared_pbits)
        for (uint32_t s = 0; s < m.subsets; s++) pbit[2 * s] = pbit[2 * s + 1] = get(1);
    const bool has_p = m.endpoint_pbits || m.shared_pbits;
    uint64_t wide[6];  // an endpoint's channels 16 bits apart
    for (uint32_t e = 0; e < ends; e++) {
        for (uint32_t c = 0; c < 4; c++) {
            uint32_t bits = c < 3 ? m.color_bits : m.alpha_bits;
            if (!bits) continue;  // alpha of an opaque mode: 255 already
            uint32_t v = ep[e][c];
            if (has_p) v = (v << 1) | pbit[e], bits++;
            v <<= 8 - bits;
            ep[e][c] = (v | (v >> bits)) & 0xffu;
        }
        wide[e] = uint64_t(ep[e][0]) | uint64_t(ep[e][1]) << 16 | uint64_t(ep[e][2]) << 32 | uint64_t(ep[e][3]) << 48;
    }
    constexpr uint64_t kHalf = 0x0020002000200020ull;  // + 32 in every channel
    auto mix = [&](uint32_t s, uint64_t w) -> uint32_t {  // the texel between subset s's endpoints at weight w of 64
        const uint64_t v = ((64 - w) * wide[2 * s] + w * wide[2 * s + 1] + kHalf) >> 6;
        return uint32_t(v & 0xff) | uint32_t((v >> 8) & 0xff00) | uint32_t((v >> 16) & 0xff0000) |
               uint32_t((v >> 24) & 0xff000000);
    };
    if (!m.index2_bits) {  // one index per texel, for colour and alpha: the block's last bits (63 at most)
        // bit i: texel i is an anchor (its index has one bit less); 16: none
        const uint32_t anchors = 1u | 1u << (m.subsets == 2 ? kAnchor2[part] : m.subsets == 3 ? kAnchor3a[part] : 16u) |
                                 1u << (m.subsets == 3 ? kAnchor3b[part] : 16u);
        const uint32_t two = m.subsets == 2 ? kPart2[part] : 0u, three = m.subsets == 3 ? kPart3[part] : 0u;
        const uint8_t* w = weights(m.index_bits);
        uint64_t field = hi >> (64 - (128 - pos));
        for (uint32_t i = 0; i < 16; i++) {
            const uint32_t n = m.index_bits - (anchors >> i & 1);
            out[i] = mix((two >> i & 1) | (three >> (2 * i) & 3), w[uint32_t(field) & ((1u << n) - 1u)]);
            field >>= n;
        }
        return;
    }
    // Modes 4 and 5: one subset, a second index set for alpha (or for colour when `select`), channels rotated. The
    // first set is 31 bits; the second one is the block's last bits (47 at most).
    const uint32_t n2 = 16u * m.index2_bits - 1u;
    uint64_t field = peek(), field2 = hi >> (64 - n2);
    uint32_t idx[16], idx2[16];
    for (uint32_t i = 0; i < 16; i++) {
        const uint32_t n = m.index_bits - (i == 0 ? 1u : 0u), k = m.index2_bits - (i == 0 ? 1u : 0u);
        idx[i] = uint32_t(field) & ((1u << n) - 1u), field >>= n;
        idx2[i] = uint32_t(field2) & ((1u << k) - 1u), field2 >>= k;
    }
    const uint8_t* wc = weights(select ? m.index2_bits : m.index_bits);
    const uint8_t* wa = weights(select ? m.index_bits : m.index2_bits);
    for (uint32_t i = 0; i < 16; i++) {
        const uint32_t ci = select ? idx2[i] : idx[i], ai = select ? idx[i] : idx2[i];
        uint32_t px = (mix(0, wc[ci]) & 0x00ffffffu) | (mix(0, wa[ai]) & 0xff000000u);
        if (rotation) {  // alpha and channel rotation - 1 change places
            const uint32_t at = 8 * (rotation - 1), a = px >> 24, ch = (px >> at) & 0xffu;
            px = (px & ~(0xff000000u | (0xffu << at))) | (ch << 24) | (a << at);
        }
        out[i] = px;
    }
}

void bc7_fast(const uint8_t* block, uint32_t out[16]) {
    switch (block[0] ? std::countr_zero(uint32_t(block[0])) : 8) {
        case 0: return bc7_mode(kModes[0], 0, block, out);
        case 1: return bc7_mode(kModes[1], 1, block, out);
        case 2: return bc7_mode(kModes[2], 2, block, out);
        case 3: return bc7_mode(kModes[3], 3, block, out);
        case 4: return bc7_mode(kModes[4], 4, block, out);
        case 5: return bc7_mode(kModes[5], 5, block, out);
        case 6: return bc7_mode(kModes[6], 6, block, out);
        case 7: return bc7_mode(kModes[7], 7, block, out);
        default: std::memset(out, 0, 64);  // reserved: transparent black
    }
}

// Rows r0..r1 of blocks of an image (row r is row r % bh of slice r / bh): its own rows of the output, so the
// threads never write the same bytes. The block decoders of BC1-BC5 are bc_decode_old's.
void bc_rows(Kind k, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t bytes, uint64_t r0, uint64_t r1,
             uint8_t* out) {
    const uint32_t bw = (width + 3) / 4, bh = (height + 3) / 4;
    const uint32_t block_size = k == kBc1 || k == kBc1a || k == kBc4 || k == kBc4s ? 8 : 16;
    const uint64_t pitch = uint64_t(width) * bytes;
    for (uint64_t r = r0; r < r1; r++) {
        const uint32_t by = uint32_t(r % bh), ny = std::min(4u, height - by * 4);
        const uint8_t* b = blocks + r * bw * block_size;
        uint8_t* rows = out + ((r / bh) * height + uint64_t(by) * 4) * pitch;
        for (uint32_t bx = 0; bx < bw; bx++, b += block_size) {
            const uint32_t nx = std::min(4u, width - bx * 4);
            uint8_t* at = rows + uint64_t(bx) * 4 * bytes;
            if (k == kBc7) {
                uint32_t t[16];
                bc7_fast(b, t);
                if (nx == 4)
                    for (uint32_t y = 0; y < ny; y++) std::memcpy(at + y * pitch, t + 4 * y, 16);
                else
                    for (uint32_t y = 0; y < ny; y++) std::memcpy(at + y * pitch, t + 4 * y, nx * 4);
                continue;
            }
            uint8_t px[16][4] = {};
            switch (k) {
                case kBc1: bc1_colors(b, px, false, false); break;
                case kBc1a: bc1_colors(b, px, false, true); break;
                case kBc2:
                    bc1_colors(b + 8, px, true, false);
                    for (int i = 0; i < 16; i++) px[i][3] = uint8_t(((b[i / 2] >> (4 * (i & 1))) & 15) * 17);
                    break;
                case kBc3: {
                    uint8_t a[16];
                    bc4(b, a, false);
                    bc1_colors(b + 8, px, true, false);
                    for (int i = 0; i < 16; i++) px[i][3] = a[i];
                    break;
                }
                case kBc4: case kBc4s: {
                    uint8_t red[16];
                    bc4(b, red, k == kBc4s);
                    for (int i = 0; i < 16; i++) px[i][0] = red[i];
                    break;
                }
                case kBc5: case kBc5s: {
                    uint8_t red[16], green[16];
                    bc4(b, red, k == kBc5s);
                    bc4(b + 8, green, k == kBc5s);
                    for (int i = 0; i < 16; i++) px[i][0] = red[i], px[i][1] = green[i];
                    break;
                }
                default: break;
            }
            for (uint32_t y = 0; y < ny; y++) {
                uint8_t* d = at + y * pitch;
                const uint8_t(*p)[4] = px + y * 4;
                if (bytes == 4) {
                    if (nx == 4)
                        std::memcpy(d, p, 16);
                    else
                        std::memcpy(d, p, nx * 4);
                } else if (bytes == 2) {
                    for (uint32_t x = 0; x < nx; x++) d[2 * x] = p[x][0], d[2 * x + 1] = p[x][1];
                } else {
                    for (uint32_t x = 0; x < nx; x++) d[x] = p[x][0];
                }
            }
        }
    }
}

// One image: its rows of blocks, taken `chunk` at a time by whoever asks next (the caller and the pool's threads; a
// thread on a slow core just takes fewer).
struct BcJob {
    Kind k = kNone;
    const uint8_t* blocks = nullptr;
    uint32_t width = 0, height = 0, bytes = 0;
    uint8_t* out = nullptr;
    uint64_t rows = 0, chunk = 1;
    std::atomic<uint64_t> next{0};
    void work() {
        for (;;) {
            const uint64_t r = next.fetch_add(chunk, std::memory_order_relaxed);
            if (r >= rows) return;
            bc_rows(k, blocks, width, height, bytes, r, std::min(rows, r + chunk), out);
        }
    }
};

// The threads that help with big images. Made on first use, never ended; they sleep between images.
struct BcPool {
    std::mutex m;
    std::condition_variable wake, done;
    BcJob* job = nullptr;
    uint64_t generation = 0;  // one more for every job
    uint32_t threads = 0, busy = 0;
};
BcPool& bc_pool() {
    static BcPool* const p = new BcPool;  // not destroyed: its threads may outlive the statics
    return *p;
}

void bc_worker(uint64_t seen) {
    plat::set_thread_name("bc decode");
    BcPool& p = bc_pool();
    std::unique_lock lk(p.m);
    for (;;) {
        p.wake.wait(lk, [&] { return p.generation != seen; });
        seen = p.generation;
        BcJob* const j = p.job;
        lk.unlock();
        j->work();
        lk.lock();
        if (--p.busy == 0) p.done.notify_one();
    }
}

// Runs `j` on the caller and on `workers` pool threads, and returns when all of it is decoded.
void bc_run(BcJob& j, uint32_t workers) {
    static std::mutex one;  // a job at a time
    std::lock_guard only(one);
    BcPool& p = bc_pool();
    {
        std::lock_guard lk(p.m);
        try {
            while (p.threads < workers) {
                std::thread(bc_worker, p.generation).detach();
                p.threads++;
            }
        } catch (const std::system_error&) {  // no more threads: with the ones there are
        }
        p.job = &j, p.busy = p.threads, p.generation++;
    }
    p.wake.notify_all();
    j.work();
    std::unique_lock lk(p.m);
    p.done.wait(lk, [&] { return p.busy == 0; });
    p.job = nullptr;
}

}  // namespace

void bc_decode(uint32_t nvn_format, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t slices,
               uint8_t* out) {
    static const int which = [] {  // 0 fast, 1 old, 2 check
        const char* e = std::getenv("NX_BC_DECODE");
        return e && std::strcmp(e, "old") == 0 ? 1 : e && std::strcmp(e, "check") == 0 ? 2 : 0;
    }();
    static const uint32_t threads = [] {
        const char* e = std::getenv("NX_BC_THREADS");
        const int n = e ? std::atoi(e) : int(std::thread::hardware_concurrency() / 2);
        return uint32_t(std::clamp(n, 1, e ? 8 : 4));
    }();
    const uint64_t t0 = plat::monotonic_ns();
    const uint32_t bytes = bc_decoded_bytes(nvn_format), bw = (width + 3) / 4, bh = (height + 3) / 4;
    const uint64_t size = uint64_t(width) * height * slices * bytes;
    auto fast = [&](uint8_t* to) {
        BcJob j;
        j.k = kind(nvn_format), j.blocks = blocks, j.width = width, j.height = height, j.bytes = bytes, j.out = to;
        j.rows = uint64_t(slices) * bh;
        if (!j.rows || !bw) return;
        const uint32_t workers = j.rows * bw >= 32768 && j.rows >= 8 ? threads - 1 : 0;
        j.chunk = workers ? std::clamp<uint64_t>(j.rows / (uint64_t(threads) * 8), 1, 32) : j.rows;
        if (workers)
            bc_run(j, workers);
        else
            j.work();
    };
    static uint64_t differ = 0;
    if (which == 1) {
        bc_decode_old(nvn_format, blocks, width, height, slices, out);
    } else if (which == 2) {
        std::vector<uint8_t> a(size), b(size);
        bc_decode_old(nvn_format, blocks, width, height, slices, a.data());
        fast(b.data());
        if (a != b && ++differ <= 20)
            log("nvn/vk: BC check: a %ux%u image (format %#x) differs between the old and the new decoder "
                "(%llu so far)",
                width, height, nvn_format, (unsigned long long)differ);
        if (size) std::memcpy(out, a.data(), size);
    } else {
        fast(out);
    }
    // Under the GPU lock, as every upload: plain counters.
    static uint64_t images = 0, total = 0, ns = 0, told = 0;
    images++, total += size, ns += plat::monotonic_ns() - t0;
    if (images == 1 || total - told >= (64ull << 20)) {
        told = total;
        char how[48];
        if (which == 1)
            std::snprintf(how, sizeof how, "old, NX_BC_DECODE=old");
        else
            std::snprintf(how, sizeof how, "%s, %u thread%s", which == 2 ? "check" : "fast", threads,
                          threads == 1 ? "" : "s");
        log("nvn/vk: BC decoded on the CPU (%s): %llu images, %.1f MB, %.0f ms (%.0f MB/s)", how,
            (unsigned long long)images, double(total) / 1048576.0, double(ns) / 1e6,
            ns ? double(total) / 1048576.0 / (double(ns) / 1e9) : 0.0);
    }
}

// The decoder as it was before AP_BC_FAST: NX_BC_DECODE=old and =check, and what the tests compare with.
static void bc_decode_old(uint32_t nvn_format, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t slices,
                          uint8_t* out) {
    Kind k = kind(nvn_format);
"""

RECO_OLD = """    @('both', 'framework\\runtime\\src\\kernel.cpp', 'AP_ANDROID_ADPF', 'apply_android_adpf.py', $false),
"""
RECO_NEW = """    @('both', 'framework\\runtime\\src\\kernel.cpp', 'AP_ANDROID_ADPF', 'apply_android_adpf.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_bc.cpp', 'AP_BC_FAST', 'apply_bc_fast.py', $false),
"""

EDITS = [
    (BC, INC_OLD, INC_NEW),
    (BC, DEC_OLD, DEC_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
