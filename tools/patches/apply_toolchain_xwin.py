r"""apply_toolchain_xwin.py - Windows without Visual Studio Build Tools, plus the prebuilt pack. Marker AP_TOOLCHAIN_XWIN.

New files (delivered with this patch, not edited by it):
  tools\internal\install_requirements_xwin.ps1  the flow without VS (dot-sourced by install_requirements.ps1)
  tools\internal\prebuilt.json                  pins of the prebuilt pack (empty until the maintainer publishes it)
  tools\dev\build_prebuilt.ps1                  makes the pack (maintainer, on a PC with the VS flow installed)

Measured 2026-11-02 (4th VM run, 16:18): step 1 6:35 = VS Build Tools 3:57 + LLVM 1:04 + .NET 0:56 + winget 0:30, all
one after the other (LLVM 23 is an MSI, so it cannot run next to VS's MSIs: that is why the "LLVM in parallel" of
apply_install_parallel.py never happened; it looked for an .exe installer). This patch:
1. install_requirements.ps1: -Toolchain auto | xwin | vs. With the prebuilt pack pinned in tools\internal\prebuilt.json,
   auto = xwin: LLVM (its MSI unpacked with msiexec /a, not installed), CMake, Ninja, Python (python-build-standalone),
   xwin + the MSVC CRT/SDK, and the pack (vcpkg libraries, spirv-val, Vulkan headers, optionally spirv_harness and
   nsextract as self-contained exes) go per user to NX_TOOLS_DIR (default %LOCALAPPDATA%\tsuki-tools), in parallel,
   with the venv: no VS Build Tools, no vcpkg, no Vulkan SDK, no winget, no administrator (and no .NET SDK when the pack
   brings both exes). It writes _prepare\toolchain.json. Without the pack: vs, exactly as before. In the vs flow,
   LLVM is no longer tried in parallel (it was useless: an MSI). The prebuilt spirv_harness / nsextract carry a fingerprint
   of their sources: if the repo has other ones (a later commit), they are built here as before.
2. recomp_tsukihime.ps1: reads _prepare\toolchain.json (NX_TOOLCHAIN=vs ignores it): prebuilt nsextract / spirv_harness
   (exe, no dotnet; only while the sources match their fingerprint), always loads dev_env.ps1 in that mode (spirv-val and the tools come from there), and step 9 with
   no previous CMakeCache configures with tools\linux\clangcl-xwin.cmake + NX_XWIN + the prebuilt libraries
   (CMAKE_PREFIX_PATH, NX_OPUS_ROOT) instead of vcpkg. The second edition copies NX_XWIN, NX_LLVM, CMAKE_PREFIX_PATH.
   HUD icons: also in the background when the run ends at step 8 (R1 of -Recomp) and waited for at the end of the run,
   so R2 finds them up to date (~20 s less in -Recomp).
3. dev_env.ps1: with toolchain.json (mode xwin) it puts LLVM, CMake, Ninja, Python (and .NET if any) in the PATH and sets
   NX_XWIN, NX_LLVM, NX_DEPS and VULKAN_SDK (the pack's vulkan folder: spirv-val + headers) instead of the VS DevShell.
4. tools\linux\clangcl-xwin.cmake: also on a Windows host (the tools end in .exe there). The rest (/winsysroot, MSVC
   version from the CRT, llvm-rc flags) was already host-independent; llvm-mt of the official LLVM 23 MSI has libxml2
   (checked), so the manifest step works without the Windows SDK tools.
Not tested on Windows (no Windows here): parser, ASCII, apply/remove in any order, and the PowerShell parts
with stubs in pwsh 7 on Linux.

Run: cd <root>\framework; private\venv\Scripts\python ..\tools\patches\apply_toolchain_xwin.py [--remove]
Needs (already in the repo): AP_INSTALL_PARALLEL, AP_RECOMP_PARALLEL, AP_DROP_UNUSED, AP_FFMPEG_PREBUILT.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_TOOLCHAIN_XWIN"
INSTALL = "../tools/install_requirements.ps1"
RECOMP = "../tools/recomp_tsukihime.ps1"
DEVENV = "../tools/dev_env.ps1"
XWINCMAKE = "../tools/linux/clangcl-xwin.cmake"

EDITS = [
    # ================================================================================ install_requirements.ps1
    (INSTALL,
     r"""#   .\install_requirements.ps1 -VcpkgCache D:\vcpkgbin # vcpkg binary cache (another copy of %LOCALAPPDATA%\vcpkg\archives)
""",
     r"""#   .\install_requirements.ps1 -VcpkgCache D:\vcpkgbin # vcpkg binary cache (another copy of %LOCALAPPDATA%\vcpkg\archives)
#   .\install_requirements.ps1 -Toolchain xwin         # AP_TOOLCHAIN_XWIN: no VS Build Tools (tools\internal\install_requirements_xwin.ps1):
#       clang-cl + xwin + the prebuilt pack of tools\internal\prebuilt.json, per user, no administrator. -Toolchain auto (the
#       default) uses it when prebuilt.json has the pack; -Toolchain vs = VS Build Tools, .NET, Vulkan SDK and vcpkg as before
"""),
    (INSTALL,
     r"""    [switch]$AdminChild,
    [switch]$Worker
)
""",
     r"""    [switch]$AdminChild,
    [switch]$Worker,
    # AP_TOOLCHAIN_XWIN: auto | xwin | vs (see above); -Chain is internal (one chain of step 1 in the xwin flow)
    [ValidateSet('auto', 'xwin', 'vs')][string]$Toolchain = 'auto',
    [string]$Chain = ''
)
"""),
    (INSTALL,
     r"""    $par = @($all | Where-Object { $_ -in 'llvm', 'vulkan' })
""",
     r"""    $par = @($all | Where-Object { $_ -in 'vulkan' })   # AP_TOOLCHAIN_XWIN: LLVM 23 is an MSI: it waits for the MSIs of VS anyway
"""),
    (INSTALL,
     r"""if ($FunctionsOnly) { return }
""",
     r"""# AP_TOOLCHAIN_XWIN: Windows without VS Build Tools, only when chosen (-Toolchain xwin, or auto with the prebuilt pack
# pinned). Loaded here, after every function of this script, because it redefines some of them (steps, StartVcpkgWorker).
$script:UseXwin = $false
if ($OnWindows) {
    $nxXwin = Join-Path $PSScriptRoot 'internal/install_requirements_xwin.ps1'
    if (Test-Path -LiteralPath $nxXwin) { . $nxXwin }
    elseif ($Toolchain -eq 'xwin') { Write-Host "ERROR: $nxXwin is missing (is it in the repo?)" -ForegroundColor Red; exit 1 }
}
if ($script:UseXwin) {
    $StepNames[1] = 'tools (per user, in parallel; xwin)'; $StepNames[4] = 'vcpkg (not needed)'; $StepNames[5] = 'libraries (prebuilt pack)'
    $StepNames[8] = 'spirv_harness'
}
if ($FunctionsOnly) { return }
"""),
    (INSTALL,
     r"""    if ($AdminOnly) { AdminMode; return 0 }
""",
     r"""    if ($AdminOnly) { AdminMode; return 0 }
    if ($Chain) { return (RunChain $Chain) }   # AP_TOOLCHAIN_XWIN: one chain of step 1 (xwin); its output is captured by the parent
"""),
    # ================================================================================ recomp_tsukihime.ps1
    (RECOMP,
     r"""$NsProj = Under $Tools 'nsextract\nsextract.csproj'
""",
     r"""$NsProj = Under $Tools 'nsextract\nsextract.csproj'
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
"""),
    (RECOMP,
     r"""function EnsureNsExtract {
    if (-not (Test-Path -LiteralPath $NsProj)) { Fail "$NsProj is missing (it comes with the repo)" }
""",
     r"""function EnsureNsExtract {
    if ($NsExtract -like '*.exe') { Info "nsextract: $NsExtract (prebuilt)"; return }   # AP_TOOLCHAIN_XWIN
    if (-not (Test-Path -LiteralPath $NsProj)) { Fail "$NsProj is missing (it comes with the repo)" }
"""),
    (RECOMP,
     r"""    $argStr = ((@($NsExtract) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $dotnet = (Get-Command dotnet -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
""",
     r"""    $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN: the prebuilt exe, or dotnet + the dll
    $argStr = ((@($nc | Select-Object -Skip 1) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $dotnet = if ($nc[0] -eq 'dotnet') { (Get-Command dotnet -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source } else { $nc[0] }
"""),
    (RECOMP,
     r"""    if ($toolsMissing -or $builds) {
""",
     r"""    if ($toolsMissing -or $builds -or $Toolchain) {   # AP_TOOLCHAIN_XWIN: in that mode spirv-val and the tools come from dev_env.ps1
"""),
    (RECOMP,
     r"""            elseif ($builds -and -not $OnLinux -and (-not $env:INCLUDE -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment INCLUDE or VCPKG_ROOT are still empty: step 9 may fail' }
""",
     r"""            elseif ($builds -and -not $OnLinux -and $Toolchain -and -not $env:NX_XWIN) { Warn 'after loading the environment NX_XWIN is still empty (_prepare\toolchain.json): step 9 may fail' }   # AP_TOOLCHAIN_XWIN
            elseif ($builds -and -not $OnLinux -and -not $Toolchain -and (-not $env:INCLUDE -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment INCLUDE or VCPKG_ROOT are still empty: step 9 may fail' }
"""),
    (RECOMP,
     r"""    if ($From -le 2 -and $To -ge 1 -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\nsextract)' }   # AP_DROP_UNUSED
""",
     r"""    if ($From -le 2 -and $To -ge 1 -and $NsExtract -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\nsextract)' }   # AP_DROP_UNUSED, AP_TOOLCHAIN_XWIN
"""),
    (RECOMP,
     r"""        if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10)' }
""",
     r"""        if ($Harness -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10)' }   # AP_TOOLCHAIN_XWIN
"""),
    (RECOMP,
     r"""    $r = RunCapture 'dotnet' @($NsExtract, 'info', '--keys', $Keys, '--input', $InputFile)
""",
     r"""    $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN
    $r = RunCapture $nc[0] (@($nc | Select-Object -Skip 1) + @('info', '--keys', $Keys, '--input', $InputFile))
"""),
    (RECOMP,
     r"""        RunVisible 'dotnet' @($Harness, '--batch', $in, '--out', $out, '--jobs', "$hj", '--spirv-val', (SpirvVal)) 'spirv_harness'
""",
     r"""        $hc = ToolCmd $Harness   # AP_TOOLCHAIN_XWIN: the prebuilt exe, or dotnet + the dll
        RunVisible $hc[0] (@($hc | Select-Object -Skip 1) + @('--batch', $in, '--out', $out, '--jobs', "$hj", '--spirv-val', (SpirvVal))) 'spirv_harness'
"""),
    (RECOMP,
     r"""                if ($OnLinux) { $cacheKeys += @('VCPKG_CHAINLOAD_TOOLCHAIN_FILE', 'VCPKG_HOST_TRIPLET', 'VCPKG_OVERLAY_TRIPLETS', 'NX_XWIN', 'NX_LLVM') }
""",
     r"""                if ($OnLinux) { $cacheKeys += @('VCPKG_CHAINLOAD_TOOLCHAIN_FILE', 'VCPKG_HOST_TRIPLET', 'VCPKG_OVERLAY_TRIPLETS', 'NX_XWIN', 'NX_LLVM') }
                if ($Toolchain) { $cacheKeys += @('NX_XWIN', 'NX_LLVM', 'CMAKE_PREFIX_PATH') }   # AP_TOOLCHAIN_XWIN
"""),
    (RECOMP,
     r"""            } else {
                # New PC, without any build: the same values the CMakeCache files have today, taken from the
""",
     r"""            } elseif ($Toolchain) {
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
"""),
    (RECOMP,
     r"""        if ($n -ge 5 -and $n -le 8 -and $To -ge 9) { StartHudBackground }   # AP_RECOMP_PARALLEL
""",
     r"""        if ($n -ge 5 -and $n -le 8 -and $To -ge 8) { StartHudBackground }   # AP_RECOMP_PARALLEL (AP_TOOLCHAIN_XWIN: also when the run ends at 8)
"""),
    (RECOMP,
     r"""        Info 'HUD icons by device: building in the background meanwhile (shown at the start of step 9)'
""",
     r"""        Info 'HUD icons by device: building in the background meanwhile (shown at the start of step 9, or at the end of this run)'   # AP_TOOLCHAIN_XWIN
"""),
    (RECOMP,
     r"""    if ($Compare) { $script:CurrentStep = 11; CompareInventory $Compare }
""",
     r"""    if ($script:HudJob) { HudAssets }   # AP_TOOLCHAIN_XWIN: a run that ends before step 9 (R1 of -Recomp) waits for the background HUD build
    if ($Compare) { $script:CurrentStep = 11; CompareInventory $Compare }
"""),
    # ================================================================================ dev_env.ps1
    (DEVENV,
     r"""$nxVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
""",
     r"""# AP_TOOLCHAIN_XWIN: Windows without VS Build Tools (install_requirements.ps1 -Toolchain xwin): the tools of
# <root>\_prepare\toolchain.json instead of the VS DevShell. NX_TOOLCHAIN=vs (environment) ignores it.
$nxTc = Join-Path (Split-Path -Parent $nxTools) '_prepare\toolchain.json'
if ($env:NX_TOOLCHAIN -ne 'vs' -and (Test-Path -LiteralPath $nxTc)) {
    $nxJ = $null
    try { $nxJ = Get-Content -LiteralPath $nxTc -Raw | ConvertFrom-Json } catch { Write-Warning "$nxTc cannot be read: using VS Build Tools" }
    if ($nxJ -and $nxJ.mode -eq 'xwin') {
        foreach ($nxP in @($nxJ.dotnet_root, $nxJ.python, $nxJ.ninja_dir, $nxJ.cmake_bin, $nxJ.llvm_bin)) {
            if ("$nxP" -and (Test-Path -LiteralPath "$nxP")) { $env:PATH = "$nxP;$env:PATH" }
        }
        if ("$($nxJ.dotnet_root)") { $env:DOTNET_ROOT = "$($nxJ.dotnet_root)" }
        $env:NX_XWIN = "$($nxJ.xwin)"; $env:NX_LLVM = "$($nxJ.llvm_bin)"; $env:NX_DEPS = "$($nxJ.deps)"
        $env:VULKAN_SDK = "$($nxJ.vulkan)"   # spirv-val (Bin) and the Vulkan headers (Include) of the prebuilt pack
        Remove-Item Env:NX_DATA_ROOT -ErrorAction SilentlyContinue
        $nxInfo = 'dev env (xwin, no VS):'
        if (Get-Command clang-cl -ErrorAction SilentlyContinue) { $nxInfo += ' clang-cl ' + ((clang-cl --version)[0] -replace '.*version (\S+).*', '$1') }
        if (Get-Command cmake -ErrorAction SilentlyContinue) { $nxInfo += ' | cmake ' + ((cmake --version)[0] -replace 'cmake version ', '') }
        $nxInfo += ' | CRT/SDK ' + $env:NX_XWIN + ' | prebuilt ' + "$($nxJ.prebuilt_tag)"
        Write-Host $nxInfo
        return
    }
}

$nxVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
"""),
    # ================================================================================ clangcl-xwin.cmake
    (XWINCMAKE,
     """# Not used on Windows: there the build keeps using clang-cl from the PATH and the VS environment, as always.
""",
     """# Also on a Windows host (AP_TOOLCHAIN_XWIN): install_requirements.ps1 -Toolchain xwin builds the game with it, without
# VS Build Tools (step 9 of recomp: -DCMAKE_TOOLCHAIN_FILE=<this file> -DNX_XWIN -DNX_LLVM + the prebuilt libraries).
"""),
    (XWINCMAKE,
     """get_filename_component(_nx_bin "${NX_CLANG_CL}" DIRECTORY)
foreach(_nx_tool lld-link llvm-lib llvm-ar llvm-rc llvm-mt)
    if(NOT EXISTS "${_nx_bin}/${_nx_tool}")
""",
     """get_filename_component(_nx_bin "${NX_CLANG_CL}" DIRECTORY)
# AP_TOOLCHAIN_XWIN: on a Windows host the tools end in .exe
if(CMAKE_HOST_WIN32)
    set(_nx_x ".exe")
else()
    set(_nx_x "")
endif()
foreach(_nx_tool lld-link llvm-lib llvm-ar llvm-rc llvm-mt)
    if(NOT EXISTS "${_nx_bin}/${_nx_tool}${_nx_x}")
"""),
    (XWINCMAKE,
     """set(CMAKE_LINKER "${_nx_bin}/lld-link" CACHE FILEPATH "")
""",
     """set(CMAKE_LINKER "${_nx_bin}/lld-link${_nx_x}" CACHE FILEPATH "")
"""),
    (XWINCMAKE,
     """set(CMAKE_AR "${_nx_bin}/llvm-lib" CACHE FILEPATH "")
set(CMAKE_RC_COMPILER "${_nx_bin}/llvm-rc" CACHE FILEPATH "")
set(CMAKE_MT "${_nx_bin}/llvm-mt" CACHE FILEPATH "")
""",
     """set(CMAKE_AR "${_nx_bin}/llvm-lib${_nx_x}" CACHE FILEPATH "")
set(CMAKE_RC_COMPILER "${_nx_bin}/llvm-rc${_nx_x}" CACHE FILEPATH "")
set(CMAKE_MT "${_nx_bin}/llvm-mt${_nx_x}" CACHE FILEPATH "")
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
