"""apply_title_draws_diag.py - TEMPORARY DIAGNOSTIC: log the draws of one frame to find the title logo sprite (AP_TITLE_DRAWS_DIAG).

Goal: clicking the game's title logo should change the title video (as F6 does). For that the runtime has to know
when the logo is on screen and where. This diagnostic only logs; it changes nothing that is drawn.

With NX_TITLE_DRAWS=1 (without it nothing changes, not even the cost):
  - every block-linear texture upload is hashed (FNV-1a 64 of layer 0 of level 0, the same hash AP_HUD_GLYPHS uses,
    taken BEFORE the HUD hook can replace the contents);
  - a render target drawn from a hashed texture of its same size (the game's cached copy of a UI atlas) remembers
    that hash as "from";
  - F8 arms a capture: every draw of the next two presented frames goes to the log (2000 at most), as
      titledraws: frame F draw N vs <hash> fs <hash> arrays|indexed count C first B inst I -> target <ptr> WxH fmt F ...
        t<k>: texture <ptr> WxH fmt F [rt] hash <h> | from <h>
        vertex <v> (stride S): <floats>
        vs c3: <floats> / fs c3: <floats> / vs s0: <floats>
    and ends with "titledraws: end (N draws)".

Edits:
  - runtime/src/nvn/nvn_vk.h: GpuImage gets diag_hash and diag_from;
  - runtime/src/nvn/nvn_upload.cpp: the hash at upload;
  - runtime/src/nvn/nvn_draw.cpp: title_draws_diag() (definition) and its call after each draw;
  - runtime/src/platform_win.cpp: F8 arms the capture (only when NX_TITLE_DRAWS is set).
Temporary: it is not in $Markers of recomp_tsukihime.ps1. Idempotent (marker AP_TITLE_DRAWS_DIAG), all or nothing,
keeps CRLF and BOM, refuses if an anchor is not found; --remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_title_draws_diag.py            then rebuild (both editions if you test both)
  $env:NX_TITLE_DRAWS = "1"; run the game; press F8 on each screen to compare; send the log
  python tools\\patches\\apply_title_draws_diag.py --remove   when done, then rebuild
"""
from _patch import run

MARK = "AP_TITLE_DRAWS_DIAG"

# ---- nvn_vk.h: two fields in GpuImage
H_ANCHOR = "    uint64_t filter_generation = 0;\n"
H_NEW = H_ANCHOR + ("    // AP_TITLE_DRAWS_DIAG (temporary): hash of the uploaded contents, or of the texture this render target was copied from\n"
                    "    uint64_t diag_hash = 0, diag_from = 0;\n")

# ---- nvn_upload.cpp: the hash, before the HUD hook can change the bytes
U_ANCHOR = "    if (UploadFilter f = g_upload_filter.load(std::memory_order_acquire)) {  // AP_HUD_GLYPHS: a game's own contents\n"
U_NEW = ("    {  // AP_TITLE_DRAWS_DIAG (temporary): FNV-1a 64 of layer 0 of level 0, as AP_HUD_GLYPHS hashes it\n"
         "        static const bool diag = std::getenv(\"NX_TITLE_DRAWS\") != nullptr;\n"
         "        if (diag) {\n"
         "            uint64_t h = 14695981039346656037ull;\n"
         "            const uint8_t* p = linear.data() + levels[0].offset;\n"
         "            for (uint64_t k = 0; k < levels[0].layer_size; k++) h = (h ^ p[k]) * 1099511628211ull;\n"
         "            img.diag_hash = h, img.diag_from = 0;\n"
         "        }\n"
         "    }\n") + U_ANCHOR

# ---- nvn_draw.cpp: the diagnostic and its call
D_DEF_ANCHOR = "using namespace replay;\n\nnamespace {\n"
D_DEF_NEW = "using namespace replay;\n\n" + r'''// AP_TITLE_DRAWS_DIAG (apply_title_draws_diag.py, TEMPORARY). NX_TITLE_DRAWS=1: F8 (platform_win.cpp) logs every draw of
// the next two presented frames, to find the title logo sprite. Only logs: nothing drawn changes.
std::atomic<int> g_title_draws_arm{0};

namespace {
std::string title_draws_floats(const void* p, uint32_t n) {
    std::string s;
    const float* f = reinterpret_cast<const float*>(p);
    for (uint32_t k = 0; k < n; k++) {
        char b[24];
        std::snprintf(b, sizeof b, " %g", f[k]);
        s += b;
    }
    return s;
}
}  // namespace

void title_draws_diag(const Targets& t, bool indexed, const Cmd& c, const ShaderInfo* vs, const ShaderInfo* fs) {
    static const bool on = std::getenv("NX_TITLE_DRAWS") != nullptr;
    if (!on) return;
    // A render target drawn from a hashed texture of its same size is the game's copy of it: it keeps that hash.
    GpuImage* dst = t.color_count && t.colors[0] ? image_of(t.colors[0]) : nullptr;
    if (dst && !dst->diag_hash)
        for (const ResolvedTexture& r : g_textures.resolved) {
            GpuImage* src = r.texture ? image_of(r.texture) : nullptr;
            const uint64_t h = src ? (src->diag_hash ? src->diag_hash : src->diag_from) : 0;
            if (src && src != dst && h && src->width == dst->width && src->height == dst->height) {
                dst->diag_from = h;
                break;
            }
        }
    static uint64_t until = 0;
    static int logged = 0;
    if (g_title_draws_arm.exchange(0)) {
        until = g_frame.load() + 2, logged = 0;
        log("titledraws: armed at frame %llu", (unsigned long long)g_frame.load());
    }
    if (!until) return;
    if (g_frame.load() >= until || logged >= 2000) {
        log("titledraws: end (%d draws)", logged);
        until = 0;
        return;
    }
    logged++;
    const Texture* tt = dst ? get<Texture>(t.colors[0]) : nullptr;
    char sc[48] = "-";
    if (g_state.has_scissor)
        std::snprintf(sc, sizeof sc, "%d,%d %dx%d", g_state.scissor[0], g_state.scissor[1], g_state.scissor[2], g_state.scissor[3]);
    log("titledraws: frame %llu draw %llu vs %s fs %s %s count %d first %d inst %d -> target %llx %ux%u fmt %x "
        "vp %d,%d %dx%d sc %s blend %d",
        (unsigned long long)g_frame.load(), (unsigned long long)g_stats.done, vs->hash.c_str(), fs ? fs->hash.c_str() : "-",
        indexed ? "indexed" : "arrays", int(indexed ? c.i[0] : c.i[1]), int(indexed ? c.i[1] : c.i[0]), int(c.i[3]),
        (unsigned long long)(t.color_count ? t.colors[0] : 0), dst ? dst->width : 0, dst ? dst->height : 0,
        tt ? tt->format : 0, g_state.viewport[0], g_state.viewport[1], g_state.viewport[2], g_state.viewport[3], sc,
        int(g_state.color.blend_enable[0]));
    for (size_t k = 0; k < g_textures.resolved.size(); k++) {
        const ResolvedTexture& r = g_textures.resolved[k];
        GpuImage* si = r.texture ? image_of(r.texture) : nullptr;
        const Texture* st = r.texture ? get<Texture>(r.texture) : nullptr;
        log("  t%zu: texture %llx %ux%u fmt %x%s hash %016llx from %016llx", k, (unsigned long long)r.texture,
            si ? si->width : 0, si ? si->height : 0, st ? st->format : 0, si && si->rendered ? " rt" : "",
            (unsigned long long)(si ? si->diag_hash : 0), (unsigned long long)(si ? si->diag_from : 0));
    }
    if (!g_state.streams.empty())
        if (const Range& vb = g_state.vertex_buffers[0]; vb.address)
            if (const uint8_t* w = guest_bytes(vb.address)) {
                const uint32_t stride = g_state.streams[0].stride, words = std::min<uint32_t>(stride / 4, 8);
                const uint64_t base = uint64_t(std::max(0, int(indexed ? c.i[1] : c.i[0])));
                for (uint64_t v = base; v < base + 4 && stride && (v + 1) * stride <= vb.size; v++)
                    log("  vertex %llu (stride %u):%s", (unsigned long long)v, stride,
                        title_draws_floats(w + v * stride, words).c_str());
            }
    if (const Range& u = g_state.uniforms[0][0]; u.address)
        if (const uint8_t* f = guest_bytes(u.address))
            log("  vs c3:%s", title_draws_floats(f, uint32_t(std::min<uint64_t>(u.size / 4, 16))).c_str());
    if (const Range& u = g_state.uniforms[1][0]; u.address)
        if (const uint8_t* f = guest_bytes(u.address))
            log("  fs c3:%s", title_draws_floats(f, uint32_t(std::min<uint64_t>(u.size / 4, 8))).c_str());
    if (const Range& s = g_state.storage[0][0]; s.address)
        if (const uint8_t* f = guest_bytes(s.address))
            log("  vs s0:%s", title_draws_floats(f, uint32_t(std::min<uint64_t>(s.size / 4, 16))).c_str());
}

namespace {
'''
D_CALL_ANCHOR = "    g_stats.done++;\n"
D_CALL_NEW = D_CALL_ANCHOR + "    title_draws_diag(t, indexed, c, vs, fs);  // AP_TITLE_DRAWS_DIAG (temporary)\n"

# ---- platform_win.cpp: F8 arms the capture
P_DECL_ANCHOR = "HWND g_window = nullptr;\n"
P_DECL_NEW = P_DECL_ANCHOR + ("namespace nvn {  // AP_TITLE_DRAWS_DIAG (apply_title_draws_diag.py, temporary): nvn_draw.cpp\n"
                              "extern std::atomic<int> g_title_draws_arm;\n"
                              "}\n")
P_KEY_ANCHOR = "            if (w == VK_F11) toggle_fullscreen(h);\n"
P_KEY_NEW = ("            if (w == VK_F8 && !(l & (1 << 30)) && std::getenv(\"NX_TITLE_DRAWS\"))  // AP_TITLE_DRAWS_DIAG (temporary)\n"
             "                nvn::g_title_draws_arm = 1, log(\"titledraws: F8\");\n") + P_KEY_ANCHOR

EDITS = [
    ("runtime/src/nvn/nvn_vk.h", H_ANCHOR, H_NEW),
    ("runtime/src/nvn/nvn_upload.cpp", U_ANCHOR, U_NEW),
    ("runtime/src/nvn/nvn_draw.cpp", D_DEF_ANCHOR, D_DEF_NEW),
    ("runtime/src/nvn/nvn_draw.cpp", D_CALL_ANCHOR, D_CALL_NEW),
    ("runtime/src/platform_win.cpp", P_DECL_ANCHOR, P_DECL_NEW),
    ("runtime/src/platform_win.cpp", P_KEY_ANCHOR, P_KEY_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)