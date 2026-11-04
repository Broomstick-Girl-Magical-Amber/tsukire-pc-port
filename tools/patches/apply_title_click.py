"""apply_title_click.py - a left click on the game's logo in the title menu changes the title video, as F6 (AP_TITLE_CLICK).
Apply it after the other title patchers (apply_title_bg.py ... apply_title_sync.py); title_bg.cpp, the new version, goes with it.

The logic is in games/tsukihime_common/title_bg.cpp (upload_watch, draw_watch, logo_click). This patcher wires it:
  - runtime/src/nvn/nvn.h: two generic hooks, null by default (Wonder unchanged): UploadWatch (a game tags some of its
    textures when they go up, by their contents) and DrawWatch (it is told each draw that samples the game's copy of a
    tagged texture, with the vertex shader, its c[3] block, the size and the NX_ULTRAWIDE squeeze);
  - runtime/src/nvn/nvn_vk.h: GpuImage::watch_tag / watch_copy;
  - runtime/src/nvn/nvn_upload.cpp: the hooks' definitions and the call at upload (block-linear textures, layer 0 of level 0);
  - runtime/src/nvn/nvn_draw.cpp: after each draw, a render target drawn from a tagged texture at its own size takes the
    tag (the game's cached copy, as AP_HUD_GLYPHS does); a draw that samples a tagged copy calls DrawWatch;
  - games/tsukihime_common/hle_hid_tsuki.cpp: a new left click inside the picture first asks tsuki_title::logo_click (u, v
    as a fraction of the picture, from to_screen: window size, full screen and black bars included); if it was the logo,
    the game does not get that touch;
  - tsukihime_common.cmake: the define NX_TITLE_CLICK;
  - title.ini.example: the "click" key;
  - a row in $Markers of ../tools/recomp_tsukihime.ps1 for each code file and title_bg.cpp.

Idempotent (marker AP_TITLE_CLICK), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. It does not touch the anchors of apply_title_draws_diag.py: both can be
applied and removed in any order. After: cmake --build build\\tsukihime; the same with build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_TITLE_CLICK"

# ---- nvn.h: the hooks
NVN_H_ANCHOR = "extern std::atomic<uint64_t> g_upload_filter_generation;\n"
NVN_H_NEW = NVN_H_ANCHOR + """// AP_TITLE_CLICK (apply_title_click.py): a game may watch where some of its textures are drawn (Tsukihime: the title
// logo, to change the title video when it is clicked).
// UploadWatch: called on the thread that uploads, for every block-linear texture, with layer 0 of level 0 untiled (rows of
// whole blocks, `size` bytes; read only). Returns a tag (0: not watched; it must return at once for those). A render
// target the game draws from a tagged texture at its own size (its cached copy) takes the tag.
// DrawWatch: called on the replay thread after each draw that samples a tagged copy: its tag, the vertex shader's hash,
// the first `floats` floats of the vertex stage's c[3] block (null: none bound), the copy's size and the NX_ULTRAWIDE x
// squeeze of that draw (0: none). Null hooks (the default): nothing changes.
using UploadWatch = uint64_t (*)(const Texture& t, const uint8_t* level0, uint64_t size);
using DrawWatch = void (*)(uint64_t tag, const char* vs_hash, const float* c3, uint32_t floats, uint32_t width,
                           uint32_t height, float uw_x);
extern std::atomic<UploadWatch> g_upload_watch;
extern std::atomic<DrawWatch> g_draw_watch;
"""

# ---- nvn_vk.h: the tag of an image
VK_H_ANCHOR = "    bool filter_derived = false;\n"
VK_H_NEW = VK_H_ANCHOR + ("    // AP_TITLE_CLICK: the tag UploadWatch gave this texture, or the one of the texture this render target is a copy of\n"
                          "    uint64_t watch_tag = 0;\n"
                          "    bool watch_copy = false;\n")

# ---- nvn_upload.cpp: definitions and the call (after the HUD hook, which never changes these textures)
UP_DEF_ANCHOR = "std::atomic<uint64_t> g_upload_filter_generation{1};\n"
UP_DEF_NEW = UP_DEF_ANCHOR + ("std::atomic<UploadWatch> g_upload_watch{nullptr};  // AP_TITLE_CLICK (nvn.h)\n"
                              "std::atomic<DrawWatch> g_draw_watch{nullptr};\n")
UP_CALL_ANCHOR = "    if (decode) {  // every level's blocks -> texels, tightly packed, then copied as an uncompressed image\n"
UP_CALL_NEW = ("    if (UploadWatch w = g_upload_watch.load(std::memory_order_acquire))  // AP_TITLE_CLICK: textures a game watches\n"
               "        img.watch_tag = w(t, linear.data() + levels[0].offset, levels[0].layer_size), img.watch_copy = false;\n"
               ) + UP_CALL_ANCHOR

# ---- nvn_draw.cpp: copies take the tag; draws of a tagged copy are told
DRAW_ANCHOR = ("    g_pass_written |= ((1u << t.color_count) - 1) | (g_state.depth.depth_write || g_state.depth.stencil_test ? 1u << 8 : 0);\n")
DRAW_NEW = r'''    // AP_TITLE_CLICK (apply_title_click.py): a render target drawn from a watched texture at its own size is the game's
    // copy of it and takes its tag; a draw that samples a tagged copy is told to the game (g_draw_watch).
    if (DrawWatch dw = g_draw_watch.load(std::memory_order_acquire)) {
        GpuImage* dst = t.color_count && t.colors[0] ? image_of(t.colors[0]) : nullptr;
        for (const ResolvedTexture& r : g_textures.resolved) {
            GpuImage* src = r.texture ? image_of(r.texture) : nullptr;
            if (!src || !src->watch_tag || src == dst) continue;
            if (!src->watch_copy) {
                if (dst && !dst->watch_tag && dst->width == src->width && dst->height == src->height)
                    dst->watch_tag = src->watch_tag, dst->watch_copy = true;
                continue;
            }
            const float* c3 = nullptr;
            uint32_t floats = 0;
            if (const Range& u = g_state.uniforms[0][0]; u.address)
                if (const uint8_t* f = guest_bytes(u.address))
                    c3 = reinterpret_cast<const float*>(f), floats = uint32_t(std::min<uint64_t>(u.size / 4, 16));
            dw(src->watch_tag, vs->hash.c_str(), c3, floats, src->width, src->height, key.uw_x);
        }
    }
''' + DRAW_ANCHOR

# ---- hle_hid_tsuki.cpp: the click asks title_bg.cpp first
HID_DECL_ANCHOR = '#include "platform.h"\n\nnamespace nx {\nnamespace {\n'
HID_DECL_NEW = ('#include "platform.h"\n\n'
                "#ifdef NX_TITLE_CLICK  // AP_TITLE_CLICK (apply_title_click.py): a click on the title logo (title_bg.cpp)\n"
                "namespace tsuki_title {\nbool logo_click(float u, float v);\n}\n#endif\n\n"
                "namespace nx {\nnamespace {\n")
HID_FN_ANCHOR = ("            std::clamp(int32_t(v * kTouchH), 0, kTouchH - 1)};\n"
                 "}\n"
                 "#endif\n")
HID_FN_NEW = ("            std::clamp(int32_t(v * kTouchH), 0, kTouchH - 1)};\n"
              "}\n"
              "\n"
              "// AP_TITLE_CLICK: true if title_bg.cpp took this click (the logo of the title menu): the game does not get it.\n"
              "bool title_logo_click(const ScreenPoint& pt) {\n"
              "#ifdef NX_TITLE_CLICK\n"
              "    return tsuki_title::logo_click((pt.x + 0.5f) / kTouchW, (pt.y + 0.5f) / kTouchH);\n"
              "#else\n"
              "    (void)pt;\n"
              "    return false;\n"
              "#endif\n"
              "}\n"
              "#endif\n")
HID_CLICK_OLD = ("            g_touch_seen_downs = p.downs[0];\n"
                 "            if (pt.inside) {\n"
                 "                g_touching = true;\n")
HID_CLICK_NEW = ("            g_touch_seen_downs = p.downs[0];\n"
                 "            if (pt.inside && title_logo_click(pt)) {  // AP_TITLE_CLICK: the title video changes instead\n"
                 "                if (trace_on()) log(\"hid: clic en el logo del titulo (%d,%d): no va al juego\", pt.x, pt.y);\n"
                 "            } else if (pt.inside) {\n"
                 "                g_touching = true;\n")

# ---- tsukihime_common.cmake
CMAKE_ANCHOR = "target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_LIVE)\n"
CMAKE_NEW = CMAKE_ANCHOR + ("# AP_TITLE_CLICK (apply_title_click.py): a click on the logo of the title menu changes the video, as F6\n"
                            "target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_CLICK)\n")

# ---- title.ini.example
INI_ANCHOR = "; hotkeys = on\n"
INI_NEW = INI_ANCHOR + (";\n"
                        "; A left click on the game's logo (the kanji or TSUKIHIME) in the title menu does what F6 does. It only works\n"
                        "; while the menu options are on screen: on \"press any button\" the click starts the game as always. AP_TITLE_CLICK\n"
                        "; Like F5 / F6, it only goes through the videos your save has unlocked (see unlock below).\n"
                        "; on (default) / off\n"
                        "; click = on\n")
INI_UNLOCK_OLD = "; titlebg number it asks for is what is unlocked (1 on a new save). video, pool, music and F5 / F6 stay within it.\n"
INI_UNLOCK_NEW = ("; titlebg number it asks for is what is unlocked (1 on a new save). video, pool, music, F5 / F6 and the click on\n"
                  "; the logo stay within it (AP_TITLE_CLICK).\n")

# ---- recomp_tsukihime.ps1
MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE_MUSIC', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_upload.cpp', 'AP_TITLE_CLICK', 'apply_title_click.py', $false),\n"
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_draw.cpp', 'AP_TITLE_CLICK', 'apply_title_click.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_TITLE_CLICK', 'apply_title_click.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_CLICK', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("runtime/src/nvn/nvn.h", NVN_H_ANCHOR, NVN_H_NEW),
    ("runtime/src/nvn/nvn_vk.h", VK_H_ANCHOR, VK_H_NEW),
    ("runtime/src/nvn/nvn_upload.cpp", UP_DEF_ANCHOR, UP_DEF_NEW),
    ("runtime/src/nvn/nvn_upload.cpp", UP_CALL_ANCHOR, UP_CALL_NEW),
    ("runtime/src/nvn/nvn_draw.cpp", DRAW_ANCHOR, DRAW_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_DECL_ANCHOR, HID_DECL_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_FN_ANCHOR, HID_FN_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_CLICK_OLD, HID_CLICK_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
    ("games/tsukihime_common/title.ini.example", INI_ANCHOR, INI_NEW),
    ("games/tsukihime_common/title.ini.example", INI_UNLOCK_OLD, INI_UNLOCK_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)