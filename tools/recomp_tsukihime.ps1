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
#   1 identify       CNMT of the Meta NCA: program ID, Application type, version v0, program NCA. The container is
#                    recognized by its header (PFS0 = NSP, HEAD = XCI), not by the extension or the edition.
#   2 extract        program NCA -> exefs + romfs (in _recomp\, moved at the end); deletes the intermediate NCA
#   3 verify         program ID of main.npdm and build ID of main (+0x40) against the measured ones; then updates\ (below)
#   4 structure      japan: games\tsukihime; global: create_tsukihime_global.ps1 (idempotent)
#   5 analysis       tools\nso2bin.py -> analysis\main.bin and sdk.bin
#   6 recomp         regen_tsuki*.ps1 main, sdk -> gen\ (the previous gen\ is kept as gen.old)
#   7 fonts          fonts\shared_0.ttf (Noto Sans JP, OFL license): from -Fonts, from <root>\resources\fonts\ (in the repo) or from the other edition
#   8 shaders        tsuki_shaders_offline.py (romfs\shader + main) -> harness only for those without a .spv
#   9 build          cmake -B (parameters of the existing CMakeCache) + cmake --build
#  10 summary
#
# The build environment (dev.local.ps1, or tools\dev_env.ps1 if there is none) is loaded only when needed (step 0).
# Rules: does not touch user\, language.txt, input.ini or the framework (read only; the global uses create_tsukihime_global.ps1).
# Never deletes from data\ what it did not create: what is replaced is kept as *.old. Keys are never copied or printed.
# -Replace redoes extraction and recomp even if they are done. -Root to point to another copy of the port.
# Keys: <root>\data\prod.keys (never copied or printed).
# -ProgressSeconds: how often the progress of long extractions is reported (30 by default).
# Linux (PowerShell 7, via tools/linux/recomp_linux.sh): same steps; step 9 cross-compiles the SAME Windows exe
# (clang-cl + lld-link + llvm-rc against the xwin CRT/SDK; tools/linux/clangcl-xwin.cmake) to play it with
# Proton. The tools are installed by tools/linux/install_requirements_linux.sh. Nothing changes on Windows.
# Updates and mods: every <root>\updates\*.nsp (updates or patches, e.g. a translation; a name starting with _ is
# skipped) of this edition is processed after step 3 by tools\internal\mods_nsp.py, without the base dump: the base
# romfs is rebuilt from extract\romfs plus <data>\romfs_index.bin, made ONCE from the dump the first time there is an
# .nsp (afterwards the dump is not needed). Its exefs goes to <data>\updates\<name>\ (only for the recompilation) and
# the romfs files that differ from the base go to <root>\mods\<edition>\<name>\romfs. The exe reads every folder of
# <root>\mods\<edition>\ (name order, the last one wins; _ skips it) before its own romfs (apply_romfs_mods.py), so
# mods can also be copied there by hand, without recompiling. Once processed, the .nsp can be deleted: an update stays
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
    [switch]$NoNsExtract,
    [switch]$ForceBuildId,
    [switch]$NoUpdates,
    [ValidateRange(2, 600)][int]$ProgressSeconds = 30
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
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\runtime\src\nvn\nvn_vk.cpp', 'AP_VK_REQ', 'apply_vk_requirements.py', $false),
    @('both', 'framework\runtime\src\hle_fs.cpp', 'AP_ROMFS_MODS', 'apply_romfs_mods.py', $false),
    @('both', 'framework\games\tsukihime_common\tsukihime_common.cmake', 'AP_ROMFS_MODS', 'apply_romfs_mods.py', $false),
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
$Nstool = if ($OnLinux) { Under $Tools 'nstool' } else { Under $Tools 'nstool.exe' }   # Linux: built by install_requirements_linux.sh
# nsextract (tools\nsextract, C# + LibHac): reads exefs and romfs straight from the NSP/XCI, without the intermediate copy.
# If it is not built (dotnet build tools/nsextract/nsextract.csproj -c Release) or -NoNsExtract is given, nstool is used as before.
$NsExtract = Under $Tools 'nsextract\bin\Release\net10.0\nsextract.dll'
$NsProj = Under $Tools 'nsextract\nsextract.csproj'
$script:NsBroken = $false   # the build failed: nstool is used
function UseNsExtract { return ((-not $NoNsExtract) -and (-not $script:NsBroken) -and (Test-Path -LiteralPath $NsExtract) -and [bool](Get-Command dotnet -ErrorAction SilentlyContinue)) }
# Builds nsextract if the dll is missing or older than its sources (needs dotnet and network the first time, for the LibHac
# package). If the build fails it only warns: the script goes on with nstool (what it did before).
function EnsureNsExtract {
    if ($NoNsExtract -or -not (Test-Path -LiteralPath $NsProj)) { return }
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Warn 'dotnet not found: nsextract cannot be built, using nstool'; return }
    $src = @(Get-ChildItem -LiteralPath (Split-Path -Parent $NsProj) -File | Where-Object { $_.Extension -in '.cs', '.csproj' })
    $newest = ($src | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Test-Path -LiteralPath $NsExtract) -and (Get-Item -LiteralPath $NsExtract).LastWriteTimeUtc -ge $newest) { Info 'nsextract already built'; return }
    Info 'building nsextract (tools\nsextract)'
    try { RunVisible 'dotnet' @('build', $NsProj, '-c', 'Release', '--nologo', '-v', 'q') 'dotnet build nsextract' }
    catch { $script:NsBroken = $true; Warn "nsextract was not built ($($_.Exception.Message)): using nstool (-NoNsExtract to skip this)" }
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

# External program; captures the output without printing it (nstool: none of its output goes to the console or a file).
function RunCapture([string]$exe, [string[]]$arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    return @{ Code = $code; Output = $out }
}
function Nstool([string[]]$arguments, [string]$what) {
    $r = RunCapture $Nstool (@('-k', $Keys) + $arguments)
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nstool failed to $what (code $($r.Code)): $($err -join ' | ')"
    }
    return $r.Output
}
# External program with its output visible (recomp, harness, cmake).
function RunVisible([string]$exe, [string[]]$arguments, [string]$what) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (code $LASTEXITCODE)" }
}

# nstool for long extractions: runs separately and every -ProgressSeconds reports how much it has written (I/O counter
# of the process; if it cannot be read, the size of the destination folder). Its output goes to two temporary files in
# _recomp\ that are read only to look for ERROR lines and deleted right away (they are not shown).
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
function NstoolLong([string[]]$arguments, [string]$what, [string]$dest, [int64]$total) {
    $id = [guid]::NewGuid().ToString('N')
    $fo = Under $Stage "nstool_$id.out"; $fe = Under $Stage "nstool_$id.err"
    $argStr = ((@('-k', $Keys) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $p = Start-Process -FilePath $Nstool -ArgumentList $argStr -NoNewWindow -PassThru -RedirectStandardOutput $fo -RedirectStandardError $fe
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
        Fail "nstool failed to $what (code $code): $($err -join ' | ')"
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

# ---------------------------------------------------------------------------------------------- CNMT
# Format (binary, already decrypted by nstool -x /0 of the Meta NCA): +0x00 title ID u64, +0x08 version u32,
# +0x0C type u8 (0x80 Application), +0x0E size of the extended header u16, +0x10 number of contents u16;
# records of 0x38 at 0x20 + ext: hash 0x20, NCA ID 0x10, size 6 bytes, type u8 (1 = Program).
function ReadCnmt([string]$p) {
    $b = [IO.File]::ReadAllBytes($p)
    $r = @{ TitleId = (U64 $b 0); Version = (U32 $b 8); Type = $b[0x0C]; Contents = @() }
    $ext = [BitConverter]::ToUInt16($b, 0x0E); $n = [BitConverter]::ToUInt16($b, 0x10)
    for ($i = 0; $i -lt $n; $i++) {
        $o = 0x20 + $ext + $i * 0x38
        if ($o + 0x38 -gt $b.Length) { Fail "short CNMT ($p)" }
        $id = (($b[($o + 0x20)..($o + 0x2F)] | ForEach-Object { $_.ToString('x2') }) -join '')
        $size = [int64]0; for ($k = 5; $k -ge 0; $k--) { $size = $size * 256 + $b[$o + 0x30 + $k] }
        $r.Contents += @{ Id = $id; Size = $size; Type = $b[$o + 0x36] }
    }
    return $r
}

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

# The keys do not leave data\: only their path is passed to nstool, nsextract and mods_nsp.py.
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
    if ($toolsMissing -or $builds) {
        if ($DevLocal) {
            Info "loading $DevLocal"
            LoadDevLocal
            if ($builds -and $OnLinux -and (-not $env:NX_XWIN -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment NX_XWIN or VCPKG_ROOT are still empty: step 9 may fail' }
            elseif ($builds -and -not $OnLinux -and (-not $env:INCLUDE -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment INCLUDE or VCPKG_ROOT are still empty: step 9 may fail' }
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
    if ($From -le 2 -and $To -ge 1 -and -not (Test-Path -LiteralPath $Nstool)) { $missing += $Nstool }
    if ($To -ge 8) {
        if (-not (Test-Path -LiteralPath $Harness)) { $missing += "$Harness (dotnet build tools/maxwell/spirv_harness/SpirvHarness.csproj -c Release -m:2)" }
        if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10)' }
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

# nstool -x <internal path of a file> <output>: depending on the version it leaves <output> as a file or as a folder
# with the file inside; both are accepted. Returns the path of the file or $null if nstool failed.
$script:LastNstoolError = ''
function ExtractFile([string]$internal, [string]$output, [string]$container) {
    $r = RunCapture $Nstool @('-k', $Keys, '-x', $internal, $output, $container)
    $script:LastNstoolError = "code $($r.Code): " + ((@($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)) -join ' | ')
    if ($r.Code -ne 0) { return $null }
    if (Test-Path -LiteralPath $output -PathType Leaf) { return $output }
    $inside = Under $output (Split-Path $internal -Leaf)
    if (Test-Path -LiteralPath $inside -PathType Leaf) { return $inside }
    $script:LastNstoolError += ' (finished without error but left neither the file nor a folder with it)'
    return $null
}

# nstool 1.9.2 on Windows does not extract a single file from a PFS0 (tested 20:27: with a missing output it fails in
# getDirectoryListing, with an existing folder FileStream::open "Access denied"). Folders do work (/0, /1, /):
# if the single file fails, the whole partition (NSP: /, XCI: /secure) is extracted once to _recomp\container
# and step 2 takes the program NCA from there (same space: the NCA was going to be copied anyway).
# Size of what -x <partition> writes: NSP = the whole file; XCI = the secure partition, read from the root HFS0 of
# the header (an untrimmed XCI is padded up to the cartridge size, and it may carry update and normal partitions).
# Root HFS0: offset u64 at 0x130; 'HFS0', file count u32, string table size u32, reserved u32, then entries of
# 0x40 bytes (offset u64, size u64, name offset u32, ...) and the string table; data after the table.
# Only used to estimate free space: if anything does not fit, the size of the whole file (as before).
function PartitionSize([string]$p) {
    $len = (Get-Item -LiteralPath $p).Length
    if ((ContainerType $p) -ne 'xci') { return $len }
    try {
        $fs = [IO.File]::OpenRead($p)
        try {
            $br = New-Object IO.BinaryReader($fs)
            $fs.Position = 0x130; $off = $br.ReadInt64()
            if ($off -le 0 -or $off + 0x10 -gt $len) { return $len }
            $fs.Position = $off
            if ([Text.Encoding]::ASCII.GetString($br.ReadBytes(4)) -ne 'HFS0') { return $len }
            $count = $br.ReadInt32(); $strSize = $br.ReadInt32()
            if ($count -lt 1 -or $count -gt 8 -or $strSize -lt 0 -or $strSize -gt 0x10000) { return $len }
            $fs.Position = $off + 0x10
            $ents = @(); for ($i = 0; $i -lt $count; $i++) { $ents += , @($br.ReadInt64(), $br.ReadInt64(), $br.ReadInt32()); [void]$br.ReadBytes(0x2C) }
            $strs = $br.ReadBytes($strSize)
            foreach ($e in $ents) {
                $end = [Array]::IndexOf($strs, [byte]0, $e[2]); if ($end -lt 0) { $end = $strs.Length }
                if ([Text.Encoding]::ASCII.GetString($strs, $e[2], $end - $e[2]) -eq 'secure' -and $e[1] -gt 0 -and $e[1] -le $len) { return [int64]$e[1] }
            }
        } finally { $fs.Dispose() }
    } catch { }
    return $len
}

$script:Container = $null
function FromContainer([string]$name, [string]$dest) {
    if (-not $script:Container) {
        $f = ExtractFile ($script:Prefix + $name) $dest $InputFile
        if ($f) { return $f }
        $size = PartitionSize $InputFile
        $needed = [int64]$size * 2 + 2GB    # container (the partition) + romfs/exefs (smaller than the partition)
        $free = FreeOn $Data
        if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
        Info "space: $(GB $free) free, ~$(GB $needed) needed"
        $c = Under $Stage 'container'
        NstoolLong @('-x', $script:Partition, $c, $InputFile) "extract $($script:Partition) from the input" $c $size
        $script:Container = $c
    }
    $f = Under $script:Container $name
    if (Test-Path -LiteralPath $f -PathType Leaf) { return $f }
    return $null
}

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

# Meta NCAs inside a partition of the nstool --fstree tree (by indentation: "  secure/" and its files further in).
function MetasOfPartition([string[]]$tree, [string]$partition) {
    $inside = $false; $indent = -1; $r = @()
    foreach ($l in $tree) {
        if ($l -notmatch '^(\s*)(\S.*?)\s*$') { continue }
        $ind = $Matches[1].Length; $nm = $Matches[2]
        if ($inside -and $ind -le $indent) { $inside = $false }
        if (-not $inside -and $nm -eq "$partition/") { $inside = $true; $indent = $ind; continue }
        if ($inside) { foreach ($m in [regex]::Matches($nm, '[0-9a-f]{32}\.cnmt\.nca')) { $r += $m.Value } }
    }
    return @($r | Sort-Object -Unique)
}

function IdentifyInput {
    NewStage
    $script:Container = $null
    if (UseNsExtract) {
        # Direct path: nsextract reports the program ID of the base application; the NCA is not extracted.
        $r = RunCapture 'dotnet' @($NsExtract, 'info', '--keys', $Keys, '--input', $InputFile)
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
        return
    }
    $isXci = (ContainerType $InputFile) -eq 'xci'
    $tree = Nstool @('--fstree', $InputFile) 'list the input'
    $metas = @($tree | ForEach-Object { [regex]::Matches($_, '[0-9a-f]{32}\.cnmt\.nca') } | ForEach-Object { $_.Value } | Sort-Object -Unique)
    $ncas = @($tree | ForEach-Object { [regex]::Matches($_, '[0-9a-f]{32}\.nca') } | ForEach-Object { $_.Value } | Sort-Object -Unique)
    if (-not $metas.Count) { Fail 'the input has no Meta NCA (*.cnmt.nca)' }
    # XCI: only the Meta NCAs of secure/ (update/ has ~100 system ones). Without this filter, the first one tried could
    # be from update/: nstool 1.9.2 on Linux does not find it in /secure, exits with 0 leaving nothing and it was
    # concluded that it does not extract single files (extraction of the whole /secure, ~2x the XCI in space). On
    # Windows the result does not change: there nstool fails with any single file and /secure is extracted as before.
    if ($isXci) {
        $sec = @(MetasOfPartition $tree 'secure')
        if ($sec.Count) { Info "Meta NCAs in secure/: $($sec.Count) of $($metas.Count)"; $metas = $sec }
        else { Warn "secure/ not found in the nstool --fstree tree: trying the $($metas.Count) Meta NCAs" }
    }
    # In an XCI the update partition has other CNMTs (system ones): all those of /secure are read and the
    # Application one is kept. In an NSP there must be only one.
    $prefix = if ($isXci) { '/secure/' } else { '/' }
    $script:Prefix = $prefix
    $script:Partition = if ($isXci) { '/secure' } else { '/' }
    if (-not $isXci -and $metas.Count -ne 1) { Fail "the NSP has $($metas.Count) Meta NCAs: only the base is expected (no update or DLC together)" }
    $apps = @(); $others = @(); $k = 0
    foreach ($m in $metas) {
        $k++
        $meta = FromContainer $m (Under $Stage "meta$k")
        if (-not $meta) {
            if ($isXci) { continue }   # not in /secure (update or normal)
            Fail "nstool could not extract $prefix$m ($script:LastNstoolError)"
        }
        $dir = Under $Stage "cnmt$k"
        Nstool @('-x', '/0', $dir, $meta) "open $m" | Out-Null
        $cnmt = @(Get-ChildItem -LiteralPath $dir -Recurse -Filter '*.cnmt' -File)
        if ($cnmt.Count -ne 1) { Fail "1 .cnmt was expected inside $m and there are $($cnmt.Count)" }
        $c = ReadCnmt $cnmt[0].FullName
        if ($c.Type -eq 0x80) { $apps += , $c } else { $others += ('{0} type 0x{1:x2} v{2}' -f (Hex16 $c.TitleId), $c.Type, $c.Version) }
    }
    if ($others.Count) { Info ("other CNMTs in the input (ignored): " + ($others -join ', ')) }
    if ($apps.Count -ne 1) { Fail "1 Application CNMT was expected and there are $($apps.Count)$(if ($isXci) { ' in /secure (if it is 0, send me the output of nstool --fstree of the XCI)' })" }
    $c = $apps[0]
    if ($c.TitleId -ne $V.ProgramId) {
        $other = ($Editions.GetEnumerator() | Where-Object { $_.Value.ProgramId -eq $c.TitleId } | Select-Object -First 1)
        $extra = if ($other) { " (it is the $($other.Key) edition)" } else { '' }
        $script:WrongEdition = $true
        Fail "$(Split-Path -Leaf $InputFile): program ID $(Hex16 $c.TitleId)$extra, not $(Hex16 $V.ProgramId) of the $Edition edition"
    }
    if ($c.Version -ne 0) { Fail "CNMT version $($c.Version), not the base v0" }
    $prog = @($c.Contents | Where-Object { $_.Type -eq 1 })
    if ($prog.Count -ne 1) { Fail "1 program NCA was expected in the CNMT and there are $($prog.Count)" }
    $name = "$($prog[0].Id).nca"
    if ($ncas -notcontains $name) { Fail "the CNMT names $name and the input does not have it" }
    $script:Ident = @{ Nca = $name; Size = $prog[0].Size; Path = ($prefix + $name) }
    Info "program ID $(Hex16 $c.TitleId), Application, v0; program NCA $name ($(GB $prog[0].Size))"
    $Summary['Input'] = "$InputFile (program ID $(Hex16 $c.TitleId), v0)"
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
        return
    }
    if ((Test-Path -LiteralPath $exefs) -and -not $Replace) { Fail "$exefs exists but the extraction is incomplete: check it or use -Replace" }
    if ((Test-Path -LiteralPath $romfs) -and -not $Replace) { Fail "$romfs exists but the extraction is incomplete: check it or use -Replace" }
    if (-not $script:Ident) { Fail 'step 1 (identify) is missing' }
    foreach ($p in @($exefs, $romfs)) {   # before extracting ~23 GB: what SetAside would reject at the end
        if ((Test-Path -LiteralPath $p) -and (Test-Path -LiteralPath "$p.old")) { Fail "$p.old already exists (from a previous run): delete or rename it by hand" }
    }
    if ($script:Ident.Direct) {
        # exefs and romfs go straight from the input to _recomp\: only their own size is written (no copy of the NCA).
        $needed = [int64]$script:Ident.Size + 2GB    # upper bound: romfs + exefs are smaller than the input
        $free = FreeOn $Data
        if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
        Info "space: $(GB $free) free, ~$(GB $needed) needed at most"
        Info 'extracting exefs and romfs straight from the input (nsextract)'
        $t0 = Get-Date
        RunVisible 'dotnet' @($NsExtract, 'extract', '--keys', $Keys, '--input', $InputFile, '--program-id', (Hex16 $V.ProgramId),
                              '--exefs', (Under $Stage 'exefs'), '--romfs', (Under $Stage 'romfs')) 'nsextract'
        Info ('extracted in {0:mm\:ss}' -f ((Get-Date) - $t0))
    } else {
        if (-not $script:Container) {
            $needed = [int64]$script:Ident.Size * 2 + 2GB    # intermediate NCA + romfs/exefs
            $free = FreeOn $Data
            if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
            Info "space: $(GB $free) free, ~$(GB $needed) needed (~$(GB $script:Ident.Size) are freed at the end)"
            Info 'extracting the program NCA'
        }
        New-Item -ItemType Directory -Path (Under $Stage 'nca') -Force | Out-Null
        $nca = FromContainer $script:Ident.Nca (Under $Stage "nca\$($script:Ident.Nca)")
        if (-not $nca) { Fail "nstool could not extract $($script:Ident.Path) ($script:LastNstoolError)" }
        if ((Get-Item -LiteralPath $nca).Length -ne $script:Ident.Size) {
            Fail "the extracted program NCA does not have the size of the CNMT ($($script:Ident.Size) bytes)"
        }
        Info 'extracting exefs (/0) and romfs (/1)'
        Nstool @('-x', '/0', (Under $Stage 'exefs'), $nca) 'extract exefs' | Out-Null
        NstoolLong @('-x', '/1', (Under $Stage 'romfs'), $nca) 'extract romfs' (Under $Stage 'romfs') ((Get-Item -LiteralPath $nca).Length)
        Remove-Item -LiteralPath (Under $Stage 'nca') -Recurse -Force
        if ($script:Container) { Remove-Item -LiteralPath $script:Container -Recurse -Force; $script:Container = $null }
    }
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
        $ra = @{ Modules = @('main', 'sdk'); Root = $Root }
        if ($script:UpdateMain) { $ra['MainNso'] = $script:UpdateMain }
        & $regenToRun @ra
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
        RunVisible 'dotnet' @($Harness, '--batch', $in, '--out', $out, '--jobs', '3', '--spirv-val', (SpirvVal)) 'spirv_harness'
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

function CacheValue([string]$cache, [string]$k) {
    $l = Select-String -LiteralPath $cache -Pattern "^$k(:[A-Z]+)?=(.*)$" | Select-Object -First 1
    if ($l) { return $l.Matches[0].Groups[2].Value } else { return '' }
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

function Step9 {
    $cache = Under $Build 'CMakeCache.txt'
    $gen = ((Under $Data 'gen') -replace '\\', '/')
    Push-Location $Framework
    try {
        if (Test-Path -LiteralPath $cache) {
            $g = (CacheValue $cache 'NX_GEN_DIR') -replace '\\', '/'
            if ($g -ne $gen) { Fail "$Build uses NX_GEN_DIR=$g, not $gen (in a test copy: delete that build\ of the copy, it is configured again)" }
            Info "$Build already configured"
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
                foreach ($k in $cacheKeys) {
                    $value = CacheValue $other $k
                    if ($value) { if ($k -eq 'CMAKE_GENERATOR') { $a += @('-G', $value) } else { $a += "-D$k=$value" } }
                }
                if ($OnLinux) { $a += "-DNX_VULKAN_SDK=$(VulkanView)" }
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
            Info "cmake $($a -join ' ')"
            RunVisible 'cmake' $a 'cmake (configure)'
        }
        RunVisible 'cmake' @('--build', $Build) 'cmake --build'
    } finally { Pop-Location }
    $exe = Under $Build "$($V.Game).exe"
    if (-not (Test-Path -LiteralPath $exe)) { Fail "$exe does not appear" }
    $Summary['Exe'] = "$exe ($((Get-Item -LiteralPath $exe).LastWriteTime))"
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
        & "Step$n"
    }
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