# build_launcher.ps1 - builds tsukihime_launcher.exe (sources in <root>\launcher) and copies it to the root of the port.
#
# Same chain as the game (step 9 of recomp_tsukihime.ps1): the build environment (dev.local.ps1, or tools\dev_env.ps1)
# puts cmake, ninja and LLVM in the PATH; with the xwin flow (Windows without VS Build Tools, or Linux) CMake uses
# tools/linux/clangcl-xwin.cmake (clang-cl + lld-link + llvm-rc against the CRT/SDK of xwin); with VS Build Tools,
# clang-cl in the VS environment. The C++ runtime is static (/MT): the exe needs no DLL besides Windows itself.
# The launcher is built once by whoever publishes it (players download it): it does not need the game or its data.
#
#   Windows:  powershell -ExecutionPolicy Bypass -File .\tools\dev\build_launcher.ps1
#   Linux:    ~/.local/tsuki-tools/pwsh-7.6.7/pwsh tools/dev/build_launcher.ps1
#   -Clean    deletes launcher\build first (needed after switching between the xwin and VS chains)
#   -Tag      release tag the exe downloads the port from when the port is missing (for example v1.0.0); empty = the
#             latest release. Use the tag you are about to publish: the exe and the port zip must be of the same release.
#   -Root     another port folder (default: two folders above this script)
param(
    [string]$Root = '',
    [switch]$Clean,
    [string]$Tag = ''
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
$OnLinux = (Test-Path variable:IsLinux) -and $IsLinux

if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # tools\dev -> root
$Root = [IO.Path]::GetFullPath($Root)
$Src = Join-Path $Root 'launcher'
$Build = Join-Path $Src 'build'
$Exe = Join-Path $Root 'tsukihime_launcher.exe'
if (-not (Test-Path -LiteralPath (Join-Path $Src 'CMakeLists.txt'))) { throw "$Src\CMakeLists.txt is missing (are the launcher sources in the repo?)" }

# Runs a native program showing its output; PowerShell 5.1 with 'Stop' would turn its stderr into an error.
function RunNative([string]$exe, [string[]]$arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @arguments 2>&1 | ForEach-Object { Write-Host "   $_" }; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "$exe $($arguments -join ' ') failed (code $code)" }
}

# Build environment: the same choice as recomp_tsukihime.ps1 (the personal dev.local.ps1 first).
$envScript = @((Join-Path $Root 'framework/dev.local.ps1'), (Join-Path $Root 'tools/dev.local.ps1'), (Join-Path $Root 'tools/dev_env.ps1')) |
             Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $envScript) { throw "no build environment (tools\dev_env.ps1 is missing)" }
Write-Host "build_launcher: root $Root; environment $envScript"
. $envScript
$ErrorActionPreference = 'Stop'

foreach ($t in 'cmake', 'ninja') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "$t is not in the PATH: install the tools first (tools\install_requirements.ps1)" }
}

if ($Tag -and $Tag -notmatch '^[A-Za-z0-9._-]+$') { throw "-Tag '$Tag' is not a valid tag name (letters, digits, . _ -)." }
$cmakeArgs = @('-S', $Src, '-B', $Build, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', "-DLAUNCHER_TAG=$Tag")
$xwin = "$env:NX_XWIN"
if ($xwin) {
    # Static CRT: libcmt.lib (C runtime) and libcpmt.lib (C++ library) must be in the xwin output.
    foreach ($lib in 'libcmt.lib', 'libcpmt.lib') {
        $hit = Get-ChildItem -LiteralPath (Join-Path $xwin 'VC/Tools/MSVC') -Recurse -File -Filter $lib -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $hit) { throw "$lib is not in $xwin (xwin CRT without the static libraries): the launcher cannot be linked with /MT" }
    }
    $cmakeArgs += @("-DCMAKE_TOOLCHAIN_FILE=$(Join-Path $Root 'tools/linux/clangcl-xwin.cmake')", "-DNX_XWIN=$xwin")
    if ($env:NX_LLVM) { $cmakeArgs += "-DNX_LLVM=$env:NX_LLVM" }
    Write-Host "   chain: clang-cl + xwin ($xwin)"
} elseif ($OnLinux) {
    throw 'NX_XWIN is not set: run tools/linux/install_requirements_linux.sh first'
} else {
    if (-not (Get-Command clang-cl -ErrorAction SilentlyContinue)) { throw 'clang-cl is not in the PATH (LLVM): install the tools first' }
    $cmakeArgs += @('-DCMAKE_C_COMPILER=clang-cl', '-DCMAKE_CXX_COMPILER=clang-cl')
    Write-Host '   chain: clang-cl + VS Build Tools'
}

if ($Clean -and (Test-Path -LiteralPath $Build)) { Remove-Item -LiteralPath $Build -Recurse -Force; Write-Host "   $Build deleted" }
$t0 = Get-Date
RunNative 'cmake' $cmakeArgs
RunNative 'cmake' @('--build', $Build)
$built = Join-Path $Build 'tsukihime_launcher.exe'
if (-not (Test-Path -LiteralPath $built)) { throw "$built does not appear" }

# The exe must not need the Visual C++ runtime (VCRUNTIME140.dll, MSVCP140.dll, api-ms-win-crt-*).
$readobj = Get-Command llvm-readobj -ErrorAction SilentlyContinue
if ($readobj) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $imports = @(& $readobj.Source --coff-imports $built 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -match '^\s*Name:\s*(\S+\.dll)' } | ForEach-Object { $Matches[1] } | Sort-Object -Unique)
    $ErrorActionPreference = $prev
    $bad = @($imports | Where-Object { $_ -match '^(vcruntime|msvcp|ucrtbase|api-ms-win-crt)' })
    Write-Host "   imports: $($imports -join ', ')"
    if ($bad.Count) { throw "the launcher needs $($bad -join ', '): it was not linked with the static CRT" }
} else {
    Write-Host '   (llvm-readobj not found: the DLL imports were not checked)'
}

Copy-Item -LiteralPath $built -Destination $Exe -Force
Write-Host ('build_launcher: {0} ({1:N0} KB) in {2:mm\:ss}' -f $Exe, ((Get-Item -LiteralPath $Exe).Length / 1KB), ((Get-Date) - $t0)) -ForegroundColor Green
