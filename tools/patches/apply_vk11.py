"""apply_vk11.py - the game runs on Vulkan 1.1 GPUs (AP_VK11).

Until now the game required Vulkan 1.3 with dynamicRendering and synchronization2 (AP_VK_REQ,
apply_vk_requirements.py): with less it showed a warning and exited with code 5. Measured on a Vulkan 1.1 phone
(Mali-G77, driver 1.1.213), two things were really missing, and both have a fallback now:
  - Dynamic rendering (1.3). The runtime already had render pass and framebuffer objects in its place
    (nvn_renderpass.cpp, NX_RENDER_PASS_OBJECTS=1). A GPU without dynamicRendering now uses them by itself.
    synchronization2 is not used by the runtime: it was only checked.
  - Shader capabilities. Every translated shader declares FragmentShaderPixelInterlockEXT and many declare
    GroupNonUniform*, though no instruction uses them (tools/dev/spv_inventory.py --usage: 0 of 183). A GPU without
    VK_EXT_fragment_shader_interlock, or whose subgroups do not cover every stage and operation (the Mali: fragment and
    compute only), refused those pipelines and the screen stayed black. On such a GPU the unused declarations are now
    removed when the module is made (header words only: no id changes; a module that really uses them is kept as it
    is). This is what tools/dev/spv_strip_caps.py did to copies on disk, now in memory.
The check becomes Vulkan 1.1: a GPU below that (1.0) still gets the warning and exit code 5.
GPUs with Vulkan 1.3 and full subgroups (NVIDIA, AMD, Intel desktop) keep dynamic rendering and their modules as
before; a GPU without the interlock extension gets the unused declaration removed (its pipeline cache refills once).

Switches: NX_RENDER_PASS_OBJECTS=1 (render pass objects on any GPU, as before); NX_SPV_STRIP=1 (remove the unused
declarations on any GPU, to test that path on a PC) / NX_SPV_STRIP=0 (never); NX_VK_SKIP_CHECK=1 (no check, as before).
What to see in nx.log:
  nvn/vk: Vulkan X.Y: dynamic rendering yes|no; shaders: unused interlock declarations removed|kept, unused subgroup
          declarations removed|kept (subgroup stages .., operations ..)
  nvn/vk: no dynamic rendering on this GPU (Vulkan 1.1/1.2): render pass and framebuffer objects      (if so)
  nvn/vk: shaders: unused capability declarations removed in N modules so far                        (if so)

Edits: runtime/src/nvn/nvn_vk.cpp (the AP_VK_REQ check and its messages, the 1.3 features query, the device's
capabilities), runtime/src/nvn/nvn_vk.h, runtime/src/nvn/nvn_renderpass.cpp, runtime/src/nvn/nvn_pipeline.cpp, and
../tools/recomp_tsukihime.ps1 (its $Markers row, after the AP_VK_FAIL row, which no other patcher uses as an anchor).
Needs AP_VK_REQ applied (it edits its text): to undo both, remove this one first.
Idempotent (marker AP_VK11), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_vk11.py            then rebuild (PC: recomp_tsukihime.ps1 -Edition <ed> -From 9;
                                                   phone: tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_vk11.py --remove
"""
from _patch import run

MARK = "AP_VK11"

VK = "runtime/src/nvn/nvn_vk.cpp"
VKH = "runtime/src/nvn/nvn_vk.h"
RP = "runtime/src/nvn/nvn_renderpass.cpp"
PIPE = "runtime/src/nvn/nvn_pipeline.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

REQ_OLD = '''// El render usa funciones core de Vulkan 1.3 (dynamic rendering, synchronization2): con un device 1.2 esos punteros
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
'''

REQ_NEW = '''// AP_VK11 (apply_vk11.py): Vulkan 1.1 is enough. Without 1.3's dynamic rendering the passes are render pass and
// framebuffer objects (nvn_renderpass.cpp), synchronization2 is not used, and shader capabilities the GPU lacks but
// no instruction uses are removed (nvn_pipeline.cpp). A GPU with 1.1 is chosen; if none, a clear warning and exit
// (code 5). NX_VK_SKIP_CHECK=1: no check.
bool vk_req_ok(VkPhysicalDevice pd, VkPhysicalDeviceProperties& p) {
    p = {};
    p_vkGetPhysicalDeviceProperties(pd, &p);
    return VK_API_VERSION_MAJOR(p.apiVersion) > 1 || VK_API_VERSION_MINOR(p.apiVersion) >= 1;
}
'''

GLOBALS_OLD = "VkBool32 g_depth_clip_control = VK_FALSE;\n"
GLOBALS_NEW = '''VkBool32 g_depth_clip_control = VK_FALSE;
// AP_VK11: dynamic rendering (Vulkan 1.3) on this GPU, and whether unused shader capability declarations must go.
bool g_dynamic_rendering = true, g_spv_strip_interlock = false, g_spv_strip_subgroup = false;
'''

CHAIN_OLD = '''    features.pNext = &f13;
    void** tail = &f13.pNext;
'''
CHAIN_NEW = '''    // AP_VK11: the 1.3 features only on a 1.3 device (a 1.1 device does not know the structure).
    void** tail = &features.pNext;
    if (VK_API_VERSION_MAJOR(props.apiVersion) > 1 || VK_API_VERSION_MINOR(props.apiVersion) >= 3)
        *tail = &f13, tail = &f13.pNext;
'''

CAPS_OLD = "    g_depth_clip_control = fclip.depthClipControl;\n"
CAPS_NEW = '''    g_depth_clip_control = fclip.depthClipControl;
    {   // AP_VK11: what a Vulkan 1.1 GPU lacks. Dynamic rendering: render pass objects instead. Fragment shader
        // interlock and subgroups that do not cover every stage and operation: the translated shaders declare them
        // without using them, and those declarations are removed (nvn_pipeline.cpp). NX_SPV_STRIP=1|0 forces it.
        g_dynamic_rendering = f13.dynamicRendering;
        VkPhysicalDeviceSubgroupProperties sg{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES};
        VkPhysicalDeviceProperties2 p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        p2.pNext = &sg;
        p_vkGetPhysicalDeviceProperties2(g_physical, &p2);
        const VkShaderStageFlags all = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT | VK_SHADER_STAGE_COMPUTE_BIT;
        g_spv_strip_interlock = !has_extension(dext, "VK_EXT_fragment_shader_interlock");
        g_spv_strip_subgroup = (sg.supportedStages & all) != all || (sg.supportedOperations & 0xff) != 0xff;
        if (const char* e = std::getenv("NX_SPV_STRIP"); e && (e[0] == '0' || e[0] == '1'))
            g_spv_strip_interlock = g_spv_strip_subgroup = e[0] == '1';
        log("nvn/vk: Vulkan %u.%u: dynamic rendering %s; shaders: unused interlock declarations %s, unused subgroup "
            "declarations %s (subgroup stages %x, operations %x)", VK_API_VERSION_MAJOR(props.apiVersion),
            VK_API_VERSION_MINOR(props.apiVersion), g_dynamic_rendering ? "yes" : "no",
            g_spv_strip_interlock ? "removed" : "kept", g_spv_strip_subgroup ? "removed" : "kept",
            unsigned(sg.supportedStages), unsigned(sg.supportedOperations));
    }
'''

ACC_OLD = "bool vk_depth_clip_control() { return g_depth_clip_control; }\n"
ACC_NEW = '''bool vk_depth_clip_control() { return g_depth_clip_control; }
bool vk_dynamic_rendering() { return g_dynamic_rendering; }  // AP_VK11
bool vk_spv_strip_interlock() { return g_spv_strip_interlock; }
bool vk_spv_strip_subgroup() { return g_spv_strip_subgroup; }
'''

H_OLD = "bool render_pass_objects();\n"
H_NEW = '''bool render_pass_objects();
// AP_VK11 (apply_vk11.py): the GPU has dynamic rendering (Vulkan 1.3); unused shader capability declarations to remove.
bool vk_dynamic_rendering();
bool vk_spv_strip_interlock();
bool vk_spv_strip_subgroup();
'''

RP_OLD = '''        bool v = e && e[0] == '1';
        if (v) log("nvn/vk: render pass and framebuffer objects instead of dynamic rendering (NX_RENDER_PASS_OBJECTS)");
'''
RP_NEW = '''        bool v = e && e[0] == '1';
        if (!v && !vk_dynamic_rendering()) {  // AP_VK11: a Vulkan 1.1/1.2 GPU
            log("nvn/vk: no dynamic rendering on this GPU (Vulkan 1.1/1.2): render pass and framebuffer objects");
            return true;
        }
        if (v) log("nvn/vk: render pass and framebuffer objects instead of dynamic rendering (NX_RENDER_PASS_OBJECTS)");
'''

STRIP_ANCHOR = "// `kinds`: per vertex input location, 0 float or the integer kind of its attribute format (vertex_int_kind).\n"
STRIP_FUNC = r'''// AP_VK11 (apply_vk11.py): removes the capability declarations a module makes but never uses, on a GPU without them.
// Every translated shader declares FragmentShaderPixelInterlockEXT (and the extension), many GroupNonUniform*, and
// none uses them; a Vulkan 1.1 Mali (no interlock, subgroups in fragment and compute only) refused the pipelines.
// Only OpCapability / OpExtension words of the header go, so no id changes. A module that does use them is kept.
// Returns whether something was removed.
bool strip_unused_caps(std::vector<uint32_t>& code, bool interlock, bool subgroup) {
    if ((!interlock && !subgroup) || code.size() < 5 || code[0] != 0x07230203u) return false;
    bool uses_interlock = false, uses_subgroup = false;
    std::vector<size_t> heads;  // OpCapability and OpExtension instructions
    for (size_t i = 5; i < code.size();) {
        const uint32_t op = code[i] & 0xffff, n = code[i] >> 16;
        if (!n || i + n > code.size()) return false;  // malformed: left alone
        if (op == 5364 || op == 5365 || (op == 16 && n >= 3 && code[i + 2] >= 5366 && code[i + 2] <= 5371))
            uses_interlock = true;  // OpBegin/EndInvocationInterlockEXT, an interlock execution mode
        if ((op >= 333 && op <= 366) || op == 4421 || op == 4422 || (op >= 4428 && op <= 4430) || op == 4432 ||
            (op == 71 && n >= 4 && code[i + 2] == 11 &&
             ((code[i + 3] >= 36 && code[i + 3] <= 41) || (code[i + 3] >= 4416 && code[i + 3] <= 4420))))
            uses_subgroup = true;  // a subgroup instruction or a subgroup built-in
        if (op == 17 || op == 10) heads.push_back(i);
        i += n;
    }
    std::vector<bool> drop(code.size(), false);
    bool any = false;
    for (size_t i : heads) {
        const uint32_t op = code[i] & 0xffff, n = code[i] >> 16;
        bool go = false;
        if (op == 17 && n >= 2) {
            const uint32_t cap = code[i + 1];
            if (interlock && !uses_interlock && (cap == 5363 || cap == 5372 || cap == 5378)) go = true;
            if (subgroup && !uses_subgroup && cap >= 61 && cap <= 68) go = true;
        } else if (op == 10 && n >= 2 && interlock && !uses_interlock) {
            const char* s = reinterpret_cast<const char*>(&code[i + 1]);
            go = strnlen(s, size_t(n - 1) * 4) == 33 && std::memcmp(s, "SPV_EXT_fragment_shader_interlock", 33) == 0;
        }
        if (go) drop[i] = any = true;
    }
    if (!any) return false;
    std::vector<uint32_t> out(code.begin(), code.begin() + 5);
    out.reserve(code.size());
    for (size_t i = 5; i < code.size();) {
        const uint32_t n = code[i] >> 16;
        if (!drop[i]) out.insert(out.end(), code.begin() + i, code.begin() + i + n);
        i += n;
    }
    code.swap(out);
    return true;
}

'''

MOD_OLD = '''        VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        ci.codeSize = code.size() * 4;
'''
MOD_NEW = '''        if (strip_unused_caps(code, vk_spv_strip_interlock(), vk_spv_strip_subgroup())) {  // AP_VK11
            static int stripped = 0;
            if (++stripped == 1 || stripped % 100 == 0)
                log("nvn/vk: shaders: unused capability declarations removed in %d modules so far", stripped);
        }
        VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        ci.codeSize = code.size() * 4;
'''

MSG = [
    ('''            log("nvn/vk: %s: sin Vulkan 1.3 (dynamic rendering, synchronization2); se usa %s", why, q.deviceName);
''',
     '''            log("nvn/vk: %s: sin Vulkan 1.1; se usa %s", why, q.deviceName);  // AP_VK11
'''),
    ('''    log("nvn/vk: ninguna GPU con Vulkan 1.3 (dynamic rendering, synchronization2): %s. Actualizar el driver de la "
''',
     '''    log("nvn/vk: ninguna GPU con Vulkan 1.1 (o mas nueva): %s. Actualizar el driver de la "
'''),
    ('''    std::fprintf(stderr, "nvn/vk: se necesita Vulkan 1.3; la GPU tiene %s. Actualiza el driver de la GPU.\\n", why);
''',
     '''    std::fprintf(stderr, "nvn/vk: se necesita Vulkan 1.1; la GPU tiene %s. Actualiza el driver de la GPU.\\n", why);
'''),
    ('''                  L"El juego necesita Vulkan 1.3 y esta GPU o su driver no lo soportan.\\n\\n"
''',
     '''                  L"El juego necesita Vulkan 1.1 y esta GPU o su driver no lo soportan.\\n\\n"
'''),
    ('''                  L"This GPU or its driver does not support Vulkan 1.3. Please update the GPU driver.\\n\\n"
''',
     '''                  L"This GPU or its driver does not support Vulkan 1.1. Please update the GPU driver.\\n\\n"
'''),
    ('''    ::MessageBoxW(nullptr, msg, L"Vulkan 1.3 requerido", 0x10 /* MB_ICONERROR */);
''',
     '''    ::MessageBoxW(nullptr, msg, L"Vulkan 1.1 requerido", 0x10 /* MB_ICONERROR */);
'''),
]

EDITS = [
    (VK, REQ_OLD, REQ_NEW),
    *[(VK, a, b) for a, b in MSG],
    (VK, GLOBALS_OLD, GLOBALS_NEW),
    (VK, CHAIN_OLD, CHAIN_NEW),
    (VK, CAPS_OLD, CAPS_NEW),
    (VK, ACC_OLD, ACC_NEW),
    (VKH, H_OLD, H_NEW),
    (RP, RP_OLD, RP_NEW),
    (PIPE, STRIP_ANCHOR, STRIP_FUNC + STRIP_ANCHOR),
    (PIPE, MOD_OLD, MOD_NEW),
    (RECO,
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_FAIL', 'apply_vk_fail.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_FAIL', 'apply_vk_fail.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK11', 'apply_vk11.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
