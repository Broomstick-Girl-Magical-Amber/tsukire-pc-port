"""apply_linux_build.py - native Linux build of the game (AP_LINUX_BUILD): an x86_64 ELF next to the Windows exe.

The framework already has a Linux branch (CMakeLists.txt: SDL3 window/input/audio, platform_linux.cpp). This makes
the Tsukihime build use it:
- CMakeLists.txt (UNIX branch): NX_OUTPUT_DIR puts the binary in another folder than the CMake build folder
  (recomp_tsukihime.ps1 -Target linux: CMake in framework/build/<game>/linux, the binary in framework/build/<game>/,
  next to <game>.exe, so the data root "../../../data/<edition>" of game.cpp is the same); DT_RPATH $ORIGIN and nothing else (the
  FFmpeg .so files next to the binary, their own dependencies included).
- tsukihime_common.cmake: a Linux block (before the Android and Windows ones): FFmpeg 9.0 LGPL shared of BtbN (NX_FFMPEG_ROOT,
  lib/lib*.so; each library is copied next to the binary with its soname), opus/libjpeg-turbo/libpng/zlib static from
  vcpkg (x64-linux), no tsukihime.rc.
- game.cmake of each edition enables the resource compiler (Windows icon): on Linux CMAKE_RC_COMPILER is llvm-rc of the
  same LLVM (tsukihime.rc itself is never compiled there).
Windows (native or cross from Linux) and Android do not change.
"""
from _patch import run

MARK = "AP_LINUX_BUILD"
TOP = "CMakeLists.txt"
COMMON = "games/tsukihime_common/tsukihime_common.cmake"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (TOP,
     '''    target_compile_options(${NX_GAME} PRIVATE -fvisibility=hidden -march=x86-64-v3 -ffp-contract=off)
    # The executable (PIE) loads near 0x55_5555_0000_0000, far above the guest ranges (0x40_0000_0000 and up). The
    # game runs on its own thread with a 64 MiB stack (platform_linux.cpp).
''',
     '''    target_compile_options(${NX_GAME} PRIVATE -fvisibility=hidden -march=x86-64-v3 -ffp-contract=off)
    # The executable (PIE) loads near 0x55_5555_0000_0000, far above the guest ranges (0x40_0000_0000 and up). The
    # game runs on its own thread with a 64 MiB stack (platform_linux.cpp).
    # AP_LINUX_BUILD (apply_linux_build.py): NX_OUTPUT_DIR = the folder of the binary when it is not the build folder
    # (recomp_tsukihime.ps1 -Target linux: next to the Windows exe); the shared libraries next to it are found through
    # DT_RPATH $ORIGIN (DT_RPATH, not RUNPATH: it also applies to the dependencies of those libraries).
    set(NX_OUTPUT_DIR "" CACHE PATH "Folder of the binary (empty: the build folder)")
    if(NX_OUTPUT_DIR)
        set_target_properties(${NX_GAME} PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${NX_OUTPUT_DIR}")
    endif()
    # BUILD_WITH_INSTALL_RPATH: exactly $ORIGIN (no absolute folder of the libraries of this PC in the binary)
    set_target_properties(${NX_GAME} PROPERTIES BUILD_WITH_INSTALL_RPATH ON INSTALL_RPATH "$ORIGIN")
    target_link_options(${NX_GAME} PRIVATE "LINKER:--disable-new-dtags")
    # games/<game>/game.cmake enables the resource compiler for the Windows icon (tsukihime.rc, never compiled here:
    # tsukihime_common.cmake marks it HEADER_FILE_ONLY on Linux); a Linux PC has no rc.exe: llvm-rc of the same LLVM
    if(NOT CMAKE_RC_COMPILER)
        get_filename_component(nx_cc_dir "${CMAKE_CXX_COMPILER}" DIRECTORY)
        find_program(NX_LLVM_RC NAMES llvm-rc llvm-windres windres HINTS "${nx_cc_dir}")
        set(CMAKE_RC_COMPILER "${NX_LLVM_RC}")
        if(NOT NX_LLVM_RC)
            set(CMAKE_RC_COMPILER "${CMAKE_COMMAND}")  # only so enable_language(RC) works: no .rc is compiled on Linux
        endif()
    endif()
'''),
    (COMMON,
     '''# AP_ANDROID_BUILD (apply_android_build.py): Android (arm64-v8a, NDK). The libraries come built for arm64-android
''',
     '''# AP_LINUX_BUILD (apply_linux_build.py): native Linux (x86_64 ELF). FFmpeg is the LGPL "shared" build of BtbN for
# linux64 (NX_FFMPEG_ROOT: include/, lib/lib*.so; install_requirements.ps1 -Target linux, step 5); each library is
# copied next to the binary under its soname (the binary finds them through DT_RPATH $ORIGIN, CMakeLists.txt). opus,
# libjpeg-turbo, libpng and zlib are the static ones of vcpkg (x64-linux, CMAKE_TOOLCHAIN_FILE); SDL3 is linked by
# CMakeLists.txt. No Windows resources. Android and Windows follow: return() ends this file here on Linux.
if(UNIX AND NOT APPLE AND NOT ANDROID)
    set_source_files_properties("${NX_TSUKI_COMMON}/tsukihime.rc" PROPERTIES HEADER_FILE_ONLY ON)
    set(NX_FFMPEG_ROOT "" CACHE PATH "Prebuilt FFmpeg for Linux (include, lib/lib*.so)")
    if(NOT EXISTS "${NX_FFMPEG_ROOT}/include/libavcodec/avcodec.h")
        message(FATAL_ERROR "NX_FFMPEG_ROOT (${NX_FFMPEG_ROOT}) has no include/libavcodec/avcodec.h "
                            "(tools/linux/install_requirements_linux.sh -Target linux, step 5)")
    endif()
    target_include_directories(${NX_GAME} PRIVATE "${NX_FFMPEG_ROOT}/include")
    foreach(nx_ff_name avcodec avformat avutil swscale swresample)
        set(nx_ff_lib "${NX_FFMPEG_ROOT}/lib/lib${nx_ff_name}.so")
        if(NOT EXISTS "${nx_ff_lib}")
            message(FATAL_ERROR "${nx_ff_lib} does not exist (install_requirements.ps1 -Target linux, step 5)")
        endif()
        target_link_libraries(${NX_GAME} PRIVATE "${nx_ff_lib}")
        # lib<name>.so.<major>: the soname the binary asks for (a link to the real file: copy_if_different copies the file)
        file(GLOB nx_ff_so "${NX_FFMPEG_ROOT}/lib/lib${nx_ff_name}.so.*")
        list(FILTER nx_ff_so INCLUDE REGEX "\\\\.so\\\\.[0-9]+$")
        list(LENGTH nx_ff_so nx_ff_so_count)
        if(NOT nx_ff_so_count EQUAL 1)
            message(FATAL_ERROR "${NX_FFMPEG_ROOT}/lib/lib${nx_ff_name}.so.<major>: ${nx_ff_so_count} files (1 expected)")
        endif()
        add_custom_command(TARGET ${NX_GAME} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${nx_ff_so}" "$<TARGET_FILE_DIR:${NX_GAME}>")
    endforeach()
    find_package(Opus CONFIG REQUIRED)
    find_package(JPEG REQUIRED)
    find_package(PNG REQUIRED)
    find_package(ZLIB REQUIRED)
    target_link_libraries(${NX_GAME} PRIVATE Opus::opus JPEG::JPEG PNG::PNG ZLIB::ZLIB)
    # std::filesystem::u8path is deprecated in C++20 (the Windows build silences it with
    # _SILENCE_CXX20_U8PATH_DEPRECATION_WARNING, which libstdc++ does not read)
    target_compile_options(${NX_GAME} PRIVATE -Wno-deprecated-declarations)
    # hle_codec.cpp includes <opus.h> (the Windows block adds include/opus the same way)
    get_target_property(nx_opus_inc Opus::opus INTERFACE_INCLUDE_DIRECTORIES)
    foreach(nx_dir IN LISTS nx_opus_inc)
        if(EXISTS "${nx_dir}/opus/opus.h")
            target_include_directories(${NX_GAME} PRIVATE "${nx_dir}/opus")
        endif()
    endforeach()
    return()
endif()

# AP_ANDROID_BUILD (apply_android_build.py): Android (arm64-v8a, NDK). The libraries come built for arm64-android
'''),
    (RECO,
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_NINJA_LOG_FORCE', 'apply_ninja_log_force.py', $false),
''',
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_NINJA_LOG_FORCE', 'apply_ninja_log_force.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_LINUX_BUILD', 'apply_linux_build.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
