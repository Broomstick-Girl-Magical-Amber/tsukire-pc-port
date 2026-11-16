# games/tsukihime_common/tsukihime_common.cmake: lo comun de Tsukihime japones y global.
# Lo incluyen games/tsukihime/game.cmake y games/tsukihime_global/game.cmake; no es un juego (sin game.cpp).
# Lo propio de cada edicion queda en su carpeta: game.cpp (kInfo, kWindowTitle) y las opciones de su game.cmake.
# Si algun dia el codigo comun tiene que distinguir la edicion: una define desde el game.cmake de la global
# (por ejemplo NX_TSUKI_GLOBAL), inerte para la japonesa.
set(NX_TSUKI_COMMON "${CMAKE_CURRENT_LIST_DIR}")
file(GLOB NX_TSUKI_COMMON_SOURCES CONFIGURE_DEPENDS "${NX_TSUKI_COMMON}/*.cpp")
if(ANDROID)  # AP_ANDROID_BUILD: controllers and touch come from the runtime (platform_android_input.cpp), not SDL3
    list(FILTER NX_TSUKI_COMMON_SOURCES EXCLUDE REGEX "/pad_sdl\\.cpp$")
endif()
target_sources(${NX_GAME} PRIVATE ${NX_TSUKI_COMMON_SOURCES})
target_include_directories(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}")  # movie_core.h, input_tsuki.h
# AP_PACKAGED_LAYOUT (apply_packaged_layout.py): block height of packaged (BNTX) textures from the game, not guessed
target_compile_definitions(${NX_GAME} PRIVATE NX_PACKAGED_LAYOUT_HLE)
# AP_HUD_GLYPHS (apply_hud_glyphs.py): button icons of the HUD follow the last device used (hud_glyphs.cpp);
# its files go to <data root>/hud (tools/hud/build_hud_assets.py). NX_HUD_GLYPHS=off at run time turns it off.
target_compile_definitions(${NX_GAME} PRIVATE NX_HUD_GLYPHS)
# AP_TITLE_MUSIC (apply_title_music.py): the music of the title screen can be another track (title_bg.cpp, title.ini)
target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_MUSIC)
# AP_TITLE_LIVE (apply_title_live.py): F5 / F6 change the title screen video while it is up
target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_LIVE)
# AP_TITLE_CLICK (apply_title_click.py): a click on the logo of the title menu changes the video, as F6
target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_CLICK)
# AP_ROMFS_MODS (apply_romfs_mods.py): romfs layers of <root>/mods/<edition>: mods copied by hand and the romfs of
# the updates of <root>/updates (recomp_tsukihime.ps1)
if(NX_GAME STREQUAL "tsukihime_global")
    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION="global")
else()
    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION="japan")
endif()
# AP_ROMFS_DIRECT (apply_romfs_direct.py): rom: reads the RomFS straight from the .xci/.nsp of the root of the port
# (romfs_direct.cpp, with the <data root>/romfs_source.bin that recomp_tsukihime.ps1 writes); without it, extract/romfs
target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_DIRECT)
# AP_CONFIG_ENV (apply_config_env.py): the NX_* lines of <data root>/config.txt become environment variables at start
# (platform_win.cpp); the launcher's Controls and title tab writes that file
target_compile_definitions(${NX_GAME} PRIVATE NX_CONFIG_ENV)

# AP_ANDROID_BUILD (apply_android_build.py): Android (arm64-v8a, NDK). The libraries come built for arm64-android
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

# FFmpeg for the movie:: HLE.
# AP_FFMPEG_PREBUILT (apply_ffmpeg_prebuilt.py): with NX_FFMPEG_ROOT (recomp_tsukihime.ps1 sets it to the prebuilt FFmpeg 9.0
# LGPL that install_requirements.ps1 leaves in _prepare/ffmpeg: include/, lib/*.lib and bin/*.dll) that one is linked and
# its DLLs are copied next to the exe; without it, the one from vcpkg (find_package(FFMPEG), x64-windows) as before.
set(NX_FFMPEG_ROOT "" CACHE PATH "Prebuilt FFmpeg (include, lib, bin); empty = FFmpeg from vcpkg")
if(NX_FFMPEG_ROOT)
    if(NOT EXISTS "${NX_FFMPEG_ROOT}/include/libavcodec/avcodec.h")
        message(FATAL_ERROR "NX_FFMPEG_ROOT=${NX_FFMPEG_ROOT} has no include/libavcodec/avcodec.h (install_requirements.ps1 step 5)")
    endif()
    target_include_directories(${NX_GAME} PRIVATE "${NX_FFMPEG_ROOT}/include")
    foreach(nx_ff_name avcodec avformat avutil swscale swresample)
        if(NOT EXISTS "${NX_FFMPEG_ROOT}/lib/${nx_ff_name}.lib")
            message(FATAL_ERROR "${NX_FFMPEG_ROOT}/lib/${nx_ff_name}.lib does not exist (install_requirements.ps1 step 5)")
        endif()
        target_link_libraries(${NX_GAME} PRIVATE "${NX_FFMPEG_ROOT}/lib/${nx_ff_name}.lib")
        file(GLOB nx_ff_dll "${NX_FFMPEG_ROOT}/bin/${nx_ff_name}-*.dll")
        list(LENGTH nx_ff_dll nx_ff_dll_count)
        if(NOT nx_ff_dll_count EQUAL 1)
            message(FATAL_ERROR "${NX_FFMPEG_ROOT}/bin/${nx_ff_name}-*.dll: ${nx_ff_dll_count} files (1 expected)")
        endif()
        add_custom_command(TARGET ${NX_GAME} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${nx_ff_dll}" "$<TARGET_FILE_DIR:${NX_GAME}>")
    endforeach()
else()
    find_package(FFMPEG REQUIRED)
    target_include_directories(${NX_GAME} PRIVATE ${FFMPEG_INCLUDE_DIRS})
    target_link_directories(${NX_GAME} PRIVATE ${FFMPEG_LIBRARY_DIRS})
    target_link_libraries(${NX_GAME} PRIVATE ${FFMPEG_LIBRARIES})
endif()
# libopus: HardwareOpusDecoder (hle_codec.cpp)
set(NX_OPUS_ROOT "C:/vcpkg/installed/x64-windows" CACHE PATH "Prefijo de libopus (include/opus, lib, bin)")
find_path(NX_OPUS_INCLUDE opus.h HINTS "${NX_OPUS_ROOT}/include/opus" REQUIRED)
find_library(NX_OPUS_LIB opus HINTS "${NX_OPUS_ROOT}/lib" REQUIRED)
target_include_directories(${NX_GAME} PRIVATE "${NX_OPUS_INCLUDE}")
target_link_libraries(${NX_GAME} PRIVATE "${NX_OPUS_LIB}")
# opus.dll junto al exe
add_custom_command(TARGET ${NX_GAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${NX_OPUS_ROOT}/bin/opus.dll" "$<TARGET_FILE_DIR:${NX_GAME}>")
# AP_CARGA (apply_native_loading.py): libjpeg-turbo, libpng y zlib nativos para hle_carga.cpp (JpegDecoder::Decode,
# PngDecoder::Decode y StreamingDecompressZlib/Gzip del sdk). vcpkg: libjpeg-turbo, libpng y zlib para x64-windows.
find_package(JPEG REQUIRED)
find_package(PNG REQUIRED)
find_package(ZLIB REQUIRED)
target_link_libraries(${NX_GAME} PRIVATE JPEG::JPEG PNG::PNG ZLIB::ZLIB)
foreach(nx_tsuki_dll jpeg62.dll libpng16.dll z.dll zlib1.dll)
    if(EXISTS "${NX_OPUS_ROOT}/bin/${nx_tsuki_dll}")
        add_custom_command(TARGET ${NX_GAME} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${NX_OPUS_ROOT}/bin/${nx_tsuki_dll}" "$<TARGET_FILE_DIR:${NX_GAME}>")
    endif()
endforeach()
# AP_SDL_PAD (apply_sdl_pad.py): SDL3 solo para los mandos (pad_sdl.cpp): PlayStation, Switch y Xbox por USB y
# Bluetooth. vcpkg: sdl3 para x64-windows. SDL3.dll va con /DELAYLOAD: sin la DLL el exe arranca y usa XInput.
find_package(SDL3 CONFIG REQUIRED)
target_link_libraries(${NX_GAME} PRIVATE SDL3::SDL3)
if(MSVC)
    target_link_options(${NX_GAME} PRIVATE "/DELAYLOAD:SDL3.dll")
    target_compile_definitions(${NX_GAME} PRIVATE NX_SDL_DELAYLOAD)
endif()
add_custom_command(TARGET ${NX_GAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "$<TARGET_FILE:SDL3::SDL3>" "$<TARGET_FILE_DIR:${NX_GAME}>")
