# games/tsukihime_common/tsukihime_common.cmake: lo comun de Tsukihime japones y global.
# Lo incluyen games/tsukihime/game.cmake y games/tsukihime_global/game.cmake; no es un juego (sin game.cpp).
# Lo propio de cada edicion queda en su carpeta: game.cpp (kInfo, kWindowTitle) y las opciones de su game.cmake.
# Si algun dia el codigo comun tiene que distinguir la edicion: una define desde el game.cmake de la global
# (por ejemplo NX_TSUKI_GLOBAL), inerte para la japonesa.
set(NX_TSUKI_COMMON "${CMAKE_CURRENT_LIST_DIR}")
file(GLOB NX_TSUKI_COMMON_SOURCES CONFIGURE_DEPENDS "${NX_TSUKI_COMMON}/*.cpp")
target_sources(${NX_GAME} PRIVATE ${NX_TSUKI_COMMON_SOURCES})
target_include_directories(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}")  # movie_core.h, input_tsuki.h
# AP_PACKAGED_LAYOUT (apply_packaged_layout.py): block height of packaged (BNTX) textures from the game, not guessed
target_compile_definitions(${NX_GAME} PRIVATE NX_PACKAGED_LAYOUT_HLE)
# AP_HUD_GLYPHS (apply_hud_glyphs.py): button icons of the HUD follow the last device used (hud_glyphs.cpp);
# its files go to <data root>/hud (tools/hud/build_hud_assets.py). NX_HUD_GLYPHS=off at run time turns it off.
target_compile_definitions(${NX_GAME} PRIVATE NX_HUD_GLYPHS)
# AP_ROMFS_MODS (apply_romfs_mods.py): romfs layers of <root>/mods/<edition>: mods copied by hand and the romfs of
# the updates of <root>/updates (recomp_tsukihime.ps1)
if(NX_GAME STREQUAL "tsukihime_global")
    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION="global")
else()
    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION="japan")
endif()

# FFmpeg (vcpkg, x64-windows) para el HLE de movie::.
find_package(FFMPEG REQUIRED)
target_include_directories(${NX_GAME} PRIVATE ${FFMPEG_INCLUDE_DIRS})
target_link_directories(${NX_GAME} PRIVATE ${FFMPEG_LIBRARY_DIRS})
target_link_libraries(${NX_GAME} PRIVATE ${FFMPEG_LIBRARIES})
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
