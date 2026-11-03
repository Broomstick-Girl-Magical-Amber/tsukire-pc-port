"""apply_install_flow.py - step timing + one-command install/recompile (marker AP_INSTALL_FLOW).

A. Timing: install_requirements.ps1 and recomp_tsukihime.ps1 print how long each step took, a table with the total at
   the end (also when a step fails) and append the same lines to <root>\\_prepare\\step_times.log (not versioned).
B. One command, no new entry script:
   - install_requirements.ps1 -Recomp -Edition global|japan|both  installs the tools AND recompiles the edition(s).
   - It first checks (read only, prod.keys is never read) that the keys and the dump(s) are where they have to be, so a
     bad input fails in seconds, not after the long steps.
   - Order with -Recomp: tools (1,2,3,6,7,8) -> recomp steps 0-8 (identify, extract, analysis, recomp, shaders) ->
     vcpkg (4,5) -> recomp steps 9-10 (build). A wrong dump / missing keys / no disk is found before vcpkg.
   - It runs recomp_tsukihime.ps1 in a new process of its own, so there is no need to close the console.
   - -InputFile and -Keys are passed through to recomp_tsukihime.ps1.
   - Step 3 no longer fails when the root is not a git repo (a ZIP download): it only warns.
   - Step 11 no longer fails with ONE edition in Windows PowerShell 5.1 (bug of the original: $eds.Count on a string).
   - Times are shown with tenths of a second so short steps do not add up to 0.
   - recomp_tsukihime.ps1 loads the build environment (dev_env.ps1) also when step 8 (shaders) runs, because step 8
     needs spirv-val from VULKAN_SDK; before it was only loaded when cmake/clang-cl were missing or for step 9.
Without -Recomp everything works as before (plus the timing).

Edits ../tools/install_requirements.ps1 and ../tools/recomp_tsukihime.ps1 (row in $Markers for the first one).
Idempotent, all or nothing, keeps CRLF/LF and BOM. Does not touch framework\\.
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_install_flow.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_INSTALL_FLOW"
INST = "../tools/install_requirements.ps1"
RECO = "../tools/recomp_tsukihime.ps1"

# ------------------------------------------------------------------------------------------ install_requirements.ps1
INST_USAGE_OLD = (
    "#   .\\install_requirements.ps1 -CrossRecomp            # at the end, recomp_tsukihime.ps1 -From 0 -To 0 of the chosen editions\n"
)
INST_USAGE_NEW = INST_USAGE_OLD + (
    "#   .\\install_requirements.ps1 -Recomp -Edition global # AP_INSTALL_FLOW: tools + recompile that edition in ONE run\n"
    "#       checks keys + dump first (read only), extracts and translates shaders BEFORE vcpkg, builds after it;\n"
    "#       needs -Edition global|japan|both (one dump per edition in the root, or -InputFile / -Keys as in recomp_tsukihime.ps1)\n"
    "#   Every run prints the time of each step and appends it to <root>\\_prepare\\step_times.log\n"
)

INST_PARAM_OLD = "    [switch]$CrossRecomp,\n"
INST_PARAM_NEW = (
    "    [switch]$CrossRecomp,\n"
    "    [switch]$Recomp,\n"
    "    [string]$InputFile = '',\n"
    "    [string]$Keys = '',\n"
)

INST_BOUND_OLD = "$ProgressPreference = 'SilentlyContinue'   # the Invoke-WebRequest progress bar of PowerShell 5.1 makes downloads very slow\n"
INST_BOUND_NEW = INST_BOUND_OLD + "$EditionGiven = $PSBoundParameters.ContainsKey('Edition')   # AP_INSTALL_FLOW\n"

INST_STEP3_OLD = (
    "    if (-not (Test-Path -LiteralPath (PathOf $Root '.git'))) { Problem \"$Root is not a git repo: clone the port repo into that folder\" }\n"
)
INST_STEP3_NEW = (
    "    if (-not (Test-Path -LiteralPath (PathOf $Root '.git'))) { Warn \"$Root is not a git repo (downloaded as a ZIP?): going on; git is only needed to update the port with git pull\" }\n"
)

INST_FUNCS_OLD = "function ResolveRoot {\n"
INST_FUNCS_NEW = r"""# ---------------------------------------------------------------------------------------------- AP_INSTALL_FLOW
# Step timing and the -Recomp flow (see tools/patches/apply_install_flow.py).
$script:Times = New-Object System.Collections.ArrayList
$script:InRecomp = ''
$script:RecompBuilt = @()

function FormatSpan([TimeSpan]$t) { '{0:00}:{1:00}:{2:00}.{3}' -f [int][Math]::Floor($t.TotalHours), $t.Minutes, $t.Seconds, [int][Math]::Floor($t.Milliseconds / 100) }

# Runs the function named $fn as step $n (a number, or R1/R2 for the recomp phases) and records how long it took.
function TimedStep([string]$n, [string]$name, [string]$fn) {
    Write-Host ''; Write-Host "== step ${n}: $name" -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { & $fn }
    finally {
        $sw.Stop()
        [void]$script:Times.Add(@($n, $name, $sw.Elapsed))
        Info ('(took {0})' -f (FormatSpan $sw.Elapsed))
    }
}

function ShowTimes {
    if (-not $script:Times.Count) { return }
    Write-Host ''
    Write-Host '   Step times:' -ForegroundColor Green
    $total = [TimeSpan]::Zero
    foreach ($t in $script:Times) { $total += $t[2]; Write-Host ('     {0,-3} {1,-46} {2}' -f $t[0], $t[1], (FormatSpan $t[2])) }
    Write-Host ('     {0,-50} {1}' -f 'total', (FormatSpan $total))
    if ($Check) { return }
    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $lines = @(foreach ($t in $script:Times) { "$stamp`tinstall_requirements`t$Edition`t$($t[0])`t$($t[1])`t$(FormatSpan $t[2])" })
        [IO.File]::AppendAllText((PathOf $script:WorkDir 'step_times.log'), (($lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
    } catch { }
}

# NSP (PFS0 at 0) or XCI (HEAD at 0x100) by the header only: no keys, nothing else of the file is read.
function InputKind([string]$p) {
    $n = 0
    try {
        $fs = [IO.File]::OpenRead($p)
        try { $b = New-Object byte[] 0x104; $n = $fs.Read($b, 0, $b.Length) } finally { $fs.Dispose() }
    } catch { return $null }
    if ($n -ge 4 -and [Text.Encoding]::ASCII.GetString($b, 0, 4) -eq 'PFS0') { return 'nsp' }
    if ($n -ge 0x104 -and [Text.Encoding]::ASCII.GetString($b, 0x100, 4) -eq 'HEAD') { return 'xci' }
    return $null
}

function Early([string]$t) { if ($Recomp) { Problem $t } else { Warn $t } }

# Read-only check of what the user has to provide (prod.keys is only looked for, never read). With -Recomp a problem
# stops the run now; without it, it is a warning. Program ID, base version v0 and edition are checked by recomp (step 1).
function CheckInputs {
    $script:CurrentStep = 0
    $eds = @(Editions)
    if ($Recomp) {
        if (-not $EditionGiven) { Fail '-Recomp needs -Edition global|japan|both (it says how many dumps to expect)' }
        if ($InputFile -and $eds.Count -gt 1) { Fail '-InputFile is a single dump: use it with -Edition global or -Edition japan' }
    }
    $keysPath = if ($Keys) { [IO.Path]::GetFullPath($Keys) } else { PathOf $Root 'data/prod.keys' }
    if (Test-Path -LiteralPath $keysPath -PathType Leaf) { Info "prod.keys present: $keysPath (not read)" }
    else { Early "prod.keys not found: copy your keys to $keysPath" }
    $found = @()
    if ($InputFile) {
        $f = [IO.Path]::GetFullPath($InputFile)
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { Early "-InputFile $f does not exist" }
        elseif (-not (InputKind $f)) { Early "$f is neither an NSP nor an XCI (header check)" }
        else { $found += $f }
    } elseif (Test-Path -LiteralPath $Root) {
        foreach ($f in @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' })) {
            if (InputKind $f.FullName) { $found += $f.FullName } else { Warn "$($f.Name): neither an NSP nor an XCI (header check): ignored" }
        }
    }
    if ($found.Count) { Info ('dump(s): ' + (($found | ForEach-Object { Split-Path -Leaf $_ }) -join ', ')) }
    if ($found.Count -lt $eds.Count) {
        Early "$($eds.Count) dump(s) (.xci or .nsp) needed for -Edition $Edition in $Root (or -InputFile); found $($found.Count)"
    }
}

# recomp_tsukihime.ps1 in a process of its own: it sees the PATH this run refreshed, so no new console is needed.
function RunRecomp([string]$ed, [int]$from, [int]$to) {
    $m = PathOf $Root 'tools/recomp_tsukihime.ps1'
    if (-not (Test-Path -LiteralPath $m)) { Fail "$m is missing" }
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $m, '-Edition', $ed, '-From', "$from", '-To', "$to", '-Root', $Root)
    if ($InputFile) { $a += @('-InputFile', $InputFile) }
    if ($Keys) { $a += @('-Keys', $Keys) }
    $script:InRecomp = "recomp_tsukihime.ps1 -Edition $ed -From $from -To $to"
    Info $script:InRecomp
    $env:NX_VCPKG = $Vcpkg                  # dev_env.ps1 reads it (the recomp process must use the same vcpkg)
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & (Get-Process -Id $PID).Path @a | Out-Host; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }   # Out-Host: shown live, not returned to the caller
    if ($code -ne 0) { Fail "$script:InRecomp failed (code $code)" }
    $script:InRecomp = ''
}
function PhaseR1 { foreach ($ed in (Editions)) { RunRecomp $ed 0 8 } }
function PhaseR2 {
    foreach ($ed in (Editions)) {
        RunRecomp $ed 9 10
        $g = if ($ed -eq 'global') { 'tsukihime_global' } else { 'tsukihime' }
        $exe = PathOf $Framework "build/$g/$g.exe"
        if (Test-Path -LiteralPath $exe) { $script:RecompBuilt += $exe }
    }
}

function ResolveRoot {
"""

INST_LOOP_OLD = r"""        $script:CurrentStep = 0; Step 0 $StepNames[0]; Step0
        for ($n = [Math]::Max(1, $From); $n -le $To; $n++) {
            $script:CurrentStep = $n
            Step $n $StepNames[$n]
            & "Step$n"
        }
        if ($To -lt 11) { Write-Host ''; Write-Host "   (summary: step 11)" }
"""
INST_LOOP_NEW = r"""        $script:CurrentStep = 0
        TimedStep 0 $StepNames[0] 'Step0'
        CheckInputs
        $order = @(1..11)
        if ($Recomp -and $Check) { Info '-Recomp is not run with -Check (the inputs above were checked)' }
        elseif ($Recomp) { $order = @(1, 2, 3, 6, 7, 8, 'R1', 4, 5, 'R2', 9, 10, 11) }   # fail early: everything but the build before vcpkg
        foreach ($n in $order) {
            if ($n -is [string]) {
                if ($n -eq 'R1' -and $To -ge 8) { TimedStep 'R1' 'recomp steps 0-8: identify, extract, shaders' 'PhaseR1' }
                if ($n -eq 'R2' -and $To -ge 5) { TimedStep 'R2' 'recomp steps 9-10: build' 'PhaseR2' }
                continue
            }
            if ($n -lt $From -or $n -gt $To) { continue }
            $script:CurrentStep = $n
            TimedStep $n $StepNames[$n] "Step$n"
        }
        if ($To -lt 11) { Write-Host ''; Write-Host "   (summary: step 11)" }
        if ($script:RecompBuilt.Count) {
            Write-Host ''; Write-Host '   Built:' -ForegroundColor Green
            foreach ($x in $script:RecompBuilt) { Write-Host "     $x" }
        }
"""

INST_CATCH_OLD = r"""        Write-Host "ERROR in step $($script:CurrentStep): $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Resume with -From $($script:CurrentStep) once it is fixed (log: $log)." -ForegroundColor Red
"""
INST_CATCH_NEW = r"""        Write-Host "ERROR in $(if ($script:InRecomp) { 'the recompile' } else { "step $($script:CurrentStep)" }): $($_.Exception.Message)" -ForegroundColor Red
        if ($script:InRecomp) { Write-Host "Run the same command again once it is fixed: the steps already done are skipped (log: $log)." -ForegroundColor Red }
        else { Write-Host "Resume with -From $($script:CurrentStep) once it is fixed (log: $log)." -ForegroundColor Red }
"""

INST_EDS_OLD = """    $eds = Editions
    $needed = if ($eds.Count -gt 1) { 'one dump per edition' } else { 'the dump' }
"""
INST_EDS_NEW = """    $eds = @(Editions)   # AP_INSTALL_FLOW: with ONE edition Editions gives a string, and in Windows PowerShell 5.1 (StrictMode 2) a string has no .Count
    $needed = if ($eds.Count -gt 1) { 'one dump per edition' } else { 'the dump' }
"""

INST_FINALLY_OLD = r"""    } finally {
        if ($encPrev) { try { [Console]::OutputEncoding = $encPrev } catch { } }
"""
INST_FINALLY_NEW = r"""    } finally {
        ShowTimes
        if ($encPrev) { try { [Console]::OutputEncoding = $encPrev } catch { } }
"""

# ------------------------------------------------------------------------------------------ recomp_tsukihime.ps1
REC_ENV_OLD = r"""    $builds = ($From -le 9 -and $To -ge 9)
    if ($toolsMissing -or $builds) {
"""
REC_ENV_NEW = r"""    $builds = ($From -le 9 -and $To -ge 9)
    $shaders = ($From -le 8 -and $To -ge 8)   # AP_INSTALL_FLOW: step 8 needs VULKAN_SDK (spirv-val) and dotnet from the environment
    if ($toolsMissing -or $builds -or $shaders) {
"""

REC_MARK_OLD = "    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_VK_REQ', 'apply_vk_requirements.py', $false),\n"
REC_MARK_NEW = REC_MARK_OLD + (
    "    @('both', 'tools\\install_requirements.ps1', 'AP_INSTALL_FLOW', 'apply_install_flow.py', $false),\n"
)

REC_FUNCS_OLD = 'if ($From -gt $To) { throw "-From $From greater than -To $To" }\nWrite-Host "recomp_tsukihime: edition'
REC_FUNCS_NEW = r"""# AP_INSTALL_FLOW: step timing (table at the end, also on failure; appended to <root>\_prepare\step_times.log)
$script:Times = New-Object System.Collections.ArrayList
function FormatSpan([TimeSpan]$t) { '{0:00}:{1:00}:{2:00}.{3}' -f [int][Math]::Floor($t.TotalHours), $t.Minutes, $t.Seconds, [int][Math]::Floor($t.Milliseconds / 100) }
function TimedStep([int]$n, [string]$name, [string]$fn) {
    Step $n $name
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { & $fn }
    finally {
        $sw.Stop()
        [void]$script:Times.Add(@($n, $name, $sw.Elapsed))
        Info ('(took {0})' -f (FormatSpan $sw.Elapsed))
    }
}
function ShowTimes {
    if (-not $script:Times.Count) { return }
    Write-Host ''
    Write-Host '   Step times:' -ForegroundColor Green
    $total = [TimeSpan]::Zero
    foreach ($t in $script:Times) { $total += $t[2]; Write-Host ('     {0,-3} {1,-46} {2}' -f $t[0], $t[1], (FormatSpan $t[2])) }
    Write-Host ('     {0,-50} {1}' -f 'total', (FormatSpan $total))
    try {
        $dir = InRoot '_prepare'
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $lines = @(foreach ($t in $script:Times) { "$stamp`trecomp_tsukihime`t$Edition`t$($t[0])`t$($t[1])`t$(FormatSpan $t[2])" })
        [IO.File]::AppendAllText((Join-Path $dir 'step_times.log'), (($lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
    } catch { }
}

if ($From -gt $To) { throw "-From $From greater than -To $To" }
Write-Host "recomp_tsukihime: edition"""

REC_LOOP_OLD = r"""    Step 0 $StepNames[0]; Step0
    # Step 3 (verify exefs) is cheap and everything else depends on it: when resuming after it, it runs first.
    if ($From -gt 3) { $script:CurrentStep = 3; Step 3 "$($StepNames[3]) (always)"; Step3 }
    $start = if ($From -eq 2) { 1 } else { [Math]::Max(1, $From) }   # step 2 needs what step 1 identifies
    for ($n = $start; $n -le $To; $n++) {
        $script:CurrentStep = $n
        Step $n $StepNames[$n]
        & "Step$n"
    }
    if ($Compare) { $script:CurrentStep = 11; CompareInventory $Compare }
} catch {
"""
REC_LOOP_NEW = r"""    TimedStep 0 $StepNames[0] 'Step0'
    # Step 3 (verify exefs) is cheap and everything else depends on it: when resuming after it, it runs first.
    if ($From -gt 3) { $script:CurrentStep = 3; TimedStep 3 "$($StepNames[3]) (always)" 'Step3' }
    $start = if ($From -eq 2) { 1 } else { [Math]::Max(1, $From) }   # step 2 needs what step 1 identifies
    for ($n = $start; $n -le $To; $n++) {
        $script:CurrentStep = $n
        TimedStep $n $StepNames[$n] "Step$n"
    }
    if ($Compare) { $script:CurrentStep = 11; CompareInventory $Compare }
    ShowTimes
} catch {
"""

REC_CATCH_OLD = r"""    Write-Host "What the step managed to do was undone. Resume with -From $($script:CurrentStep) once it is fixed." -ForegroundColor Red
    exit 1
"""
REC_CATCH_NEW = r"""    Write-Host "What the step managed to do was undone. Resume with -From $($script:CurrentStep) once it is fixed." -ForegroundColor Red
    ShowTimes
    exit 1
"""

EDITS = [
    (INST, INST_USAGE_OLD, INST_USAGE_NEW),
    (INST, INST_PARAM_OLD, INST_PARAM_NEW),
    (INST, INST_BOUND_OLD, INST_BOUND_NEW),
    (INST, INST_STEP3_OLD, INST_STEP3_NEW),
    (INST, INST_FUNCS_OLD, INST_FUNCS_NEW),
    (INST, INST_LOOP_OLD, INST_LOOP_NEW),
    (INST, INST_CATCH_OLD, INST_CATCH_NEW),
    (INST, INST_FINALLY_OLD, INST_FINALLY_NEW),
    (INST, INST_EDS_OLD, INST_EDS_NEW),
    (RECO, REC_ENV_OLD, REC_ENV_NEW),
    (RECO, REC_MARK_OLD, REC_MARK_NEW),
    (RECO, REC_FUNCS_OLD, REC_FUNCS_NEW),
    (RECO, REC_LOOP_OLD, REC_LOOP_NEW),
    (RECO, REC_CATCH_OLD, REC_CATCH_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)