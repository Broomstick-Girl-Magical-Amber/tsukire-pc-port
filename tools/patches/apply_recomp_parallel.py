"""apply_recomp_parallel.py - recomp_tsukihime.ps1 does independent work at the same time. Marker AP_RECOMP_PARALLEL.

1. Step 6: main and sdk are recompiled at the same time, each one in its own process (the same regen_tsuki*.ps1 with
   -Modules main / -Modules sdk; same output files). sdk takes ~1 min and main ~13 s used to come after it.
2. HUD icons (build_hud_assets.py, ~3 min the first time per edition in the VM; less with apply_hud_fast.py): they
   need only the romfs, the mod layers (final after step 3) and utils\\, not the exe. When the run goes on to step 9,
   they start in the background at the first of steps 5-8 that runs (those are mostly single-threaded Python) and
   are joined at the START of step 9, before the compile (no CPU contention with it). From step 9 alone: as before,
   after the build.
3. Step 8: the shader harness uses CPUs - 1 jobs (at least 3, as before) instead of a fixed 3.
4. Step 9: -Jobs N limits the compile jobs (`cmake --build --parallel N`; 0 = ninja's default, CPUs + 2): the
   recompiled files are big and a PC with little RAM may swap. After the build, the time it took and where it went
   (tools\\dev\\ninja_times.py on what this build appended to .ninja_log: recompiled main / sdk, runtime, link,
   the slowest files): the measurement to decide later on ccache, precompiled headers or other build flags.
NX_RECOMP_SERIAL=1: everything one after the other, as before (1 and 2).

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_recomp_parallel.py [--remove]
Needs tools\\dev\\ninja_times.py (new file, delivered with this patch) for the summary of 4; without it, no summary.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_RECOMP_PARALLEL"
RECOMP = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECOMP,
     """    [ValidateRange(2, 600)][int]$ProgressSeconds = 30
)
""",
     """    [ValidateRange(2, 600)][int]$ProgressSeconds = 30,
    [ValidateRange(0, 512)][int]$Jobs = 0   # AP_RECOMP_PARALLEL: compile jobs of step 9 (0 = ninja's default, CPUs + 2)
)
"""),
    # ------------------------------------------------------------------------------------------ 1. step 6
    (RECOMP,
     """        $ra = @{ Modules = @('main', 'sdk'); Root = $Root }
        if ($script:UpdateMain) { $ra['MainNso'] = $script:UpdateMain }
        & $regenToRun @ra
""",
     """        if ($env:NX_RECOMP_SERIAL) {
            $ra = @{ Modules = @('main', 'sdk'); Root = $Root }
            if ($script:UpdateMain) { $ra['MainNso'] = $script:UpdateMain }
            & $regenToRun @ra
        } else { RegenParallel $regenToRun }   # AP_RECOMP_PARALLEL: main and sdk at the same time
"""),
    (RECOMP,
     """function Step6 {
""",
     r"""# AP_RECOMP_PARALLEL: main and sdk are independent: each one in its own process (the regen script with -Modules <module>),
# at the same time; the output of each one is shown when it finishes. NX_RECOMP_SERIAL=1: one after the other, as before.
function RegenParallel([string]$regen) {
    $host_ = (Get-Process -Id $PID).Path
    $runs = @()
    foreach ($m in 'main', 'sdk') {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $regen, '-Modules', $m, '-Root', $Root)
        if ($m -eq 'main' -and $script:UpdateMain) { $a += @('-MainNso', $script:UpdateMain) }
        $log = Join-Path ([IO.Path]::GetTempPath()) "tsuki_regen_$($m)_$([guid]::NewGuid().ToString('N')).log"
        $p = Start-Process -FilePath $host_ -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru `
             -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
        $runs += @{ M = $m; P = $p; Log = $log }
    }
    Info 'recompiling main and sdk at the same time (the output of each one is shown when it finishes)'
    $failed = @()
    foreach ($r in $runs) {
        $r.P.WaitForExit()
        $code = -1; try { $code = $r.P.ExitCode } catch { }
        if ($null -eq $code) { $code = -1 }
        $took = ''; try { $took = ' in {0:mm\:ss}' -f ($r.P.ExitTime - $r.P.StartTime) } catch { }
        Info "-- recomp $($r.M)$took$(if ($code -ne 0) { " FAILED (code $code)" })"
        foreach ($f in @($r.Log, "$($r.Log).err")) {
            if (Test-Path -LiteralPath $f) {
                foreach ($l in @(Get-Content -LiteralPath $f)) { if ($l -match '\S') { Info "   $l" } }
                Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
            }
        }
        if ($code -ne 0) { $failed += $r.M }
    }
    if ($failed.Count) { Fail "recompiling $($failed -join ' and ') failed (output above)" }
}

function Step6 {
"""),
    # ------------------------------------------------------------------------------------------ 3. step 8
    (RECOMP,
     """        RunVisible 'dotnet' @($Harness, '--batch', $in, '--out', $out, '--jobs', '3', '--spirv-val', (SpirvVal)) 'spirv_harness'
""",
     """        $hj = [Math]::Max(3, [Environment]::ProcessorCount - 1)   # AP_RECOMP_PARALLEL: was a fixed 3
        RunVisible 'dotnet' @($Harness, '--batch', $in, '--out', $out, '--jobs', "$hj", '--spirv-val', (SpirvVal)) 'spirv_harness'
"""),
    # ------------------------------------------------------------------------------------------ 2. HUD in the background
    (RECOMP,
     """function HudAssets {
    $script = Under $Tools 'hud\\build_hud_assets.py'
    if (-not (Test-Path -LiteralPath $script)) { Warn "$script is missing: HUD icons by device skipped"; return }
    Info 'HUD icons by device (tools\\hud\\build_hud_assets.py; the keyboard tiles take about a minute the first time)'
    $r = RunCapture $Python @($script, '--edition', $Edition, '--root', $Root)
""",
     r"""# AP_RECOMP_PARALLEL: the HUD icons need only the romfs, the mod layers (final after step 3) and utils\: when the run goes
# on to step 9 they are built in the background from the first of steps 5-8 that runs, and joined at the start of step 9.
$script:HudJob = $null
$script:HudDone = $false
function StartHudBackground {
    if ($script:HudJob -or $script:HudDone -or $env:NX_RECOMP_SERIAL) { return }
    $py = Under $Tools 'hud\build_hud_assets.py'
    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $Python)) { return }
    $log = Join-Path ([IO.Path]::GetTempPath()) "tsuki_hud_$($Edition)_$([guid]::NewGuid().ToString('N')).log"
    try {
        $p = Start-Process -FilePath $Python -ArgumentList ((@($py, '--edition', $Edition, '--root', $Root) | ForEach-Object { Quote $_ }) -join ' ') `
             -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        $null = $p.Handle
        $script:HudJob = @{ P = $p; Log = $log }
        Info 'HUD icons by device: building in the background meanwhile (shown at the start of step 9)'
    } catch { }
}

function HudAssets {
    if ($script:HudDone) { return }   # AP_RECOMP_PARALLEL: already done (in the background, joined before the build)
    $script:HudDone = $true
    $script = Under $Tools 'hud\build_hud_assets.py'
    if (-not (Test-Path -LiteralPath $script)) { Warn "$script is missing: HUD icons by device skipped"; return }
    if ($script:HudJob) {
        $j = $script:HudJob; $script:HudJob = $null
        if (-not $j.P.HasExited) { Info 'HUD icons by device: waiting for the background build...' }
        $j.P.WaitForExit()
        $code = -1; try { $code = $j.P.ExitCode } catch { }
        if ($null -eq $code) { $code = -1 }
        Info 'HUD icons by device (tools\hud\build_hud_assets.py, built in the background)'
        $lines = @()
        foreach ($f in @($j.Log, "$($j.Log).err")) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f); Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } }
        $r = @{ Code = $code; Output = $lines }
    } else {
        Info 'HUD icons by device (tools\hud\build_hud_assets.py)'
        $r = RunCapture $Python @($script, '--edition', $Edition, '--root', $Root)
    }
"""),
    (RECOMP,
     """function Step9 {
    $cache = Under $Build 'CMakeCache.txt'
""",
     """function Step9 {
    if ($script:HudJob) { HudAssets }   # AP_RECOMP_PARALLEL: the background HUD build ends before the compile starts
    $cache = Under $Build 'CMakeCache.txt'
"""),
    # ------------------------------------------------------------------------------------------ 4. -Jobs and the build times
    (RECOMP,
     """        RunVisible 'cmake' @('--build', $Build) 'cmake --build'
""",
     r"""        # AP_RECOMP_PARALLEL: -Jobs N limits the compile jobs; afterwards, where the time of this build went
        $nlog = Under $Build '.ninja_log'
        $nOff = [int64]0; if (Test-Path -LiteralPath $nlog) { $nOff = (Get-Item -LiteralPath $nlog).Length }
        $ba = @('--build', $Build); if ($Jobs -gt 0) { $ba += @('--parallel', "$Jobs") }
        $t0 = Get-Date
        RunVisible 'cmake' $ba 'cmake --build'
        Info ('cmake --build: {0:hh\:mm\:ss}' -f ((Get-Date) - $t0))
        BuildTimes $nlog $nOff
"""),
    (RECOMP,
     """function CacheValue([string]$cache, [string]$k) {
""",
     """# AP_RECOMP_PARALLEL: summary of what one build appended to .ninja_log (tools\\dev\\ninja_times.py); never fatal.
function BuildTimes([string]$log, [int64]$offset) {
    $nt = Under $Tools 'dev\\ninja_times.py'
    if (-not (Test-Path -LiteralPath $nt) -or -not (Test-Path -LiteralPath $log)) { return }
    try { $r = RunCapture $Python @($nt, $log, '--offset', "$offset"); if ($r.Code -eq 0) { foreach ($l in $r.Output) { Info $l } } } catch { }
}

function CacheValue([string]$cache, [string]$k) {
"""),
    # ------------------------------------------------------------------------------------------ main loop
    (RECOMP,
     """        Step $n $StepNames[$n]
        & "Step$n"
""",
     """        Step $n $StepNames[$n]
        if ($n -ge 5 -and $n -le 8 -and $To -ge 9) { StartHudBackground }   # AP_RECOMP_PARALLEL
        & "Step$n"
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
