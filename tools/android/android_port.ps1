# android_port.ps1 - Everything the phone needs, in one command (phase 2.3 of the Android port,
# claude/tsukihime-android-fase2.md): after the game is recompiled on this PC (tools\recomp_tsukihime.ps1, or the
# launcher's "Recompile"), it builds the game's library for Android (build_game.ps1: the Android pack + the LLVM clang
# of the PC install; no Android Studio, no NDK), puts the folder for the phone together (phone_data.ps1) and, if asked,
# sends it to the phone by USB with adb and installs or updates the app.
#
#   .\tools\android\android_port.ps1 -Edition global                       # the folder: _android\global\phone
#   .\tools\android\android_port.ps1 -Edition global -InternalStorage      # and send it to the phone (adb, USB debugging)
#   .\tools\android\android_port.ps1 -Edition global -InternalStorage -InstallApp -GetAdb -Mods
#   Linux: bash tools/android/android_port.sh -Edition global
#
# -InternalStorage / -ExternalStorage [-Volume <id>] [-PhoneFolder <name>]: where on the phone (default folder
# Tsukihime). Without them nothing is sent: copy the CONTENTS of _android\<edition>\phone to the phone by hand.
# -Mods: the active mod layers too (on the phone, layers no longer active here are removed). -Saves: your saves of this
# PC (they replace the phone's!). -Dump: the .xci/.nsp too (~23 GB).
# -InstallApp: installs the app (tools\internal\android_pack.json "apk_url", or _android\tsukihime.apk if it was built
# here) when the phone does not have it or has another version. -UpdateApp: installs it again in any case.
# -GetAdb: if this PC has no adb, downloads it (Android SDK Platform-Tools of Google, ~7 MB, into the tools folder).
# -Pack <zip or folder>: the Android pack to use (otherwise _prepare\android-pack, or the published one).
# Every line it prints is also what the launcher shows: "== android: ..." starts a part, "adb: N% ..." is the progress.
param(
    [Parameter(Mandatory = $true)][ValidateSet('japan', 'global')][string]$Edition,
    [switch]$InternalStorage,
    [switch]$ExternalStorage,
    [string]$Volume,
    [string]$PhoneFolder = 'Tsukihime',
    [switch]$Mods,
    [switch]$Saves,
    [switch]$Dump,
    [switch]$InstallApp,
    [switch]$UpdateApp,
    [switch]$GetAdb,
    [string]$Adb,
    [string]$Pack,
    [int]$Jobs = 0,
    [string]$Root
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'android_common.ps1')

function Part([string]$t) { Write-Host ''; Write-Host "== android: $t" -ForegroundColor Cyan }

# Runs one of the other Android scripts in this same PowerShell; fails if it failed (they end with exit 1).
function RunScript([string]$name, [hashtable]$params) {
    $global:LASTEXITCODE = 0
    & (Join-Path $PSScriptRoot $name) @params
    if ($LASTEXITCODE) { Fail "$name failed (see above)" }
}

# The app on the phone: @(installed, versionName, SHA-256 of its base.apk ('' if it cannot be read)).
function InstalledApp([string]$adb) {
    $p = AdbText $adb @('shell', 'pm', 'path', $AndroidPackage)
    $m = [regex]::Match($p, 'package:(\S+base\.apk)')
    if (-not $m.Success) { return , @($false, '', '') }
    $d = AdbText $adb @('shell', 'dumpsys', 'package', $AndroidPackage)
    $v = [regex]::Match($d, 'versionName=(\S+)')
    $h = [regex]::Match((AdbText $adb @('shell', "sha256sum $(ShQ $m.Groups[1].Value)")), '^([0-9a-fA-F]{64})')
    return , @($true, $(if ($v.Success) { $v.Groups[1].Value } else { '?' }), $(if ($h.Success) { $h.Groups[1].Value.ToLower() } else { '' }))
}

# The APK to install: the published one (android_pack.json, checked against its SHA-256) or the one built here.
# Returns @(path, version) - version '' when unknown (built here) - or $null (none: a warning).
function FindApk {
    $local = Under $Root '_android\tsukihime.apk'
    $pinFile = Under $Root 'tools\internal\android_pack.json'
    $pin = $null
    if (Test-Path -LiteralPath $pinFile) { $pin = Get-Content -LiteralPath $pinFile -Raw | ConvertFrom-Json }
    $url = if ($pin -and $pin.PSObject.Properties['apk_url']) { "$($pin.apk_url)" } else { '' }
    if (Test-Path -LiteralPath $local) {
        # built here: warn when the app's sources changed after it (tools\android\build_apk.ps1 was not run again)
        $src = Under $Root 'android'
        if (Test-Path -LiteralPath $src) {
            $newer = @(Get-ChildItem -LiteralPath $src -Recurse -File | Where-Object { $_.LastWriteTimeUtc -gt (Get-Item -LiteralPath $local).LastWriteTimeUtc })
            if ($newer.Count) { Warn "$local is older than $($newer[0].FullName): run tools\android\build_apk.ps1 to build the app again with it" }
        }
        return , @($local, '')
    }
    if (-not $url) { Warn "no app to install: none published yet (tools\internal\android_pack.json apk_url) and no $local (tools\android\build_apk.ps1)"; return $null }
    $ver = "$($pin.apk_version)"
    $sha = "$($pin.apk_sha256)".ToLower()
    $dl = Under $Root "_prepare\downloads\tsukihime-android-$ver.apk"
    New-Item -ItemType Directory -Path (Split-Path -Parent $dl) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $dl) -or (FileSha256 $dl) -ne $sha) {
        Info "downloading the app v${ver}: $url"
        $oldPp = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -Uri $url -OutFile "$dl.partial" -UseBasicParsing } finally { $ProgressPreference = $oldPp }
        $h = FileSha256 "$dl.partial"
        if ($sha -and $h -ne $sha) { Remove-Item -LiteralPath "$dl.partial" -Force; Fail "the downloaded app does not match (SHA-256 $h, expected $sha)" }
        Move-Item -LiteralPath "$dl.partial" -Destination $dl -Force
    }
    return , @($dl, $ver)
}

try {
    $Root = FindRoot $Root
    $ed = $AndroidEditions[$Edition]
    $data = Under $Root $ed.Data
    if ($InternalStorage -and $ExternalStorage) { Fail '-InternalStorage or -ExternalStorage: one of them' }
    $send = $InternalStorage -or $ExternalStorage
    if (($InstallApp -or $UpdateApp -or $Dump) -and -not $send) { Fail '-InstallApp, -UpdateApp and -Dump go with -InternalStorage or -ExternalStorage' }

    Write-Host "android_port: edition $Edition$(if ($send) { ', send to the phone' })"
    foreach ($f in @('analysis\main.bin', 'analysis\sdk.bin', 'romfs_source.bin', 'spirv\results.tsv', 'fonts\shared_0.ttf', 'gen\main\funcs.h', 'gen\sdk\funcs.h')) {
        if (-not (Test-Path -LiteralPath (Under $data $f))) {
            Fail "$(Under $data $f) does not exist: recompile the edition first (tools\recomp_tsukihime.ps1 -Edition $Edition, or the launcher's Recompile)"
        }
    }

    # 1. the game's library (only what changed is compiled again)
    Part "game library ($Edition)"
    $p = @{ Edition = $Edition; Root = $Root }
    if ($Pack) { $p.Pack = $Pack }
    if ($Jobs) { $p.Jobs = $Jobs }
    RunScript 'build_game.ps1' $p

    # 2. the folder for the phone, and with -InternalStorage / -ExternalStorage, sent to it
    Part $(if ($send) { 'folder for the phone, sent by USB' } else { 'folder for the phone' })
    $p = @{ Edition = $Edition; Root = $Root; PhoneFolder = $PhoneFolder }
    foreach ($k in @('Mods', 'Saves', 'Dump', 'InternalStorage', 'ExternalStorage', 'GetAdb')) {
        if ((Get-Variable -Name $k -ValueOnly).IsPresent) { $p[$k] = $true }
    }
    if ($Volume) { $p.Volume = $Volume }
    if ($Adb) { $p.Adb = $Adb }
    RunScript 'phone_data.ps1' $p

    # 3. the app
    if ($InstallApp -or $UpdateApp) {
        Part 'app on the phone'
        $adbExe = NeedAdb $Adb '' ([bool]$GetAdb)
        AdbDevice $adbExe
        $apk = FindApk
        $have = InstalledApp $adbExe
        # The same APK is already there when the phone's base.apk has its SHA-256 (exact, also for one rebuilt here
        # with the same version); if that cannot be read, by the version (published APKs only).
        $same = $false
        if ($apk -and $have[0]) {
            $sha = FileSha256 $apk[0]
            $same = $(if ($have[2]) { $have[2] -eq $sha } else { [bool]$apk[1] -and $have[1] -eq $apk[1] })
        }
        if (-not $apk) {
            if (-not $have[0]) { Warn 'install the app on the phone by hand (the APK of the release)' }
        } elseif ($same -and -not $UpdateApp) {
            Info "the app is already on the phone (version $($have[1]), the same APK)"
        } else {
            Info $(if ($have[0]) { "updating the app on the phone (version $($have[1]) -> $(if ($apk[1]) { $apk[1] } else { 'the one built here' }))" } else { 'installing the app' })
            $out = AdbText $adbExe @('install', '-r', $apk[0])
            if ($out -notmatch 'Success') {
                if ($out -match 'INSTALL_FAILED_UPDATE_INCOMPATIBLE') {
                    Fail ('the app on the phone was signed with another key: uninstall it on the phone and run this again ' +
                          '(the port folder, your saves and the dump stay; only the folder chosen in the app is asked again)')
                }
                Fail "adb install failed: $($out.Trim())"
            }
            Info 'app installed'
        }
    }

    Write-Host ''
    $phone = Under $Root "_android\$Edition\phone"
    if ($send) { Write-Host 'Done. On the phone: open Tsukihime, choose the port folder (the first time) and Play.' }
    else { Write-Host "Done. Copy the CONTENTS of $phone to a folder of the phone, then in the app choose that folder and Play." }
} catch {
    Write-Host ''
    Write-Host "ERROR in android_port: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
