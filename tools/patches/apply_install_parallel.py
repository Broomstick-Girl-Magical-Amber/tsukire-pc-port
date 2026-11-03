"""apply_install_parallel.py - install_requirements.ps1 does independent work at the same time. Marker AP_INSTALL_PARALLEL.

Measured in a clean Windows VM (2026-10-31, 12:31 in total): step 1 6:38 (VS Build Tools 3:54, LLVM 0:56, .NET 0:52,
Vulkan SDK 0:30, winget 0:24), step 4 0:35, step 5 2:55, step 6 0:39, step 8 0:58. Everything ran one after the other.
Three overlaps, none of which changes what gets installed or where:

1. Step 1 (elevated window): LLVM and the Vulkan SDK use neither winget nor Windows Installer (MSI), so each one
   installs in its own elevated process while VS Build Tools, Git and .NET (winget / MSI: one at a time) install as
   before. LLVM: the NSIS installer at the URL of the winget manifest, checked against the manifest's SHA-256 (the
   same file and check winget uses; the URL and SHA-256 are read with `winget show` BEFORE VS starts, so two winget
   never run at once). If a parallel install fails it is retried in the usual way, one at a time. ~1:25 less.
2. While that window installs, this one prepares what later steps download: FFmpeg (step 5), the ryubing
   tarball (step 8, without the harness build) and the vcpkg clone + bootstrap (step 4, only if Git is
   already there). A failure there is only a warning: the step tries again in its turn. ~1:30 hidden behind VS.
3. Step 5 (vcpkg builds opus, libjpeg-turbo, libpng, zlib, sdl3: ~2:20) runs in a background process (this same
   script with -Worker -From 5 -To 5, output in _prepare\\vcpkg_background_<date>.log) while steps 6-9 run (and, with
   -Recomp, while the dump is extracted and recompiled: recomp steps 0-8 do not use vcpkg). It is joined before
   anything needs it (step 10 / the build) and then checked in line. If something else fails meanwhile, the script
   waits for it before exiting (Ctrl+C stops both).
   New order: 1 2 3 4 [5 starts] 6 7 8 9 [5 joined] 10 11; with -Recomp: 1 2 3 4 [5 starts] 6 7 8 R1 [5 joined] R2 9 10 11.
Expected on that VM: ~12:30 -> ~8:30 (estimate; the step times of _prepare\\step_times.log will tell).
NX_SERIAL_INSTALL=1: step 1 one tool after the other, as before. -Check is unchanged (no background work).

Needs AP_INSTALL_FLOW (TimedStep, -Recomp) and AP_TOOL_TIMES in install_requirements.ps1 (both are in the repo).
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_install_parallel.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_INSTALL_PARALLEL"
INSTALL = "../tools/install_requirements.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------ parameters
    (INSTALL,
     """    [string]$ResultFile = '',
    [switch]$FunctionsOnly
)
""",
     """    [string]$ResultFile = '',
    [switch]$FunctionsOnly,
    # AP_INSTALL_PARALLEL (internal): -AdminChild = one tool installed in parallel by the elevated instance;
    # -Worker = step 5 in the background (StartVcpkgWorker)
    [switch]$AdminChild,
    [switch]$Worker
)
"""),
    # ------------------------------------------------------------------------------------------ LLVM without winget in a child
    (INSTALL,
     """        'vulkan' { InstallVulkan }
        default { InstallWinget $key }
""",
     """        'vulkan' { InstallVulkan }
        'llvm' { if ($AdminChild) { InstallLlvmDirect } else { InstallWinget 'llvm' } }   # AP_INSTALL_PARALLEL
        default { InstallWinget $key }
"""),
    # ------------------------------------------------------------------------------------------ helpers (before the elevated instance)
    (INSTALL,
     """# Elevated instance: this same script is relaunched with -AdminOnly (a single UAC) and leaves the result in a JSON.
""",
     r"""# ---------------------------------------------------------------------------------------------- AP_INSTALL_PARALLEL
# LLVM and the Vulkan SDK use neither winget nor Windows Installer (MSI): the elevated instance installs them in their own
# elevated processes (-AdminChild) while VS Build Tools / Git / .NET (winget, MSI: one at a time) install in it.
# LLVM in a child: the NSIS installer of the winget manifest (URL and SHA-256 read with `winget show` before anything
# else starts, so two winget never run at the same time), checked against that SHA-256 and run with /S like winget does.
function ResolveLlvmInstaller {
    if ($env:NX_LLVM_URL -and $env:NX_LLVM_SHA) { return $true }
    try {
        if (-not (WingetAvailable)) { return $false }
        $h = $ToolSpecs.llvm
        $show = RunCapture 'winget' @('show', '--id', $h.Winget, '--exact', '--versions', '--source', 'winget', '--accept-source-agreements')
        $ver = PickVersion 'llvm' $show.Output
        if (-not $ver) { return $false }
        $m = RunCapture 'winget' @('show', '--id', $h.Winget, '--exact', '--version', $ver, '--source', 'winget', '--accept-source-agreements')
        $txt = $m.Output -join "`n"
        $u = [regex]::Match($txt, 'https://\S+?/LLVM-[0-9.]+-win64\.exe')
        $s = @([regex]::Matches($txt, '(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])') | ForEach-Object { $_.Value.ToUpper() } | Sort-Object -Unique)
        if (-not $u.Success -or $s.Count -ne 1) { return $false }   # the text of winget show is localized: only the URL and the hash are read
        $env:NX_LLVM_URL = $u.Value; $env:NX_LLVM_SHA = $s[0]; $env:NX_LLVM_VER = $ver
        return $true
    } catch { return $false }
}
function InstallLlvmDirect {
    $url = $env:NX_LLVM_URL; $sha = "$env:NX_LLVM_SHA"
    if (-not $url -or $sha -notmatch '^[0-9A-F]{64}$') { Fail 'no LLVM installer URL / SHA-256 from winget' }
    if ($env:NX_LLVM_VER -and $env:NX_LLVM_VER -ne $ToolSpecs.llvm.Pin) { Warn "winget no longer has LLVM $($ToolSpecs.llvm.Pin): installing $($env:NX_LLVM_VER) (meets '$($ToolSpecs.llvm.Rule)')" }
    $dl = PathOf $WorkDir 'downloads'
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    $exe = PathOf $dl (Split-Path $url -Leaf)
    if (-not (Test-Path -LiteralPath $exe) -or (Sha256 $exe) -ne $sha) { Download $url $exe }
    if ((Sha256 $exe) -ne $sha) { Remove-Item -LiteralPath $exe -Force; Fail 'the LLVM installer does not match the SHA-256 of the winget manifest' }
    Info "LLVM $(Split-Path $url -Leaf): SHA-256 OK (the one of the winget manifest)"
    $p = Start-Process -FilePath $exe -ArgumentList '/S' -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "the LLVM installer finished with code $($p.ExitCode)" }
    if (Get-Command CleanDownload -ErrorAction SilentlyContinue) { CleanDownload $exe }
}
function AdminInstallOne([string]$c, $r) {
    Write-Host "== $($ToolSpecs[$c].Name)" -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()   # AP_TOOL_TIMES
    try { InstallTool $c; $r[$c] = 'ok' } catch { $r[$c] = "error: $($_.Exception.Message)"; Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red }
    $sw.Stop()
    $r["${c}_seconds"] = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
    Write-Host ('   (took {0})' -f $sw.Elapsed.ToString('hh\:mm\:ss\.f'))
}
function StartParallelInstalls([string[]]$all) {
    $kids = @{}
    if ($AdminChild -or $env:NX_SERIAL_INSTALL -or -not $OnWindows) { return $kids }
    $par = @($all | Where-Object { $_ -in 'llvm', 'vulkan' })
    if (-not $par.Count -or $par.Count -eq $all.Count) { return $kids }   # nothing to overlap with
    $host_ = (Get-Process -Id $PID).Path
    foreach ($c in $par) {
        if ($c -eq 'llvm' -and -not (ResolveLlvmInstaller)) { Info 'LLVM: no installer URL / SHA-256 from winget show: installed here, with winget'; continue }
        $tag = "${c}_$(Stamp)"
        $res = PathOf $WorkDir "admin_$tag.json"; $out = PathOf $WorkDir "admin_$tag.out"
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-AdminOnly', '-AdminChild', '-List', $c, '-ResultFile', $res, '-Root', $Root)
        try {
            $p = Start-Process -FilePath $host_ -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError "$out.err"
            $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
            $kids[$c] = @{ P = $p; Res = $res; Out = $out }
            Write-Host "== $($ToolSpecs[$c].Name): installing in parallel (its output is shown when it finishes)" -ForegroundColor Cyan
        } catch { Info "$($ToolSpecs[$c].Name): could not start it in parallel ($($_.Exception.Message)): installed here" }
    }
    return $kids
}
function FinishParallelInstalls($kids, $r) {
    foreach ($c in @($kids.Keys)) {
        $k = $kids[$c]
        $k.P.WaitForExit()
        Write-Host "== $($ToolSpecs[$c].Name) (in parallel)" -ForegroundColor Cyan
        foreach ($f in @($k.Out, "$($k.Out).err")) {
            if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f | Where-Object { $_ -match '\S' } | ForEach-Object { Write-Host "   | $_" }; Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
        }
        $ok = $false; $why = 'no result'
        if (Test-Path -LiteralPath $k.Res) {
            try {
                $j = Get-Content -LiteralPath $k.Res -Raw | ConvertFrom-Json
                $pr = $j.PSObject.Properties[$c]; $tp = $j.PSObject.Properties["${c}_seconds"]
                if ($tp) { $r["${c}_seconds"] = $tp.Value }
                if ($pr) { $why = "$($pr.Value)"; $ok = ($why -eq 'ok') }
            } catch { $why = "unreadable result ($($_.Exception.Message))" }
            Remove-Item -LiteralPath $k.Res -Force -ErrorAction SilentlyContinue
        }
        if ($ok) { $r[$c] = 'ok' }
        else { Write-Host "   in parallel: $why; installing it again here, the usual way" -ForegroundColor Yellow; AdminInstallOne $c $r }
    }
}

# While the elevated window installs (step 1), this one prepares what later steps download. Each step checks its own part
# again in its turn: here a failure is only a warning. Nothing here needs administrator or the tools of step 1 (vcpkg is
# cloned only if Git is already installed).
$script:RyubingOnly = $false
function PrefetchOne([string]$what, [scriptblock]$sb) {
    $n = $script:Done.Count
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { & $sb; Info ('{0}: ready ({1:N1} s)' -f $what, $sw.Elapsed.TotalSeconds) }
    catch { Warn "$what could not be prepared now ($($_.Exception.Message)): its step tries again" }
    finally { $script:RyubingOnly = $false }
    while ($script:Done.Count -gt $n) { $script:Done.RemoveAt($n) }   # the step itself reports it in the summary
}
function Prefetch {
    if ($Check -or $AdminOnly) { return }
    Info 'while that window installs, this one prepares what the next steps download:'
    if ($From -le 5 -and $To -ge 5) { PrefetchOne 'FFmpeg (step 5)' { EnsureFfmpeg } }
    if ($From -le 8 -and $To -ge 8 -and -not (Test-Path -LiteralPath (PathOf $Framework 'private\third_party\ryubing'))) {
        PrefetchOne 'ryubing (step 8, without the harness)' { $script:RyubingOnly = $true; Step8 }
    }
    if ($From -le 4 -and $To -ge 4 -and -not (Test-Path -LiteralPath $Vcpkg) -and (DetectGit)) { PrefetchOne 'vcpkg clone + bootstrap (step 4)' { Step4 } }
    Info 'waiting for the administrator window to finish...'
}

# Elevated instance: this same script is relaunched with -AdminOnly (a single UAC) and leaves the result in a JSON.
"""),
    # ------------------------------------------------------------------------------------------ prefetch while elevated
    (INSTALL,
     """    try { $p = Start-Process -FilePath $host_ -Verb RunAs -Wait -PassThru -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') }
    catch { Fail "administrator permission was not granted ($($_.Exception.Message))" }
    if (-not (Test-Path -LiteralPath $res)) { Fail "the administrator installation left no result (code $($p.ExitCode)); see the admin_*.log in $WorkDir" }
""",
     """    try { $p = Start-Process -FilePath $host_ -Verb RunAs -PassThru -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') }
    catch { Fail "administrator permission was not granted ($($_.Exception.Message))" }
    try { $null = $p.Handle } catch { }
    Prefetch                                  # AP_INSTALL_PARALLEL: meanwhile, the downloads of later steps
    $p.WaitForExit()
    $ec = '?'; try { $ec = $p.ExitCode } catch { }
    if (-not (Test-Path -LiteralPath $res)) { Fail "the administrator installation left no result (code $ec); see the admin_*.log in $WorkDir" }
"""),
    # ------------------------------------------------------------------------------------------ elevated instance
    (INSTALL,
     """    $log = PathOf $WorkDir "admin_$(Stamp).log"
    try { Start-Transcript -Path $log | Out-Null } catch { }
    RefreshEnv
    $r = [ordered]@{}
    foreach ($c in ($List -split ',' | Where-Object { $_ })) {
        Write-Host "== $($ToolSpecs[$c].Name)" -ForegroundColor Cyan
        $sw = [Diagnostics.Stopwatch]::StartNew()   # AP_TOOL_TIMES
        try { InstallTool $c; $r[$c] = 'ok' } catch { $r[$c] = "error: $($_.Exception.Message)"; Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red }
        $sw.Stop()
        $r["${c}_seconds"] = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
        Write-Host ('   (took {0})' -f $sw.Elapsed.ToString('hh\\:mm\\:ss\\.f'))
    }
""",
     """    $log = PathOf $WorkDir "admin_$(Stamp)_$(($List -split ',' | Where-Object { $_ }) -join '-').log"   # AP_INSTALL_PARALLEL: one log per instance
    try { Start-Transcript -Path $log | Out-Null } catch { }
    RefreshEnv
    $r = [ordered]@{}
    $all = @($List -split ',' | Where-Object { $_ })
    $kids = StartParallelInstalls $all        # AP_INSTALL_PARALLEL: LLVM / Vulkan SDK in their own processes
    foreach ($c in @($all | Where-Object { -not $kids.ContainsKey($_) })) { AdminInstallOne $c $r }
    FinishParallelInstalls $kids $r
"""),
    # ------------------------------------------------------------------------------------------ ryubing only (prefetch)
    (INSTALL,
     """    $dll = PathOf $Framework 'private\\build\\spirv_harness\\bin\\Release\\net10.0\\spirv_harness.dll'
""",
     """    if ($script:RyubingOnly) { return }   # AP_INSTALL_PARALLEL: prefetch (the harness needs .NET and the venv: step 8 itself)
    $dll = PathOf $Framework 'private\\build\\spirv_harness\\bin\\Release\\net10.0\\spirv_harness.dll'
"""),
    # ------------------------------------------------------------------------------------------ step times of the worker
    (INSTALL,
     """function ShowTimes {
    if (-not $script:Times.Count) { return }
""",
     """function ShowTimes {
    if ($Worker -or -not $script:Times.Count) { return }   # AP_INSTALL_PARALLEL: the background step 5 is shown and recorded by the main run
"""),
    # ------------------------------------------------------------------------------------------ background step 5
    (INSTALL,
     """function ResolveRoot {
""",
     r"""# AP_INSTALL_PARALLEL: step 5 (vcpkg builds its ports, minutes of CPU) in a background process while the next steps run.
# It is this same script with -Worker -From 5 -To 5 -NonInteractive; its output goes to _prepare\vcpkg_background_<date>.log.
$script:VcpkgWorker = $null
function StartVcpkgWorker {
    if ($Check -or $Worker -or $From -gt 5 -or $To -lt 5 -or $env:NX_SERIAL_INSTALL) { return }
    if (-not (Test-Path -LiteralPath (VcpkgExe))) { return }   # step 4 did not leave vcpkg: step 5 in line says why
    if (-not @(MissingPorts).Count -and (FfmpegReady (PathOf $WorkDir "ffmpeg\$FfmpegDirName"))) { return }   # nothing to build
    $out = PathOf $WorkDir "vcpkg_background_$(Stamp).log"
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Worker', '-From', '5', '-To', '5', '-NonInteractive',
           '-Root', $Root, '-Vcpkg', $Vcpkg, '-Edition', $Edition)
    if ($VcpkgWithDebug) { $a += '-VcpkgWithDebug' }
    if ($VcpkgCache) { $a += @('-VcpkgCache', $VcpkgCache) }
    if ($OnLinux) { $a += @('-XwinCrt', $XwinCrt, '-XwinSdk', $XwinSdk) }
    $p = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru `
         -RedirectStandardOutput $out -RedirectStandardError "$out.err"
    $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
    $script:VcpkgWorker = @{ P = $p; Out = $out; Sw = [Diagnostics.Stopwatch]::StartNew() }
    Write-Host ''
    Write-Host '== step 5: vcpkg libraries -> in the background while the next steps run' -ForegroundColor Cyan
    Info "output: $out"
}
function JoinVcpkgWorker {
    $w = $script:VcpkgWorker
    if (-not $w) { return }
    $script:VcpkgWorker = $null
    $wait = [Diagnostics.Stopwatch]::StartNew()
    if (-not $w.P.HasExited) { Write-Host ''; Info 'waiting for the vcpkg libraries (step 5, in the background)...' }
    $w.P.WaitForExit()
    $wait.Stop(); $w.Sw.Stop()
    $code = -1
    try { $code = $w.P.ExitCode } catch { }
    if ($null -eq $code) { $code = -1 }
    $lines = @()
    foreach ($f in @($w.Out, "$($w.Out).err")) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f | Where-Object { $_ -match '\S' }) } }
    $err = "$($w.Out).err"
    if ((Test-Path -LiteralPath $err) -and (Get-Item -LiteralPath $err).Length -eq 0) { Remove-Item -LiteralPath $err -Force -ErrorAction SilentlyContinue }
    # Only the time spent waiting for it counts in the table (the rest overlapped other steps), so the total stays wall time.
    [void]$script:Times.Add(@('5', ('vcpkg libraries (bg {0}; waited)' -f (FormatSpan $w.Sw.Elapsed)), $wait.Elapsed))
    Write-Host ''
    Write-Host ('== step 5: vcpkg libraries (in the background) finished: {0} in total, {1} of them waited for here' -f (FormatSpan $w.Sw.Elapsed), (FormatSpan $wait.Elapsed)) -ForegroundColor Cyan
    foreach ($l in @($lines | Select-Object -Last 8)) { Info "| $l" }
    if ($code -ne 0) { $script:CurrentStep = 5; Fail "step 5 (vcpkg libraries, in the background) failed (code $code); its whole output is in $($w.Out)" }
}

function ResolveRoot {
"""),
    # ------------------------------------------------------------------------------------------ main: order and phases
    (INSTALL,
     """        TimedStep 0 $StepNames[0] 'Step0'
        CheckInputs
        $order = @(1..11)
        if ($Recomp -and $Check) { Info '-Recomp is not run with -Check (the inputs above were checked)' }
        elseif ($Recomp) { $order = @(1, 2, 3, 6, 7, 8, 'R1', 4, 5, 'R2', 9, 10, 11) }   # fail early: everything but the build before vcpkg
        foreach ($n in $order) {
            if ($n -is [string]) {
""",
     """        if (-not $Worker) { TimedStep 0 $StepNames[0] 'Step0'; CheckInputs }
        $order = @(1..11)
        # AP_INSTALL_PARALLEL: V5 starts step 5 in the background (vcpkg), J5 waits for it and checks it in line
        if (-not $Check) { $order = @(1, 2, 3, 4, 'V5', 6, 7, 8, 9, 'J5', 10, 11) }
        if ($Worker) { $order = @(5) }
        elseif ($Recomp -and $Check) { Info '-Recomp is not run with -Check (the inputs above were checked)' }
        elseif ($Recomp) { $order = @(1, 2, 3, 4, 'V5', 6, 7, 8, 'R1', 'J5', 'R2', 9, 10, 11) }   # inputs checked first (CheckInputs); recomp 0-8 does not need vcpkg
        foreach ($n in $order) {
            if ($n -is [string]) {
                if ($n -eq 'V5' -and $From -le 5 -and $To -ge 5) {
                    $script:CurrentStep = 5
                    StartVcpkgWorker
                    if (-not $script:VcpkgWorker) { TimedStep 5 $StepNames[5] 'Step5' }
                }
                if ($n -eq 'J5' -and $script:VcpkgWorker) { JoinVcpkgWorker; $script:CurrentStep = 5; TimedStep 5 "$($StepNames[5]) (check)" 'Step5' }
"""),
    (INSTALL,
     """        if ($To -lt 11) { Write-Host ''; Write-Host "   (summary: step 11)" }
""",
     """        if ($To -lt 11 -and -not $Worker) { Write-Host ''; Write-Host "   (summary: step 11)" }   # AP_INSTALL_PARALLEL
"""),
    (INSTALL,
     """    } finally {
        ShowTimes
""",
     """    } finally {
        if ($script:VcpkgWorker) {   # AP_INSTALL_PARALLEL: something else failed while step 5 was building: wait for it
            try { JoinVcpkgWorker } catch { Write-Host "   $($_.Exception.Message)" -ForegroundColor Red; $code = 1 }
        }
        ShowTimes
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
