"""apply_vk_e3.py - optional Vulkan capabilities enabled where the GPU has them, fallbacks where not (AP_VK_E3).

The runtime used three things without enabling them the way Vulkan asks; the drivers seen so far tolerate it, a
stricter one may not (wrong images or a failed vkCreateDevice). Measured with a temporary diagnostic on a Mali-G77
(Vulkan 1.1), an Adreno 830 (1.3) and an RTX 4060 (1.4): all three have the three capabilities; the only real gap was
the format list on the 1.1 phone (used on 500+ images without its extension enabled). Now:
  - Image format list (VkImageFormatListCreateInfo: core 1.2, VK_KHR_image_format_list on 1.1). On Android every
    RGBA8/BGRA8 colour image lists its UNORM and sRGB views, so the GPU keeps its framebuffer compression. A 1.1 GPU
    gets the extension enabled; without it the list is not chained and the image is mutable as on a PC.
  - Mirror clamp to edge (NVN wrap modes 2, 3 and 4). VK_KHR_sampler_mirror_clamp_to_edge is enabled where listed;
    otherwise, on 1.2+, the samplerMirrorClampToEdge feature (only that 1.2 feature). Without either: mirrored repeat
    (the same inside 0..1, it only differs past the edge).
  - A4B4G4R4 (NVN format 0x39): core in 1.3; on 1.1/1.2, VK_EXT_4444_formats with its features is enabled where
    listed. Without it the texture is reported as not supported and gets no image (as before). No game texture in
    the measured runs used this format.
Nothing changes on a GPU where the capability is core or the game does not use it.

Switch: NX_VK_LIMIT=<list> forces fallbacks on a GPU that has the capability, to test them: format_list,
mirror_clamp, 4444 (comma separated; "all" = the three).
What to see in nx.log:
  vk_e3: format list <core|extension|off (fallback: ...)>; mirror clamp to edge <...>; 4444 <...>

Edits runtime/src/nvn/nvn_vk.cpp (device creation, three accessors), runtime/src/nvn/nvn_sampler.cpp (wrap modes),
runtime/src/nvn/nvn_image.cpp (format list, 4444 check), and ../tools/recomp_tsukihime.ps1 (its $Markers rows, after
the AP_VK_GIPA row, which no other patcher uses as an anchor).
Idempotent (marker AP_VK_E3), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were. If apply_vk_e3_diag.py is applied, remove it first.

Usage (in <root>):
  python tools\\patches\\apply_vk_e3.py            then rebuild (PC: recomp_tsukihime.ps1 -Edition <ed> -From 9;
                                                    phone: tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_vk_e3.py --remove
"""
from _patch import run

MARK = "AP_VK_E3"

VK = "runtime/src/nvn/nvn_vk.cpp"
SAMPLER = "runtime/src/nvn/nvn_sampler.cpp"
IMAGE = "runtime/src/nvn/nvn_image.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# ---- nvn_vk.cpp ----------------------------------------------------------------------------------------------------
GLOBALS_OLD = "VkPhysicalDeviceFeatures g_features{};  // the core features enabled (vk_features())\n"
GLOBALS_NEW = GLOBALS_OLD + (
    "// AP_VK_E3: optional capabilities the device was created with (format list, mirror clamp to edge, A4B4G4R4).\n"
    "bool g_e3_format_list = false, g_e3_mirror_clamp = false, g_e3_4444 = false;\n")

QUERY_OLD = "    p_vkGetPhysicalDeviceFeatures2(g_physical, &features);\n"
QUERY_NEW = '''    // AP_VK_E3: the format list (core 1.2, extension on 1.1), mirror clamp to edge (extension, or the 1.2 feature
    // below) and A4B4G4R4 (core 1.3, extension before), each enabled only where the GPU has it. NX_VK_LIMIT=<list>
    // (format_list, mirror_clamp, 4444, all) leaves one out to test its fallback.
    const bool e3_12 = VK_API_VERSION_MAJOR(props.apiVersion) > 1 || VK_API_VERSION_MINOR(props.apiVersion) >= 2;
    const bool e3_13 = VK_API_VERSION_MAJOR(props.apiVersion) > 1 || VK_API_VERSION_MINOR(props.apiVersion) >= 3;
    auto e3_limited = [](const char* what) {
        const char* e = std::getenv("NX_VK_LIMIT");
        if (!e) return false;
        const std::string list = std::string(",") + e + ",";
        return list.find(std::string(",") + what + ",") != std::string::npos || list.find(",all,") != std::string::npos;
    };
    const bool e3_list_ext = !e3_12 && !e3_limited("format_list") && has_extension(dext, "VK_KHR_image_format_list");
    if (e3_list_ext) dexts.push_back("VK_KHR_image_format_list");
    g_e3_format_list = !e3_limited("format_list") && (e3_12 || e3_list_ext);
    const bool e3_mirror_ext = !e3_limited("mirror_clamp") && has_extension(dext, "VK_KHR_sampler_mirror_clamp_to_edge");
    if (e3_mirror_ext) dexts.push_back("VK_KHR_sampler_mirror_clamp_to_edge");
    VkPhysicalDevice4444FormatsFeaturesEXT f4444{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_4444_FORMATS_FEATURES_EXT};
    const bool e3_4444_ext = !e3_13 && !e3_limited("4444") && has_extension(dext, "VK_EXT_4444_formats");
    if (e3_4444_ext) {
        dexts.push_back("VK_EXT_4444_formats");
        *tail = &f4444, tail = &f4444.pNext;
    }
    p_vkGetPhysicalDeviceFeatures2(g_physical, &features);
    VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
    {   // AP_VK_E3: mirror clamp by the 1.2 feature only when the extension is not listed (enables no other 1.2 feature)
        bool feature = false;
        if (!e3_mirror_ext && e3_12 && !e3_limited("mirror_clamp")) {
            VkPhysicalDeviceVulkan12Features q12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
            VkPhysicalDeviceFeatures2 q{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
            q.pNext = &q12;
            p_vkGetPhysicalDeviceFeatures2(g_physical, &q);
            if (q12.samplerMirrorClampToEdge) {
                f12.samplerMirrorClampToEdge = VK_TRUE;
                *tail = &f12, tail = &f12.pNext;
                feature = true;
            }
        }
        g_e3_mirror_clamp = e3_mirror_ext || feature;
        g_e3_4444 = e3_13 ? !e3_limited("4444") : e3_4444_ext && f4444.formatA4B4G4R4;
        log("vk_e3: format list %s; mirror clamp to edge %s; 4444 %s",
            g_e3_format_list ? (e3_12 ? "core" : "extension") : "off (fallback: mutable images without the list)",
            e3_mirror_ext ? "extension" : feature ? "1.2 feature" : "off (fallback: mirrored repeat)",
            g_e3_4444 ? (e3_13 ? "core" : "extension") : "off (fallback: texture not supported)");
    }
'''

ACC_OLD = "bool gpu_require_subgroup32() { return g_require_sg32; }\n"
ACC_NEW = ACC_OLD + (
    "bool vk_e3_format_list() { return g_e3_format_list; }  // AP_VK_E3 (nvn_image.cpp)\n"
    "bool vk_e3_mirror_clamp() { return g_e3_mirror_clamp; }  // (nvn_sampler.cpp)\n"
    "bool vk_e3_4444() { return g_e3_4444; }  // (nvn_image.cpp)\n")

# ---- nvn_sampler.cpp -----------------------------------------------------------------------------------------------
S_DECL_OLD = "\nnamespace {\n\nVkSamplerAddressMode vk_wrap(uint32_t w) {"
S_DECL_NEW = ("\nbool vk_e3_mirror_clamp();  // AP_VK_E3: the device has mirror clamp to edge (nvn_vk.cpp)\n"
              "\nnamespace {\n\nVkSamplerAddressMode vk_wrap(uint32_t w) {")
S_WRAP_OLD = "        case 4: return VK_SAMPLER_ADDRESS_MODE_MIRROR_CLAMP_TO_EDGE;\n"
S_WRAP_NEW = ("        case 4:  // AP_VK_E3: without mirror clamp to edge, mirrored repeat (the same inside 0..1)\n"
              "            return vk_e3_mirror_clamp() ? VK_SAMPLER_ADDRESS_MODE_MIRROR_CLAMP_TO_EDGE\n"
              "                                        : VK_SAMPLER_ADDRESS_MODE_MIRRORED_REPEAT;\n")

# ---- nvn_image.cpp -------------------------------------------------------------------------------------------------
I_DECL_OLD = "using namespace nx::vk;\n\nnamespace {\n"
I_DECL_NEW = ("using namespace nx::vk;\n\n"
              "bool vk_e3_format_list();  // AP_VK_E3: the device can take a format list (nvn_vk.cpp)\n"
              "bool vk_e3_4444();         // the device has A4B4G4R4 enabled (nvn_vk.cpp)\n\n"
              "namespace {\n")
I_OK_OLD = "    auto ok = [&] {\n        VkImageFormatProperties p{};\n"
I_OK_NEW = ("    auto ok = [&] {\n"
            "        if (ci.format == VK_FORMAT_A4B4G4R4_UNORM_PACK16 && !vk_e3_4444()) return false;  // AP_VK_E3\n"
            "        VkImageFormatProperties p{};\n")
I_LIST_OLD = "    if (img->mutable_format && ubwc_flags()) {\n"
I_LIST_NEW = ("    // AP_VK_E3: the list only where the device takes it; otherwise mutable without it, as on a PC.\n"
              "    if (img->mutable_format && ubwc_flags() && vk_e3_format_list()) {\n")

ROW_ANCHOR = "    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_GIPA', 'apply_android_vk_gipa.py', $false),\n"
ROWS = (
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_E3', 'apply_vk_e3.py', $false),\n"
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_sampler.cpp', 'AP_VK_E3', 'apply_vk_e3.py', $false),\n"
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_image.cpp', 'AP_VK_E3', 'apply_vk_e3.py', $false),\n")

EDITS = [
    (VK, GLOBALS_OLD, GLOBALS_NEW),
    (VK, QUERY_OLD, QUERY_NEW),
    (VK, ACC_OLD, ACC_NEW),
    (SAMPLER, S_DECL_OLD, S_DECL_NEW),
    (SAMPLER, S_WRAP_OLD, S_WRAP_NEW),
    (IMAGE, I_DECL_OLD, I_DECL_NEW),
    (IMAGE, I_OK_OLD, I_OK_NEW),
    (IMAGE, I_LIST_OLD, I_LIST_NEW),
    (RECO, ROW_ANCHOR, ROW_ANCHOR + ROWS),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)