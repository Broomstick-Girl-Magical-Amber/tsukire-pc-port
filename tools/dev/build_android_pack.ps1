# build_android_pack.ps1 - MAINTAINER: makes the "Android pack" (phase 2 of the Android port,
# claude/tsukihime-android-fase2.md): what an end user needs to build the game's library for Android WITHOUT Android
# Studio, the NDK or vcpkg, only with the LLVM clang of the PC install. Nothing of the game goes into it:
#   runtime\<game>\libruntime.a   the runtime + games\tsukihime_common built for arm64-android (the objects of the
#                                 NDK build of tools\android\build_apk.ps1 -Edition, without the recompiled code)
#   deps\lib\*.a, deps\licenses   FFmpeg, opus, libjpeg-turbo, libpng, zlib (static; _prepare\android-deps)
#   sysroot\usr\include, sysroot\usr\lib\aarch64-linux-android\{30\*, libc++_static.a, libc++abi.a}   from the NDK
#   clangrt\libclang_rt.builtins-aarch64-android.a, clangrt\libunwind.a                               from the NDK
#   pack.json                     version, NDK, editions, fingerprint of framework\runtime\include\nx\*.h
# Run it on a PC that already built the edition(s) with the NDK:  tools\android\build_apk.ps1 -Edition global  (and japan).
#
#   .\tools\dev\build_android_pack.ps1 -Version 1                    # every edition already built with the NDK
#   .\tools\dev\build_android_pack.ps1 -Version 1 -Edition global
#
# Output: <root>\_prepare\android-pack (ready for tools\android\build_game.ps1 on this PC) and
# <root>\_android\tsukire-android-pack-v<Version>.zip with its SHA-256, to upload to the release and pin in
# tools\internal\android_pack.json. Make a new one whenever the runtime or framework\runtime\include change.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [ValidateSet('', 'japan', 'global')][string]$Edition = '',
    [string]$Root,
    [string]$Sdk,
    [string]$Ndk
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'android') 'android_common.ps1')

function CopyTree([string]$src, [string]$dst) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
}

try {
    $Root = FindRoot $Root
    $fw = Under $Root 'framework'
    $deps = Under $Root '_prepare\android-deps'
    $pack = Under $Root '_prepare\android-pack'
    $zip = Under $Root "_android\tsukire-android-pack-v$Version.zip"

    Step 'Android pack'
    $sdkDir = $null
    if (-not $Ndk -and -not $env:ANDROID_NDK_HOME) { $sdkDir = FindSdk $Sdk }
    $ndkDir = FindNdk $sdkDir $Ndk
    $ndkBin = NdkLlvmBin $ndkDir
    $prebuilt = Split-Path -Parent $ndkBin
    $sysroot = Under $prebuilt 'sysroot'
    Info "NDK $ndkDir"
    if (-not (Test-Path -LiteralPath (Under $deps 'lib\libavcodec.a'))) { Fail "no Android libraries in ${deps}: tools\android\android_deps.ps1" }
    $eds = @()
    foreach ($e in @('japan', 'global')) {
        if ($Edition -and $Edition -ne $e) { continue }
        $g = $AndroidEditions[$e].Game
        if (Test-Path -LiteralPath (Under $fw "build\android_$g\lib$g.so")) { $eds += $e }
        elseif ($Edition) { Fail "no NDK build of $e (framework\build\android_$g\lib$g.so): tools\android\build_apk.ps1 -Edition $e" }
    }
    if (-not $eds.Count) { Fail 'no edition built with the NDK yet: tools\android\build_apk.ps1 -Edition <japan|global>' }

    $tmp = "$pack.tmp"
    if (Test-Path -LiteralPath $tmp) {   # a staging folder left by a run that failed
        try { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction Stop }
        catch { Fail "cannot remove $tmp (left by an earlier run; in use?): close what has it open, or delete it by hand, and run this again" }
    }
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null

    foreach ($e in $eds) {
        $g = $AndroidEditions[$e].Game
        Step "runtime of $e ($g)"
        # every object of the game's target (runtime, tsukihime_common, the app glue); the recompiled code is in the
        # static libraries game_main / game_sdk (other folders of CMakeFiles), never here
        $objDir = Under $fw "build\android_$g\CMakeFiles\$g.dir"
        $objs = @(Get-ChildItem -LiteralPath $objDir -Recurse -File | Where-Object { $_.Extension -eq '.o' } | ForEach-Object { $_.FullName })
        if ($objs.Count -lt 40) { Fail "only $($objs.Count) objects in ${objDir}: build it again (tools\android\build_apk.ps1 -Edition $e -Clean)" }
        $bad = @($objs | Where-Object { $_ -match '[\\/]gen[\\/]|code_\d+\.cpp' })
        if ($bad.Count) { Fail "recompiled code among the runtime objects ($($bad[0])): not packed" }
        $out = Under $tmp "runtime\$g"
        New-Item -ItemType Directory -Path $out -Force | Out-Null
        $rsp = Join-Path $out 'objects.rsp'
        [IO.File]::WriteAllLines($rsp, @($objs | ForEach-Object { '"' + ($_ -replace '\\', '/') + '"' }), (New-Object Text.UTF8Encoding($false)))
        RunNative (Under $ndkBin "llvm-ar$Exe") @('rcs', (Join-Path $out 'libruntime.a'), "@$rsp") 'llvm-ar'
        Remove-Item -LiteralPath $rsp -Force
        Info ("libruntime.a: {0} objects, {1:n1} MB" -f $objs.Count, ((Get-Item -LiteralPath (Join-Path $out 'libruntime.a')).Length / 1MB))
    }

    Step 'libraries, sysroot and compiler runtime'
    New-Item -ItemType Directory -Path (Under $tmp 'deps\lib') -Force | Out-Null
    foreach ($l in @('avformat', 'avcodec', 'swscale', 'swresample', 'avutil', 'opus', 'jpeg', 'png16', 'z')) {
        $a = Under $deps "lib\lib$l.a"
        if (-not (Test-Path -LiteralPath $a)) { Fail "$a is missing (tools\android\android_deps.ps1)" }
        Copy-Item -LiteralPath $a -Destination (Under $tmp 'deps\lib')
    }
    if (Test-Path -LiteralPath (Under $deps 'licenses')) { CopyTree (Under $deps 'licenses') (Under $tmp 'deps\licenses') }
    CopyTree (Under $sysroot 'usr\include') (Under $tmp 'sysroot\usr\include')
    $libArch = Under $sysroot 'usr\lib\aarch64-linux-android'
    CopyTree (Under $libArch '30') (Under $tmp 'sysroot\usr\lib\aarch64-linux-android\30')
    foreach ($a in @('libc++_static.a', 'libc++abi.a')) {
        if (-not (Test-Path -LiteralPath (Join-Path $libArch $a))) { Fail "$(Join-Path $libArch $a) is missing in this NDK" }
        Copy-Item -LiteralPath (Join-Path $libArch $a) -Destination (Under $tmp 'sysroot\usr\lib\aarch64-linux-android')
    }
    New-Item -ItemType Directory -Path (Under $tmp 'clangrt') -Force | Out-Null
    $clangLib = Under $prebuilt 'lib\clang'
    $builtins = @(Get-ChildItem -LiteralPath $clangLib -Recurse -File -Filter 'libclang_rt.builtins-aarch64-android.a')
    $unwind = @(Get-ChildItem -LiteralPath $clangLib -Recurse -File -Filter 'libunwind.a' | Where-Object { $_.FullName -match '[\\/]aarch64[\\/]' })
    if (-not $builtins.Count) { Fail "no libclang_rt.builtins-aarch64-android.a under $clangLib" }
    if (-not $unwind.Count) { Fail "no aarch64\libunwind.a under $clangLib" }
    Copy-Item -LiteralPath $builtins[0].FullName -Destination (Under $tmp 'clangrt')
    Copy-Item -LiteralPath $unwind[0].FullName -Destination (Under $tmp 'clangrt')
    New-Item -ItemType Directory -Path (Under $tmp 'licenses') -Force | Out-Null
    foreach ($n in @('NOTICE', 'NOTICE.toolchain')) {
        if (Test-Path -LiteralPath (Join-Path $ndkDir $n)) { Copy-Item -LiteralPath (Join-Path $ndkDir $n) -Destination (Under $tmp "licenses\NDK-$n.txt") }
    }
    Copy-Item -LiteralPath (Under $fw 'LICENSE') -Destination (Under $tmp 'licenses\framework-LICENSE.txt')

    $json = [ordered]@{
        version  = $Version
        ndk      = (Split-Path -Leaf $ndkDir)
        target   = 'aarch64-linux-android30'
        editions = @($eds)
        headers  = (HeadersFingerprint $Root)
    }
    [IO.File]::WriteAllText((Join-Path $tmp 'pack.json'), ($json | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))

    ReplaceDir $tmp $pack   # retries / copies when Windows still holds the new files (antivirus scan)
    Info "pack folder: $pack (editions: $($eds -join ', '))"

    Step 'zip'
    New-Item -ItemType Directory -Path (Split-Path -Parent $zip) -Force | Out-Null
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($pack, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
    Info ("{0} ({1:n1} MB)" -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB))
    Info "SHA-256: $(FileSha256 $zip)"
    Write-Host ''
    Write-Host 'Done. To publish: upload the zip to the release and put its tag, URL and SHA-256 in tools\internal\android_pack.json.'
    Write-Host 'On this PC the pack is already in place: tools\android\build_game.ps1 -Edition <japan|global>'
} catch {
    Write-Host ''
    Write-Host "ERROR in build_android_pack: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
