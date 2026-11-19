r"""apply_linux_target.py - install_requirements / recomp_tsukihime -Target windows|linux on Linux (AP_LINUX_TARGET).

On Linux, step 9 of recomp_tsukihime.ps1 built only the Windows exe (clang-cl + xwin, played with Proton). With
-Target linux it builds a native x86_64 binary instead (framework side: apply_linux_build.py, AP_LINUX_BUILD):
- install_requirements.ps1 -Target linux (Linux only; windows is the default and does what it did):
  no xwin and no Microsoft license; step 1 also installs the headers SDL3 needs (X11, Wayland, libdecor, PulseAudio,
  PipeWire, udev, EGL: pkg-config modules, one sudo, asking first); step 5 = FFmpeg 9.0 LGPL shared of BtbN for linux64
  (fixed SHA-256, _prepare/ffmpeg) + vcpkg opus libjpeg-turbo libpng zlib sdl3[core,vulkan,x11,wayland] for the stock
  x64-linux triplet (static) with VCPKG_BUILD_TYPE release (_prepare/triplets-linux) and, for sdl3, PipeWire/PulseAudio/
  udev/libdecor/X11 extensions back on (its port turns them off; v2, 2026-11-18). -Target is passed to recomp.
- recomp_tsukihime.ps1 -Target linux: CMake in framework/build/<game>/linux (clang/clang++ + lld of the pinned LLVM,
  vcpkg x64-linux, the BtbN FFmpeg); the binary <game> (no extension) goes to framework/build/<game>/, next to
  <game>.exe: both can be there, and the game finds data/ the same way.
- dev_env.ps1: no xwin warning with -Target linux (NX_TARGET).
On Windows nothing changes (-Target linux is refused there). Functions that other patchers own (AskFirst, the Linux
Step2, FfmpegPrebuilt) are not edited: with -Target linux they are wrapped or redefined after their definition.
"""
from _patch import run

MARK = "AP_LINUX_TARGET"
INST = "../tools/install_requirements.ps1"
LIN = "../tools/linux/install_requirements_linux.ps1"
RECO = "../tools/recomp_tsukihime.ps1"
DEV = "../tools/dev_env.ps1"

ONLINUX = r'''$OnLinux = [bool]((Test-Path variable:IsLinux) -and $IsLinux)   # PowerShell 7 on Linux (in 5.1 the variable does not exist)
'''

EDITS = [
    # ------------------------------------------------------------------------------------ install_requirements.ps1
    (INST,
     r'''    [ValidateSet('auto', 'xwin', 'vs')][string]$Toolchain = 'auto',
    [string]$Chain = ''
)
''',
     r'''    [ValidateSet('auto', 'xwin', 'vs')][string]$Toolchain = 'auto',
    [string]$Chain = '',
    # AP_LINUX_TARGET (apply_linux_target.py), Linux only: what step 9 of recomp builds. windows (the default) = the exe,
    # cross-compiled with clang-cl + xwin and played with Proton; linux = a native x86_64 binary (no xwin, no Microsoft license)
    [ValidateSet('windows', 'linux')][string]$Target = 'windows'
)
'''),
    (INST, ONLINUX,
     ONLINUX + r'''# AP_LINUX_TARGET: -Target linux = native Linux build: FFmpeg of BtbN for linux64 and vcpkg x64-linux (static, Release
# only), no xwin. The FFmpeg folder has another name than the Windows one: both can be in _prepare/ffmpeg.
$NativeLinux = $OnLinux -and $Target -eq 'linux'
if ($Target -eq 'linux' -and -not $OnLinux) { Write-Host 'ERROR: -Target linux is for Linux (tools/linux/install_requirements_linux.sh)' -ForegroundColor Red; exit 1 }
if ($NativeLinux) {
    # same BtbN autobuild and FFmpeg version as Windows; SHA-256 of its checksums.sha256 (also measured on the file)
    $FfmpegUrl = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n9.0.2-17-g2a571b6068-linux64-lgpl-shared-9.0.tar.xz'
    $FfmpegSha256 = '7A1BA2AF36E5BEEB98151E766A903B1D115F2206A86571F17F873CD7292C424F'
    $FfmpegDirName = 'ffmpeg-n9.0.2-17-g2a571b6068-linux64-lgpl-shared-9.0'
    # SDL3 with its Vulkan surface (SDL_Vulkan_*), X11 and Wayland; without the default ibus feature (it builds dbus)
    $VcpkgPorts = @('opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3[core,vulkan,x11,wayland]')
}
function VcpkgTriplet { if ($NativeLinux) { 'x64-linux' } else { 'x64-windows' } }
# The overlay triplet of -Target linux (_prepare/triplets-linux/x64-linux.cmake): the stock x64-linux (static) with Release
# only and, for sdl3, back on what its port turns off on purpose ("prevent host-installed Unix libraries from changing
# SDL's capabilities"): without PipeWire/PulseAudio SDL has no audio device at all (ALSA is not built either), without
# udev no controller hotplug, without libdecor no window frame on GNOME Wayland, without the X11 extensions no
# fullscreen modes, raw mouse or screensaver control. All of them are loaded at run time (SDL_DEPS_SHARED). The triplet
# options come after the port's ones: they win.
function NativeTripletText {
    $base = PathOf $Vcpkg 'triplets/x64-linux.cmake'
    if (-not (Test-Path -LiteralPath $base)) { Fail "$base does not exist" }
    $sdl = @('SDL_PIPEWIRE', 'SDL_PULSEAUDIO', 'SDL_LIBUDEV', 'SDL_WAYLAND_LIBDECOR', 'SDL_X11_XCURSOR', 'SDL_X11_XDBE', 'SDL_X11_XFIXES',
             'SDL_X11_XINPUT', 'SDL_X11_XRANDR', 'SDL_X11_XSHAPE', 'SDL_X11_XSCRNSAVER', 'SDL_X11_XSYNC', 'SDL_X11_XTEST') | ForEach-Object { "-D$_=ON" }
    return [IO.File]::ReadAllText($base).TrimEnd() + "`n`n# tsukihime (install_requirements.ps1 -Target linux): Release only`nset(VCPKG_BUILD_TYPE release)`n" +
           "# sdl3: audio (PipeWire, PulseAudio), udev, libdecor and the X11 extensions, which the port turns off`n" +
           "if(PORT STREQUAL `"sdl3`")`n    list(APPEND VCPKG_CMAKE_CONFIGURE_OPTIONS $($sdl -join ' '))`nendif()`n"
}
'''),
    (INST,
     r'''    foreach ($l in $r.Output) { if ($l -match '^([a-z0-9-]+)(\[[^\]]*\])?:x64-windows\s') { $have[$Matches[1]] = 1 } }
    return @($VcpkgPorts | Where-Object { -not $have.ContainsKey($_) })
''',
     r'''    # AP_LINUX_TARGET: the triplet of -Target; a port asked with [features] also needs each one (vcpkg list: a line per feature)
    $tri = [regex]::Escape((VcpkgTriplet))
    foreach ($l in $r.Output) { if ($l -match "^([a-z0-9-]+)(\[[^\]]*\])?:$tri\s") { $have[$Matches[1] + $Matches[2]] = 1; $have[$Matches[1]] = 1 } }
    return @($VcpkgPorts | Where-Object {
        $name = $_ -replace '\[.*$', ''
        $feats = @(if ($_ -match '\[(.*)\]') { $Matches[1] -split ',' | Where-Object { $_ -and $_ -ne 'core' } })
        (-not $have.ContainsKey($name)) -or (@($feats | Where-Object { -not $have.ContainsKey("$name[$_]") }).Count -gt 0)
    })
'''),
    (INST,
     r'''    $files = @('include\libavcodec\avcodec.h', 'include\libavformat\avformat.h') + @($FfmpegLibs | ForEach-Object { "lib\$_.lib" }) + @($FfmpegDlls | ForEach-Object { "bin\$_" })
''',
     r'''    $files = @('include\libavcodec\avcodec.h', 'include\libavformat\avformat.h') + @($FfmpegLibs | ForEach-Object { "lib\$_.lib" }) + @($FfmpegDlls | ForEach-Object { "bin\$_" })
    if ($NativeLinux) { $files = @('include\libavcodec\avcodec.h', 'include\libavformat\avformat.h') + @($FfmpegLibs | ForEach-Object { "lib\lib$_.so" }) }   # AP_LINUX_TARGET
'''),
    (INST,
     r'''    $zip = PathOf $parent 'ffmpeg_prebuilt.zip'
''',
     r'''    $zip = PathOf $parent $(if ($NativeLinux) { 'ffmpeg_prebuilt.tar.xz' } else { 'ffmpeg_prebuilt.zip' })   # AP_LINUX_TARGET
'''),
    (INST,
     r'''        Unzip $zip $tmp   # AP_INSTALL_IO
''',
     r'''        if ($NativeLinux) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null; Run 'tar' @('-xJf', $zip, '-C', $tmp) 'tar (FFmpeg)' }   # AP_LINUX_TARGET
        else { Unzip $zip $tmp }   # AP_INSTALL_IO
'''),
    (INST,
     r'''    if (-not $missing.Count) { Info "x64-windows libraries present: $($VcpkgPorts -join ', ')"; $script:Done.Add('vcpkg libraries'); return }
''',
     r'''    if (-not $missing.Count) { Info "$(VcpkgTriplet) libraries present: $($VcpkgPorts -join ', ')"; $script:Done.Add('vcpkg libraries'); return }   # AP_LINUX_TARGET
'''),
    (INST,
     r'''    $a = @('install') + $missing + @('--triplet', 'x64-windows', '--clean-after-build')
''',
     r'''    $a = @('install') + $missing + @('--triplet', (VcpkgTriplet), '--clean-after-build')   # AP_LINUX_TARGET
'''),
    (INST,
     r'''    if ($OnLinux) {
        # Cross build: x64-windows overlay triplet with the clang-cl + xwin toolchain (TripletLinux, in
''',
     r'''    if ($NativeLinux) {
        # AP_LINUX_TARGET: native: the stock x64-linux triplet of vcpkg (static libraries) with VCPKG_BUILD_TYPE release, in
        # its own overlay folder (the cross build keeps _prepare/triplets, whose host triplet is the stock x64-linux)
        if ($VcpkgWithDebug) { Warn '-VcpkgWithDebug is ignored with -Target linux (Release only)' }
        $ov = PathOf $WorkDir 'triplets-linux'
        New-Item -ItemType Directory -Path $ov -Force | Out-Null
        [IO.File]::WriteAllText((PathOf $ov 'x64-linux.cmake'), (NativeTripletText), (New-Object Text.UTF8Encoding($false)))
        $a += @("--overlay-triplets=$ov", '--host-triplet', 'x64-linux')
    } elseif ($OnLinux) {
        # Cross build: x64-windows overlay triplet with the clang-cl + xwin toolchain (TripletLinux, in
'''),
    (INST,
     r'''    if ($OnLinux) { $a += @('-XwinCrt', $XwinCrt, '-XwinSdk', $XwinSdk) }
''',
     r'''    if ($OnLinux) { $a += @('-XwinCrt', $XwinCrt, '-XwinSdk', $XwinSdk) }
    if ($OnLinux) { $a += @('-Target', $Target) }   # AP_LINUX_TARGET
'''),
    (INST,
     r'''    if ($TrimXci) { $a += '-TrimXci' }   # AP_ROMFS_DIRECT
''',
     r'''    if ($TrimXci) { $a += '-TrimXci' }   # AP_ROMFS_DIRECT
    if ($OnLinux) { $a += @('-Target', $Target) }   # AP_LINUX_TARGET
'''),
    (INST,
     r'''        $exe = PathOf $Framework "build/$g/$g.exe"
''',
     r'''        $exe = PathOf $Framework "build/$g/$g$(if ($NativeLinux) { '' } else { '.exe' })"   # AP_LINUX_TARGET
'''),
    (INST,
     r"""# AP_QUICKEDIT (apply_quickedit_off.py): QuickEdit off while the script runs, so a click in the console does not pause it.
""",
     r"""# AP_LINUX_TARGET: -Target linux needs no Windows CRT/SDK, so the questions asked first (AskFirst) leave out its
# Microsoft license: for that call only it is taken as not needed (XWIN_ACCEPT_LICENSE); nothing of xwin is downloaded.
if ($NativeLinux) {
    $script:AskFirstBase = ${function:AskFirst}
    function AskFirst {
        $prev = $env:XWIN_ACCEPT_LICENSE
        $env:XWIN_ACCEPT_LICENSE = 'true'
        try { & $script:AskFirstBase } finally { $env:XWIN_ACCEPT_LICENSE = $prev }
    }
    # vcpkg does not build again a port that is already installed when the triplet changes: if the overlay triplet that
    # was used is not the current one (NativeTripletText; e.g. an sdl3 built without audio before 2026-11-18), sdl3 is
    # removed first so step 5 builds it again with the current options.
    $script:Step5Base = ${function:Step5}
    function Step5 {
        $tri = PathOf $WorkDir 'triplets-linux/x64-linux.cmake'
        $have = @()
        if (Test-Path -LiteralPath (VcpkgExe)) { $have = @((RunCapture (VcpkgExe) @('list', 'sdl3')).Output | Where-Object { $_ -match '^sdl3(\[[^\]]*\])?:x64-linux\s' }) }
        $old = if (Test-Path -LiteralPath $tri) { [IO.File]::ReadAllText($tri) } else { '' }
        if ($have.Count -and $old -ne (NativeTripletText)) {
            if ($Check) { Problem 'sdl3 (x64-linux) was built with other options (no audio, udev, ...): step 5 builds it again' }
            else {
                Info 'sdl3 (x64-linux) was built with other options than the current triplet (no audio, udev, ...): removing it so it is built again'
                Run (VcpkgExe) @('remove', 'sdl3:x64-linux', '--recurse') 'vcpkg remove sdl3'
            }
        }
        & $script:Step5Base
    }
}

# AP_QUICKEDIT (apply_quickedit_off.py): QuickEdit off while the script runs, so a click in the console does not pause it.
"""),
    # ------------------------------------------------------------------------------------ linux/install_requirements_linux.ps1
    (LIN,
     r'''    python3 = @{ apt = 'python3'; dnf = 'python3'; pacman = 'python' }
}
''',
     r'''    python3 = @{ apt = 'python3'; dnf = 'python3'; pacman = 'python' }
}
# AP_LINUX_TARGET: -Target linux also builds SDL3 (vcpkg, step 5), which needs the headers of the window systems, audio
# servers, udev and EGL of the distribution: "pc:<module>" = a pkg-config module (the other keys are commands).
# Fedora and Arch names: not tried yet.
if ($NativeLinux) {
    $NativePkgs = [ordered]@{
        'wayland-scanner' = @{ apt = 'libwayland-bin'; dnf = 'wayland-devel'; pacman = 'wayland' }
        'pc:x11' = @{ apt = 'libx11-dev'; dnf = 'libX11-devel'; pacman = 'libx11' }
        'pc:xext' = @{ apt = 'libxext-dev'; dnf = 'libXext-devel'; pacman = 'libxext' }
        'pc:xrandr' = @{ apt = 'libxrandr-dev'; dnf = 'libXrandr-devel'; pacman = 'libxrandr' }
        'pc:xcursor' = @{ apt = 'libxcursor-dev'; dnf = 'libXcursor-devel'; pacman = 'libxcursor' }
        'pc:xfixes' = @{ apt = 'libxfixes-dev'; dnf = 'libXfixes-devel'; pacman = 'libxfixes' }
        'pc:xi' = @{ apt = 'libxi-dev'; dnf = 'libXi-devel'; pacman = 'libxi' }
        'pc:xscrnsaver' = @{ apt = 'libxss-dev'; dnf = 'libXScrnSaver-devel'; pacman = 'libxss' }
        'pc:xtst' = @{ apt = 'libxtst-dev'; dnf = 'libXtst-devel'; pacman = 'libxtst' }   # SDL 3.4 requires XTest
        'pc:xkbcommon' = @{ apt = 'libxkbcommon-dev'; dnf = 'libxkbcommon-devel'; pacman = 'libxkbcommon' }
        'pc:wayland-client' = @{ apt = 'libwayland-dev'; dnf = 'wayland-devel'; pacman = 'wayland' }
        'pc:wayland-egl' = @{ apt = 'libwayland-dev'; dnf = 'wayland-devel'; pacman = 'wayland' }
        'pc:wayland-protocols' = @{ apt = 'wayland-protocols'; dnf = 'wayland-protocols-devel'; pacman = 'wayland-protocols' }
        'pc:libdecor-0' = @{ apt = 'libdecor-0-dev'; dnf = 'libdecor-devel'; pacman = 'libdecor' }
        'pc:egl' = @{ apt = 'libegl-dev'; dnf = 'libglvnd-devel'; pacman = 'libglvnd' }
        'pc:libpulse' = @{ apt = 'libpulse-dev'; dnf = 'pulseaudio-libs-devel'; pacman = 'libpulse' }
        'pc:libpipewire-0.3' = @{ apt = 'libpipewire-0.3-dev'; dnf = 'pipewire-devel'; pacman = 'libpipewire' }
        'pc:libudev' = @{ apt = 'libudev-dev'; dnf = 'systemd-devel'; pacman = 'systemd-libs' }
    }
    foreach ($k in $NativePkgs.Keys) { $SystemPkgs[$k] = $NativePkgs[$k] }
}
'''),
    (LIN,
     r'''function MissingSystem { @($SystemPkgs.Keys | Where-Object { -not (InPath $_) }) }
''',
     r'''function MissingSystem {   # AP_LINUX_TARGET: a "pc:<module>" key is a pkg-config module (headers of -Target linux)
    @($SystemPkgs.Keys | Where-Object {
        if ($_ -like 'pc:*') { (-not (InPath 'pkg-config')) -or (RunCapture 'pkg-config' @('--exists', $_.Substring(3))).Code -ne 0 } else { -not (InPath $_) }
    })
}
'''),
    (LIN,
     r'''$ToolSpecs['git'] = @{ Name = 'Git (from the system)'; Pin = ''; Rule = '>= 2.40'; Detect = 'DetectGit'; Meets = { param($v) $v -ge [version]'2.40' } }
''',
     r'''$ToolSpecs['git'] = @{ Name = 'Git (from the system)'; Pin = ''; Rule = '>= 2.40'; Detect = 'DetectGit'; Meets = { param($v) $v -ge [version]'2.40' } }
if ($NativeLinux) { $ToolSpecs.Remove('xwin') }   # AP_LINUX_TARGET: -Target linux does not use xwin (nor the Windows CRT/SDK)
function TarKeys { @($Tarballs.Keys | Where-Object { -not ($NativeLinux -and $_ -eq 'xwin') }) }   # AP_LINUX_TARGET
'''),
    (LIN,
     r'''    Info ('  {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { "OK ($(XwinRequest))" } else { "not present ($(XwinRequest))" }))
''',
     r'''    if ($NativeLinux) { Info ('  {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', 'not needed (-Target linux)') }   # AP_LINUX_TARGET
    else { Info ('  {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { "OK ($(XwinRequest))" } else { "not present ($(XwinRequest))" })) }
'''),
    (LIN,
     r'''    Write-Host ('     {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { 'OK' } else { 'MISSING' }))
''',
     r'''    if (-not $NativeLinux) { Write-Host ('     {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { 'OK' } else { 'MISSING' })) }   # AP_LINUX_TARGET
'''),
    (LIN,
     r'''    foreach ($ed in (Editions)) { Write-Host "     bash tools/linux/recomp_linux.sh -Edition $ed" }
''',
     r'''    foreach ($ed in (Editions)) { Write-Host "     bash tools/linux/recomp_linux.sh -Edition $ed$(if ($NativeLinux) { ' -Target linux' })" }   # AP_LINUX_TARGET
'''),
    (LIN,
     r"""# AP_DROP_UNUSED: the Linux step 7 (nstool built from source) removed: step 7 is the one of install_requirements.ps1
""",
     r"""# AP_LINUX_TARGET: step 2 of -Target linux: the same tarballs and parallel chains (llvm | misc) without xwin: no Windows
# CRT/SDK and no Microsoft license. Replaces Step2 (above) only with -Target linux.
if ($NativeLinux) {
    function Step2 {
        New-Item -ItemType Directory -Path $script:T -Force -ErrorAction SilentlyContinue | Out-Null
        if ($Check) {
            foreach ($k in (TarKeys)) {
                $e = Status $k
                if ($e.State -eq 'ok') { Info ('{0,-36} {1} OK ({2})' -f $Tarballs[$k].Name, $e.Det.Text, $e.Det.Path) }
                else { Problem "$($Tarballs[$k].Name) $($Tarballs[$k].Pin): not present in $(TarDir $k)" }
            }
            RefreshEnv
            return
        }
        $todo = @(@('llvm', 'misc') | Where-Object { ChainPending $_ })
        if (-not $todo.Count) { Info 'everything already in place' }
        elseif ($env:NX_SERIAL_INSTALL) { foreach ($c in $todo) { & $LinuxChains[$c] } }
        else {
            $main = Join-Path (Split-Path -Parent $PSScriptRoot) 'install_requirements.ps1'   # $PSCommandPath here would be this file
            $runs = @()
            foreach ($c in $todo) {
                $out = Join-Path $WorkDir "chain_${c}_$(Stamp).log"
                $a = @('-NoProfile', '-File', $main, '-Chain', $c, '-NonInteractive', '-Root', $Root, '-Edition', $Edition,
                       '-From', "$From", '-To', "$To", '-Target', $Target)
                $p = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru `
                     -RedirectStandardOutput $out -RedirectStandardError "$out.err"
                $null = $p.Handle
                $runs += @{ C = $c; P = $p; Out = $out }
            }
            Info "in parallel: $($todo -join ', ') (the output of each one is shown when it finishes)"
            $failed = @()
            foreach ($r in $runs) {
                $r.P.WaitForExit()
                $code = -1; try { $code = $r.P.ExitCode } catch { }
                if ($null -eq $code) { $code = -1 }
                Write-Host "   -- $($r.C)$(if ($code -ne 0) { ' FAILED' })" -ForegroundColor $(if ($code -ne 0) { 'Red' } else { 'Gray' })
                foreach ($f in @($r.Out, "$($r.Out).err")) {
                    if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f | Where-Object { $_ -match '\S' } | ForEach-Object { Write-Host "      $_" }; Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
                }
                if ($code -ne 0) { $failed += $r.C }
            }
            if ($failed.Count) { Fail "step 2: $($failed -join ', ') failed (output above); run again: what is done is kept" }
        }
        foreach ($k in (TarKeys)) {
            $e = Status $k
            if ($e.State -ne 'ok') { Fail "$($Tarballs[$k].Name): not present after step 2" }
            $script:Done.Add("$($Tarballs[$k].Name) $($e.Det.Text)")
        }
        RefreshEnv
    }
}

# AP_DROP_UNUSED: the Linux step 7 (nstool built from source) removed: step 7 is the one of install_requirements.ps1
"""),
    # ------------------------------------------------------------------------------------ recomp_tsukihime.ps1
    (RECO,
     r'''    [ValidateRange(0, 512)][int]$Jobs = 0   # AP_RECOMP_PARALLEL: compile jobs of step 9 (0 = ninja's default, CPUs + 2)
)
''',
     r'''    [ValidateRange(0, 512)][int]$Jobs = 0,   # AP_RECOMP_PARALLEL: compile jobs of step 9 (0 = ninja's default, CPUs + 2)
    # AP_LINUX_TARGET (apply_linux_target.py), Linux only: step 9 builds the Windows exe (windows, the default: clang-cl +
    # xwin, played with Proton) or a native x86_64 binary (linux: framework/build/<game>/<game>, next to the exe)
    [ValidateSet('windows', 'linux')][string]$Target = 'windows'
)
'''),
    (RECO, ONLINUX,
     ONLINUX + r'''$NativeLinux = $OnLinux -and $Target -eq 'linux'   # AP_LINUX_TARGET
if ($Target -eq 'linux' -and -not $OnLinux) { throw '-Target linux is for Linux (tools/linux/recomp_linux.sh)' }
if ($OnLinux) { $env:NX_TARGET = $Target }   # AP_LINUX_TARGET: dev_env.ps1 does not ask for xwin with linux
'''),
    (RECO,
     r'''$BuildOther = Under $Framework "build\$($Editions[$V.Other].Game)"
''',
     r'''$BuildOther = Under $Framework "build\$($Editions[$V.Other].Game)"
# AP_LINUX_TARGET: -Target linux: CMake works in build\<game>\linux and the binary goes to build\<game>\ (NX_OUTPUT_DIR),
# next to <game>.exe (other names: no clash), so the game finds data\ the same way
$BinDir = $Build
$ExeName = if ($NativeLinux) { $V.Game } else { "$($V.Game).exe" }
if ($NativeLinux) { $Build = Under $Build 'linux'; $BuildOther = Under $BuildOther 'linux' }
'''),
    (RECO,
     r'''            if ($builds -and $OnLinux -and (-not $env:NX_XWIN -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment NX_XWIN or VCPKG_ROOT are still empty: step 9 may fail' }
''',
     r'''            if ($builds -and $NativeLinux -and -not $env:VCPKG_ROOT) { Warn 'after loading the environment VCPKG_ROOT is still empty: step 9 may fail' }   # AP_LINUX_TARGET
            elseif ($builds -and $OnLinux -and -not $NativeLinux -and (-not $env:NX_XWIN -or -not $env:VCPKG_ROOT)) { Warn 'after loading the environment NX_XWIN or VCPKG_ROOT are still empty: step 9 may fail' }
'''),
    (RECO,
     r'''            if ($OnLinux) {
                $vk = VulkanView
''',
     r'''            if ($OnLinux -and -not $NativeLinux) {   # AP_LINUX_TARGET: native: NX_VULKAN_SDK is VULKAN_SDK itself
                $vk = VulkanView
'''),
    (RECO,
     r'''            $a = @('-B', $Build, "-DNX_GAME=$($V.Game)", "-DNX_GEN_DIR=$gen")
''',
     r'''            if ($NativeLinux) { $other = $null }   # AP_LINUX_TARGET: a native build is always configured with the values below
            $a = @('-B', $Build, "-DNX_GAME=$($V.Game)", "-DNX_GEN_DIR=$gen")
'''),
    (RECO,
     r'''                if ($OnLinux) {
                    # Cross build: the tools/linux toolchain sets up clang-cl/lld-link/llvm-rc against the xwin CRT and
''',
     r'''                if ($NativeLinux) {
                    # AP_LINUX_TARGET: native Linux: clang/clang++ and lld of the LLVM of install_requirements (the folder of
                    # clang-cl), vcpkg x64-linux (static, Release only) and the BtbN FFmpeg for linux64 (NX_FFMPEG_ROOT, below)
                    $llvm = Split-Path -Parent $cc
                    foreach ($f in @("$vcpkg/scripts/buildsystems/vcpkg.cmake", "$vcpkg/installed/x64-linux/include/SDL3/SDL.h", "$llvm/clang++", "$llvm/ld.lld")) {
                        if (-not (Test-Path -LiteralPath $f)) { Fail "$f does not exist (tools/linux/install_requirements_linux.sh -Target linux)" }
                    }
                    if (-not $env:VULKAN_SDK -or -not (Test-Path -LiteralPath (Under $env:VULKAN_SDK 'include/vulkan/vulkan.h'))) { Fail 'VULKAN_SDK has no include/vulkan/vulkan.h (tools/dev_env.ps1; tools/linux/install_requirements_linux.sh)' }
                    if (-not (FfmpegPrebuilt)) { Fail 'the FFmpeg for Linux is missing in _prepare/ffmpeg (tools/linux/install_requirements_linux.sh -Target linux -From 5)' }
                    Info 'no previous CMakeCache: native Linux build (Ninja, Release, clang + lld, vcpkg x64-linux)'
                    $a += @('-G', 'Ninja', "-DCMAKE_TOOLCHAIN_FILE=$vcpkg/scripts/buildsystems/vcpkg.cmake", '-DVCPKG_TARGET_TRIPLET=x64-linux',
                            '-DCMAKE_BUILD_TYPE=Release', "-DCMAKE_C_COMPILER=$llvm/clang", "-DCMAKE_CXX_COMPILER=$llvm/clang++",
                            '-DCMAKE_LINKER_TYPE=LLD', "-DNX_VULKAN_SDK=$($env:VULKAN_SDK -replace '\\', '/')", "-DNX_OUTPUT_DIR=$($BinDir -replace '\\', '/')")
                } elseif ($OnLinux) {
                    # Cross build: the tools/linux toolchain sets up clang-cl/lld-link/llvm-rc against the xwin CRT and
'''),
    (RECO,
     r'''    $exe = Under $Build "$($V.Game).exe"
    if (-not (Test-Path -LiteralPath $exe)) { Fail "$exe does not appear" }
''',
     r'''    $exe = Under $BinDir $ExeName   # AP_LINUX_TARGET
    if (-not (Test-Path -LiteralPath $exe)) { Fail "$exe does not appear" }
'''),
    (RECO,
     r'''        $exe = Under $Build "$($V.Game).exe"
        $Summary['Exe']''',
     r'''        $exe = Under $BinDir $ExeName   # AP_LINUX_TARGET
        $Summary['Exe']'''),
    (RECO,
     r'''    @('both', 'tools\install_requirements.ps1', 'AP_INSTALL_YES', 'apply_install_yes.py', $false),
''',
     r'''    @('both', 'tools\install_requirements.ps1', 'AP_INSTALL_YES', 'apply_install_yes.py', $false),
    @('both', 'tools\recomp_tsukihime.ps1', 'AP_LINUX_TARGET', 'apply_linux_target.py', $false),
    @('both', 'tools\install_requirements.ps1', 'AP_LINUX_TARGET', 'apply_linux_target.py', $false),
    @('both', 'tools\linux\install_requirements_linux.ps1', 'AP_LINUX_TARGET', 'apply_linux_target.py', $false),
    @('both', 'tools\dev_env.ps1', 'AP_LINUX_TARGET', 'apply_linux_target.py', $false),
'''),
    (RECO,
     r"""# HUD button icons by device (AP_HUD_GLYPHS, hud_glyphs.cpp): <data>\hud from the romfs and its mod layers (only hashes
""",
     r"""# AP_LINUX_TARGET: with -Target linux the FFmpeg of step 9 is the BtbN build for linux64 (lib/libavcodec.so) that
# install_requirements.ps1 -Target linux leaves in _prepare/ffmpeg; this replaces FfmpegPrebuilt (above) only then.
if ($NativeLinux) {
    function FfmpegPrebuilt {
        $base = InRoot '_prepare\ffmpeg'
        if (-not (Test-Path -LiteralPath $base)) { return '' }
        $d = @(Get-ChildItem -LiteralPath $base -Directory | Where-Object { Test-Path -LiteralPath (Under $_.FullName 'lib/libavcodec.so') -PathType Leaf } | Sort-Object LastWriteTime -Descending)
        if (-not $d.Count) { return '' }
        return ($d[0].FullName -replace '\\', '/')
    }
}

# HUD button icons by device (AP_HUD_GLYPHS, hud_glyphs.cpp): <data>\hud from the romfs and its mod layers (only hashes
"""),
    # ------------------------------------------------------------------------------------ dev_env.ps1
    (DEV,
     r'''    if (-not (Test-Path -LiteralPath (Join-Path $env:NX_XWIN 'VC/Tools/MSVC'))) { Write-Warning "$env:NX_XWIN does not exist (Windows CRT/SDK): tools/linux/install_requirements_linux.sh -From 2" }
''',
     r'''    # AP_LINUX_TARGET: recomp_tsukihime.ps1 -Target linux (NX_TARGET=linux) does not use the Windows CRT/SDK
    if ($env:NX_TARGET -ne 'linux' -and -not (Test-Path -LiteralPath (Join-Path $env:NX_XWIN 'VC/Tools/MSVC'))) { Write-Warning "$env:NX_XWIN does not exist (Windows CRT/SDK): tools/linux/install_requirements_linux.sh -From 2" }
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
