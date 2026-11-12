"""apply_android_vk_gipa.py - no more vkGetInstanceProcAddr(VK_NULL_HANDLE, ...) noise in the Android log (AP_VK_GIPA).

Pulido of the Android port. In nvn_vk.cpp, init() asks the loader for every function of the generated table
(g_entries, about 2300 names) with a null instance:

    for (size_t k = 0; k < g_entry_count; k++) *g_entries[k].pfn = gipa(nullptr, g_entries[k].name);

and, after vkCreateInstance, asks for all of them again with the real instance. The Vulkan spec only allows a null
instance for the global commands (vkEnumerateInstanceVersion, vkEnumerateInstanceExtensionProperties,
vkEnumerateInstanceLayerProperties, vkCreateInstance); for any other name the answer is NULL. The Android loader logs
every one of those calls as an error (E/vulkan: invalid vkGetInstanceProcAddr(VK_NULL_HANDLE, "vk...") call):
hundreds of lines in logcat.

This patch asks with the null instance only for the global commands of the table (today only vkEnumerateInstanceVersion;
the other three are loaded by hand right below). Nothing changes on Windows and Linux/Proton: between that loop and the
second one (after vkCreateInstance) init() only uses p_enum_instance_ext and p_create_instance (loaded explicitly) and
plat:: functions, so no other pointer of the table is read there, and the second loop sets every pointer the instance
knows, exactly as before. The names that are not global used to get NULL from the null-instance call (the pointers are
nullptr from the start); they still do until the second loop.

Not touched: vk_bridge.cpp load_loader() has the same loop, but it only runs when the guest calls Vulkan directly (the
NVN games never do).

Idempotent (marker AP_VK_GIPA), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as they
were. Independent of the other AP_* patchers (the anchor line is not touched by any of them). Also edits
../tools/recomp_tsukihime.ps1 (a $Markers row, anchored after the nvn_vk.cpp row of apply_vk_fail.py, which no other
patcher uses as anchor).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_vk_gipa.py
"""
from _patch import run

MARK = "AP_VK_GIPA"
VK = "runtime/src/nvn/nvn_vk.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (VK,
     """    for (size_t k = 0; k < g_entry_count; k++) *g_entries[k].pfn = gipa(nullptr, g_entries[k].name);
""",
     """    // AP_VK_GIPA (apply_android_vk_gipa.py): with a null instance the loader only knows the global commands; asking for
    // the rest logs an error per name on Android. The second load (after vkCreateInstance) sets all the others.
    for (size_t k = 0; k < g_entry_count; k++) {
        const char* const nm = g_entries[k].name;
        if (!std::strcmp(nm, "vkEnumerateInstanceVersion") || !std::strcmp(nm, "vkEnumerateInstanceExtensionProperties") ||
            !std::strcmp(nm, "vkEnumerateInstanceLayerProperties") || !std::strcmp(nm, "vkCreateInstance"))
            *g_entries[k].pfn = gipa(nullptr, nm);
    }
"""),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_FAIL', 'apply_vk_fail.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_FAIL', 'apply_vk_fail.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_GIPA', 'apply_android_vk_gipa.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
