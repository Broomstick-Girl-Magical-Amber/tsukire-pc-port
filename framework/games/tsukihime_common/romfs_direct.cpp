// romfs_direct.cpp - AP_ROMFS_DIRECT (apply_romfs_direct.py): the rom: mount reads the RomFS straight from the
// .xci/.nsp in the root of the port, without extracting it.
//
// tools/internal/romfs_source.py (recomp_tsukihime.ps1, step 2) writes <data root>/romfs_source.bin: where the program
// NCA is inside the dump, where its RomFS image starts, the AES-CTR counter of the section, the content key (option B:
// the exe never reads prod.keys), the first 0x100 bytes of the NCA (to recognise the dump) and the table of every
// folder and file of the RomFS. Here: the table is loaded once, the dump is looked for in <data root>/../.. (the root
// of the port; the file name it had first, then any .xci/.nsp there; NX_ROMFS_DUMP=<file> overrides) and every read
// decrypts only the bytes asked for (AES-NI, 8 blocks at a time; plain C if the CPU has no AES-NI).
// hle_fs.cpp asks here only for what is not a file of mods/<edition> or of extract/romfs (that one, if it is still
// there, wins file by file). Without romfs_source.bin nothing changes. With the table but without the dump (and
// without a full extract/romfs) the game cannot load anything: a message box says so and the exe stops (code 7).
#ifdef NX_ROMFS_DIRECT

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <nx/runtime.h>

#include "platform.h"

#if defined(__x86_64__) || defined(_M_X64)
#define TSUKI_ROMFS_X86 1
#include <immintrin.h>
#include <wmmintrin.h>
#if defined(_MSC_VER)
#include <intrin.h>
#else
#include <cpuid.h>
#endif
#if defined(__clang__) || defined(__GNUC__)
#define TSUKI_AES_TARGET __attribute__((target("aes,sse2")))
#else
#define TSUKI_AES_TARGET
#endif
#endif

namespace fs = std::filesystem;

namespace tsuki_romfs {

namespace {

// ---- AES-128 (encryption only: CTR) ----------------------------------------------------------------------------

const uint8_t kSbox[256] = {
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76, 0xca, 0x82, 0xc9,
    0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0, 0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f,
    0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15, 0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07,
    0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75, 0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3,
    0x29, 0xe3, 0x2f, 0x84, 0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58,
    0xcf, 0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8, 0x51, 0xa3,
    0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2, 0xcd, 0x0c, 0x13, 0xec, 0x5f,
    0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73, 0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88,
    0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb, 0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac,
    0x62, 0x91, 0x95, 0xe4, 0x79, 0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a,
    0xae, 0x08, 0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a, 0x70,
    0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e, 0xe1, 0xf8, 0x98, 0x11,
    0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf, 0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42,
    0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16};

inline uint8_t xtime(uint8_t x) { return uint8_t((x << 1) ^ ((x & 0x80) ? 0x1b : 0)); }

// FIPS-197 key schedule: 11 round keys of 16 bytes (the same ones AESENC uses).
void expand_key(const uint8_t key[16], uint8_t rk[176]) {
    std::memcpy(rk, key, 16);
    uint8_t rcon = 1;
    for (int i = 16; i < 176; i += 4) {
        uint8_t t[4] = {rk[i - 4], rk[i - 3], rk[i - 2], rk[i - 1]};
        if (i % 16 == 0) {
            uint8_t u = t[0];
            t[0] = uint8_t(kSbox[t[1]] ^ rcon);
            t[1] = kSbox[t[2]];
            t[2] = kSbox[t[3]];
            t[3] = kSbox[u];
            rcon = xtime(rcon);
        }
        for (int k = 0; k < 4; k++) rk[i + k] = uint8_t(rk[i - 16 + k] ^ t[k]);
    }
}

void encrypt_block_c(const uint8_t rk[176], const uint8_t in[16], uint8_t out[16]) {
    uint8_t s[16];
    for (int i = 0; i < 16; i++) s[i] = uint8_t(in[i] ^ rk[i]);
    for (int r = 1; r <= 10; r++) {
        uint8_t t[16];
        for (int i = 0; i < 16; i++) t[i] = kSbox[s[i]];
        // ShiftRows (column-major state: byte 4*c + r)
        for (int c = 0; c < 4; c++)
            for (int row = 0; row < 4; row++) s[4 * c + row] = t[4 * ((c + row) % 4) + row];
        if (r != 10) {
            for (int c = 0; c < 4; c++) {
                uint8_t* p = s + 4 * c;
                uint8_t a0 = p[0], a1 = p[1], a2 = p[2], a3 = p[3], all = uint8_t(a0 ^ a1 ^ a2 ^ a3);
                p[0] = uint8_t(a0 ^ all ^ xtime(uint8_t(a0 ^ a1)));
                p[1] = uint8_t(a1 ^ all ^ xtime(uint8_t(a1 ^ a2)));
                p[2] = uint8_t(a2 ^ all ^ xtime(uint8_t(a2 ^ a3)));
                p[3] = uint8_t(a3 ^ all ^ xtime(uint8_t(a3 ^ a0)));
            }
        }
        for (int i = 0; i < 16; i++) s[i] = uint8_t(s[i] ^ rk[16 * r + i]);
    }
    std::memcpy(out, s, 16);
}

void put_be64(uint8_t* p, uint64_t v) {
    for (int i = 7; i >= 0; i--) {
        p[i] = uint8_t(v);
        v >>= 8;
    }
}

#ifdef TSUKI_ROMFS_X86
bool cpu_has_aesni() {
#if defined(_MSC_VER)
    int r[4];
    __cpuid(r, 1);
    return (r[2] >> 25) & 1;
#else
    unsigned a = 0, b = 0, c = 0, d = 0;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return false;
    return (c >> 25) & 1;
#endif
}

// XOR of `n` bytes with the keystream of whole blocks first_block, first_block + 1, ... (upper half of the counter
// fixed, lower half = block number, big-endian).
TSUKI_AES_TARGET void ctr_blocks_ni(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data,
                                    size_t blocks) {
    __m128i k[11];
    for (int i = 0; i < 11; i++) k[i] = _mm_loadu_si128(reinterpret_cast<const __m128i*>(rk + 16 * i));
    alignas(16) uint8_t ctr[8 * 16];
    size_t b = 0;
    while (b < blocks) {
        const size_t n = std::min<size_t>(8, blocks - b);
        __m128i x[8];
        for (size_t j = 0; j < n; j++) {
            std::memcpy(ctr + 16 * j, upper, 8);
            put_be64(ctr + 16 * j + 8, first_block + b + j);
            x[j] = _mm_xor_si128(_mm_load_si128(reinterpret_cast<const __m128i*>(ctr + 16 * j)), k[0]);
        }
        for (int r = 1; r < 10; r++)
            for (size_t j = 0; j < n; j++) x[j] = _mm_aesenc_si128(x[j], k[r]);
        for (size_t j = 0; j < n; j++) {
            x[j] = _mm_aesenclast_si128(x[j], k[10]);
            __m128i* p = reinterpret_cast<__m128i*>(data + 16 * (b + j));
            _mm_storeu_si128(p, _mm_xor_si128(_mm_loadu_si128(p), x[j]));
        }
        b += n;
    }
}
#endif

void ctr_blocks_c(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data, size_t blocks) {
    uint8_t ctr[16], ks[16];
    std::memcpy(ctr, upper, 8);
    for (size_t b = 0; b < blocks; b++) {
        put_be64(ctr + 8, first_block + b);
        encrypt_block_c(rk, ctr, ks);
        for (int i = 0; i < 16; i++) data[16 * b + i] ^= ks[i];
    }
}

bool use_aesni() {
#ifdef TSUKI_ROMFS_X86
    static const bool on = [] {
        const char* e = std::getenv("NX_ROMFS_NO_AESNI");  // tests: force the plain C path
        return cpu_has_aesni() && !(e && *e && *e != '0');
    }();
    return on;
#else
    return false;
#endif
}

void ctr_blocks(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data, size_t blocks) {
#ifdef TSUKI_ROMFS_X86
    if (use_aesni()) {
        ctr_blocks_ni(rk, upper, first_block, data, blocks);
        return;
    }
#endif
    ctr_blocks_c(rk, upper, first_block, data, blocks);
}

// ---- the table -------------------------------------------------------------------------------------------------

struct FileEntry {
    uint64_t off = 0;   // in the RomFS image
    uint64_t size = 0;
};

struct Child {
    std::string name;  // as in the RomFS
    bool dir = false;
    uint64_t size = 0;
};

struct Source {
    bool loaded = false;   // romfs_source.bin read
    bool usable = false;   // and its dump found
    uint64_t program_id = 0, nca_off = 0, nca_size = 0, romfs_off = 0, romfs_size = 0;
    uint8_t upper[8] = {};
    uint8_t rk[176] = {};
    uint8_t sig[0x100] = {};
    std::string hint;
    std::string dump;  // utf-8 path of the .xci/.nsp
    std::unordered_map<std::string, FileEntry> files;          // key: lower case, '/' separated
    std::unordered_map<std::string, std::vector<Child>> dirs;  // key: lower case folder ("" = root)
};

std::string lower(std::string s) {
    for (char& c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string norm(const std::string& rel) {
    std::string s;
    s.reserve(rel.size());
    for (char c : rel) s += (c == '\\') ? '/' : c;
    while (!s.empty() && s.front() == '/') s.erase(0, 1);
    while (!s.empty() && s.back() == '/') s.pop_back();
    return lower(s);
}

std::string utf8(const fs::path& p) {
    auto s = p.u8string();
    return std::string(s.begin(), s.end());
}

struct Reader {
    const std::vector<uint8_t>& b;
    size_t pos = 0;
    bool ok = true;
    bool need(size_t n) {
        if (pos + n > b.size()) ok = false;
        return ok;
    }
    uint32_t u32() {
        if (!need(4)) return 0;
        uint32_t v;
        std::memcpy(&v, b.data() + pos, 4);
        pos += 4;
        return v;
    }
    uint64_t u64() {
        if (!need(8)) return 0;
        uint64_t v;
        std::memcpy(&v, b.data() + pos, 8);
        pos += 8;
        return v;
    }
    void bytes(void* out, size_t n) {
        if (!need(n)) return;
        std::memcpy(out, b.data() + pos, n);
        pos += n;
    }
    std::string str() {
        uint32_t n = u32();
        if (n > (1u << 16) || !need(n)) {
            ok = false;
            return {};
        }
        std::string s(reinterpret_cast<const char*>(b.data() + pos), n);
        pos += n;
        return s;
    }
};

bool read_whole(const fs::path& p, std::vector<uint8_t>& out) {
    FILE* f = nx::plat::fopen_utf8(utf8(p), "rb");
    if (!f) return false;
    nx::plat::fseek64(f, 0, SEEK_END);
    int64_t n = nx::plat::ftell64(f);
    nx::plat::fseek64(f, 0, SEEK_SET);
    if (n < 0 || n > (int64_t(1) << 30)) {
        std::fclose(f);
        return false;
    }
    out.resize(size_t(n));
    bool ok = n == 0 || std::fread(out.data(), 1, size_t(n), f) == size_t(n);
    std::fclose(f);
    return ok;
}

bool parse(const std::vector<uint8_t>& raw, Source& s) {
    static const char kMagic[8] = {'N', 'X', 'R', 'S', 'R', 'C', '1', '\0'};
    if (raw.size() < 0x154 || std::memcmp(raw.data(), kMagic, 8) != 0) return false;
    Reader r{raw, 8};
    if (r.u32() != 1) return false;  // version
    r.u32();                         // flags
    s.program_id = r.u64();
    s.nca_off = r.u64();
    s.nca_size = r.u64();
    s.romfs_off = r.u64();
    s.romfs_size = r.u64();
    r.bytes(s.upper, 8);
    uint8_t key[16];
    r.bytes(key, 16);
    expand_key(key, s.rk);
    std::memset(key, 0, sizeof key);
    r.bytes(s.sig, sizeof s.sig);
    s.hint = r.str();
    s.dirs[""];
    // A folder, after its parents (each one listed once in the folder above it).
    std::function<void(const std::string&)> add_dir = [&s, &add_dir](const std::string& path) {
        if (path.empty() || s.dirs.count(lower(path))) return;
        const size_t slash = path.rfind('/');
        const std::string parent = slash == std::string::npos ? std::string() : path.substr(0, slash);
        add_dir(parent);
        s.dirs[lower(path)];
        s.dirs[lower(parent)].push_back({slash == std::string::npos ? path : path.substr(slash + 1), true, 0});
    };
    uint32_t nd = r.u32();
    for (uint32_t i = 0; i < nd && r.ok; i++) add_dir(r.str());
    uint32_t nf = r.u32();
    for (uint32_t i = 0; i < nf && r.ok; i++) {
        FileEntry e;
        e.off = r.u64();
        e.size = r.u64();
        std::string path = r.str();
        if (!r.ok || e.off + e.size > s.romfs_size) return false;
        size_t slash = path.rfind('/');
        std::string parent = slash == std::string::npos ? std::string() : path.substr(0, slash);
        add_dir(parent);
        s.dirs[lower(parent)].push_back({slash == std::string::npos ? path : path.substr(slash + 1), false, e.size});
        s.files[lower(path)] = e;
    }
    char end[4] = {};
    r.bytes(end, 4);
    return r.ok && std::memcmp(end, "END1", 4) == 0;
}

bool is_the_dump(const fs::path& p, const Source& s) {
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return false;
    uint64_t size = fs::file_size(p, ec);
    if (ec || size < s.nca_off + s.nca_size) return false;
    FILE* f = nx::plat::fopen_utf8(utf8(p), "rb");
    if (!f) return false;
    uint8_t buf[0x100];
    bool ok = nx::plat::read_at(f, int64_t(s.nca_off), buf, sizeof buf) == sizeof buf &&
              std::memcmp(buf, s.sig, sizeof buf) == 0;
    std::fclose(f);
    return ok;
}

fs::path data_root() { return fs::u8path(nx::plat::data_root()); }

std::string find_dump(const Source& s) {
    if (const char* e = std::getenv("NX_ROMFS_DUMP"); e && *e) {
        fs::path p = fs::u8path(e);
        if (is_the_dump(p, s)) return utf8(p);
        nx::log("romfs: NX_ROMFS_DUMP=%s is not the dump of romfs_source.bin", e);
        return {};
    }
    const fs::path root = (data_root() / ".." / "..").lexically_normal();
    std::vector<fs::path> cands;
    if (!s.hint.empty()) cands.push_back(root / fs::u8path(s.hint));
    std::error_code ec;
    for (auto it = fs::directory_iterator(root, ec); !ec && it != fs::directory_iterator(); it.increment(ec)) {
        std::string ext = lower(utf8(it->path().extension()));
        if ((ext == ".xci" || ext == ".nsp") && (cands.empty() || it->path() != cands.front())) cands.push_back(it->path());
    }
    for (const auto& p : cands)
        if (is_the_dump(p, s)) return utf8(p);
    return {};
}

[[noreturn]] void dump_missing(const Source& s) {
    const fs::path root = (data_root() / ".." / "..").lexically_normal();
    nx::log("romfs: the game reads its files from %s, and it is not in %s (nor any .xci/.nsp there that is that dump). "
            "Put it back in the root of the port (or recompile with -Extract). Stopping.",
            s.hint.c_str(), utf8(root).c_str());
    std::fprintf(stderr, "romfs: %s is not in %s: put it back in the root of the port.\n", s.hint.c_str(), utf8(root).c_str());
    std::fflush(stderr);
#if defined(_WIN32)
    std::wstring hint(s.hint.begin(), s.hint.end());  // file names of the root are ASCII in practice; only for the box
    std::wstring msg = L"The game reads its files straight from your dump (" + hint + L"),\n"
                       L"and it is not in the root of the port (the folder with framework, tools and data).\n\n"
                       L"Put the .xci/.nsp back there (the same file the game was recompiled from; renaming it is fine)\n"
                       L"and start the game again.";
    ::MessageBoxW(nullptr, msg.c_str(), L"Game data not found", 0x10 | 0x10000 | 0x40000);
#endif
    std::_Exit(7);
}

Source& src() {
    static Source s;
    static std::once_flag once;
    std::call_once(once, [] {
        const fs::path table = data_root() / "romfs_source.bin";
        std::vector<uint8_t> raw;
        if (!read_whole(table, raw)) {
            nx::log("romfs: no %s: rom: is extract/romfs", utf8(table).c_str());
            return;
        }
        if (!parse(raw, s)) {
            s = Source();
            nx::log("romfs: %s is damaged or of another version: rom: is extract/romfs (recompile from step 2)",
                    utf8(table).c_str());
            return;
        }
        s.loaded = true;
        s.dump = find_dump(s);
        if (s.dump.empty()) {
            std::error_code ec;
            if (fs::is_directory(data_root() / "extract" / "romfs" / "shader", ec)) {
                nx::log("romfs: the dump %s is not in the root; extract/romfs is used instead", s.hint.c_str());
                return;
            }
            dump_missing(s);
        }
        s.usable = true;
        nx::log("romfs: read straight from %s (program %016llx, %zu files, AES-%s)", s.dump.c_str(),
                (unsigned long long)s.program_id, s.files.size(), use_aesni() ? "NI" : "C");
    });
    return s;
}

}  // namespace

// ---- what hle_fs.cpp calls ---------------------------------------------------------------------------------------

struct Handle {
    FILE* f = nullptr;
    uint64_t start = 0;  // offset of the file in the RomFS image
    uint64_t size = 0;
};

bool enabled() { return src().usable; }

bool file(const std::string& rel, uint64_t* size) {
    Source& s = src();
    if (!s.usable) return false;
    auto it = s.files.find(norm(rel));
    if (it == s.files.end()) return false;
    if (size) *size = it->second.size;
    return true;
}

bool dir(const std::string& rel) {
    Source& s = src();
    return s.usable && s.dirs.count(norm(rel)) != 0;
}

void list(const std::string& rel, std::vector<std::string>& names, std::vector<uint8_t>& is_dir,
          std::vector<uint64_t>& sizes) {
    Source& s = src();
    if (!s.usable) return;
    auto it = s.dirs.find(norm(rel));
    if (it == s.dirs.end()) return;
    for (const Child& c : it->second) {
        names.push_back(c.name);
        is_dir.push_back(c.dir ? 1 : 0);
        sizes.push_back(c.size);
    }
}

Handle* open(const std::string& rel) {
    Source& s = src();
    if (!s.usable) return nullptr;
    auto it = s.files.find(norm(rel));
    if (it == s.files.end()) return nullptr;
    FILE* f = nx::plat::fopen_utf8(s.dump, "rb");  // one per open file, as with extract/romfs
    if (!f) {
        nx::log("romfs: %s could not be opened (errno %d)", s.dump.c_str(), errno);
        return nullptr;
    }
    return new Handle{f, it->second.off, it->second.size};
}

uint64_t size(const Handle* h) { return h ? h->size : 0; }

uint64_t read(Handle* h, int64_t off, void* buf, uint64_t n) {
    if (!h || off < 0 || uint64_t(off) >= h->size) return 0;
    n = std::min<uint64_t>(n, h->size - uint64_t(off));
    Source& s = src();
    const uint64_t nca_rel = s.romfs_off + h->start + uint64_t(off);  // offset in the NCA: what the counter counts
    uint8_t* out = static_cast<uint8_t*>(buf);
    // Whole 16-byte blocks; the unaligned head and tail go through a small buffer.
    const uint64_t head = nca_rel & 15;
    uint64_t done = 0;
    if (head) {
        uint8_t blk[16];
        const uint64_t m = std::min<uint64_t>(16 - head, n);
        if (nx::plat::read_at(h->f, int64_t(s.nca_off + nca_rel - head), blk, 16) != 16) return 0;
        ctr_blocks(s.rk, s.upper, (nca_rel - head) >> 4, blk, 1);
        std::memcpy(out, blk + head, size_t(m));
        done = m;
    }
    while (done < n) {
        const uint64_t pos = nca_rel + done;  // block aligned here
        const uint64_t left = n - done;
        if (left < 16) {
            uint8_t blk[16];
            if (nx::plat::read_at(h->f, int64_t(s.nca_off + pos), blk, 16) != 16) return done;
            ctr_blocks(s.rk, s.upper, pos >> 4, blk, 1);
            std::memcpy(out + done, blk, size_t(left));
            done += left;
            break;
        }
        const uint64_t chunk = std::min<uint64_t>(left & ~uint64_t(15), uint64_t(8) << 20);
        const uint64_t got = nx::plat::read_at(h->f, int64_t(s.nca_off + pos), out + done, chunk);
        const uint64_t whole = got & ~uint64_t(15);
        ctr_blocks(s.rk, s.upper, pos >> 4, out + done, size_t(whole >> 4));
        done += whole;
        if (got != chunk) break;
    }
    return done;
}

void close(Handle* h) {
    if (!h) return;
    if (h->f) std::fclose(h->f);
    delete h;
}

bool read_all(const std::string& rel, std::string& out) {
    Handle* h = open(rel);
    if (!h) return false;
    out.resize(size_t(h->size));
    const uint64_t n = h->size ? read(h, 0, &out[0], h->size) : 0;
    close(h);
    if (n != out.size()) {
        nx::log("romfs: short read of %s (%llu of %zu bytes)", rel.c_str(), (unsigned long long)n, out.size());
        out.clear();
        return false;
    }
    return true;
}

}  // namespace tsuki_romfs

#endif  // NX_ROMFS_DIRECT
