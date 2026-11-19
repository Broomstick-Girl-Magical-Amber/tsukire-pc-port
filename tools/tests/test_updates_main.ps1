<#
test_updates_main.ps1 - active updates with different main (recomp_tsukihime.ps1, StepUpdates).

Runs the REAL block of recomp_tsukihime.ps1 ("# Active updates:" up to the summary line) with its real helper
functions (Under, IsUpdateLayer, ReadText, Info, Fail) on throwaway trees built here: no dump, no keys, no game files.

Cases:
  - two active updates with different main               -> refused, both named, nothing deleted
  - two active updates with the same main                -> accepted, main of the first, both named
  - one with its own main + one romfs-only update        -> accepted
  - different mains but one disabled (_<name>)           -> accepted (only active ones count)
  - different mains with -NoUpdates                      -> accepted, no main taken from the updates
  - three active updates, two of them with the same main and one different -> refused
  - an update that also changes another exefs module     -> refused
  - an update whose layer folder is gone                 -> its data is removed, it does not count

Run (Windows PowerShell 5.1 or PowerShell 7):
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_updates_main.ps1
Exit code 0 = all passed.
#>
param([string]$Script = '')

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path (Split-Path -Parent $here) 'recomp_tsukihime.ps1' }
if (-not (Test-Path -LiteralPath $Script)) { Write-Host "ERROR: $Script does not exist"; exit 2 }

$lines = [IO.File]::ReadAllLines($Script)

# The real helpers (one-line functions of the script).
$helpers = @($lines | Where-Object { $_ -match '^function (Under|IsUpdateLayer|ReadText|Info|Fail)\(' })
if ($helpers.Count -ne 5) { Write-Host "ERROR: expected 5 helper functions in $Script, found $($helpers.Count)"; exit 2 }
$Sep = [IO.Path]::DirectorySeparatorChar
. ([scriptblock]::Create(($helpers -join "`n")))

# The real block.
$first = -1; $last = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($first -lt 0 -and $lines[$i] -match '^\s*# Active updates:') { $first = $i }
    if ($first -ge 0 -and $lines[$i] -match '^\s*\$Summary\[''Updates''\]') { $last = $i; break }
}
if ($first -lt 0 -or $last -lt 0) { Write-Host 'ERROR: the "Active updates" block was not found in the script'; exit 2 }
$blockText = ($lines[$first..$last] -join "`n")
$block = [scriptblock]::Create($blockText)

$script:Log = @()
function Info([string]$t) { $script:Log += $t }   # collected instead of printed

$script:fail = 0
function Check([bool]$ok, [string]$name, [string]$detail = '') {
    if ($ok) { Write-Host "ok   $name" } else { Write-Host "FAIL $name $detail"; $script:fail++ }
}

$tmpRoot = Join-Path ([IO.Path]::GetTempPath()) ("upd_main_test_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpRoot | Out-Null

function New-Case([string]$tag, $updates) {
    # $updates: @{ Name; Nsp; Diff (array); Sha; Layer ('on' | 'off' | 'gone') }
    $root = Join-Path $tmpRoot $tag
    $upd = Join-Path $root 'data\updates'
    $mods = Join-Path $root 'mods\japan'
    New-Item -ItemType Directory -Path $upd, $mods -Force | Out-Null
    foreach ($u in $updates) {
        $d = Join-Path $upd $u.Name
        New-Item -ItemType Directory -Path (Join-Path $d 'exefs') -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $d 'source.txt'), "stamp`r`n")
        [IO.File]::WriteAllBytes((Join-Path $d 'exefs\main'), [Text.Encoding]::ASCII.GetBytes("main of " + $u.Name))
        $json = [ordered]@{ nsp = $u.Nsp; changed = 3; new = 1; exefs_different = @($u.Diff); removed = @(); main_sha256 = $u.Sha }
        [IO.File]::WriteAllText((Join-Path $d 'mod.json'), ($json | ConvertTo-Json -Depth 4))
        $layerName = switch ($u.Layer) { 'off' { '_' + $u.Name } default { $u.Name } }
        if ($u.Layer -ne 'gone') {
            $l = Join-Path $mods $layerName
            New-Item -ItemType Directory -Path $l | Out-Null
            [IO.File]::WriteAllText((Join-Path $l 'from_update.txt'), "layer`r`n")
        }
    }
    return @{ Upd = $upd; Mods = $mods }
}

function Run-Block($case, [bool]$noUpdates = $false) {
    $script:UpdData = $case.Upd
    $script:ModsOut = $case.Mods
    $script:Edition = 'japan'
    $script:NoUpdates = $noUpdates
    $script:Summary = @{}
    $script:UpdateMain = 'unset'
    $script:UpdateMainFrom = 'unset'
    $script:Log = @()
    try { & $block; return $null } catch { return [string]$_.Exception.Message }
}

$A = 'A' * 64; $B = 'B' * 64

# 1. different mains -> refused
$c = New-Case 'c1' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $B; Layer = 'on' })
$err = Run-Block $c
Check ($err -ne $null -and $err -match 'different main and cannot be combined') 'two active updates with different main: refused' $err
Check ($err -match 'update_one\.nsp: main AAAAAAAAAAAAAAAA' -and $err -match 'update_two\.nsp: main BBBBBBBBBBBBBBBB') 'the message names both updates with the first 16 hex digits of their main' $err
Check ($err -match '_<name>') 'the message says how to fix it (rename the folder to _<name>)' $err
Check ((Test-Path -LiteralPath (Join-Path $c.Upd 'u1\mod.json')) -and (Test-Path -LiteralPath (Join-Path $c.Upd 'u2\mod.json')) -and
       (Test-Path -LiteralPath (Join-Path $c.Mods 'u1\from_update.txt')) -and (Test-Path -LiteralPath (Join-Path $c.Mods 'u2\from_update.txt'))) 'refusing deletes nothing (data and layers intact)'

# 2. same main -> accepted
$c = New-Case 'c2' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' })
$err = Run-Block $c
Check ($null -eq $err) 'two active updates with the same main: accepted' $err
Check ($script:UpdateMain -eq (Join-Path $c.Upd 'u1\exefs\main')) 'same main: the first (by name) provides it' $script:UpdateMain
Check ($script:UpdateMainFrom -eq 'update_one.nsp, update_two.nsp') 'same main: both updates are named as its source' $script:UpdateMainFrom

# 3. its own main + romfs-only update -> accepted
$c = New-Case 'c3' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'translation.nsp'; Diff = @(); Sha = $null; Layer = 'on' })
$err = Run-Block $c
Check ($null -eq $err -and $script:UpdateMain -eq (Join-Path $c.Upd 'u1\exefs\main')) 'one update with main + one romfs-only update: accepted' "$err $script:UpdateMain"

# 4. different mains, one disabled -> accepted
$c = New-Case 'c4' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $B; Layer = 'off' })
$err = Run-Block $c
Check ($null -eq $err -and $script:UpdateMainFrom -eq 'update_one.nsp') 'different mains but one disabled (_name): accepted, only the active one counts' "$err $script:UpdateMainFrom"
Check ((Test-Path -LiteralPath (Join-Path $c.Upd 'u2\mod.json'))) 'the disabled update keeps its data'

# 5. different mains with -NoUpdates -> accepted, no main from the updates
$c = New-Case 'c5' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $B; Layer = 'on' })
$err = Run-Block $c $true
Check ($null -eq $err -and $null -eq $script:UpdateMain) '-NoUpdates with different mains: accepted, the base main is used' "$err $script:UpdateMain"

# 6. three updates, two share a main and one is different -> refused
$c = New-Case 'c6' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u3'; Nsp = 'update_three.nsp'; Diff = @('main'); Sha = $B; Layer = 'on' })
$err = Run-Block $c
Check ($err -match 'different main' -and $err -match 'update_three\.nsp: main BBBBBBBBBBBBBBBB' -and $err -match 'update_one\.nsp') 'three updates, one with another main: refused and all are listed' $err

# 7. another exefs module besides main -> refused
$c = New-Case 'c7' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main', 'sdk'); Sha = $A; Layer = 'on' })
$err = Run-Block $c
Check ($err -match 'changes sdk besides main: not supported') 'an update that also changes sdk: refused' $err

# 8. layer gone -> its data is removed and it does not count
$c = New-Case 'c8' @(
    @{ Name = 'u1'; Nsp = 'update_one.nsp'; Diff = @('main'); Sha = $A; Layer = 'on' },
    @{ Name = 'u2'; Nsp = 'update_two.nsp'; Diff = @('main'); Sha = $B; Layer = 'gone' })
$err = Run-Block $c
Check ($null -eq $err -and $script:UpdateMainFrom -eq 'update_one.nsp') 'an update whose layer folder is gone does not count' "$err $script:UpdateMainFrom"
Check (-not (Test-Path -LiteralPath (Join-Path $c.Upd 'u2'))) 'an update whose layer folder is gone: its data is removed'

Remove-Item -LiteralPath $tmpRoot -Recurse -Force
if ($script:fail) { Write-Host "$($script:fail) FAILED"; exit 1 }
Write-Host 'all passed'
exit 0