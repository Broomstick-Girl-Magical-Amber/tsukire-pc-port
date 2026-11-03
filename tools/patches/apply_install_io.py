"""apply_install_io.py - less waiting on disk and network in install_requirements.ps1. Marker AP_INSTALL_IO.

1. Zip files (FFmpeg ~77 MB, the winget dependencies) are unpacked with tar.exe (bsdtar, in System32 since
   Windows 10 1803; the script already requires 1809) instead of Expand-Archive, which in Windows PowerShell 5.1 is
   very slow (one PowerShell call per file). If tar fails, Expand-Archive as before. Linux: Expand-Archive (pwsh 7).
2. Ryubing tree check (step 8): one SHA-256 object reused for its 4310 files instead of one Get-FileHash call per file
   (same text and same hash; several times faster), and once the tree has been verified a stamp in
   _prepare\\ryubing_verified.txt (reference hash + file count, total size and newest date of the tree, without bin\\
   and obj\\) lets the next runs skip the 4310 hashes while nothing in the tree changes. A change in any file (date or
   size), or a file more or less, makes the full check run again.
3. vcpkg (step 4): only the reference commit is fetched (git init + fetch --depth 1 + checkout), not the whole
   history: measured ~20 s and ~0.1 GB less. vcpkg in classic mode (vcpkg install <ports>) does not read the history.
   NX_VCPKG_FULL_CLONE=1: the full clone, as before. Moving an existing vcpkg to the reference commit now fetches
   that commit only (works for both kinds of clone).
4. VS Build Tools: --includeRecommended stays (it chooses the Windows SDK of that VS version), but three recommended
   components that nothing here uses are left out: VC.ASAN (AddressSanitizer), TestTools.BuildTools and Vcpkg
   (VS's own vcpkg; this project uses its own in -Vcpkg). Less to download and install (not measured). If after
   installing VS the C++ compiler is not there, it is installed once more exactly as before (without the --remove).
5. The Defender warning of step 5 no longer talks about the ffmpeg build (FFmpeg is prebuilt now).

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_install_io.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_INSTALL_IO"
INSTALL = "../tools/install_requirements.ps1"
REMOVES = (" --remove Microsoft.VisualStudio.Component.VC.ASAN --remove Microsoft.VisualStudio.Component.TestTools.BuildTools"
           " --remove Microsoft.VisualStudio.Component.Vcpkg")

EDITS = [
    # ------------------------------------------------------------------------------------------ 4. VS components
    (INSTALL,
     """                Override = '--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --add Microsoft.VisualStudio.Component.VC.CMake.Project' }""",
     """                Override = '--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --add Microsoft.VisualStudio.Component.VC.CMake.Project"""
     + REMOVES + """' }   # AP_INSTALL_IO: --remove of 3 recommended components nothing here uses"""),
    (INSTALL,
     """    InstallWinget 'vs'
}
""",
     """    InstallWinget 'vs'
    # AP_INSTALL_IO: if the C++ compiler is not there after installing without ASAN / TestTools / Vcpkg, once more as before
    $after = DetectVS
    if ((-not $after -or $after.ContainsKey('NoVC')) -and $ToolSpecs.vs.Override -match ' --remove ') {
        Warn 'VS Build Tools without the C++ compiler after the install: trying again with every recommended component'
        $ToolSpecs.vs.Override = $ToolSpecs.vs.Override -replace ' --remove \\S+', ''
        InstallVS
    }
}
"""),
    # ------------------------------------------------------------------------------------------ 1. unzip with tar.exe
    (INSTALL,
     """function IsAdmin {
""",
     """# AP_INSTALL_IO: tar.exe (bsdtar, System32 since Windows 10 1803) unpacks a zip much faster than Expand-Archive of
# Windows PowerShell 5.1; if it is missing or fails, Expand-Archive as before (and always on Linux).
function Unzip([string]$zip, [string]$dst) {
    $tar = if ($OnWindows) { "$env:SystemRoot\\System32\\tar.exe" } else { '' }
    if ($tar -and (Test-Path -LiteralPath $tar)) {
        New-Item -ItemType Directory -Path $dst -Force | Out-Null
        $r = RunCapture $tar @('-xf', $zip, '-C', $dst)
        if ($r.Code -eq 0) { return }
        Warn "tar.exe could not unpack $(Split-Path -Leaf $zip) ($($r.Output | Select-Object -Last 1)): Expand-Archive"
        Remove-Item -LiteralPath $dst -Recurse -Force -ErrorAction SilentlyContinue
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $dst -Force
}

function IsAdmin {
"""),
    (INSTALL,
     """    Expand-Archive -LiteralPath $depsZip -DestinationPath $depsDir -Force
""",
     """    Unzip $depsZip $depsDir   # AP_INSTALL_IO
"""),
    (INSTALL,
     """        Expand-Archive -LiteralPath $zip -DestinationPath $tmp
""",
     """        Unzip $zip $tmp   # AP_INSTALL_IO
"""),
    # ------------------------------------------------------------------------------------------ 2. ryubing tree
    (INSTALL,
     """    $l = New-Object System.Collections.Generic.List[string]
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
        $p = $f.FullName.Substring($base.Length + 1).Replace('\\', '/')
        if ($p -match '(^|/)(bin|obj)/') { continue }
        $l.Add("$p  " + (Sha256 $f.FullName))
    }
""",
     """    $l = New-Object System.Collections.Generic.List[string]
    $hasher = [Security.Cryptography.SHA256]::Create()   # AP_INSTALL_IO: one hasher for every file (Get-FileHash per file is much slower)
    try {
        foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
            $p = $f.FullName.Substring($base.Length + 1).Replace('\\', '/')
            if ($p -match '(^|/)(bin|obj)/') { continue }
            $fs = [IO.File]::OpenRead($f.FullName)
            try { $hb = $hasher.ComputeHash($fs) } finally { $fs.Dispose() }
            $l.Add("$p  " + [BitConverter]::ToString($hb).Replace('-', ''))
        }
    } finally { $hasher.Dispose() }
"""),
    (INSTALL,
     """function TreeInventory([string]$dir) {
""",
     """# AP_INSTALL_IO: cheap fingerprint of a verified tree (reference hash, number of files, total size, newest date; without
# bin\\ and obj\\): the full TreeInventory runs again only if it changes.
function TreeStamp([string]$dir) {
    $base = [IO.Path]::GetFullPath($dir).TrimEnd('\\', '/')
    $n = 0; $size = [int64]0; $newest = [int64]0
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
        $p = $f.FullName.Substring($base.Length + 1).Replace('\\', '/')
        if ($p -match '(^|/)(bin|obj)/') { continue }
        $n++; $size += $f.Length
        if ($f.LastWriteTimeUtc.Ticks -gt $newest) { $newest = $f.LastWriteTimeUtc.Ticks }
    }
    return "$RyubingHash|$n|$size|$newest"
}
function RyubingStampFile { PathOf $WorkDir 'ryubing_verified.txt' }

function TreeInventory([string]$dir) {
"""),
    (INSTALL,
     """    if (Test-Path -LiteralPath $ry) {
        $inv = TreeInventory $ry
        if ($inv.Hash -eq $RyubingHash) { Info "ryubing a82350bb77: tree identical to the reference one ($($inv.Files) files)" }
""",
     """    if (Test-Path -LiteralPath $ry) {
        # AP_INSTALL_IO: verified before and unchanged since (stamp): the 4310 hashes are not computed again
        $sf = RyubingStampFile
        $quick = $null
        if (Test-Path -LiteralPath $sf) { $quick = TreeStamp $ry; if ((Get-Content -LiteralPath $sf -Raw).Trim() -ne $quick) { $quick = $null } }
        $inv = if ($quick) { @{ Hash = $RyubingHash; Files = $RyubingFiles } } else { TreeInventory $ry }
        if ($inv.Hash -eq $RyubingHash -and -not $quick -and -not $Check) { try { [IO.File]::WriteAllText($sf, (TreeStamp $ry)) } catch { } }
        if ($inv.Hash -eq $RyubingHash) { Info "ryubing a82350bb77: tree identical to the reference one ($($inv.Files) files$(if ($quick) { ', unchanged since it was verified' }))" }
"""),
    (INSTALL,
     """            Info "ryubing extracted into $ry ($RyubingFiles files, verified tree)"
""",
     """            Info "ryubing extracted into $ry ($RyubingFiles files, verified tree)"
            try { [IO.File]::WriteAllText((RyubingStampFile), (TreeStamp $ry)) } catch { }   # AP_INSTALL_IO
"""),
    # ------------------------------------------------------------------------------------------ 3. shallow vcpkg
    (INSTALL,
     """            Run $git @('clone', $VcpkgRepo, $tmp) 'git clone vcpkg'
            Run $git @('-C', $tmp, 'checkout', '-q', $VcpkgCommit) 'git checkout (vcpkg)'
""",
     """            if ($env:NX_VCPKG_FULL_CLONE) {
                Run $git @('clone', $VcpkgRepo, $tmp) 'git clone vcpkg'
                Run $git @('-C', $tmp, 'checkout', '-q', $VcpkgCommit) 'git checkout (vcpkg)'
            } else {
                # AP_INSTALL_IO: only the reference commit, without history (~20 s and ~0.1 GB less; classic mode does not read it)
                Run $git @('init', '-q', $tmp) 'git init (vcpkg)'
                Run $git @('-C', $tmp, 'remote', 'add', 'origin', $VcpkgRepo) 'git remote add (vcpkg)'
                Run $git @('-C', $tmp, 'fetch', '-q', '--depth', '1', 'origin', $VcpkgCommit) 'git fetch (vcpkg)'
                Run $git @('-C', $tmp, 'checkout', '-q', 'FETCH_HEAD') 'git checkout (vcpkg)'
            }
"""),
    (INSTALL,
     """                Run $git @('-C', $Vcpkg, 'fetch', 'origin') 'git fetch (vcpkg)'
""",
     """                Run $git @('-C', $Vcpkg, 'fetch', 'origin', $VcpkgCommit) 'git fetch (vcpkg)'   # AP_INSTALL_IO: that commit only (shallow clones too)
"""),
    # ------------------------------------------------------------------------------------------ 5. Defender text
    (INSTALL,
     """                Warn "Defender scans every file the ffmpeg build creates (thousands of MSYS2 processes): it can make it several times slower. If you want, as administrator: Add-MpPreference -ExclusionPath '$Vcpkg'"
""",
     """                Warn "Defender scans every file the vcpkg builds create: it can make them slower. If you want, as administrator: Add-MpPreference -ExclusionPath '$Vcpkg'"   # AP_INSTALL_IO
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
