<#
.SYNOPSIS
  MAINTAINER: pins the Android pack and the APK of a consolidated GitHub release in tools\internal\android_pack.json.
.DESCRIPTION
  One release holds everything: tsukihime_launcher.exe, tsukire-pc-port.zip (+ .sha256), the Android pack and the APK.
  This reads the pack zip (_android\tsukire-android-pack-v<N>.zip, made by tools\dev\build_android_pack.ps1) and the
  app (_android\tsukihime.apk, made by tools\android\build_apk.ps1), computes their SHA-256 and writes version, url and
  sha256 (apk_version, apk_url, apk_sha256 for the app) with the download URLs of <Tag>. Nothing is uploaded.

  Order of a consolidated release (the port zip carries android_pack.json, so this goes BEFORE it is made):
    1. tools\android\build_apk.ps1 -Edition global -Version <app version>     (and japan if it is published)
    2. tools\dev\build_android_pack.ps1 -Version <N>
    3. tools\dev\pin_android_release.ps1 -Tag <tag> -ApkVersion <app version>
    4. tools\dev\build_launcher.ps1 -Tag <tag>
    5. tools\dev\pack_test_zip.ps1 -Release
    6. gh release create <tag> (the files this script prints)
  Rebuilding the pack or the APK afterwards changes its hash: run this again, then steps 5 and 6.
.PARAMETER Tag         Tag of the release that will hold the files (letters, digits, . _ -).
.PARAMETER ApkVersion  versionName of the APK (build_apk.ps1 -Version); android_port.ps1 -InstallApp compares with it.
.PARAMETER PackVersion Pack version (the N of tsukire-android-pack-vN.zip). Default: the newest zip in _android.
.PARAMETER Repo        Release repository URL (default: the one of launcher\src\fetch.cpp).
.PARAMETER Root        Port folder (default: two levels above this script).
#>
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$ApkVersion,
    [string]$PackVersion = '',
    [string]$Repo = '',
    [string]$Root = ''
)
$ErrorActionPreference = 'Stop'
# Not a parameter default: Windows PowerShell 5.1 leaves $PSScriptRoot empty there when other parameters are mandatory.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Root) { $Root = Join-Path $here '..\..' }
$Root = (Resolve-Path -LiteralPath $Root).Path
if ($Tag -notmatch '^[A-Za-z0-9._-]+$') { throw "-Tag '$Tag': only letters, digits, '.', '_' and '-'." }
if ($ApkVersion -notmatch '^[A-Za-z0-9._-]+$') { throw "-ApkVersion '$ApkVersion': only letters, digits, '.', '_' and '-'." }
if (-not $Repo) {
    $src = Join-Path $Root 'launcher\src\fetch.cpp'
    if (Test-Path -LiteralPath $src) {
        $m = [regex]::Match([IO.File]::ReadAllText($src), 'kRepo\s*=\s*L"([^"]+)"')
        if ($m.Success) { $Repo = $m.Groups[1].Value }
    }
}
if (-not $Repo) { throw 'Repository URL unknown: pass -Repo https://github.com/<owner>/<repo>.' }
$Repo = $Repo.TrimEnd('/')

$dir = Join-Path $Root '_android'
if ($PackVersion) {
    $pack = Join-Path $dir "tsukire-android-pack-v$PackVersion.zip"
    if (-not (Test-Path -LiteralPath $pack)) { throw "${pack} not found: run tools\dev\build_android_pack.ps1 -Version $PackVersion." }
} else {
    $zips = @(Get-ChildItem -LiteralPath $dir -Filter 'tsukire-android-pack-v*.zip' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending)
    if (-not $zips.Count) { throw "no tsukire-android-pack-v*.zip in ${dir}: run tools\dev\build_android_pack.ps1 -Version <N>." }
    $pack = $zips[0].FullName
    $PackVersion = [regex]::Match($zips[0].Name, '^tsukire-android-pack-v(.+)\.zip$').Groups[1].Value
    if ($zips.Count -gt 1) { Write-Host "   $($zips.Count) pack zips in $dir; using the newest: $($zips[0].Name)" }
}
$apk = Join-Path $dir 'tsukihime.apk'
if (-not (Test-Path -LiteralPath $apk)) { throw "${apk} not found: run tools\android\build_apk.ps1 -Version $ApkVersion." }

$packName = Split-Path -Leaf $pack
$packSha = (Get-FileHash -LiteralPath $pack -Algorithm SHA256).Hash.ToLower()
$apkSha = (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLower()
$base = "$Repo/releases/download/$Tag"

$pinFile = Join-Path $Root 'tools\internal\android_pack.json'
$comment = ''
if (Test-Path -LiteralPath $pinFile) {
    $old = Get-Content -LiteralPath $pinFile -Raw | ConvertFrom-Json
    if ($old.PSObject.Properties['comment']) { $comment = "$($old.comment)" }
}
if (-not $comment) { $comment = 'What tools/android downloads from the GitHub release when it is not on this PC (the Android pack and the app).' }
$esc = { param($s) ($s -replace '\\', '\\' -replace '"', '\"') }
$lines = @(
    '{',
    ('  "comment": "{0}",' -f (& $esc $comment)),
    ('  "version": "{0}",' -f (& $esc $PackVersion)),
    ('  "url": "{0}/{1}",' -f $base, $packName),
    ('  "sha256": "{0}",' -f $packSha),
    ('  "apk_version": "{0}",' -f (& $esc $ApkVersion)),
    ('  "apk_url": "{0}/tsukihime.apk",' -f $base),
    ('  "apk_sha256": "{0}"' -f $apkSha),
    '}'
)
[IO.File]::WriteAllText($pinFile, (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))

Write-Host ''
Write-Host "tools\internal\android_pack.json pinned to release '$Tag':"
Write-Host ("   pack  v{0}  {1:n1} MB  {2}" -f $PackVersion, ((Get-Item -LiteralPath $pack).Length / 1MB), $packSha)
Write-Host ("   app   v{0}  {1:n1} MB  {2}" -f $ApkVersion, ((Get-Item -LiteralPath $apk).Length / 1MB), $apkSha)
Write-Host ''
Write-Host 'Next: tools\dev\build_launcher.ps1 -Tag' $Tag ' then  tools\dev\pack_test_zip.ps1 -Release'
Write-Host 'Files of the release (names exactly as they are):'
Write-Host '   tsukihime_launcher.exe'
Write-Host '   _prepare\release\tsukire-pc-port.zip'
Write-Host '   _prepare\release\tsukire-pc-port.zip.sha256'
Write-Host ("   _android\{0}" -f $packName)
Write-Host '   _android\tsukihime.apk'
