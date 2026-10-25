# regen_tsuki_global.ps1 - Regenerates the recompiled code of Tsukihime global edition (python -m recomp) with the game options.
#
#   .\regen_tsuki_global.ps1        # main
#   .\regen_tsuki_global.ps1 main, sdk  # several modules
#
# Recompiler options specific to Tsukihime (environment variables, only during this run):
#   NX_RECOMP_JT_LSR=1  jump tables with an "lsr + cmp" check (apply_jt_lsr.py; crash main+9eba4)
#   NX_RECOMP_JT_MOV=1  jump tables with the bound in a register (apply_jt_mov.py; crash main+9708c)
# After: cmake --build build\tsukihime_global
# Paths relative to the script: <root>\tools\internal\regen_tsuki_global.ps1 -> the root is two folders up (or -Root <folder>).
param([string[]]$Modules = @('main'), [string]$Root = '')

$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # <root>\tools\internal\ -> <root>
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
$Framework = Join-Path $Root 'framework'
$ExeFS = Join-Path $Root 'data\tsuki_global\exefs'
$Gen = Join-Path $Root 'data\tsuki_global\gen'
$Bases = @{ main = '0x4000000000'; sdk = '0x4010000000' }
$OnLinux = [bool]((Test-Path variable:IsLinux) -and $IsLinux)   # PowerShell 7 on Linux (in 5.1 the variable does not exist)
$Python = if ($OnLinux) { Join-Path $Framework 'private/venv/bin/python' } else { Join-Path $Framework 'private\venv\Scripts\python.exe' }

# The build has to read from the same folder that is regenerated.
$cache = Join-Path $Framework 'build\tsukihime_global\CMakeCache.txt'
if (Test-Path $cache) {
    $line = Select-String -Path $cache -Pattern '^NX_GEN_DIR:' | Select-Object -First 1
    if ($line -and ($line.Line -replace '\\', '/') -notmatch [regex]::Escape(($Gen -replace '\\', '/'))) {
        throw "build\tsukihime_global uses another folder of generated code: $($line.Line)"
    }
}
if (-not (Select-String -Path (Join-Path $Framework 'recomp\analysis.py') -Pattern '_JT_LSR = ' -Quiet)) {
    throw 'recomp\analysis.py without apply_jt_lsr.py version 2: apply it first'
}
# AP_JT_FUERA: jump table cases outside the function (sdk+0x5c5164 of SDK 17.5.4)
if (-not (Select-String -Path (Join-Path $Framework 'recomp\emit.py') -Pattern '_JT_FUERA = ' -Quiet)) {
    throw 'recomp\emit.py without apply_jt_outside.py: apply it first'
}

# AP_RELR: RELR relocations of SDK 17.5.4
if (-not (Select-String -Path (Join-Path $Framework 'recomp\nso.py') -Pattern '_RELR = ' -Quiet)) {
    throw 'recomp\nso.py without apply_relr.py: apply it first'
}
# AP_JT_MOV: jump tables with the bound in a register (main+97000 of the global)
if (-not (Select-String -Path (Join-Path $Framework 'recomp\analysis.py') -Pattern '_JT_MOV = ' -Quiet)) {
    throw 'recomp\analysis.py without apply_jt_mov.py: apply it first'
}
$prev = $env:NX_RECOMP_JT_LSR
$env:NX_RECOMP_JT_LSR = '1'
$prevFuera = $env:NX_RECOMP_JT_FUERA  # AP_JT_FUERA
$env:NX_RECOMP_JT_FUERA = '1'
$prevRelr = $env:NX_RECOMP_RELR  # AP_RELR
$env:NX_RECOMP_RELR = '1'
$prevMov = $env:NX_RECOMP_JT_MOV  # AP_JT_MOV
$env:NX_RECOMP_JT_MOV = '1'
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
    if ($null -eq $prevRelr) { Remove-Item Env:NX_RECOMP_RELR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_RELR = $prevRelr }  # AP_RELR
    if ($null -eq $prev) { Remove-Item Env:NX_RECOMP_JT_LSR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_LSR = $prev }
    if ($null -eq $prevFuera) { Remove-Item Env:NX_RECOMP_JT_FUERA -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_FUERA = $prevFuera }  # AP_JT_FUERA
    if ($null -eq $prevMov) { Remove-Item Env:NX_RECOMP_JT_MOV -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_MOV = $prevMov }  # AP_JT_MOV
}
Write-Host 'Done. Now: cmake --build build\tsukihime_global'