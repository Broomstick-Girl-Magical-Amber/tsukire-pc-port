<#
.SYNOPSIS
  Packs the port + the compiled launcher into one zip to try it on a clean machine.
.DESCRIPTION
  Zips what git would publish (tracked files + new files that .gitignore allows, so uncommitted changes and patched
  scripts are included) plus tsukihime_launcher.exe from the root. Never includes the game: no dump (.xci/.nsp),
  no prod.keys, no data\, no build output. On the clean machine: unzip, put your .xci/.nsp and prod.keys next to
  the launcher, run tsukihime_launcher.exe.
.PARAMETER Root     Port folder (default: two levels above this script).
.PARAMETER Out      Zip to create (default: _prepare\tsuki-clean-test.zip; with -Release: _prepare\release\tsukire-pc-port.zip).
.PARAMETER Release  Makes the release asset: the same zip (port + tsukihime_launcher.exe) named tsukire-pc-port.zip, plus
                    tsukire-pc-port.zip.sha256 (sha256sum format). Upload both to the GitHub release (and the exe on its
                    own too): when the launcher runs alone in a folder it downloads these two, checks the hash and
                    unpacks the zip; tar -k leaves the running exe untouched.
                    Build the exe with the same tag: tools\dev\build_launcher.ps1 -Tag <the release tag>.
                    Better from a clean `git status` (uncommitted changes would be inside the zip).
#>
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\..'),
    [string]$Out,
    [switch]$Release
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not $Out) { $Out = Join-Path $Root $(if ($Release) { '_prepare\release\tsukire-pc-port.zip' } else { '_prepare\tsuki-clean-test.zip' }) }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is required (it decides which files are publishable).' }
$exe = Join-Path $Root 'tsukihime_launcher.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'tsukihime_launcher.exe not found in the root: run tools\dev\build_launcher.ps1 first.' }

Push-Location $Root
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $raw = (& git -c core.quotepath=off ls-files -z --cached --others --exclude-standard) -join ''
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed (is this a git checkout?).' }
    $files = @($raw.Split([char]0) | Where-Object { $_ } | Sort-Object -Unique)
    if ($Release -and @(& git status --porcelain).Count -gt 0) { Write-Warning 'git status is not clean: the uncommitted changes go into this release zip.' }
} finally { Pop-Location }
# Tracked files that were deleted in the working tree do not travel.
$files = @($files | Where-Object { Test-Path -LiteralPath (Join-Path $Root ($_ -replace '/', '\')) -PathType Leaf })

# Safety net: the game and the keys must never travel in this zip.
$banned = '(^|/)(prod|title)\.keys$|\.(xci|nsp|nsz|xcz)$|^(data|dumps|_prepare)/'
# data/_put_your_keys_here is the empty placeholder the repo tracks; it is harmless and travels.
$bad = @($files | Where-Object { $_ -match $banned -and $_ -ne 'data/_put_your_keys_here' })
if ($bad.Count -gt 0) { throw ("Refusing to pack (game data or keys would be included): " + ($bad[0..([Math]::Min(4, $bad.Count - 1))] -join ', ')) }

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null
if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
$zip = [System.IO.Compression.ZipFile]::Open($Out, 'Create')
$n = 0
try {
    foreach ($f in $files + @('tsukihime_launcher.exe')) {
        $p = Join-Path $Root ($f -replace '/', '\')
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $p, $f, [System.IO.Compression.CompressionLevel]::Optimal)
        $n++
    }
} finally { $zip.Dispose() }
'{0} files, {1:N1} MB -> {2}' -f $n, ((Get-Item -LiteralPath $Out).Length / 1MB), $Out
if ($Release) {
    $hash = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash.ToLower()
    $shaFile = $Out + '.sha256'
    [System.IO.File]::WriteAllText($shaFile, ($hash + '  ' + (Split-Path -Leaf $Out) + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    "sha256 $hash -> $shaFile"
    'Upload both files to the release, plus tsukihime_launcher.exe on its own (built with build_launcher.ps1 -Tag <tag>).'
} else {
    'Clean machine: unzip, put your .xci/.nsp and prod.keys next to tsukihime_launcher.exe and run it.'
}