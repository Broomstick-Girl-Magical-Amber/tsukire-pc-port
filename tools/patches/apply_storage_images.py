"""apply_storage_images.py - on Android, colour images are made without the STORAGE usage (AP_STORAGE_IMAGES).

Why: nvn_image.cpp gave every colour image whose format allows it VK_IMAGE_USAGE_STORAGE_BIT, so that any shader
could write any texture. On Mali (and other mobile GPUs) that usage can turn framebuffer compression (AFBC) off for
the game's render targets, and every pass then reads and writes them uncompressed. Measured on a Mali-G77 with
apply_gpu_limits_diag.py (NX_NO_STORAGE_IMAGES=1) and apply_gpu_pass_diag.py (NX_GPU_PROFILE=2): the 720p passes
cost 2-3 times less per GPU clock cycle (a text scene: 15.3-16.2 ms of GPU a frame at 260 MHz before, 11.1-12.4 ms
at 156 MHz after), and in a whole session no shader wrote an image ("storage view": 0 times), no picture went wrong.

What changes:
  - runtime/src/nvn/nvn_image.cpp: on Android an image is made without STORAGE unless NX_STORAGE_IMAGES=1
    (environment or config.txt), which gives it back. Logged once:
      nvn/vk: images are made without STORAGE usage (framebuffer compression); NX_STORAGE_IMAGES=1 gives it back
    Windows, Linux and macOS: as before.
  - runtime/src/nvn/nvn_pipeline.cpp: storage_view already bound a dummy image for an image without STORAGE (as for a
    format without storage support); now that is logged, once per image (the first 20), so a scene that needs it
    shows in nx.log:
      nvn/vk: a shader writes a WxH image (format F) made without STORAGE usage: it gets a dummy (...)
  - ../tools/recomp_tsukihime.ps1: its $Markers rows, after the row of nvn_frames.cpp (AP_GPU_HANG), which no patcher
    uses.
Works with apply_gpu_limits_diag.py (temporary) applied or not, in any order.

  python tools\\patches\\apply_storage_images.py            then rebuild the Android library
                                                           (tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_storage_images.py --remove   undoes it (byte for byte)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_STORAGE_IMAGES"

IMG = "runtime/src/nvn/nvn_image.cpp"
PIPE = "runtime/src/nvn/nvn_pipeline.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

IMG_OLD = """        if (img->storage) ci.usage |= VK_IMAGE_USAGE_STORAGE_BIT;
"""
IMG_NEW = """#if defined(__ANDROID__)
        // AP_STORAGE_IMAGES (apply_storage_images.py): no STORAGE usage on Android (it can turn framebuffer compression
        // off on Mali and other mobile GPUs; the game's shaders did not write images); NX_STORAGE_IMAGES=1 gives it back.
        static const bool storage_wanted = [] {
            const char* e = std::getenv("NX_STORAGE_IMAGES");
            const bool on = e && e[0] == '1';
            if (!on) log("nvn/vk: images are made without STORAGE usage (framebuffer compression); NX_STORAGE_IMAGES=1 gives it back");
            return on;
        }();
        if (!storage_wanted) img->storage = false;
#endif
        if (img->storage) ci.usage |= VK_IMAGE_USAGE_STORAGE_BIT;
"""

PIPE_OLD = """    static std::map<VkImageViewType, GpuImage> dummies;
    VkImageViewType type = view_type(shape);
"""
PIPE_NEW = """    if (img && !img->storage) {  // AP_STORAGE_IMAGES: a shader writes an image made without STORAGE (logged once)
        static std::set<VkImage> told;
        if (told.size() < 20 && told.insert(img->image).second)
            log("nvn/vk: a shader writes a %ux%u image (format %d) made without STORAGE usage: it gets a dummy "
                "(NX_STORAGE_IMAGES=1 gives images STORAGE again on Android)",
                img->width, img->height, int(img->format));
    }
    static std::map<VkImageViewType, GpuImage> dummies;
    VkImageViewType type = view_type(shape);
"""

RECO_OLD = """    @('both', 'framework\\runtime\\src\\nvn\\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
"""
RECO_NEW = """    @('both', 'framework\\runtime\\src\\nvn\\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_image.cpp', 'AP_STORAGE_IMAGES', 'apply_storage_images.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_pipeline.cpp', 'AP_STORAGE_IMAGES', 'apply_storage_images.py', $false),
"""

EDITS = [
    (IMG, IMG_OLD, IMG_NEW),
    (PIPE, PIPE_OLD, PIPE_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)