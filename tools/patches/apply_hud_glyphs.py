"""apply_hud_glyphs.py - HUD button icons follow the last device used: Xbox, PlayStation, Switch or keyboard (AP_HUD_GLYPHS).

Tsukihime draws the button icons of its help bar (Options, Save/Load, the controls tab) from one 384x256 BC7 atlas
(help_key, in allui). At load it draws every such UI texture once into a render target of its own (a UI cache:
fragment shader 388795e5..., 214 of them at boot) and the menus sample that copy, not the atlas. With this patch the
game code (games/tsukihime_common/hud_glyphs.cpp) swaps the atlas, whole, while it is uploaded to the GPU, and later
rewrites its cached copy, for the device with the last real input (a button, a key, a click or the wheel, a stick or
trigger past half way; stick noise does not count). The guest memory is never touched. Switch keeps the game's icons.

  - runtime/src/nvn/nvn.h, nvn_vk.h, nvn_upload.cpp: g_upload_filter, a hook a game may set. It is called for every
    block-linear texture upload with the untiled level 0 (layer 0), before it is copied (or BC-decoded) to the image,
    and returns a tag (0: not the game's). The image keeps the tag and the g_upload_filter_generation it went up with.
  - runtime/src/nvn/nvn_draw.cpp: a draw that samples a tagged image and renders into a target of the same size makes
    that target "derived" (same tag). NX_HUD_DUMP=1 with NX_SNAP_RAW=1: the target is also written to
    <data root>/frames/00000_d000000NN.raw right after that draw (to check what the game's copy does to the colours).
  - runtime/src/nvn/nvn_pipeline.cpp: when the game bumps g_upload_filter_generation, a tagged image is not "ready" and,
    at its next use, a tagged texture is uploaded again (the filter runs again) and a derived target gets its contents
    from g_derived_filter (RGBA8 rows from the game). Pool snapshots and re-initialized textures share the image.
  - runtime/src/nvn/nvn_debug.cpp: NX_SNAP_RAW also writes RGBA8 sRGB targets as stored.
  - games/tsukihime_common/input_tsuki.cpp/.h: first_key(button), the first key input.ini (or the defaults) gives an
    Npad button; keyboard keys and XInput pads report their real input (hud::note_input).
  - games/tsukihime_common/pad_sdl.cpp: SDL pads report it with their type (PlayStation, Switch, else Xbox).
  - games/tsukihime_common/hle_hid_tsuki.cpp: mouse clicks and the wheel report the keyboard.
  - games/tsukihime_common/tsukihime_common.cmake: NX_HUD_GLYPHS (hud_glyphs.cpp compiles nothing without it).
Unset hooks (any other game, NX_HUD_GLYPHS=off): nothing changes. The assets go to <data root>/hud
(tools/hud/build_hud_assets.py, run by recomp_tsukihime.ps1 after the build).
NX_HUD_GLYPHS=auto (default) | switch | xbox | ps | keyboard (fixed) | off.

Needs games/tsukihime_common/hud_glyphs.cpp and hud_glyphs.h (in the repo) and apply_sdl_pad.py, apply_mouse_ini.py,
apply_packaged_layout.py applied (their lines are the anchors).
Idempotent (marker AP_HUD_GLYPHS), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte as they were. After: rebuild (recomp_tsukihime.ps1 -Edition japan -From 9).
"""
from _patch import run

MARK = "AP_HUD_GLYPHS"

# ---- runtime: the hooks ------------------------------------------------------------------------------------------------
NVN_H_ANCHOR = ("    ~Texture();  // nvn_vk.cpp: the image is freed once the GPU is done with it\n"
                "};\n")
NVN_H_NEW = NVN_H_ANCHOR + (
    "\n"
    "// AP_HUD_GLYPHS (apply_hud_glyphs.py): a game may choose the contents of some of its textures (Tsukihime swaps its\n"
    "// button icon atlas for the last device used).\n"
    "// UploadFilter: called on the thread that uploads, for every block-linear texture, with layer 0 of level 0 untiled\n"
    "// (rows of whole blocks, tightly packed, `size` bytes); what it leaves there goes to the image. Returns a tag (0: not\n"
    "// the game's; it must return at once for those). The image keeps the tag and the generation it went up with.\n"
    "// DerivedFilter: a render target the game drew from a tagged texture (same size: a cached copy) asks for its contents\n"
    "// as RGBA8 rows, in the target's own encoding; false leaves it as it is.\n"
    "// Bumping g_upload_filter_generation makes every tagged image and derived target ask again at its next use.\n"
    "// Null hooks (the default): nothing changes.\n"
    "using UploadFilter = uint64_t (*)(const Texture& t, uint8_t* level0, uint64_t size);\n"
    "using DerivedFilter = bool (*)(uint64_t tag, uint32_t width, uint32_t height, std::vector<uint8_t>& rgba);\n"
    "extern std::atomic<UploadFilter> g_upload_filter;\n"
    "extern std::atomic<DerivedFilter> g_derived_filter;\n"
    "extern std::atomic<uint64_t> g_upload_filter_generation;\n")

UPLOAD_NS_ANCHOR = "namespace nx::nvn {\n\nusing namespace nx::vk;\n"
UPLOAD_NS_NEW = UPLOAD_NS_ANCHOR + ("\nstd::atomic<UploadFilter> g_upload_filter{nullptr};  // AP_HUD_GLYPHS (nvn.h)\n"
                                    "std::atomic<DerivedFilter> g_derived_filter{nullptr};\n"
                                    "std::atomic<uint64_t> g_upload_filter_generation{1};\n")

UPLOAD_CALL_ANCHOR = ('        log("nvn/vk: texture %ux%u format %#x: could not untile (%llu bytes)", img.width, img.height, t.format,\n'
                      "            (unsigned long long)t.storage_size);\n"
                      "        return;\n"
                      "    }\n")
UPLOAD_CALL_NEW = UPLOAD_CALL_ANCHOR + (
    "    if (UploadFilter f = g_upload_filter.load(std::memory_order_acquire)) {  // AP_HUD_GLYPHS: a game's own contents\n"
    "        img.filter_generation = g_upload_filter_generation.load(std::memory_order_acquire);\n"
    "        img.filter_tag = f(t, linear.data() + levels[0].offset, levels[0].layer_size);\n"
    "        img.filter_derived = false;\n"
    "    }\n")

VK_H_ANCHOR = "    bool uploaded = false;  // filled from the texture's pool memory\n"
VK_H_NEW = VK_H_ANCHOR + (
    "    // AP_HUD_GLYPHS: contents a game hook chose (nvn.h): its tag (0: none), whether this is a render target drawn\n"
    "    // from a tagged texture, and the generation they belong to\n"
    "    uint64_t filter_tag = 0;\n"
    "    bool filter_derived = false;\n"
    "    uint64_t filter_generation = 0;\n")

READY_ANCHOR = "    const GpuImage& img = *t->image;\n    bool ok = img.rendered"
READY_NEW = ("    const GpuImage& img = *t->image;\n"
             "    if (img.filter_tag && img.filter_generation != g_upload_filter_generation.load(std::memory_order_acquire))\n"
             "        return g_not_ready[1]++, false;  // AP_HUD_GLYPHS: new contents to take first (ensure_contents)\n"
             "    bool ok = img.rendered")

REWRITE_ANCHOR = "GpuImage* ensure_contents(VkCommandBuffer cb, uint64_t texture) {\n"
REWRITE_NEW = (
    "// AP_HUD_GLYPHS: a render target the game drew from a tagged texture takes the game's new contents (DerivedFilter).\n"
    "void rewrite_derived(VkCommandBuffer cb, GpuImage& img, uint64_t generation) {\n"
    "    img.filter_generation = generation;\n"
    "    DerivedFilter f = g_derived_filter.load(std::memory_order_acquire);\n"
    "    if (!f || img.layers != 1 || (img.format != VK_FORMAT_R8G8B8A8_SRGB && img.format != VK_FORMAT_R8G8B8A8_UNORM)) return;\n"
    "    std::vector<uint8_t> rgba;\n"
    "    if (!f(img.filter_tag, img.width, img.height, rgba) || rgba.size() != size_t(img.width) * img.height * 4) return;\n"
    "    VkImageLayout back = img.layout;\n"
    "    transition(cb, img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);\n"
    "    Upload u = upload(rgba.data(), rgba.size(), 16);\n"
    "    VkBufferImageCopy r{};\n"
    "    r.bufferOffset = u.offset;\n"
    "    r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};\n"
    "    r.imageExtent = {img.width, img.height, 1};\n"
    "    p_vkCmdCopyBufferToImage(cb, u.buffer, img.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);\n"
    "    transition(cb, img, back != VK_IMAGE_LAYOUT_UNDEFINED ? back : VK_IMAGE_LAYOUT_GENERAL);\n"
    "}\n"
    "\n" + REWRITE_ANCHOR)

ENSURE_ANCHOR = "    bool upload = !img->rendered && (!img->uploaded || (dirty && (!img->texels || reupload)));\n"
ENSURE_NEW = ENSURE_ANCHOR + (
    "    // AP_HUD_GLYPHS: new contents asked for (a new generation): a tagged texture goes up again (the filter runs again),\n"
    "    // a derived render target is rewritten; whatever texture object samples the image now.\n"
    "    if (img->filter_tag) {\n"
    "        const uint64_t generation = g_upload_filter_generation.load(std::memory_order_acquire);\n"
    "        if (img->filter_generation != generation) {\n"
    "            if (!img->filter_derived && !img->rendered) upload = true;\n"
    "            else if (img->filter_derived && img->rendered) rewrite_derived(cb, *img, generation);\n"
    "            else img->filter_generation = generation;\n"
    "        }\n"
    "    }\n")

DRAW_ANCHOR = "    g_stats.done++;\n"
DRAW_NEW = DRAW_ANCHOR + (
    "    // AP_HUD_GLYPHS: drawing a tagged texture into a target of its size (the game's cached copy) makes that target\n"
    "    // derived: it follows the game's hook from then on (rewrite_derived, nvn_pipeline.cpp).\n"
    "    if (t.color_count && t.colors[0])\n"
    "        for (const ResolvedTexture& r : g_textures.resolved) {\n"
    "            GpuImage* src = r.texture ? image_of(r.texture) : nullptr;\n"
    "            if (!src || !src->filter_tag || src->filter_derived) continue;\n"
    "            GpuImage* dst = image_of(t.colors[0]);\n"
    "            if (!dst || dst == src || dst->width != src->width || dst->height != src->height ||\n"
    "                (dst->filter_tag && !dst->filter_derived))\n"
    "                break;\n"
    "            // generation 0: rewritten at its first use (the game may have drawn the texture as it came from the romfs)\n"
    "            dst->filter_tag = src->filter_tag, dst->filter_derived = true, dst->filter_generation = 0;\n"
    "            static int derived = 0;\n"
    "            if (derived++ < 8)\n"
    "                log(\"nvn: render target %llx (%ux%u) drawn from a game-chosen texture (tag %016llx): it follows the game\",\n"
    "                    (unsigned long long)t.colors[0], dst->width, dst->height, (unsigned long long)src->filter_tag);\n"
    "            static const bool dump = std::getenv(\"NX_HUD_DUMP\") != nullptr;  // with NX_SNAP_RAW=1: frames/00000_d*.raw\n"
    "            if (dump && derived <= 8) draw_end_rendering(cb, 6), debug_snapshot(cb, *dst, 0xD0000000ull + derived);\n"
    "            break;\n"
    "        }\n")

DEBUG_ANCHOR = "                img.format == VK_FORMAT_R32_UINT || img.format == VK_FORMAT_R8G8B8A8_UNORM || wide)) {\n"
DEBUG_NEW = ("                img.format == VK_FORMAT_R32_UINT || img.format == VK_FORMAT_R8G8B8A8_UNORM ||\n"
             "                img.format == VK_FORMAT_R8G8B8A8_SRGB /* AP_HUD_GLYPHS: NX_HUD_DUMP */ || wide)) {\n")

# ---- input: which device gave the last real input, and the key of each button -----------------------------------------
INPUT_H_ANCHOR = "const Target& mouse_target(MouseSource s);\n"
INPUT_H_NEW = INPUT_H_ANCHOR + (
    "\n"
    "// AP_HUD_GLYPHS: la primera tecla (codigo VK de Windows) que input.ini, o el mapeo por defecto, le da a ese boton\n"
    "// del Npad (el mismo nombre que en input.ini: A, UP, SUP...); 0 si ninguna. Para los iconos del HUD (hud_glyphs.cpp).\n"
    "int first_key(const std::string& npad_name);\n")

INPUT_INC_ANCHOR = '#include "pad_sdl.h"  // AP_SDL_PAD\n'
INPUT_INC_NEW = INPUT_INC_ANCHOR + '#include "hud_glyphs.h"  // AP_HUD_GLYPHS\n'

FIRST_KEY_ANCHOR = "const Target& mouse_target(MouseSource s) { return config().mouse[s]; }\n"
FIRST_KEY_NEW = FIRST_KEY_ANCHOR + r'''
// AP_HUD_GLYPHS: la primera linea de [teclado] (en orden) cuyo boton es exactamente ese.
int first_key(const std::string& npad_name) {
    Target want;
    if (!parse_target(npad_name, want)) return 0;
    for (const KeyBind& k : config().keys)
        if (k.t.bits == want.bits && k.t.lx == want.lx && k.t.ly == want.ly && k.t.rx == want.rx && k.t.ry == want.ry)
            return k.vk;
    return 0;
}
'''

XINPUT_ANCHOR = ("    XINPUT_GAMEPAD g{};\n"
                 "    if (!read_xinput(g)) return false;\n")
XINPUT_NEW = XINPUT_ANCHOR + r'''#ifdef NX_HUD_GLYPHS
    {   // AP_HUD_GLYPHS: un boton nuevo, o un stick o gatillo pasado la mitad, es entrada real del mando (Xbox)
        static uint16_t antes = 0;
        const uint16_t nuevos = uint16_t(g.wButtons & ~antes);
        antes = g.wButtons;
        auto mitad = [](int v) { return v > 16384 || v < -16384; };
        if (nuevos || mitad(g.sThumbLX) || mitad(g.sThumbLY) || mitad(g.sThumbRX) || mitad(g.sThumbRY) ||
            g.bLeftTrigger > 100 || g.bRightTrigger > 100)
            hud::note_input(hud::Device::kXbox);
    }
#endif
'''

KEYS_OLD = ("        for (const KeyBind& k : c.keys)\n"
            "            if (!(alt && k.vk == kVkReturn) && (GetAsyncKeyState(k.vk) & 0x8000)) press(p, k.t);\n")
KEYS_NEW = ("        for (const KeyBind& k : c.keys)\n"
            "            if (!(alt && k.vk == kVkReturn) && (GetAsyncKeyState(k.vk) & 0x8000)) {\n"
            "                press(p, k.t);\n"
            "#ifdef NX_HUD_GLYPHS\n"
            "                if (!(teclas_antes[k.vk & 0xFF] & 1)) hud::note_input(hud::Device::kKeyboard);  // AP_HUD_GLYPHS\n"
            "                teclas_ahora[k.vk & 0xFF] = 1;\n"
            "#endif\n"
            "            }\n")
KEYS_PRE_OLD = ("    if (g_window && GetForegroundWindow() == g_window) {\n"
                "        const bool alt = GetAsyncKeyState(kVkMenu) & 0x8000;  // Alt+Enter: pantalla completa, no el boton de Enter\n")
KEYS_PRE_NEW = ("#ifdef NX_HUD_GLYPHS\n"
                "    // AP_HUD_GLYPHS: una tecla asignada que se aprieta ahora (no mientras se mantiene) es entrada del teclado\n"
                "    static uint8_t teclas_antes[256];\n"
                "    uint8_t teclas_ahora[256] = {};\n"
                "    struct Guardar {\n"
                "        uint8_t* a;\n"
                "        const uint8_t* b;\n"
                "        ~Guardar() { std::memcpy(a, b, 256); }\n"
                "    } guardar{teclas_antes, teclas_ahora};\n"
                "#endif\n" + KEYS_PRE_OLD)

CSTRING_ANCHOR = "#include <cctype>\n#include <cstdlib>\n#include <filesystem>\n"
CSTRING_NEW = "#include <cctype>\n#include <cstdlib>\n#include <cstring>  // AP_HUD_GLYPHS\n#include <filesystem>\n"

# ---- SDL pads -----------------------------------------------------------------------------------------------------------
SDL_INC_ANCHOR = '#include "pad_sdl.h"\n'
SDL_INC_NEW = SDL_INC_ANCHOR + '#include "hud_glyphs.h"  // AP_HUD_GLYPHS\n'

SDL_BTN_OLD = "        case SDL_EVENT_GAMEPAD_BUTTON_DOWN: usar(e.gbutton.which); break;\n"
SDL_BTN_NEW = "        case SDL_EVENT_GAMEPAD_BUTTON_DOWN: usar(e.gbutton.which), nota(e.gbutton.which); break;  // AP_HUD_GLYPHS\n"
SDL_AXIS_OLD = "            if (gat ? v > kGatilloActivo : (v > kStickActivo || v < -kStickActivo)) usar(e.gaxis.which);\n"
SDL_AXIS_NEW = ("            if (gat ? v > kGatilloActivo : (v > kStickActivo || v < -kStickActivo))\n"
                "                usar(e.gaxis.which), nota(e.gaxis.which);  // AP_HUD_GLYPHS\n")
SDL_NOTA_ANCHOR = "    void usar(SDL_JoystickID id) {\n"
SDL_NOTA_NEW = r'''    // AP_HUD_GLYPHS: entrada real de este mando, para los iconos del HUD segun su tipo (por posicion; un mando
    // generico o desconocido muestra los de Xbox).
    void nota(SDL_JoystickID id) {
#ifdef NX_HUD_GLYPHS
        SDL_Gamepad* gp = buscar(id);
        if (!gp) return;
        switch (SDL_GetGamepadType(gp)) {
        case SDL_GAMEPAD_TYPE_PS3:
        case SDL_GAMEPAD_TYPE_PS4:
        case SDL_GAMEPAD_TYPE_PS5: hud::note_input(hud::Device::kPs); break;
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_PRO:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_LEFT:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_RIGHT:
        case SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_JOYCON_PAIR: hud::note_input(hud::Device::kSwitch); break;
        default: hud::note_input(hud::Device::kXbox); break;
        }
#else
        (void)id;
#endif
    }

''' + SDL_NOTA_ANCHOR

# ---- mouse --------------------------------------------------------------------------------------------------------------
HID_INC_ANCHOR = '#include "input_tsuki.h"\n'
HID_INC_NEW = HID_INC_ANCHOR + '#include "hud_glyphs.h"  // AP_HUD_GLYPHS\n'
HID_MOUSE_ANCHOR = ("    // Derecho y medio: apretados mientras se mantienen, y al menos 80 ms desde el clic (un clic mas corto que un\n"
                    "    // frame igual se ve).\n")
HID_MOUSE_NEW = r'''#ifdef NX_HUD_GLYPHS
    {   // AP_HUD_GLYPHS: un clic de cualquier boton (tambien el tactil) o la rueda es entrada del teclado (el mouse
        // muestra los iconos del teclado); moverlo no cuenta.
        static uint32_t hud_downs[3] = {ptr.downs[0], ptr.downs[1], ptr.downs[2]};
        static int32_t hud_wheel = ptr.wheel;
        if (ptr.downs[0] != hud_downs[0] || ptr.downs[1] != hud_downs[1] || ptr.downs[2] != hud_downs[2] ||
            ptr.wheel != hud_wheel)
            tsuki::hud::note_input(tsuki::hud::Device::kKeyboard);
        for (int k = 0; k < 3; k++) hud_downs[k] = ptr.downs[k];
        hud_wheel = ptr.wheel;
    }
#endif
''' + HID_MOUSE_ANCHOR

# ---- build --------------------------------------------------------------------------------------------------------------
CMAKE_ANCHOR = "target_compile_definitions(${NX_GAME} PRIVATE NX_PACKAGED_LAYOUT_HLE)\n"
CMAKE_NEW = CMAKE_ANCHOR + (
    "# AP_HUD_GLYPHS (apply_hud_glyphs.py): button icons of the HUD follow the last device used (hud_glyphs.cpp);\n"
    "# its files go to <data root>/hud (tools/hud/build_hud_assets.py). NX_HUD_GLYPHS=off at run time turns it off.\n"
    "target_compile_definitions(${NX_GAME} PRIVATE NX_HUD_GLYPHS)\n")

EDITS = [
    ("runtime/src/nvn/nvn.h", NVN_H_ANCHOR, NVN_H_NEW),
    ("runtime/src/nvn/nvn_upload.cpp", UPLOAD_NS_ANCHOR, UPLOAD_NS_NEW),
    ("runtime/src/nvn/nvn_upload.cpp", UPLOAD_CALL_ANCHOR, UPLOAD_CALL_NEW),
    ("runtime/src/nvn/nvn_vk.h", VK_H_ANCHOR, VK_H_NEW),
    ("runtime/src/nvn/nvn_pipeline.cpp", READY_ANCHOR, READY_NEW),
    ("runtime/src/nvn/nvn_pipeline.cpp", REWRITE_ANCHOR, REWRITE_NEW),
    ("runtime/src/nvn/nvn_pipeline.cpp", ENSURE_ANCHOR, ENSURE_NEW),
    ("runtime/src/nvn/nvn_draw.cpp", DRAW_ANCHOR, DRAW_NEW),
    ("runtime/src/nvn/nvn_debug.cpp", DEBUG_ANCHOR, DEBUG_NEW),
    ("games/tsukihime_common/input_tsuki.h", INPUT_H_ANCHOR, INPUT_H_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", CSTRING_ANCHOR, CSTRING_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", INPUT_INC_ANCHOR, INPUT_INC_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", FIRST_KEY_ANCHOR, FIRST_KEY_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", XINPUT_ANCHOR, XINPUT_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", KEYS_PRE_OLD, KEYS_PRE_NEW),
    ("games/tsukihime_common/input_tsuki.cpp", KEYS_OLD, KEYS_NEW),
    ("games/tsukihime_common/pad_sdl.cpp", SDL_INC_ANCHOR, SDL_INC_NEW),
    ("games/tsukihime_common/pad_sdl.cpp", SDL_BTN_OLD, SDL_BTN_NEW),
    ("games/tsukihime_common/pad_sdl.cpp", SDL_AXIS_OLD, SDL_AXIS_NEW),
    ("games/tsukihime_common/pad_sdl.cpp", SDL_NOTA_ANCHOR, SDL_NOTA_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_INC_ANCHOR, HID_INC_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_MOUSE_ANCHOR, HID_MOUSE_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
