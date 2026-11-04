# recomp_tsukihime.ps1 - From a Tsukihime .xci/.nsp to the compiled exe, ready to play (it does not start the game).
#
# Paths are relative to where the script is: <root>\tools\recomp_tsukihime.ps1 -> the root is the folder above
# (or the current one if the script is not in tools\). Put your .xci or .nsp (either one, for either edition) in the
# ROOT of the port and -InputFile is not needed: the script looks for it there.
#   .\recomp_tsukihime.ps1 -Edition global
#   .\recomp_tsukihime.ps1 -Edition japan
#   .\recomp_tsukihime.ps1 -InputFile <path\to\dump.nsp or .xci> -Edition global     # a dump somewhere else
#   .\recomp_tsukihime.ps1 -Edition global -From 6            # resume (from step 3 on, -InputFile is not needed)
#   .\recomp_tsukihime.ps1 -Edition global -From 3 -Compare <hashes.txt>   # check against an inventory
#
# Steps (each one checks its result; if it is already done and verified, it is skipped):
#   0 precheck       tools, framework patches (markers of each apply_*.py), keys, space
#   1 identify       program ID of the base application, read with tools\nsextract (C#, LibHac). The container is
#                    recognized by its header (PFS0 = NSP, HEAD = XCI), not by the extension or the edition.
#   2 extract        exefs + romfs straight from the input with nsextract (in _recomp\, moved at the end); AP_DROP_UNUSED:
#                    nstool, the fallback until 2026-11-02, is gone
#   3 verify         program ID of main.npdm and build ID of main (+0x40) against the measured ones; then updates\ (below)
#   4 structure      japan: games\tsukihime; global: create_tsukihime_global.ps1 (idempotent)
#   5 analysis       tools\nso2bin.py -> analysis\main.bin and sdk.bin
#   6 recomp         regen_tsuki*.ps1 main, sdk -> gen\ (the previous gen\ is kept as gen.old)
#   7 fonts          fonts\shared_0.ttf (Noto Sans JP, OFL license): from -Fonts, from <root>\resources\fonts\ (in the repo) or from the other edition
#   8 shaders        tsuki_shaders_offline.py (romfs\shader + main) -> harness only for those without a .spv
#   9 build          cmake -B (parameters of the existing CMakeCache) + cmake --build; then the HUD icons by device
#                    (tools\hud\build_hud_assets.py -> <data>\hud; never fatal: without them the game keeps its icons)
#  10 summary
#
# The build environment (dev.local.ps1, or tools\dev_env.ps1 if there is none) is loaded only when needed (step 0).
# Rules: does not touch user\, language.txt, input.ini or the framework (read only; the global uses create_tsukihime_global.ps1).
# Never deletes from data\ what it did not create: what is replaced is kept as *.old. Keys are never copied or printed.
# -Replace redoes extraction and recomp even if they are done. -Root to point to another copy of the port.
# Keys: <root>\data\prod.keys (never copied or printed).
# -ProgressSeconds: how often the progress of the extraction (step 2) is reported (30 by default).
# Linux (PowerShell 7, via tools/linux/recomp_linux.sh): same steps; step 9 cross-compiles the SAME Windows exe
# (clang-cl + lld-link + llvm-rc against the xwin CRT/SDK; tools/linux/clangcl-xwin.cmake) to play it with
# Proton. The tools are installed by tools/linux/install_requirements_linux.sh. Nothing changes on Windows.
# Updates and mods: every <root>\updates\*.nsp (updates or patches, e.g. a translation; a name starting with _ is
# skipped) of this edition is processed after step 3 by tools\internal\mods_nsp.py, without the base dump: the base
# romfs is rebuilt from extract\romfs plus <data>\romfs_index.bin, made ONCE from the dump: in step 2 whenever the dump
# is at hand (so a later update never needs it), or else the first time there is an .nsp. Its exefs goes to
# <data>\updates\<name>\ (only for the recompilation) and the romfs files that differ from the base go to
# <root>\mods\<edition>\<name>\romfs. The exe reads every folder of <root>\mods\<edition>\ (name order, the last one
# wins; _ skips it) before its own romfs (apply_romfs_mods.py), so mods can also be copied there by hand, without
# recompiling. Once processed, the .nsp can be deleted: an update stays
# while its folder mods\<edition>\<name> exists (rename it to _<name> to disable it, delete it to remove it; then
# recompile). If an update changes main, steps 5-6 recompile main from it and its layer gets requires_main.txt (the
# exe skips it if it was built from another main). Two active updates with different mains cannot be combined; any
# other exefs module changed is refused. -NoUpdates: base main, updates\ not read (romfs-only layers still load).
# Portable data root: game.cpp has "../../../data/<edition>" and the runtime (apply_data_root_exe.py, NX_DATA_ROOT_EXE
# in game.cmake) resolves it from the exe folder: a full copy of the port works in any folder or PC,
# with a double click and without NX_DATA_ROOT (which still takes priority if set).
param(
    [string]$InputFile = '',
    [Parameter(Mandatory = $true)][ValidateSet('japan', 'global')][string]$Edition,
    [ValidateRange(0, 10)][int]$From = 0,
    [ValidateRange(0, 10)][int]$To = 10,
    [string]$Root = '',
    [string]$Keys = '',
    [string]$Fonts = '',
    [string]$Compare = '',
    [switch]$Replace,
    [switch]$ForceBuildId,
    [switch]$NoUpdates,
    [ValidateRange(2, 600)][int]$ProgressSeconds = 30,
    [ValidateRange(0, 512)][int]$Jobs = 0   # AP_RECOMP_PARALLEL: compile jobs of step 9 (0 = ninja's default, CPUs + 2)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$OnLinux = [bool]((Test-Path variable:IsLinux) -and $IsLinux)   # PowerShell 7 on Linux (in 5.1 the variable does not exist)

# ---------------------------------------------------------------------------------------------- root
# Without -Root: the folder above tools\ (where this script is); if the script is not in tools\, its folder or the
# current folder (or the one above) as long as it has framework\.
if (-not $Root) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
    $cands = @((Split-Path -Parent $here), $here, (Get-Location).Path, (Split-Path -Parent (Get-Location).Path)) | Where-Object { $_ }
    $Root = $cands | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'framework') -PathType Container } | Select-Object -First 1
    if (-not $Root) { throw "cannot find the port root (a folder with framework\) starting from ${here}: pass -Root <folder>" }
}
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
if (-not $Keys) { $Keys = Join-Path $Root 'data\prod.keys' }

# ---------------------------------------------------------------------------------------------- measured data
# Build IDs: japan taken from data\tsuki\exefs\main (2026-10-18), global from tsukihime-global.md (Stage 1).
# Romfs files: those of data\tsuki*\extract today (only a warning if it differs).
$Editions = @{
    japan  = @{ ProgramId = [Convert]::ToUInt64('01001DC01486A000', 16); BuildId = '94a8cbee6e48575ec8c90e3079608082a9a29b61'
                Data = 'data\tsuki'; Game = 'tsukihime'; Regen = 'regen_tsuki.ps1'; RomfsFiles = 16729
                Other = 'global' }
    global = @{ ProgramId = [Convert]::ToUInt64('010057A01E4D4000', 16); BuildId = 'b825b1746fa69a0ab8fab386321de8d02d10a3ac'
                Data = 'data\tsuki_global'; Game = 'tsukihime_global'; Regen = 'regen_tsuki_global.ps1'
                RomfsFiles = 16835; Other = 'japan' }
}
$FontSha256 = '8EA2D3ECEECB43A55F2567F7A8ABF85A5BB1F4FCA9A53E4786B7773F2C6F9D56'  # fonts\shared_0.ttf of today

# Markers: string each apply_*.py uses to detect that it is already applied (read from the patchers).
# Ed: both | japan | global. Created = file created by step 4 (checked after that step).
$Markers = @(
    @('both', 'framework\recomp\analysis.py', '_JT_LSR = ', 'apply_jt_lsr.py (version 2)', $false),
    @('both', 'framework\runtime\src\kernel.cpp', 'CancelSynchronization that arrived before', 'apply_cancel_sync.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw_state.h', 'stage_program', 'apply_stage_programs.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw_state.cpp', 'stage_program', 'apply_stage_programs.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw.cpp', 'stage_program', 'apply_stage_programs.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn.h', 'apply_clear_texture', 'apply_clear_texture.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.h', 'apply_clear_texture', 'apply_clear_texture.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw_state.cpp', 'apply_clear_texture', 'apply_clear_texture.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_copy.cpp', 'apply_clear_texture', 'apply_clear_texture.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw.cpp', 'origin_lower_left', 'apply_origin_lower_left.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'origin_lower_left', 'apply_origin_lower_left.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_draw.cpp', 'NX_DEFAULT_ORIGIN_LOWER_LEFT', 'apply_origin_default.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'NX_DEFAULT_ORIGIN_LOWER_LEFT', 'apply_origin_default.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'pick_swapchain_format', 'apply_srgb_present.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_ASPECT', 'apply_aspect.py', $false),
    @('both', 'framework\runtime\src\platform.h', 'AP_ASPECT', 'apply_aspect.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_ASPECT', 'apply_aspect.py', $false),
    @('both', 'framework\runtime\include\nx\game.h', 'nvn_format_count', 'apply_nvn_format_table.py', $false),
    @('both', 'framework\runtime\include\nx\game.h', 'audio_execution_mode_offset', 'apply_audio_sdk.py', $false),
    @('both', 'framework\runtime\src\hle_audio.cpp', 'execution_mode_offset()', 'apply_audio_sdk.py', $false),
    @('both', 'framework\runtime\src\hle_audio.cpp', 'OpenDefaultAudioOutNoEvent', 'apply_audio_out.py', $false),
    @('both', 'framework\runtime\src\platform.h', 'AP_MOUSE', 'apply_mouse.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_MOUSE', 'apply_mouse.py', $false),
    @('both', 'framework\runtime\src\hle_hid.cpp', 'AP_MOUSE', 'apply_mouse.py', $false),
    @('both', 'framework\runtime\src\platform.h', 'AP_PAD_SOURCE', 'apply_pad_source.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_PAD_SOURCE', 'apply_pad_source.py', $false),
    @('both', 'framework\runtime\src\hle_hid.cpp', 'AP_PAD_SOURCE', 'apply_pad_source.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_WINDOW', 'apply_game_window.py', $false),
    @('both', 'framework\runtime\src\svc_am.cpp', 'AP_LANG_LOG', 'apply_lang_log.py', $false),
    @('both', 'framework\runtime\src\language.h', 'AP_LANG_DEFAULT', 'apply_game_language.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'NX_TSUKI_COMMON', 'games\tsukihime_common (shared code, in the repo)', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'write_frame_to_texture', 'apply_movie_texture.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_DATA_ROOT_EXE', 'apply_data_root_exe.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_DISCORD', 'apply_discord.py', $false),
    @('both', 'framework\games\tsukihime_common\discord_tsuki.cpp', 'AP_DISCORD', 'discord_tsuki.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_CARGA', 'apply_native_loading.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_carga.cpp', 'AP_CARGA', 'hle_carga.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),
    @('both', 'framework\games\tsukihime_common\input_tsuki.cpp', 'AP_SDL_PAD', 'apply_sdl_pad.py', $false),
    @('both', 'framework\games\tsukihime_common\pad_sdl.cpp', 'AP_SDL_PAD', 'pad_sdl.cpp and pad_sdl.h (copy them to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\input_tsuki.cpp', 'AP_MOUSE_INI', 'apply_mouse_ini.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_BG', 'apply_title_bg.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_BG', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\runtime\src\hle_fs.cpp', 'AP_TITLE_MUSIC', 'apply_title_music.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_MUSIC', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_SCOPE', 'apply_title_scope.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_SCOPE', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),
    @('both', 'framework\games\tsukihime_common\movie_core.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),
    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_TITLE_LIVE', 'apply_title_live.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_LIVE', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\hle_codec.cpp', 'AP_TITLE_LIVE_OPUS', 'apply_title_live_opus.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_LIVE_OPUS', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),
    @('both', 'framework\games\tsukihime_common\movie_core.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_codec.cpp', 'AP_TITLE_FADE', 'apply_title_fade.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_FADE', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_SYNC', 'apply_title_sync.py', $false),
    @('both', 'framework\games\tsukihime_common\movie_core.cpp', 'AP_TITLE_SYNC', 'apply_title_sync.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_SYNC', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\runtime\src\hle_fs.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),
    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),
    @('both', 'framework\games\tsukihime_common\movie_core.cpp', 'AP_TITLE_SMOOTH', 'apply_title_smooth.py', $false),
    @('both', 'framework\runtime\src\hle_fs.cpp', 'AP_TITLE_LIVE_MUSIC', 'apply_title_live_music.py', $false),
    @('both', 'framework\games\tsukihime_common\title_bg.cpp', 'AP_TITLE_LIVE_MUSIC', 'title_bg.cpp (copy it to games\tsukihime_common)', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_VK_REQ', 'apply_vk_requirements.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_VK_FAIL', 'apply_vk_fail.py', $false),
    @('both', 'framework\runtime\src\hle_fs.cpp', 'AP_ROMFS_MODS', 'apply_romfs_mods.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_ROMFS_MODS', 'apply_romfs_mods.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_objects.cpp', 'AP_PACKAGED_LAYOUT', 'apply_packaged_layout.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_PACKAGED_LAYOUT', 'apply_packaged_layout.py', $false),
    @('both', 'framework\games\tsukihime_common\hud_glyphs.cpp', 'AP_HUD_GLYPHS', 'hud_glyphs.cpp and hud_glyphs.h (in the repo)', $false),
    @('both', 'framework\runtime\src\nvn\nvn_upload.cpp', 'AP_HUD_GLYPHS', 'apply_hud_glyphs.py', $false),
    @('both', 'framework\games\tsukihime_common\input_tsuki.cpp', 'AP_HUD_GLYPHS', 'apply_hud_glyphs.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_HUD_GLYPHS', 'apply_hud_glyphs.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
    @('both', 'tools\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'tsukihime_common/tsukihime_common.cmake', 'games\tsukihime_common (shared code, in the repo)', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'NX_DEFAULT_ORIGIN_LOWER_LEFT', 'apply_origin_default.py', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'NX_FIXED_ASPECT', 'apply_aspect.py', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'AP_LANG_DEFAULT', 'apply_game_language.py', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'NX_DATA_ROOT_EXE', 'apply_data_root_exe.py', $false),
    @('japan', 'framework\games\tsukihime\game.cpp', '"../../../data/tsuki",', 'apply_data_root_exe.py', $false),
    @('japan', 'tools\internal\regen_tsuki.ps1', 'NX_RECOMP_JT_LSR', 'original regen_tsuki.ps1', $false),
    @('japan', 'tools\internal\regen_tsuki.ps1', 'NX_RECOMP_JT_MOV', 'regen_tsuki.ps1 of the updates\ change (tools\internal)', $false),
    @('japan', 'framework\games\tsukihime\game.cmake', 'AP_VK_REQ', 'apply_vk_requirements.py', $false),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'AP_VK_REQ', 'apply_vk_requirements.py', $true),
    @('global', 'framework\recomp\emit.py', '_JT_FUERA = ', 'apply_jt_outside.py', $false),
    @('global', 'framework\recomp\nso.py', '_RELR = ', 'apply_relr.py', $false),
    @('both', 'framework\recomp\analysis.py', '_JT_MOV = ', 'apply_jt_mov.py', $false),
    @('global', 'framework\runtime\src\main.cpp', 'AP_DT_INIT', 'apply_dt_init.py', $false),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'tsukihime_common/tsukihime_common.cmake', 'create_tsukihime_global.ps1', $true),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'AP_DT_INIT', 'apply_dt_init.py', $true),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'AP_LANG_DEFAULT', 'apply_game_language.py', $true),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'NX_DEFAULT_ORIGIN_LOWER_LEFT', 'create_tsukihime_global.ps1', $true),
    @('global', 'framework\games\tsukihime_global\game.cmake', 'NX_DATA_ROOT_EXE', 'apply_data_root_exe.py', $true),
    @('global', 'framework\games\tsukihime_global\game.cpp', '"../../../data/tsuki_global",', 'apply_data_root_exe.py', $true),
    @('global', 'tools\internal\regen_tsuki_global.ps1', 'AP_JT_FUERA', 'apply_jt_outside.py', $true),
    @('global', 'tools\internal\regen_tsuki_global.ps1', 'AP_RELR', 'apply_relr.py', $true),
    @('global', 'tools\internal\regen_tsuki_global.ps1', 'AP_JT_MOV', 'apply_jt_mov.py', $true)
)

# ---------------------------------------------------------------------------------------------- paths
$Sep = [IO.Path]::DirectorySeparatorChar
function InRoot([string]$rel) { Join-Path $Root ($rel -replace '[\\/]', $Sep) }   # path under -Root
function Under([string]$base, [string]$rel) { Join-Path $base ($rel -replace '[\\/]', $Sep) }

$V = $Editions[$Edition]
$Framework = InRoot 'framework'
$Tools = InRoot 'tools'
$Data = InRoot $V.Data
$DataOther = InRoot $Editions[$V.Other].Data
$Stage = Under $Data '_recomp'                      # intermediate files of this script (same disk as data\)
$StageStamp = Under $Stage 'CREATED_BY_recomp_tsukihime.txt'
$Build = Under $Framework "build\$($V.Game)"
$BuildOther = Under $Framework "build\$($Editions[$V.Other].Game)"
$Python = if ($OnLinux) { Under $Framework 'private\venv\bin\python' } else { Under $Framework 'private\venv\Scripts\python.exe' }
$Nso2bin = Under $Framework 'tools\nso2bin.py'
$Maxwell = Under $Framework 'tools\maxwell'
$Harness = Under $Framework 'private\build\spirv_harness\bin\Release\net10.0\spirv_harness.dll'
$ShadersPy = Under $Tools 'internal\tsuki_shaders_offline.py'
# AP_DROP_UNUSED: nsextract (tools\nsextract, C# + LibHac) is the only extractor: it reads exefs and romfs straight from the
# NSP/XCI, without the intermediate copy. nstool (the fallback until 2026-11-02) is gone.
$NsExtract = Under $Tools 'nsextract\bin\Release\net10.0\nsextract.dll'
$NsProj = Under $Tools 'nsextract\nsextract.csproj'
# AP_TOOLCHAIN_XWIN: Windows without VS Build Tools (install_requirements.ps1 -Toolchain xwin) leaves its tools in
# <root>\_prepare\toolchain.json: prebuilt nsextract / spirv_harness (exe, no .NET needed) and, for step 9, clang-cl + xwin
# + the prebuilt libraries. NX_TOOLCHAIN=vs ignores it (VS Build Tools + vcpkg, as before).
$Toolchain = $null
if (-not $OnLinux -and $env:NX_TOOLCHAIN -ne 'vs' -and (Test-Path -LiteralPath (InRoot '_prepare\toolchain.json'))) {
    try { $tcj = Get-Content -LiteralPath (InRoot '_prepare\toolchain.json') -Raw | ConvertFrom-Json; if ($tcj.mode -eq 'xwin') { $Toolchain = $tcj } } catch { }
}
# Fingerprint of the sources a prebuilt tool was built from (the same function is in tools\dev\build_prebuilt.ps1 and
# recomp_tsukihime.ps1): SHA-256 over "path SHA-256(text without CR)" of every source file ($SrcExt), bin\ and obj\ excluded.
$SrcExt = @('.cs', '.csproj', '.props', '.targets', '.patch', '.py')
function SourceStamp([string]$root, [string[]]$rels) {
    $sha = [Security.Cryptography.SHA256]::Create(); $enc = New-Object Text.UTF8Encoding($false); $lines = @()
    foreach ($rel in $rels) {
        $p = Join-Path $root $rel
        if (-not (Test-Path -LiteralPath $p)) { $lines += "missing $rel"; continue }
        $base = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\', '/')
        foreach ($f in @(Get-ChildItem -LiteralPath $p -Recurse -File -Force | Where-Object { $_.Extension -in $SrcExt -and $_.FullName.Substring($base.Length) -notmatch '[\\/](bin|obj)[\\/]' })) {
            $t = [IO.File]::ReadAllText($f.FullName, $enc).Replace("`r", '')
            $h = [BitConverter]::ToString($sha.ComputeHash($enc.GetBytes($t))).Replace('-', '')
            $lines += ($f.FullName.Substring($base.Length + 1).Replace('\', '/') + ' ' + $h)
        }
    }
    $sorted = [string[]]$lines; [Array]::Sort($sorted, [StringComparer]::Ordinal)
    return [BitConverter]::ToString($sha.ComputeHash($enc.GetBytes(($sorted -join "`n")))).Replace('-', '')
}
# A prebuilt exe only while the repo has the sources it was built from (after a git pull with other ones: the local build).
function PrebuiltTool([string]$k, [string[]]$rels) {
    $exe = "$($Toolchain.$k)"; $src = if ($Toolchain.PSObject.Properties["${k}_source"]) { "$($Toolchain."${k}_source")" } else { '' }
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    if ($src -and (SourceStamp $Root $rels) -ne $src) { Write-Host "   WARNING: the prebuilt $k is from other sources than this repo: the local build is used (tools\install_requirements.ps1 builds it)" -ForegroundColor Yellow; return $null }
    return $exe
}
if ($Toolchain) {
    $nxP = PrebuiltTool 'nsextract' @('tools/nsextract'); if ($nxP) { $NsExtract = $nxP }
    $nxP = PrebuiltTool 'harness' @('framework/tools/maxwell/spirv_harness', 'framework/tools/maxwell/apply_patch.py'); if ($nxP) { $Harness = $nxP }
}
# A .NET tool as a command: the prebuilt exe alone, or dotnet + the dll built here.
function ToolCmd([string]$p) { if ($p -like '*.exe') { return , @($p) } else { return , @('dotnet', $p) } }
# AP_XWIN_RUNTIME: the game and the vcpkg DLLs use the Visual C++ runtime (/MD). VS Build Tools installs it in Windows; the
# xwin flow does not, so the copy of the prebuilt pack (redist\) goes next to the exe (app-local, as Microsoft allows).
function CopyVcRuntime([string]$dir) {
    $rd = if ($Toolchain.PSObject.Properties['redist']) { "$($Toolchain.redist)" } else { '' }
    if ($rd -and (Test-Path -LiteralPath (Join-Path $rd 'msvcp140.dll'))) {
        $n = 0
        foreach ($f in @(Get-ChildItem -LiteralPath $rd -Filter '*.dll' -File)) {
            $d = Join-Path $dir $f.Name
            $same = $false
            if (Test-Path -LiteralPath $d) { $g = Get-Item -LiteralPath $d; $same = ($g.Length -eq $f.Length -and $g.LastWriteTimeUtc -eq $f.LastWriteTimeUtc) }
            if (-not $same) { Copy-Item -LiteralPath $f.FullName -Destination $d -Force; $n++ }
        }
        Info "Visual C++ runtime next to the exe ($rd): $n DLL(s) copied"
    } elseif (-not (Test-Path -LiteralPath (Join-Path $env:SystemRoot 'System32\msvcp140.dll'))) {
        Warn 'the game needs the Visual C++ runtime (msvcp140.dll, vcruntime140.dll) and neither Windows nor the prebuilt pack has it: install vc_redist.x64.exe from Microsoft, or run tools\install_requirements.ps1 -From 1 with a pack made by the current tools\dev\build_prebuilt.ps1'
    }
}
# Builds nsextract if the dll is missing or older than its sources (needs dotnet, and network the first time for the
# LibHac package; tools\install_requirements.ps1 step 7 already builds it).
function EnsureNsExtract {
    if ($NsExtract -like '*.exe') { Info "nsextract: $NsExtract (prebuilt)"; return }   # AP_TOOLCHAIN_XWIN
    if (-not (Test-Path -LiteralPath $NsProj)) { Fail "$NsProj is missing (it comes with the repo)" }
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Fail 'dotnet (.NET SDK 10) not found: it builds and runs tools\nsextract (tools\install_requirements.ps1 installs it)' }
    $src = @(Get-ChildItem -LiteralPath (Split-Path -Parent $NsProj) -File | Where-Object { $_.Extension -in '.cs', '.csproj' })
    $newest = ($src | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Test-Path -LiteralPath $NsExtract) -and (Get-Item -LiteralPath $NsExtract).LastWriteTimeUtc -ge $newest) { Info 'nsextract already built'; return }
    Info 'building nsextract (tools\nsextract)'
    RunVisible 'dotnet' @('build', $NsProj, '-c', 'Release', '--nologo', '-v', 'q', '-p:NuGetAudit=false') 'dotnet build nsextract'
    if (-not (Test-Path -LiteralPath $NsExtract)) { Fail "the build finished but $NsExtract does not appear" }
}
$Regen = Under $Tools "internal\$($V.Regen)"
$UpdatesIn = InRoot 'updates'                       # the .nsp the user drops (both editions)
$UpdData = Under $Data 'updates'                    # their exefs (main) and processing data
$ModsOut = InRoot "mods\$Edition"                   # romfs layers the exe reads (by hand, or from updates\)
$RomfsIndex = Under $Data 'romfs_index.bin'
$ModsPy = Under $Tools 'internal\mods_nsp.py'
$Create = Under $Tools 'internal\create_tsukihime_global.ps1'
# Build environment: the personal dev.local.ps1 (ignored, not in a fresh clone) or, if there is none, the
# portable dev_env.ps1 that is in the repo (tools\).
function SpirvVal { if (-not $env:VULKAN_SDK) { return '' }; if ($OnLinux) { Under $env:VULKAN_SDK 'bin\spirv-val' } else { Under $env:VULKAN_SDK 'Bin\spirv-val.exe' } }
$DevLocal = @((Under $Framework 'dev.local.ps1'), (Under $Tools 'dev.local.ps1'), (Under $Tools 'dev_env.ps1')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

$Summary = [ordered]@{}
function Step([int]$n, [string]$name) { Write-Host ''; Write-Host "== step $n`: $name" -ForegroundColor Cyan }
function Info([string]$t) { Write-Host "   $t" }
function Warn([string]$t) { Write-Host "   WARNING: $t" -ForegroundColor Yellow }
function Fail([string]$t) { throw $t }

# External program; captures the output without printing it.
function RunCapture([string]$exe, [string[]]$arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    return @{ Code = $code; Output = $out }
}
# AP_DROP_UNUSED: function Nstool removed (nstool is gone)
# External program with its output visible (recomp, harness, cmake).
function RunVisible([string]$exe, [string[]]$arguments, [string]$what) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (code $LASTEXITCODE)" }
}

# nsextract for the extraction (step 2): runs separately and every -ProgressSeconds reports how much it has written (I/O
# counter of the process; if it cannot be read, the size of the destination folder). Its output goes to two temporary
# files in _recomp\ that are read only to look for ERROR lines and deleted right away (they are not shown).
function Quote([string]$a) { if ($a -match '[\s"]' -or $a -eq '') { '"' + $a + '"' } else { $a } }
function BytesWritten([int]$id, [string]$dest) {
    if ($OnLinux) {
        try { $l = @(Get-Content -LiteralPath "/proc/$id/io" -ErrorAction Stop | Where-Object { $_ -like 'write_bytes:*' }); if ($l.Count) { return [int64](($l[0] -split ':')[1].Trim()) } } catch { }
    }
    try {
        $w = (Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction Stop).WriteTransferCount
        if ($null -ne $w) { return [int64]$w }
    } catch { }
    if (Test-Path -LiteralPath $dest) {
        $s = (Get-ChildItem -LiteralPath $dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        if ($s) { return [int64]$s }
    }
    return [int64]0
}
function ExtractLong([string[]]$arguments, [string]$what, [string]$dest, [int64]$total) {   # AP_DROP_UNUSED: was NstoolLong
    $id = [guid]::NewGuid().ToString('N')
    $fo = Under $Stage "nsextract_$id.out"; $fe = Under $Stage "nsextract_$id.err"
    $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN: the prebuilt exe, or dotnet + the dll
    $argStr = ((@($nc | Select-Object -Skip 1) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $dotnet = if ($nc[0] -eq 'dotnet') { (Get-Command dotnet -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source } else { $nc[0] }
    $p = Start-Process -FilePath $dotnet -ArgumentList $argStr -NoNewWindow -PassThru -RedirectStandardOutput $fo -RedirectStandardError $fe
    $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
    $t0 = Get-Date; $before = [int64]0; $tBefore = $t0
    while (-not $p.WaitForExit($ProgressSeconds * 1000)) {
        $now = Get-Date; $b = BytesWritten $p.Id $dest
        $speed = ($b - $before) / [Math]::Max(1, ($now - $tBefore).TotalSeconds)
        $pct = if ($total -gt 0) { ' ({0:N0} %)' -f [Math]::Min(100.0, 100.0 * $b / $total) } else { '' }
        $left = if ($speed -gt 0 -and $total -gt $b) { ', ~{0:N0} min left' -f (($total - $b) / $speed / 60) } else { '' }
        Info ('... {0}: {1:N1} of ~{2:N1} GB{3}, {4:N0} MB/s{5} [{6:mm\:ss}]' -f $what, ($b / 1GB), ($total / 1GB), $pct, ($speed / 1MB), $left, ($now - $t0))
        $before = $b; $tBefore = $now
    }
    $p.WaitForExit()
    $code = $p.ExitCode
    $lines = @()
    foreach ($f in $fo, $fe) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f); Remove-Item -LiteralPath $f -Force } }
    if ($code -ne 0) {
        $err = @($lines | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nsextract failed to $what (code $code): $($err -join ' | ')"
    }
    Info ('{0}: done in {1:mm\:ss}' -f $what, ((Get-Date) - $t0))
}

function Sha256([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash }
function ReadText([string]$p) { [IO.File]::ReadAllText($p) }
function U32($b, [int]$o) { [BitConverter]::ToUInt32($b, $o) }
function U64($b, [int]$o) { [BitConverter]::ToUInt64($b, $o) }

function ProgramIdOf([string]$npdm) {   # same as create_tsukihime_global.ps1
    $b = [IO.File]::ReadAllBytes($npdm)
    if ([Text.Encoding]::ASCII.GetString($b, 0, 4) -ne 'META') { Fail "$npdm is not an NPDM (no META)" }
    $aci = [int](U32 $b 0x70)
    if ([Text.Encoding]::ASCII.GetString($b, $aci, 4) -ne 'ACI0') { Fail "$npdm without ACI0" }
    return (U64 $b ($aci + 0x10))
}
function BuildIdOf([string]$nso) {
    $b = [IO.File]::ReadAllBytes($nso)
    if ([Text.Encoding]::ASCII.GetString($b, 0, 4) -ne 'NSO0') { Fail "$nso is not an NSO" }
    return (($b[0x40..0x53] | ForEach-Object { $_.ToString('x2') }) -join '')
}
function Hex16([uint64]$value) { '{0:X16}' -f $value }

function FreeOn([string]$p) {
    $full = [IO.Path]::GetFullPath($p)
    $d = [IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $full.StartsWith($_.Name, [StringComparison]::OrdinalIgnoreCase) } |
         Sort-Object { $_.Name.Length } -Descending | Select-Object -First 1
    if (-not $d) { Fail "cannot find the drive of $p" }
    return [int64]$d.AvailableFreeSpace
}
function GB([int64]$n) { '{0:N1} GB' -f ($n / 1GB) }

function NewStage {
    if (Test-Path -LiteralPath $Stage) {
        if (-not (Test-Path -LiteralPath $StageStamp)) { Fail "$Stage exists and was not created by this script: check it by hand" }
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Stage | Out-Null
    [IO.File]::WriteAllText($StageStamp, "Intermediate files of recomp_tsukihime.ps1; safe to delete.`r`n")
}
function RemoveStage { if ((Test-Path -LiteralPath $Stage) -and (Test-Path -LiteralPath $StageStamp)) { Remove-Item -LiteralPath $Stage -Recurse -Force } }

# Sets aside what is going to be replaced (kept as *.old); refuses if there is already a .old (overwrites nothing).
function SetAside([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $a = "$p.old"
    if (Test-Path -LiteralPath $a) { Fail "$a already exists (from a previous run): delete or rename it by hand" }
    Move-Item -LiteralPath $p -Destination $a
    return $a
}

function CheckMarkers([bool]$afterStructure) {
    $missing = @()
    foreach ($m in $Markers) {
        if ($m[0] -ne 'both' -and $m[0] -ne $Edition) { continue }
        if ($m[4] -and -not $afterStructure) { continue }
        if (-not $m[4] -and $afterStructure) { continue }
        $p = InRoot $m[1]
        if (-not (Test-Path -LiteralPath $p)) { $missing += "  $($m[1]) does not exist ($($m[3]))"; continue }
        if (-not (ReadText $p).Contains($m[2])) { $missing += "  $($m[1]) without '$($m[2])': missing $($m[3])" }
    }
    if ($missing.Count) { Fail ("patches missing in the framework (this script does not apply them):`n" + ($missing -join "`n")) }
    $n = @($Markers | Where-Object { ($_[0] -eq 'both' -or $_[0] -eq $Edition) -and ($_[4] -eq $afterStructure) }).Count
    Info "markers OK ($n)"
}

function GenComplete {
    foreach ($m in 'main', 'sdk') {
        foreach ($f in 'funcs.h', 'table.cpp', 'report.txt') { if (-not (Test-Path -LiteralPath (Under $Data "gen\$m\$f"))) { return $false } }
        if (-not (Get-ChildItem -LiteralPath (Under $Data "gen\$m") -Filter 'code_*.cpp' | Select-Object -First 1)) { return $false }
    }
    return $true
}
function FunctionCount([string]$m) {
    $r = Under $Data "gen\$m\report.txt"
    if (-not (Test-Path -LiteralPath $r)) { return '?' }
    $l = (Get-Content -LiteralPath $r -TotalCount 1)
    if ($l -match '^functions (\d+)') { return [int]$Matches[1] } else { return '?' }
}

# AP_DROP_UNUSED: ReadCnmt removed (it read the CNMT that nstool extracted)

# ---------------------------------------------------------------------------------------------- steps
$script:Ident = $null
$script:Candidates = @()

# Container by its header: NSP = 'PFS0' at 0; XCI = 'HEAD' at 0x100. The extension and the edition do not matter.
function ContainerType([string]$p) {
    $fs = [IO.File]::OpenRead($p)
    try {
        $b = New-Object byte[] 0x104
        $n = $fs.Read($b, 0, $b.Length)
    } finally { $fs.Dispose() }
    if ($n -ge 4 -and [Text.Encoding]::ASCII.GetString($b, 0, 4) -eq 'PFS0') { return 'nsp' }
    if ($n -ge 0x104 -and [Text.Encoding]::ASCII.GetString($b, 0x100, 4) -eq 'HEAD') { return 'xci' }
    return $null
}

# Inputs to try: -InputFile, or the .xci/.nsp in the ROOT (that level only). If there are several they are tried in
# order and the one of the requested edition is kept (step 1 compares the program ID); first the usual extension of
# the edition (global: .nsp, japan: .xci) to avoid reading too much, but any of them works for either.
function FindInputs {
    if ($InputFile) {
        $e = [IO.Path]::GetFullPath($InputFile)
        if (-not (Test-Path -LiteralPath $e -PathType Leaf)) { Fail "$e does not exist" }
        if (-not (ContainerType $e)) { Fail "$e is neither an NSP (PFS0) nor an XCI (HEAD)" }
        return , $e
    }
    $pref = if ($Edition -eq 'global') { '.nsp' } else { '.xci' }
    $all = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } |
             Sort-Object @{ Expression = { if ($_.Extension -eq $pref) { 0 } else { 1 } } }, Name)
    $ok = @()
    foreach ($f in $all) {
        if (ContainerType $f.FullName) { $ok += $f.FullName } else { Warn "$($f.Name): neither NSP (PFS0) nor XCI (HEAD): ignored" }
    }
    if (-not $ok.Count) { Fail "there is no .xci or .nsp in $Root (put it there, or pass -InputFile <path>)" }
    return , $ok
}

# The keys do not leave data\: only their path is passed to nsextract and mods_nsp.py.
function CheckKeys {
    $k = [IO.Path]::GetFullPath($Keys)
    $allowed = [IO.Path]::GetFullPath((InRoot 'data')) + $Sep
    if (-not $k.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) {
        Fail "the keys must be in $allowed (data\ of the root), not in $k"
    }
    if (-not (Test-Path -LiteralPath $k -PathType Leaf)) { Fail "$k does not exist (copy your keys there)" }
    $script:Keys = $k
}

function LoadDevLocal { Push-Location; try { . $DevLocal } finally { Pop-Location } }

function Step0 {
    if (-not (Test-Path -LiteralPath $Framework)) { Fail "$Framework does not exist" }
    # Build environment (dev.local.ps1 or tools\dev_env.ps1): loaded only when needed, that is, if cmake/clang-cl
    # are missing from the PATH or if a build is going to happen (step 9). For a build it is always loaded: the VS
    # DevShell overwrites VCPKG_ROOT with its internal copy and without the environment clang-cl fails with
    # "'algorithm' file not found" even if it is in the PATH; loading it again is harmless. The PATH and the variables
    # stay in this process (your console does not change).
    $toolsMissing = -not (Get-Command cmake -ErrorAction SilentlyContinue) -or -not (Get-Command clang-cl -ErrorAction SilentlyContinue)
    $builds = ($From -le 9 -and $To -ge 9)
    if ($toolsMissing -or $builds -or $Toolchain) {   # AP_TOOLCHAIN_XWIN: in that mode spirv-val and the tools come from dev_env.ps1
        if ($DevLocal) {
            Info "loading $DevLocal"
            LoadDevLocal
            if ($builds -and $OnLinux -and (-not $env:NX_XWIN -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment NX_XWIN or VCPKG_ROOT are still empty: step 9 may fail' }
            elseif ($builds -and -not $OnLinux -and $Toolchain -and -not $env:NX_XWIN) { Warn 'after loading the environment NX_XWIN is still empty (_prepare\toolchain.json): step 9 may fail' }   # AP_TOOLCHAIN_XWIN
            elseif ($builds -and -not $OnLinux -and -not $Toolchain -and (-not $env:INCLUDE -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment INCLUDE or VCPKG_ROOT are still empty: step 9 may fail' }
        } elseif ($toolsMissing) {
            Fail "no cmake/clang-cl in the PATH and no build environment (dev.local.ps1 or tools\dev_env.ps1; searched in $Framework and $Tools)"
        } else {
            Warn "cannot find dev.local.ps1 or tools\dev_env.ps1 (in $Framework or $Tools): step 9 may fail"
        }
    }
    $missing = @()
    foreach ($p in @($Python, $Nso2bin, $Maxwell, (Under $Maxwell 'containers.py'), (Under $Maxwell 'exefs_shaders.py'), $ShadersPy, $Regen)) {
        if (-not (Test-Path -LiteralPath $p)) { $missing += $p }
    }
    if ($Edition -eq 'global' -and -not (Test-Path -LiteralPath $Create)) { $missing += $Create }
    if ($From -le 2 -and $To -ge 1 -and $NsExtract -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\nsextract)' }   # AP_DROP_UNUSED, AP_TOOLCHAIN_XWIN
    if ($To -ge 8) {
        if (-not (Test-Path -LiteralPath $Harness)) { $missing += "$Harness (dotnet build tools/maxwell/spirv_harness/SpirvHarness.csproj -c Release -m:2)" }
        if ($Harness -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10)' }   # AP_TOOLCHAIN_XWIN
        if (-not (SpirvVal) -or -not (Test-Path -LiteralPath (SpirvVal))) { $missing += 'spirv-val (VULKAN_SDK\Bin)' }
    }
    if ($To -ge 9 -and -not (Get-Command cmake -ErrorAction SilentlyContinue)) { $missing += 'cmake' }
    if ($missing.Count) { Fail ("missing tools:`n  " + ($missing -join "`n  ")) }
    $r = RunCapture $Python @('-c', 'import lz4.block')
    if ($r.Code -ne 0) { Fail "the project python does not have lz4 (nso2bin.py needs it)" }
    Info "tools OK (python, nso2bin, containers/exefs_shaders, harness, cmake)"

    CheckMarkers $false

    if ($From -le 2 -and $To -ge 1 -and (ExtractedOk) -and -not $Replace) {
        Info 'exefs and romfs already extracted: the .xci/.nsp and the keys are not needed (-Replace to extract again)'
    } elseif ($From -le 2 -and $To -ge 1) {
        $script:Candidates = @(FindInputs)
        EnsureNsExtract
        CheckKeys
        foreach ($c in $script:Candidates) { Info "input $c ($('{0:N0}' -f (Get-Item -LiteralPath $c).Length) bytes, $(ContainerType $c))" }
        Info 'keys present (not read)'
    }
}

# AP_DROP_UNUSED: ExtractFile, PartitionSize and FromContainer removed (nstool)
$script:WrongEdition = $false
function Step1 {
    if ((ExtractedOk) -and -not $Replace) {
        Info 'already extracted: the input is not read again (step 3 verifies program ID and build ID of the exefs; -Replace to extract again)'
        return
    }
    $n = $script:Candidates.Count
    for ($i = 0; $i -lt $n; $i++) {
        $script:InputFile = $script:Candidates[$i]
        $script:WrongEdition = $false
        if ($n -gt 1) { Info "trying $($script:InputFile)" }
        try { IdentifyInput; return }
        catch {
            if (-not $script:WrongEdition -or $i -eq $n - 1) { throw }
            Warn "$($_.Exception.Message); trying the next one"
        }
    }
}

# AP_DROP_UNUSED: MetasOfPartition removed (nstool --fstree)
function IdentifyInput {
    NewStage
    # nsextract reports the program ID of the base application; the NCA is not extracted.
    $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN
    $r = RunCapture $nc[0] (@($nc | Select-Object -Skip 1) + @('info', '--keys', $Keys, '--input', $InputFile))
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nsextract failed to read $(Split-Path -Leaf $InputFile) (code $($r.Code)): $($err -join ' | ')"
    }
    $kv = @{}
    foreach ($l in $r.Output) { if ($l -match '^([A-Z]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2].Trim() } }
    if (-not $kv.ContainsKey('PROGRAMID')) { Fail 'nsextract did not report a PROGRAMID' }
    $progId = [Convert]::ToUInt64($kv['PROGRAMID'], 16)
    if ($progId -ne $V.ProgramId) {
        $other = ($Editions.GetEnumerator() | Where-Object { $_.Value.ProgramId -eq $progId } | Select-Object -First 1)
        $extra = if ($other) { " (it is the $($other.Key) edition)" } else { '' }
        $script:WrongEdition = $true
        Fail "$(Split-Path -Leaf $InputFile): program ID $(Hex16 $progId)$extra, not $(Hex16 $V.ProgramId) of the $Edition edition"
    }
    $script:Ident = @{ Direct = $true; Size = (Get-Item -LiteralPath $InputFile).Length; Nca = ''; Path = '' }
    Info "program ID $(Hex16 $progId); exefs and romfs will be read straight from the input (nsextract)"
    $Summary['Input'] = "$InputFile (program ID $(Hex16 $progId))"
}

function ExtractedOk {
    $main = Under $Data 'exefs\main'; $npdm = Under $Data 'exefs\main.npdm'
    $romfs = Under $Data 'extract\romfs'
    return ((Test-Path -LiteralPath $main) -and (Test-Path -LiteralPath $npdm) -and (Test-Path -LiteralPath (Under $romfs 'shader')))
}

function Step2 {
    $exefs = Under $Data 'exefs'; $romfs = Under $Data 'extract\romfs'
    if ((ExtractedOk) -and -not $Replace) {
        if ((ProgramIdOf (Under $exefs 'main.npdm')) -ne $V.ProgramId) { Fail "$exefs is from another program ID: check it (or -Replace)" }
        $n = @(Get-ChildItem -LiteralPath $romfs -Recurse -File).Count
        Info "already extracted: $exefs and $romfs ($n files); -Replace to extract again"
        if ($n -ne $V.RomfsFiles) { Warn "romfs with $n files; today the $Edition edition has $($V.RomfsFiles)" }
        RemoveStage
        if (-not (Test-Path -LiteralPath $RomfsIndex)) {
            $dumps = @(); try { $dumps = @(FindInputs); CheckKeys } catch { $dumps = @() }   # no dump or no keys: later
            if ($dumps.Count) { TryRomfsIndex $dumps }
        }
        return
    }
    if ((Test-Path -LiteralPath $exefs) -and -not $Replace) { Fail "$exefs exists but the extraction is incomplete: check it or use -Replace" }
    if ((Test-Path -LiteralPath $romfs) -and -not $Replace) { Fail "$romfs exists but the extraction is incomplete: check it or use -Replace" }
    if (-not $script:Ident) { Fail 'step 1 (identify) is missing' }
    foreach ($p in @($exefs, $romfs)) {   # before extracting ~23 GB: what SetAside would reject at the end
        if ((Test-Path -LiteralPath $p) -and (Test-Path -LiteralPath "$p.old")) { Fail "$p.old already exists (from a previous run): delete or rename it by hand" }
    }
    # exefs and romfs go straight from the input to _recomp\: only their own size is written (no copy of the NCA).
    $needed = [int64]$script:Ident.Size + 2GB    # upper bound: romfs + exefs are smaller than the input
    $free = FreeOn $Data
    if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
    Info "space: $(GB $free) free, ~$(GB $needed) needed at most"
    Info 'extracting exefs and romfs straight from the input (nsextract)'
    ExtractLong @('extract', '--keys', $Keys, '--input', $InputFile, '--program-id', (Hex16 $V.ProgramId),
                  '--exefs', (Under $Stage 'exefs'), '--romfs', (Under $Stage 'romfs')) 'extract exefs and romfs' (Under $Stage 'romfs') ([int64]$script:Ident.Size)
    foreach ($f in 'main', 'main.npdm', 'sdk') { if (-not (Test-Path -LiteralPath (Under $Stage "exefs\$f"))) { Fail "exefs without $f" } }
    if (-not (Test-Path -LiteralPath (Under $Stage 'romfs\shader'))) { Fail 'romfs without the shader folder' }
    if ((ProgramIdOf (Under $Stage 'exefs\main.npdm')) -ne $V.ProgramId) { Fail 'the extracted main.npdm is from another program ID' }
    $n = @(Get-ChildItem -LiteralPath (Under $Stage 'romfs') -Recurse -File).Count
    # Move into place (same disk: rename). What was there is kept as *.old.
    $previous = @()
    try {
        $a = SetAside $exefs; if ($a) { $previous += , @($exefs, $a) }
        $a = SetAside $romfs; if ($a) { $previous += , @($romfs, $a) }
        New-Item -ItemType Directory -Path (Under $Data 'extract') -Force | Out-Null
        Move-Item -LiteralPath (Under $Stage 'exefs') -Destination $exefs
        Move-Item -LiteralPath (Under $Stage 'romfs') -Destination $romfs
    } catch {
        foreach ($p in $previous) { if (-not (Test-Path -LiteralPath $p[0])) { Move-Item -LiteralPath $p[1] -Destination $p[0] } }
        throw
    }
    RemoveStage
    Info "extracted: $exefs and $romfs ($n files)"
    if ($n -ne $V.RomfsFiles) { Warn "romfs with $n files; today the $Edition edition has $($V.RomfsFiles)" }
    TryRomfsIndex @($InputFile)
    foreach ($p in $previous) { Warn "the previous one was kept in $($p[1]) (delete it by hand whenever you want)" }
}

function Step3 {
    $main = Under $Data 'exefs\main'; $npdm = Under $Data 'exefs\main.npdm'
    if (-not (Test-Path -LiteralPath $main) -or -not (Test-Path -LiteralPath $npdm)) { Fail "there is no exefs in $Data (step 2)" }
    $pid_ = ProgramIdOf $npdm
    if ($pid_ -ne $V.ProgramId) { Fail "main.npdm is from program ID $(Hex16 $pid_), not $(Hex16 $V.ProgramId)" }
    $bid = BuildIdOf $main
    if ($bid -ne $V.BuildId) {
        $msg = "build ID of main $bid, not $($V.BuildId): the recompiler patches and the kInfo offsets (game.cpp) are measured for that binary"
        if ($ForceBuildId) { Warn $msg } else { Fail "$msg (-ForceBuildId to continue anyway)" }
    }
    Info "main.npdm $(Hex16 $pid_); build ID $bid OK"
    $Summary['Build ID'] = $bid
    StepUpdates
}

# ---------------------------------------------------------------------------------------------- updates
$script:UpdateMain = $null     # main NSO of the updates (path), $null = the base one
$script:UpdateMainFrom = ''

# Identity of a processed .nsp: name, size, date, and the version of mods_nsp.py (a new one processes them again).
function ModStamp([IO.FileInfo]$f) { "$($f.Name)|$($f.Length)|$($f.LastWriteTimeUtc.Ticks)|$((Get-Item -LiteralPath $ModsPy).LastWriteTimeUtc.Ticks)" }

# romfs_index.bin: made once from the dump (the .xci/.nsp of the root, or -InputFile).
function EnsureRomfsIndex {
    if (Test-Path -LiteralPath $RomfsIndex) { return }
    Info 'romfs_index.bin is missing: it is made once from your dump (afterwards the dump is not needed for updates)'
    $cands = @(FindInputs)
    foreach ($c in $cands) {
        Info "index from $c"
        & $Python $ModsPy index --keys $Keys --input $c --program-id (Hex16 $V.ProgramId) --base-romfs (Under $Data 'extract\romfs') --out $RomfsIndex
        if ($LASTEXITCODE -eq 0) { return }
        if ($LASTEXITCODE -ne 3) { Fail "mods_nsp.py index failed (code $LASTEXITCODE)" }
    }
    Fail "none of the .xci/.nsp of $Root is the $Edition edition: romfs_index.bin cannot be made (put your dump there once)"
}

# Step 2: while the dump is at hand the index is made, so a later update does not need the dump. It never fails the
# step (EnsureRomfsIndex makes it later if this did not); an existing index is kept; a dump of the other edition
# (mods_nsp.py code 3) is skipped.
function TryRomfsIndex([string[]]$dumps) {
    if (Test-Path -LiteralPath $RomfsIndex) { return }
    $r = RunCapture $Python @('-c', 'import Crypto, lz4.block')
    if ($r.Code -ne 0) { Warn 'romfs_index.bin not made (the project python lacks pycryptodome): an update will need the dump once'; return }
    foreach ($d in $dumps) {
        Info "making romfs_index.bin from $(Split-Path -Leaf $d) (afterwards updates do not need the dump)"
        $t0 = Get-Date
        & $Python $ModsPy index --keys $Keys --input $d --program-id (Hex16 $V.ProgramId) --base-romfs (Under $Data 'extract\romfs') --out $RomfsIndex
        $code = $LASTEXITCODE
        if ($code -eq 0) { Info ('romfs_index.bin made in {0:mm\:ss}' -f ((Get-Date) - $t0)); return }
        if (Test-Path -LiteralPath $RomfsIndex) { Remove-Item -LiteralPath $RomfsIndex -Force }
        if ($code -ne 3) { Warn "romfs_index.bin not made (mods_nsp.py index, code $code): an update will need the dump once"; return }
    }
    Warn "none of the dumps is the $Edition edition: romfs_index.bin not made (an update will need the dump once)"
}

# The NSO main compiles from: the one of the updates or the base one. Its SHA-256 goes to analysis\main_source.txt
# (read by the exe to skip layers of another main) and gen\main_source.txt (step 6 regenerates when it changes).
function MainSource { if ($script:UpdateMain) { $script:UpdateMain } else { Under $Data 'exefs\main' } }
function GenStamp { $f = Under $Data 'gen\main_source.txt'; if (Test-Path -LiteralPath $f) { (ReadText $f).Trim() } else { '' } }

# Layers of mods\<edition> the exe will load with this main (same rule as apply_romfs_mods.py), for step 8.
function ActiveShaderDirs {
    $out = @()
    if (-not (Test-Path -LiteralPath $ModsOut -PathType Container)) { return , $out }
    $want = Sha256 (MainSource)
    foreach ($d in @(Get-ChildItem -LiteralPath $ModsOut -Directory | Sort-Object Name)) {
        if ($d.Name.StartsWith('_') -or $d.Name.StartsWith('.')) { continue }
        $req = Under $d.FullName 'requires_main.txt'
        if ((Test-Path -LiteralPath $req) -and (ReadText $req).Trim() -ne $want) { continue }
        $sh = Under $d.FullName 'romfs\shader'
        if (Test-Path -LiteralPath $sh -PathType Container) { $out += $sh }
    }
    return , $out
}

# A layer of mods\<edition> made from an update (from_update.txt); the others were copied by hand and are not touched.
function IsUpdateLayer([string]$d) { Test-Path -LiteralPath (Under $d 'from_update.txt') }

# An update is processed once (from the .nsp of updates\) and then lives in <data>\updates\<name> (exefs, mod.json)
# plus its layer mods\<edition>\<name>; the .nsp is no longer needed. The layer folder is the switch: _<name> disables
# it, deleting it removes the update (its data is deleted at the next run).
function StepUpdates {
    $nsps = @()
    if ($NoUpdates) { Info "-NoUpdates: base main; $UpdatesIn is not read (layers without their own main still load)" }
    elseif (Test-Path -LiteralPath $UpdatesIn -PathType Container) {
        $nsps = @(Get-ChildItem -LiteralPath $UpdatesIn -File -Filter '*.nsp' | Sort-Object Name)
        foreach ($f in @($nsps | Where-Object { $_.Name.StartsWith('_') })) { Info "updates\$($f.Name): skipped (starts with _)" }
        $nsps = @($nsps | Where-Object { -not $_.Name.StartsWith('_') })
    }
    if ($nsps.Count) {
        Info "updates: $($nsps.Count) .nsp in $UpdatesIn"
        $r = RunCapture $Python @('-c', 'import Crypto, lz4.block')
        if ($r.Code -ne 0) { Fail 'the project python does not have pycryptodome (tools\install_requirements.ps1 -From 6 installs tools\requirements.txt)' }
        CheckKeys
        New-Item -ItemType Directory -Path $UpdData -Force | Out-Null
        foreach ($f in $nsps) {
            $name = [IO.Path]::GetFileNameWithoutExtension($f.Name) -replace '[^A-Za-z0-9._()\[\] +-]', '_'
            $dst = Under $UpdData $name
            $layer = Under $ModsOut $name
            $src = Under $dst 'source.txt'
            $stamp = ModStamp $f
            if (IsUpdateLayer (Under $ModsOut "_$name")) { Info "$($f.Name): disabled (mods\$Edition\_$name): not processed"; continue }
            $done = (Test-Path -LiteralPath (Under $dst 'mod.json')) -and (Test-Path -LiteralPath $src) -and (ReadText $src).Trim() -eq $stamp -and (IsUpdateLayer $layer)
            if ($done) { Info "$($f.Name): already processed (you can delete the .nsp)"; continue }
            if ((Test-Path -LiteralPath $dst) -and -not (Test-Path -LiteralPath $src)) { Fail "$dst exists and was not made by this script: move it away" }
            if ((Test-Path -LiteralPath $layer) -and -not (IsUpdateLayer $layer)) { Fail "$layer exists and is not the romfs of an update (a mod copied by hand with the same name): rename one of them" }
            EnsureRomfsIndex
            $tmp = Under $UpdData "_new_$name"
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
            $free = FreeOn $UpdData
            if ($free -lt $f.Length + 1GB) { Fail "~$(GB ($f.Length + 1GB)) free are needed on the drive of $UpdData and there are $(GB $free)" }
            Info "$($f.Name): extracting what differs from the base (reads the whole patch)"
            $t0 = Get-Date
            & $Python $ModsPy mod --keys $Keys --nsp $f.FullName --program-id (Hex16 $V.ProgramId) --index $RomfsIndex `
                --base-romfs (Under $Data 'extract\romfs') --base-exefs (Under $Data 'exefs') --out $tmp
            $code = $LASTEXITCODE
            if ($code -ne 0 -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force }
            if ($code -eq 3) { Info "$($f.Name): it is not for the $Edition edition (skipped)"; continue }
            if ($code -ne 0) { Fail "mods_nsp.py failed with $($f.Name) (code $code)" }
            $j = (ReadText (Under $tmp 'mod.json')) | ConvertFrom-Json
            # Layer mods\<edition>\<name> (always, even without romfs changes: it is the switch of the update).
            if (Test-Path -LiteralPath $layer) { Remove-Item -LiteralPath $layer -Recurse -Force }
            New-Item -ItemType Directory -Path $layer -Force | Out-Null
            $romTmp = Under $tmp 'romfs'
            if (Test-Path -LiteralPath $romTmp) { Move-Item -LiteralPath $romTmp -Destination (Under $layer 'romfs') }   # same drive as the root: a rename
            else { New-Item -ItemType Directory -Path (Under $layer 'romfs') | Out-Null }
            [IO.File]::WriteAllText((Under $layer 'from_update.txt'), ("romfs of the update $($f.Name) ($Edition edition), made by recomp_tsukihime.ps1. " +
                "To disable the update rename this folder to _$name; to remove it delete the folder. Then recompile.`r`n"))
            if (@($j.exefs_different) -contains 'main') { [IO.File]::WriteAllText((Under $layer 'requires_main.txt'), (Sha256 (Under $tmp 'exefs\main')) + "`r`n") }
            [IO.File]::WriteAllText((Under $tmp 'source.txt'), $stamp + "`r`n")
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
            Move-Item -LiteralPath $tmp -Destination $dst
            Info ('{0}: processed in {1:mm\:ss} (the .nsp can be deleted now)' -f $f.Name, ((Get-Date) - $t0))
        }
    }
    # Active updates: the processed ones whose layer is in mods\<edition>. _<name> = disabled (kept); no layer = removed.
    $active = @()
    if (Test-Path -LiteralPath $UpdData) {
        foreach ($d in @(Get-ChildItem -LiteralPath $UpdData -Directory | Sort-Object Name)) {
            if ($d.Name.StartsWith('_new_')) { Remove-Item -LiteralPath $d.FullName -Recurse -Force; continue }   # a run that was cut
            if (-not ((Test-Path -LiteralPath (Under $d.FullName 'source.txt')) -and (Test-Path -LiteralPath (Under $d.FullName 'mod.json')))) { continue }
            if (IsUpdateLayer (Under $ModsOut $d.Name)) { $active += $d.Name; continue }
            if (IsUpdateLayer (Under $ModsOut "_$($d.Name)")) { Info "updates: $($d.Name) disabled (mods\$Edition\_$($d.Name))"; continue }
            Remove-Item -LiteralPath $d.FullName -Recurse -Force
            Info "updates: $($d.Name) removed (its folder mods\$Edition\$($d.Name) is gone)"
        }
    }
    if ($NoUpdates) { $active = @() }
    $mains = @()
    foreach ($name in $active) {
        $dst = Under $UpdData $name
        $j = (ReadText (Under $dst 'mod.json')) | ConvertFrom-Json
        $diff = @($j.exefs_different)
        $other = @($diff | Where-Object { $_ -ne 'main' })
        if ($other.Count) { Fail "$($j.nsp) changes $($other -join ', ') besides main: not supported (kInfo and the recompiler patches are measured for the base sdk)" }
        if ($diff -contains 'main') { $mains += [pscustomobject]@{ Sha = [string]$j.main_sha256; Nsp = [string]$j.nsp; Path = (Under $dst 'exefs\main') } }
        Info ("update {0} active: romfs changed {1}, new {2} (mods\{3}\{4}); main {5}" -f $j.nsp, $j.changed, $j.new, $Edition, $name, $(if ($diff -contains 'main') { 'own (recompiled)' } else { 'the base one' }))
        $removed = @($j.removed)
        if ($removed.Count) { Info "   $($removed.Count) base files are not in the update (they stay visible): $(($removed | Select-Object -First 3) -join ', ')" }
    }
    $distinct = @($mains | ForEach-Object { $_.Sha } | Sort-Object -Unique)
    if ($distinct.Count -gt 1) {
        Fail ("these active updates carry different main and cannot be combined (disable all but one: rename its folder in mods\$Edition to _<name>):`n  " +
              (($mains | ForEach-Object { "$($_.Nsp): main $($_.Sha.Substring(0, 16))" }) -join "`n  "))
    }
    $script:UpdateMain = if ($mains.Count) { $mains[0].Path } else { $null }
    $script:UpdateMainFrom = ($mains | ForEach-Object { $_.Nsp }) -join ', '
    $Summary['Updates'] = if ($active.Count) { "$($active -join ', '); main: $(if ($script:UpdateMain) { "of $($script:UpdateMainFrom)" } else { 'the base one' })" } else { 'none active' }
    $layers = @(); if (Test-Path -LiteralPath $ModsOut) { $layers = @(Get-ChildItem -LiteralPath $ModsOut -Directory | Where-Object { -not $_.Name.StartsWith('_') -and -not $_.Name.StartsWith('.') } | Sort-Object Name | ForEach-Object { $_.Name }) }
    $Summary['Mods'] = if ($layers.Count) { "mods\$Edition`: $($layers -join ', ')" } else { "none in mods\$Edition" }
    # Resuming after step 5 with another main would build an exe that does not match analysis\main.bin.
    if ($From -gt 5 -and (GenComplete)) {
        $have = GenStamp
        $want = Sha256 (MainSource)
        if (($have -and $have -ne $want) -or (-not $have -and $script:UpdateMain)) { Fail "main changes with the updates: resume from step 5 (-From 5) or lower" }
    }
}

function Step4 {
    if ($Edition -eq 'global') {
        Info 'create_tsukihime_global.ps1 (idempotent: creates only what is missing)'
        & $Create -Root $Root
    } else {
        foreach ($f in 'game.cpp', 'game.cmake') {
            if (-not (Test-Path -LiteralPath (Under $Framework "games\tsukihime\$f"))) { Fail "games\tsukihime\$f is missing" }
        }
        Info 'games\tsukihime present'
    }
    CheckMarkers $true
}

function Step5 {
    $exefs = Under $Data 'exefs'; $an = Under $Data 'analysis'
    New-Item -ItemType Directory -Path $an -Force | Out-Null
    NewStage
    foreach ($m in 'main', 'sdk') {
        $tmp = Under $Stage "$m.bin"; $dst = Under $an "$m.bin"
        $nso = if ($m -eq 'main') { MainSource } else { Under $exefs $m }
        if ($nso -ne (Under $exefs $m)) { Info "main from the updates ($script:UpdateMainFrom)" }
        $r = RunCapture $Python @($Nso2bin, $nso, $tmp)
        if ($r.Code -ne 0) { Fail "nso2bin $m failed: $($r.Output -join ' | ')" }
        $h = Sha256 $tmp
        if ((Test-Path -LiteralPath $dst) -and (Sha256 $dst) -eq $h) { Info "analysis\$m.bin already up to date ($($h.Substring(0, 16)))"; continue }
        if ($m -eq 'main' -and (Test-Path -LiteralPath "$dst.old")) {   # switching main (updates\): the .old is regenerable
            Remove-Item -LiteralPath "$dst.old" -Force; Info 'the previous analysis\main.bin.old was removed (it is regenerable)'
        }
        $a = SetAside $dst
        Move-Item -LiteralPath $tmp -Destination $dst
        Info "analysis\$m.bin written ($($h.Substring(0, 16)))"
        if ($a) { Warn "the previous one was kept in $a" }
    }
    [IO.File]::WriteAllText((Under $an 'main_source.txt'), (Sha256 (MainSource)) + "`r`n")   # read by the exe (apply_romfs_mods.py)
    RemoveStage
}

# AP_RECOMP_PARALLEL: main and sdk are independent: each one in its own process (the regen script with -Modules <module>),
# at the same time; the output of each one is shown when it finishes. NX_RECOMP_SERIAL=1: one after the other, as before.
function RegenParallel([string]$regen) {
    $host_ = (Get-Process -Id $PID).Path
    $runs = @()
    foreach ($m in 'main', 'sdk') {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $regen, '-Modules', $m, '-Root', $Root)
        if ($m -eq 'main' -and $script:UpdateMain) { $a += @('-MainNso', $script:UpdateMain) }
        $log = Join-Path ([IO.Path]::GetTempPath()) "tsuki_regen_$($m)_$([guid]::NewGuid().ToString('N')).log"
        $p = Start-Process -FilePath $host_ -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru `
             -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
        $runs += @{ M = $m; P = $p; Log = $log }
    }
    Info 'recompiling main and sdk at the same time (the output of each one is shown when it finishes)'
    $failed = @()
    foreach ($r in $runs) {
        $r.P.WaitForExit()
        $code = -1; try { $code = $r.P.ExitCode } catch { }
        if ($null -eq $code) { $code = -1 }
        $took = ''; try { $took = ' in {0:mm\:ss}' -f ($r.P.ExitTime - $r.P.StartTime) } catch { }
        Info "-- recomp $($r.M)$took$(if ($code -ne 0) { " FAILED (code $code)" })"
        foreach ($f in @($r.Log, "$($r.Log).err")) {
            if (Test-Path -LiteralPath $f) {
                foreach ($l in @(Get-Content -LiteralPath $f)) { if ($l -match '\S') { Info "   $l" } }
                Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
            }
        }
        if ($code -ne 0) { $failed += $r.M }
    }
    if ($failed.Count) { Fail "recompiling $($failed -join ' and ') failed (output above)" }
}

function Step6 {
    $gen = Under $Data 'gen'
    # gen\main_source.txt: SHA-256 of the NSO main was generated from. Without it (made before updates\ existed): the base one.
    $want = Sha256 (MainSource)
    $have = GenStamp
    $stale = ($have -and $have -ne $want) -or (-not $have -and $script:UpdateMain)
    if ((GenComplete) -and -not $Replace -and -not $stale) {
        if (-not $have) { [IO.File]::WriteAllText((Under $gen 'main_source.txt'), $want + "`r`n") }
        Info "gen\ already generated (main $(FunctionCount main) functions, sdk $(FunctionCount sdk)); -Replace to regenerate"
        return
    }
    if ($stale) {
        Info "main changed ($(if ($script:UpdateMain) { "the one of $script:UpdateMainFrom" } else { 'back to the base one' })): regenerating gen\"
        if (Test-Path -LiteralPath "$gen.old") { Remove-Item -LiteralPath "$gen.old" -Recurse -Force; Info 'the previous gen.old was removed (it is regenerable)' }
    }
    $regenToRun = $Regen
    $previous = SetAside $gen
    try {
        if ($env:NX_RECOMP_SERIAL) {
            $ra = @{ Modules = @('main', 'sdk'); Root = $Root }
            if ($script:UpdateMain) { $ra['MainNso'] = $script:UpdateMain }
            & $regenToRun @ra
        } else { RegenParallel $regenToRun }   # AP_RECOMP_PARALLEL: main and sdk at the same time
        if (-not (GenComplete)) { Fail "$($V.Regen) finished but gen\ is incomplete" }
        [IO.File]::WriteAllText((Under $gen 'main_source.txt'), $want + "`r`n")
    } catch {
        if (Test-Path -LiteralPath $gen) { Remove-Item -LiteralPath $gen -Recurse -Force }
        if ($previous) { Move-Item -LiteralPath $previous -Destination $gen; Warn 'gen\ restored to how it was' }
        throw
    }
    if ($regenToRun -ne $Regen) { RemoveStage }
    Info "gen\ generated: main $(FunctionCount main) functions, sdk $(FunctionCount sdk)"
    if ($previous) { Warn "the previous gen\ was kept in $previous (delete it by hand whenever you want)" }
}

function Step7 {
    $dst = Under $Data 'fonts\shared_0.ttf'
    if (Test-Path -LiteralPath $dst) {
        $h = Sha256 $dst
        Info "fonts\shared_0.ttf present ($($h.Substring(0, 16)))"
        if ($h -ne $FontSha256) { Warn 'it is not the same font used until today' }
        return
    }
    $src = $null
    $inRepo = Under $Root 'resources\fonts\shared_0.ttf'
    if ($Fonts) {
        $src = if (Test-Path -LiteralPath $Fonts -PathType Container) { Under $Fonts 'shared_0.ttf' } else { $Fonts }
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { Fail "$src does not exist" }
    } elseif (Test-Path -LiteralPath $inRepo -PathType Leaf) {
        $src = $inRepo
    } elseif (Test-Path -LiteralPath (Under $DataOther 'fonts\shared_0.ttf')) {
        $src = Under $DataOther 'fonts\shared_0.ttf'
    } else {
        Fail "fonts\shared_0.ttf is missing: it must be in $inRepo (it comes with the repo; Noto Sans JP, OFL) or be passed with -Fonts <file or folder>"
    }
    New-Item -ItemType Directory -Path (Under $Data 'fonts') -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination "$dst.tmp"
    if ((Sha256 "$dst.tmp") -ne (Sha256 $src)) { Remove-Item -LiteralPath "$dst.tmp"; Fail 'the copy of the font does not match' }
    Move-Item -LiteralPath "$dst.tmp" -Destination $dst
    Info "fonts\shared_0.ttf copied from $src"
    if ((Sha256 $dst) -ne $FontSha256) { Warn 'it is not the same font used until today' }
}

function CountShaders([string]$out) {
    $a = @('--maxwell', $Maxwell, '--romfs-shader', (Under $Data 'extract\romfs\shader'), '--main-bin', (Under $Data 'analysis\main.bin'), '--spirv', (Under $Data 'spirv'))
    foreach ($d in (ActiveShaderDirs)) { $a += @('--romfs-shader', $d) }   # shaders that mods\<edition> adds or changes
    if ($out) { $a += @('--out', $out) }
    $r = RunCapture $Python (@($ShadersPy) + $a)
    if ($r.Code -ne 0) { Fail "tsuki_shaders_offline.py failed: $(($r.Output | Select-Object -Last 3) -join ' | ')" }
    # Lines printed by tsuki_shaders_offline.py (its wording is read here: change both together).
    $l = @($r.Output | Where-Object { $_ -match '^SUMMARY ' })
    if ($l.Count -ne 1) { Fail 'tsuki_shaders_offline.py without summary' }
    $h = @{}
    foreach ($kv in ($l[0] -replace '^SUMMARY ', '') -split ' ') { $p = $kv -split '='; $h[$p[0]] = [int]$p[1] }
    $h['missing'] = @($r.Output | Where-Object { $_ -match '^without \.spv: ' } | ForEach-Object { ($_ -split ' ')[2] })
    return $h
}

function Step8 {
    if (-not (Test-Path -LiteralPath (Under $Data 'analysis\main.bin'))) { Fail 'analysis\main.bin is missing (step 5)' }
    $spirv = Under $Data 'spirv'
    $c = CountShaders ''
    Info "offline: $($c.total) blocks (romfs $($c.romfs), main $($c.main)); with .spv $($c.with_spv), without .spv $($c.without_spv)"
    $translated = 0
    if ($c.without_spv -gt 0) {
        NewStage
        $in = Under $Stage 'shaders\in'; $out = Under $Stage 'shaders\out'
        $c2 = CountShaders $in
        if ($c2.without_spv -ne $c.without_spv) { Fail 'the shader count changed between two runs' }
        Info "translating $($c.without_spv) with the harness"
        $hj = [Math]::Max(3, [Environment]::ProcessorCount - 1)   # AP_RECOMP_PARALLEL: was a fixed 3
        $hc = ToolCmd $Harness   # AP_TOOLCHAIN_XWIN: the prebuilt exe, or dotnet + the dll
        RunVisible $hc[0] (@($hc | Select-Object -Skip 1) + @('--batch', $in, '--out', $out, '--jobs', "$hj", '--spirv-val', (SpirvVal))) 'spirv_harness'
        $res = Under $out 'results.tsv'
        if (-not (Test-Path -LiteralPath $res)) { Fail 'the harness did not write results.tsv' }
        $lines = @(Get-Content -LiteralPath $res)
        $head = $lines[0]
        if (-not $head.StartsWith("hash`tgroup`tstage")) { Fail "results.tsv of the harness with another header: $head" }
        $newRows = @(); $spvs = @()
        foreach ($l in @($lines | Select-Object -Skip 1)) {
            if (-not $l) { continue }
            $f = $l -split "`t"
            if ($f[4] -like 'ok*' -and (Test-Path -LiteralPath (Under $out "$($f[0]).spv"))) { $newRows += $l; $spvs += $f[0] }
            else { Warn "shader $($f[0]): $($f[4]) $(if ($f.Count -gt 9) { $f[9] })" }
        }
        # New results.tsv (the rows already there, untouched; the new ones at the end) built before copying anything.
        New-Item -ItemType Directory -Path $spirv -Force | Out-Null
        $dstRes = Under $spirv 'results.tsv'
        if (Test-Path -LiteralPath $dstRes) {
            $text = ReadText $dstRes
            $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
            $have = @{}; foreach ($l in ($text -split "`r?`n")) { if ($l) { $have[($l -split "`t")[0]] = 1 } }
            $add = @($newRows | Where-Object { -not $have.ContainsKey(($_ -split "`t")[0]) })
            if (-not $text.EndsWith("`n") -and $text.Length) { $text += $nl }
            $newText = $text + (($add | ForEach-Object { $_ + $nl }) -join '')
        } else {
            $nl = "`r`n"; $add = $newRows
            $newText = (@($head) + $newRows | ForEach-Object { $_ + $nl }) -join ''
        }
        $copied = @()
        try {
            foreach ($h in $spvs) {
                $d = Under $spirv "$h.spv"
                if (Test-Path -LiteralPath $d) { continue }
                Copy-Item -LiteralPath (Under $out "$h.spv") -Destination $d; $copied += $d
            }
            if (Test-Path -LiteralPath $dstRes) {
                $bak = Under $spirv ('results.tsv.before-recomp-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
                Copy-Item -LiteralPath $dstRes -Destination $bak
                Info "backup of results.tsv: $bak"
            }
            [IO.File]::WriteAllText("$dstRes.tmp", $newText, (New-Object Text.UTF8Encoding($false)))
            Move-Item -LiteralPath "$dstRes.tmp" -Destination $dstRes -Force
        } catch {
            foreach ($d in $copied) { Remove-Item -LiteralPath $d -Force -ErrorAction SilentlyContinue }
            throw
        }
        $translated = $spvs.Count
        Info "added $($copied.Count) .spv and $($add.Count) rows to results.tsv"
        RemoveStage
        $c = CountShaders ''
    }
    $Summary['Shaders'] = "$($c.total) (romfs $($c.romfs) + main $($c.main)); translated now $translated; untranslated $($c.without_spv)"
    if ($c.without_spv -gt 0) { Warn ("without .spv: " + ($c.missing -join ', ')) }
}

# AP_RECOMP_PARALLEL: summary of what one build appended to .ninja_log (tools\dev\ninja_times.py); never fatal.
function BuildTimes([string]$log, [int64]$offset) {
    $nt = Under $Tools 'dev\ninja_times.py'
    if (-not (Test-Path -LiteralPath $nt) -or -not (Test-Path -LiteralPath $log)) { return }
    try { $r = RunCapture $Python @($nt, $log, '--offset', "$offset"); if ($r.Code -eq 0) { foreach ($l in $r.Output) { Info $l } } } catch { }
}

function CacheValue([string]$cache, [string]$k) {
    $l = Select-String -LiteralPath $cache -Pattern "^$k(:[A-Z]+)?=(.*)$" | Select-Object -First 1
    if ($l) { return $l.Matches[0].Groups[2].Value } else { return '' }
}

# AP_FFMPEG_PREBUILT: folder of the prebuilt FFmpeg that install_requirements.ps1 (step 5) leaves in _prepare\ffmpeg (the most
# recent one that has lib\avcodec.lib), with forward slashes for CMake; '' if there is none (CMake then uses the vcpkg one).
function FfmpegPrebuilt {
    $base = InRoot '_prepare\ffmpeg'
    if (-not (Test-Path -LiteralPath $base)) { return '' }
    $d = @(Get-ChildItem -LiteralPath $base -Directory | Where-Object { Test-Path -LiteralPath (Under $_.FullName 'lib\avcodec.lib') -PathType Leaf } | Sort-Object LastWriteTime -Descending)
    if (-not $d.Count) { return '' }
    return ($d[0].FullName -replace '\\', '/')
}

# Linux: the framework CMakeLists (WIN32 branch) includes "${NX_VULKAN_SDK}/Include" as in the Windows SDK; the
# Linux SDK has the folder in lowercase (x86_64/include) and Linux is case sensitive. Only the headers are needed (the
# vulkan-1.dll loader comes from the driver or Proton: no .lib is linked). View with an Include -> include link in
# _prepare/linux/vulkan-sdk; passed as -DNX_VULKAN_SDK (VULKAN_SDK and the CMakeLists are not touched).
function VulkanView {
    if (-not $env:VULKAN_SDK) { Fail 'VULKAN_SDK is empty (tools/dev_env.ps1; installed by tools/linux/install_requirements_linux.sh)' }
    $inc = Under $env:VULKAN_SDK 'include'
    if (-not (Test-Path -LiteralPath (Under $inc 'vulkan/vulkan.h') -PathType Leaf)) { Fail "$inc/vulkan/vulkan.h does not exist" }
    $view = InRoot '_prepare/linux/vulkan-sdk'
    $link = Under $view 'Include'
    $it = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($it) {
        if ($it.LinkType -ne 'SymbolicLink' -or $it.Target -ne $inc) { Fail "$link exists and is not a link to ${inc}: set it aside by hand" }
    } else {
        New-Item -ItemType Directory -Path $view -Force | Out-Null
        New-Item -ItemType SymbolicLink -Path $link -Target $inc | Out-Null
        Info "Vulkan headers: $link -> $inc"
    }
    return ($view -replace '\\', '/')
}

# HUD button icons by device (AP_HUD_GLYPHS, hud_glyphs.cpp): <data>\hud from the romfs and its mod layers (only hashes
# of the game's atlas) and from the icon packs in utils\. Never fatal: without them the game keeps its own icons.
# AP_RECOMP_PARALLEL: the HUD icons need only the romfs, the mod layers (final after step 3) and utils\: when the run goes
# on to step 9 they are built in the background from the first of steps 5-8 that runs, and joined at the start of step 9.
$script:HudJob = $null
$script:HudDone = $false
function StartHudBackground {
    if ($script:HudJob -or $script:HudDone -or $env:NX_RECOMP_SERIAL) { return }
    $py = Under $Tools 'hud\build_hud_assets.py'
    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $Python)) { return }
    $log = Join-Path ([IO.Path]::GetTempPath()) "tsuki_hud_$($Edition)_$([guid]::NewGuid().ToString('N')).log"
    try {
        $p = Start-Process -FilePath $Python -ArgumentList ((@($py, '--edition', $Edition, '--root', $Root) | ForEach-Object { Quote $_ }) -join ' ') `
             -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        $null = $p.Handle
        $script:HudJob = @{ P = $p; Log = $log }
        Info 'HUD icons by device: building in the background meanwhile (shown at the start of step 9, or at the end of this run)'   # AP_TOOLCHAIN_XWIN
    } catch { }
}

function HudAssets {
    if ($script:HudDone) { return }   # AP_RECOMP_PARALLEL: already done (in the background, joined before the build)
    $script:HudDone = $true
    $script = Under $Tools 'hud\build_hud_assets.py'
    if (-not (Test-Path -LiteralPath $script)) { Warn "$script is missing: HUD icons by device skipped"; return }
    if ($script:HudJob) {
        $j = $script:HudJob; $script:HudJob = $null
        if (-not $j.P.HasExited) { Info 'HUD icons by device: waiting for the background build...' }
        $j.P.WaitForExit()
        $code = -1; try { $code = $j.P.ExitCode } catch { }
        if ($null -eq $code) { $code = -1 }
        Info 'HUD icons by device (tools\hud\build_hud_assets.py, built in the background)'
        $lines = @()
        foreach ($f in @($j.Log, "$($j.Log).err")) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f); Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } }
        $r = @{ Code = $code; Output = $lines }
    } else {
        Info 'HUD icons by device (tools\hud\build_hud_assets.py)'
        $r = RunCapture $Python @($script, '--edition', $Edition, '--root', $Root)
    }
    foreach ($l in $r.Output) { Info $l }
    if ($r.Code -ne 0) { Warn "build_hud_assets.py failed (code $($r.Code)): the game keeps its own icons"; return }
    $Summary['HUD'] = Under $Data 'hud'
}

function Step9 {
    if ($script:HudJob) { HudAssets }   # AP_RECOMP_PARALLEL: the background HUD build ends before the compile starts
    $cache = Under $Build 'CMakeCache.txt'
    $gen = ((Under $Data 'gen') -replace '\\', '/')
    Push-Location $Framework
    try {
        if (Test-Path -LiteralPath $cache) {
            $g = (CacheValue $cache 'NX_GEN_DIR') -replace '\\', '/'
            if ($g -ne $gen) { Fail "$Build uses NX_GEN_DIR=$g, not $gen (in a test copy: delete that build\ of the copy, it is configured again)" }
            Info "$Build already configured"
            # AP_FFMPEG_PREBUILT: a build configured before this patch (or with another FFmpeg folder) changes only this variable
            $ffRoot = FfmpegPrebuilt
            if ($ffRoot -and (CacheValue $cache 'NX_FFMPEG_ROOT') -ne $ffRoot) {
                Info "NX_FFMPEG_ROOT -> $ffRoot (reconfiguring; the rest of the cache stays the same)"
                RunVisible 'cmake' @('-B', $Build, "-DNX_FFMPEG_ROOT=$ffRoot") 'cmake (reconfigure NX_FFMPEG_ROOT)'
            }
            if ($OnLinux) {
                $vk = VulkanView
                if ((CacheValue $cache 'NX_VULKAN_SDK') -ne $vk) {
                    Info "NX_VULKAN_SDK -> $vk (reconfiguring; the rest of the cache stays the same)"
                    RunVisible 'cmake' @('-B', $Build, "-DNX_VULKAN_SDK=$vk") 'cmake (reconfigure NX_VULKAN_SDK)'
                }
            }
        } else {
            # Generator and toolchain: from the build of the other edition (read only).
            $cands = @((Under $BuildOther 'CMakeCache.txt'))
            $other = $cands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            $a = @('-B', $Build, "-DNX_GAME=$($V.Game)", "-DNX_GEN_DIR=$gen")
            if ($other) {
                Info "parameters from $other"
                $cacheKeys = @('CMAKE_GENERATOR', 'CMAKE_TOOLCHAIN_FILE', 'CMAKE_BUILD_TYPE', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER', 'VCPKG_TARGET_TRIPLET', 'NX_OPUS_ROOT')
                if ($OnLinux) { $cacheKeys += @('VCPKG_CHAINLOAD_TOOLCHAIN_FILE', 'VCPKG_HOST_TRIPLET', 'VCPKG_OVERLAY_TRIPLETS', 'NX_XWIN', 'NX_LLVM') }
                if ($Toolchain) { $cacheKeys += @('NX_XWIN', 'NX_LLVM', 'CMAKE_PREFIX_PATH') }   # AP_TOOLCHAIN_XWIN
                foreach ($k in $cacheKeys) {
                    $value = CacheValue $other $k
                    if ($value) { if ($k -eq 'CMAKE_GENERATOR') { $a += @('-G', $value) } else { $a += "-D$k=$value" } }
                }
                if ($OnLinux) { $a += "-DNX_VULKAN_SDK=$(VulkanView)" }
            } elseif ($Toolchain) {
                # AP_TOOLCHAIN_XWIN: Windows without VS: clang-cl + lld-link + llvm-rc against the xwin CRT/SDK (the toolchain file
                # of the Linux cross build, which also works on a Windows host) and the prebuilt libraries (no vcpkg).
                $tc = ((Under $Tools 'linux\clangcl-xwin.cmake') -replace '\\', '/')
                $deps = ("$($Toolchain.deps)" -replace '\\', '/').TrimEnd('/')
                $xw = ("$($Toolchain.xwin)" -replace '\\', '/').TrimEnd('/')
                $llvm = ("$($Toolchain.llvm_bin)" -replace '\\', '/').TrimEnd('/')
                foreach ($f in @($tc, "$deps/include", "$deps/share/sdl3", "$xw/VC/Tools/MSVC", "$llvm/clang-cl.exe")) {
                    if (-not (Test-Path -LiteralPath $f)) { Fail "$f does not exist (tools\install_requirements.ps1 -Toolchain xwin)" }
                }
                Info 'no previous CMakeCache: clang-cl + xwin, prebuilt libraries (Ninja, Release; no VS Build Tools, no vcpkg)'
                $a += @('-G', 'Ninja', "-DCMAKE_TOOLCHAIN_FILE=$tc", "-DNX_XWIN=$xw", "-DNX_LLVM=$llvm", '-DCMAKE_BUILD_TYPE=Release',
                        "-DCMAKE_PREFIX_PATH=$deps", "-DNX_OPUS_ROOT=$deps")
            } else {
                # New PC, without any build: the same values the CMakeCache files have today, taken from the
                # environment of dev.local.ps1 (VCPKG_ROOT, clang-cl from the PATH).
                if (-not $env:VCPKG_ROOT) { Fail 'no CMakeCache.txt to copy the toolchain from and no VCPKG_ROOT (load dev.local.ps1)' }
                $vcpkg = ($env:VCPKG_ROOT -replace '\\', '/').TrimEnd('/')
                $cc = (Get-Command clang-cl -ErrorAction Stop).Source -replace '\\', '/'
                if ($OnLinux) {
                    # Cross build: the tools/linux toolchain sets up clang-cl/lld-link/llvm-rc against the xwin CRT and
                    # SDK (NX_XWIN); the libraries are the x64-windows ones built by install_requirements_linux.sh with
                    # the triplet of _prepare/triplets.
                    $tc = ((Under $Tools 'linux\clangcl-xwin.cmake') -replace '\\', '/')
                    foreach ($f in @("$vcpkg/scripts/buildsystems/vcpkg.cmake", "$vcpkg/installed/x64-windows", $tc, "$env:NX_XWIN/VC/Tools/MSVC")) {
                        if (-not (Test-Path -LiteralPath $f)) { Fail "$f does not exist (tools/linux/install_requirements_linux.sh)" }
                    }
                    Info 'no previous CMakeCache: cross build (Ninja, Release, clang-cl + xwin, vcpkg x64-windows)'
                    $a += @('-G', 'Ninja', "-DCMAKE_TOOLCHAIN_FILE=$vcpkg/scripts/buildsystems/vcpkg.cmake", "-DVCPKG_CHAINLOAD_TOOLCHAIN_FILE=$tc",
                            "-DNX_XWIN=$env:NX_XWIN", "-DNX_LLVM=$(Split-Path -Parent $cc)", '-DCMAKE_BUILD_TYPE=Release',
                            '-DVCPKG_TARGET_TRIPLET=x64-windows', '-DVCPKG_HOST_TRIPLET=x64-linux',
                            "-DVCPKG_OVERLAY_TRIPLETS=$(((InRoot '_prepare\triplets') -replace '\\', '/'))", "-DNX_OPUS_ROOT=$vcpkg/installed/x64-windows",
                            "-DNX_VULKAN_SDK=$(VulkanView)")
                } else {
                foreach ($f in @("$vcpkg/scripts/buildsystems/vcpkg.cmake", "$vcpkg/installed/x64-windows")) {
                    if (-not (Test-Path -LiteralPath $f)) { Fail "$f does not exist (vcpkg: install ffmpeg and opus for x64-windows)" }
                }
                Info 'no previous CMakeCache: default parameters (Ninja, Release, clang-cl, vcpkg x64-windows)'
                $a += @('-G', 'Ninja', "-DCMAKE_TOOLCHAIN_FILE=$vcpkg/scripts/buildsystems/vcpkg.cmake", '-DCMAKE_BUILD_TYPE=Release',
                        "-DCMAKE_C_COMPILER=$cc", "-DCMAKE_CXX_COMPILER=$cc", '-DVCPKG_TARGET_TRIPLET=x64-windows', "-DNX_OPUS_ROOT=$vcpkg/installed/x64-windows")
                }
            }
            # AP_FFMPEG_PREBUILT: prebuilt FFmpeg of install_requirements.ps1 (step 5); without it CMake uses the vcpkg one
            $ffRoot = FfmpegPrebuilt
            if ($ffRoot) { $a += "-DNX_FFMPEG_ROOT=$ffRoot" }
            Info "cmake $($a -join ' ')"
            RunVisible 'cmake' $a 'cmake (configure)'
        }
        # AP_RECOMP_PARALLEL: -Jobs N limits the compile jobs; afterwards, where the time of this build went
        $nlog = Under $Build '.ninja_log'
        $nOff = [int64]0; if (Test-Path -LiteralPath $nlog) { $nOff = (Get-Item -LiteralPath $nlog).Length }
        $ba = @('--build', $Build); if ($Jobs -gt 0) { $ba += @('--parallel', "$Jobs") }
        $t0 = Get-Date
        RunVisible 'cmake' $ba 'cmake --build'
        Info ('cmake --build: {0:hh\:mm\:ss}' -f ((Get-Date) - $t0))
        BuildTimes $nlog $nOff
        if ($Toolchain) { CopyVcRuntime $Build }   # AP_XWIN_RUNTIME
    } finally { Pop-Location }
    $exe = Under $Build "$($V.Game).exe"
    if (-not (Test-Path -LiteralPath $exe)) { Fail "$exe does not appear" }
    $Summary['Exe'] = "$exe ($((Get-Item -LiteralPath $exe).LastWriteTime))"
    HudAssets
}

function Step10 {
    if (-not $Summary.Contains('Build ID') -and (Test-Path -LiteralPath (Under $Data 'exefs\main'))) { $Summary['Build ID'] = BuildIdOf (Under $Data 'exefs\main') }
    $Summary['Functions'] = "main $(FunctionCount main), sdk $(FunctionCount sdk)"
    if (-not $Summary.Contains('Exe')) {
        $exe = Under $Build "$($V.Game).exe"
        $Summary['Exe'] = if (Test-Path -LiteralPath $exe) { "$exe ($((Get-Item -LiteralPath $exe).LastWriteTime))" } else { '(not built)' }
    }
    if (-not $Summary.Contains('Shaders') -and (Test-Path -LiteralPath (Under $Data 'analysis\main.bin')) -and (Test-Path -LiteralPath (Under $Data 'extract\romfs\shader'))) {
        $c = CountShaders ''
        $Summary['Shaders'] = "$($c.total) (romfs $($c.romfs) + main $($c.main)); untranslated $($c.without_spv)"
    }
    $Summary['Data root'] = $Data
    $left = @('gen.old', 'exefs.old', 'extract\romfs.old', 'analysis\main.bin.old', 'analysis\sdk.bin.old' |
              Where-Object { Test-Path -LiteralPath (Under $Data $_) })
    if ($left.Count) { $Summary['Old'] = ($left -join ', ') + ' (in the data root; delete them by hand when convenient)' }
    Write-Host ''
    foreach ($k in $Summary.Keys) { Write-Host ('   {0,-10} {1}' -f $k, $Summary[$k]) }
}

# Compares gen\ and analysis\{main,sdk}.bin with a "HASH  PATH" inventory (the hashes.txt of Get-FileHash).
function CompareInventory([string]$file) {
    Step 11 "compare with $file"
    $base = $null   # the paths INSIDE the inventory are those of the PC that made it; its root is what precedes <data>\ in the first matching line
    $dataRel = ($V.Data -replace '/', '\') + '\'
    $expected = @{}
    foreach ($l in Get-Content -LiteralPath $file) {
        if ($l -notmatch '^([0-9A-Fa-f]{64})\s+(.+)$') { continue }
        $h = $Matches[1].ToUpper(); $p = $Matches[2].Trim()
        if ($null -eq $base) {
            $cut = $p.IndexOf('\' + $dataRel, [StringComparison]::OrdinalIgnoreCase)
            if ($cut -lt 0) { continue }
            $base = $p.Substring(0, $cut + 1)
        }
        if (-not $p.StartsWith($base + $dataRel, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $rel = $p.Substring(($base + $dataRel).Length)
        if ($rel -like 'gen\*' -or $rel -eq 'analysis\main.bin' -or $rel -eq 'analysis\sdk.bin') { $expected[$rel] = $h }
    }
    if (-not $expected.Count) { Fail "$file has no lines of $dataRel gen\ or analysis\" }
    $equal = 0; $different = @(); $missing = @()
    foreach ($rel in ($expected.Keys | Sort-Object)) {
        $p = Under $Data $rel
        if (-not (Test-Path -LiteralPath $p)) { $missing += $rel; continue }
        if ((Sha256 $p) -eq $expected[$rel]) { $equal++ } else { $different += $rel }
    }
    $extra = @(Get-ChildItem -LiteralPath (Under $Data 'gen') -Recurse -File | ForEach-Object { 'gen\' + $_.FullName.Substring((Under $Data 'gen').Length + 1).Replace($Sep, '\') } |
               Where-Object { -not $expected.ContainsKey($_) })
    Info "equal $equal of $($expected.Count); different $($different.Count); missing $($missing.Count); new in gen\ $($extra.Count)"
    foreach ($x in $different) { Warn "different: $x" }
    foreach ($x in $missing) { Warn "missing: $x" }
    foreach ($x in $extra) { Warn "new: $x" }
    if ($different.Count -or $missing.Count -or $extra.Count) { Fail 'the result is not equal to the inventory' }
    Info 'equal to the inventory'
}

# ---------------------------------------------------------------------------------------------- main
if ($From -gt $To) { throw "-From $From greater than -To $To" }
Write-Host "recomp_tsukihime: edition $Edition, steps $From..$To, root $Root$(if ($Replace) { ', -Replace' })"
$StepNames = @('precheck', 'identify', 'extract', 'verify exefs', 'structure', 'analysis', 'recomp', 'fonts', 'shaders', 'build', 'summary')
$script:CurrentStep = 0
try {
    Step 0 $StepNames[0]; Step0
    # Step 3 (verify exefs) is cheap and everything else depends on it: when resuming after it, it runs first.
    if ($From -gt 3) { $script:CurrentStep = 3; Step 3 "$($StepNames[3]) (always)"; Step3 }
    $start = if ($From -eq 2) { 1 } else { [Math]::Max(1, $From) }   # step 2 needs what step 1 identifies
    for ($n = $start; $n -le $To; $n++) {
        $script:CurrentStep = $n
        Step $n $StepNames[$n]
        if ($n -ge 5 -and $n -le 8 -and $To -ge 8) { StartHudBackground }   # AP_RECOMP_PARALLEL (AP_TOOLCHAIN_XWIN: also when the run ends at 8)
        & "Step$n"
    }
    if ($script:HudJob) { HudAssets }   # AP_TOOLCHAIN_XWIN: a run that ends before step 9 (R1 of -Recomp) waits for the background HUD build
    if ($Compare) { $script:CurrentStep = 11; CompareInventory $Compare }
} catch {
    Write-Host ''
    Write-Host "ERROR in step $($script:CurrentStep): $($_.Exception.Message)" -ForegroundColor Red
    try { RemoveStage } catch { Write-Host "   (could not delete $Stage`: delete it by hand)" }
    Write-Host "What the step managed to do was undone. Resume with -From $($script:CurrentStep) once it is fixed." -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host 'Done (the game was not started).' -ForegroundColor Green