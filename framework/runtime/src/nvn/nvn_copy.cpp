// NVN copies between textures (docs/NVN.md, N6). nvnCommandBufferCopyTextureToTexture(cmd, const NVNtexture* src,
// const NVNtextureView* srcView, const NVNcopyRegion* srcRegion, const NVNtexture* dst, const NVNtextureView* dstView,
// const NVNcopyRegion* dstRegion, int copyFlags) (Wonder's call sites). NVNcopyRegion {int x, y, z, width, height,
// depth}; for array textures z/depth are layers. Flags: 1 linear filter, 4/8/16 mirror x/y/z (2 selects the 2D
// engine: no difference here). Replayed as a vkCmdCopyImage when formats and sizes match, else a vkCmdBlitImage.
// Wonder copies its frame into a texture for screen transitions (the opening's cloud wipe).
// Copies within one image (another level or layer, or another rectangle of the same one: blur chains, mips, atlases;
// textures over one storage share an image since #36) are replayed with the image in GENERAL, as Vulkan allows when
// the regions do not overlap; overlapping ones are dropped (G10). NX_COPY_NO_SAME_IMAGE=1: all dropped, the old way.
#include <algorithm>
#include <cmath>  // apply_clear_texture.py
#include <cstdlib>
#include <cstring>

#include "nvn_pipeline.h"
#include "nvn_vk.h"

namespace nx::nvn {

using namespace vk;

namespace {

struct CopyInfo {
    ViewInfo src_view, dst_view;
    int32_t src[6] = {}, dst[6] = {};
    uint32_t flags = 0;
};

void CopyTextureToTexture(CPU& c) {
    CopyInfo info;
    info.src_view = read_view(c, c.x[2]);
    info.dst_view = read_view(c, c.x[5]);
    for (int k = 0; k < 6; k++) {
        info.src[k] = c.x[3] ? ld<int32_t>(c.x[3] + 4ull * k) : 0;
        info.dst[k] = c.x[6] ? ld<int32_t>(c.x[6] + 4ull * k) : 0;
    }
    info.flags = uint32_t(c.x[7]);
    Cmd cmd{Cmd::CopyTextureToTexture};
    cmd.a[0] = c.x[1], cmd.a[1] = c.x[4];
    record(c.x[0], cmd, &info, sizeof info);
}

bool compressed(VkFormat f) {
    return (f >= VK_FORMAT_BC1_RGB_UNORM_BLOCK && f <= VK_FORMAT_ASTC_12x12_SRGB_BLOCK);
}

// A copy within one image keeps it GENERAL, where transition() records nothing: the writes before it (a pass,
// another copy) and the reads after it need a barrier of their own.
void general_barrier(VkCommandBuffer cb, const GpuImage& img) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
    b.oldLayout = b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = img.image;
    b.subresourceRange = {img.aspect, 0, VK_REMAINING_MIP_LEVELS, 0, VK_REMAINING_ARRAY_LAYERS};
    p_vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0,
                           nullptr, 1, &b);
}

// Half-open ranges [a0, a0 + an) and [b0, b0 + bn) intersect.
bool ranges_meet(int64_t a0, int64_t an, int64_t b0, int64_t bn) { return a0 < b0 + bn && b0 < a0 + an; }

uint64_t g_same_image = 0, g_same_overlap = 0;

}  // namespace

void replay_copy(VkCommandBuffer cb, const Cmd& c, const CmdList& list) {
    static const bool off = std::getenv("NX_NO_COPY") != nullptr;  // debug: skip texture copies
    if (off) return;
    if (c.blob >= list.blobs.size() || list.blobs[c.blob].size() != sizeof(CopyInfo)) return;
    CopyInfo info;
    std::memcpy(&info, list.blobs[c.blob].data(), sizeof info);
    GpuImage* src = ensure_contents(cb, c.a[0]);
    GpuImage* dst = ensure_contents(cb, c.a[1]);
    if (!src || !dst) return;
    bool same = src == dst;
    static const bool no_same = std::getenv("NX_COPY_NO_SAME_IMAGE") != nullptr;  // bisecting: the old way
    if (same && no_same) return;
    uint32_t sl = std::min(info.src_view.base_level, src->levels - 1), dl = std::min(info.dst_view.base_level, dst->levels - 1);
    bool s3d = src->view_type == VK_IMAGE_VIEW_TYPE_3D, d3d = dst->view_type == VK_IMAGE_VIEW_TYPE_3D;
    auto sub = [](const GpuImage& img, uint32_t level, int32_t z, int32_t depth, uint32_t view_layer, bool is3d) {
        VkImageAspectFlags aspect = img.aspect;
        uint32_t layer = is3d ? 0 : std::min<uint32_t>(uint32_t(std::max(z, 0)) + view_layer, img.layers - 1);
        uint32_t count = is3d ? 1 : std::clamp<uint32_t>(uint32_t(std::max(depth, 1)), 1, img.layers - layer);
        return VkImageSubresourceLayers{aspect, level, layer, count};
    };
    VkImageSubresourceLayers ss = sub(*src, sl, info.src[2], info.src[5], info.src_view.min_layer, s3d);
    VkImageSubresourceLayers ds = sub(*dst, dl, info.dst[2], info.dst[5], info.dst_view.min_layer, d3d);
    int32_t sw = std::max(info.src[3], 1), sh = std::max(info.src[4], 1);
    int32_t dw = std::max(info.dst[3], 1), dh = std::max(info.dst[4], 1);
    if (same) {
        // One subresource and layer range, and the rectangles (and 3D slices) meet: Vulkan forbids it, dropped.
        bool overlap = ss.mipLevel == ds.mipLevel &&
                       ranges_meet(ss.baseArrayLayer, ss.layerCount, ds.baseArrayLayer, ds.layerCount) &&
                       ranges_meet(info.src[0], sw, info.dst[0], dw) && ranges_meet(info.src[1], sh, info.dst[1], dh) &&
                       (!s3d || ranges_meet(info.src[2], std::max(info.src[5], 1), info.dst[2],
                                            std::max(info.dst[5], 1)));
        g_same_image++, g_same_overlap += overlap;
        if (g_same_image == 1 || g_same_image % 1000 == 0 || (overlap && g_same_overlap <= 5))
            log("nvn: %llu copies within one image (%llu overlapping, dropped); this one %llx level %u -> %u, "
                "layer %u -> %u%s",
                (unsigned long long)g_same_image, (unsigned long long)g_same_overlap, (unsigned long long)c.a[0],
                ss.mipLevel, ds.mipLevel, ss.baseArrayLayer, ds.baseArrayLayer, overlap ? " (overlapping)" : "");
        if (overlap) return;
    }
    static const bool trace_cubes = std::getenv("NX_TRACE_CUBES") != nullptr;
    static int cube_logs = 0;
    if (trace_cubes && (dst->view_type == VK_IMAGE_VIEW_TYPE_CUBE || dst->view_type == VK_IMAGE_VIEW_TYPE_CUBE_ARRAY) &&
        cube_logs++ < 400)
        log("cubes: copy %llx -> %llx level %u layer %u+%u (z %d depth %d view layer %u) %dx%d", (unsigned long long)c.a[0],
            (unsigned long long)c.a[1], ds.mipLevel, ds.baseArrayLayer, ds.layerCount, info.dst[2], info.dst[5],
            info.dst_view.min_layer, info.dst[3], info.dst[4]);
    VkImageLayout sly = same ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    VkImageLayout dly = same ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    transition(cb, *src, sly);
    transition(cb, *dst, dly);
    if (same) general_barrier(cb, *src);
    bool mirror = info.flags & (4 | 8 | 16);
    if (src->format == dst->format && sw == dw && sh == dh && !mirror && ss.layerCount == ds.layerCount) {
        VkImageCopy r{};
        r.srcSubresource = ss, r.dstSubresource = ds;
        r.srcOffset = {info.src[0], info.src[1], s3d ? info.src[2] : 0};
        r.dstOffset = {info.dst[0], info.dst[1], d3d ? info.dst[2] : 0};
        r.extent = {uint32_t(sw), uint32_t(sh), s3d ? uint32_t(std::max(info.src[5], 1)) : 1u};
        p_vkCmdCopyImage(cb, src->image, sly, dst->image, dly, 1, &r);
    } else if (!compressed(src->format) && !compressed(dst->format) && src->aspect == dst->aspect) {
        VkImageBlit b{};
        b.srcSubresource = ss, b.dstSubresource = ds;
        ds.layerCount = ss.layerCount = std::min(ss.layerCount, ds.layerCount);
        b.srcSubresource.layerCount = b.dstSubresource.layerCount = ss.layerCount;
        b.srcOffsets[0] = {info.src[0], info.src[1], 0};
        b.srcOffsets[1] = {info.src[0] + sw, info.src[1] + sh, 1};
        b.dstOffsets[0] = {info.dst[0], info.dst[1], 0};
        b.dstOffsets[1] = {info.dst[0] + dw, info.dst[1] + dh, 1};
        if (info.flags & 4) std::swap(b.dstOffsets[0].x, b.dstOffsets[1].x);
        if (info.flags & 8) std::swap(b.dstOffsets[0].y, b.dstOffsets[1].y);
        bool linear = (info.flags & 1) && src->aspect == VK_IMAGE_ASPECT_COLOR_BIT;
        p_vkCmdBlitImage(cb, src->image, sly, dst->image, dly, 1, &b, linear ? VK_FILTER_LINEAR : VK_FILTER_NEAREST);
    } else {
        static int logged = 0;
        if (logged++ < 5)
            log("nvn: copy %llx -> %llx not replayed (formats %d -> %d, %dx%d -> %dx%d)", (unsigned long long)c.a[0],
                (unsigned long long)c.a[1], int(src->format), int(dst->format), sw, sh, dw, dh);
        return;
    }
    if (same) general_barrier(cb, *dst);
    dst->rendered = true;  // GPU-written from now on
}

// ---- nvnCommandBufferClearTexture (apply_clear_texture.py) ----------------------------------------------------
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

NX_HLE("nvnCommandBufferCopyTextureToTexture", CopyTextureToTexture);

}  // namespace nx::nvn
