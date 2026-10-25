#!/usr/bin/env python3
"""apply_srgb_present.py - sRGB swapchain when the game's window texture is sRGB.

Symptom (Tsukihime): the whole game looks darker than on the console, evenly.
Likely cause: the swapchain is created B8G8R8A8_UNORM (format 44). If the game's window texture is sRGB,
vkCmdBlitImage decodes sRGB -> linear and writes that linear value as is into the UNORM: the image ends up dark.
With an sRGB swapchain the blit encodes again and the values reach the screen intact.

Change (nvn_vk.cpp): before creating the swapchain for the first time, the format of the texture the game
presents is checked; it is logged ("window texture format N") and, if it is sRGB and the surface offers the sRGB
variant of the chosen format, the swapchain is created with it. NX_SRGB_SWAPCHAIN=0 disables it (only the log stays).
Wonder: if its window texture is not sRGB, nothing changes.

Usage: python apply_srgb_present.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent; all or nothing; keeps CRLF.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
PATH = os.path.join(ROOT, "runtime", "src", "nvn", "nvn_vk.cpp")
MARK = "pick_swapchain_format"

HELPER = r'''// The swapchain's format from the window texture the game presents (apply_srgb_present.py): a blit from an sRGB
// image decodes to linear, and into a UNORM swapchain that linear value was shown as is (the whole picture darker).
// An sRGB swapchain encodes it again. NX_SRGB_SWAPCHAIN=0: only the log line.
static void pick_swapchain_format(const GpuImage* src) {
    static bool done = false;
    if (done || !src) return;
    done = true;
    VkFormat want = VK_FORMAT_UNDEFINED;
    switch (src->format) {
        case VK_FORMAT_R8G8B8A8_SRGB:
        case VK_FORMAT_B8G8R8A8_SRGB:
        case VK_FORMAT_A8B8G8R8_SRGB_PACK32:
            want = g_sc_format == VK_FORMAT_R8G8B8A8_UNORM ? VK_FORMAT_R8G8B8A8_SRGB : VK_FORMAT_B8G8R8A8_SRGB;
            break;
        default: break;
    }
    log("nvn/vk: window texture format %d (%s), swapchain format %d", int(src->format),
        want ? "sRGB" : "not sRGB", int(g_sc_format));
    const char* e = std::getenv("NX_SRGB_SWAPCHAIN");
    if (!want || (e && e[0] == '0')) return;
    uint32_t n = 0;
    p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &n, nullptr);
    std::vector<VkSurfaceFormatKHR> formats(n);
    p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &n, formats.data());
    for (const VkSurfaceFormatKHR& f : formats)
        if (f.format == want) {
            g_sc_format = want;
            log("nvn/vk: sRGB swapchain (format %d) for the sRGB window texture", int(want));
            return;
        }
    log("nvn/vk: the surface offers no sRGB format %d: the picture stays darker", int(want));
}

'''

EDITS = [
    ("void gpu_present(uint64_t texture, const int32_t* crop) {\n",
     HELPER + "void gpu_present(uint64_t texture, const int32_t* crop) {\n"),
    ("    if (!g_swapchain && !make_swapchain()) return;\n",
     "    if (!g_swapchain) pick_swapchain_format(image_of(texture));  // apply_srgb_present.py\n"
     "    if (!g_swapchain && !make_swapchain()) return;\n"),
]


def main():
    raw = open(PATH, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if MARK in text:
        print("nvn_vk.cpp: already applied")
        return
    for old, new in EDITS:
        n = text.count(old)
        if n != 1:
            sys.exit(f"nvn_vk.cpp: 1 match was expected and there are {n} for:\n{old}\nNothing was changed.")
        text = text.replace(old, new)
    with open(PATH, "wb") as f:
        f.write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print(f"nvn_vk.cpp: {len(EDITS)} change(s). Rebuild: cmake --build build\\tsukihime")


if __name__ == "__main__":
    main()