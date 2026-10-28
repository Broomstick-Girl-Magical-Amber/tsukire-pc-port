# skip_fs.ps1 - Starts the game without the draws of a range of fragment shaders (NX_SKIP_FS), to find which one draws
# something wrong by halves. The shaders are the fragment rows of <data>\spirv\results.tsv, numbered 0.. in file order.
#
#   .\skip_fs.ps1 -List                       # numbered list of the fragment shaders
#   .\skip_fs.ps1 -From 0 -To 32              # play without shaders 0..32, then close the game
#   .\skip_fs.ps1 -From 0 -To 32 -Edition global
#
# Only sets NX_SKIP_FS for that run (it is removed afterwards). Nothing is written.
param(
    [int]$From = -1,
    [int]$To = -1,
    [ValidateSet('japan', 'global')][string]$Edition = 'japan',
    [switch]$List,
    [string]$Root = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # <root>\tools\dev\ -> <root>
$data = if ($Edition -eq 'global') { 'data\tsuki_global' } else { 'data\tsuki' }
$game = if ($Edition -eq 'global') { 'tsukihime_global' } else { 'tsukihime' }
$tsv = Join-Path $Root "$data\spirv\results.tsv"
$exe = Join-Path $Root "framework\build\$game\$game.exe"
if (-not (Test-Path -LiteralPath $tsv)) { throw "$tsv does not exist" }

$frag = @(Get-Content -LiteralPath $tsv | Select-Object -Skip 1 | Where-Object { $_ } |
          ForEach-Object { $f = $_ -split "`t"; if ($f[2] -eq 'fragment') { [pscustomobject]@{ Hash = $f[0]; Group = $f[1] } } })
if ($List -or $From -lt 0) {
    for ($i = 0; $i -lt $frag.Count; $i++) { '{0,3}  {1}  {2}' -f $i, $frag[$i].Hash, $frag[$i].Group }
    "$($frag.Count) fragment shaders"
    return
}
if ($To -lt $From -or $To -ge $frag.Count) { throw "range $From..$To outside 0..$($frag.Count - 1)" }
if (-not (Test-Path -LiteralPath $exe)) { throw "$exe does not exist" }

$skip = @($frag[$From..$To] | ForEach-Object { $_.Hash })
Write-Host "skipping $($skip.Count) fragment shaders ($From..$To of 0..$($frag.Count - 1)): $($skip -join ' ')"
$prev = $env:NX_SKIP_FS
$env:NX_SKIP_FS = $skip -join ','
try {
    Push-Location (Split-Path -Parent $exe)
    try { Start-Process -FilePath $exe -Wait } finally { Pop-Location }
} finally {
    if ($null -eq $prev) { Remove-Item Env:NX_SKIP_FS -ErrorAction SilentlyContinue } else { $env:NX_SKIP_FS = $prev }
}
Write-Host "closed. Stripes still there? Tell Claude the range ($From..$To) and yes/no."
