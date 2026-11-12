# phone_data.ps1 - Puts together the port folder for the phone (phase 1 of the Android port): only what the game reads besides the dump, with the same layout as the port on the PC, so
# the game finds everything by itself. Copy that folder to the phone (USB by hand, or adb with -InternalStorage or
# -ExternalStorage), put the .xci/.nsp in it (or choose it in the app with "Dump") and choose the folder in the app.
#
#   .\tools\android\phone_data.ps1 -Edition global
#   .\tools\android\phone_data.ps1 -Edition japan -Mods -Saves        # also the mod layers and your saves of this PC
#   .\tools\android\phone_data.ps1 -Edition global -InternalStorage    # and copy it to the phone's internal storage (adb)
#   .\tools\android\phone_data.ps1 -Edition global -ExternalStorage    # or to its SD card (adb)
#   .\tools\android\phone_data.ps1 -Edition global -InternalStorage -Dump   # the dump too (~23 GB: a while over USB)
#   Linux: bash tools/android/phone_data.sh -Edition global
#
# Result: <root>\_android\<edition>\phone\ with
#   data\<tsuki|tsuki_global>\analysis\main.bin, sdk.bin, main_source.txt   (the game's modules: from your dump)
#   data\<...>\android\lib<game>.so   the game's library (build_apk.ps1 -Edition); the app installs it from here
#   data\<...>\romfs_source.bin   the table of the RomFS (with the key of its NCA: the game never reads prod.keys)
#   data\<...>\spirv\             the translated shaders
#   data\<...>\fonts\             the shared font (Noto Sans JP, OFL)
#   data\<...>\hud\               the HUD icons by device (if they were built)
#   data\<...>\language.txt, title.ini   if you have them
#   mods\<edition>\               with -Mods: the active layers (folders not starting with _ or .)
#   data\<...>\user\              with -Saves: your saves (on the phone they replace the ones there!)
# Not copied: extract\, exefs\, gen\, romfs_build\, updates\, input.ini, config.txt (Windows options), nx.log,
# pipelines (GPU cache of this PC). Everything here comes from your dump: never publish it.
# Without -InternalStorage / -ExternalStorage nothing is sent to the phone (copy the folder by hand).
# -InternalStorage: adb push to /storage/emulated/0/<PhoneFolder> (the phone's own storage; default folder Tsukihime).
# -ExternalStorage: adb push to /storage/<SD card>/<PhoneFolder> (the SD card; -Volume <id, e.g. 1A2B-3C4D> if there
# are several). The phone must be connected with USB debugging. The files are replaced, the rest of that folder (your
# saves on the phone, the dump) is not touched unless -Saves/-Dump.
param(
    [Parameter(Mandatory = $true)][ValidateSet('japan', 'global')][string]$Edition,
    [switch]$Mods,
    [switch]$Saves,
    [switch]$InternalStorage,
    [switch]$ExternalStorage,
    [string]$Volume,
    [switch]$Dump,
    [string]$PhoneFolder = 'Tsukihime',
    [string]$Root,
    [string]$Sdk
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'android_common.ps1')

# Copies a file if the target is missing or differs in size or date (robocopy-like, without deleting anything).
function SyncFile([string]$src, [string]$dst) {
    $s = Get-Item -LiteralPath $src
    if (Test-Path -LiteralPath $dst) {
        $d = Get-Item -LiteralPath $dst
        if ($d.Length -eq $s.Length -and $d.LastWriteTimeUtc -eq $s.LastWriteTimeUtc) { return 0 }
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
    (Get-Item -LiteralPath $dst).LastWriteTimeUtc = $s.LastWriteTimeUtc
    return 1
}
function SyncDir([string]$src, [string]$dst) {
    $n = 0
    $base = (Get-Item -LiteralPath $src).FullName.TrimEnd('\', '/')
    foreach ($f in @(Get-ChildItem -LiteralPath $src -Recurse -File -Force)) {
        $rel = $f.FullName.Substring($base.Length + 1)
        $n += SyncFile $f.FullName (Join-Path $dst $rel)
    }
    return $n
}
function SizeOf([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return 0 }
    $m = @(Get-ChildItem -LiteralPath $p -Recurse -File -Force | Measure-Object -Property Length -Sum)
    if ($m.Count -and $m[0].Sum) { return [int64]$m[0].Sum }
    return 0
}

try {
    $Root = FindRoot $Root
    $ed = $AndroidEditions[$Edition]
    $data = Under $Root $ed.Data
    $out = Under $Root "_android\$Edition\phone"
    $outData = Under $out $ed.Data

    Step "port folder for the phone: $Edition"
    $need = @('analysis\main.bin', 'analysis\sdk.bin', 'romfs_source.bin', 'spirv\results.tsv', 'fonts\shared_0.ttf')
    foreach ($f in $need) {
        if (-not (Test-Path -LiteralPath (Under $data $f))) {
            Fail "$(Under $data $f) does not exist: recompile the edition first (tools\recomp_tsukihime.ps1 -Edition $Edition)"
        }
    }
    $copied = 0
    foreach ($f in @('analysis\main.bin', 'analysis\sdk.bin', 'analysis\main_source.txt', 'romfs_source.bin', 'language.txt', 'title.ini')) {
        if (Test-Path -LiteralPath (Under $data $f)) { $copied += SyncFile (Under $data $f) (Under $outData $f) }
    }
    # the game's library (tools\android\build_apk.ps1 -Edition): the app installs it from data\<...>\android\
    $gameLib = Under $Root "_android\$Edition\lib$($ed.Game).so"
    if (Test-Path -LiteralPath $gameLib) { $copied += SyncFile $gameLib (Under $outData "android\lib$($ed.Game).so") }
    else { Warn "no $gameLib (tools\android\build_apk.ps1 -Edition $Edition): the folder goes without the game's library" }
    foreach ($d in @('spirv', 'fonts', 'hud')) {
        if (Test-Path -LiteralPath (Under $data $d) -PathType Container) { $copied += SyncDir (Under $data $d) (Under $outData $d) }
        elseif ($d -eq 'hud') { Warn 'no hud\ (HUD icons by device): the game shows the Switch ones' }
    }
    # The folder is ours: what was put in it by an earlier run with -Mods / -Saves goes away when they are not given now
    # (otherwise old saves could be copied to the phone by mistake).
    $stMods = Under $out $ed.Mods
    if (-not $Mods -and (Test-Path -LiteralPath $stMods)) { Remove-Item -LiteralPath $stMods -Recurse -Force; Info 'mods\ of an earlier run removed from the folder (no -Mods)' }
    if (-not $Saves -and (Test-Path -LiteralPath (Under $outData 'user'))) { Remove-Item -LiteralPath (Under $outData 'user') -Recurse -Force; Info 'user\ of an earlier run removed from the folder (no -Saves)' }
    if ($Mods) {
        $m = Under $Root $ed.Mods
        $layers = @()
        if (Test-Path -LiteralPath $m -PathType Container) {
            $layers = @(Get-ChildItem -LiteralPath $m -Directory | Where-Object { -not $_.Name.StartsWith('_') -and -not $_.Name.StartsWith('.') })
        }
        if (-not $layers.Count) { Warn "no active layers in $m" }
        if (Test-Path -LiteralPath $stMods) {   # layers no longer active on the PC
            foreach ($o in @(Get-ChildItem -LiteralPath $stMods -Directory)) {
                if (-not @($layers | Where-Object { $_.Name -eq $o.Name }).Count) { Remove-Item -LiteralPath $o.FullName -Recurse -Force; Info "layer $($o.Name) removed from the folder (not active)" }
            }
        }
        foreach ($l in $layers) {
            $copied += SyncDir $l.FullName (Under $out "$($ed.Mods)\$($l.Name)")
            Info "mod layer: $($l.Name)"
            $req = Join-Path $l.FullName 'requires_main.txt'
            if (Test-Path -LiteralPath $req) {
                $main = Under $data 'analysis\main_source.txt'
                if (-not (Test-Path -LiteralPath $main) -or (Get-Content -LiteralPath $req -Raw).Trim() -ne (Get-Content -LiteralPath $main -Raw).Trim()) {
                    Warn "$($l.Name) needs another main (requires_main.txt): the game will skip it unless the APK is built from that main"
                }
            }
        }
    }
    if ($Saves) {
        if (Test-Path -LiteralPath (Under $data 'user') -PathType Container) {
            $copied += SyncDir (Under $data 'user') (Under $outData 'user')
            Warn 'your saves of this PC are in the folder: on the phone they replace the ones there'
        } else { Warn "no saves in $(Under $data 'user')" }
    }
    $mb = [math]::Round((SizeOf $out) / 1MB, 1)
    Info "$out ($mb MB; $copied files copied or updated now)"

    if ($InternalStorage -and $ExternalStorage) { Fail '-InternalStorage or -ExternalStorage: one of them' }
    if ($Dump -and -not ($InternalStorage -or $ExternalStorage)) { Fail '-Dump goes with -InternalStorage or -ExternalStorage' }
    $Push = $InternalStorage -or $ExternalStorage
    $target = $null
    if ($Push) {
        $sdkDir = FindSdk $Sdk
        $adb = Under $sdkDir "platform-tools\adb$Exe"
        if (-not (Test-Path -LiteralPath $adb)) { Fail "no adb in $(Under $sdkDir 'platform-tools') (SDK Manager > Android SDK Platform-Tools)" }
        AdbDevice $adb
        if ($InternalStorage) { $target = "/storage/emulated/0/$PhoneFolder" }
        else {
            # SD cards are mounted as /storage/<id> (e.g. 1A2B-3C4D); "emulated" and "self" are the internal storage
            $vols = @((AdbText $adb @('shell', 'ls', '/storage')) -split '\s+' | Where-Object { $_ -and $_ -ne 'emulated' -and $_ -ne 'self' -and $_ -notmatch '^ls:' })
            if ($Volume) {
                if (-not ($vols | Where-Object { $_ -eq $Volume })) { Fail "no volume $Volume on the phone (it has: $(if ($vols.Count) { $vols -join ', ' } else { 'no SD card' }))" }
                $vol = $Volume
            } elseif ($vols.Count -eq 1) { $vol = $vols[0] }
            elseif (-not $vols.Count) { Fail 'the phone has no SD card (no /storage/<id>): use -InternalStorage, or copy the folder by hand' }
            else { Fail "several external volumes ($($vols -join ', ')): choose one with -Volume <id>" }
            $target = "/storage/$vol/$PhoneFolder"
        }
        Step "copy to the phone: $target (adb)"
        RunNative $adb @('shell', 'mkdir', '-p', $target) 'adb shell mkdir'
        # adb push of a folder copies it inside the target: push each top folder to its parent
        foreach ($top in @(Get-ChildItem -LiteralPath $out -Directory)) {
            RunNative $adb @('push', $top.FullName, "$target/") "adb push $($top.Name)"
        }
        if ($Dump) {
            $dumps = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -match '^\.(xci|nsp)$' })
            $hint = $null
            try {
                $b = [IO.File]::ReadAllBytes((Under $data 'romfs_source.bin'))
                $n = [BitConverter]::ToInt32($b, 336)
                if ($n -gt 0 -and $n -lt 4096) { $hint = [Text.Encoding]::UTF8.GetString($b, 340, $n) }
            } catch { $hint = $null }
            $pick = @($dumps | Where-Object { $_.Name -eq $hint })
            if (-not $pick.Count) { $pick = $dumps }
            if ($pick.Count -ne 1) { Fail "-Dump: $($dumps.Count) .xci/.nsp in $Root and none named ${hint}: copy the dump by hand" }
            $size = AdbText $adb @('shell', 'stat', '-c', '%s', "$target/$($pick[0].Name)")
            if ("$size".Trim() -eq "$($pick[0].Length)") { Info "$($pick[0].Name) is already on the phone (same size)" }
            else {
                Info "copying $($pick[0].Name) ($([math]::Round($pick[0].Length / 1GB, 1)) GB): this takes a while"
                RunNative $adb @('push', $pick[0].FullName, "$target/") 'adb push (dump)'
            }
        }
        Info "on the phone: $target"
    }
    Write-Host ''
    if ($Push) { Write-Host "Done. In the app: Port folder -> $target -> Use this folder, then Play." }
    else {
        Write-Host "Done. Copy the CONTENTS of $out to a folder of the phone (for example Internal storage\$PhoneFolder),"
        Write-Host 'put the .xci/.nsp in that folder (or choose it in the app with "Dump") and choose the folder in the app.'
    }
} catch {
    Write-Host ''
    Write-Host "ERROR in phone_data: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
