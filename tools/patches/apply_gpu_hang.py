#!/usr/bin/env python3
"""apply_gpu_hang.py - marker AP_GPU_HANG

The runtime considers the GPU hung if a submit does not finish in 2 s (nvn_vk.cpp end_and_wait, nvn_frames.cpp
wait_fences) and closes the game ("nvn/vk: GPU hang (a submit did not finish in 2 s): stopping"). With a CPU
renderer (llvmpipe, e.g. a Linux VM without a GPU) loading the first scene takes ~9 s and the game always closed
at frame ~20, although it was not hung.

With the patch:
  - NX_GPU_HANG_MS=<ms> raises that limit (never below 2000 ms). Without the variable: 2 s, as always.
  - A submit that finishes but took more than 2 s leaves "nvn/vk: a submit took N s" in the log (only possible with the
    variable set).
Without the variable the behavior is the usual one (Windows, Wonder): no per-game define is needed.

Edits runtime/src/**/nvn_vk.cpp and runtime/src/**/nvn_frames.cpp (engine _patch.py: all or nothing, idempotent,
keeps CRLF and BOM, --remove leaves the files byte for byte as they were).
    private\\venv\\Scripts\\python ..\\tools\\patches\\apply_gpu_hang.py [--root <root>\\framework] [--remove]
After: cmake --build build\\tsukihime and cmake --build build\\tsukihime_global.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_GPU_HANG"
VK = "runtime/src/**/nvn_vk.cpp"
FRAMES = "runtime/src/**/nvn_frames.cpp"

EDITS = [
    # nvn_vk.cpp: the function, next to gpu_hung (outside the anonymous namespace: nvn_frames.cpp uses it)
    (VK,
     "    _exit(4);\n"
     "}\n"
     "\n"
     "namespace {\n"
     "\n"
     "bool check(VkResult r, const char* what) {",
     "    _exit(4);\n"
     "}\n"
     "\n"
     "// AP_GPU_HANG (apply_gpu_hang.py): how long a submit may take before it counts as hung. 2 s by default;\n"
     "// NX_GPU_HANG_MS=<ms> raises it (a CPU renderer, llvmpipe in a VM, took 9 s for a scene load). Never below 2 s.\n"
     "uint64_t gpu_hang_timeout_ns() {\n"
     "    static const uint64_t ns = [] {\n"
     "        const char* e = std::getenv(\"NX_GPU_HANG_MS\");\n"
     "        const long long ms = e ? std::atoll(e) : 0;\n"
     "        return ms > 2000 ? uint64_t(ms) * 1'000'000ull : 2'000'000'000ull;\n"
     "    }();\n"
     "    return ns;\n"
     "}\n"
     "\n"
     "namespace {\n"
     "\n"
     "bool check(VkResult r, const char* what) {"),
    # nvn_vk.cpp: the synchronous path (end_and_wait)
    (VK,
     "    if (queued) {\n"
     "        VkResult w = p_vkWaitForFences(g_device, 1, &g_fence, VK_TRUE, 2'000'000'000ull);\n",
     "    if (queued) {\n"
     "        VkResult w = p_vkWaitForFences(g_device, 1, &g_fence, VK_TRUE, gpu_hang_timeout_ns());  // AP_GPU_HANG\n"
     "        if (w == VK_SUCCESS && plat::monotonic_ns() - t1 > 2'000'000'000ull)\n"
     "            log(\"nvn/vk: a submit took %.1f s\", double(plat::monotonic_ns() - t1) / 1e9);\n"),
    # nvn_frames.cpp: the declaration
    (FRAMES,
     "std::recursive_mutex& gpu_lock();  // nvn_vk.cpp\n",
     "std::recursive_mutex& gpu_lock();  // nvn_vk.cpp\n"
     "uint64_t gpu_hang_timeout_ns();    // nvn_vk.cpp (AP_GPU_HANG: NX_GPU_HANG_MS)\n"),
    # nvn_frames.cpp: the asynchronous / pipelined path (wait_fences)
    (FRAMES,
     "    VkResult r = p_vkWaitForFences(vk_device(), n, fences, VK_TRUE, kFenceTimeout);\n",
     "    VkResult r = p_vkWaitForFences(vk_device(), n, fences, VK_TRUE,\n"
     "                                   std::max(kFenceTimeout, gpu_hang_timeout_ns()));  // AP_GPU_HANG\n"
     "    if (r == VK_SUCCESS && plat::monotonic_ns() - t0 > kFenceTimeout)\n"
     "        log(\"nvn/vk: a submit took %.1f s\", double(plat::monotonic_ns() - t0) / 1e9);\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)