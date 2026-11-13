"""apply_install_yes.py - install_requirements.ps1 -AssumeYes (AP_INSTALL_YES): answer yes to its questions.

install_requirements.ps1 asks before it changes something that is already on the machine and does not match the
project (Ask): a tool whose version breaks the rule (installs the pinned one, which may replace it), the vcpkg in
another commit (moves it), a venv of another Python (kept as venv.old, a new one is created), a ryubing that is not
the reference tree (kept as ryubing.old, the Codeberg one is downloaded), and on Linux the sudo of step 1. With
-NonInteractive it answers no and fails; without it, it waits for a typed y. The Windows launcher runs it hidden, so
it needs a third way: -AssumeYes answers yes to all of them ("Info <question> -> yes (-AssumeYes)" in the log).
"""
from _patch import run

MARK = "AP_INSTALL_YES"
INST = "../tools/install_requirements.ps1"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (INST,
     '''    [switch]$NonInteractive,
    [switch]$CrossRecomp,
''',
     '''    [switch]$NonInteractive,
    [switch]$AssumeYes,   # AP_INSTALL_YES: answer yes to every question (replace/move what does not match), except the Microsoft license
    [switch]$CrossRecomp,
'''),
    (INST,
     '''function Ask([string]$t) {
    if ($NonInteractive) { Info "$t -> no (-NonInteractive)"; return $false }
''',
     '''function Ask([string]$t) {
    # AP_INSTALL_YES: -AssumeYes says yes to everything but the Microsoft license (only -AcceptLicense or a typed y accept that)
    if ($AssumeYes -and $t -notmatch '^Do you accept the Microsoft license') { Info "$t -> yes (-AssumeYes)"; return $true }
    if ($NonInteractive) { Info "$t -> no (-NonInteractive)"; return $false }
'''),
    (RECO,
     '''    @('both', 'tools\\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
''',
     '''    @('both', 'tools\\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
    @('both', 'tools\\install_requirements.ps1', 'AP_INSTALL_YES', 'apply_install_yes.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
