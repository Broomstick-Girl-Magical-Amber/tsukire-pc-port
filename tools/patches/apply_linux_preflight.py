r"""apply_linux_preflight.py - native Linux: checks before the install and after the libraries (AP_LINUX_PREFLIGHT).

Three things found the first time the install ran on Fedora 45:
 1. OpenSSL: a system with only OpenSSL 4 makes PowerShell 7 (.NET) abort with "No usable version of libssl was found".
    install_requirements_linux.sh / recomp_linux.sh now stop BEFORE downloading anything and say what to install
    (tools/linux/preflight.sh: preflight_openssl); with OpenSSL 3 next to a newer one, they ask .NET for 3 by themselves.
 2. Leftovers of an interrupted run ("<tool>.tmp" in the tools folder): the installer refused to go on ("set it aside by
    hand"). install_requirements_linux.sh now removes them first (preflight_tmp), never while another install or recomp
    is running, and not with -Check.
 3. A static SDL3 built without the Wayland / X11 headers (step 1, the system -dev packages, was skipped with -From 2)
    has no window driver: the game stops with "SDL_Init: No available video device", and vcpkg keeps that SDL3 in its
    binary cache. The Linux branch of framework/CMakeLists.txt now looks inside libSDL3.a and stops at the configure of
    step 9 if it has neither driver (a warning if it has only one), and says how to rebuild SDL3.

tools/linux/preflight.sh is a new file of the repo (copy it with this patcher). Windows and Android do not change.
"""
import os
import sys

from _patch import run

MARK = "AP_LINUX_PREFLIGHT"
INST = "../tools/linux/install_requirements_linux.sh"
RECO_SH = "../tools/linux/recomp_linux.sh"
RECO = "../tools/recomp_tsukihime.ps1"
CMAKE = "CMakeLists.txt"

EDITS = [
    (INST,
     '''install_pwsh
run_ps1 "$ROOT/tools/install_requirements.ps1" "$@"
''',
     '''# AP_LINUX_PREFLIGHT (apply_linux_preflight.py): OpenSSL 3 for .NET and the leftovers of an interrupted run, before
# anything is installed (tools/linux/preflight.sh).
. "$HERE/preflight.sh"
preflight_openssl
[ "$check" = 1 ] || preflight_tmp
install_pwsh
run_ps1 "$ROOT/tools/install_requirements.ps1" "$@"
'''),
    (RECO_SH,
     '''run_ps1 "$ROOT/tools/recomp_tsukihime.ps1" "$@"
''',
     '''# AP_LINUX_PREFLIGHT (apply_linux_preflight.py): OpenSSL 3 for .NET (tools/linux/preflight.sh)
. "$HERE/preflight.sh"
preflight_openssl
run_ps1 "$ROOT/tools/recomp_tsukihime.ps1" "$@"
'''),
    (CMAKE,
     '''    # local build (-DCMAKE_PREFIX_PATH=<its install prefix>).
    find_package(SDL3 REQUIRED CONFIG)
    find_package(Threads REQUIRED)
''',
     '''    # local build (-DCMAKE_PREFIX_PATH=<its install prefix>).
    find_package(SDL3 REQUIRED CONFIG)
    # AP_LINUX_PREFLIGHT (apply_linux_preflight.py): a static SDL3 (vcpkg) built without the Wayland / X11 headers has
    # no window driver and the game stops with "SDL_Init: No available video device": stop here and say what is
    # missing. A shared SDL3 of the distribution (no libSDL3.a next to its CMake files) is not checked.
    find_file(NX_SDL3_STATIC libSDL3.a PATHS "${SDL3_DIR}/../.." "${SDL3_DIR}/../../lib" NO_DEFAULT_PATH)
    if(NX_SDL3_STATIC AND CMAKE_AR)
        execute_process(COMMAND "${CMAKE_AR}" t "${NX_SDL3_STATIC}" OUTPUT_VARIABLE nx_sdl_members
                        RESULT_VARIABLE nx_sdl_rc ERROR_QUIET)
        if(nx_sdl_rc EQUAL 0)
            string(FIND "${nx_sdl_members}" "SDL_waylandvideo" nx_sdl_wl)
            string(FIND "${nx_sdl_members}" "SDL_x11video" nx_sdl_x11)
            if(nx_sdl_wl EQUAL -1 AND nx_sdl_x11 EQUAL -1)
                message(FATAL_ERROR
                    "${NX_SDL3_STATIC} has neither the Wayland nor the X11 video driver: the game would stop with "
                    "'SDL_Init: No available video device'. SDL3 was built without the system -dev packages "
                    "(step 1 of install_requirements_linux.sh installs them; -From 2 or later skips it). Fix: run step 1 "
                    "(tools/linux/install_requirements_linux.sh -Target linux -From 1), then 'vcpkg remove "
                    "sdl3:x64-linux', delete the vcpkg binary cache (~/.cache/vcpkg/archives) and install again.")
            elseif(nx_sdl_wl EQUAL -1)
                message(WARNING "SDL3 has no Wayland video driver (only X11: it runs through XWayland). "
                                "Missing: wayland-devel, wayland-protocols, libxkbcommon at the time SDL3 was built.")
            elseif(nx_sdl_x11 EQUAL -1)
                message(WARNING "SDL3 has no X11 video driver (only Wayland). Missing: the X11 -dev packages at the "
                                "time SDL3 was built.")
            endif()
        endif()
    endif()
    find_package(Threads REQUIRED)
'''),
    (RECO,
     '''    @('both', 'tools\\hud\\build_hud_assets.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
''',
     '''    @('both', 'tools\\hud\\build_hud_assets.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
    @('both', 'tools\\linux\\install_requirements_linux.sh', 'AP_LINUX_PREFLIGHT', 'apply_linux_preflight.py', $false),
    @('both', 'tools\\linux\\recomp_linux.sh', 'AP_LINUX_PREFLIGHT', 'apply_linux_preflight.py', $false),
    @('both', 'framework\\CMakeLists.txt', 'AP_LINUX_PREFLIGHT', 'apply_linux_preflight.py', $false),
'''),
]

if __name__ == "__main__":
    if "--remove" not in sys.argv:
        pf = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "linux", "preflight.sh")
        if not os.path.isfile(pf):
            sys.exit("ERROR: tools/linux/preflight.sh is missing (copy it with this patcher). Nothing touched.")
    run(MARK, EDITS, __doc__)
