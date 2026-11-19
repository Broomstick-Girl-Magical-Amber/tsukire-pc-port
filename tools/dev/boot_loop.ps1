# boot_loop.ps1 - Boots the game several times in a row and checks each nx.log for a crash, to test an intermittent
# crash at boot (the audio limiter one: DEBUG.md #28, DEBUG-2 #74, apply_audio_pace.py). Windows.
#
#   .\boot_loop.ps1                              # 20 boots of 25 s each (japan)
#   .\boot_loop.ps1 -Runs 30 -Seconds 20 -Edition global
#   .\boot_loop.ps1 -Set 'NX_AUDIO_PACE=all'     # the same with an NX_ variable set for these runs only
#
# Each run: starts the exe (as the launcher does: from its folder), waits -Seconds or until it exits, closes it if it is
# still open, and keeps a copy of nx.log in <data>\boot_loop\run_NN.log. Leave the window alone while it runs.
# Prints per run: crash or not, how long it lived, the audio pacing lines; and a summary at the end.
param(
    [int]$Runs = 20,
    [int]$Seconds = 25,
    [ValidateSet('japan', 'global')][string]$Edition = 'japan',
    [string[]]$Set = @(),
    [string]$Root = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # <root>\tools\dev\ -> <root>
$data = Join-Path $Root $(if ($Edition -eq 'global') { 'data\tsuki_global' } else { 'data\tsuki' })
$game = if ($Edition -eq 'global') { 'tsukihime_global' } else { 'tsukihime' }
$exe = Join-Path $Root "framework\build\$game\$game.exe"
$log = Join-Path $data 'nx.log'
$out = Join-Path $data 'boot_loop'
if (-not (Test-Path -LiteralPath $exe)) { throw "$exe does not exist" }
if (Get-Process -Name $game -ErrorAction SilentlyContinue) { throw "$game.exe is already running: close it first" }
New-Item -ItemType Directory -Force -Path $out | Out-Null

$saved = @{}
foreach ($e in $Set) {
    $k, $v = $e -split '=', 2
    if (-not $k -or $null -eq $v) { throw "-Set takes NAME=value, not '$e'" }
    $saved[$k] = [Environment]::GetEnvironmentVariable($k)
    [Environment]::SetEnvironmentVariable($k, $v)
}

$crashes = @()
try {
    for ($i = 1; $i -le $Runs; $i++) {
        if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue }
        $t0 = Get-Date
        $p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -PassThru
        $exited = $p.WaitForExit($Seconds * 1000)
        $lived = ((Get-Date) - $t0).TotalSeconds
        if (-not $exited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            $p.WaitForExit(10000) | Out-Null
        }
        $copy = Join-Path $out ('run_{0:D2}.log' -f $i)
        for ($try = 0; $try -lt 5; $try++) {
            try { Copy-Item -LiteralPath $log -Destination $copy -Force; break } catch { Start-Sleep -Milliseconds 500 }
        }
        $lines = if (Test-Path -LiteralPath $copy) { @(Get-Content -LiteralPath $copy) } else { @() }
        $crash = @($lines | Where-Object { $_ -match 'CRASH' })
        $giveUp = @($lines | Where-Object { $_ -match 'audio: no render for 1 s' }).Count
        $pace = @($lines | Where-Object { $_ -match 'audio: pacing:' } | Select-Object -First 1)
        $state = if ($crash.Count) { 'CRASH' } elseif ($exited) { 'exited' } else { 'ok' }
        Write-Host ('run {0,2}/{1}: {2,-6} {3,5:N1} s, {4} lines{5}' -f $i, $Runs, $state, $lived, $lines.Count,
                    $(if ($giveUp) { ", 'no render for 1 s' x$giveUp" } else { '' }))
        if ($pace) { Write-Host "         $($pace[0])" }
        if ($crash.Count) {
            $crashes += $i
            $crash | Select-Object -First 3 | ForEach-Object { Write-Host "         $_" -ForegroundColor Red }
        }
        Start-Sleep -Seconds 2
    }
} finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
}
Write-Host ''
if ($crashes.Count) {
    Write-Host "$($crashes.Count) of $Runs runs crashed: $($crashes -join ', ') (logs in $out)" -ForegroundColor Red
} else {
    Write-Host "$Runs runs, no crash (logs in $out)" -ForegroundColor Green
}