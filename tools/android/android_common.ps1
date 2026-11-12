# android_common.ps1 - Shared part of the Android scripts (build_apk.ps1, android_deps.ps1, phone_data.ps1,
# build_game.ps1, android_port.ps1): the port root, the editions, where the Android SDK, NDK, build-tools, android.jar,
# the JDK, CMake/Ninja and the Vulkan headers are, and adb (found, or downloaded with -GetAdb) with a push that reports
# its progress. Loaded with a dot (. $PSScriptRoot\android_common.ps1); it does not run on its own.
# Windows PowerShell 5.1 and PowerShell 7 (Linux). Apart from adb with -GetAdb nothing is installed here: what is
# missing is reported with where to get it (Android Studio installs the SDK; its SDK Manager adds the NDK).

$OnWindows = [Environment]::OSVersion.Platform -eq 'Win32NT'
$Sep = [IO.Path]::DirectorySeparatorChar
$Exe = if ($OnWindows) { '.exe' } else { '' }
$Bat = if ($OnWindows) { '.bat' } else { '' }

function Info([string]$t) { Write-Host "   $t" }
function Warn([string]$t) { Write-Host "   WARNING: $t" -ForegroundColor Yellow }
function Fail([string]$t) { throw $t }
function Step([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Under([string]$base, [string]$rel) { Join-Path $base ($rel -replace '[\\/]', $Sep) }

# The port root: -Root, or the folder two above this script (<root>\tools\android).
function FindRoot([string]$given) {
    $r = if ($given) { $given } else { Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
    $r = [IO.Path]::GetFullPath($r).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath (Under $r 'framework\CMakeLists.txt'))) { Fail "$r is not the port root (no framework\CMakeLists.txt): pass -Root <folder>" }
    return $r
}

# The app: one APK for both editions, with nothing of the game inside (phase 2).
$AndroidPackage = 'io.github.tsukire.android'
$AndroidLabel = 'Tsukihime'

# The two editions: the game of the framework, its data folder, the mods folder.
$AndroidEditions = @{
    japan  = @{ Game = 'tsukihime'; Data = 'data\tsuki'; Mods = 'mods\japan'; ProgramId = '01001DC01486A000' }
    global = @{ Game = 'tsukihime_global'; Data = 'data\tsuki_global'; Mods = 'mods\global'; ProgramId = '010057A01E4D4000' }
}

# A version number "1.2.3" or "28.2.13676358" as something that sorts.
function VersionKey([string]$name) {
    $parts = @(($name -replace '^[^0-9]*', '') -split '[.-]' | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) { $parts += 0 }
    return ('{0:D6}{1:D6}{2:D9}{3:D9}' -f $parts[0], $parts[1], $parts[2], $parts[3])
}

# Newest subfolder of $dir (by version in its name) that passes $test; $null if none.
function NewestDir([string]$dir, [scriptblock]$test) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return $null }
    $c = @(Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue | Where-Object { & $test $_.FullName } |
        Sort-Object { VersionKey $_.Name } -Descending)
    if ($c.Count) { return $c[0].FullName }
    return $null
}

# Where an Android SDK may be: -Sdk, ANDROID_HOME, ANDROID_SDK_ROOT, Android Studio's default and C:\Android\sdk
# (the folder the first Android tests of this port used).
function SdkCandidates([string]$given) {
    $c = @($given, $env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)
    if ($OnWindows) { $c += @((Join-Path $env:LOCALAPPDATA 'Android\Sdk'), 'C:\Android\sdk', 'C:\Android\Sdk') }
    else { $c += @((Join-Path $HOME 'Android/Sdk'), (Join-Path $HOME 'Android/sdk')) }
    $out = @()
    foreach ($x in $c) {
        if ($x -and (Test-Path -LiteralPath $x -PathType Container)) {
            $f = [IO.Path]::GetFullPath($x).TrimEnd('\', '/')
            if (-not ($out | Where-Object { $_ -eq $f })) { $out += $f }
        }
    }
    return $out
}

# The newest NDK (with its CMake toolchain) in an SDK folder: ndk\<version> or the old ndk-bundle.
function NdkIn([string]$sdk) {
    $n = NewestDir (Under $sdk 'ndk') { param($d) Test-Path -LiteralPath (Under $d 'build\cmake\android.toolchain.cmake') }
    if (-not $n -and (Test-Path -LiteralPath (Under $sdk 'ndk-bundle\build\cmake\android.toolchain.cmake'))) { $n = Under $sdk 'ndk-bundle' }
    return $n
}

# The SDK to use: the first candidate with platforms\ that also has an NDK; else the first with platforms\.
function FindSdk([string]$given) {
    $all = @(SdkCandidates $given)
    $withPlatforms = @($all | Where-Object { Test-Path -LiteralPath (Under $_ 'platforms') -PathType Container })
    foreach ($c in $withPlatforms) { if (NdkIn $c) { return $c } }
    if ($withPlatforms.Count) { return $withPlatforms[0] }
    Fail ('Android SDK not found (looked in: ' + ($(if ($all.Count) { $all -join ', ' } else { 'ANDROID_HOME, the default folders' })) + '). ' +
          'Install Android Studio and open it once (it downloads the SDK), or pass -Sdk <folder>; then the SDK Manager: ' +
          '"Android SDK Build-Tools", "NDK (Side by side)", "Android SDK Platform 35".')
}

function FindNdk([string]$sdk, [string]$given) {
    foreach ($c in @($given, $env:ANDROID_NDK_HOME, $env:ANDROID_NDK_ROOT)) {
        if ($c -and (Test-Path -LiteralPath (Under $c 'build\cmake\android.toolchain.cmake'))) { return [IO.Path]::GetFullPath($c) }
    }
    if ($given) { Fail "-Ndk $given has no build\cmake\android.toolchain.cmake (it must be the NDK folder itself, e.g. <sdk>\ndk\28.2.13676358)" }
    $looked = @()
    $sdks = @()
    if ($sdk) { $sdks += $sdk }
    $sdks += @(@(SdkCandidates '') | Where-Object { $_ -ne $sdk })
    foreach ($s in $sdks) {
        $looked += (Under $s 'ndk')
        $n = NdkIn $s
        if ($n) {
            $major = 0
            [void][int]::TryParse(((Split-Path -Leaf $n) -replace '\..*$', ''), [ref]$major)
            if ($major -and $major -lt 27) { Warn "NDK $(Split-Path -Leaf $n) is old (tested with r27/r28): if the build fails, install a newer one" }
            if ($sdk -and $s -ne $sdk) { Info "NDK found in another SDK folder: $s" }
            return $n
        }
    }
    Fail ("no NDK found (looked in: $($looked -join ', ')). Android Studio > Settings > Languages & Frameworks > Android SDK > " +
          'SDK Tools > "NDK (Side by side)" (r27 or newer), or pass -Ndk <the NDK folder>')
}

# The NDK's own LLVM bin folder (llvm-strip), for this host.
function NdkLlvmBin([string]$ndk) {
    $b = NewestDir (Under $ndk 'toolchains\llvm\prebuilt') { param($d) Test-Path -LiteralPath (Under $d "bin\llvm-strip$Exe") }
    if (-not $b) { Fail "the NDK $ndk has no toolchains/llvm/prebuilt/<host>/bin/llvm-strip" }
    return (Under $b 'bin')
}

function FindBuildTools([string]$sdk) {
    $b = NewestDir (Under $sdk 'build-tools') { param($d) (Test-Path -LiteralPath (Under $d "aapt2$Exe")) -and (Test-Path -LiteralPath (Under $d "d8$Bat")) }
    if (-not $b) { Fail "no build-tools with aapt2 and d8 in $(Under $sdk 'build-tools') (SDK Manager > SDK Tools > Android SDK Build-Tools)" }
    return $b
}

function FindPlatformJar([string]$sdk) {
    $p = NewestDir (Under $sdk 'platforms') { param($d) (Test-Path -LiteralPath (Under $d 'android.jar')) -and ((VersionKey (Split-Path -Leaf $d)) -ge (VersionKey '30')) }
    if (-not $p) { Fail "no android.jar of API 30 or newer in $(Under $sdk 'platforms') (SDK Manager > SDK Platforms > Android 15 (API 35))" }
    return (Under $p 'android.jar')
}

# JAVA_HOME and the PATH for d8 and apksigner (they look for java there).
function UseJdk([string]$jdk) {
    $env:JAVA_HOME = $jdk
    $env:PATH = (Join-Path $jdk 'bin') + [IO.Path]::PathSeparator + $env:PATH
    return $jdk
}

# A JDK (javac + java): JAVA_HOME, Android Studio's own (jbr), or the PATH. Sets JAVA_HOME for d8/apksigner.
function FindJdk {
    $cands = @($env:JAVA_HOME)
    if ($OnWindows) {
        $cands += (Join-Path $env:ProgramFiles 'Android\Android Studio\jbr')
        $cands += @(Get-ChildItem -Path (Join-Path $env:ProgramFiles 'Microsoft\jdk-*'), (Join-Path $env:ProgramFiles 'Java\jdk-*'), (Join-Path $env:ProgramFiles 'Eclipse Adoptium\jdk-*') -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object { $_.FullName })
    } else {
        $cands += @((Join-Path $HOME 'android-studio/jbr'), '/opt/android-studio/jbr', '/usr/local/android-studio/jbr', '/snap/android-studio/current/jbr')
    }
    foreach ($c in $cands) {
        if ($c -and (Test-Path -LiteralPath (Under $c "bin\javac$Exe"))) { return (UseJdk ([IO.Path]::GetFullPath($c))) }
    }
    $j = Get-Command "javac$Exe" -ErrorAction SilentlyContinue
    if ($j) {
        return (UseJdk (Split-Path -Parent (Split-Path -Parent $j.Source)))
    }
    Fail 'no JDK (javac) found: Android Studio brings one (jbr), or set JAVA_HOME'
}

# CMake (3.25 or newer: the framework's CMakeLists asks for it) and Ninja: those of the PC install
# (_prepare\toolchain.json), the PATH, or the SDK's own (SDK Manager > SDK Tools > CMake: 3.31 works, 3.22 is too old).
function CmakeVersionOk([string]$cmake) {
    try {
        $v = (& $cmake --version 2>$null | Select-Object -First 1) -replace '^cmake version\s+', ''
        return ((VersionKey $v) -ge (VersionKey '3.25'))
    } catch { return $false }
}
function FindCmakeNinja([string]$sdk, [string]$root) {
    $cm = @(); $nj = @()
    $tc = Under $root '_prepare\toolchain.json'
    if (Test-Path -LiteralPath $tc) {
        try {
            $j = Get-Content -LiteralPath $tc -Raw | ConvertFrom-Json
            if ($j.PSObject.Properties['cmake_bin']) { $cm += (Join-Path "$($j.cmake_bin)" "cmake$Exe") }
            if ($j.PSObject.Properties['ninja_dir']) { $nj += (Join-Path "$($j.ninja_dir)" "ninja$Exe") }
        } catch { }
    }
    $c = Get-Command "cmake$Exe" -ErrorAction SilentlyContinue
    if ($c) { $cm += $c.Source }
    $n = Get-Command "ninja$Exe" -ErrorAction SilentlyContinue
    if ($n) { $nj += $n.Source }
    if ($sdk -and (Test-Path -LiteralPath (Under $sdk 'cmake') -PathType Container)) {
        foreach ($d in @(Get-ChildItem -LiteralPath (Under $sdk 'cmake') -Directory | Sort-Object { VersionKey $_.Name } -Descending)) {
            $cm += (Under $d.FullName "bin\cmake$Exe"); $nj += (Under $d.FullName "bin\ninja$Exe")
        }
    }
    $r = @{ Cmake = $null; Ninja = $null }
    foreach ($x in $cm) { if ((Test-Path -LiteralPath $x) -and (CmakeVersionOk $x)) { $r.Cmake = $x; break } }
    foreach ($x in $nj) { if (Test-Path -LiteralPath $x) { $r.Ninja = $x; break } }
    if (-not $r.Cmake) { Fail 'no CMake 3.25 or newer (the one of install_requirements, or SDK Manager > SDK Tools > CMake 3.31)' }
    if (-not $r.Ninja) { Fail 'ninja not found (SDK Manager > SDK Tools > CMake brings it, or the one of install_requirements)' }
    return $r
}

# The Vulkan headers: the runtime needs recent ones (vk_gen.h names functions of Vulkan 1.4.35x), newer than the NDK's.
# VULKAN_SDK (Include\ or include\), _prepare\toolchain.json "vulkan" (the prebuilt pack), the machine's VULKAN_SDK.
# Returns a folder X with X/Include/vulkan/vulkan.h (the CMakeLists adds "${NX_VULKAN_SDK}/Include"); on Linux, where
# the SDK has include\ in lowercase, a view <root>/_prepare/android/vulkan-sdk/Include -> include.
function FindVulkanSdk([string]$root) {
    $cands = @($env:VULKAN_SDK)
    $tc = Under $root '_prepare\toolchain.json'
    if (Test-Path -LiteralPath $tc) {
        try { $j = Get-Content -LiteralPath $tc -Raw | ConvertFrom-Json; if ($j.PSObject.Properties['vulkan']) { $cands += "$($j.vulkan)" } } catch { }
    }
    if ($OnWindows) { $cands += [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine') }
    else { $cands += @(Get-ChildItem -Path (Join-Path $HOME '.local/tsuki-tools/vulkan-*/x86_64') -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
    foreach ($c in $cands) {
        if (-not $c) { continue }
        if (Test-Path -LiteralPath (Under $c 'Include\vulkan\vulkan.h') -PathType Leaf) {
            if ($OnWindows -or (Test-Path -LiteralPath (Join-Path $c 'Include') -PathType Container)) { return ($c -replace '\\', '/') }
        }
        $inc = Join-Path $c 'include'
        if (-not $OnWindows -and (Test-Path -LiteralPath (Join-Path $inc 'vulkan/vulkan.h') -PathType Leaf)) {
            $view = Under $root '_prepare/android/vulkan-sdk'
            $link = Join-Path $view 'Include'
            $it = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
            if ($it) {
                if ($it.LinkType -ne 'SymbolicLink' -or "$($it.Target)" -ne $inc) { Fail "$link exists and is not a link to ${inc}: set it aside by hand" }
            } else {
                New-Item -ItemType Directory -Path $view -Force | Out-Null
                New-Item -ItemType SymbolicLink -Path $link -Target $inc | Out-Null
                Info "Vulkan headers: $link -> $inc"
            }
            return $view
        }
    }
    Fail ('no Vulkan headers (VULKAN_SDK with Include\vulkan\vulkan.h). The PC install leaves them in the prebuilt pack ' +
          '(_prepare\toolchain.json) or the Vulkan SDK; the NDK ones are too old for the runtime.')
}

# The LLVM clang++ of the PC install (not the NDK's): -Clang, NX_LLVM, _prepare\toolchain.json, the tools folder,
# %ProgramFiles%\LLVM, the PATH. It must have the AArch64 target and ld.lld next to it (the official LLVM builds do).
function FindLlvmClang([string]$root, [string]$given) {
    $cands = @($given)
    if ($env:NX_LLVM) { $cands += (Join-Path $env:NX_LLVM "clang++$Exe") }
    $tc = Under $root '_prepare\toolchain.json'
    if (Test-Path -LiteralPath $tc) {
        try { $j = Get-Content -LiteralPath $tc -Raw | ConvertFrom-Json; if ($j.PSObject.Properties['llvm_bin']) { $cands += (Join-Path "$($j.llvm_bin)" "clang++$Exe") } } catch { }
    }
    if ($OnWindows) {
        $cands += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'tsuki-tools\llvm-*\bin\clang++.exe') -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
        $cands += (Join-Path $env:ProgramFiles 'LLVM\bin\clang++.exe')
    } else {
        $cands += @(Get-ChildItem -Path (Join-Path $HOME '.local/tsuki-tools/llvm-*/bin/clang++') -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    }
    $c = Get-Command "clang++$Exe" -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    foreach ($x in $cands) {
        if ($x -and (Test-Path -LiteralPath $x) -and ($x -notmatch '[\\/]ndk[\\/]')) {
            if (-not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $x) "ld.lld$Exe"))) { continue }
            return $x
        }
    }
    Fail 'no LLVM clang++ with ld.lld found (the one tools\install_requirements.ps1 installs; or -Clang <path to clang++>)'
}

# Fingerprint of the headers the game's recompiled code is compiled against (framework\runtime\include\nx\*.h): the
# prebuilt runtime of the Android pack must come from the same ones (the CPU struct and the tables are shared).
function HeadersFingerprint([string]$root) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $all = New-Object IO.MemoryStream
    foreach ($f in @(Get-ChildItem -LiteralPath (Under $root 'framework\runtime\include\nx') -Filter '*.h' | Sort-Object Name)) {
        $text = [IO.File]::ReadAllText($f.FullName).Replace("`r", '')
        $b = [Text.Encoding]::UTF8.GetBytes($f.Name + "`n" + $text + "`n")
        $all.Write($b, 0, $b.Length)
    }
    return (-join ($sha.ComputeHash($all.ToArray()) | ForEach-Object { $_.ToString('x2') }))
}

function FileSha256([string]$path) { return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }

# adb's output as text, without failing on what it writes to stderr ("* daemon not running; starting now", which
# Windows PowerShell 5.1 turns into an error with ErrorActionPreference Stop).
function AdbText([string]$adb, [string[]]$argv) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $o = @(& $adb @argv 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $old }
    return ($o -join "`n")
}

# The phone connected with USB debugging, or a clear error. Starts the adb server first (quietly).
function AdbDevice([string]$adb) {
    [void](AdbText $adb @('start-server'))
    $state = AdbText $adb @('get-state')
    if (-not (($state -split "`n") | Where-Object { $_.Trim() -eq 'device' })) {
        Fail "no phone found by adb ($($state.Trim())): connect it with USB debugging on (Developer options) and accept the prompt on the phone"
    }
}

# A native program with its output visible; fails with $what if the exit code is not 0.
function RunNative([string]$exe, [string[]]$argv, [string]$what) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # PS 5.1: a native writing to stderr is not an error by itself
    try { & $exe @argv | Out-Host; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
    if ($code) { Fail "$what failed (exit code $code)" }
}

# ------------------------------------------------------------------------------------------------ adb (phase 2.3)
# adb without Android Studio: -Adb, ADB, the PATH, the platform-tools of an Android SDK, or the ones -GetAdb leaves in
# the tools folder of the port (%LOCALAPPDATA%\tsuki-tools\platform-tools; Linux ~/.local/tsuki-tools/platform-tools).
function ToolsDir {
    if ($OnWindows) { return (Join-Path $env:LOCALAPPDATA 'tsuki-tools') }
    return (Join-Path $HOME '.local/tsuki-tools')
}
function FindAdb([string]$given, [string]$sdk) {
    $cands = @($given, $env:ADB)
    $c = Get-Command "adb$Exe" -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    foreach ($s in @(SdkCandidates $sdk)) { $cands += (Under $s "platform-tools\adb$Exe") }
    $cands += (Under (ToolsDir) "platform-tools\adb$Exe")
    foreach ($x in $cands) { if ($x -and (Test-Path -LiteralPath $x -PathType Leaf)) { return [IO.Path]::GetFullPath($x) } }
    return $null
}
# Google's Android SDK Platform-Tools (adb), latest version, into the tools folder (about 7 MB; their license:
# https://developer.android.com/license). Only with -GetAdb: the player asked for it.
function GetAdb {
    $os = if ($OnWindows) { 'windows' } elseif ((Test-Path variable:IsMacOS) -and $IsMacOS) { 'darwin' } else { 'linux' }
    $url = "https://dl.google.com/android/repository/platform-tools-latest-$os.zip"
    $dir = ToolsDir
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $zip = Join-Path $dir 'platform-tools.zip.partial'
    Info "downloading adb (Android SDK Platform-Tools): $url"
    $oldPp = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing }
    catch { Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue; Fail "could not download adb ($url): $($_.Exception.Message)" }
    finally { $ProgressPreference = $oldPp }
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $tmp = Join-Path $dir 'platform-tools.tmp'
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
    Remove-Item -LiteralPath $zip -Force
    $inner = Join-Path $tmp 'platform-tools'
    if (-not (Test-Path -LiteralPath (Join-Path $inner "adb$Exe"))) { Remove-Item -LiteralPath $tmp -Recurse -Force; Fail "the download has no platform-tools/adb$Exe" }
    $final = Join-Path $dir 'platform-tools'
    if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Recurse -Force }
    Move-Item -LiteralPath $inner -Destination $final
    Remove-Item -LiteralPath $tmp -Recurse -Force
    if (-not $OnWindows) { & chmod +x (Join-Path $final 'adb') }
    return (Join-Path $final "adb$Exe")
}
# adb for sending to the phone: found, or downloaded with -GetAdb, or a clear error.
function NeedAdb([string]$given, [string]$sdk, [bool]$download) {
    $a = FindAdb $given $sdk
    if ($a) { return $a }
    if ($download) { return (GetAdb) }
    Fail ('adb not found (it sends the folder to the phone by USB). Run again with -GetAdb to download it (Android ' +
          'SDK Platform-Tools of Google, about 7 MB, into ' + (Under (ToolsDir) 'platform-tools') + '), or copy the folder by hand')
}

# A path of the phone inside single quotes for "adb shell" (the phone's sh).
function ShQ([string]$p) { return "'" + $p.Replace("'", "'\''") + "'" }

# The files under a folder of the phone: relative path (with /) -> @(size, mtime in Unix seconds). Empty if the folder
# does not exist yet.
function AdbRemoteFiles([string]$adb, [string]$target) {
    $map = @{}
    $text = AdbText $adb @('shell', "cd $(ShQ $target) 2>/dev/null && find . -type f -exec stat -c '%s %Y %n' {} +")
    foreach ($line in ($text -split "`n")) {
        $m = [regex]::Match($line.TrimEnd("`r"), '^(\d+) (\d+) \./(.+)$')
        if ($m.Success) { $map[$m.Groups[3].Value] = @([int64]$m.Groups[1].Value, [int64]$m.Groups[2].Value) }
    }
    return $map
}

function MB([int64]$n) { return ('{0:N1}' -f ($n / 1MB)) }

# Sends a folder of this PC to a folder of the phone with progress ("   adb: 34% (28.0 of 81.1 MB)"), only the files
# that are missing there or differ in size or date (adb push keeps the date), in batches by folder. Nothing on the
# phone is deleted here. Big files go alone and their progress is read from the phone while they are sent.
function AdbPushTree([string]$adb, [string]$local, [string]$target) {
    $base = (Get-Item -LiteralPath $local).FullName.TrimEnd('\', '/')
    $remote = AdbRemoteFiles $adb $target
    $epoch = [DateTime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
    $todo = @()
    $same = 0
    foreach ($f in @(Get-ChildItem -LiteralPath $base -Recurse -File -Force | Sort-Object FullName)) {
        $rel = $f.FullName.Substring($base.Length + 1).Replace('\', '/')
        $mt = [int64][Math]::Floor(($f.LastWriteTimeUtc - $epoch).TotalSeconds)
        $r = $remote[$rel]
        if ($r -and $r[0] -eq $f.Length -and [Math]::Abs($r[1] - $mt) -le 2) { $same++; continue }   # FAT/exFAT: 2 s
        $todo += , @($rel, $f.FullName, [int64]$f.Length)
    }
    $total = [int64]0
    foreach ($t in $todo) { $total += $t[2] }
    if (-not $todo.Count) { Info "adb: nothing to send ($same files already on the phone)"; return }
    Info "adb: $($todo.Count) files to send ($(MB $total) MB; $same already on the phone)"
    # the folders first, in one call
    $dirs = @($todo | ForEach-Object { $d = Split-Path -Parent ($_[0] -replace '/', '\'); if ($d) { "$target/$($d.Replace('\', '/'))" } else { $target } } | Sort-Object -Unique)
    $mk = 'mkdir -p ' + (($dirs | ForEach-Object { ShQ $_ }) -join ' ')
    [void](AdbText $adb @('shell', $mk))
    $sent = [int64]0
    $i = 0
    while ($i -lt $todo.Count) {
        $first = $todo[$i]
        $dir = Split-Path -Parent ($first[0] -replace '/', '\')
        $dest = if ($dir) { "$target/$($dir.Replace('\', '/'))/" } else { "$target/" }
        if ($first[2] -ge 128MB) {   # alone, with its progress read from the phone
            AdbPushBig $adb $first[1] "$dest$(Split-Path -Leaf $first[1])" $first[2] $sent $total
            $sent += $first[2]; $i++
        } else {   # a batch of small files of the same folder: up to 64 files or 32 MB
            $batch = @(); $bytes = [int64]0
            while ($i -lt $todo.Count -and $batch.Count -lt 64 -and $bytes -lt 32MB -and $todo[$i][2] -lt 128MB) {
                $d2 = Split-Path -Parent ($todo[$i][0] -replace '/', '\')
                if ($d2 -ne $dir) { break }
                $batch += $todo[$i][1]; $bytes += $todo[$i][2]; $i++
            }
            $out = AdbText $adb (@('push') + $batch + @($dest))
            if ($LASTEXITCODE) { Fail "adb push to $dest failed: $($out.Trim())" }
            $sent += $bytes
        }
        $pct = if ($total) { [int](100 * $sent / $total) } else { 100 }
        Info "adb: $pct% ($(MB $sent) of $(MB $total) MB)"
    }
}

# One big file (the game's library is 46 MB; a dump is GBs): adb push in the background, the size on the phone read
# every few seconds for the progress.
function AdbPushBig([string]$adb, [string]$file, [string]$dest, [int64]$size, [int64]$before, [int64]$total) {
    Info "adb: sending $(Split-Path -Leaf $file) ($(MB $size) MB)"
    $errFile = [IO.Path]::GetTempFileName()
    $outFile = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $adb -ArgumentList @('push', "`"$file`"", "`"$dest`"") -NoNewWindow -PassThru `
        -RedirectStandardError $errFile -RedirectStandardOutput $outFile
    $last = -1
    while (-not $p.WaitForExit(3000)) {
        $now = AdbText $adb @('shell', "stat -c %s $(ShQ $dest) 2>/dev/null")
        $n = [int64]0
        if ([int64]::TryParse($now.Trim(), [ref]$n) -and $n -ne $last) {
            $last = $n
            $pct = if ($total) { [int](100 * ($before + [Math]::Min($n, $size)) / $total) } else { 0 }
            Info "adb: $pct% ($(MB ($before + [Math]::Min($n, $size))) of $(MB $total) MB)"
        }
    }
    $p.WaitForExit()
    $code = $p.ExitCode
    $err = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
    Remove-Item -LiteralPath $errFile, $outFile -Force -ErrorAction SilentlyContinue
    if ($code) { Fail "adb push of $(Split-Path -Leaf $file) failed (exit code $code): $("$err".Trim())" }
}
