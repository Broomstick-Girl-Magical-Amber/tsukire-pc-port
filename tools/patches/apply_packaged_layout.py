"""apply_packaged_layout.py - Block height of packaged (BNTX) textures from the game, not guessed (AP_PACKAGED_LAYOUT).

Problem (2026-10-27): with the translation patch of updates\\, the 1432x328 BC7 texture of Options > Sound came out in
vertical stripes (the same texture already striped before any shader: dumped with NX_DUMP_TEXTURES). nvn_objects.cpp
does not get the block height of packaged textures from the driver (nvnTextureBuilderGetPackagedTextureLayout returns
1 for every texture, in Wonder and in Tsukihime: measured with diag_packaged_layout.py) and guesses it with the fit
from Wonder's files (docs/nvn/TILING.md: "smallest power of two >= rows * 3 / 32"). The fit is right for the game's
own textures (dozens where it differs from NVN's default rule look right), but the patch's tool tiled that texture
with 16 GOBs (the default rule) and the fit says 8.

Fix, per game and inert for the others (Wonder unchanged):
  - runtime/src/nvn/nvn_objects.cpp: with NX_PACKAGED_LAYOUT_HLE, nvnTextureBuilderSetPackagedTextureLayout becomes
    an HLE that keeps the 8 layout bytes it is given (BRTI +0x34 of the BNTX: bits 0..2 = log2 of the block height in
    GOBs) per builder and then calls the driver's own. At texture init, a packaged texture whose builder has a layout
    uses that block height; without one, the fit as before. The first textures where the two differ are logged
    ("nvn: packaged layout ..."), and once how many packaged textures had no layout.
    NX_PACKAGED_LAYOUT=fit: the fit for every texture (the behaviour before this patch), without rebuilding.
    Without the define the compiled code is the usual one.
  - games/tsukihime_common/tsukihime_common.cmake: target_compile_definitions(NX_PACKAGED_LAYOUT_HLE) (both editions).

Idempotent (marker AP_PACKAGED_LAYOUT), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte as they were. After: rebuild (recomp_tsukihime.ps1 -Edition japan -From 9).
If diag_packaged_layout.py is applied, remove it FIRST (diag_packaged_layout.py --remove): its anchor is the line this
patch extends, so it could not be removed afterwards.
"""
from _patch import run

MARK = "AP_PACKAGED_LAYOUT"

HELPER_ANCHOR = "// NX_TRACE_TEXTURES=1 (or =<frame>: from that presented frame on) logs texture inits, finalizations and pool\n"
HELPER = r'''#ifdef NX_PACKAGED_LAYOUT_HLE
// AP_PACKAGED_LAYOUT (apply_packaged_layout.py): the layout nn::gfx gives for a packaged (BNTX) texture, kept per
// builder. The driver's getter only says "packaged" (1), so without this the block height is a fit (below), and a
// texture tiled with another one (the 1432x328 BC7 of Tsukihime's translation patch: 16 GOBs, the fit says 8) comes
// out in vertical stripes. BRTI +0x34: bits 0..2 = log2 of the block height in GOBs (docs/nvn/TILING.md).
std::mutex g_packaged_lock;
std::unordered_map<uint64_t, uint64_t> g_packaged_layout;  // builder -> the 8 layout bytes

void TextureBuilderSetPackagedTextureLayout(CPU& c) {  // (NVNtextureBuilder*, const NVNpackagedTextureLayout*)
    uint64_t builder = c.x[0], layout = c.x[1];
    uint64_t v = layout >= kModuleBase && layout < kKernelBase + kKernelSize ? ld<uint64_t>(layout) : 0;
    {
        std::lock_guard lk(g_packaged_lock);
        if (v) g_packaged_layout[builder] = v;
        else g_packaged_layout.erase(builder);
    }
    driver(c, "nvnTextureBuilderSetPackagedTextureLayout", {builder, layout});
}

// Block height in GOBs from the layout given for `builder`; 0 if none (or NX_PACKAGED_LAYOUT=fit).
uint32_t packaged_block_height(uint64_t builder, uint64_t& raw) {
    static const bool fit_only = [] {
        const char* e = std::getenv("NX_PACKAGED_LAYOUT");
        return e && std::strcmp(e, "fit") == 0;
    }();
    raw = 0;
    if (fit_only) return 0;
    std::lock_guard lk(g_packaged_lock);
    auto it = g_packaged_layout.find(builder);
    if (it == g_packaged_layout.end()) return 0;
    raw = it->second;
    uint32_t log2 = uint32_t(raw & 7);
    return log2 <= 4 ? 1u << log2 : 0;
}
#endif

'''

FIT_OLD = ("        while (h * 32 < rows * 3 && h < 16) h <<= 1;\n"
           "        t.block_height_gobs = h;\n"
           "    }\n")
FIT_NEW = ("        while (h * 32 < rows * 3 && h < 16) h <<= 1;\n"
           "        t.block_height_gobs = h;\n"
           "#ifdef NX_PACKAGED_LAYOUT_HLE\n"
           "        {   // AP_PACKAGED_LAYOUT: the game's own block height when it gave one\n"
           "            uint64_t raw = 0;\n"
           "            uint32_t given = packaged_block_height(builder, raw);\n"
           "            static std::atomic<uint32_t> differ{0}, without{0};\n"
           "            if (given && given != h && differ++ < 40)\n"
           "                log(\"nvn: packaged layout %016llx: texture %llx %ux%u format %#x uses block height %u (the fit said %u)\",\n"
           "                    (unsigned long long)raw, (unsigned long long)guest, t.width, t.height, t.format, given, h);\n"
           "            if (!given && raw == 0 && without++ == 0)\n"
           "                log(\"nvn: a packaged texture without a layout from the game (%ux%u format %#x): block height fit %u\",\n"
           "                    t.width, t.height, t.format, h);\n"
           "            if (given) t.block_height_gobs = given;\n"
           "        }\n"
           "#endif\n"
           "    }\n")

HLE_ANCHOR = 'NX_HLE("nvnTextureWriteTexels", TextureWriteTexels);\n'
HLE_NEW = (HLE_ANCHOR +
           "#ifdef NX_PACKAGED_LAYOUT_HLE\n"
           'NX_HLE("nvnTextureBuilderSetPackagedTextureLayout", TextureBuilderSetPackagedTextureLayout);  // AP_PACKAGED_LAYOUT\n'
           "#endif\n")

CMAKE_ANCHOR = 'target_include_directories(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}")  # movie_core.h, input_tsuki.h\n'
CMAKE_NEW = CMAKE_ANCHOR + (
    "# AP_PACKAGED_LAYOUT (apply_packaged_layout.py): block height of packaged (BNTX) textures from the game, not guessed\n"
    "target_compile_definitions(${NX_GAME} PRIVATE NX_PACKAGED_LAYOUT_HLE)\n")

EDITS = [
    ("runtime/src/nvn/nvn_objects.cpp", "#include <set>\n", "#include <set>\n#include <unordered_map>  // AP_PACKAGED_LAYOUT\n"),
    ("runtime/src/nvn/nvn_objects.cpp", HELPER_ANCHOR, HELPER + HELPER_ANCHOR),
    ("runtime/src/nvn/nvn_objects.cpp", FIT_OLD, FIT_NEW),
    ("runtime/src/nvn/nvn_objects.cpp", HLE_ANCHOR, HLE_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
