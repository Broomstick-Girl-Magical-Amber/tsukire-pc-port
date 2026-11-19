# android_llvm_probe.ps1 - Feasibility test for phase 2 of the Android port: can the game's
# recompiled code be built for Android WITHOUT the NDK's compiler, with the LLVM clang the PC install already has plus
# only the NDK's sysroot (headers and libraries)? If yes, end users will not need Android Studio or the NDK: a small
# pack with that sysroot is enough. Nothing is installed or changed; it writes only to <root>\_android\probe.
#
#   .\tools\dev\android_llvm_probe.ps1 -Edition global            # 6 files of gen\main and gen\sdk each
#   .\tools\dev\android_llvm_probe.ps1 -Edition global -Files 0   # every file (the real compile time)
#
# It reports: the clang used and its version, whether it has the AArch64 target and ld.lld, how many files compiled and
# in how long, and whether a shared library links from them. Send the output back.
param(
    [Parameter(Mandatory = $true)][ValidateSet('japan', 'global')][string]$Edition,
    [int]$Files = 6,
    [string]$Root,
    [string]$Sdk,
    [string]$Ndk,
    [string]$Clang
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'android') 'android_common.ps1')

try {
    $Root = FindRoot $Root
    $ed = $AndroidEditions[$Edition]
    $gen = Under $Root "$($ed.Data)\gen"
    $out = Under $Root '_android\probe'
    if (-not (Test-Path -LiteralPath (Under $gen 'main\funcs.h'))) { Fail "no recompiled code in ${gen}: recompile the edition first" }

    Step 'tools'
    $sdkDir = $null
    if (-not $Ndk -and -not $env:ANDROID_NDK_HOME) { $sdkDir = FindSdk $Sdk }
    $ndkDir = FindNdk $sdkDir $Ndk
    $ndkBin = NdkLlvmBin $ndkDir
    $sysroot = Under (Split-Path -Parent $ndkBin) 'sysroot'
    if (-not (Test-Path -LiteralPath (Under $sysroot 'usr\include\android\api-level.h'))) { Fail "no sysroot in $sysroot" }
    # the LLVM clang of the PC install: -Clang, NX_LLVM, _prepare\toolchain.json, %ProgramFiles%\LLVM, the PATH; never the NDK's
    $cands = @($Clang)
    if ($env:NX_LLVM) { $cands += (Join-Path $env:NX_LLVM "clang++$Exe") }
    $tc = Under $Root '_prepare\toolchain.json'
    if (Test-Path -LiteralPath $tc) {
        try { $j = Get-Content -LiteralPath $tc -Raw | ConvertFrom-Json; if ($j.PSObject.Properties['llvm_bin']) { $cands += (Join-Path "$($j.llvm_bin)" "clang++$Exe") } } catch { }
    }
    if ($OnWindows) { $cands += (Join-Path $env:ProgramFiles 'LLVM\bin\clang++.exe') }
    else { $cands += @(Get-ChildItem -Path (Join-Path $HOME '.local/tsuki-tools/llvm-*/bin/clang++') -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
    $c = Get-Command "clang++$Exe" -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    $cxx = $null
    foreach ($x in $cands) { if ($x -and (Test-Path -LiteralPath $x) -and -not $x.StartsWith($ndkDir)) { $cxx = $x; break } }
    if (-not $cxx) { Fail 'no LLVM clang++ found outside the NDK (-Clang <path to clang++>)' }
    $bin = Split-Path -Parent $cxx
    Info "clang++ $cxx"
    Info ((& $cxx --version | Select-Object -First 1))
    $targets = (& $cxx --print-targets) -join "`n"
    $hasArm = $targets -match '(?m)^\s*aarch64\s'
    $lld = Join-Path $bin "ld.lld$Exe"
    Info "AArch64 target: $hasArm; ld.lld: $(Test-Path -LiteralPath $lld)"
    Info "sysroot $sysroot (NDK $(Split-Path -Leaf $ndkDir))"
    if (-not $hasArm) { Fail 'this clang has no AArch64 target' }

    Step "compile (-Files $Files per module; 0 = all)"
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    $flags = @('--target=aarch64-linux-android30', "--sysroot=$sysroot", '-std=c++20', '-O2', '-fPIC', '-fvisibility=hidden',
               '-fno-semantic-interposition', '-march=armv8.2-a+crypto', '-Wno-unused-label', '-Wno-unused-value',
               '-Wno-parentheses-equality', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
               '-Wno-constant-logical-operand', '-Wno-unused-function', "-I$(Under $Root 'framework\runtime\include')")
    $objs = @(); $failed = 0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    foreach ($mod in @('main', 'sdk')) {
        $src = @(Get-ChildItem -LiteralPath (Under $gen $mod) -Filter '*.cpp' | Sort-Object Name)
        $table = @($src | Where-Object { $_.Name -eq 'table.cpp' })
        $code = @($src | Where-Object { $_.Name -ne 'table.cpp' })
        if ($Files -gt 0) { $code = @($code | Select-Object -First $Files) }
        foreach ($f in @($code + $table)) {
            $o = Join-Path $out "${mod}_$($f.BaseName).o"
            $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            $msg = @(& $cxx @flags "-I$(Under $gen $mod)" '-c' $f.FullName '-o' $o 2>&1 | ForEach-Object { "$_" })
            $code2 = $LASTEXITCODE; $ErrorActionPreference = $old
            if ($code2) { $failed++; Warn "$mod\$($f.Name) failed:"; $msg | Select-Object -First 12 | ForEach-Object { Write-Host "      $_" } }
            else { $objs += $o }
        }
        Info "${mod}: $($code.Count + $table.Count) of $($src.Count) files tried"
    }
    $sw.Stop()
    Info ("compiled {0} objects, {1} failed, in {2:n1} s (one at a time; the real build runs them in parallel)" -f $objs.Count, $failed, $sw.Elapsed.TotalSeconds)

    Step 'link (shared library, symbols of the runtime left open)'
    $linked = $false
    if ($objs.Count -and (Test-Path -LiteralPath $lld)) {
        $so = Join-Path $out 'libprobe.so'
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        $largs = @('--target=aarch64-linux-android30', "--sysroot=$sysroot", '-fuse-ld=lld', '-shared', '-nostdlib',
                   '-Wl,--unresolved-symbols=ignore-all', '-Wl,-z,max-page-size=16384', '-o', $so) + $objs
        $msg = @(& $cxx @largs 2>&1 | ForEach-Object { "$_" })
        $code2 = $LASTEXITCODE; $ErrorActionPreference = $old
        if ($code2) { Warn 'link failed:'; $msg | Select-Object -First 12 | ForEach-Object { Write-Host "      $_" } }
        else { $linked = $true; Info ("libprobe.so: {0:n1} MB" -f ((Get-Item -LiteralPath $so).Length / 1MB)) }
    } else { Warn 'not tried (no objects, or no ld.lld next to clang++)' }

    Write-Host ''
    Write-Host ("RESULT: compile {0}, link {1}" -f $(if ($objs.Count -and -not $failed) { 'OK' } else { 'FAILED' }), $(if ($linked) { 'OK' } else { 'FAILED' }))
} catch {
    Write-Host ''
    Write-Host "ERROR in android_llvm_probe: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
