#!/usr/bin/env python3
"""apply_clear_texture.py - implements nvnCommandBufferClearTexture (and the i / ui variants).

Symptom (Tsukihime): images that stay on screen when they should already be gone, rectangles of flat color,
noise, the green circle with rings, mask stripes over a CG. Not shaders, not videos, not sharing or
recycling images (NX_NO_IMAGE_SHARE / NX_IMAGE_REUSE=0 changed nothing).
Cause: nvnCommandBufferClearTexture is not implemented in the framework; it falls into the stub of nvn_trace.cpp (it
appears in nvn_trace.txt from startup). The game clears its layers/render targets with that call before composing:
without the clear, each layer keeps what was there before (the previous scene, a transition) or uninitialized memory
of the Vulkan image (garbage shaped like noise, rings, flat colors).

Signature: nvnCommandBufferClearTexture(cmd, const NVNtexture*, const NVNtextureView*, const NVNcopyRegion* {x,y,z,w,h,d},
const float* color, int channelMask); ...i with int[4], ...ui with uint[4].

Replay (nvn_copy.cpp, next to the texture copies): ends the current pass and
  - region = the whole level: vkCmdClearColorImage over the layers of the region;
  - partial region, RGBA8/BGRA8 format (UNORM or sRGB): the packed color is copied from the upload buffer
    with vkCmdCopyBufferToImage (the rest of the texture is kept: ensure_contents before);
  - partial of another format, or channel mask other than 0xF: done the same (mask ignored) or warned.
The image is marked as written by the GPU (it is not uploaded again from guest memory over the clear).

Files: nvn.h (Cmd::ClearTexture at the end of the enum), nvn_vk.h (declaration), nvn_draw_state.cpp (dispatch),
nvn_copy.cpp (recording, replay and NX_HLE). Refuses if the framework already registers nvnCommandBufferClearTexture.

Usage: python apply_clear_texture.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent; all or nothing; keeps CRLF.
"""
import glob
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
NVN = os.path.join(ROOT, "runtime", "src", "nvn")
MARK = "apply_clear_texture"

CODE = r'''// ---- nvnCommandBufferClearTexture (apply_clear_texture.py) ----------------------------------------------------
// (cmd, const NVNtexture*, const NVNtextureView*, const NVNcopyRegion* {x, y, z, width, height, depth},
//  const float* color, int channelMask); the i / ui forms take int[4] / uint[4]. Without it (the trace stub) the
// game's layers kept what they held before: old scenes, transition masks, or never-initialized image memory.
namespace {

struct ClearTexInfo {
    ViewInfo view;
    int32_t region[6] = {};
    uint32_t kind = 0;  // 0 float, 1 int, 2 uint
    uint32_t color[4] = {};
    uint32_t mask = 0xF;
};

void clear_texture(CPU& c, uint32_t kind) {
    ClearTexInfo info;
    info.view = read_view(c, c.x[2]);
    for (int k = 0; k < 6; k++) info.region[k] = c.x[3] ? ld<int32_t>(c.x[3] + 4ull * k) : 0;
    info.kind = kind;
    if (c.x[4]) std::memcpy(info.color, reinterpret_cast<const void*>(c.x[4]), sizeof info.color);
    info.mask = uint32_t(c.x[5]);
    Cmd cmd{Cmd::ClearTexture};
    cmd.a[0] = c.x[1];
    record(c.x[0], cmd, &info, sizeof info);
}
void CommandBufferClearTexture(CPU& c) { clear_texture(c, 0); }
void CommandBufferClearTexturei(CPU& c) { clear_texture(c, 1); }
void CommandBufferClearTextureui(CPU& c) { clear_texture(c, 2); }

uint8_t unorm8(float v) { return uint8_t(std::lround(std::clamp(v, 0.0f, 1.0f) * 255.0f)); }
uint8_t srgb8(float v) {
    v = std::clamp(v, 0.0f, 1.0f);
    return unorm8(v <= 0.0031308f ? v * 12.92f : 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f);
}

}  // namespace

void replay_clear_texture(VkCommandBuffer cb, const Cmd& c, const CmdList& list) {
    static const bool off = std::getenv("NX_NO_CLEAR_TEXTURE") != nullptr;  // bisecting: the old stub
    if (off) return;
    if (c.blob >= list.blobs.size() || list.blobs[c.blob].size() != sizeof(ClearTexInfo)) return;
    ClearTexInfo info;
    std::memcpy(&info, list.blobs[c.blob].data(), sizeof info);
    GpuImage* img = image_of(c.a[0]);
    if (!img || !img->image) return;
    static uint64_t clears = 0, partial = 0, skipped = 0;
    if (img->aspect != VK_IMAGE_ASPECT_COLOR_BIT) {
        if (skipped++ < 5) log("nvn: ClearTexture of a depth/stencil texture %llx: not replayed", (unsigned long long)c.a[0]);
        return;
    }
    uint32_t level = std::min(info.view.base_level, img->levels - 1);
    int32_t lw = int32_t(std::max(1u, img->width >> level)), lh = int32_t(std::max(1u, img->height >> level));
    int32_t x = std::max(info.region[0], 0), y = std::max(info.region[1], 0);
    int32_t w = std::min(std::max(info.region[3], 1), lw - x), h = std::min(std::max(info.region[4], 1), lh - y);
    if (w <= 0 || h <= 0) return;
    bool is3d = img->view_type == VK_IMAGE_VIEW_TYPE_3D;
    uint32_t base = is3d ? 0 : std::min<uint32_t>(uint32_t(std::max(info.region[2], 0)) + info.view.min_layer, img->layers - 1);
    uint32_t count = is3d ? 1 : std::clamp<uint32_t>(uint32_t(std::max(info.region[5], 1)), 1, img->layers - base);
    bool whole = x == 0 && y == 0 && w == lw && h == lh;
    if (++clears == 1 || clears % 1000 == 0 || (info.mask & 0xF) != 0xF)
        log("nvn: ClearTexture #%llu %llx %dx%d at %d,%d of %dx%d level %u, mask %#x, format %d%s",
            (unsigned long long)clears, (unsigned long long)c.a[0], w, h, x, y, lw, lh, level, info.mask,
            int(img->format), whole ? "" : " (partial)");
    if (whole) {
        draw_end_rendering(cb, 2);
        transition(cb, *img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        VkClearColorValue v{};
        std::memcpy(&v, info.color, sizeof info.color);
        VkImageSubresourceRange r{VK_IMAGE_ASPECT_COLOR_BIT, level, 1, base, count};
        p_vkCmdClearColorImage(cb, img->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &v, 1, &r);
        img->uploaded = true;  // nothing from guest memory over it
        img->rendered = true;  // GPU-written from now on
        return;
    }
    // A rectangle: the packed colour through the upload buffer (8-bit RGBA / BGRA only).
    bool bgra = img->format == VK_FORMAT_B8G8R8A8_UNORM || img->format == VK_FORMAT_B8G8R8A8_SRGB;
    bool rgba = img->format == VK_FORMAT_R8G8B8A8_UNORM || img->format == VK_FORMAT_R8G8B8A8_SRGB;
    bool srgb = img->format == VK_FORMAT_R8G8B8A8_SRGB || img->format == VK_FORMAT_B8G8R8A8_SRGB;
    if ((!bgra && !rgba) || info.kind != 0) {
        if (skipped++ < 5)
            log("nvn: ClearTexture %llx: a %dx%d rectangle of format %d (kind %u) not replayed", (unsigned long long)c.a[0],
                w, h, int(img->format), info.kind);
        return;
    }
    GpuImage* full = ensure_contents(cb, c.a[0]);  // the rest of the texture as it is
    if (full) img = full;
    draw_end_rendering(cb, 2);
    float f[4];
    std::memcpy(f, info.color, sizeof f);
    uint8_t px[4] = {srgb ? srgb8(f[0]) : unorm8(f[0]), srgb ? srgb8(f[1]) : unorm8(f[1]),
                     srgb ? srgb8(f[2]) : unorm8(f[2]), unorm8(f[3])};
    if (bgra) std::swap(px[0], px[2]);
    uint64_t bytes = uint64_t(w) * h * 4 * count;
    Upload u = bytes > (1ull << 20) ? staging_alloc(bytes) : upload_alloc(bytes, 16);
    if (!u.ptr) return;
    for (uint64_t k = 0; k < bytes; k += 4) std::memcpy(u.ptr + k, px, 4);
    transition(cb, *img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    VkBufferImageCopy r{};
    r.bufferOffset = u.offset;
    r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, level, base, count};
    r.imageOffset = {x, y, 0};
    r.imageExtent = {uint32_t(w), uint32_t(h), 1};
    p_vkCmdCopyBufferToImage(cb, u.buffer, img->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);
    img->rendered = true;
    partial++;
}

NX_HLE("nvnCommandBufferClearTexture", CommandBufferClearTexture);
NX_HLE("nvnCommandBufferClearTexturei", CommandBufferClearTexturei);
NX_HLE("nvnCommandBufferClearTextureui", CommandBufferClearTextureui);

'''

EDITS = {
    "nvn.h": [(
        "        BindMultisampleState,  // n: alpha to coverage enabled (nvn_state.cpp)\n    } op;\n",
        "        BindMultisampleState,  // n: alpha to coverage enabled (nvn_state.cpp)\n"
        "        ClearTexture,  // a[0] the texture, blob ClearTexInfo (nvn_copy.cpp, apply_clear_texture.py)\n"
        "    } op;\n",
    )],
    "nvn_vk.h": [(
        "void replay_copy(VkCommandBuffer cb, const Cmd& c, const CmdList& list);  // nvn_copy.cpp: texture copies\n",
        "void replay_copy(VkCommandBuffer cb, const Cmd& c, const CmdList& list);  // nvn_copy.cpp: texture copies\n"
        "void replay_clear_texture(VkCommandBuffer cb, const Cmd& c, const CmdList& list);  // nvn_copy.cpp: "
        "nvnCommandBufferClearTexture (apply_clear_texture.py)\n",
    )],
    "nvn_draw_state.cpp": [(
        "        default: raster_command(c); break;\n",
        "        case Cmd::ClearTexture: replay_clear_texture(cb, c, list); break;  // apply_clear_texture.py\n"
        "        default: raster_command(c); break;\n",
    )],
    "nvn_copy.cpp": [
        ("#include <algorithm>\n#include <cstdlib>\n#include <cstring>\n",
         "#include <algorithm>\n#include <cmath>  // apply_clear_texture.py\n#include <cstdlib>\n#include <cstring>\n"),
        ('NX_HLE("nvnCommandBufferCopyTextureToTexture", CopyTextureToTexture);\n',
         CODE + 'NX_HLE("nvnCommandBufferCopyTextureToTexture", CopyTextureToTexture);\n'),
    ],
}


def main():
    # Already registered by the framework (without this patch): do not overwrite it.
    for path in glob.glob(os.path.join(NVN, "*.cpp")):
        text = open(path, "rb").read().decode("utf-8", "replace")
        if 'NX_HLE("nvnCommandBufferClearTexture"' in text and MARK not in text:
            sys.exit(f"{os.path.basename(path)} already registers nvnCommandBufferClearTexture: nothing is applied.")
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
        print("Done. Rebuild: cmake --build build\\tsukihime")


if __name__ == "__main__":
    main()