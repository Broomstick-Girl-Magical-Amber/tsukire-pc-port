"""apply_aspect.py - picture with its aspect (black bars) and a window that only scales in that aspect.

Changes (framework, runtime/src):
  platform.h       plat::PictureRect + plat::picture_rect(cw, ch): where the picture sits in the client area, the
                   same rectangle the blit uses. So the game converts the mouse without computing it by itself.
  nvn_vk.cpp       keeps the size of what was presented (window texture or its crop) and defines picture_rect().
                   With NX_FIXED_ASPECT: the blit clears to black and draws in that rectangle (letterbox / pillarbox).
  platform_win.cpp with NX_FIXED_ASPECT: WM_SIZING locks the aspect (NX_RESOLUTION, 16:9 by default),
                   WM_GETMINMAXINFO sets a 640 px minimum width, Ctrl+1/2/3 = 50/75/100 % of 1920 px width.

Per-game option: target_compile_definitions(${NX_GAME} PRIVATE NX_FIXED_ASPECT) in games/<game>/game.cmake.
Without it (Wonder) the blit and the window stay as always; the only thing that runs is keeping two integers per frame,
and picture_rect() returns the whole client area (which is what the blit does when stretching). With the present pass
(NX_PRESENT_PASS=1 / NX_FSR=1, which already adds bars) picture_rect() returns the rectangle with bars.

Requires apply_mouse.py (anchor in wndproc).
Usage:  python apply_aspect.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_ASPECT), all or nothing, keeps CRLF, refuses if an anchor is not found.
"""
from _patch import run

MARK = "AP_ASPECT"
NVN = "runtime/src/**/nvn_vk.cpp"

EDITS = [
    # ---- platform.h ------------------------------------------------------------------------------------------
    ("runtime/src/platform.h",
     "void screen_size(uint32_t& w, uint32_t& h);  // the display's own size, landscape (Android; the window on PC)\n",
     "void screen_size(uint32_t& w, uint32_t& h);  // the display's own size, landscape (Android; the window on PC)\n"
     "// AP_ASPECT (apply_aspect.py, nvn_vk.cpp): where the presented picture sits in a client area of cw x ch pixels,\n"
     "// the same rectangle the blit (or the present pass) draws it into. NX_FIXED_ASPECT (games/<juego>/game.cmake): the\n"
     "// picture keeps its aspect, centred with black bars; without it the blit stretches it over the whole client area.\n"
     "struct PictureRect {\n"
     "    int32_t x = 0, y = 0;\n"
     "    uint32_t w = 0, h = 0;\n"
     "};\n"
     "PictureRect picture_rect(uint32_t cw, uint32_t ch);\n"),

    # ---- nvn_vk.cpp ------------------------------------------------------------------------------------------
    (NVN,
     "#include <algorithm>\n"
     "#include <cstdio>\n",
     "#include <algorithm>\n"
     "#include <atomic>  // AP_ASPECT\n"
     "#include <cstdio>\n"),

    (NVN,
     "// NX_ORIGIN_LOWER_LEFT=1: NVN's default lower-left window origin",
     "// AP_ASPECT (apply_aspect.py, platform.h picture_rect): the size of what was last presented (the window\n"
     "// texture, or its crop), and whether it is shown with its aspect: NX_FIXED_ASPECT, or the present pass (it fits\n"
     "// with bars, nvn_present.cpp). Otherwise the blit stretches it over the whole swapchain.\n"
     "static std::atomic<uint32_t> g_pic_w{0}, g_pic_h{0};\n"
     "static bool picture_keeps_aspect() {\n"
     "#ifdef NX_FIXED_ASPECT\n"
     "    return true;\n"
     "#else\n"
     "    return present_pass_wanted() && !present_pass_failed();\n"
     "#endif\n"
     "}\n"
     "\n"
     "// NX_ORIGIN_LOWER_LEFT=1: NVN's default lower-left window origin"),

    (NVN,
     "    bool drawn = src && present_pass_record(g_cb, *src, crop, index);  // nvn_present.cpp: leaves dst PRESENT_SRC\n",
     "    if (src) {  // AP_ASPECT: the picture's size, for picture_rect (the same crop test as below)\n"
     "        const bool c = crop && crop[2] > 0 && crop[3] > 0 && crop[0] + crop[2] <= int32_t(src->width) &&\n"
     "                       crop[1] + crop[3] <= int32_t(src->height);\n"
     "        g_pic_w = c ? uint32_t(crop[2]) : src->width;\n"
     "        g_pic_h = c ? uint32_t(crop[3]) : src->height;\n"
     "    }\n"
     "    bool drawn = src && present_pass_record(g_cb, *src, crop, index);  // nvn_present.cpp: leaves dst PRESENT_SRC\n"),

    (NVN,
     "        blit.dstOffsets[1] = {int32_t(g_sc_extent.width), int32_t(g_sc_extent.height), 1};\n",
     "        blit.dstOffsets[1] = {int32_t(g_sc_extent.width), int32_t(g_sc_extent.height), 1};\n"
     "#ifdef NX_FIXED_ASPECT  // AP_ASPECT (apply_aspect.py): the picture keeps its aspect, black bars around it\n"
     "        if (const plat::PictureRect pr = plat::picture_rect(g_sc_extent.width, g_sc_extent.height);\n"
     "            pr.w && pr.h && (pr.w != g_sc_extent.width || pr.h != g_sc_extent.height)) {\n"
     "            VkClearColorValue black{{0.0f, 0.0f, 0.0f, 1.0f}};\n"
     "            VkImageSubresourceRange rr{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};\n"
     "            p_vkCmdClearColorImage(g_cb, dst.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &black, 1, &rr);\n"
     "            VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER};  // the clear before the blit's write\n"
     "            mb.srcAccessMask = mb.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
     "            p_vkCmdPipelineBarrier(g_cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0,\n"
     "                                   nullptr, 0, nullptr);\n"
     "            blit.dstOffsets[0] = {pr.x, pr.y, 0};\n"
     "            blit.dstOffsets[1] = {pr.x + int32_t(pr.w), pr.y + int32_t(pr.h), 1};\n"
     "        }\n"
     "#endif\n"),

    (NVN,
     "}  // namespace nx::nvn\n",
     "}  // namespace nx::nvn\n"
     "\n"
     "namespace nx::plat {\n"
     "\n"
     "// AP_ASPECT (platform.h): the rectangle the picture is presented into, for a client area of cw x ch pixels.\n"
     "PictureRect picture_rect(uint32_t cw, uint32_t ch) {\n"
     "    PictureRect r;\n"
     "    r.w = cw, r.h = ch;\n"
     "    if (!cw || !ch || !nvn::picture_keeps_aspect()) return r;\n"
     "    uint64_t pw = nvn::g_pic_w, ph = nvn::g_pic_h;\n"
     "    if (!pw || !ph) {  // nothing presented yet: the aspect the window was made in (NX_RESOLUTION, default 16:9)\n"
     "        unsigned rw = 16, rh = 9;\n"
     "        if (const char* e = std::getenv(\"NX_RESOLUTION\")) std::sscanf(e, \"%ux%u\", &rw, &rh);\n"
     "        pw = std::max(1u, rw), ph = std::max(1u, rh);\n"
     "    }\n"
     "    if (uint64_t(cw) * ph > uint64_t(ch) * pw) {  // wider than the picture: bars left and right\n"
     "        r.w = uint32_t((uint64_t(ch) * pw + ph / 2) / ph);\n"
     "        r.x = int32_t((cw - r.w) / 2);\n"
     "    } else {  // taller: bars above and below\n"
     "        r.h = uint32_t((uint64_t(cw) * ph + pw / 2) / pw);\n"
     "        r.y = int32_t((ch - r.h) / 2);\n"
     "    }\n"
     "    return r;\n"
     "}\n"
     "\n"
     "}  // namespace nx::plat\n"),

    # ---- platform_win.cpp ------------------------------------------------------------------------------------
    ("runtime/src/platform_win.cpp",
     "// Mouse (AP_MOUSE, platform.h PointerState): written by the window thread",
     "// AP_ASPECT (apply_aspect.py): with NX_FIXED_ASPECT (games/<juego>/game.cmake) the client area keeps the\n"
     "// picture's aspect (NX_RESOLUTION, default 16:9) while the window is resized, is at least 640 px wide, and\n"
     "// Ctrl+1 / Ctrl+2 / Ctrl+3 set it to 50% / 75% / 100% of 1920 px wide (within the screen's work area).\n"
     "static unsigned g_aspect_w = 16, g_aspect_h = 9;\n"
     "#ifdef NX_FIXED_ASPECT\n"
     "static constexpr int kMinClientW = 640;\n"
     "\n"
     "static int aspect_h(int cw) { return int((int64_t(cw) * g_aspect_h + g_aspect_w / 2) / g_aspect_w); }\n"
     "static int aspect_w(int ch) { return int((int64_t(ch) * g_aspect_w + g_aspect_h / 2) / g_aspect_h); }\n"
     "\n"
     "static SIZE frame_extra(HWND h) {  // what the borders and the title bar add to the client size\n"
     "    RECT r{0, 0, 0, 0};\n"
     "    AdjustWindowRectExForDpi(&r, DWORD(GetWindowLongW(h, GWL_STYLE)), FALSE, DWORD(GetWindowLongW(h, GWL_EXSTYLE)),\n"
     "                             GetDpiForWindow(h));\n"
     "    return {r.right - r.left, r.bottom - r.top};\n"
     "}\n"
     "\n"
     "// WM_SIZING: the dragged rectangle in the picture's aspect, the side opposite the dragged one staying put.\n"
     "static void keep_aspect(HWND h, WPARAM edge, RECT* r) {\n"
     "    const SIZE f = frame_extra(h);\n"
     "    int cw = std::max(1, int(r->right - r->left - f.cx)), ch = std::max(1, int(r->bottom - r->top - f.cy));\n"
     "    if (edge == WMSZ_TOP || edge == WMSZ_BOTTOM)\n"
     "        cw = aspect_w(ch);\n"
     "    else if (edge == WMSZ_LEFT || edge == WMSZ_RIGHT)\n"
     "        ch = aspect_h(cw);\n"
     "    else if (int64_t(cw) * g_aspect_h >= int64_t(ch) * g_aspect_w)  // a corner: the longer side leads\n"
     "        ch = aspect_h(cw);\n"
     "    else\n"
     "        cw = aspect_w(ch);\n"
     "    if (cw < kMinClientW) cw = kMinClientW, ch = aspect_h(cw);\n"
     "    const int ww = cw + f.cx, wh = ch + f.cy;\n"
     "    if (edge == WMSZ_LEFT || edge == WMSZ_TOPLEFT || edge == WMSZ_BOTTOMLEFT)\n"
     "        r->left = r->right - ww;\n"
     "    else\n"
     "        r->right = r->left + ww;\n"
     "    if (edge == WMSZ_TOP || edge == WMSZ_TOPLEFT || edge == WMSZ_TOPRIGHT)\n"
     "        r->top = r->bottom - wh;\n"
     "    else\n"
     "        r->bottom = r->top + wh;\n"
     "}\n"
     "\n"
     "// Ctrl+1/2/3: the client area at pct% of 1920 px wide, centred where the window was, inside the work area.\n"
     "static void set_client_width(HWND h, int pct) {\n"
     "    if (g_fullscreen) return;\n"
     "    if (IsZoomed(h)) ShowWindow(h, SW_RESTORE);\n"
     "    MONITORINFO mi{sizeof mi};\n"
     "    GetMonitorInfoW(MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), &mi);\n"
     "    const RECT wa = mi.rcWork;\n"
     "    const SIZE f = frame_extra(h);\n"
     "    int cw = 1920 * pct / 100, ch = aspect_h(cw);\n"
     "    if (cw + f.cx > wa.right - wa.left) cw = wa.right - wa.left - f.cx, ch = aspect_h(cw);\n"
     "    if (ch + f.cy > wa.bottom - wa.top) ch = wa.bottom - wa.top - f.cy, cw = aspect_w(ch);\n"
     "    if (cw < kMinClientW) cw = kMinClientW, ch = aspect_h(cw);\n"
     "    RECT cur{};\n"
     "    GetWindowRect(h, &cur);\n"
     "    const int ww = cw + f.cx, wh = ch + f.cy;\n"
     "    int x = (cur.left + cur.right - ww) / 2, y = (cur.top + cur.bottom - wh) / 2;\n"
     "    x = std::max(int(wa.left), std::min(x, int(wa.right) - ww));\n"
     "    y = std::max(int(wa.top), std::min(y, int(wa.bottom) - wh));\n"
     "    SetWindowPos(h, nullptr, x, y, ww, wh, SWP_NOZORDER | SWP_NOACTIVATE);\n"
     "    log(\"window: client %dx%d (%d%% of 1920 wide)\", cw, ch, pct);\n"
     "}\n"
     "#endif\n"
     "\n"
     "// Mouse (AP_MOUSE, platform.h PointerState): written by the window thread"),

    ("runtime/src/platform_win.cpp",
     "        case WM_CLOSE:\n"
     "            log(\"window closed\");\n",
     "#ifdef NX_FIXED_ASPECT  // AP_ASPECT\n"
     "        case WM_SIZING:\n"
     "            keep_aspect(h, w, reinterpret_cast<RECT*>(l));\n"
     "            return TRUE;\n"
     "        case WM_GETMINMAXINFO:\n"
     "            if (!g_fullscreen) {\n"
     "                const SIZE f = frame_extra(h);\n"
     "                reinterpret_cast<MINMAXINFO*>(l)->ptMinTrackSize = {kMinClientW + f.cx, aspect_h(kMinClientW) + f.cy};\n"
     "            }\n"
     "            return 0;\n"
     "#endif\n"
     "        case WM_CLOSE:\n"
     "            log(\"window closed\");\n"),

    ("runtime/src/platform_win.cpp",
     "            if (w == VK_F11) toggle_fullscreen(h);\n",
     "            if (w == VK_F11) toggle_fullscreen(h);\n"
     "#ifdef NX_FIXED_ASPECT  // AP_ASPECT: Ctrl+1/2/3, the window at 50/75/100% of 1920 px wide\n"
     "            if (w >= '1' && w <= '3' && GetKeyState(VK_CONTROL) < 0)\n"
     "                set_client_width(h, w == '1' ? 50 : w == '2' ? 75 : 100);\n"
     "#endif\n"),

    ("runtime/src/platform_win.cpp",
     "    if (const char* r = std::getenv(\"NX_RESOLUTION\")) std::sscanf(r, \"%ux%u\", &rw, &rh);\n",
     "    if (const char* r = std::getenv(\"NX_RESOLUTION\")) std::sscanf(r, \"%ux%u\", &rw, &rh);\n"
     "    g_aspect_w = std::max(1u, rw), g_aspect_h = std::max(1u, rh);  // AP_ASPECT\n"),
]


def check_barrier():
    """The blit with bars uses p_vkCmdPipelineBarrier: if the framework does not have it, do not patch (broken build)."""
    import argparse, glob, os, sys
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--root", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework"))
    ap.add_argument("--remove", action="store_true")
    a, _ = ap.parse_known_args()
    if a.remove:
        return
    for f in glob.glob(os.path.join(a.root, "runtime", "src", "**", "*.*"), recursive=True):
        if f.endswith((".h", ".cpp", ".inc")):
            with open(f, "rb") as fh:
                if b"p_vkCmdPipelineBarrier" in fh.read():
                    return
    sys.exit("ERROR: cannot find p_vkCmdPipelineBarrier in runtime/src (the blit with bars needs it). Nothing touched.")


if __name__ == "__main__":
    check_barrier()
    run(MARK, EDITS, __doc__)
