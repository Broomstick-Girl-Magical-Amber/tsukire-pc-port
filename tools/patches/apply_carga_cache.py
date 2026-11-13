"""apply_carga_cache.py - a cache of decoded images for the game's loads (AP_CARGA_CACHE).

hle_carga.cpp decodes the game's JPEG, PNG and zlib/gzip loads natively. The game decodes the same files again and
again while it runs (S25, 2026-11-13: 4 GB out of gzip and 1.9 GB out of JPEG in 5.4 minutes, many of them the same
sizes over and over; the texture uploader already finds 3 of every 4 uploaded MB identical to an earlier upload), and
its main thread waits for every one of those loads: they are the 100-200 ms stalls and most of what is left under
60 fps.

This keeps the decoded output of what was decoded before:
- key: a 128-bit hash of the whole compressed input, its size, and what shapes the output (kind; JPEG and PNG: width,
  height, pixel format);
- an output is kept from the SECOND time its input is seen (a file loaded once costs only the hash, about 1-2 ms for
  5 MB) and comes from the cache from the third on: a copy (about 5 ms for 23 MB) instead of a decode (25-35 ms);
- JPEG and PNG rows are kept packed and written back row by row, so the bytes between rows (the stride padding the
  sdk does not touch) stay untouched; zlib/gzip only when the whole stream comes in one call on a fresh context and
  ends in it, and the game's buffer holds the whole output (the context is left finished, as after the real decode);
- least recently used out first, up to NX_TSUKI_CACHE_MB megabytes (nothing over half of that is kept). Default: 256
  on Android, 0 (off) elsewhere until it is tried there; NX_TSUKI_CACHE_MB=0 turns it off, any other number sets it.
  Outputs under 256 KB are not worth it and are skipped.
Only in the native mode (NX_TSUKI_CARGA unset): `0` and `check` never use it.
Log: `carga/cache: on, up to 256 MB (NX_TSUKI_CACHE_MB)` once, and every 100 lookups
`carga/cache: 61 of 100 decodes came from the cache (1403 MB not decoded again; 310 of 400 in all); 14 kept, 212 of 256 MB`.
A load from the cache that still takes 20 ms or more shows as `carga: gzip (cache) ...`.

Idempotent (marker AP_CARGA_CACHE), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Only edits games/tsukihime_common/hle_carga.cpp (no other patcher edits its text) and
../tools/recomp_tsukihime.ps1 (a $Markers row, anchored after the hle_hid_tsuki.cpp row of apply_android_input.py,
which no other patcher uses).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_carga_cache.py
"""
from _patch import run

MARK = "AP_CARGA_CACHE"
CARGA = "games/tsukihime_common/hle_carga.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

CACHE = r'''// ---- AP_CARGA_CACHE (apply_carga_cache.py): the decoded output of what was decoded before ----
// The game decodes the same files over and over and its main thread waits for each load. Key: a 128-bit hash of the
// whole compressed input + its size + what shapes the output. An output is kept from the second time its input is
// seen and served from the third on. Least recently used out first, up to NX_TSUKI_CACHE_MB (256 on Android, 0 = off
// elsewhere by default).
struct CacheKey {
    uint64_t a = 0, b = 0;
    bool operator==(const CacheKey& o) const { return a == o.a && b == o.b; }
};
struct CacheKeyHash {
    size_t operator()(const CacheKey& k) const { return size_t(k.a ^ (k.b * 0x9E3779B97F4A7C15ull)); }
};
struct CacheEntry {
    std::vector<uint8_t> data;
    uint64_t aux = 0;   // zlib/gzip: input bytes consumed
    uint64_t used = 0;  // tick of the last use
};
constexpr uint64_t kCacheMin = 256 * 1024;  // smaller outputs are not worth a lookup

uint64_t cache_cap() {
    static const uint64_t cap = [] {
#if defined(__ANDROID__)
        uint64_t mb = 256;
#else
        uint64_t mb = 0;
#endif
        if (const char* e = std::getenv("NX_TSUKI_CACHE_MB")) mb = uint64_t(std::strtoull(e, nullptr, 10));
        if (mb > 8192) mb = 8192;
        if (mb) log("carga/cache: on, up to %llu MB (NX_TSUKI_CACHE_MB)", (unsigned long long)mb);
        else log("carga/cache: off (NX_TSUKI_CACHE_MB=<megabytes> turns it on)");
        return mb << 20;
    }();
    return cap;
}

inline uint64_t cache_rotl(uint64_t x, int r) { return (x << r) | (x >> (64 - r)); }
// Four lanes of 8 bytes (the rounds of xxHash64), folded two different ways into 128 bits; `tag` mixes in the kind
// and the shape of the output.
CacheKey cache_key(const uint8_t* p, uint64_t n, uint64_t tag) {
    constexpr uint64_t P1 = 0x9E3779B185EBCA87ull, P2 = 0xC2B2AE3D27D4EB4Full, P3 = 0x165667B19E3779F9ull,
                       P4 = 0x85EBCA77C2B2AE63ull, P5 = 0x27D4EB2F165667C5ull;
    uint64_t v[4] = {tag + P1 + P2, tag + P2, tag, tag - P1};
    const uint8_t* const end = p + n;
    while (end - p >= 32) {
        uint64_t w[4];
        std::memcpy(w, p, 32);
        for (int k = 0; k < 4; k++) v[k] = cache_rotl(v[k] + w[k] * P2, 31) * P1;
        p += 32;
    }
    uint8_t tail[32] = {};
    std::memcpy(tail, p, size_t(end - p));
    uint64_t w[4];
    std::memcpy(w, tail, 32);
    for (int k = 0; k < 4; k++) v[k] = cache_rotl(v[k] + w[k] * P2, 31) * P1;
    auto fin = [&](uint64_t h) {
        h ^= h >> 33, h *= P2, h ^= h >> 29, h *= P3, h ^= h >> 32;
        return h;
    };
    CacheKey k;
    k.a = fin(cache_rotl(v[0], 1) + cache_rotl(v[1], 7) + cache_rotl(v[2], 12) + cache_rotl(v[3], 18) + n * P5 + tag);
    k.b = fin((v[0] ^ cache_rotl(v[3], 23)) * P4 + (v[1] ^ cache_rotl(v[2], 41)) * P5 + (n ^ tag) * P1);
    return k;
}
uint64_t cache_tag(char kind, uint64_t x, uint64_t y, uint64_t z) {
    return (uint64_t(uint8_t(kind)) << 56) ^ (x * 0x100000001B3ull) ^ (y << 24) ^ (z << 48) ^ (z >> 16);
}

std::mutex g_cache_m;
std::unordered_map<CacheKey, std::shared_ptr<CacheEntry>, CacheKeyHash> g_cache;
std::unordered_map<CacheKey, uint8_t, CacheKeyHash> g_cache_seen;  // inputs seen once: kept the next time
uint64_t g_cache_bytes = 0, g_cache_tick = 0, g_cache_lookups = 0, g_cache_hits = 0, g_cache_saved = 0;
uint64_t g_cache_lookups0 = 0, g_cache_hits0 = 0;  // at the last log line

std::shared_ptr<CacheEntry> cache_find(const CacheKey& k) {
    std::lock_guard lk(g_cache_m);
    g_cache_lookups++;
    auto it = g_cache.find(k);
    std::shared_ptr<CacheEntry> e;
    if (it != g_cache.end()) {
        e = it->second;
        e->used = ++g_cache_tick;
        g_cache_hits++;
        g_cache_saved += e->data.size();
    }
    if (g_cache_lookups - g_cache_lookups0 >= 100) {
        log("carga/cache: %llu of %llu decodes came from the cache (%.0f MB not decoded again; %llu of %llu in all); "
            "%zu kept, %.0f of %.0f MB",
            (unsigned long long)(g_cache_hits - g_cache_hits0), (unsigned long long)(g_cache_lookups - g_cache_lookups0),
            double(g_cache_saved) / 1048576.0, (unsigned long long)g_cache_hits, (unsigned long long)g_cache_lookups,
            g_cache.size(), double(g_cache_bytes) / 1048576.0, double(cache_cap()) / 1048576.0);
        g_cache_lookups0 = g_cache_lookups, g_cache_hits0 = g_cache_hits;
    }
    return e;
}
// After a miss: true when this input was seen before (keep its output now).
bool cache_seen_before(const CacheKey& k) {
    std::lock_guard lk(g_cache_m);
    if (g_cache_seen.count(k)) return true;
    if (g_cache_seen.size() >= 8192) g_cache_seen.clear();
    g_cache_seen[k] = 1;
    return false;
}
void cache_store(const CacheKey& k, std::vector<uint8_t>&& data, uint64_t aux) {
    const uint64_t cap = cache_cap(), size = data.size();
    if (!size || size > cap / 2) return;
    auto e = std::make_shared<CacheEntry>();
    e->data = std::move(data), e->aux = aux;
    std::lock_guard lk(g_cache_m);
    if (g_cache.count(k)) return;
    while (g_cache_bytes + size > cap && !g_cache.empty()) {
        auto old = g_cache.begin();
        for (auto it = g_cache.begin(); it != g_cache.end(); ++it)
            if (it->second->used < old->second->used) old = it;
        g_cache_bytes -= old->second->data.size();
        g_cache.erase(old);
    }
    e->used = ++g_cache_tick;
    g_cache_bytes += size;
    g_cache[k] = std::move(e);
    g_cache_seen.erase(k);
}
// Rows of `row` bytes every `pitch` bytes <-> packed.
std::vector<uint8_t> cache_pack(const uint8_t* img, uint64_t rows, uint64_t pitch, uint64_t row) {
    std::vector<uint8_t> v(size_t(rows * row));
    for (uint64_t y = 0; y < rows; y++) std::memcpy(v.data() + y * row, img + y * pitch, size_t(row));
    return v;
}
void cache_unpack(const std::vector<uint8_t>& v, uint8_t* img, uint64_t rows, uint64_t pitch, uint64_t row) {
    if (pitch == row) {
        std::memcpy(img, v.data(), size_t(rows * row));
        return;
    }
    for (uint64_t y = 0; y < rows; y++) std::memcpy(img + y * pitch, v.data() + y * row, size_t(row));
}

'''

EDITS = [
    # ------------------------------------------------------------------------------------------------ the cache
    (CARGA,
     '''// ---- JpegDecoder::Decode ----
// Dos versiones del sdk (mismo resultado;''',
     CACHE + '''// ---- JpegDecoder::Decode ----
// Dos versiones del sdk (mismo resultado;'''),

    # ------------------------------------------------------------------------------------------------ JPEG
    (CARGA,
     '''    if (modo() == Modo::Nativo) {
        if (decode_jpeg_rgba(reinterpret_cast<const uint8_t*>(data), size, w, h, alignment, out) != kJpegOk) {
            static int failed = 0;
            if (failed++ < 5) log("carga: jpeg nativo fallo (%ux%u): por el sdk", w, h);
            call(c, real);
            return;
        }
        c.x[0] = 0;
        measure(g_jpeg, "jpeg", t0, uint64_t(w) * h * 4);
        return;
    }
''',
     '''    if (modo() == Modo::Nativo) {
        // AP_CARGA_CACHE: rows of w * 4 bytes every stride * 4.
        const uint64_t jrow = uint64_t(w) * 4, jbytes = jrow * h;
        const bool cached = cache_cap() && jbytes >= kCacheMin;
        CacheKey key;
        if (cached) {
            key = cache_key(reinterpret_cast<const uint8_t*>(data), size, cache_tag('J', w, h, 0xe));
            if (auto e = cache_find(key); e && e->data.size() == jbytes) {
                cache_unpack(e->data, out, h, uint64_t(stride) * 4, jrow);
                c.x[0] = 0;
                measure(g_jpeg, "jpeg (cache)", t0, jbytes);
                return;
            }
        }
        if (decode_jpeg_rgba(reinterpret_cast<const uint8_t*>(data), size, w, h, alignment, out) != kJpegOk) {
            static int failed = 0;
            if (failed++ < 5) log("carga: jpeg nativo fallo (%ux%u): por el sdk", w, h);
            call(c, real);
            return;
        }
        if (cached && cache_seen_before(key)) cache_store(key, cache_pack(out, h, uint64_t(stride) * 4, jrow), 0);
        c.x[0] = 0;
        measure(g_jpeg, "jpeg", t0, uint64_t(w) * h * 4);
        return;
    }
'''),

    # ------------------------------------------------------------------------------------------------ PNG
    (CARGA,
     '''    if (modo() == Modo::Nativo) {
        if (decode_png(reinterpret_cast<const uint8_t*>(data), size, fmt, w, h, stride, out) != 0) {
            static int failed = 0;
            if (failed++ < 5) log("carga: png nativo fallo (formato %d, %ux%u): por el sdk", fmt, w, h);
            call(c, real);
            return;
        }
''',
     '''    if (modo() == Modo::Nativo) {
        // AP_CARGA_CACHE: rows of rowbytes every stride.
        const uint64_t pbytes = rowbytes * h;
        const bool cached = cache_cap() && pbytes >= kCacheMin;
        CacheKey key;
        bool hit = false;
        if (cached) {
            key = cache_key(reinterpret_cast<const uint8_t*>(data), size, cache_tag('P', w, h, uint64_t(fmt)));
            if (auto e = cache_find(key); e && e->data.size() == pbytes) {
                cache_unpack(e->data, out, h, stride, rowbytes);
                hit = true;
            }
        }
        if (!hit && decode_png(reinterpret_cast<const uint8_t*>(data), size, fmt, w, h, stride, out) != 0) {
            static int failed = 0;
            if (failed++ < 5) log("carga: png nativo fallo (formato %d, %ux%u): por el sdk", fmt, w, h);
            call(c, real);
            return;
        }
        if (!hit && cached && cache_seen_before(key)) cache_store(key, cache_pack(out, h, stride, rowbytes), 0);
'''),
    (CARGA,
     '''        c.x[0] = 0;
        measure(g_png, "png", t0, uint64_t(h) * rowbytes);
        return;
''',
     '''        c.x[0] = 0;
        measure(g_png, hit ? "png (cache)" : "png", t0, uint64_t(h) * rowbytes);  // AP_CARGA_CACHE
        return;
'''),

    # ------------------------------------------------------------------------------------------------ zlib / gzip
    (CARGA,
     '''    if (modo() == Modo::Nativo) {
        bool r = s->step(out_size, in_used, out, out_cap, in, in_size);
        c.x[0] = r ? 1 : 0;
        measure(g_zlib, what, t0, r ? *out_size : 0);
        return;
    }
''',
     '''    if (modo() == Modo::Nativo) {
        // AP_CARGA_CACHE: only a whole stream in one call: a fresh context, and the stream ends in this call.
        const uint64_t cap32 = uint64_t(uint32_t(out_cap)), in32 = uint64_t(uint32_t(in_size));
        const bool cached = cache_cap() && s->live && !s->done && s->zs.total_in == 0 && s->zs.total_out == 0 && in &&
                            out && in32 >= 4096 && cap32 >= kCacheMin;
        CacheKey key;
        if (cached) {
            key = cache_key(in, in32, cache_tag('Z', uint64_t(s->wbits), 0, 0));
            if (auto e = cache_find(key); e && e->data.size() <= cap32 && e->aux <= in32) {
                std::memcpy(out, e->data.data(), e->data.size());
                *out_size = e->data.size(), *in_used = e->aux;
                s->done = true;  // as after Z_STREAM_END
                inflateEnd(&s->zs);
                s->live = false;
                c.x[0] = 1;
                measure(g_zlib, what[0] == 'g' ? "gzip (cache)" : "zlib (cache)", t0, *out_size);
                return;
            }
        }
        bool r = s->step(out_size, in_used, out, out_cap, in, in_size);
        if (r && cached && s->done && *out_size >= kCacheMin && cache_seen_before(key))
            cache_store(key, std::vector<uint8_t>(out, out + *out_size), *in_used);
        c.x[0] = r ? 1 : 0;
        measure(g_zlib, what, t0, r ? *out_size : 0);
        return;
    }
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_ANDROID_INPUT', 'apply_android_input.py', $false),
""",
     """    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_ANDROID_INPUT', 'apply_android_input.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_CARGA_CACHE', 'apply_carga_cache.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
