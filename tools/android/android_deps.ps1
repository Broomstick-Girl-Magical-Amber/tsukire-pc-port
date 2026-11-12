# android_deps.ps1 - The libraries the game links on Android, built for arm64-android with vcpkg: FFmpeg (avcodec,
# avformat, swresample, swscale: the videos), opus (the music), libjpeg-turbo, libpng and zlib (the native loading of
# images and zlib). Static (.a), as the framework's Android build expects. Run it once per PC (and again only if the
# vcpkg pin changes); the result goes to <root>\_prepare\android-deps (include\ and lib\), where
# tools\android\build_apk.ps1 and the CMake of the framework (NX_ANDROID_DEPS) look for it.
#
#   .\tools\android\android_deps.ps1
#   .\tools\android\android_deps.ps1 -Vcpkg D:\vcpkg -Ndk <sdk>\ndk\28.2.13676358
#   Linux: bash tools/android/android_deps.sh
#
# vcpkg: -Vcpkg, else NX_VCPKG, else the usual one (Windows C:\dev\vcpkg, Linux ~/dev/vcpkg); if there is none it is
# cloned (git) at the commit install_requirements.ps1 uses into the tools folder (Windows %LOCALAPPDATA%\tsuki-tools\vcpkg,
# Linux ~/.local/tsuki-tools/vcpkg). FFmpeg for Android takes a while (vcpkg downloads its own MSYS2 on Windows).
# Nothing of the game is involved; these are the same open source libraries as on Windows.
param(
    [string]$Root,
    [string]$Vcpkg,
    [string]$Sdk,
    [string]$Ndk
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'android_common.ps1')

$VcpkgCommit = 'bf19794383e8fb95eba5aa553235bc8ec2271688'   # the same as install_requirements.ps1
$Ports = @('ffmpeg[core,avcodec,avformat,swresample,swscale]', 'opus', 'libjpeg-turbo', 'libpng', 'zlib')
$Triplet = 'arm64-android'

try {
    $Root = FindRoot $Root
    $dst = Under $Root '_prepare\android-deps'

    Step 'Android NDK'
    $sdkDir = $null
    if (-not $Ndk -and -not $env:ANDROID_NDK_HOME) { $sdkDir = FindSdk $Sdk }
    $ndkDir = FindNdk $sdkDir $Ndk
    $env:ANDROID_NDK_HOME = $ndkDir
    Info "NDK $ndkDir"

    Step 'vcpkg'
    $toolsDir = if ($env:NX_TOOLS_DIR) { $env:NX_TOOLS_DIR } elseif ($OnWindows) { Join-Path $env:LOCALAPPDATA 'tsuki-tools' } else { Join-Path $HOME '.local/tsuki-tools' }
    $cands = @($Vcpkg, $env:NX_VCPKG)
    if ($OnWindows) { $cands += 'C:\dev\vcpkg' } else { $cands += (Join-Path $HOME 'dev/vcpkg') }
    $cands += (Join-Path $toolsDir 'vcpkg')
    $vc = $null
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath (Join-Path $c "vcpkg$Exe"))) { $vc = $c; break } }
    if (-not $vc) {
        $vc = Join-Path $toolsDir 'vcpkg'
        if (Test-Path -LiteralPath $vc) { Fail "$vc exists without vcpkg$Exe (an interrupted clone?): set it aside by hand" }
        $git = Get-Command "git$Exe" -ErrorAction SilentlyContinue
        if (-not $git) { Fail 'no vcpkg found and no git to clone it: install git, or pass -Vcpkg <folder>' }
        Info "cloning vcpkg into $vc (commit $VcpkgCommit)"
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
        RunNative $git.Source @('clone', 'https://github.com/microsoft/vcpkg', $vc) 'git clone (vcpkg)'
        RunNative $git.Source @('-C', $vc, 'checkout', '--quiet', $VcpkgCommit) 'git checkout (vcpkg)'
        $boot = if ($OnWindows) { Join-Path $vc 'bootstrap-vcpkg.bat' } else { Join-Path $vc 'bootstrap-vcpkg.sh' }
        RunNative $boot @('-disableMetrics') 'vcpkg bootstrap'
    }
    Info "vcpkg $vc"

    Step "vcpkg install ($Triplet): $($Ports -join ', ')"
    $argv = @('install') + @($Ports | ForEach-Object { "${_}:$Triplet" }) + @('--triplet', $Triplet, '--clean-after-build')
    RunNative (Join-Path $vc "vcpkg$Exe") $argv 'vcpkg install'
    $inst = Join-Path (Join-Path $vc 'installed') $Triplet
    foreach ($f in @('include\libavcodec\avcodec.h', 'include\opus\opus.h', 'lib\libavcodec.a', 'lib\libopus.a', 'lib\libz.a')) {
        if (-not (Test-Path -LiteralPath (Under $inst $f))) { Fail "vcpkg finished but $(Under $inst $f) is missing" }
    }

    Step "copy to $dst"
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $inst 'include') -Destination $tmp -Recurse
    Copy-Item -LiteralPath (Join-Path $inst 'lib') -Destination $tmp -Recurse
    if (Test-Path -LiteralPath (Join-Path $inst 'share')) {   # licences of each library (copyright files)
        New-Item -ItemType Directory -Path (Join-Path $tmp 'licenses') -Force | Out-Null
        foreach ($d in @(Get-ChildItem -LiteralPath (Join-Path $inst 'share') -Directory)) {
            $cp = Join-Path $d.FullName 'copyright'
            if (Test-Path -LiteralPath $cp) { Copy-Item -LiteralPath $cp -Destination (Join-Path (Join-Path $tmp 'licenses') "$($d.Name).txt") }
        }
    }
    [IO.File]::WriteAllText((Join-Path $tmp 'SOURCE.txt'), "vcpkg $vc, triplet $Triplet, NDK $ndkDir`r`nports: $($Ports -join ' ')`r`n")
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
    Move-Item -LiteralPath $tmp -Destination $dst
    $mb = [math]::Round((@(Get-ChildItem -LiteralPath $dst -Recurse -File | Measure-Object -Property Length -Sum)[0].Sum) / 1MB, 1)
    Info "$dst ($mb MB)"
    Write-Host ''
    Write-Host "Done. Next: tools\android\build_apk.ps1 -Edition <japan|global>"
} catch {
    Write-Host ''
    Write-Host "ERROR in android_deps: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
