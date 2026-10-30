#!/usr/bin/env python3
"""apply_vk_fail.py - marker AP_VK_FAIL

When Vulkan cannot be used at all, the game used to stay on a blank window with the audio playing (or close with no
explanation). nvn_vk.cpp init() runs lazily, from the first gpu_submit / gpu_present, on the render thread: every
failure there was `return false`, the callers just `return`, and the window (created earlier by the guest's nn::vi
calls) stayed empty. AP_VK_REQ (apply_vk_requirements.py) only covers "there is a GPU but it is not Vulkan 1.3".

With the patch, in init(), each of these cases writes a line to the log and to stderr, shows a MessageBox (Windows,
also under Wine/Proton) and ends the process with exit code 6:
  - vulkan-1.dll (the loader) cannot be loaded,
  - vkCreateInstance fails (e.g. VK_ERROR_INCOMPATIBLE_DRIVER: no ICD / no driver),
  - there is no physical device,
  - the window surface cannot be made, or no queue family can draw and present,
  - vkCreateDevice fails.
The message says what happened, shows the VkResult and tells the user to install or update the GPU driver.

Test hook: NX_VK_FAKE_FAIL=loader|instance|gpu|device forces one of those failures without touching the real driver
(the variable is only read while Vulkan starts). Without it the normal path is unchanged.

It does not depend on a per-game define (like AP_GPU_HANG) and does not edit game.cmake: it works in both Tsukihime
editions. It is independent of AP_VK_REQ: its anchors are not shared, so the two can be applied or removed in any order.

Edits runtime/src/**/nvn_vk.cpp only (engine _patch.py: all or nothing, idempotent, keeps CRLF and BOM, --remove
leaves the file byte for byte as it was).
    private\\venv\\Scripts\\python ..\\tools\\patches\\apply_vk_fail.py [--root <root>\\framework] [--remove]
After: cmake --build build\\tsukihime and cmake --build build\\tsukihime_global.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_VK_FAIL"
VK = "runtime/src/**/nvn_vk.cpp"

EDITS = [
    # 1. MessageBoxW without <windows.h> (the same declaration AP_VK_REQ uses; repeating an identical one is fine)
    (VK,
     "#include \"nvn_vk.h\"\n"
     "#include \"platform.h\"\n",
     "#include \"nvn_vk.h\"\n"
     "#include \"platform.h\"\n"
     "\n"
     "#include <cwchar>  // AP_VK_FAIL (apply_vk_fail.py)\n"
     "#if defined(_WIN32) && !defined(_WINUSER_)\n"
     "extern \"C\" __declspec(dllimport) int __stdcall MessageBoxW(void*, const wchar_t*, const wchar_t*, unsigned);\n"
     "#endif\n"),
    # 2. the helper, before g_gipa (after check() and has_extension())
    (VK,
     "void* g_gipa = nullptr;  // vkGetInstanceProcAddr, for surfaces made later (make_surface)\n",
     "// AP_VK_FAIL (apply_vk_fail.py): Vulkan cannot be used. Log, stderr, a MessageBox (Windows, Wine/Proton included)\n"
     "// and exit code 6, instead of a blank window with the audio playing. NX_VK_FAKE_FAIL=loader|instance|gpu|device\n"
     "// forces each failure (tests).\n"
     "int g_surface_result = 0;  // the VkResult of the last make_surface() failure\n"
     "\n"
     "bool vk_fake(const char* what) {\n"
     "    const char* e = std::getenv(\"NX_VK_FAKE_FAIL\");\n"
     "    return e && std::strcmp(e, what) == 0;\n"
     "}\n"
     "\n"
     "const char* vk_result_name(int r) {\n"
     "    switch (r) {\n"
     "        case 0: return \"VK_SUCCESS\";\n"
     "        case -1: return \"VK_ERROR_OUT_OF_HOST_MEMORY\";\n"
     "        case -2: return \"VK_ERROR_OUT_OF_DEVICE_MEMORY\";\n"
     "        case -3: return \"VK_ERROR_INITIALIZATION_FAILED\";\n"
     "        case -4: return \"VK_ERROR_DEVICE_LOST\";\n"
     "        case -5: return \"VK_ERROR_MEMORY_MAP_FAILED\";\n"
     "        case -6: return \"VK_ERROR_LAYER_NOT_PRESENT\";\n"
     "        case -7: return \"VK_ERROR_EXTENSION_NOT_PRESENT\";\n"
     "        case -8: return \"VK_ERROR_FEATURE_NOT_PRESENT\";\n"
     "        case -9: return \"VK_ERROR_INCOMPATIBLE_DRIVER\";\n"
     "        case -1000000000: return \"VK_ERROR_SURFACE_LOST_KHR\";\n"
     "        case -1000000001: return \"VK_ERROR_NATIVE_WINDOW_IN_USE_KHR\";\n"
     "        default: return \"VkResult\";\n"
     "    }\n"
     "}\n"
     "\n"
     "// tag: short text for the log; what: what happened, for the box (English). has_result: result is a VkResult.\n"
     "[[noreturn]] void vk_unusable(const char* tag, bool has_result, int result, [[maybe_unused]] const wchar_t* what) {\n"
     "    char res[96] = \"\";\n"
     "    if (has_result) std::snprintf(res, sizeof res, \" (VkResult %d, %s)\", result, vk_result_name(result));\n"
     "    log(\"nvn/vk: Vulkan cannot be used: %s%s. Install or update the GPU driver. Stopping.\", tag, res);\n"
     "    std::fprintf(stderr, \"nvn/vk: Vulkan cannot be used: %s%s. Install or update the GPU driver.\\n\", tag, res);\n"
     "    std::fflush(stderr);\n"
     "#if defined(_WIN32)\n"
     "    wchar_t rw[96] = L\"\";\n"
     "    if (has_result) std::swprintf(rw, sizeof rw / sizeof rw[0], L\"VkResult: %d (%hs)\\n\", result, vk_result_name(result));\n"
     "    wchar_t msg[2048];\n"
     "    std::swprintf(msg, sizeof msg / sizeof msg[0],\n"
     "                  L\"The game needs Vulkan to draw, and it cannot be used.\\n\\n\"\n"
     "                  L\"%ls\\n%ls\\n\"\n"
     "                  L\"What to do: install or update the GPU driver from the manufacturer's site (Intel: Intel Driver & \"\n"
     "                  L\"Support Assistant; AMD: Adrenalin; NVIDIA: GeForce/Studio), not from Windows Update, then start the \"\n"
     "                  L\"game again. On Linux (Proton): install your distribution's Vulkan drivers.\\n\\n\"\n"
     "                  L\"Details: nx.log\",\n"
     "                  what, rw);\n"
     "    // MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST: not hidden behind the game window\n"
     "    ::MessageBoxW(nullptr, msg, L\"Vulkan unavailable\", 0x10 | 0x10000 | 0x40000);\n"
     "#endif\n"
     "    _exit(6);\n"
     "}\n"
     "\n"
     "void* g_gipa = nullptr;  // vkGetInstanceProcAddr, for surfaces made later (make_surface)\n"),
    # 3. the loader
    (VK,
     "    auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(plat::vulkan_get_instance_proc_addr());\n"
     "    if (!gipa) {\n"
     "        log(\"nvn/vk: no Vulkan loader\");\n"
     "        return false;\n"
     "    }\n",
     "    auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(plat::vulkan_get_instance_proc_addr());\n"
     "    if (vk_fake(\"loader\")) gipa = nullptr;  // AP_VK_FAIL\n"
     "    if (!gipa) {\n"
     "        log(\"nvn/vk: no Vulkan loader\");\n"
     "        vk_unusable(\"the Vulkan loader (vulkan-1.dll) could not be loaded\", false, 0,\n"
     "                    L\"vulkan-1.dll (the Vulkan loader) could not be loaded: no Vulkan driver is installed.\");\n"
     "    }\n"),
    # 4. vkCreateInstance
    (VK,
     "    if (!check(p_create_instance(&ici, nullptr, &g_instance), \"vkCreateInstance\")) return false;\n",
     "    {   // AP_VK_FAIL\n"
     "        const VkResult ri = vk_fake(\"instance\") ? VK_ERROR_INCOMPATIBLE_DRIVER\n"
     "                                                : p_create_instance(&ici, nullptr, &g_instance);\n"
     "        if (!check(ri, \"vkCreateInstance\"))\n"
     "            vk_unusable(\"vkCreateInstance failed\", true, int(ri),\n"
     "                        L\"The Vulkan instance could not be created (no Vulkan driver, or an incompatible one).\");\n"
     "    }\n"),
    # 5. vkEnumeratePhysicalDevices: keep the result
    (VK,
     "    n = 0;\n"
     "    p_enum_physical(g_instance, &n, nullptr);\n",
     "    n = 0;\n"
     "    const VkResult enum_r = p_enum_physical(g_instance, &n, nullptr);  // AP_VK_FAIL\n"),
    # 6. no physical device
    (VK,
     "    if (!g_physical) {\n"
     "        log(\"nvn/vk: no GPU\");\n"
     "        return false;\n"
     "    }\n",
     "    if (vk_fake(\"gpu\")) g_physical = VK_NULL_HANDLE;  // AP_VK_FAIL\n"
     "    if (!g_physical) {\n"
     "        log(\"nvn/vk: no GPU\");\n"
     "        vk_unusable(\"no Vulkan GPU found\", true, int(enum_r),\n"
     "                    L\"No GPU with Vulkan support was found.\");\n"
     "    }\n"),
    # 7. the surface: keep the result
    (VK,
     "    if (!check(VkResult(plat::vulkan_create_surface(g_instance, g_window, &surface, g_gipa)), \"surface\")) return false;\n",
     "    const VkResult sr = VkResult(plat::vulkan_create_surface(g_instance, g_window, &surface, g_gipa));  // AP_VK_FAIL\n"
     "    if (!check(sr, \"surface\")) {\n"
     "        g_surface_result = int(sr);\n"
     "        return false;\n"
     "    }\n"),
    # 8. init(): the surface failed
    (VK,
     "    g_gipa = reinterpret_cast<void*>(gipa);\n"
     "    if (!make_surface()) return false;\n",
     "    g_gipa = reinterpret_cast<void*>(gipa);\n"
     "    if (!make_surface())\n"
     "        vk_unusable(\"the window surface could not be created\", true, g_surface_result,  // AP_VK_FAIL\n"
     "                    L\"The Vulkan surface for the game window could not be created.\");\n"),
    # 9. no queue that draws and presents
    (VK,
     "    if (g_family == UINT32_MAX) {\n"
     "        log(\"nvn/vk: no graphics + present queue\");\n"
     "        return false;\n"
     "    }\n",
     "    if (g_family == UINT32_MAX) {\n"
     "        log(\"nvn/vk: no graphics + present queue\");\n"
     "        vk_unusable(\"no queue family can draw and present\", false, 0,  // AP_VK_FAIL\n"
     "                    L\"The chosen GPU cannot draw and present to the game window.\");\n"
     "    }\n"),
    # 10. vkCreateDevice
    (VK,
     "    if (!check(p_create_device(g_physical, &dci, nullptr, &g_device), \"vkCreateDevice\")) return false;\n",
     "    {   // AP_VK_FAIL\n"
     "        const VkResult rd = vk_fake(\"device\") ? VK_ERROR_INITIALIZATION_FAILED\n"
     "                                               : p_create_device(g_physical, &dci, nullptr, &g_device);\n"
     "        if (!check(rd, \"vkCreateDevice\"))\n"
     "            vk_unusable(\"vkCreateDevice failed\", true, int(rd),\n"
     "                        L\"The Vulkan device could not be created on the GPU (incomplete or faulty driver).\");\n"
     "    }\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)