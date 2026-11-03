"""apply_ffmpeg_prebuilt.py - link a prebuilt FFmpeg instead of building it with vcpkg. Marker AP_FFMPEG_PREBUILT.

Why: `vcpkg install ffmpeg` is the longest step of install_requirements.ps1 (step 5). BtbN publishes FFmpeg 9.0 LGPL
"shared" builds (include/, lib/*.lib import libraries, bin/*.dll). The repo does NOT contain or redistribute anything:
each PC downloads the zip from BtbN and it is checked against a fixed SHA-256 before it is used.

Pin: BtbN month-end autobuild 2026-09-30 (BtbN keeps the last build of every month for two years; the daily ones only
14 days), FFmpeg n9.0.2 (the same version as the vcpkg reference commit), DLLs avcodec-63, avformat-63, avutil-61,
swscale-10, swresample-7 (the same names the vcpkg build produced). The SHA-256 is the one of the winget manifest
BtbN.FFmpeg.LGPL.Shared.9.0 9.0.2-20260930 and was also measured on the downloaded zip.

Edits (framework and tools; the runtime and Wonder are unchanged):
- games/tsukihime_common/tsukihime_common.cmake: new cache variable NX_FFMPEG_ROOT. If it is set, the include folder, the
  five import libraries are linked and the five DLLs are copied next to the exe; if it is empty, the old
  find_package(FFMPEG) (vcpkg) path is used as before.
- ../tools/install_requirements.ps1: "ffmpeg" leaves $VcpkgPorts; step 5 first calls EnsureFfmpeg (download, SHA-256,
  unpack to <root>\\_prepare\\ffmpeg\\<name>, all or nothing; with -Check it only reports).
- ../tools/recomp_tsukihime.ps1: step 9 passes -DNX_FFMPEG_ROOT=<that folder> (new configure) or reconfigures only that
  variable in a build that was configured before; two new rows in $Markers.

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_ffmpeg_prebuilt.py [--remove]
After: powershell -File ..\\tools\\install_requirements.ps1 -From 5 -To 5   (downloads FFmpeg, installs the other ports),
then recomp_tsukihime.ps1 -From 9 -To 9 (reconfigures and rebuilds).
To go back to the vcpkg FFmpeg without removing the patch: cmake -B build\\<edition> -DNX_FFMPEG_ROOT= (and vcpkg install ffmpeg).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_FFMPEG_PREBUILT"
CMAKE = "games/tsukihime_common/tsukihime_common.cmake"
INSTALL = "../tools/install_requirements.ps1"
RECOMP = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------ CMake
    (CMAKE,
     """# FFmpeg (vcpkg, x64-windows) para el HLE de movie::.
find_package(FFMPEG REQUIRED)
target_include_directories(${NX_GAME} PRIVATE ${FFMPEG_INCLUDE_DIRS})
target_link_directories(${NX_GAME} PRIVATE ${FFMPEG_LIBRARY_DIRS})
target_link_libraries(${NX_GAME} PRIVATE ${FFMPEG_LIBRARIES})
""",
     """# FFmpeg for the movie:: HLE.
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
"""),

    # ------------------------------------------------------------------------------------------ install_requirements.ps1
    (INSTALL,
     "#   5 libraries      vcpkg install ffmpeg opus libjpeg-turbo libpng zlib sdl3 (x64-windows Release only; the longest)\n",
     "#   5 libraries      prebuilt FFmpeg 9.0 LGPL from BtbN (fixed SHA-256, to _prepare\\ffmpeg; AP_FFMPEG_PREBUILT) and\n"
     "#                    vcpkg install opus libjpeg-turbo libpng zlib sdl3 (x64-windows Release only)\n"),

    (INSTALL,
     "$VcpkgPorts = @('ffmpeg', 'opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3')\n",
     r"""$VcpkgPorts = @('opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3')
# AP_FFMPEG_PREBUILT (apply_ffmpeg_prebuilt.py): FFmpeg is not built by vcpkg any more. FFmpeg 9.0 LGPL "shared" build of BtbN
# (include, lib\*.lib, bin\*.dll), month-end autobuild 2026-09-30 (BtbN keeps those for two years), n9.0.2 like the vcpkg
# reference commit. SHA-256 of the winget manifest BtbN.FFmpeg.LGPL.Shared.9.0 9.0.2-20260930 (also measured on the zip).
$FfmpegUrl = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n9.0.2-17-g2a571b6068-win64-lgpl-shared-9.0.zip'
$FfmpegSha256 = '7157177B8A6CB2174C1650BA8C71B363F2C78CBA5330F88C4C02CF5B2B880646'
$FfmpegDirName = 'ffmpeg-n9.0.2-17-g2a571b6068-win64-lgpl-shared-9.0'
$FfmpegLibs = @('avcodec', 'avformat', 'avutil', 'swscale', 'swresample')
$FfmpegDlls = @('avcodec-63.dll', 'avformat-63.dll', 'avutil-61.dll', 'swscale-10.dll', 'swresample-7.dll')
"""),

    (INSTALL,
     "function Step5 {\n"
     "    $env:VCPKG_ROOT = $Vcpkg                 # a VS console points it to its internal copy\n"
     "    $env:VCPKG_DISABLE_METRICS = '1'\n"
     "    $missing = @(MissingPorts)\n",
     r"""# AP_FFMPEG_PREBUILT: the prebuilt FFmpeg is complete if it has the headers, the five import libraries and the five DLLs.
function FfmpegReady([string]$dir) {
    $files = @('include\libavcodec\avcodec.h', 'include\libavformat\avformat.h') + @($FfmpegLibs | ForEach-Object { "lib\$_.lib" }) + @($FfmpegDlls | ForEach-Object { "bin\$_" })
    foreach ($f in $files) { if (-not (Test-Path -LiteralPath (PathOf $dir $f) -PathType Leaf)) { return $false } }
    return $true
}
# Downloads the zip (the bytes are checked against $FfmpegSha256 before anything is unpacked), unpacks it next to the final folder
# and renames it at the end: a folder that exists is a complete one. Nothing is installed or changed outside <root>\_prepare\ffmpeg.
function EnsureFfmpeg {
    $dir = PathOf $WorkDir "ffmpeg\$FfmpegDirName"
    if (FfmpegReady $dir) { Info "prebuilt FFmpeg $FfmpegDirName in $dir"; $script:Done.Add("FFmpeg prebuilt $FfmpegDirName"); return }
    if ($Check) { Problem "prebuilt FFmpeg is missing ($dir): step 5 downloads it from BtbN ($FfmpegUrl)"; return }
    $parent = Split-Path $dir -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $zip = PathOf $parent 'ffmpeg_prebuilt.zip'
    $tmp = "$dir.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    if (-not ((Test-Path -LiteralPath $zip) -and (Sha256 $zip) -eq $FfmpegSha256)) {
        Download $FfmpegUrl $zip
        $got = Sha256 $zip
        if ($got -ne $FfmpegSha256) { Remove-Item -LiteralPath $zip -Force; Fail "the FFmpeg zip has SHA-256 $got, not the reference $FfmpegSha256 (nothing was unpacked)" }
    }
    Info 'FFmpeg zip: SHA-256 OK; unpacking'
    try {
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp
        $inner = PathOf $tmp $FfmpegDirName
        if (-not (FfmpegReady $inner)) { Fail "the FFmpeg zip does not have the expected files in $FfmpegDirName\ (headers, lib\*.lib, bin\*.dll)" }
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        Move-Item -LiteralPath $inner -Destination $dir
    } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue } }
    Remove-Item -LiteralPath $zip -Force
    Info "prebuilt FFmpeg in $dir"
    $script:Done.Add("FFmpeg prebuilt $FfmpegDirName (installed now)")
}
function Step5 {
    $env:VCPKG_ROOT = $Vcpkg                 # a VS console points it to its internal copy
    $env:VCPKG_DISABLE_METRICS = '1'
    EnsureFfmpeg
    $missing = @(MissingPorts)
"""),

    (INSTALL,
     """    Info "installing $($missing -join ', ') (ffmpeg builds from source: from 1 to several hours depending on the PC; if it is interrupted, -From 5 continues where it stopped)"\n""",
     """    Info "installing $($missing -join ', ') (vcpkg builds them from source; if it is interrupted, -From 5 continues where it stopped)"\n"""),

    # ------------------------------------------------------------------------------------------ recomp_tsukihime.ps1
    (RECOMP,
     "    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_HUD_GLYPHS', 'apply_hud_glyphs.py', $false),\n",
     "    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_HUD_GLYPHS', 'apply_hud_glyphs.py', $false),\n"
     "    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),\n"
     "    @('both', 'tools\\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),\n"),

    (RECOMP,
     "# Linux: the framework CMakeLists (WIN32 branch) includes \"${NX_VULKAN_SDK}/Include\" as in the Windows SDK; the\n",
     r"""# AP_FFMPEG_PREBUILT: folder of the prebuilt FFmpeg that install_requirements.ps1 (step 5) leaves in _prepare\ffmpeg (the most
# recent one that has lib\avcodec.lib), with forward slashes for CMake; '' if there is none (CMake then uses the vcpkg one).
function FfmpegPrebuilt {
    $base = InRoot '_prepare\ffmpeg'
    if (-not (Test-Path -LiteralPath $base)) { return '' }
    $d = @(Get-ChildItem -LiteralPath $base -Directory | Where-Object { Test-Path -LiteralPath (Under $_.FullName 'lib\avcodec.lib') -PathType Leaf } | Sort-Object LastWriteTime -Descending)
    if (-not $d.Count) { return '' }
    return ($d[0].FullName -replace '\\', '/')
}

""" + "# Linux: the framework CMakeLists (WIN32 branch) includes \"${NX_VULKAN_SDK}/Include\" as in the Windows SDK; the\n"),

    (RECOMP,
     '            Info "$Build already configured"\n',
     r"""            Info "$Build already configured"
            # AP_FFMPEG_PREBUILT: a build configured before this patch (or with another FFmpeg folder) changes only this variable
            $ffRoot = FfmpegPrebuilt
            if ($ffRoot -and (CacheValue $cache 'NX_FFMPEG_ROOT') -ne $ffRoot) {
                Info "NX_FFMPEG_ROOT -> $ffRoot (reconfiguring; the rest of the cache stays the same)"
                RunVisible 'cmake' @('-B', $Build, "-DNX_FFMPEG_ROOT=$ffRoot") 'cmake (reconfigure NX_FFMPEG_ROOT)'
            }
"""),

    (RECOMP,
     """            Info "cmake $($a -join ' ')"\n            RunVisible 'cmake' $a 'cmake (configure)'\n""",
     """            # AP_FFMPEG_PREBUILT: prebuilt FFmpeg of install_requirements.ps1 (step 5); without it CMake uses the vcpkg one\n"""
     """            $ffRoot = FfmpegPrebuilt\n"""
     """            if ($ffRoot) { $a += "-DNX_FFMPEG_ROOT=$ffRoot" }\n"""
     """            Info "cmake $($a -join ' ')"\n            RunVisible 'cmake' $a 'cmake (configure)'\n"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)