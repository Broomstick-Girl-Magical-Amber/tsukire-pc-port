"""apply_launcher.py - the Windows launcher in git (AP_LAUNCHER): .gitignore lets its sources through.

The launcher (tsukihime_launcher.exe, part 1: play and saves, install and recompile) is new code in <root>/launcher
(CMakeLists.txt, src/, res/) plus tools/dev/build_launcher.ps1; it needs no change to the existing scripts or to the
framework. The root .gitignore is an allowlist, so launcher/ needs its lines: only the sources go in; launcher/build,
the .exe in the root and what the launcher writes in the root (launcher.ini, save_backups/) stay out like everything
else in the root. tools/dev/build_launcher.ps1 already goes in with tools/dev/.

Touches ../.gitignore and ../tools/recomp_tsukihime.ps1 (one $Markers row, anchored after the row of
apply_jt_lsr.py, which no other patcher uses as an anchor). Idempotent (marker AP_LAUNCHER), all or nothing, keeps
CRLF and BOM, --remove leaves both files byte for byte as they were. Independent of the other patchers (either order).
New files (copy them before applying): launcher/CMakeLists.txt, launcher/src/*, launcher/res/*,
tools/dev/build_launcher.ps1.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_launcher.py
"""
from _patch import run

MARK = "AP_LAUNCHER"
GITIGNORE = "../.gitignore"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (GITIGNORE,
     '''/tools/dev.local.ps1
''',
     '''/tools/dev.local.ps1
# AP_LAUNCHER: the Windows launcher (apply_launcher.py), sources only. launcher/build, tsukihime_launcher.exe and what
# the launcher writes in the root (launcher.ini, save_backups/) stay out like everything else in the root.
!/launcher/
/launcher/*
!/launcher/CMakeLists.txt
!/launcher/src/
!/launcher/res/
'''),
    (RECO,
     '''    @('both', 'framework\\recomp\\analysis.py', '_JT_LSR = ', 'apply_jt_lsr.py (version 2)', $false),
''',
     '''    @('both', 'framework\\recomp\\analysis.py', '_JT_LSR = ', 'apply_jt_lsr.py (version 2)', $false),
    @('both', '.gitignore', 'AP_LAUNCHER', 'apply_launcher.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
