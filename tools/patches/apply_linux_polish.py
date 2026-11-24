"""apply_linux_polish.py - small fixes of the install scripts found on native Linux (AP_LINUX_POLISH).

1. Download of the Ryubing tarball (step 8): up to 3 retries with a 60 s timeout (a Codeberg 504 used to wait 10 minutes and fail).
   Download gets two optional parameters; every other caller keeps the old behaviour.
2. Summary of step 11 on Linux: the tools are no longer listed twice, "GPU to play: NO" says that it is the CPU driver (llvmpipe)
   or none, and after -Recomp "Next" shows how to play instead of the recompile commands that were just run.
3. create_tsukihime_global.ps1: "Warning: no ...CMakeCache.txt" is now a note (it is the normal state of a first build).

Touches ../tools/install_requirements.ps1, ../tools/linux/install_requirements_linux.ps1 and ../tools/internal/create_tsukihime_global.ps1.
The Linux summary is a second definition of Step11 placed after the first one, so no line of another patcher is edited.
Idempotent (marker AP_LINUX_POLISH), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as they were.
Apply: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_linux_polish.py
"""
from _patch import run

MARK = "AP_LINUX_POLISH"
INSTALL = "../tools/install_requirements.ps1"
LINUX = "../tools/linux/install_requirements_linux.ps1"
GLOBAL = "../tools/internal/create_tsukihime_global.ps1"

STEP11_TAIL = '''    if ($script:Problems.Count) {
        Write-Host ''
        Write-Host '   Problems:' -ForegroundColor Yellow
        foreach ($p in $script:Problems) { Write-Host "     $p" }
    }
}
'''

STEP11_NEW = '''
# AP_LINUX_POLISH (apply_linux_polish.py): the summary of step 11 again (this definition wins over the one above): the tools are
# not listed twice, the GPU line says what NO means, and after -Recomp "Next" says how to play.
function Step11 {
    $nl = ((Test-Path variable:NativeLinux) -and $NativeLinux)
    Write-Host ''
    Write-Host '   Status:' -ForegroundColor Green
    $toolNames = @()
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'MISSING' } default { "$($e.Det.Text) (does not meet $($ToolSpecs[$c].Rule))" } }
        Write-Host ('     {0,-36} {1}' -f $ToolSpecs[$c].Name, $t)
        $toolNames += $ToolSpecs[$c].Name
    }
    if (-not $nl) { Write-Host ('     {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { 'OK' } else { 'MISSING' })) }
    foreach ($l in $script:Done) {
        $dup = $false
        foreach ($n in $toolNames) { if ($l.StartsWith($n)) { $dup = $true; break } }
        if (-not $dup) { Write-Host "     $l" }
    }
    if ($null -ne $script:GpuOk) {
        $g = if ($script:GpuOk) { 'OK' } elseif ($nl) { 'NO, only a CPU driver (llvmpipe) or none: it builds, but it plays slowly or not at all' } else { 'NO (see the warning of step 9)' }
        Write-Host ('     GPU to play{0}: {1}' -f $(if ($nl) { '' } else { ' (Proton)' }), $g)
    }
    Write-Host ''
    Write-Host '   By hand (the script does not touch it):' -ForegroundColor Yellow
    $keys = Join-Path $Root 'data/prod.keys'
    Write-Host ("     prod.keys   {0}" -f $(if (Test-Path -LiteralPath $keys) { "present in $keys (not read)" } else { "copy your keys to $keys" }))
    $dumps = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } | ForEach-Object { $_.Name })
    Write-Host ("     dumps       {0}" -f $(if ($dumps.Count) { "in ${Root}: " + ($dumps -join ', ') } else { "copy the .xci or .nsp of each edition to $Root (the root, not data/)" }))
    Write-Host ''
    Write-Host '   Next:' -ForegroundColor Cyan
    if ($Recomp -and -not $script:Problems.Count) {
        Write-Host '     bash tools/linux/launcher.sh gui      (the graphical launcher)'
        Write-Host '     bash tools/linux/launcher.sh play     (or play from the terminal)'
    } else {
        foreach ($ed in (Editions)) { Write-Host "     bash tools/linux/recomp_linux.sh -Edition $ed$(if ($nl) { ' -Target linux' })" }
    }
    if ($script:Problems.Count) {
        Write-Host ''
        Write-Host '   Problems:' -ForegroundColor Yellow
        foreach ($p in $script:Problems) { Write-Host "     $p" }
    }
}
'''

EDITS = [
    (INSTALL,
     '''function Download([string]$url, [string]$dst) {
''',
     '''function Download([string]$url, [string]$dst, [int]$Retries = 0, [int]$TimeoutSec = 0) {   # AP_LINUX_POLISH: optional retries and timeout
'''),
    (INSTALL,
     '''    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
    Move-Item -LiteralPath $tmp -Destination $dst -Force
''',
     '''    for ($attempt = 0; ; $attempt++) {
        try {
            if ($TimeoutSec -gt 0) { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp -TimeoutSec $TimeoutSec }
            else { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp }
            break
        } catch {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
            if ($attempt -ge $Retries) { throw }
            Warn "download failed: $($_.Exception.Message). Attempt $($attempt + 2) of $($Retries + 1) in 5 s"
            Start-Sleep -Seconds 5
        }
    }
    Move-Item -LiteralPath $tmp -Destination $dst -Force
'''),
    (INSTALL,
     '''{ Download $RyubingUrl $tgz }''',
     '''{ Download $RyubingUrl $tgz -Retries 3 -TimeoutSec 60 }   # AP_LINUX_POLISH'''),
    (LINUX, STEP11_TAIL, STEP11_TAIL + STEP11_NEW),
    (GLOBAL,
     '''} else { Write-Host "Warning: no $cache; the command below has no generator or toolchain" }''',
     '''} else { Write-Host "Note: no $cache yet (normal in a first build): step 9 of recomp_tsukihime.ps1 configures the build; the command below is only a reference" }   # AP_LINUX_POLISH'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
