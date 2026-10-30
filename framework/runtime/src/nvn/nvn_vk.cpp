// NVN's GPU side on Vulkan (docs/NVN.md, N3): the host device, a VkImage per NVN texture the GPU touches, the
// replay of recorded command lists, and presenting a window texture on the host swapchain.
//
// First version: every submit is recorded into one Vulkan command buffer and waited for (NX_PIPELINED, NX_ASYNC_GPU:
// into a slot's, not waited for, nvn_frames.cpp); image layouts are tracked per image, in submission order. The image
// behind a texture is nvn_image.cpp. On macOS this is MoltenVK: the instance enumerates portability drivers and the
// device enables VK_KHR_portability_subset.
#if !defined(_WIN32)
#include <unistd.h>  // _exit (stdlib.h on Windows)
#endif

#include <algorithm>
#include <atomic>  // AP_ASPECT
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "nvn_vk.h"
#include "platform.h"

#include <cwchar>  // AP_VK_FAIL (apply_vk_fail.py)
#if defined(_WIN32) && !defined(_WINUSER_)
extern "C" __declspec(dllimport) int __stdcall MessageBoxW(void*, const wchar_t*, const wchar_t*, unsigned);
#endif

#include <nx/game.h>

#if defined(NX_VK_REQUIRE_13) && defined(_WIN32) && !defined(_WINUSER_)  // AP_VK_REQ: el aviso, sin <windows.h>
extern "C" __declspec(dllimport) int __stdcall MessageBoxW(void*, const wchar_t*, const wchar_t*, unsigned);
#endif

namespace nx::nvn {

using namespace nx::vk;


namespace {

std::recursive_mutex g_gpu;  // the whole GPU side: one submitting thread at a time
bool g_ready = false, g_failed = false;
VkInstance g_instance = VK_NULL_HANDLE;
VkPhysicalDevice g_physical = VK_NULL_HANDLE;
VkDevice g_device = VK_NULL_HANDLE;
VkQueue g_queue = VK_NULL_HANDLE;
uint32_t g_family = 0;
VkPhysicalDeviceMemoryProperties g_memory{};
VkCommandPool g_pool = VK_NULL_HANDLE;
VkCommandBuffer g_cb = VK_NULL_HANDLE;
VkFence g_fence = VK_NULL_HANDLE;

VkSurfaceKHR g_surface = VK_NULL_HANDLE;
VkSwapchainKHR g_swapchain = VK_NULL_HANDLE;
std::vector<VkImage> g_sc_images;
VkExtent2D g_sc_extent{};
VkExtent2D g_sc_current{};  // the surface's currentExtent when the swapchain was made (SUBOPTIMAL: did it change?)
VkSurfaceTransformFlagBitsKHR g_sc_transform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
VkFormat g_sc_format = VK_FORMAT_B8G8R8A8_UNORM;
VkSemaphore g_acquired = VK_NULL_HANDLE, g_rendered = VK_NULL_HANDLE;
VkBool32 g_depth_clip_control = VK_FALSE;
bool g_integrated = false;
bool g_require_sg32 = false;  // compute pipelines are made with a required subgroup size of 32 (Maxwell's warp)  // the chosen GPU is an integrated one (unified memory, the GPU is the long side)
VkPhysicalDeviceFeatures g_features{};  // the core features enabled (vk_features())
VkDescriptorPool g_descriptors = VK_NULL_HANDLE;

// The per-submit upload buffer (nvn_vk.h): one host-visible buffer, bump-allocated, reset at every submit.
VkBuffer g_upload = VK_NULL_HANDLE;
VkDeviceMemory g_upload_memory = VK_NULL_HANDLE;
uint8_t* g_upload_ptr = nullptr;
uint64_t g_upload_size = 0, g_upload_used = 0;
void make_upload_buffer(uint64_t size);

// Commands the Hades bridge intercepts are not in the generated table: ours are loaded here.
PFN_vkEnumerateInstanceExtensionProperties p_enum_instance_ext = nullptr;
PFN_vkCreateInstance p_create_instance = nullptr;
PFN_vkEnumeratePhysicalDevices p_enum_physical = nullptr;
PFN_vkCreateDevice p_create_device = nullptr;

}  // namespace

// A lost device means the GPU hung (a shader looping for ever): the process ends at once. Submitting more work, or
// waiting, keeps the GPU stuck, and on macOS that froze WindowServer and restarted the machine.
[[noreturn]] void gpu_hung(const char* why) {
    log("nvn/vk: GPU hang (%s): stopping", why);
    std::fprintf(stderr, "nvn/vk: GPU hang (%s): stopping\n", why);
    _exit(4);
}

// AP_GPU_HANG (apply_gpu_hang.py): how long a submit may take before it counts as hung. 2 s by default;
// NX_GPU_HANG_MS=<ms> raises it (a CPU renderer, llvmpipe in a VM, took 9 s for a scene load). Never below 2 s.
uint64_t gpu_hang_timeout_ns() {
    static const uint64_t ns = [] {
        const char* e = std::getenv("NX_GPU_HANG_MS");
        const long long ms = e ? std::atoll(e) : 0;
        return ms > 2000 ? uint64_t(ms) * 1'000'000ull : 2'000'000'000ull;
    }();
    return ns;
}

namespace {

bool check(VkResult r, const char* what) {
    if (r >= 0) return true;
    log("nvn/vk: %s failed (%d)", what, int(r));
    if (r == VK_ERROR_DEVICE_LOST) gpu_hung(what);
    return false;
}

bool has_extension(const std::vector<VkExtensionProperties>& list, const char* name) {
    for (auto& e : list)
        if (std::strcmp(e.extensionName, name) == 0) return true;
    return false;
}

// AP_VK_FAIL (apply_vk_fail.py): Vulkan cannot be used. Log, stderr, a MessageBox (Windows, Wine/Proton included)
// and exit code 6, instead of a blank window with the audio playing. NX_VK_FAKE_FAIL=loader|instance|gpu|device
// forces each failure (tests).
int g_surface_result = 0;  // the VkResult of the last make_surface() failure

bool vk_fake(const char* what) {
    const char* e = std::getenv("NX_VK_FAKE_FAIL");
    return e && std::strcmp(e, what) == 0;
}

const char* vk_result_name(int r) {
    switch (r) {
        case 0: return "VK_SUCCESS";
        case -1: return "VK_ERROR_OUT_OF_HOST_MEMORY";
        case -2: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
        case -3: return "VK_ERROR_INITIALIZATION_FAILED";
        case -4: return "VK_ERROR_DEVICE_LOST";
        case -5: return "VK_ERROR_MEMORY_MAP_FAILED";
        case -6: return "VK_ERROR_LAYER_NOT_PRESENT";
        case -7: return "VK_ERROR_EXTENSION_NOT_PRESENT";
        case -8: return "VK_ERROR_FEATURE_NOT_PRESENT";
        case -9: return "VK_ERROR_INCOMPATIBLE_DRIVER";
        case -1000000000: return "VK_ERROR_SURFACE_LOST_KHR";
        case -1000000001: return "VK_ERROR_NATIVE_WINDOW_IN_USE_KHR";
        default: return "VkResult";
    }
}

// tag: short text for the log; what: what happened, for the box (English). has_result: result is a VkResult.
[[noreturn]] void vk_unusable(const char* tag, bool has_result, int result, [[maybe_unused]] const wchar_t* what) {
    char res[96] = "";
    if (has_result) std::snprintf(res, sizeof res, " (VkResult %d, %s)", result, vk_result_name(result));
    log("nvn/vk: Vulkan cannot be used: %s%s. Install or update the GPU driver. Stopping.", tag, res);
    std::fprintf(stderr, "nvn/vk: Vulkan cannot be used: %s%s. Install or update the GPU driver.\n", tag, res);
    std::fflush(stderr);
#if defined(_WIN32)
    wchar_t rw[96] = L"";
    if (has_result) std::swprintf(rw, sizeof rw / sizeof rw[0], L"VkResult: %d (%hs)\n", result, vk_result_name(result));
    wchar_t msg[2048];
    std::swprintf(msg, sizeof msg / sizeof msg[0],
                  L"The game needs Vulkan to draw, and it cannot be used.\n\n"
                  L"%ls\n%ls\n"
                  L"What to do: install or update the GPU driver from the manufacturer's site (Intel: Intel Driver & "
                  L"Support Assistant; AMD: Adrenalin; NVIDIA: GeForce/Studio), not from Windows Update, then start the "
                  L"game again. On Linux (Proton): install your distribution's Vulkan drivers.\n\n"
                  L"Details: nx.log",
                  what, rw);
    // MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST: not hidden behind the game window
    ::MessageBoxW(nullptr, msg, L"Vulkan unavailable", 0x10 | 0x10000 | 0x40000);
#endif
    _exit(6);
}

void* g_gipa = nullptr;  // vkGetInstanceProcAddr, for surfaces made later (make_surface)
bool make_surface();

#ifdef NX_VK_REQUIRE_13  // AP_VK_REQ (apply_vk_requirements.py)
// El render usa funciones core de Vulkan 1.3 (dynamic rendering, synchronization2): con un device 1.2 esos punteros
// quedan nulos y el primer submit salta a 0. Se elige una GPU que cumpla; si ninguna, un aviso claro y la salida.
// NX_VK_SKIP_CHECK=1: sin la verificacion.
bool vk_req_ok(VkPhysicalDevice pd, VkPhysicalDeviceProperties& p) {
    p = {};
    p_vkGetPhysicalDeviceProperties(pd, &p);
    if (VK_API_VERSION_MAJOR(p.apiVersion) == 1 && VK_API_VERSION_MINOR(p.apiVersion) < 3) return false;
    if (!p_vkGetPhysicalDeviceFeatures2) return false;
    VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
    f.pNext = &f13;
    p_vkGetPhysicalDeviceFeatures2(pd, &f);
    return f13.dynamicRendering && f13.synchronization2;
}

VkPhysicalDevice vk_req_pick(const std::vector<VkPhysicalDevice>& gpus, VkPhysicalDevice chosen) {
    const char* skip = std::getenv("NX_VK_SKIP_CHECK");
    if (!chosen || (skip && skip[0] == '1')) return chosen;
    VkPhysicalDeviceProperties p{};
    if (vk_req_ok(chosen, p)) return chosen;
    char why[400];
    std::snprintf(why, sizeof why, "%s, Vulkan %u.%u.%u, driver %08x", p.deviceName,
                  VK_API_VERSION_MAJOR(p.apiVersion), VK_API_VERSION_MINOR(p.apiVersion),
                  VK_API_VERSION_PATCH(p.apiVersion), p.driverVersion);
    if (!std::getenv("NX_GPU")) {  // otra GPU que cumpla, mejor una dedicada (NX_GPU elegida a mano: se respeta)
        VkPhysicalDevice best = VK_NULL_HANDLE;
        bool best_discrete = false;
        for (VkPhysicalDevice g : gpus) {
            VkPhysicalDeviceProperties q{};
            if (g == chosen || !vk_req_ok(g, q)) continue;
            const bool discrete = q.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
            if (!best || (discrete && !best_discrete)) best = g, best_discrete = discrete;
        }
        if (best) {
            VkPhysicalDeviceProperties q{};
            p_vkGetPhysicalDeviceProperties(best, &q);
            log("nvn/vk: %s: sin Vulkan 1.3 (dynamic rendering, synchronization2); se usa %s", why, q.deviceName);
            return best;
        }
    }
    log("nvn/vk: ninguna GPU con Vulkan 1.3 (dynamic rendering, synchronization2): %s. Actualizar el driver de la "
        "GPU (NX_VK_SKIP_CHECK=1 omite esta verificacion)", why);
    std::fprintf(stderr, "nvn/vk: se necesita Vulkan 1.3; la GPU tiene %s. Actualiza el driver de la GPU.\n", why);
    std::fflush(stderr);
#if defined(_WIN32)
    wchar_t msg[1024];
    std::swprintf(msg, sizeof msg / sizeof msg[0],
                  L"El juego necesita Vulkan 1.3 y esta GPU o su driver no lo soportan.\n\n"
                  L"GPU: %hs\n\n"
                  L"Actualiza el driver de la GPU desde la p\u00e1gina del fabricante (Intel: Intel Driver & Support "
                  L"Assistant; AMD: Adrenalin; NVIDIA: GeForce/Studio), no desde Windows Update.\n\n"
                  L"This GPU or its driver does not support Vulkan 1.3. Please update the GPU driver.\n\n"
                  L"NX_VK_SKIP_CHECK=1 omite esta verificaci\u00f3n.",
                  why);
    ::MessageBoxW(nullptr, msg, L"Vulkan 1.3 requerido", 0x10 /* MB_ICONERROR */);
#endif
    _exit(5);
}
#endif

bool init() {
    if (g_ready || g_failed) return g_ready;
    g_failed = true;  // until everything below succeeds
    auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(plat::vulkan_get_instance_proc_addr());
    if (vk_fake("loader")) gipa = nullptr;  // AP_VK_FAIL
    if (!gipa) {
        log("nvn/vk: no Vulkan loader");
        vk_unusable("the Vulkan loader (vulkan-1.dll) could not be loaded", false, 0,
                    L"vulkan-1.dll (the Vulkan loader) could not be loaded: no Vulkan driver is installed.");
    }
    for (size_t k = 0; k < g_entry_count; k++) *g_entries[k].pfn = gipa(nullptr, g_entries[k].name);
    p_enum_instance_ext = reinterpret_cast<PFN_vkEnumerateInstanceExtensionProperties>(
        gipa(nullptr, "vkEnumerateInstanceExtensionProperties"));
    p_create_instance = reinterpret_cast<PFN_vkCreateInstance>(gipa(nullptr, "vkCreateInstance"));

    uint32_t n = 0;
    p_enum_instance_ext(nullptr, &n, nullptr);
    std::vector<VkExtensionProperties> iext(n);
    p_enum_instance_ext(nullptr, &n, iext.data());
    std::vector<const char*> exts = {VK_KHR_SURFACE_EXTENSION_NAME, plat::vulkan_surface_extension()};
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    if (has_extension(iext, "VK_KHR_portability_enumeration")) {
        exts.push_back("VK_KHR_portability_enumeration");
        ici.flags |= 0x00000001;  // VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR
    }
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = nx::game::kInfo.title;
    app.apiVersion = VK_API_VERSION_1_3;
    ici.pApplicationInfo = &app;
    ici.enabledExtensionCount = uint32_t(exts.size());
    ici.ppEnabledExtensionNames = exts.data();
    {   // AP_VK_FAIL
        const VkResult ri = vk_fake("instance") ? VK_ERROR_INCOMPATIBLE_DRIVER
                                                : p_create_instance(&ici, nullptr, &g_instance);
        if (!check(ri, "vkCreateInstance"))
            vk_unusable("vkCreateInstance failed", true, int(ri),
                        L"The Vulkan instance could not be created (no Vulkan driver, or an incompatible one).");
    }
    for (size_t k = 0; k < g_entry_count; k++)
        if (auto p = gipa(g_instance, g_entries[k].name)) *g_entries[k].pfn = p;
    p_enum_physical = reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(gipa(g_instance, "vkEnumeratePhysicalDevices"));
    p_create_device = reinterpret_cast<PFN_vkCreateDevice>(gipa(g_instance, "vkCreateDevice"));

    n = 0;
    const VkResult enum_r = p_enum_physical(g_instance, &n, nullptr);  // AP_VK_FAIL
    std::vector<VkPhysicalDevice> gpus(n);
    p_enum_physical(g_instance, &n, gpus.data());
    gpus.resize(n);
    g_physical = pick_physical_device(gpus);  // NX_GPU, default: the first discrete GPU (nvn_gpu_pick.cpp)
    if (vk_fake("gpu")) g_physical = VK_NULL_HANDLE;  // AP_VK_FAIL
    if (!g_physical) {
        log("nvn/vk: no GPU");
        vk_unusable("no Vulkan GPU found", true, int(enum_r),
                    L"No GPU with Vulkan support was found.");
    }
#ifdef NX_VK_REQUIRE_13
    g_physical = vk_req_pick(gpus, g_physical);  // AP_VK_REQ: Vulkan 1.3, o un aviso y la salida
#endif
    VkPhysicalDeviceProperties props{};
    p_vkGetPhysicalDeviceProperties(g_physical, &props);
    p_vkGetPhysicalDeviceMemoryProperties(g_physical, &g_memory);
    g_integrated = props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
    // An integrated GPU shares the package's power budget with the CPU: ask the desktop for its performance profile
    // (NX_POWER_PROFILE=1: also with a discrete GPU; 0: never). frame_stats.cpp.
    if (g_integrated || (std::getenv("NX_POWER_PROFILE") && std::getenv("NX_POWER_PROFILE")[0] == '1'))
        plat::request_performance_profile();

    g_gipa = reinterpret_cast<void*>(gipa);
    if (!make_surface())
        vk_unusable("the window surface could not be created", true, g_surface_result,  // AP_VK_FAIL
                    L"The Vulkan surface for the game window could not be created.");

    p_vkGetPhysicalDeviceQueueFamilyProperties(g_physical, &n, nullptr);
    std::vector<VkQueueFamilyProperties> families(n);
    p_vkGetPhysicalDeviceQueueFamilyProperties(g_physical, &n, families.data());
    g_family = UINT32_MAX;
    for (uint32_t k = 0; k < n; k++) {
        VkBool32 present = VK_FALSE;
        p_vkGetPhysicalDeviceSurfaceSupportKHR(g_physical, k, g_surface, &present);
        if ((families[k].queueFlags & VK_QUEUE_GRAPHICS_BIT) && present) {
            g_family = k;
            break;
        }
    }
    if (g_family == UINT32_MAX) {
        log("nvn/vk: no graphics + present queue");
        vk_unusable("no queue family can draw and present", false, 0,  // AP_VK_FAIL
                    L"The chosen GPU cannot draw and present to the game window.");
    }

    p_vkEnumerateDeviceExtensionProperties(g_physical, nullptr, &n, nullptr);
    std::vector<VkExtensionProperties> dext(n);
    p_vkEnumerateDeviceExtensionProperties(g_physical, nullptr, &n, dext.data());
    std::vector<const char*> dexts = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    // Features: everything core the GPU has, dynamic rendering (1.3), 8-bit indices (NVN has them), clip-space
    // depth -1..1 (NVN's default depth mode, what Wonder uses), and on MoltenVK the portability subset's own
    // features, which must be enabled to be used.
    VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
    VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceIndexTypeUint8FeaturesEXT fu8{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT};
    VkPhysicalDeviceDepthClipControlFeaturesEXT fclip{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DEPTH_CLIP_CONTROL_FEATURES_EXT};
    struct {  // VkPhysicalDevicePortabilitySubsetFeaturesKHR, without its header (beta extensions)
        VkStructureType sType = VkStructureType(1000163000);
        void* pNext = nullptr;
        VkBool32 fields[15] = {};
    } fport;
    features.pNext = &f13;
    void** tail = &f13.pNext;
    if (has_extension(dext, "VK_EXT_index_type_uint8")) {
        dexts.push_back("VK_EXT_index_type_uint8");
        *tail = &fu8, tail = &fu8.pNext;
    }
    if (has_extension(dext, "VK_EXT_depth_clip_control")) {
        dexts.push_back("VK_EXT_depth_clip_control");
        *tail = &fclip, tail = &fclip.pNext;
    }
    if (has_extension(dext, "VK_KHR_portability_subset")) {
        dexts.push_back("VK_KHR_portability_subset");
        *tail = &fport, tail = &fport.pNext;
    }
    p_vkGetPhysicalDeviceFeatures2(g_physical, &features);
    {   // The translated shaders assume Maxwell's warp of 32 (TRANSLATOR.md). Intel runs subgroups of 8, 16 or 32 as its
        // compiler chooses; where the size can be pinned (core 1.3 subgroup size control), compute pipelines pin 32.
        VkPhysicalDeviceSubgroupSizeControlProperties sgc{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES};
        VkPhysicalDeviceProperties2 p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        p2.pNext = &sgc;
        p_vkGetPhysicalDeviceProperties2(g_physical, &p2);
        const char* e = std::getenv("NX_SUBGROUP32");
        bool can = f13.subgroupSizeControl && sgc.minSubgroupSize <= 32 && sgc.maxSubgroupSize >= 32 &&
                   (sgc.requiredSubgroupSizeStages & VK_SHADER_STAGE_COMPUTE_BIT);
        g_require_sg32 = can && e && e[0] == '1';  // experimental, off by default: no visible effect on the Intel silhouettes
        log("nvn/vk: subgroup sizes %u..%u, compute subgroup size %s", sgc.minSubgroupSize, sgc.maxSubgroupSize,
            g_require_sg32 ? "pinned to 32" : "left to the driver");
    }
    g_depth_clip_control = fclip.depthClipControl;
    g_features = features.features;
    // NX_ASTC_DECODE=1|linear|srgb: the CPU decoder even where the GPU has ASTC (to compare the two on one GPU).
    set_astc_native(g_features.textureCompressionASTC_LDR);
    log("nvn/vk: ASTC %s", astc_native() ? "native" : "decoded to RGBA8 at upload (no textureCompressionASTC_LDR)");
    log("nvn/vk: depthClamp %u, depthBiasClamp %u, depthBounds %u, depth clip control %u (extension %u)",
        g_features.depthClamp, g_features.depthBiasClamp, g_features.depthBounds, g_depth_clip_control,
        has_extension(dext, "VK_EXT_depth_clip_control") ? 1u : 0u);
    log("nvn/vk: portability: format reinterpretation %u, format swizzle %u", fport.fields[2], fport.fields[3]);
    log_device_caps(g_physical, dext);  // nvn_caps.cpp
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = g_family;
    qci.queueCount = 1;
    qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qci;
    dci.enabledExtensionCount = uint32_t(dexts.size());
    dci.ppEnabledExtensionNames = dexts.data();
    dci.pNext = &features;
    {   // AP_VK_FAIL
        const VkResult rd = vk_fake("device") ? VK_ERROR_INITIALIZATION_FAILED
                                               : p_create_device(g_physical, &dci, nullptr, &g_device);
        if (!check(rd, "vkCreateDevice"))
            vk_unusable("vkCreateDevice failed", true, int(rd),
                        L"The Vulkan device could not be created on the GPU (incomplete or faulty driver).");
    }
    p_vkGetDeviceQueue(g_device, g_family, 0, &g_queue);

    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    pci.queueFamilyIndex = g_family;
    p_vkCreateCommandPool(g_device, &pci, nullptr, &g_pool);
    VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cai.commandPool = g_pool;
    cai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cai.commandBufferCount = 1;
    p_vkAllocateCommandBuffers(g_device, &cai, &g_cb);
    VkFenceCreateInfo fci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    p_vkCreateFence(g_device, &fci, nullptr, &g_fence);
    VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    p_vkCreateSemaphore(g_device, &sci, nullptr, &g_acquired);
    p_vkCreateSemaphore(g_device, &sci, nullptr, &g_rendered);

    // A busy submit reached 67 MB (uniforms padded to 4 KB, storage to 64 KB). Android: 128 MB, half, for an 8 GB
    // phone (docs/nvn/android-perf/MEMORY.md: the phone never filled it; big uploads have staging buffers, #72).
#if defined(__ANDROID__)
    make_upload_buffer(128ull << 20);
#else
    make_upload_buffer(256ull << 20);
#endif
    if (async_gpu())
        frame_init(g_pool, g_family, g_upload, g_upload_ptr, g_upload_size);  // the upload buffer: the slots' ring
    else
        g_descriptors = make_descriptor_pool();
    log("nvn/vk: %s, Vulkan %u.%u, queue family %u", props.deviceName, VK_API_VERSION_MAJOR(props.apiVersion),
        VK_API_VERSION_MINOR(props.apiVersion), g_family);
    g_failed = false;
    g_ready = true;
    return true;
}

// The window's surface, and the swapchain format from what the surface offers: B8G8R8A8_UNORM (the Mac), else
// R8G8B8A8_UNORM (Android surfaces offer no BGRA: a swapchain made with it showed nothing on a Galaxy S23), else the
// first. A new Android window (the app back from home or the lock screen) needs a new surface (gpu_present).
void* g_window = nullptr;
uint64_t g_window_gen = 0;
bool make_surface() {
    if (g_surface) p_vkDestroySurfaceKHR(g_instance, g_surface, nullptr);
    if (g_window) plat::release_window(g_window);
    g_surface = VK_NULL_HANDLE;
    g_window = plat::create_window();  // waits for a window (Android); the generation is read once it is there
    g_window_gen = plat::window_generation();
    uint64_t surface = 0;
    const VkResult sr = VkResult(plat::vulkan_create_surface(g_instance, g_window, &surface, g_gipa));  // AP_VK_FAIL
    if (!check(sr, "surface")) {
        g_surface_result = int(sr);
        return false;
    }
    g_surface = reinterpret_cast<VkSurfaceKHR>(surface);
    uint32_t n = 0;
    p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &n, nullptr);
    std::vector<VkSurfaceFormatKHR> formats(n);
    p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &n, formats.data());
    auto offered = [&](VkFormat f) {
        for (const VkSurfaceFormatKHR& x : formats)
            if (x.format == f) return true;
        return false;
    };
    g_sc_format = offered(VK_FORMAT_B8G8R8A8_UNORM)   ? VK_FORMAT_B8G8R8A8_UNORM
                  : offered(VK_FORMAT_R8G8B8A8_UNORM) ? VK_FORMAT_R8G8B8A8_UNORM
                  : n                                  ? formats[0].format
                                                       : VK_FORMAT_B8G8R8A8_UNORM;
    log("nvn/vk: surface: %u formats offered, swapchain format %d", n, int(g_sc_format));
    return true;
}

bool make_swapchain() {
    if (g_swapchain) {
        if (async_gpu()) frame_wait_all();
        p_vkDeviceWaitIdle(g_device);
        present_pass_release();
        p_vkDestroySwapchainKHR(g_device, g_swapchain, nullptr);
        g_swapchain = VK_NULL_HANDLE;
    }
    VkSurfaceCapabilitiesKHR caps{};
    p_vkGetPhysicalDeviceSurfaceCapabilitiesKHR(g_physical, g_surface, &caps);
    VkExtent2D extent = caps.currentExtent;
    if (extent.width == UINT32_MAX) {
        uint32_t w = 0, h = 0;
        plat::window_size(w, h);
        extent = {w ? w : 1280, h ? h : 720};
    }
    if (!extent.width || !extent.height) return false;  // minimized
    VkSwapchainCreateInfoKHR ci{VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
    ci.surface = g_surface;
    ci.minImageCount = std::max(caps.minImageCount, 3u);
    if (caps.maxImageCount) ci.minImageCount = std::min(ci.minImageCount, caps.maxImageCount);
    ci.imageFormat = g_sc_format;
    ci.imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
    ci.imageExtent = extent;
    ci.imageArrayLayers = 1;
    ci.imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    ci.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ci.preTransform = caps.currentTransform;
    // The present pass (nvn_present.cpp) draws the picture pre-rotated: the images are in the display's own
    // orientation (portrait on a phone held sideways). Without it the images stay upright (the Mac: identity).
    bool sideways = caps.currentTransform & (VK_SURFACE_TRANSFORM_ROTATE_90_BIT_KHR | VK_SURFACE_TRANSFORM_ROTATE_270_BIT_KHR);
    bool pass = present_pass_wanted() && !present_pass_failed();
    g_sc_current = caps.currentExtent;
    if (pass && sideways && extent.width > extent.height) std::swap(extent.width, extent.height);
    if (!pass && (caps.supportedTransforms & VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR))
        ci.preTransform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
    // Some drivers report maxImageExtent in the other orientation: the larger side bounds both.
    uint32_t most = std::max(caps.maxImageExtent.width, caps.maxImageExtent.height);
    if (most) extent.width = std::min(extent.width, most), extent.height = std::min(extent.height, most);
    ci.imageExtent = extent;
    ci.compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(caps.supportedCompositeAlpha & ci.compositeAlpha))
        for (VkCompositeAlphaFlagBitsKHR a : {VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR, VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR,
                                              VK_COMPOSITE_ALPHA_POST_MULTIPLIED_BIT_KHR})
            if (caps.supportedCompositeAlpha & a) {
                ci.compositeAlpha = a;
                break;
            }
    log("nvn/vk: surface %ux%u (current %ux%u), transform %u of %#x, composite alpha %#x of %#x, usage %#x",
        extent.width, extent.height, caps.currentExtent.width, caps.currentExtent.height, unsigned(caps.currentTransform),
        caps.supportedTransforms, unsigned(ci.compositeAlpha), caps.supportedCompositeAlpha,
        caps.supportedUsageFlags);
    // The game paces itself on its vsync event (hle_vi.cpp, 60 Hz): a FIFO present also waited for the display's own
    // refresh, two clocks drifting against each other (stutter near 60 fps, docs/nvn/DEBUG.md #33). MAILBOX never
    // blocks and shows the newest frame at each refresh; else IMMEDIATE (MoltenVK: no display sync in the Metal
    // layer, the macOS compositor still shows whole frames at its refresh), FIFO last. NX_FIFO=1: FIFO (DEBUG.md #62).
    ci.presentMode = VK_PRESENT_MODE_FIFO_KHR;
    uint32_t modes_n = 0;
    p_vkGetPhysicalDeviceSurfacePresentModesKHR(g_physical, g_surface, &modes_n, nullptr);
    std::vector<VkPresentModeKHR> modes(modes_n);
    p_vkGetPhysicalDeviceSurfacePresentModesKHR(g_physical, g_surface, &modes_n, modes.data());
    auto has = [&](VkPresentModeKHR m) { return std::find(modes.begin(), modes.end(), m) != modes.end(); };
    if (!std::getenv("NX_FIFO")) {
        if (has(VK_PRESENT_MODE_MAILBOX_KHR))
            ci.presentMode = VK_PRESENT_MODE_MAILBOX_KHR;
        else if (has(VK_PRESENT_MODE_IMMEDIATE_KHR))
            ci.presentMode = VK_PRESENT_MODE_IMMEDIATE_KHR;
    }
    static const char* const mode_names[] = {"IMMEDIATE", "MAILBOX", "FIFO", "FIFO_RELAXED"};
    log("nvn/vk: present mode %s (%u modes offered)", ci.presentMode <= 3 ? mode_names[ci.presentMode] : "?", modes_n);
    ci.clipped = VK_TRUE;
    if (!check(p_vkCreateSwapchainKHR(g_device, &ci, nullptr, &g_swapchain), "vkCreateSwapchainKHR")) return false;
    uint32_t n = 0;
    p_vkGetSwapchainImagesKHR(g_device, g_swapchain, &n, nullptr);
    g_sc_images.resize(n);
    p_vkGetSwapchainImagesKHR(g_device, g_swapchain, &n, g_sc_images.data());
    g_sc_extent = extent;
    g_sc_transform = ci.preTransform;
    if (n > 8) log("nvn/vk: %u swapchain images: more than the 8 present semaphores (nvn_frames.cpp)", n);
    bool drawn = pass && present_pass_targets(g_sc_format, g_sc_images, extent, ci.preTransform);
    if (pass && !drawn && present_pass_failed()) {  // the pass is out: an upright swapchain for the blit
        log("nvn/vk: present pass failed: swapchain made again for the blit");
        return make_swapchain();
    }
    log("nvn/vk: swapchain %ux%u, %u images, transform %u, %s", extent.width, extent.height, n,
        unsigned(ci.preTransform), drawn ? "drawn by the present pass" : "blitted");
    return true;
}

}  // namespace

std::recursive_mutex& gpu_lock() { return g_gpu; }
VkDevice vk_device() { return g_device; }
VkPhysicalDevice vk_physical() { return g_physical; }
VkQueue vk_queue() { return g_queue; }
uint32_t vk_queue_family() { return g_family; }
bool vk_depth_clip_control() { return g_depth_clip_control; }
const VkPhysicalDeviceFeatures& vk_features() { return g_features; }
bool gpu_is_integrated() { return g_integrated; }
bool gpu_require_subgroup32() { return g_require_sg32; }

uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t k = 0; k < g_memory.memoryTypeCount; k++)
        if ((bits >> k) & 1 && (g_memory.memoryTypes[k].propertyFlags & want) == want) return k;
    return 0;
}

Upload upload_alloc(uint64_t size, uint64_t align) {
    if (async_gpu()) return frame_upload_alloc(size, align);
    uint64_t at = (g_upload_used + align - 1) & ~(align - 1);
    if (at + size > g_upload_size) {
        log("nvn/vk: upload buffer full (%llu + %llu bytes)", (unsigned long long)at, (unsigned long long)size);
        at = 0;  // wraps: this submit's earlier data may be overwritten (grow the buffer when this shows)
        replay::dedup_reset();  // shared copies may be among them
    }
    g_upload_used = at + size;
    return {g_upload, at, g_upload_ptr + at};
}

Upload upload(const void* data, uint64_t size, uint64_t align) {
    Upload u = upload_alloc(size, align);
    if (data && size) std::memcpy(u.ptr, data, size);
    return u;
}

VkDescriptorSet descriptor_set(VkDescriptorSetLayout layout) {
    if (async_gpu()) return frame_descriptor_set(layout);
    VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    ai.descriptorPool = g_descriptors;
    ai.descriptorSetCount = 1;
    ai.pSetLayouts = &layout;
    VkDescriptorSet set = VK_NULL_HANDLE;
    if (p_vkAllocateDescriptorSets(g_device, &ai, &set) != VK_SUCCESS) log("nvn/vk: descriptor pool exhausted");
    return set;
}

VkDescriptorPool make_descriptor_pool() {
    VkDescriptorPoolSize sizes[] = {{VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 65536}, {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 16384},
                                    {VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 65536}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 4096},
                                    {VK_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER, 1024}, {VK_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER, 1024},
                                    {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC, 65536}};
    VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpi.maxSets = 32768;
    dpi.poolSizeCount = uint32_t(sizeof sizes / sizeof sizes[0]);
    dpi.pPoolSizes = sizes;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    p_vkCreateDescriptorPool(g_device, &dpi, nullptr, &pool);
    return pool;
}

namespace {

void make_upload_buffer(uint64_t size) {
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = size;
    bi.usage = kUploadUsage;
    p_vkCreateBuffer(g_device, &bi, nullptr, &g_upload);
    VkMemoryRequirements req{};
    p_vkGetBufferMemoryRequirements(g_device, g_upload, &req);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = upload_memory_type(req.memoryTypeBits);  // nvn_caps.cpp (NX_RING_CACHED)
    p_vkAllocateMemory(g_device, &ai, nullptr, &g_upload_memory);
    p_vkBindBufferMemory(g_device, g_upload, g_upload_memory, 0);
    void* p = nullptr;
    p_vkMapMemory(g_device, g_upload_memory, 0, VK_WHOLE_SIZE, 0, &p);
    g_upload_ptr = static_cast<uint8_t*>(p);
    g_upload_size = size;
}

bool begin(bool replay = false) {
    if (async_gpu()) return (g_cb = frame_begin(replay)) != VK_NULL_HANDLE;
    p_vkResetCommandBuffer(g_cb, 0);
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    return check(p_vkBeginCommandBuffer(g_cb, &bi), "vkBeginCommandBuffer");
}

// Where the submitting thread's time goes, logged with the draw stats, per presented frame: the replay (recording
// into Vulkan), vkQueueSubmit (Metal encoding happens inside it), fences waited for, the rest of gpu_submit (object
// drain, image frees, write-backs), and gpu_present as a whole; bytes through the upload buffer.
struct Timing {
    uint64_t submits = 0, replay_ns = 0, encode_ns = 0, fence_ns = 0, submit_ns = 0, present_ns = 0, upload_bytes = 0;
} g_timing;

void end_and_wait(VkSemaphore wait, VkSemaphore signal) {
    uint64_t t0 = plat::monotonic_ns();
    p_vkEndCommandBuffer(g_cb);
    VkPipelineStageFlags stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &g_cb;
    if (wait) {
        si.waitSemaphoreCount = 1;
        si.pWaitSemaphores = &wait;
        si.pWaitDstStageMask = &stage;
    }
    if (signal) {
        si.signalSemaphoreCount = 1;
        si.pSignalSemaphores = &signal;
    }
    p_vkResetFences(g_device, 1, &g_fence);
    bool queued = check(p_vkQueueSubmit(g_queue, 1, &si, g_fence), "vkQueueSubmit");
    uint64_t t1 = plat::monotonic_ns();
    // 2 s at most: a frame's work takes milliseconds; longer is a shader that does not end.
    if (queued) {
        VkResult w = p_vkWaitForFences(g_device, 1, &g_fence, VK_TRUE, gpu_hang_timeout_ns());  // AP_GPU_HANG
        if (w == VK_SUCCESS && plat::monotonic_ns() - t1 > 2'000'000'000ull)
            log("nvn/vk: a submit took %.1f s", double(plat::monotonic_ns() - t1) / 1e9);
        if (w == VK_TIMEOUT) gpu_hung("a submit did not finish in 2 s");
        check(w, "vkWaitForFences");
    }
    frame_times().encode_ns += t1 - t0, frame_times().fence_ns += plat::monotonic_ns() - t1;
}

void log_timing(uint64_t frames) {
    auto ms = [&](uint64_t ns) { return double(ns) / 1e6 / double(frames); };
    static uint64_t last = 0;  // wall clock: the real frame rate
    uint64_t now = plat::monotonic_ns();
    double fps = last ? double(frames) * 1e9 / double(now - last) : 0.0;
    last = now;
    const Timing& t = g_timing;
    uint64_t known = t.replay_ns + t.encode_ns + t.fence_ns;
    log("nvn/vk: per frame over %llu: %.1f fps, %.1f submits, replay %.2f ms, submit (encode) %.2f ms, fence wait "
        "%.2f ms, other %.2f ms, present %.2f ms, upload %.2f MB",
        (unsigned long long)frames, fps, double(t.submits) / double(frames), ms(t.replay_ns), ms(t.encode_ns),
        ms(t.fence_ns), ms(t.submit_ns > known ? t.submit_ns - known : 0), ms(t.present_ns),
        double(t.upload_bytes) / 1e6 / double(frames));
    frame_log(frames);
    creation_log(frames);  // pipelines and ASTC decodes of the window; saves the pipeline cache now and then
    g_timing = {};
}

// gpu_present's time, whatever way it returns.
struct PresentTimer {
    uint64_t t0 = plat::monotonic_ns();
    ~PresentTimer() { g_timing.present_ns += plat::monotonic_ns() - t0; }
};

}  // namespace

uint64_t draw_take_dump_request();          // nvn_draw.cpp

namespace {
std::shared_ptr<const DeferredValues> g_deferred;  // the submit being replayed (replay thread only)
}
const DeferredValues* current_deferred() { return g_deferred.get(); }

void gpu_submit(const std::vector<std::shared_ptr<CmdList>>& lists, std::shared_ptr<const DeferredValues> deferred) {
    g_deferred = std::move(deferred);
    std::lock_guard lk(g_gpu);
    uint64_t t_in = plat::monotonic_ns();
    const FrameTimes f_in = frame_times();
    uint64_t lists_epoch = 0;
    for (auto& l : lists) lists_epoch = std::max(lists_epoch, l->epoch);
    ReplayScope replay(lists_epoch);  // the texture mark (#55, #57), then drain_objects (under the GPU lock, #50)
    if (!init()) return;
    bool async = async_gpu();
    if (!async) {  // the previous submit was waited for: all of it is free
        free_retired_images(UINT64_MAX);
        p_vkResetDescriptorPool(g_device, g_descriptors, 0);
        g_upload_used = 0;
    } else if (pipelined()) {  // what the retired submits were the last to use (they free nothing themselves)
        free_retired_images(frame_completed());
    }
    if (!begin(true)) return;
    draw_begin_submit();
    gpu_profile_begin(g_cb);
    zero_draws_begin_submit(g_cb);
    // Pipelined: the part up to the last dispatch is queued as soon as it is recorded (nvn_frames.cpp). Not with the
    // profile or the zero-draw queries: they span the submit, which is then waited for whole.
    size_t split_after = gpu_profile_on() || zero_draws_on() ? SIZE_MAX : frame_plan_split(lists);
    uint64_t t0 = plat::monotonic_ns();
    const FrameTimes f0 = frame_times();
    Targets st;
    for (size_t k = 0; k < lists.size(); k++) {
        replay_list(g_cb, st, *lists[k], 0);
        // After the last list with a dispatch, unless nothing follows it (replay_list may have split inside a list).
        g_cb = k == split_after && k + 1 < lists.size() ? frame_split(g_cb) : frame_cb(g_cb);
    }
    draw_end_rendering(g_cb);
    const FrameTimes f1 = frame_times();  // the first part's submit and ring waits are not replay time
    g_timing.submits++;
    g_timing.replay_ns += plat::monotonic_ns() - t0 - (f1.encode_ns - f0.encode_ns) - (f1.fence_ns - f0.fence_ns);
    g_timing.upload_bytes += async ? frame_upload_used() : g_upload_used;
    gpu_profile_mark(g_cb, "end");
    if (async)  // the profile, zero-draw queries and snapshots read this submit's results right below: waited for
        frame_submit(VK_NULL_HANDLE, VK_NULL_HANDLE, draw_take_write_backs(),
                     gpu_profile_on() || zero_draws_on() || debug_snapshots_pending());
    else
        end_and_wait(VK_NULL_HANDLE, VK_NULL_HANDLE);
    draw_end_submit();  // not waited for: snapshots only (the write-backs went with the submit)
    gpu_profile_collect(), zero_draws_collect();
    debug_dump_requested(draw_take_dump_request());  // reads back in a submit of its own, which it waits for
    const FrameTimes f2 = frame_times();
    g_timing.encode_ns += f2.encode_ns - f_in.encode_ns, g_timing.fence_ns += f2.fence_ns - f_in.fence_ns;
    g_timing.submit_ns += plat::monotonic_ns() - t_in;
}

// Headless: the presented texture is only counted, logged and dumped (no swapchain). Nothing is submitted unless a
// frame dump is due: the empty submit was a Metal command buffer and a fence wait per frame.
void present_headless(uint64_t texture) {
    GpuImage* src = image_of(texture);  // made here if new, as before (the targets rendered into it share it, #44)
    static uint64_t presents = 0;
    presents++;
    if (presents % (presents < 300 ? 60 : 300) == 0) draw_log_stats();
    if (presents % 60 == 0) log_timing(60), gpu_profile_log(60);
    if (presents % 300 == 0) zero_draws_log();
    // NX_PRESENT_TEST_ALL=1 (with NX_PRESENT_TEST): the present pass runs for every frame, and its submit is timed.
    static const bool every = std::getenv("NX_PRESENT_TEST_ALL") != nullptr;
    bool due = frame_dump_due(presents);
    if (!due && !every) return;
    static uint64_t timed_ns = 0, timed = 0;
    const uint64_t t0 = plat::monotonic_ns();
    if (src && src->layout != VK_IMAGE_LAYOUT_UNDEFINED && begin()) {
        GpuImage* shown = src;  // NX_PRESENT_TEST: what the present pass would put on a display (nvn_present.cpp)
        if (GpuImage* test = present_pass_test_target(); test && present_pass_record(g_cb, *src, nullptr, 0))
            test->layout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, shown = test;
        transition(g_cb, *shown, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        bool dump = due && frame_dump_record(g_cb, *shown);
        if (async_gpu())
            frame_submit(VK_NULL_HANDLE, VK_NULL_HANDLE, {}, dump);  // a dumped frame is waited for
        else
            end_and_wait(VK_NULL_HANDLE, VK_NULL_HANDLE);
        if (dump) frame_dump_write(*shown, presents);
        if (every && !due && ++timed % 120 == 0) {
            log("nvn/vk: present test: pass + wait %.2f ms/frame over the last %llu", double(timed_ns) / 1e6 / 120, 120ull);
            timed_ns = 0;
        } else if (every && !due) {
            timed_ns += plat::monotonic_ns() - t0;
        }
    }
    debug_dump_textures(presents);
}

// AP_ASPECT (apply_aspect.py, platform.h picture_rect): the size of what was last presented (the window
// texture, or its crop), and whether it is shown with its aspect: NX_FIXED_ASPECT, or the present pass (it fits
// with bars, nvn_present.cpp). Otherwise the blit stretches it over the whole swapchain.
static std::atomic<uint32_t> g_pic_w{0}, g_pic_h{0};
static bool picture_keeps_aspect() {
#ifdef NX_FIXED_ASPECT
    return true;
#else
    return present_pass_wanted() && !present_pass_failed();
#endif
}

// NX_ORIGIN_LOWER_LEFT=1: NVN's default lower-left window origin (OpenGL's) instead of the upper-left one Wonder
// sets. apply_origin_lower_left.py
static bool origin_lower_left() {
    static const bool on = [] {
        const char* e = std::getenv("NX_ORIGIN_LOWER_LEFT");  // 1 / 0; sin ella, el default del juego
#ifdef NX_DEFAULT_ORIGIN_LOWER_LEFT  // games/<juego>/game.cmake (apply_origin_default.py)
        if (!e) return true;
#endif
        return e && e[0] == '1';
    }();
    return on;
}

// The swapchain's format from the window texture the game presents (apply_srgb_present.py): a blit from an sRGB
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

void gpu_present(uint64_t texture, const int32_t* crop) {
    plat::perf_hint_thread();  // the thread that presents takes part in the frame (perf_hint.cpp)
    std::lock_guard lk(g_gpu);
    PresentTimer timer;
    drain_objects();  // under the GPU lock, as in gpu_submit
    if (!init()) return;
    if (!async_gpu() || pipelined()) free_retired_images(async_gpu() ? frame_completed() : UINT64_MAX);
    if (plat::headless()) return present_headless(texture);
    if (plat::window_minimized()) return;
    if (plat::window_generation() != g_window_gen) {  // Android: a new window after home or the lock screen
        if (async_gpu()) frame_wait_all();
        p_vkDeviceWaitIdle(g_device);
        present_pass_release();
        if (g_swapchain) p_vkDestroySwapchainKHR(g_device, g_swapchain, nullptr);
        g_swapchain = VK_NULL_HANDLE;
        log("nvn/vk: new window: surface and swapchain re-made");
        if (!make_surface()) return;
    }
    if (!g_swapchain) pick_swapchain_format(image_of(texture));  // apply_srgb_present.py
    if (!g_swapchain && !make_swapchain()) return;
    uint32_t index = 0;
    VkSemaphore acquired = async_gpu() ? frame_acquire_semaphore() : g_acquired;
    VkResult r = p_vkAcquireNextImageKHR(g_device, g_swapchain, UINT64_MAX, acquired, VK_NULL_HANDLE, &index);
    if (r == VK_ERROR_OUT_OF_DATE_KHR) {
        make_swapchain();
        return;
    }
    if (!check(r, "vkAcquireNextImageKHR") || !begin()) return;
    GpuImage* src = image_of(texture);
    GpuImage dst;
    dst.image = g_sc_images[index];
    dst.format = g_sc_format;
    if (src) {
        if (src->layout == VK_IMAGE_LAYOUT_UNDEFINED) {  // never rendered: dark grey, to tell it from a failure
            transition(g_cb, *src, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
            VkClearColorValue v{{0.08f, 0.08f, 0.1f, 1.0f}};
            VkImageSubresourceRange rr{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            p_vkCmdClearColorImage(g_cb, src->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &v, 1, &rr);
        }
    }
    if (src) {  // AP_ASPECT: the picture's size, for picture_rect (the same crop test as below)
        const bool c = crop && crop[2] > 0 && crop[3] > 0 && crop[0] + crop[2] <= int32_t(src->width) &&
                       crop[1] + crop[3] <= int32_t(src->height);
        g_pic_w = c ? uint32_t(crop[2]) : src->width;
        g_pic_h = c ? uint32_t(crop[3]) : src->height;
    }
    bool drawn = src && present_pass_record(g_cb, *src, crop, index);  // nvn_present.cpp: leaves dst PRESENT_SRC
    if (drawn && origin_lower_left()) {
        static bool warned = false;
        if (!warned) warned = true, log("nvn/vk: NX_ORIGIN_LOWER_LEFT: the present pass does not flip the window texture");
    }
    if (src && !drawn) transition(g_cb, *src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
    if (!drawn) transition(g_cb, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    if (src && !drawn) {
        VkImageBlit blit{};
        blit.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        blit.srcOffsets[1] = {int32_t(src->width), int32_t(src->height), 1};
        if (crop && crop[2] > 0 && crop[3] > 0 && crop[0] + crop[2] <= int32_t(src->width) &&
            crop[1] + crop[3] <= int32_t(src->height))  // nvnWindowSetCrop
            blit.srcOffsets[0] = {crop[0], crop[1], 0}, blit.srcOffsets[1] = {crop[0] + crop[2], crop[1] + crop[3], 1};
        if (origin_lower_left()) std::swap(blit.srcOffsets[0].y, blit.srcOffsets[1].y);  // row 0 is the bottom
        blit.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        blit.dstOffsets[1] = {int32_t(g_sc_extent.width), int32_t(g_sc_extent.height), 1};
#ifdef NX_FIXED_ASPECT  // AP_ASPECT (apply_aspect.py): the picture keeps its aspect, black bars around it
        if (const plat::PictureRect pr = plat::picture_rect(g_sc_extent.width, g_sc_extent.height);
            pr.w && pr.h && (pr.w != g_sc_extent.width || pr.h != g_sc_extent.height)) {
            VkClearColorValue black{{0.0f, 0.0f, 0.0f, 1.0f}};
            VkImageSubresourceRange rr{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            p_vkCmdClearColorImage(g_cb, dst.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &black, 1, &rr);
            VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER};  // the clear before the blit's write
            mb.srcAccessMask = mb.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            p_vkCmdPipelineBarrier(g_cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0,
                                   nullptr, 0, nullptr);
            blit.dstOffsets[0] = {pr.x, pr.y, 0};
            blit.dstOffsets[1] = {pr.x + int32_t(pr.w), pr.y + int32_t(pr.h), 1};
        }
#endif
        p_vkCmdBlitImage(g_cb, src->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, dst.image,
                         VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit, VK_FILTER_LINEAR);
    }
    if (!drawn) transition(g_cb, dst, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR);
    static uint64_t presents = 0;
    presents++;
    if (presents % (presents < 300 ? 60 : 300) == 0) draw_log_stats();
    if (presents % 60 == 0) log_timing(60), gpu_profile_log(60);
    if (presents % 300 == 0) zero_draws_log();
    frame_sig_flush(presents);
    const bool due = frame_dump_due(presents);
    bool dump = src && due;
    if (dump) transition(g_cb, *src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);  // the present pass left it sampled
    dump = dump && frame_dump_record(g_cb, *src);
    VkSemaphore rendered = async_gpu() ? frame_rendered_semaphore(index) : g_rendered;
    if (async_gpu())  // the blit's fence is not waited for (vkQueuePresentKHR waits on `rendered`); a dumped frame is
        frame_submit(acquired, rendered, {}, dump);
    else
        end_and_wait(acquired, rendered);
    if (dump) frame_dump_write(*src, presents);
    if (due) debug_dump_textures(presents);
    VkPresentInfoKHR pi{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};
    pi.waitSemaphoreCount = 1;
    pi.pWaitSemaphores = &rendered;
    pi.swapchainCount = 1;
    pi.pSwapchains = &g_swapchain;
    pi.pImageIndices = &index;
    r = p_vkQueuePresentKHR(g_queue, &pi);
    plat::frame_presented();  // cores, CPU time, present intervals (frame_stats.cpp)
    // SUBOPTIMAL re-makes the swapchain only when the surface size changed: Android returns it for every present when
    // the swapchain's transform is not the display's (a Galaxy S23 did for Hades), a rebuild per frame.
    bool remake = r == VK_ERROR_OUT_OF_DATE_KHR;
    if (r == VK_SUBOPTIMAL_KHR) {  // compared with what the swapchain was made from (its extent may be turned)
        VkSurfaceCapabilitiesKHR caps{};
        p_vkGetPhysicalDeviceSurfaceCapabilitiesKHR(g_physical, g_surface, &caps);
        remake = caps.currentExtent.width != g_sc_current.width || caps.currentExtent.height != g_sc_current.height ||
                 (present_pass_wanted() && !present_pass_failed() && caps.currentTransform != g_sc_transform);
    }
    if (remake) make_swapchain();
}

}  // namespace nx::nvn

namespace nx::plat {

// AP_ASPECT (platform.h): the rectangle the picture is presented into, for a client area of cw x ch pixels.
PictureRect picture_rect(uint32_t cw, uint32_t ch) {
    PictureRect r;
    r.w = cw, r.h = ch;
    if (!cw || !ch || !nvn::picture_keeps_aspect()) return r;
    uint64_t pw = nvn::g_pic_w, ph = nvn::g_pic_h;
    if (!pw || !ph) {  // nothing presented yet: the aspect the window was made in (NX_RESOLUTION, default 16:9)
        unsigned rw = 16, rh = 9;
        if (const char* e = std::getenv("NX_RESOLUTION")) std::sscanf(e, "%ux%u", &rw, &rh);
        pw = std::max(1u, rw), ph = std::max(1u, rh);
    }
    if (uint64_t(cw) * ph > uint64_t(ch) * pw) {  // wider than the picture: bars left and right
        r.w = uint32_t((uint64_t(ch) * pw + ph / 2) / ph);
        r.x = int32_t((cw - r.w) / 2);
    } else {  // taller: bars above and below
        r.h = uint32_t((uint64_t(cw) * ph + pw / 2) / pw);
        r.y = int32_t((ch - r.h) / 2);
    }
    return r;
}

}  // namespace nx::plat
