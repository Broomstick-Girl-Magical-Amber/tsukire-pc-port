# dev_env.ps1 - Tsukihime build environment on this PC: VS Build Tools (DevShell x64), LLVM/clang-cl,
# CMake and Ninja from winget, Vulkan SDK, vcpkg and nstool. It is in the repo (<root>\tools); it is the portable
# version of the personal dev.local.ps1 (which is not uploaded). If a dev.local.ps1 exists, recomp_tsukihime.ps1 uses that one.
#
#   . .\dev_env.ps1                # with a dot: the variables stay in your console
# recomp_tsukihime.ps1 loads it by itself when it is going to build. It does not change the current folder.
#
# Optional (environment variables, before loading it):
#   NX_VCPKG   vcpkg folder (default C:\dev\vcpkg, the one of install_requirements.ps1)
#   NX_LLVM    LLVM bin folder (default %ProgramFiles%\LLVM\bin)
#
# On Linux (PowerShell 7; loaded by tools/linux/recomp_linux.sh via recomp_tsukihime.ps1) there is no DevShell: it puts
# in the PATH the tools of install_requirements.ps1 (tarballs in NX_TOOLS_DIR, default ~/.local/tsuki-tools) and
# sets VCPKG_ROOT (NX_VCPKG, default ~/dev/vcpkg), NX_XWIN (xwin Windows CRT/SDK) and VULKAN_SDK (spirv-val).
$ErrorActionPreference = 'Stop'
$nxTools = $PSScriptRoot

if ((Test-Path variable:IsLinux) -and $IsLinux) {
    $nxT = if ($env:NX_TOOLS_DIR) { $env:NX_TOOLS_DIR } else { Join-Path $HOME '.local/tsuki-tools' }
    $nxPin = @{ llvm = 'llvm-23.1.3'; cmake = 'cmake-4.4.4'; ninja = 'ninja-1.13.2'; dotnet = 'dotnet-10.0.401'; vulkan = 'vulkan-1.4.363.0' }
    $nxLlvm = if ($env:NX_LLVM) { $env:NX_LLVM } else { Join-Path $nxT "$($nxPin.llvm)/bin" }
    $nxPaths = @($nxLlvm, (Join-Path $nxT "$($nxPin.cmake)/bin"), (Join-Path $nxT $nxPin.ninja), (Join-Path $nxT $nxPin.dotnet), $nxTools)
    foreach ($nxP in $nxPaths) { if ((Test-Path -LiteralPath $nxP) -and (($env:PATH -split ':') -notcontains $nxP)) { $env:PATH = "${nxP}:$env:PATH" } }
    if (Test-Path -LiteralPath (Join-Path $nxT $nxPin.dotnet)) { $env:DOTNET_ROOT = Join-Path $nxT $nxPin.dotnet }
    $env:DOTNET_SYSTEM_GLOBALIZATION_INVARIANT = '1'; $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
    $nxVk = Join-Path $nxT "$($nxPin.vulkan)/x86_64"
    if (-not $env:VULKAN_SDK -and (Test-Path -LiteralPath $nxVk)) { $env:VULKAN_SDK = $nxVk }
    if (-not $env:NX_XWIN) { $env:NX_XWIN = Join-Path $nxT 'xwin' }
    if (-not (Test-Path -LiteralPath (Join-Path $env:NX_XWIN 'VC/Tools/MSVC'))) { Write-Warning "$env:NX_XWIN does not exist (Windows CRT/SDK): tools/linux/install_requirements_linux.sh -From 2" }
    $nxVcpkg = if ($env:NX_VCPKG) { $env:NX_VCPKG } else { Join-Path $HOME 'dev/vcpkg' }
    if (-not (Test-Path -LiteralPath $nxVcpkg)) { Write-Warning "$nxVcpkg does not exist (vcpkg): tools/linux/install_requirements_linux.sh -From 4, or set NX_VCPKG." }
    $env:VCPKG_ROOT = $nxVcpkg
    Remove-Item Env:NX_DATA_ROOT -ErrorAction SilentlyContinue
    $nxInfo = 'dev env (Linux):'
    if (Get-Command clang-cl -ErrorAction SilentlyContinue) { $nxInfo += ' clang-cl ' + ((& clang-cl --version)[0] -replace '.*version (\S+).*', '$1') }
    if (Get-Command cmake -ErrorAction SilentlyContinue) { $nxInfo += ' | cmake ' + ((& cmake --version)[0] -replace 'cmake version ', '') }
    if (Test-Path -LiteralPath (Join-Path $env:NX_XWIN 'VC/Tools/MSVC')) { $nxInfo += ' | xwin CRT ' + ((Get-ChildItem -LiteralPath (Join-Path $env:NX_XWIN 'VC/Tools/MSVC') -Directory | Select-Object -First 1).Name) }
    Write-Host $nxInfo
    return
}

$nxVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $nxVswhere)) { throw "cannot find vswhere ($nxVswhere): install VS Build Tools (install_requirements.ps1)." }
$nxVs = & $nxVswhere -products * -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $nxVs) { throw 'VS Build Tools with the C++ compiler not found (vswhere).' }
if (-not $env:VSCMD_ARG_TGT_ARCH) {
    Import-Module (Join-Path $nxVs 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
    Enter-VsDevShell -VsInstallPath $nxVs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
}

# After the DevShell, so they win over what VS brings:
$nxLlvm = if ($env:NX_LLVM) { $env:NX_LLVM } else { Join-Path $env:ProgramFiles 'LLVM\bin' }
if (Test-Path -LiteralPath $nxLlvm) { $env:PATH = "$nxLlvm;$env:PATH" }
$nxWinget = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'     # cmake and ninja (winget)
if (Test-Path -LiteralPath $nxWinget) { $env:PATH = "$nxWinget;$env:PATH" }
$env:PATH = "$nxTools;$env:PATH"                                      # nstool

if (-not $env:VULKAN_SDK) {
    $nxVk = [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')
    if (-not $nxVk -and (Test-Path -LiteralPath 'C:\VulkanSDK')) {
        $nxVk = (Get-ChildItem 'C:\VulkanSDK' -Directory | Sort-Object Name | Select-Object -Last 1).FullName
    }
    if ($nxVk) { $env:VULKAN_SDK = $nxVk }
}
if ($env:VULKAN_SDK) { $env:PATH = "$env:VULKAN_SDK\Bin;$env:PATH" }

$nxVcpkg = if ($env:NX_VCPKG) { $env:NX_VCPKG } else { 'C:\dev\vcpkg' }
if (-not (Test-Path -LiteralPath $nxVcpkg)) { Write-Warning "$nxVcpkg does not exist (vcpkg): install_requirements.ps1 -From 4, or set NX_VCPKG." }
$env:VCPKG_ROOT = $nxVcpkg                  # the VS console overwrites it with its internal copy
Remove-Item Env:NX_DATA_ROOT -ErrorAction SilentlyContinue   # each exe takes its data root from game.cpp (kInfo)

$nxInfo = 'dev env:'
if (Get-Command clang-cl -ErrorAction SilentlyContinue) { $nxInfo += ' clang-cl ' + ((clang-cl --version)[0] -replace '.*version ', '') }
if (Get-Command cmake -ErrorAction SilentlyContinue) { $nxInfo += ' | cmake ' + ((cmake --version)[0] -replace 'cmake version ', '') }
if ($env:VULKAN_SDK) { $nxInfo += ' | Vulkan ' + (Split-Path $env:VULKAN_SDK -Leaf) }
Write-Host $nxInfo