# build_apk.ps1 - Android build of Tsukihime (phase 2 of the port). Two things:
#
# 1. The APK (always): <root>\_android\tsukihime.apk. One app for both editions with NOTHING of the game inside: the home
#    screen of <root>\android (Java) and libtsuki_boot.so (android\boot\boot.c), which loads the game's library from the
#    app's files folder. This is the APK that can be published.
# 2. With -Edition: the game's library of that edition, <root>\_android\<edition>\lib<game>.so: the runtime + the game's
#    recompiled code (data\<edition>\gen, from your dump: recomp_tsukihime.ps1 steps 0-8) for arm64-v8a with the NDK.
#    It is code recompiled from your dump: for your own phone only, never publish it. tools\android\phone_data.ps1 puts
#    it in the port folder for the phone (data\<edition>\android\), from where the app installs it.
#
#   .\tools\android\build_apk.ps1 -Edition global            # the library of the global edition + the APK
#   .\tools\android\build_apk.ps1 -Edition global -Install   # and install the APK on the phone (adb, USB debugging)
#   .\tools\android\build_apk.ps1                            # only the APK
#   .\tools\android\build_apk.ps1 -Edition japan -Clean      # configures the native build from zero
#   Linux: bash tools/android/build_apk.sh -Edition global
#
# Needs the Android SDK with an NDK (r27+), build-tools and a platform of API 30+ (Android Studio: SDK Manager) and a JDK
# (Android Studio brings one). For -Edition also: the game recompiled on this PC for that edition (data\<edition>\gen),
# the Android libraries (tools\android\android_deps.ps1 -> _prepare\android-deps), CMake 3.25+ and Ninja, and recent
# Vulkan headers (the PC install's prebuilt pack or the Vulkan SDK). The native build is framework\build\android_<game>.
param(
    [ValidateSet('', 'japan', 'global')][string]$Edition = '',
    [switch]$Install,
    [switch]$Clean,
    [string]$Root,
    [string]$Sdk,
    [string]$Ndk,
    [string]$Version = '0.2.0'
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'android_common.ps1')

try {
    $Root = FindRoot $Root
    $fw = Under $Root 'framework'
    $out = Under $Root '_android\apk'
    $apkFinal = Under $Root '_android\tsukihime.apk'

    Step 'Tsukihime for Android'
    $sdkDir = FindSdk $Sdk
    $ndkDir = FindNdk $sdkDir $Ndk
    $bt = FindBuildTools $sdkDir
    $jar = FindPlatformJar $sdkDir
    $jdk = FindJdk
    $llvm = NdkLlvmBin $ndkDir
    Info "SDK $sdkDir"
    Info "NDK $ndkDir"
    Info "build-tools $(Split-Path -Leaf $bt), $(Split-Path -Leaf (Split-Path -Parent $jar)), JDK $jdk"

    # ------------------------------------------------------------------------------------------------ game library
    if ($Edition) {
        $ed = $AndroidEditions[$Edition]
        $game = $ed.Game
        $gen = Under $Root "$($ed.Data)\gen"
        $deps = Under $Root '_prepare\android-deps'
        $build = Under $fw "build\android_$game"
        Step "game library lib$game.so ($Edition, arm64-v8a)"
        if (-not (Test-Path -LiteralPath (Under $gen 'main') -PathType Container) -or
            -not @(Get-ChildItem -LiteralPath (Under $gen 'main') -Filter '*.cpp' -ErrorAction SilentlyContinue).Count) {
            Fail "no recompiled code in $gen\main: recompile the edition first (tools\recomp_tsukihime.ps1 -Edition $Edition)"
        }
        if (-not (Test-Path -LiteralPath (Under $deps 'include\libavcodec\avcodec.h'))) {
            Fail "no Android libraries in ${deps}: run tools\android\android_deps.ps1 first"
        }
        foreach ($m in @('AP_ANDROID_BUILD', 'AP_ANDROID_INPUT', 'AP_ANDROID_PRESENT', 'AP_ANDROID_ROMFS')) {
            $file = switch ($m) {
                'AP_ANDROID_BUILD' { 'games\tsukihime_common\tsukihime_common.cmake' }
                'AP_ANDROID_INPUT' { 'runtime\src\platform_android_input.cpp' }
                'AP_ANDROID_PRESENT' { 'runtime\src\nvn\nvn_present.cpp' }
                default { 'games\tsukihime_common\romfs_direct.cpp' }
            }
            if (-not (Select-String -LiteralPath (Under $fw $file) -SimpleMatch $m -Quiet)) {
                $p = 'apply_' + ($m -replace '^AP_', '').ToLower() + '.py'
                Fail "the framework does not have $m ($file): apply tools\patches\$p"
            }
        }
        $cn = FindCmakeNinja $sdkDir $Root
        $vk = FindVulkanSdk $Root
        Info "cmake $($cn.Cmake)"
        Info "Vulkan headers $vk"
        if ($Clean -and (Test-Path -LiteralPath $build)) { Remove-Item -LiteralPath $build -Recurse -Force; Info "deleted $build" }
        $toolchain = (Under $ndkDir 'build\cmake\android.toolchain.cmake') -replace '\\', '/'
        $cfg = @('-S', ($fw -replace '\\', '/'), '-B', ($build -replace '\\', '/'), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
                 "-DCMAKE_TOOLCHAIN_FILE=$toolchain", '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-30',
                 '-DANDROID_STL=c++_static', "-DNX_GAME=$game", "-DNX_GEN_DIR=$($gen -replace '\\', '/')",
                 "-DNX_ANDROID_DEPS=$($deps -replace '\\', '/')", "-DNX_VULKAN_SDK=$vk",
                 "-DCMAKE_MAKE_PROGRAM=$($cn.Ninja -replace '\\', '/')")
        RunNative $cn.Cmake $cfg 'cmake (configure)'
        RunNative $cn.Cmake @('--build', $build, '--target', $game) 'cmake --build'
        $so = Under $build "lib$game.so"
        if (-not (Test-Path -LiteralPath $so)) { Fail "the build did not leave $so" }
        $libOut = Under $Root "_android\$Edition\lib$game.so"
        New-Item -ItemType Directory -Path (Split-Path -Parent $libOut) -Force | Out-Null
        RunNative (Under $llvm "llvm-strip$Exe") @('--strip-unneeded', '-o', $libOut, $so) 'llvm-strip'
        Info "game library: $libOut ($([math]::Round((Get-Item -LiteralPath $libOut).Length / 1MB, 1)) MB)"
    }

    # ------------------------------------------------------------------------------------------------ APK
    Step 'APK (home screen + libtsuki_boot.so; nothing of the game)'
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
    $stage = Under $out 'stage'
    $lib = Under $stage 'lib\arm64-v8a'
    New-Item -ItemType Directory -Path $lib, (Under $stage 'classes'), (Under $stage 'dex') -Force | Out-Null
    # the loader of the game's library: plain C with the NDK's clang (16 KB pages, as the game's library)
    $boot = Under $lib 'libtsuki_boot.so'
    RunNative (Under $llvm "clang$Exe") @('--target=aarch64-linux-android30', '-O2', '-fPIC', '-shared', '-Wall',
        '-Wl,-z,max-page-size=16384', '-Wl,-soname,libtsuki_boot.so', '-o', $boot, (Under $Root 'android\boot\boot.c'),
        '-landroid', '-llog', '-ldl') 'clang (libtsuki_boot.so)'
    RunNative (Under $llvm "llvm-strip$Exe") @('--strip-unneeded', $boot) 'llvm-strip (boot)'

    # manifest from the template
    $tpl = Get-Content -LiteralPath (Under $Root 'android\AndroidManifest.xml.in') -Raw
    $man = $tpl.Replace('@PACKAGE@', $AndroidPackage).Replace('@LABEL@', $AndroidLabel).Replace('@VERSION@', $Version)
    $manPath = Under $stage 'AndroidManifest.xml'
    [IO.File]::WriteAllText($manPath, $man, (New-Object Text.UTF8Encoding($false)))

    # resources: android\res plus the app icon, taken from the icon of the exe and the launcher
    # (framework\games\tsukihime_common\tsukihime.ico: its 64, 128 and 256 px images are PNG; copied as they are)
    $res = Under $stage 'res'
    Copy-Item -LiteralPath (Under $Root 'android\res') -Destination $res -Recurse
    $ico = [IO.File]::ReadAllBytes((Under $fw 'games\tsukihime_common\tsukihime.ico'))
    $want = @{ 64 = 'mipmap-mdpi'; 128 = 'mipmap-xhdpi'; 256 = 'mipmap-xxxhdpi' }
    $count = [BitConverter]::ToUInt16($ico, 4)
    $found = 0
    for ($i = 0; $i -lt $count; $i++) {
        $e = 6 + 16 * $i
        $w = [int]$ico[$e]; if ($w -eq 0) { $w = 256 }
        $len = [BitConverter]::ToInt32($ico, $e + 8); $off = [BitConverter]::ToInt32($ico, $e + 12)
        $png = ($ico[$off] -eq 0x89) -and ($ico[$off + 1] -eq 0x50) -and ($ico[$off + 2] -eq 0x4E) -and ($ico[$off + 3] -eq 0x47)
        if ($want.ContainsKey($w) -and $png) {
            $d = Under $res $want[$w]
            New-Item -ItemType Directory -Path $d -Force | Out-Null
            $bytes = New-Object byte[] $len
            [Array]::Copy($ico, $off, $bytes, 0, $len)
            [IO.File]::WriteAllBytes((Join-Path $d 'icon.png'), $bytes)
            $found++
        }
    }
    if (-not $found) { Fail 'tsukihime.ico has no PNG image of 64, 128 or 256 px for the app icon' }
    RunNative (Under $bt "aapt2$Exe") @('compile', '--dir', $res, '-o', (Under $stage 'res.zip')) 'aapt2 compile'
    $base = Under $out 'base.apk'
    RunNative (Under $bt "aapt2$Exe") @('link', '-o', $base, '-I', $jar, '--manifest', $manPath, '--min-sdk-version', '30',
                                        '--target-sdk-version', '35', (Under $stage 'res.zip')) 'aapt2 link'

    # the home screen: javac against android.jar, d8 to classes.dex
    $sources = @(Get-ChildItem -LiteralPath (Under $Root 'android\java') -Recurse -Filter '*.java' | ForEach-Object { $_.FullName })
    if (-not $sources.Count) { Fail "no .java in $(Under $Root 'android\java')" }
    RunNative (Under $jdk "bin\javac$Exe") (@('-encoding', 'UTF-8', '-source', '1.8', '-target', '1.8', '-Xlint:-options', '-nowarn',
                                               '-bootclasspath', $jar, '-d', (Under $stage 'classes')) + $sources) 'javac'
    $classes = @(Get-ChildItem -LiteralPath (Under $stage 'classes') -Recurse -Filter '*.class' | ForEach-Object { $_.FullName })
    RunNative (Under $bt "d8$Bat") (@('--release', '--min-api', '30', '--lib', $jar, '--output', (Under $stage 'dex')) + $classes) 'd8'

    # classes.dex and the loader into the APK (compressed: extractNativeLibs="true")
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open($base, 'Update')
    try {
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Under $stage 'dex\classes.dex'), 'classes.dex', 'Optimal')
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $boot, 'lib/arm64-v8a/libtsuki_boot.so', 'Optimal')
    } finally { $zip.Dispose() }

    $aligned = Under $out 'aligned.apk'
    RunNative (Under $bt "zipalign$Exe") @('-f', '-p', '4', $base, $aligned) 'zipalign'

    # debug key: the one of Android Studio (~/.android/debug.keystore), made with keytool if there is none
    $ks = Join-Path (Join-Path $HOME '.android') 'debug.keystore'
    if (-not (Test-Path -LiteralPath $ks)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $ks) -Force | Out-Null
        RunNative (Under $jdk "bin\keytool$Exe") @('-genkeypair', '-keystore', $ks, '-storepass', 'android', '-keypass', 'android',
            '-alias', 'androiddebugkey', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000', '-dname', 'CN=Android Debug,O=Android,C=US') 'keytool'
        Info "debug key created: $ks"
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $apkFinal) -Force | Out-Null
    RunNative (Under $bt "apksigner$Bat") @('sign', '--ks', $ks, '--ks-pass', 'pass:android', '--key-pass', 'pass:android',
                                             '--out', $apkFinal, $aligned) 'apksigner'
    $kb = [math]::Round((Get-Item -LiteralPath $apkFinal).Length / 1KB)
    Info "APK: $apkFinal ($kb KB, package $AndroidPackage)"

    if ($Install) {
        Step 'install (adb)'
        $adb = Under $sdkDir "platform-tools\adb$Exe"
        if (-not (Test-Path -LiteralPath $adb)) { Fail "no adb in $(Under $sdkDir 'platform-tools') (SDK Manager > SDK Tools > Android SDK Platform-Tools)" }
        AdbDevice $adb
        RunNative $adb @('install', '-r', $apkFinal) 'adb install'
    }
    Write-Host ''
    if ($Edition) { Write-Host "Done. Data and game library for the phone: tools\android\phone_data.ps1 -Edition $Edition" }
    else { Write-Host 'Done.' }
} catch {
    Write-Host ''
    Write-Host "ERROR in build_apk: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
