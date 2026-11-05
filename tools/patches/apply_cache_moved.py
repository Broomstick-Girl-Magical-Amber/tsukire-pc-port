"""apply_cache_moved.py - a port folder copied to another folder or PC builds without deleting framework\\build (AP_CACHE_MOVED).

A CMakeCache.txt keeps absolute paths: the framework folder it was made in (CMAKE_HOME_DIRECTORY), the toolchain file,
clang-cl, Ninja, xwin, the prebuilt libraries. When the whole port folder is copied to another folder or PC, those paths
travel with it and point to places that no longer exist:
- step 9 of recomp_tsukihime.ps1 copied the parameters of the other edition's CMakeCache.txt as they were, so CMake got a
  CMAKE_TOOLCHAIN_FILE of the old folder ("Could not find toolchain file", then "CMAKE_MAKE_PROGRAM is not set");
- a build of the same edition made elsewhere failed with "uses NX_GEN_DIR=..., not ..." (step 9) or "uses another folder
  of generated code" (regen_tsuki*.ps1, step 6).
Now a CMakeCache.txt is used only if it was made in this framework folder and every tool path it holds exists here:
- other edition: its parameters are not copied; step 9 configures with the defaults of this PC (_prepare\\toolchain.json
  with xwin, the environment otherwise), the same as on a PC without any build;
- same edition: its CMakeCache.txt and CMakeFiles\\ are removed and the build is configured again in the same folder (the
  objects are rebuilt: their command lines change);
- regen_tsuki*.ps1 skips its NX_GEN_DIR check for a build made in another folder (step 9 configures it again).

Touches ../tools/recomp_tsukihime.ps1 (and its $Markers), ../tools/internal/regen_tsuki.ps1 and
../tools/internal/regen_tsuki_global.ps1. Idempotent (marker AP_CACHE_MOVED), all or nothing, keeps CRLF and BOM,
--remove leaves the files byte for byte as they were. Independent of the other patchers (either order).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_cache_moved.py
"""
from _patch import run

MARK = "AP_CACHE_MOVED"
RECO = "../tools/recomp_tsukihime.ps1"
REGEN_JA = "../tools/internal/regen_tsuki.ps1"
REGEN_GL = "../tools/internal/regen_tsuki_global.ps1"

FUNC = r'''# AP_CACHE_MOVED: why a CMakeCache.txt cannot be used here ('' = it can): it was made in another framework folder (the port
# folder was copied to another folder or PC) or a tool path it holds does not exist on this PC.
function CacheProblem([string]$cache) {
    $made = ((CacheValue $cache 'CMAKE_HOME_DIRECTORY') -replace '\\', '/').TrimEnd('/')
    $here = ($Framework -replace '\\', '/').TrimEnd('/')
    $how = if ($OnLinux) { [StringComparison]::Ordinal } else { [StringComparison]::OrdinalIgnoreCase }
    if ($made -and -not [string]::Equals($made, $here, $how)) { return "made in $made" }
    foreach ($k in @('CMAKE_TOOLCHAIN_FILE', 'VCPKG_CHAINLOAD_TOOLCHAIN_FILE', 'CMAKE_MAKE_PROGRAM', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER',
                     'NX_XWIN', 'NX_LLVM', 'NX_OPUS_ROOT', 'CMAKE_PREFIX_PATH')) {
        foreach ($p in ((CacheValue $cache $k) -split ';')) {
            if (-not $p -or $p -like '*-NOTFOUND' -or $p -notmatch '[\\/]') { continue }
            if (-not (Test-Path -LiteralPath $p)) { return "$k=$p does not exist" }
        }
    }
    return ''
}

'''

REGEN_OLD = '''if (Test-Path $cache) {
    $line = Select-String -Path $cache -Pattern '^NX_GEN_DIR:' | Select-Object -First 1
'''
REGEN_NEW = r'''# AP_CACHE_MOVED: a build configured in another folder or PC (the port folder was copied) is configured again by step 9
# of recomp_tsukihime.ps1: no check for it.
$cacheHome = ''
if (Test-Path $cache) {
    $h = Select-String -Path $cache -Pattern '^CMAKE_HOME_DIRECTORY:[A-Z]+=(.*)$' | Select-Object -First 1
    if ($h) { $cacheHome = ($h.Matches[0].Groups[1].Value -replace '\\', '/').TrimEnd('/') }
}
if ((Test-Path $cache) -and (-not $cacheHome -or $cacheHome -eq (($Framework -replace '\\', '/').TrimEnd('/')))) {
    $line = Select-String -Path $cache -Pattern '^NX_GEN_DIR:' | Select-Object -First 1
'''

EDITS = [
    (RECO,
     '''function Step9 {
''',
     FUNC + '''function Step9 {
'''),
    (RECO,
     '''    Push-Location $Framework
    try {
        if (Test-Path -LiteralPath $cache) {
            $g = (CacheValue $cache 'NX_GEN_DIR') -replace '\\\\', '/'
''',
     '''    Push-Location $Framework
    try {
        if (Test-Path -LiteralPath $cache) {   # AP_CACHE_MOVED: a build made in another folder or PC is configured again here
            $why = CacheProblem $cache
            if ($why) {
                Info "$Build was configured elsewhere ($why): configuring it again here (its CMakeCache.txt and CMakeFiles are removed; the objects are rebuilt)"
                Remove-Item -LiteralPath $cache -Force
                $cmf = Under $Build 'CMakeFiles'
                if (Test-Path -LiteralPath $cmf) { Remove-Item -LiteralPath $cmf -Recurse -Force }
            }
        }
        if (Test-Path -LiteralPath $cache) {
            $g = (CacheValue $cache 'NX_GEN_DIR') -replace '\\\\', '/'
'''),
    (RECO,
     '''            $other = $cands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
''',
     '''            $other = $cands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($other) {   # AP_CACHE_MOVED: parameters of a build made in another folder or PC are not copied
                $why = CacheProblem $other
                if ($why) { Info "not copying the parameters of $other ($why): defaults of this PC"; $other = $null }
            }
'''),
    (RECO,
     '''    @('japan', 'framework\\games\\tsukihime\\game.cmake', 'tsukihime_common/tsukihime_common.cmake',''',
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_CACHE_MOVED', 'apply_cache_moved.py', $false),
    @('japan', 'tools\\internal\\regen_tsuki.ps1', 'AP_CACHE_MOVED', 'apply_cache_moved.py', $false),
    @('global', 'tools\\internal\\regen_tsuki_global.ps1', 'AP_CACHE_MOVED', 'apply_cache_moved.py', $false),
    @('japan', 'framework\\games\\tsukihime\\game.cmake', 'tsukihime_common/tsukihime_common.cmake','''),
    (REGEN_JA, REGEN_OLD, REGEN_NEW),
    (REGEN_GL, REGEN_OLD, REGEN_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)