"""apply_native_loading.py - native libjpeg-turbo, libpng and zlib for hle_carga.cpp (AP_CARGA). Version 2.

hle_carga.cpp (new file, copied separately to games/tsukihime_common; picked up by the GLOB of tsukihime_common.cmake)
replaces nn::image::JpegDecoder::Decode, nn::image::PngDecoder::Decode and nn::util::Initialize*Context /
StreamingDecompressZlib/Gzip of the recompiled sdk, which slowed the scenes down (claude/tsukihime-efectos.md). This
patcher:
  - tsukihime_common.cmake: find_package(JPEG), find_package(PNG) and find_package(ZLIB) (vcpkg: libjpeg-turbo, libpng,
    zlib), links them and copies the DLLs that exist (jpeg62, libpng16, z, zlib1) next to the exe from ${NX_OPUS_ROOT}/bin
    (the same vcpkg prefix as opus);
  - ../tools/recomp_tsukihime.ps1: two rows in $Markers (tsukihime_common.cmake and hle_carga.cpp with AP_CARGA).

Wonder and the framework do not change (everything is in games/tsukihime_common). Refuses if the temporary
diagnostic diag_carga.cpp is still there (it registers the same symbols: remove it first).

Version 2: if it finds the block of version 1 (without libpng) it replaces it in place with the new one; --remove leaves
the files as before any version. Idempotent (marker AP_CARGA), all or nothing, keeps CRLF and BOM, refuses if an anchor
is not found; --remove leaves the files byte for byte as they were. After: cmake --build build\\tsukihime_global and
build\\tsukihime (the .cmake change reconfigures by itself). Variables: NX_TSUKI_CARGA=0 (everything to the sdk) or
NX_TSUKI_CARGA=check (compares with the sdk).
"""
import argparse
import os
import sys

from _patch import conv, load, run

MARK = "AP_CARGA"

CMAKE = "games/tsukihime_common/tsukihime_common.cmake"
RECOMP = "../tools/recomp_tsukihime.ps1"

ANCHOR_CMAKE = (
    "add_custom_command(TARGET ${NX_GAME} POST_BUILD\n"
    '    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${NX_OPUS_ROOT}/bin/opus.dll" "$<TARGET_FILE_DIR:${NX_GAME}>")\n'
)
BLOCK_CMAKE = r'''# AP_CARGA (apply_native_loading.py): libjpeg-turbo, libpng y zlib nativos para hle_carga.cpp (JpegDecoder::Decode,
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
'''

# Block of version 1 (2026-10-19 20:15: without libpng; expected zlib1.dll): updated in place.
BLOCK_V1 = r'''# AP_CARGA (apply_native_loading.py): libjpeg-turbo y zlib nativos para hle_carga.cpp (JpegDecoder::Decode y
# StreamingDecompressZlib/Gzip del sdk). vcpkg: libjpeg-turbo y zlib para x64-windows.
find_package(JPEG REQUIRED)
find_package(ZLIB REQUIRED)
target_link_libraries(${NX_GAME} PRIVATE JPEG::JPEG ZLIB::ZLIB)
foreach(nx_tsuki_dll jpeg62.dll zlib1.dll)
    if(EXISTS "${NX_OPUS_ROOT}/bin/${nx_tsuki_dll}")
        add_custom_command(TARGET ${NX_GAME} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${NX_OPUS_ROOT}/bin/${nx_tsuki_dll}" "$<TARGET_FILE_DIR:${NX_GAME}>")
    else()
        message(WARNING "AP_CARGA: no esta ${NX_OPUS_ROOT}/bin/${nx_tsuki_dll} (vcpkg install libjpeg-turbo zlib)")
    endif()
endforeach()
'''

ANCHOR_ROW = (r"    @('both', 'framework\games\tsukihime_common\discord_tsuki.cpp', 'AP_DISCORD', "
              r"'discord_tsuki.cpp (copy it to games\tsukihime_common)', $false)," + "\n")
NEW_ROWS = (
    r"    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_CARGA', 'apply_native_loading.py', $false)," + "\n" +
    r"    @('both', 'framework\games\tsukihime_common\hle_carga.cpp', 'AP_CARGA', 'hle_carga.cpp (copy it to games\tsukihime_common)', $false)," + "\n"
)

EDITS = [
    (CMAKE, ANCHOR_CMAKE, ANCHOR_CMAKE + BLOCK_CMAKE),
    (RECOMP, ANCHOR_ROW, ANCHOR_ROW + NEW_ROWS),
]

if __name__ == "__main__":
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--root", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework"))
    pre.add_argument("--remove", action="store_true")
    a, _ = pre.parse_known_args()
    diag = os.path.join(a.root, "games", "tsukihime_common", "diag_carga.cpp")
    if not a.remove and os.path.exists(diag):
        sys.exit("ERROR: " + diag + " is there (temporary diagnostic with the same symbols). "
                 "Remove it first. Nothing touched.")
    # Update from version 1: same marker, another block in tsukihime_common.cmake.
    if not a.remove:
        cmake_path = os.path.join(a.root, CMAKE)
        if os.path.isfile(cmake_path):
            txt, crlf, bom = load(cmake_path)
            old_block, new_block = conv(BLOCK_V1, crlf), conv(BLOCK_CMAKE, crlf)
            if txt.count(old_block) == 1 and new_block not in txt:
                with open(cmake_path, "wb") as f:
                    f.write((b"\xef\xbb\xbf" if bom else b"") + txt.replace(old_block, new_block).encode("utf-8"))
                print("updated from version 1: " + CMAKE)
    if a.remove:
        # removing over version 1 still applied: first the block is moved to version 2 (not in memory: on disk)
        cmake_path = os.path.join(a.root, CMAKE)
        if os.path.isfile(cmake_path):
            txt, crlf, bom = load(cmake_path)
            old_block, new_block = conv(BLOCK_V1, crlf), conv(BLOCK_CMAKE, crlf)
            if txt.count(old_block) == 1 and new_block not in txt:
                with open(cmake_path, "wb") as f:
                    f.write((b"\xef\xbb\xbf" if bom else b"") + txt.replace(old_block, new_block).encode("utf-8"))
    run(MARK, EDITS, __doc__)