"""apply_ninja_log_force.py - step 9 of recomp_tsukihime.ps1 on Linux with a build that already exists (AP_NINJA_LOG_FORCE).

Before the build, step 9 notes the size of <build>/.ninja_log (AP_RECOMP_PARALLEL: the time summary covers only what this
build appends). On Linux, PowerShell treats a file whose name starts with a dot as hidden: Test-Path finds it, but
Get-Item without -Force does not ("Could not find item .../.ninja_log"), so every build after the first one failed in
step 9. Windows is not affected (.ninja_log has no hidden attribute there). Now Get-Item uses -Force.

Touches ../tools/recomp_tsukihime.ps1 only (and its $Markers). Idempotent (marker AP_NINJA_LOG_FORCE), all or nothing,
keeps CRLF and BOM, --remove leaves the file byte for byte as it was. Independent of the other patchers (either order).
Apply: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_ninja_log_force.py
"""
from _patch import run

MARK = "AP_NINJA_LOG_FORCE"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECO,
     '''        $nOff = [int64]0; if (Test-Path -LiteralPath $nlog) { $nOff = (Get-Item -LiteralPath $nlog).Length }
''',
     '''        $nOff = [int64]0; if (Test-Path -LiteralPath $nlog) { $nOff = (Get-Item -LiteralPath $nlog -Force).Length }   # AP_NINJA_LOG_FORCE: a dot file is hidden on Linux
'''),
    (RECO,
     '''$Markers = @(
''',
     '''$Markers = @(
    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_NINJA_LOG_FORCE', 'apply_ninja_log_force.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)