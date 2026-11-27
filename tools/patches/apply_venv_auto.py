"""apply_venv_auto.py - recomp_tsukihime.ps1 installs lz4 by itself when the venv lacks it (AP_VENV_AUTO).

Step 0 of recomp_tsukihime.ps1 tries "import lz4.block" with the project python (framework/private/venv) because
nso2bin.py needs it, and stopped with "the project python does not have lz4" if that failed. That happens when the
venv exists but its packages were never installed (an interrupted install, or a run that skipped step 6 of
install_requirements.ps1). Recompiling alone could not fix it, on Windows or on Linux (tools/linux/recomp_linux.sh runs
this same script), and the message gave no way out.

Now, when lz4 is missing, step 0 runs "python -m pip install -r tools/requirements.txt" in that venv (the same exact
versions as step 6 of install_requirements.ps1; no administrator, no sudo), checks again and goes on. If pip fails (no
network, for example), it stops with the exact command to run by hand. Nothing changes when lz4 is already there.

Touches ../tools/recomp_tsukihime.ps1 (the check of step 0 and one $Markers row, anchored after the row of
apply_linux_install.py, which no other patcher uses as an anchor). Idempotent (marker AP_VENV_AUTO), all or nothing,
keeps CRLF and BOM, --remove leaves the file byte for byte as it was. Independent of the other patchers.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_venv_auto.py
"""
from _patch import run

MARK = "AP_VENV_AUTO"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECO,
     '''    $r = RunCapture $Python @('-c', 'import lz4.block')
    if ($r.Code -ne 0) { Fail "the project python does not have lz4 (nso2bin.py needs it)" }
''',
     '''    $r = RunCapture $Python @('-c', 'import lz4.block')
    if ($r.Code -ne 0) {   # AP_VENV_AUTO: the venv exists but its packages are not installed: install them (same as step 6 of install_requirements)
        $reqTxt = Under $Tools 'requirements.txt'
        if (-not (Test-Path -LiteralPath $reqTxt)) { Fail "the project python does not have lz4 (nso2bin.py needs it) and $reqTxt is missing (it comes with the repo)" }
        Warn 'the project python does not have lz4 (nso2bin.py needs it): installing tools\\requirements.txt in its venv (pip; no administrator needed)'
        $pip = RunCapture $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $reqTxt)
        foreach ($l in $pip.Output) { Info "  $l" }
        $r = RunCapture $Python @('-c', 'import lz4.block')
        if ($r.Code -ne 0) { Fail "the project python does not have lz4 (nso2bin.py needs it) and pip could not install it (code $($pip.Code); network?). By hand: `"$Python`" -m pip install -r `"$reqTxt`"" }
        Info 'lz4 installed in the venv'
    }
'''),
    (RECO,
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_LINUX_INSTALL', 'apply_linux_install.py', $false),
''',
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_LINUX_INSTALL', 'apply_linux_install.py', $false),
    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_VENV_AUTO', 'apply_venv_auto.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
