# regen_tsuki.ps1 - Regenerates the recompiled code of Tsukihime (python -m recomp) with the game options.
#
#   .\regen_tsuki.ps1               # main
#   .\regen_tsuki.ps1 main, sdk     # several modules
#
# Recompiler options specific to Tsukihime (environment variables, only during this run):
#   NX_RECOMP_JT_LSR=1  jump tables with an "lsr + cmp" check (apply_jt_lsr.py; crash main+9eba4)
# After: cmake --build build\tsukihime
# Paths relative to the script: <root>\tools\internal\regen_tsuki.ps1 -> the root is two folders up (or -Root <folder>).
param([string[]]$Modules = @('main'), [string]$Root = '')

$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # <root>\tools\internal\ -> <root>
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
$Framework = Join-Path $Root 'framework'
$ExeFS = Join-Path $Root 'data\tsuki\exefs'
$Gen = Join-Path $Root 'data\tsuki\gen'
$Bases = @{ main = '0x4000000000'; sdk = '0x4010000000' }
$OnLinux = [bool]((Test-Path variable:IsLinux) -and $IsLinux)   # PowerShell 7 on Linux (in 5.1 the variable does not exist)
$Python = if ($OnLinux) { Join-Path $Framework 'private/venv/bin/python' } else { Join-Path $Framework 'private\venv\Scripts\python.exe' }

# The build has to read from the same folder that is regenerated.
$cache = Join-Path $Framework 'build\tsukihime\CMakeCache.txt'
if (Test-Path $cache) {
    $line = Select-String -Path $cache -Pattern '^NX_GEN_DIR:' | Select-Object -First 1
    if ($line -and ($line.Line -replace '\\', '/') -notmatch [regex]::Escape(($Gen -replace '\\', '/'))) {
        throw "build\tsukihime uses another folder of generated code: $($line.Line)"
    }
}
if (-not (Select-String -Path (Join-Path $Framework 'recomp\analysis.py') -Pattern '_JT_LSR = ' -Quiet)) {
    throw 'recomp\analysis.py without apply_jt_lsr.py version 2: apply it first'
}

$prev = $env:NX_RECOMP_JT_LSR
$env:NX_RECOMP_JT_LSR = '1'
Push-Location $Framework
try {
    foreach ($m in $Modules) {
        if (-not $Bases.ContainsKey($m)) { throw "unknown module: $m (known: $($Bases.Keys -join ', '))" }
        Write-Host "== recomp $m (base $($Bases[$m]), NX_RECOMP_JT_LSR=1)"
        & $Python -m recomp (Join-Path $ExeFS $m) (Join-Path $Gen $m) --name $m --base $Bases[$m]
        if ($LASTEXITCODE -ne 0) { throw "recomp $m failed ($LASTEXITCODE)" }
    }
} finally {
    Pop-Location
    if ($null -eq $prev) { Remove-Item Env:NX_RECOMP_JT_LSR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_LSR = $prev }
}
Write-Host 'Done. Now: cmake --build build\tsukihime'