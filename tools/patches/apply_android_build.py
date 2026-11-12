"""apply_android_build.py - Tsukihime builds for Android (arm64-v8a) with the NDK (AP_ANDROID_BUILD).

Phase 1 of the Android port. The framework already has the Android host (NativeActivity,
platform_android*.cpp) and the CMakeLists builds lib<game>.so when ANDROID is set; what was missing is Tsukihime's own
part, which only knew Windows dependencies:
  - tsukihime_common.cmake: pad_sdl.cpp (SDL3/XInput) is left out on Android (the runtime reads the controllers and the
    touch screen itself, platform_android_input.cpp), and before the PC dependencies a block for Android links FFmpeg,
    opus, libjpeg-turbo, libpng and zlib built for arm64-android (NX_ANDROID_DEPS: the prefix that
    tools/android/android_deps.ps1 leaves in <root>/_prepare/android-deps) and ends the file there with return(), so
    none of the Windows lines below (prebuilt FFmpeg, opus.dll, find_package of vcpkg x64-windows, SDL3) runs. The
    defines above it (HUD, title, romfs layers, reading the dump...) apply on Android too. It also sets NX_EXTEND_BARS
    (apply_android_present.py: the bars around the picture repeat its edge) and skips tsukihime.rc.
  - games/tsukihime/game.cmake and games/tsukihime_global/game.cmake: enable_language(RC) and the window title/icon
    defines only when not ANDROID (the NDK has no resource compiler; the .rc itself is skipped from tsukihime_common).
  - ../.gitignore: lets in the Android launcher (<root>/android: Java, manifest template, resources, boot/) and
    tools/android/*.ps1 / *.sh; everything they build stays out (<root>/_android, _prepare).
  - ../tools/recomp_tsukihime.ps1: rows in $Markers (anchored after the row of apply_pad_source.py for hle_hid.cpp,
    which no other patcher uses as an anchor).
Windows and Linux/Proton builds do not change (every addition is under if(ANDROID) / if(NOT ANDROID)).

Idempotent (marker AP_ANDROID_BUILD), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Remove it before apply_ffmpeg_prebuilt.py, apply_romfs_direct.py or apply_game_window.py if one of those is
ever removed (its block sits next to their lines).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_build.py
"""
from _patch import run

MARK = "AP_ANDROID_BUILD"
COMMON = "games/tsukihime_common/tsukihime_common.cmake"
GAME_JP = "games/tsukihime/game.cmake"
GAME_GL = "games/tsukihime_global/game.cmake"
GITIGNORE = "../.gitignore"
RECO = "../tools/recomp_tsukihime.ps1"

ANDROID_BLOCK = '''# AP_ANDROID_BUILD (apply_android_build.py): Android (arm64-v8a, NDK). The libraries come built for arm64-android
# (static) from NX_ANDROID_DEPS, the prefix tools/android/android_deps.ps1 leaves in <root>/_prepare/android-deps
# (vcpkg, triplet arm64-android). The rest of this file is for Windows: return() ends it here on Android.
if(ANDROID)
    # the bars around the picture repeat its edge pixels (apply_android_present.py, nvn_present.cpp)
    target_compile_definitions(${NX_GAME} PRIVATE NX_EXTEND_BARS)
    # no Windows resources (icon, version): game.cmake adds tsukihime.rc, which is not compiled here
    set_source_files_properties("${NX_TSUKI_COMMON}/tsukihime.rc" PROPERTIES HEADER_FILE_ONLY ON)
    if(NX_GAME STREQUAL "tsukihime_global")
        # _init at +0x10 (SDK 17.5.4) is set in game.cmake; repeated here in case create_tsukihime_global.ps1 ever
        # writes it inside the if(NOT ANDROID) of the RC lines (the same value: no redefinition warning)
        target_compile_definitions(${NX_GAME} PRIVATE NX_DT_INIT=0x10)
    endif()
    set(NX_ANDROID_DEPS "${CMAKE_SOURCE_DIR}/../_prepare/android-deps" CACHE PATH
        "Prefix with include/ and lib/ of FFmpeg, opus, libjpeg-turbo, libpng and zlib for arm64-android")
    if(NOT EXISTS "${NX_ANDROID_DEPS}/include/libavcodec/avcodec.h")
        message(FATAL_ERROR "NX_ANDROID_DEPS (${NX_ANDROID_DEPS}) has no include/libavcodec/avcodec.h: "
                            "run tools/android/android_deps.ps1")
    endif()
    target_include_directories(${NX_GAME} PRIVATE "${NX_ANDROID_DEPS}/include" "${NX_ANDROID_DEPS}/include/opus")
    # The NDK toolchain limits find_* to its sysroot: NO_DEFAULT_PATH + NO_CMAKE_FIND_ROOT_PATH look only in the
    # prefix. Static link order: from the most dependent to the least.
    foreach(nx_lib avformat avcodec swscale swresample avutil opus jpeg png16 z)
        find_library(NX_ANDROID_LIB_${nx_lib} NAMES ${nx_lib} lib${nx_lib}.a
                     PATHS "${NX_ANDROID_DEPS}/lib" NO_DEFAULT_PATH NO_CMAKE_FIND_ROOT_PATH)
        if(NOT NX_ANDROID_LIB_${nx_lib})
            message(FATAL_ERROR "lib${nx_lib}.a is not in ${NX_ANDROID_DEPS}/lib (tools/android/android_deps.ps1)")
        endif()
        target_link_libraries(${NX_GAME} PRIVATE "${NX_ANDROID_LIB_${nx_lib}}")
    endforeach()
    # libm and mediandk come with the NDK: vcpkg's FFmpeg for Android has the MediaCodec wrapper (AMediaCodec_*),
    # unused (the game decodes in software) but needed by the static link.
    target_link_libraries(${NX_GAME} PRIVATE m mediandk)
    return()
endif()

'''

GAME_OLD = '''enable_language(RC)
target_compile_definitions(${NX_GAME} PRIVATE NX_CUSTOM_WINDOW_TITLE NX_WINDOW_ICON)
'''
GAME_NEW = '''# AP_ANDROID_BUILD (apply_android_build.py): Windows resources only (the NDK has no resource compiler; on Android
# tsukihime_common.cmake skips tsukihime.rc)
if(NOT ANDROID)
enable_language(RC)
target_compile_definitions(${NX_GAME} PRIVATE NX_CUSTOM_WINDOW_TITLE NX_WINDOW_ICON)
endif()
'''

EDITS = [
    # ------------------------------------------------------------------------------------------------ tsukihime_common.cmake
    (COMMON,
     '''file(GLOB NX_TSUKI_COMMON_SOURCES CONFIGURE_DEPENDS "${NX_TSUKI_COMMON}/*.cpp")
''',
     '''file(GLOB NX_TSUKI_COMMON_SOURCES CONFIGURE_DEPENDS "${NX_TSUKI_COMMON}/*.cpp")
if(ANDROID)  # AP_ANDROID_BUILD: controllers and touch come from the runtime (platform_android_input.cpp), not SDL3
    list(FILTER NX_TSUKI_COMMON_SOURCES EXCLUDE REGEX "/pad_sdl\\\\.cpp$")
endif()
'''),
    (COMMON,
     '''# FFmpeg for the movie:: HLE.
''',
     ANDROID_BLOCK + '''# FFmpeg for the movie:: HLE.
'''),

    # ------------------------------------------------------------------------------------------------ game.cmake (both)
    (GAME_JP, GAME_OLD, GAME_NEW),
    (GAME_GL, GAME_OLD, GAME_NEW),

    # ------------------------------------------------------------------------------------------------ .gitignore
    (GITIGNORE,
     '''!/tools/linux/*.py
''',
     '''!/tools/linux/*.py
# AP_ANDROID_BUILD (apply_android_build.py): the Android port. tools\\android: its scripts; <root>\\android: the
# launcher (Java), the manifest template and its resources. What they build (<root>\\_android: APKs with the game's
# code, the data for the phone) and the libraries (_prepare\\android-deps) stay out like everything else in the root.
!/tools/android/
/tools/android/*
!/tools/android/*.ps1
!/tools/android/*.sh
!/android/
/android/*
!/android/AndroidManifest.xml.in
!/android/README.md
!/android/java/
!/android/res/
!/android/boot/
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     '''    @('both', 'framework\\runtime\\src\\hle_hid.cpp', 'AP_PAD_SOURCE', 'apply_pad_source.py', $false),
''',
     '''    @('both', 'framework\\runtime\\src\\hle_hid.cpp', 'AP_PAD_SOURCE', 'apply_pad_source.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_ANDROID_BUILD', 'apply_android_build.py', $false),
    @('japan', 'framework\\games\\tsukihime\\game.cmake', 'AP_ANDROID_BUILD', 'apply_android_build.py', $false),
    @('global', 'framework\\games\\tsukihime_global\\game.cmake', 'AP_ANDROID_BUILD', 'apply_android_build.py', $true),
    @('both', '.gitignore', 'AP_ANDROID_BUILD', 'apply_android_build.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
