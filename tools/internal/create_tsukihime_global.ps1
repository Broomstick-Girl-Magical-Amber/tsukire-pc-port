# create_tsukihime_global.ps1 - Sets up the global edition of Tsukihime as a separate game (games\tsukihime_global).
#
#   .\create_tsukihime_global.ps1            # creates / completes (idempotent)
#   .\create_tsukihime_global.ps1 -Remove    # undoes everything it did
#
# Does, in this order (checks everything before writing anything):
#  1. Data: moves data\tsuki_global\extract\exefs -> data\tsuki_global\exefs (same as the japan one) and checks
#     that main.npdm is from program ID 010057A01E4D4000. Copies spirv\ and fonts\ from the japan edition if missing
#     (the .spv files are looked up by hash: identical shaders are reused). Does not touch user\ (saves).
#  2. games\tsukihime_global with only game.cpp and game.cmake, taken from those of games\tsukihime: in game.cpp the
#     kInfo and the window title; in game.cmake the header comment, the language (en-US, if the japan one already has
#     the AP_LANG_DEFAULT block) and NX_DT_INIT=0x10 (if the runtime already has AP_DT_INIT; otherwise apply_dt_init.py
#     adds it). The shared code, the .rc and the icon are in games\tsukihime_common (in the repo), which
#     both game.cmake include: nothing is copied.
#  3. tools\internal\regen_tsuki_global.ps1, a copy of regen_tsuki.ps1 pointing to data\tsuki_global and build\tsukihime_global.
#  4. Prints the cmake command to configure build\tsukihime_global (same generator/toolchain as the japan one).
# Nothing of the framework, of games\tsukihime or of games\tsukihime_common is modified.
# The Spanish text in $PairsGameCpp, $PairsGameCmake, $PairsLanguage and $DtInitBlock is framework content (anchors in
# games\tsukihime and what is written to games\tsukihime_global): it must stay exactly as it is.
param([switch]$Remove, [string]$Root = '')  # without -Root: two folders above this script (<root>\tools\internal)

$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$Framework = Join-Path $Root 'framework'
$Tools = Join-Path $Root 'tools\internal'
$DataJp = Join-Path $Root 'data\tsuki'
$DataGl = Join-Path $Root 'data\tsuki_global'
$SrcGame = Join-Path $Framework 'games\tsukihime'
$DstGame = Join-Path $Framework 'games\tsukihime_global'
$CommonCmake = Join-Path $Framework 'games\tsukihime_common\tsukihime_common.cmake'
$MainCpp = Join-Path $Framework 'runtime\src\main.cpp'   # only read: does it have AP_DT_INIT?
$FilesGl = @('game.cpp', 'game.cmake')                   # the only files in games\tsukihime_global
$RegenJp = Join-Path $Tools 'regen_tsuki.ps1'
$RegenGl = Join-Path $Tools 'regen_tsuki_global.ps1'
$Tag = 'tsukihime_global'   # appears in the global game.cpp and in regen_tsuki_global.ps1
$ProgramId = 0x010057A01E4D4000

function ReadFile([string]$p) {
    $b = [IO.File]::ReadAllBytes($p)
    $bom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $t = (New-Object Text.UTF8Encoding($false)).GetString($b)
    if ($bom) { $t = $t.Substring(1) }
    return @{ Text = $t; Bom = $bom }
}
function WriteFile([string]$p, [string]$t, [bool]$bom) {
    [IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($bom)))
}
# Exact replacements, all or nothing: each anchor must appear exactly once. Keeps CRLF (the text of the
# replacements has no line breaks).
function ReplaceExact([string]$text, [object[]]$pairs, [string]$file) {
    foreach ($pair in $pairs) {
        $n = ([regex]::Matches($text, [regex]::Escape($pair[0]))).Count
        if ($n -ne 1) { throw "$file : anchor found $n times (1 expected): $($pair[0])" }
    }
    foreach ($pair in $pairs) { $text = $text.Replace($pair[0], $pair[1]) }
    return $text
}
function ProgramIdOf([string]$npdm) {
    $b = [IO.File]::ReadAllBytes($npdm)
    if ([Text.Encoding]::ASCII.GetString($b, 0, 4) -ne 'META') { throw "$npdm is not an NPDM (no META)" }
    $aci = [BitConverter]::ToUInt32($b, 0x70)
    if ([Text.Encoding]::ASCII.GetString($b, $aci, 4) -ne 'ACI0') { throw "$npdm without ACI0 at +0x$('{0:x}' -f $aci)" }
    return [BitConverter]::ToUInt64($b, $aci + 0x10)
}

$PairsGameCpp = @(
    @('// Tsukihime -A piece of blue glass moon- (01001DC01486A000): lo que el runtime necesita saber del juego.',
      '// Tsukihime -A piece of blue glass moon-, edicion global (010057A01E4D4000): lo que el runtime necesita saber del juego.'),
    @('{"tsukihime", "Tsukihime -A piece of blue glass moon-", 0x01001DC01486A000ull,',
      '{"tsukihime_global", "TSUKIHIME -A piece of blue glass moon- (Global)", 0x010057A01E4D4000ull,'),
    @('"io.github.local.tsukihimerecomp",', '"io.github.local.tsukihimeglobalrecomp",'),
    @('"../../../data/tsuki",', '"../../../data/tsuki_global",'),  # AP_DATA_ROOT_EXE (apply_data_root_exe.py)
    @('0x0 /* main.npdm: system resource size (SDK 11: no usa) */,',
      '0x0 /* main.npdm: system resource size (0 tambien en la global) */,'),
    @('0xc62028, 0xa1 /* tabla de formatos NVN del SDK 11 */,',
      '0xb15700, 0xa1 /* tabla de formatos NVN del SDK 17.5.4 (find_constant.py, misma tabla que 0xc62028 del SDK 11) */,'),
    @('0x2b8 /* AudioRenderSystem: modo de ejecucion (SDK 11) */',
      '0x2d8 /* AudioRenderSystem::GetExecutionMode, SDK 17.5.4 (sdk+0x210388: ldr w0, [x0, #0x2d8]) */'),
    @('L"Tsukihime -A piece of blue glass moon-";', 'L"TSUKIHIME -A piece of blue glass moon- (Global)";')
)
$PairsGameCmake = @(, @('# games/tsukihime/game.cmake:', '# games/tsukihime_global/game.cmake:'))  # comma: array of a single pair
# If the japan edition already has the per-game language (apply_game_language.py), the global one gets its own.
$PairsLanguage = @(
    @('(apply_game_language.py); edicion japonesa', '(apply_game_language.py); edicion global (tambien zh-Hans, zh-Hant, ja)'),
    @('NX_DEFAULT_LANGUAGE="ja")', 'NX_DEFAULT_LANGUAGE="en-US")')
)
# Line of the .rc (anchor of apply_dt_init.py) and what that patch adds after it in the global one.
$RcLine = 'target_sources(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}/tsukihime.rc")'
$DtInitBlock = @('', '# AP_DT_INIT: DT_INIT (_init) de main y sdk en +0x10 en el SDK 17.5.4 (el runtime asume +0x30; apply_dt_init.py)',
                 'target_compile_definitions(${NX_GAME} PRIVATE NX_DT_INIT=0x10)')
$PairsRegen = @(
    @('# regen_tsuki.ps1 - Regenerates the recompiled code of Tsukihime (python -m recomp)',
      '# regen_tsuki_global.ps1 - Regenerates the recompiled code of Tsukihime global edition (python -m recomp)'),
    @('#   .\regen_tsuki.ps1               # main', '#   .\regen_tsuki_global.ps1        # main'),
    @('#   .\regen_tsuki.ps1 main, sdk     # several modules', '#   .\regen_tsuki_global.ps1 main, sdk  # several modules'),
    @('# After: cmake --build build\tsukihime', '# After: cmake --build build\tsukihime_global'),
    @("`$ExeFS = Join-Path `$Root 'data\tsuki\exefs'", "`$ExeFS = Join-Path `$Root 'data\tsuki_global\exefs'"),
    @("`$Gen = Join-Path `$Root 'data\tsuki\gen'", "`$Gen = Join-Path `$Root 'data\tsuki_global\gen'"),
    @('<root>\tools\internal\regen_tsuki.ps1 -> the root', '<root>\tools\internal\regen_tsuki_global.ps1 -> the root'),
    @("'build\tsukihime\CMakeCache.txt'", "'build\tsukihime_global\CMakeCache.txt'"),
    @('"build\tsukihime uses another folder', '"build\tsukihime_global uses another folder'),
    @("'Done. Now: cmake --build build\tsukihime'", "'Done. Now: cmake --build build\tsukihime_global'")
)

# ---------------------------------------------------------------------------------------------- remove
if ($Remove) {
    if (Test-Path $DstGame) {
        $gc = Join-Path $DstGame 'game.cpp'
        if (-not ((Test-Path $gc) -and (ReadFile $gc).Text.Contains('"tsukihime_global"'))) {
            throw "$DstGame does not look created by this script (game.cpp without ""tsukihime_global""): not deleted"
        }
        $others = @(Get-ChildItem $DstGame -Force -Recurse | Where-Object { $FilesGl -notcontains $_.Name })
        if ($others.Count) { throw "$DstGame has files this script does not create ($($others.Name -join ', ')): not deleted" }
    }
    if ((Test-Path $RegenGl) -and -not (ReadFile $RegenGl).Text.Contains($Tag)) { throw "$RegenGl is not from this script: not deleted" }
    if (Test-Path $DstGame) { Remove-Item $DstGame -Recurse -Force; Write-Host "deleted $DstGame" }
    if (Test-Path $RegenGl) { Remove-Item $RegenGl -Force; Write-Host "deleted $RegenGl" }
    $ex = Join-Path $DataGl 'exefs'; $exOrig = Join-Path $DataGl 'extract\exefs'
    if ((Test-Path $ex) -and -not (Test-Path $exOrig)) { Move-Item $ex $exOrig; Write-Host "moved back $ex -> $exOrig" }
    Write-Host 'spirv\ and fonts\ copied to data\tsuki_global are left (delete them by hand if needed). build\tsukihime_global and gen\ too.'
    return
}

# ---------------------------------------------------------------------------------------------- check
foreach ($p in @($SrcGame, (Join-Path $SrcGame 'game.cpp'), (Join-Path $SrcGame 'game.cmake'), $CommonCmake, $RegenJp, $DataGl)) {
    if (-not (Test-Path $p)) { throw "$p does not exist (games\tsukihime_common comes with the repo)" }
}
$ex = Join-Path $DataGl 'exefs'; $exOrig = Join-Path $DataGl 'extract\exefs'
$moveExefs = $false
if (-not (Test-Path (Join-Path $ex 'main'))) {
    if (-not (Test-Path (Join-Path $exOrig 'main'))) { throw "there is no exefs\main in $DataGl or in $DataGl\extract" }
    if (Test-Path $ex) { throw "$ex exists but without main: check it by hand" }
    $moveExefs = $true
    $npdm = Join-Path $exOrig 'main.npdm'
} else { $npdm = Join-Path $ex 'main.npdm' }
$progId = ProgramIdOf $npdm
if ($progId -ne $ProgramId) { throw ('main.npdm is from program ID {0:X16}, not 010057A01E4D4000' -f $progId) }

# New text of each file, fully computed before writing anything.
$alreadyCreated = Test-Path $DstGame
if ($alreadyCreated) {
    $gc = ReadFile (Join-Path $DstGame 'game.cpp')
    if (-not $gc.Text.Contains('"tsukihime_global"')) { throw "$DstGame already exists and is not from the script: not touched" }
} else {
    $gc = ReadFile (Join-Path $SrcGame 'game.cpp')
    $gcNew = ReplaceExact $gc.Text $PairsGameCpp 'game.cpp'
    $gm = ReadFile (Join-Path $SrcGame 'game.cmake')
    if (-not $gm.Text.Contains('tsukihime_common/tsukihime_common.cmake')) {
        throw "games\tsukihime\game.cmake does not include tsukihime_common.cmake (it does in the repo): check the framework"
    }
    $pairs = $PairsGameCmake
    if ($gm.Text.Contains('AP_LANG_DEFAULT')) { $pairs += $PairsLanguage }
    $gmNew = ReplaceExact $gm.Text $pairs 'game.cmake'
    if ((Test-Path $MainCpp) -and (ReadFile $MainCpp).Text.Contains('AP_DT_INIT')) {
        $nl = if ($gm.Text.Contains("`r`n")) { "`r`n" } else { "`n" }
        $gmNew = ReplaceExact $gmNew @(, @(($RcLine + $nl), ($RcLine + $nl + ($DtInitBlock -join $nl) + $nl))) 'game.cmake'
        $dtInit = 'NX_DT_INIT=0x10 included (the runtime already has AP_DT_INIT)'
    } else { $dtInit = 'no NX_DT_INIT: the runtime does not have AP_DT_INIT; run apply_dt_init.py afterwards' }
}
$rgCreate = -not (Test-Path $RegenGl)
if ($rgCreate) {
    $rg = ReadFile $RegenJp
    $rgNew = ReplaceExact $rg.Text $PairsRegen 'regen_tsuki.ps1'
} elseif (-not (ReadFile $RegenGl).Text.Contains($Tag)) { throw "$RegenGl already exists and is not from the script" }

# ---------------------------------------------------------------------------------------------- write
if ($moveExefs) { Move-Item $exOrig $ex; Write-Host "moved $exOrig -> $ex" } else { Write-Host "exefs already in $ex" }
Write-Host ('program ID {0:X16} OK' -f $progId)
$b = [IO.File]::ReadAllBytes((Join-Path $ex 'main'))
Write-Host ('build ID main: ' + (($b[0x40..0x53] | ForEach-Object { $_.ToString('x2') }) -join ''))

foreach ($d in @('spirv', 'fonts')) {
    $s = Join-Path $DataJp $d; $t = Join-Path $DataGl $d
    if ((Test-Path $s) -and -not (Test-Path $t)) { Copy-Item $s $t -Recurse; Write-Host "copied $s -> $t" }
}

if (-not $alreadyCreated) {
    New-Item -ItemType Directory $DstGame | Out-Null
    WriteFile (Join-Path $DstGame 'game.cpp') $gcNew $gc.Bom
    WriteFile (Join-Path $DstGame 'game.cmake') $gmNew $gm.Bom
    Write-Host "created $DstGame (game.cpp and game.cmake; $dtInit)"
} else { Write-Host "$DstGame already existed (not touched)" }
if ($rgCreate) { WriteFile $RegenGl $rgNew $rg.Bom; Write-Host "created $RegenGl" } else { Write-Host "$RegenGl already existed" }

# Configuration command with the same generator/toolchain as build\tsukihime.
$cache = Join-Path $Framework 'build\tsukihime\CMakeCache.txt'
$args2 = @("-B build\tsukihime_global", "-DNX_GAME=tsukihime_global", "-DNX_GEN_DIR=$($DataGl -replace '\\','/')/gen")
if (Test-Path $cache) {
    foreach ($k in 'CMAKE_GENERATOR', 'CMAKE_TOOLCHAIN_FILE', 'CMAKE_BUILD_TYPE', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER', 'VCPKG_TARGET_TRIPLET', 'NX_OPUS_ROOT') {
        $l = Select-String -Path $cache -Pattern "^$k(:[A-Z]+)?=(.*)$" | Select-Object -First 1
        if ($l -and $l.Matches[0].Groups[2].Value) {
            $v = $l.Matches[0].Groups[2].Value
            if ($k -eq 'CMAKE_GENERATOR') { $args2 += "-G ""$v""" } else { $args2 += "-D$k=""$v""" }
        }
    }
} else { Write-Host "Warning: no $cache; the command below has no generator or toolchain" }
Write-Host ''
Write-Host "Next (in $Framework, with the build environment loaded):"
Write-Host "  $RegenGl main, sdk"
Write-Host ("  cmake " + ($args2 -join ' '))
Write-Host '  cmake --build build\tsukihime_global'