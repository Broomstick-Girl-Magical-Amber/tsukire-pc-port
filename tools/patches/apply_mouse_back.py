"""apply_mouse_back.py - the right mouse button goes back (B) inside a submenu (AP_MOUSE_BACK).

Problem: with the FSN-style input.ini the right button is Y (the system menu). In a submenu (Save, Load, Config,
flowchart, backlog) Y does nothing, so the mouse could not go back; changing the right button to B fixed that but lost
the system menu in the text.

Signal (measured in nx.log with a temporary diagnostic): the help bar at the bottom of the screen is drawn from the HUD's
help_key atlas, which the HUD patch tags, and it is on screen in every submenu above and in none of the text, the
system menu, the mode panel or the title menu. A draw that samples a tagged texture records the frame; the right
button asks whether that was in the last 3 presents.

What it does (Windows; Android has no mouse):
  - runtime/src/nvn/nvn_draw.cpp: g_help_bar_frame (the last frame a draw sampled a tagged texture), the C function
    nx_help_bar_visible() and one small scan before each draw;
  - games/tsukihime_common/hle_hid_tsuki.cpp: when the right button goes down, if input.ini / NX_MOUSE_RIGHT gives it
    exactly Y and the bar is visible, it presses B instead of Y; the choice is kept while the button is down. Any other
    assignment of the right button is left as it is; without the tag (NX_HUD_GLYPHS off, or a help_key the HUD does not
    know) nothing changes. NX_MOUSE_BACK=0 turns it off (the scan too). With NX_INPUT_TRACE=1 each case logs
    "hid: clic derecho -> B (barra de ayuda visible)";
  - games/tsukihime_common/input.ini.example: the comment that told to change the right button to B by hand;
  - ../tools/recomp_tsukihime.ps1: two rows in $Markers after the one of title_bg.cpp for the title click patch (nobody
    anchors on it).

Idempotent (marker AP_MOUSE_BACK), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. Its anchors are not the ones of apply_hud_bar_diag.py (temporary): both can
be applied and removed in any order. After: cmake --build build\\tsukihime; the same with build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_MOUSE_BACK"

# ---- nvn_draw.cpp: the state and the function (after the replay namespace), the scan (before the draw call)
DEF_ANCHOR = "}  // namespace replay\n"
DEF_NEW = DEF_ANCHOR + r'''
// AP_MOUSE_BACK (apply_mouse_back.py): the last frame (+ 1) in which a draw sampled a texture the HUD patch tags: the
// help bar's atlas, on screen only while a submenu is open. hle_hid_tsuki.cpp asks nx_help_bar_visible(): with the bar up
// the right mouse button goes back (B) instead of opening the system menu (Y). NX_MOUSE_BACK=0 turns the scan off.
namespace {
std::atomic<uint64_t> g_help_bar_frame{0};
}  // namespace

extern "C" bool nx_help_bar_visible() {
    const uint64_t t = g_help_bar_frame.load(std::memory_order_relaxed);
    return t != 0 && g_frame.load(std::memory_order_relaxed) + 1 - t <= 3;
}
'''

SCAN_ANCHOR = ("    if (indexed) {\n"
               "        uint32_t count = uint32_t(c.i[0]);\n")
SCAN_NEW = '''    {   // AP_MOUSE_BACK: a draw that samples a tagged texture (the help bar) = a submenu is open
        static const bool scan = [] { const char* e = std::getenv("NX_MOUSE_BACK"); return !(e && e[0] == '0'); }();
        const uint64_t bar_now = g_frame.load(std::memory_order_relaxed) + 1;
        if (scan && g_help_bar_frame.load(std::memory_order_relaxed) != bar_now)
            for (const ResolvedTexture& r : g_textures.resolved) {
                GpuImage* s = r.texture ? image_of(r.texture) : nullptr;
                if (s && s->filter_tag) {
                    g_help_bar_frame.store(bar_now, std::memory_order_relaxed);
                    break;
                }
            }
    }
''' + SCAN_ANCHOR

# ---- hle_hid_tsuki.cpp: the helpers before mouse_pad, and the right button in its loop
HID_FN_ANCHOR = "void mouse_pad(plat::PadState& p) {\n"
HID_FN_NEW = '''extern "C" bool nx_help_bar_visible();  // AP_MOUSE_BACK (nvn_draw.cpp)

// AP_MOUSE_BACK: the right button, when input.ini gives it exactly Y (the system menu), goes back (B) while the help bar is
// on screen (a submenu is open); in the text and in the system menu itself it stays Y. NX_MOUSE_BACK=0: always as set.
const tsuki::Target& mouse_back_target() {
    static const tsuki::Target t = [] { tsuki::Target x; tsuki::parse_target("B", x); return x; }();
    return t;
}

bool mouse_back_wanted(const tsuki::Target& right) {
    static const bool on = env_on("NX_MOUSE_BACK", true);
    static const tsuki::Target y = [] { tsuki::Target x; tsuki::parse_target("Y", x); return x; }();
    if (!on || !y.any() || right.bits != y.bits || right.lx != y.lx || right.ly != y.ly || right.rx != y.rx ||
        right.ry != y.ry || !nx_help_bar_visible())
        return false;
    if (trace_on()) log("hid: clic derecho -> B (barra de ayuda visible)");
    return true;
}

''' + HID_FN_ANCHOR

HID_FOR_ANCHOR = "    for (int k = 1; k < 3; k++) {\n"
HID_FOR_NEW = "    static bool back_held = false, back_now = false;  // AP_MOUSE_BACK\n" + HID_FOR_ANCHOR

HID_PRESS_OLD = "        if ((ptr.buttons >> k & 1) || now < until[k]) tsuki::press(p, *side[k]);\n"
HID_PRESS_NEW = ("        const bool down = (ptr.buttons >> k & 1) || now < until[k];\n"
                 "        if (k == 1) {  // AP_MOUSE_BACK: decided when the right button goes down, kept while it is down\n"
                 "            if (down && !back_held) back_now = mouse_back_wanted(*side[1]);\n"
                 "            back_held = down;\n"
                 "        }\n"
                 "        if (down) tsuki::press(p, k == 1 && back_now ? mouse_back_target() : *side[k]);\n")

# ---- input.ini.example
INI_OLD = ("; wheel down = next, wheel up = previous. To go back in a submenu with the mouse, change the right button to B.\n")
INI_NEW = ("; wheel down = next, wheel up = previous. In a submenu (the help bar is on screen) a right button set to Y goes back (B)\n"
           "; by itself; NX_MOUSE_BACK=0 turns that off. A right button set to anything else is left as you set it (AP_MOUSE_BACK).\n")

# ---- recomp_tsukihime.ps1: after the last row of the title click patch (no patcher anchors on it)
MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_CLICK', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_draw.cpp', 'AP_MOUSE_BACK', 'apply_mouse_back.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_hid_tsuki.cpp', 'AP_MOUSE_BACK', 'apply_mouse_back.py', $false),\n")

EDITS = [
    ("runtime/src/nvn/nvn_draw.cpp", DEF_ANCHOR, DEF_NEW),
    ("runtime/src/nvn/nvn_draw.cpp", SCAN_ANCHOR, SCAN_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_FN_ANCHOR, HID_FN_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_FOR_ANCHOR, HID_FOR_NEW),
    ("games/tsukihime_common/hle_hid_tsuki.cpp", HID_PRESS_OLD, HID_PRESS_NEW),
    ("games/tsukihime_common/input.ini.example", INI_OLD, INI_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)