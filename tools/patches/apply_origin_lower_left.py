#!/usr/bin/env python3
"""apply_origin_lower_left.py - NVN lower-left window origin (the OpenGL one), with NX_ORIGIN_LOWER_LEFT=1.

The runtime emulates the upper-left origin that Wonder sets: negative-height viewport in every draw
(DEBUG.md #17), and the "y" of viewport/scissor counted from the top. Tsukihime never calls
nvnDeviceSetWindowOriginMode, so it runs with the default NVN origin, lower-left: the "y" of
viewport/scissor is counted from the bottom, which in memory is row 0 of the render target. With Wonder's
emulation, what the game places with viewports comes out in a mirrored position (y' = H - y - h) but upright: the
menu ended up above the logo and the copyright at the top instead of the bottom.

With NX_ORIGIN_LOWER_LEFT=1:
  - nvn_draw.cpp: positive-height viewport (Vulkan already maps the window "y" to the memory row, the same as
    OpenGL with row 0 at the bottom); viewport and scissor stay as the game passes them. Front faces do not
    change (the area is computed in the same coordinates in both).
  - nvn_vk.cpp: the window texture has row 0 at the bottom, so the blit to the swapchain flips it.
    If the present pass is active (Android / rotation) it is not flipped and a warning is logged once.
Without the variable, everything stays as before (Wonder).

Usage: python apply_origin_lower_left.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent; all or nothing; keeps CRLF.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
NVN = os.path.join(ROOT, "runtime", "src", "nvn")
MARK = "origin_lower_left"

HELPER = (
    "// NX_ORIGIN_LOWER_LEFT=1: NVN's default lower-left window origin (OpenGL's) instead of the upper-left one Wonder\n"
    "// sets. apply_origin_lower_left.py\n"
    "static bool origin_lower_left() {\n"
    "    static const bool on = [] {\n"
    "        const char* e = std::getenv(\"NX_ORIGIN_LOWER_LEFT\");\n"
    "        return e && e[0] == '1';\n"
    "    }();\n"
    "    return on;\n"
    "}\n"
    "\n"
)

EDITS = {
    "nvn_draw.cpp": [
        (
            "bool draw(VkCommandBuffer cb, const Targets& t, uint32_t mode, bool indexed, const Cmd& c) {\n",
            HELPER + "bool draw(VkCommandBuffer cb, const Targets& t, uint32_t mode, bool indexed, const Cmd& c) {\n",
        ),
        (
            "    vp.y += vp.height, vp.height = -vp.height;\n"
            "    set_dynamic(cb, vp, sc);\n",
            "    // Lower-left origin: window y counts from the bottom, which is row 0 of the target, as a positive Vulkan\n"
            "    // viewport already maps it; the window texture is turned upright at present (nvn_vk.cpp).\n"
            "    if (!origin_lower_left()) vp.y += vp.height, vp.height = -vp.height;\n"
            "    set_dynamic(cb, vp, sc);\n",
        ),
    ],
    "nvn_vk.cpp": [
        (
            "void gpu_present(uint64_t texture, const int32_t* crop) {\n",
            HELPER + "void gpu_present(uint64_t texture, const int32_t* crop) {\n",
        ),
        (
            "    bool drawn = src && present_pass_record(g_cb, *src, crop, index);  // nvn_present.cpp: leaves dst PRESENT_SRC\n",
            "    bool drawn = src && present_pass_record(g_cb, *src, crop, index);  // nvn_present.cpp: leaves dst PRESENT_SRC\n"
            "    if (drawn && origin_lower_left()) {\n"
            "        static bool warned = false;\n"
            "        if (!warned) warned = true, log(\"nvn/vk: NX_ORIGIN_LOWER_LEFT: the present pass does not flip the window texture\");\n"
            "    }\n",
        ),
        (
            "        blit.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};\n"
            "        blit.dstOffsets[1] = {int32_t(g_sc_extent.width), int32_t(g_sc_extent.height), 1};\n",
            "        if (origin_lower_left()) std::swap(blit.srcOffsets[0].y, blit.srcOffsets[1].y);  // row 0 is the bottom\n"
            "        blit.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};\n"
            "        blit.dstOffsets[1] = {int32_t(g_sc_extent.width), int32_t(g_sc_extent.height), 1};\n",
        ),
    ],
}


def main():
    plans = []
    for name, edits in EDITS.items():
        path = os.path.join(NVN, name)
        raw = open(path, "rb").read().decode("utf-8")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        if MARK in text:
            print(f"{name}: already applied")
            continue
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                sys.exit(f"{name}: 1 match was expected and there are {n} for:\n{old}\nNo file was changed.")
            text = text.replace(old, new)
        plans.append((path, name, text.replace("\n", "\r\n") if crlf else text, len(edits)))
    for path, name, text, n in plans:
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        print(f"{name}: {n} change(s)")
    if plans:
        print("Done. Rebuild and test with $env:NX_ORIGIN_LOWER_LEFT = \"1\"")


if __name__ == "__main__":
    main()