#!/usr/bin/env python3
# apply_vk_requirements.py - marker AP_VK_REQ
#
# The runtime renderer uses Vulkan 1.3 core functions (vkCmdBeginRendering, synchronization2 barriers...).
# With a GPU or a driver that only gives Vulkan 1.2 those pointers stay null and the game crashes with rip = 0 at the
# first submit (seen on an 11th gen Intel UHD with driver 27.20.100.8984).
#
# With NX_VK_REQUIRE_13 defined (game.cmake of each Tsukihime edition), when Vulkan starts:
#   - if the chosen GPU does not have Vulkan 1.3 with dynamicRendering and synchronization2 and NX_GPU is not set,
#     another GPU that does is used (a discrete one is preferred);
#   - if none does: a line in the log, a message on stderr, a warning window (Windows) and exit with code 5;
#   - NX_VK_SKIP_CHECK=1 skips the check.
# Without the define (Wonder) the compiled code is the usual one.
#
# Edits: runtime/src/nvn_vk.cpp, games/tsukihime/game.cmake, games/tsukihime_global/game.cmake (the global one only if
# it exists). All or nothing, idempotent, keeps BOM and CRLF, refuses without an anchor or with a mixed state.
#   python apply_vk_requirements.py [--root <root>\framework] [--remove]
import argparse
import os
import sys

MARK = "AP_VK_REQ"

# --- nvn_vk.cpp -------------------------------------------------------------------------------------------------

DECL_ANCHOR = "#include <nx/game.h>\n\nnamespace nx::nvn {"
DECL_NEW = (
    "#include <nx/game.h>\n"
    "\n"
    "#if defined(NX_VK_REQUIRE_13) && defined(_WIN32) && !defined(_WINUSER_)  // AP_VK_REQ: el aviso, sin <windows.h>\n"
    "extern \"C\" __declspec(dllimport) int __stdcall MessageBoxW(void*, const wchar_t*, const wchar_t*, unsigned);\n"
    "#endif\n"
    "\n"
    "namespace nx::nvn {"
)

FUNC_ANCHOR = "bool make_surface();\n\nbool init() {"
FUNC_NEW = (
    "bool make_surface();\n"
    "\n"
    "#ifdef NX_VK_REQUIRE_13  // AP_VK_REQ (apply_vk_requirements.py)\n"
    "// El render usa funciones core de Vulkan 1.3 (dynamic rendering, synchronization2): con un device 1.2 esos punteros\n"
    "// quedan nulos y el primer submit salta a 0. Se elige una GPU que cumpla; si ninguna, un aviso claro y la salida.\n"
    "// NX_VK_SKIP_CHECK=1: sin la verificacion.\n"
    "bool vk_req_ok(VkPhysicalDevice pd, VkPhysicalDeviceProperties& p) {\n"
    "    p = {};\n"
    "    p_vkGetPhysicalDeviceProperties(pd, &p);\n"
    "    if (VK_API_VERSION_MAJOR(p.apiVersion) == 1 && VK_API_VERSION_MINOR(p.apiVersion) < 3) return false;\n"
    "    if (!p_vkGetPhysicalDeviceFeatures2) return false;\n"
    "    VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};\n"
    "    VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};\n"
    "    f.pNext = &f13;\n"
    "    p_vkGetPhysicalDeviceFeatures2(pd, &f);\n"
    "    return f13.dynamicRendering && f13.synchronization2;\n"
    "}\n"
    "\n"
    "VkPhysicalDevice vk_req_pick(const std::vector<VkPhysicalDevice>& gpus, VkPhysicalDevice chosen) {\n"
    "    const char* skip = std::getenv(\"NX_VK_SKIP_CHECK\");\n"
    "    if (!chosen || (skip && skip[0] == '1')) return chosen;\n"
    "    VkPhysicalDeviceProperties p{};\n"
    "    if (vk_req_ok(chosen, p)) return chosen;\n"
    "    char why[400];\n"
    "    std::snprintf(why, sizeof why, \"%s, Vulkan %u.%u.%u, driver %08x\", p.deviceName,\n"
    "                  VK_API_VERSION_MAJOR(p.apiVersion), VK_API_VERSION_MINOR(p.apiVersion),\n"
    "                  VK_API_VERSION_PATCH(p.apiVersion), p.driverVersion);\n"
    "    if (!std::getenv(\"NX_GPU\")) {  // otra GPU que cumpla, mejor una dedicada (NX_GPU elegida a mano: se respeta)\n"
    "        VkPhysicalDevice best = VK_NULL_HANDLE;\n"
    "        bool best_discrete = false;\n"
    "        for (VkPhysicalDevice g : gpus) {\n"
    "            VkPhysicalDeviceProperties q{};\n"
    "            if (g == chosen || !vk_req_ok(g, q)) continue;\n"
    "            const bool discrete = q.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;\n"
    "            if (!best || (discrete && !best_discrete)) best = g, best_discrete = discrete;\n"
    "        }\n"
    "        if (best) {\n"
    "            VkPhysicalDeviceProperties q{};\n"
    "            p_vkGetPhysicalDeviceProperties(best, &q);\n"
    "            log(\"nvn/vk: %s: sin Vulkan 1.3 (dynamic rendering, synchronization2); se usa %s\", why, q.deviceName);\n"
    "            return best;\n"
    "        }\n"
    "    }\n"
    "    log(\"nvn/vk: ninguna GPU con Vulkan 1.3 (dynamic rendering, synchronization2): %s. Actualizar el driver de la \"\n"
    "        \"GPU (NX_VK_SKIP_CHECK=1 omite esta verificacion)\", why);\n"
    "    std::fprintf(stderr, \"nvn/vk: se necesita Vulkan 1.3; la GPU tiene %s. Actualiza el driver de la GPU.\\n\", why);\n"
    "    std::fflush(stderr);\n"
    "#if defined(_WIN32)\n"
    "    wchar_t msg[1024];\n"
    "    std::swprintf(msg, sizeof msg / sizeof msg[0],\n"
    "                  L\"El juego necesita Vulkan 1.3 y esta GPU o su driver no lo soportan.\\n\\n\"\n"
    "                  L\"GPU: %hs\\n\\n\"\n"
    "                  L\"Actualiza el driver de la GPU desde la p\\u00e1gina del fabricante (Intel: Intel Driver & Support \"\n"
    "                  L\"Assistant; AMD: Adrenalin; NVIDIA: GeForce/Studio), no desde Windows Update.\\n\\n\"\n"
    "                  L\"This GPU or its driver does not support Vulkan 1.3. Please update the GPU driver.\\n\\n\"\n"
    "                  L\"NX_VK_SKIP_CHECK=1 omite esta verificaci\\u00f3n.\",\n"
    "                  why);\n"
    "    ::MessageBoxW(nullptr, msg, L\"Vulkan 1.3 requerido\", 0x10 /* MB_ICONERROR */);\n"
    "#endif\n"
    "    _exit(5);\n"
    "}\n"
    "#endif\n"
    "\n"
    "bool init() {"
)

PICK_ANCHOR = (
    "    VkPhysicalDeviceProperties props{};\n"
    "    p_vkGetPhysicalDeviceProperties(g_physical, &props);\n"
    "    p_vkGetPhysicalDeviceMemoryProperties(g_physical, &g_memory);"
)
PICK_NEW = (
    "#ifdef NX_VK_REQUIRE_13\n"
    "    g_physical = vk_req_pick(gpus, g_physical);  // AP_VK_REQ: Vulkan 1.3, o un aviso y la salida\n"
    "#endif\n" + PICK_ANCHOR
)

NVN_CHANGES = [(DECL_ANCHOR, DECL_NEW), (FUNC_ANCHOR, FUNC_NEW), (PICK_ANCHOR, PICK_NEW)]

# --- game.cmake -------------------------------------------------------------------------------------------------

CMAKE_ANCHOR = "target_compile_definitions(${NX_GAME} PRIVATE NX_DATA_ROOT_EXE)"
CMAKE_LINE = ("# AP_VK_REQ (apply_vk_requirements.py): Vulkan 1.3 o un aviso claro al iniciar\n"
               "target_compile_definitions(${NX_GAME} PRIVATE NX_VK_REQUIRE_13)")


class Error(Exception):
    pass


def read_file(path):
    with open(path, "rb") as f:
        data = f.read()
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    text = data.decode("utf-8")
    crlf = "\r\n" in text
    if crlf:
        if text.replace("\r\n", "").count("\r") or text.replace("\r\n", "\x00").count("\n"):
            raise Error(f"{path}: mixed line endings (CRLF and LF); not touched")
        text = text.replace("\r\n", "\n")
    return text, bom, crlf


def encode(text, bom, crlf):
    if crlf:
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def replace_once(text, old, new, path):
    n = text.count(old)
    if n != 1:
        raise Error(f"{path}: anchor found {n} times (1 expected):\n    {old.splitlines()[0]!r}")
    return text.replace(old, new, 1)


def nvn_vk(text, path, remove):
    has = MARK in text
    complete = all(new in text for _, new in NVN_CHANGES)
    if has and not complete:
        raise Error(f"{path}: mixed state (it has {MARK} but not all the blocks); check by hand")
    if remove:
        if not has:
            return None
        for old, new in reversed(NVN_CHANGES):
            text = replace_once(text, new, old, path)
        if MARK in text:
            raise Error(f"{path}: leftovers of {MARK} after removing; check by hand")
        return text
    if has:
        return None
    for old, new in NVN_CHANGES:
        text = replace_once(text, old, new, path)
    return text


def game_cmake(text, path, remove):
    new = CMAKE_ANCHOR + "\n" + CMAKE_LINE
    has = MARK in text or "NX_VK_REQUIRE_13" in text
    if has and new not in text:
        raise Error(f"{path}: mixed state (NX_VK_REQUIRE_13 or {MARK} out of place); check by hand")
    if remove:
        return replace_once(text, new, CMAKE_ANCHOR, path) if has else None
    if has:
        return None
    if text.count(CMAKE_ANCHOR) != 1:
        raise Error(f"{path}: the line is not there (once)\n    {CMAKE_ANCHOR}\n"
                    "  (apply_data_root_exe.py adds it; run it first)")
    return text.replace(CMAKE_ANCHOR, new, 1)


def main():
    ap = argparse.ArgumentParser(description="Vulkan 1.3 required with a clear warning (AP_VK_REQ)")
    ap.add_argument("--root", default=".", help="framework folder (default: the current one)")
    ap.add_argument("--remove", action="store_true", help="undoes the patch (files byte for byte as before)")
    a = ap.parse_args()

    root_dir = os.path.abspath(a.root)
    # nvn_vk.cpp is not directly in runtime/src: it is looked up under runtime/ (there must be only one)
    found = []
    for base, dirs, names in os.walk(os.path.join(root_dir, "runtime")):
        dirs[:] = [d for d in dirs if d.lower() not in ("build", ".git")]
        if "nvn_vk.cpp" in names:
            found.append(os.path.join(base, "nvn_vk.cpp"))
    if len(found) != 1:
        print(f"ERROR: one nvn_vk.cpp was expected under {os.path.join(root_dir, 'runtime')} and there are {len(found)}"
              + "".join(f"\n    {e}" for e in found)
              + "\n(--root must be the framework folder)\nNo file was changed.", file=sys.stderr)
        return 1
    files = [(found[0], nvn_vk, True),
                (os.path.join(root_dir, "games", "tsukihime", "game.cmake"), game_cmake, True),
                (os.path.join(root_dir, "games", "tsukihime_global", "game.cmake"), game_cmake, False)]
    plans = []
    try:
        for path, fn, required in files:
            if not os.path.isfile(path):
                if required:
                    raise Error(f"{path} does not exist (--root must be the framework folder)")
                print(f"  ({os.path.relpath(path, root_dir)} does not exist: skipped)")
                continue
            text, bom, crlf = read_file(path)
            new = fn(text, path, a.remove)
            if new is not None:
                plans.append((path, encode(new, bom, crlf)))
            else:
                print(f"  {os.path.relpath(path, root_dir)}: {'without the patch' if a.remove else 'already applied'}, no changes")
    except (Error, UnicodeDecodeError) as e:
        print(f"ERROR: {e}\nNo file was changed.", file=sys.stderr)
        return 1

    backups = []
    try:
        for path, data in plans:
            with open(path, "rb") as f:
                backups.append((path, f.read()))
            tmp = path + ".ap_vk_req.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            print(f"  {os.path.relpath(path, root_dir)}: {'removed' if a.remove else 'applied'}")
    except OSError as e:
        for path, data in backups:
            with open(path, "wb") as f:
                f.write(data)
        print(f"ERROR while writing: {e}\nThe changes were undone.", file=sys.stderr)
        return 1
    if plans:
        print("Done. Rebuild: cmake --build build\\tsukihime and cmake --build build\\tsukihime_global"
              " (game.cmake changes: ninja reconfigures by itself).")
    return 0


if __name__ == "__main__":
    sys.exit(main())