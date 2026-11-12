# build_game.ps1 - The game's library for Android (lib<game>.so) built on this PC WITHOUT Android Studio or the NDK
# (phase 2 of the Android port): the code recompiled from your dump
# (data\<edition>\gen: tools\recomp_tsukihime.ps1 first) is compiled for arm64 with the LLVM clang the PC install already
# has, and linked with the prebuilt runtime and libraries of the "Android pack".
#
#   .\tools\android\build_game.ps1 -Edition global
#   .\tools\android\build_game.ps1 -Edition japan -Pack <tsukire-android-pack-vN.zip or its folder>
#   Linux: bash tools/android/build_game.sh -Edition global
#
# The pack: <root>\_prepare\android-pack. If it is not there it comes from -Pack, or is downloaded from the URL of
# tools\internal\android_pack.json (checked against its SHA-256). It has nothing of the game: the runtime built for
# Android, FFmpeg and the other libraries, and the Android headers and libraries the compiler needs.
# Output: <root>\_android\<edition>\lib<game>.so. It is derived from your dump: for your own phone, never publish it.
# Then: tools\android\phone_data.ps1 -Edition <edition> puts it, with the data, in the folder for the phone.
# Only what changed is compiled again (Ninja; the build folder is <root>\_android\<edition>\build). -Clean starts over.
param(
    [Parameter(Mandatory = $true)][ValidateSet('japan', 'global')][string]$Edition,
    [string]$Pack,
    [switch]$Clean,
    [int]$Jobs = 0,
    [string]$Root,
    [string]$Clang
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'android_common.ps1')

# A path as Ninja wants it in a build line: forward slashes, and $ before spaces, colons and dollars.
function NinjaPath([string]$p) { return (($p -replace '\\', '/') -replace '([ :$])', '$$$1') }
# Text for a Ninja variable or command: only $ is special there.
function NinjaText([string]$t) { return $t.Replace('$', '$$') }
# A path as a quoted command-line argument.
function Quoted([string]$p) { return '"' + ($p -replace '\\', '/') + '"' }

function Unzip([string]$zip, [string]$dst) {
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
    if (-not (Test-Path -LiteralPath (Join-Path $tmp 'pack.json'))) { Remove-Item -LiteralPath $tmp -Recurse -Force; Fail "$zip is not an Android pack (no pack.json)" }
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
    Move-Item -LiteralPath $tmp -Destination $dst
}

try {
    $Root = FindRoot $Root
    $ed = $AndroidEditions[$Edition]
    $game = $ed.Game
    $gen = Under $Root "$($ed.Data)\gen"
    $packDir = Under $Root '_prepare\android-pack'
    $build = Under $Root "_android\$Edition\build"
    $libOut = Under $Root "_android\$Edition\lib$game.so"

    Step "game library for Android: $Edition ($game)"
    foreach ($m in @('main', 'sdk')) {
        if (-not (Test-Path -LiteralPath (Under $gen "$m\funcs.h"))) { Fail "no recompiled code in $gen\${m}: recompile the edition first (tools\recomp_tsukihime.ps1 -Edition $Edition)" }
    }

    # ------------------------------------------------------------------------------------------------ the pack
    if ($Pack) {
        if (Test-Path -LiteralPath $Pack -PathType Container) {
            if (-not (Test-Path -LiteralPath (Join-Path $Pack 'pack.json'))) { Fail "$Pack is not an Android pack (no pack.json)" }
            $packDir = [IO.Path]::GetFullPath($Pack)
        } elseif (Test-Path -LiteralPath $Pack -PathType Leaf) {
            Info "unpacking $Pack"
            Unzip ([IO.Path]::GetFullPath($Pack)) $packDir
        } else { Fail "-Pack ${Pack}: not found" }
    } elseif (-not (Test-Path -LiteralPath (Join-Path $packDir 'pack.json'))) {
        $pinFile = Under $Root 'tools\internal\android_pack.json'
        $pin = $null
        if (Test-Path -LiteralPath $pinFile) { $pin = Get-Content -LiteralPath $pinFile -Raw | ConvertFrom-Json }
        if (-not $pin -or -not "$($pin.url)") { Fail "no Android pack in $packDir and none published yet (tools\internal\android_pack.json): pass -Pack <zip>, or make it with tools\dev\build_android_pack.ps1" }
        $dl = Under $Root "_prepare\downloads\android-pack-v$($pin.version).zip"
        New-Item -ItemType Directory -Path (Split-Path -Parent $dl) -Force | Out-Null
        if (-not (Test-Path -LiteralPath $dl) -or (FileSha256 $dl) -ne "$($pin.sha256)".ToLower()) {
            Info "downloading the Android pack v$($pin.version): $($pin.url)"
            if (Test-Path -LiteralPath "$dl.partial") { Remove-Item -LiteralPath "$dl.partial" -Force }
            $oldPp = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'   # PS 5.1: the progress bar makes it very slow
            try { Invoke-WebRequest -Uri "$($pin.url)" -OutFile "$dl.partial" -UseBasicParsing } finally { $ProgressPreference = $oldPp }
            $h = FileSha256 "$dl.partial"
            if ($h -ne "$($pin.sha256)".ToLower()) { Fail "the downloaded pack has SHA-256 $h, expected $($pin.sha256): not used ($dl.partial)" }
            if (Test-Path -LiteralPath $dl) { Remove-Item -LiteralPath $dl -Force }
            Move-Item -LiteralPath "$dl.partial" -Destination $dl
        }
        Unzip $dl $packDir
    }
    $pj = Get-Content -LiteralPath (Join-Path $packDir 'pack.json') -Raw | ConvertFrom-Json
    Info "Android pack v$($pj.version) (NDK $($pj.ndk)) in $packDir"
    $runtime = Under $packDir "runtime\$game\libruntime.a"
    if (-not (Test-Path -LiteralPath $runtime)) { Fail "the pack has no runtime for $Edition (it has: $(@($pj.editions) -join ', '))" }
    if ("$($pj.headers)" -ne (HeadersFingerprint $Root)) {
        Fail ('the Android pack was made for other runtime headers (framework\runtime\include\nx) than this copy of the repo: ' +
              'update the repo and the pack together (delete _prepare\android-pack to download the published one again)')
    }
    $sysroot = Under $packDir 'sysroot'
    $libApi = Under $sysroot 'usr\lib\aarch64-linux-android\30'
    $libArch = Under $sysroot 'usr\lib\aarch64-linux-android'
    $unwind = Under $packDir 'clangrt\libunwind.a'
    $builtinsFile = Under $packDir 'clangrt\libclang_rt.builtins-aarch64-android.a'
    foreach ($f in @((Join-Path $libApi 'crtbegin_so.o'), (Join-Path $libApi 'crtend_so.o'), (Join-Path $libArch 'libc++_static.a'),
                     (Join-Path $libArch 'libc++abi.a'), $unwind, $builtinsFile)) {
        if (-not (Test-Path -LiteralPath $f)) { Fail "the pack is incomplete: $f is missing" }
    }

    # ------------------------------------------------------------------------------------------------ tools
    $cxx = FindLlvmClang $Root $Clang
    $cn = FindCmakeNinja '' $Root
    Info "clang++ $cxx"
    Info ((& $cxx --version | Select-Object -First 1))
    if (-not (((& $cxx --print-targets) -join "`n") -match '(?m)^\s*aarch64\s')) { Fail 'this clang has no AArch64 target' }

    # ------------------------------------------------------------------------------------------------ build.ninja
    if ($Clean -and (Test-Path -LiteralPath $build)) { Remove-Item -LiteralPath $build -Recurse -Force; Info "deleted $build" }
    New-Item -ItemType Directory -Path $build -Force | Out-Null
    $target = "--target=$($pj.target)"
    # the same options as the framework's CMakeLists for the recompiled code on Android (and the NDK's defaults that matter)
    $cflags = @($target, "--sysroot=$(Quoted $sysroot)", '-std=gnu++20', '-O2', '-DNDEBUG', '-DANDROID', '-fPIC',
                '-fvisibility=hidden', '-fno-semantic-interposition', '-march=armv8.2-a+crypto', '-fdata-sections',
                '-ffunction-sections', '-funwind-tables', '-fstack-protector-strong', '-Wno-unused-label', '-Wno-unused-value',
                '-Wno-parentheses-equality', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
                '-Wno-constant-logical-operand', '-Wno-unused-function', "-I$(Quoted (Under $Root 'framework\runtime\include'))")
    $deps = @('avformat', 'avcodec', 'swscale', 'swresample', 'avutil', 'opus', 'jpeg', 'png16', 'z') | ForEach-Object { Quoted (Under $packDir "deps\lib\lib$_.a") }
    $builtins = Quoted $builtinsFile
    # explicit link (no driver defaults): crt, the whole runtime (its HLE and hooks register themselves), the recompiled
    # code, the libraries, libc++ (static), the unwinder and the compiler's helpers, then the system libraries
    $lflags = @($target, "--sysroot=$(Quoted $sysroot)", '-fuse-ld=lld', '-nostdlib', '-shared', "-Wl,-soname,lib$game.so",
                '-Wl,--build-id=sha1', '-Wl,-z,max-page-size=16384', '-Wl,-z,noexecstack', '-Wl,-z,relro', '-Wl,-z,now',
                '-Wl,--no-undefined', '-Wl,--strip-all', '-Wl,--exclude-libs,libunwind.a',
                '-u', 'ANativeActivity_onCreate', "-L$(Quoted $libApi)", "-L$(Quoted $libArch)")
    $libsBefore = @((Quoted (Join-Path $libApi 'crtbegin_so.o')), '-Wl,--whole-archive', (Quoted $runtime), '-Wl,--no-whole-archive')
    $libsAfter = @($deps) + @((Quoted (Join-Path $libArch 'libc++_static.a')), (Quoted (Join-Path $libArch 'libc++abi.a')),
                              (Quoted $unwind), $builtins, '-lmediandk', '-landroid', '-llog', '-laaudio', '-lm', '-ldl', '-lc',
                              $builtins, (Quoted (Join-Path $libApi 'crtend_so.o')))

    $nl = "`n"
    $n = New-Object Text.StringBuilder
    [void]$n.Append("# generated by tools/android/build_game.ps1$nl")
    [void]$n.Append("ninja_required_version = 1.5$nl")
    [void]$n.Append("cxx = $(NinjaText (Quoted $cxx))$nl")
    [void]$n.Append("cflags = $(NinjaText ($cflags -join ' '))$nl")
    [void]$n.Append("rule cxx$nl  command = `$cxx `$cflags `$incdir -MD -MF `$out.d -c `$in -o `$out$nl  depfile = `$out.d$nl  deps = gcc$nl  description = CXX `$out$nl")
    [void]$n.Append("rule link$nl  command = `$cxx @`$out.rsp$nl  rspfile = `$out.rsp$nl  rspfile_content = $(NinjaText ($lflags -join ' ')) -o `$out $(NinjaText ($libsBefore -join ' ')) `$in $(NinjaText ($libsAfter -join ' '))$nl  description = LINK `$out$nl")
    $objs = @()
    foreach ($m in @('main', 'sdk')) {
        $dir = Under $gen $m
        foreach ($f in @(Get-ChildItem -LiteralPath $dir -Filter '*.cpp' | Sort-Object Name)) {
            $o = "obj/$m/$($f.BaseName).o"
            [void]$n.Append("build ${o}: cxx $(NinjaPath $f.FullName)$nl  incdir = $(NinjaText ('-I' + (Quoted $dir)))$nl")
            $objs += $o
        }
    }
    [void]$n.Append("build lib$game.so: link $($objs -join ' ')$nl")
    [void]$n.Append("default lib$game.so$nl")
    $ninjaFile = Join-Path $build 'build.ninja'
    $text = $n.ToString()
    if (-not (Test-Path -LiteralPath $ninjaFile) -or [IO.File]::ReadAllText($ninjaFile) -ne $text) {
        [IO.File]::WriteAllText($ninjaFile, $text, (New-Object Text.UTF8Encoding($false)))
    }

    # ------------------------------------------------------------------------------------------------ build
    Step "compile and link ($($objs.Count) files of recompiled code; only what changed)"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $argv = @('-C', $build)
    if ($Jobs -gt 0) { $argv += @('-j', "$Jobs") }
    RunNative $cn.Ninja $argv 'ninja'
    $sw.Stop()
    $so = Join-Path $build "lib$game.so"
    if (-not (Test-Path -LiteralPath $so)) { Fail "the build did not leave $so" }
    Copy-Item -LiteralPath $so -Destination $libOut -Force
    Info ("game library: {0} ({1:n1} MB) in {2:n0} s" -f $libOut, ((Get-Item -LiteralPath $libOut).Length / 1MB), $sw.Elapsed.TotalSeconds)
    Write-Host ''
    Write-Host "Done. Data and game library for the phone: tools\android\phone_data.ps1 -Edition $Edition"
} catch {
    Write-Host ''
    Write-Host "ERROR in build_game: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
