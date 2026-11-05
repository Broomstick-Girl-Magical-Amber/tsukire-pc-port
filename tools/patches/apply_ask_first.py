"""apply_ask_first.py - the install asks everything right after step 0, before it installs anything (AP_ASK_FIRST).

Before, install_requirements.ps1 stopped in the middle to ask: on Windows (xwin) the Microsoft license of the CRT/SDK in
step 1; on Linux the sudo of the system programs in step 1 and the same license in step 2, after the apt install. Now
AskFirst runs right after step 0 (and the input check) and asks both there:
- the Microsoft license of the Windows CRT/SDK that xwin downloads, only when it is not downloaded yet. -AcceptLicense
  accepts it on the command line (no question; also with -NonInteractive). Saying no stops before anything is installed.
- Linux: the system programs step 1 installs with sudo (same question as step 1), and "sudo -v" right away, so the
  password is typed now too. Step 1 then does not ask again.
The questions of steps 1 and 2 stay as they were for runs that start later (-From 1 or 2 skips nothing: AskFirst runs
only when step 0 runs, which is always outside the vcpkg worker).

Touches ../tools/install_requirements.ps1, ../tools/linux/install_requirements_linux.ps1 and
../tools/linux/install_requirements_linux.sh (comment). Idempotent (marker AP_ASK_FIRST), all or nothing, keeps CRLF
and BOM, --remove leaves the files byte for byte as they were.
Apply: cd <root>/framework; python ../tools/patches/apply_ask_first.py  (the venv python: private\\venv\\Scripts\\python
on Windows, private/venv/bin/python on Linux)
"""
from _patch import run

MARK = "AP_ASK_FIRST"
INST = "../tools/install_requirements.ps1"
LNX = "../tools/linux/install_requirements_linux.ps1"
SH = "../tools/linux/install_requirements_linux.sh"

FUNC = r'''# AP_ASK_FIRST: every question of the install, right after step 0, so the run does not stop later to ask: the
# Microsoft license of the Windows CRT/SDK (xwin; -AcceptLicense accepts it on the command line) and, on Linux, the
# sudo of step 1 (system programs), whose password is cached now with "sudo -v".
$script:SudoAsked = $false
function AskFirst {
    if ($Check -or $To -lt 1) { return }
    $asked = $false
    $crt = ($OnLinux -or $script:UseXwin) -and [bool](Get-Command XwinOk -ErrorAction SilentlyContinue)
    if ($crt -and $env:XWIN_ACCEPT_LICENSE -ne 'true' -and -not (XwinOk)) {
        Write-Host ''
        Info 'xwin downloads the MSVC CRT/STL and the Windows SDK from Microsoft. Their license:'
        Info '  https://go.microsoft.com/fwlink/?LinkId=2086102'
        if ($AcceptLicense) { Info 'accepted with -AcceptLicense'; $env:XWIN_ACCEPT_LICENSE = 'true' }
        elseif (-not $NonInteractive) {
            $asked = $true
            if (-not (Ask 'Do you accept the Microsoft license of the CRT/SDK (the same one Visual Studio asks for)?')) {
                Fail "license not accepted: without the CRT/SDK the game cannot be built (nothing was installed)$(if (-not $OnLinux) { '; -Toolchain vs uses Visual Studio instead' })"
            }
            $env:XWIN_ACCEPT_LICENSE = 'true'   # only for this run and the processes it starts
        }
    }
    if ($OnLinux -and $From -le 1 -and -not $NonInteractive -and [bool](Get-Command MissingSystem -ErrorAction SilentlyContinue)) {
        $f = @(MissingSystem)
        $g = PackageManager
        if ($f.Count -and $g) {
            $pkgs = @($f | ForEach-Object { $SystemPkgs[$_][$g] } | Sort-Object -Unique)
            $asked = $true
            if (-not (Ask "Step 1 installs with sudo: $($pkgs -join ' '). Go ahead?")) { Fail "$($f -join ', ') are missing: install them with: sudo $((SystemCommand $g $pkgs) -join ' ')" }
            & sudo -v
            if ($LASTEXITCODE -ne 0) { Fail 'sudo did not accept the password' }
            $script:SudoAsked = $true
        }
    }
    if ($asked) { Info 'no more questions: the rest runs on its own' }
}

'''

EDITS = [
    (INST,
     '''    [switch]$TrimXci,   # AP_ROMFS_DIRECT: passed to recomp_tsukihime.ps1 (-Recomp): trim the 0xFF padding of the .xci
''',
     '''    [switch]$TrimXci,   # AP_ROMFS_DIRECT: passed to recomp_tsukihime.ps1 (-Recomp): trim the 0xFF padding of the .xci
    [switch]$AcceptLicense,   # AP_ASK_FIRST: accept the Microsoft license of the Windows CRT/SDK (xwin) without asking
'''),
    (INST,
     '''function Main {
''',
     FUNC + '''function Main {
'''),
    (INST,
     '''        if (-not $Worker) { TimedStep 0 $StepNames[0] 'Step0'; CheckInputs }
''',
     '''        if (-not $Worker) { TimedStep 0 $StepNames[0] 'Step0'; CheckInputs; AskFirst }   # AP_ASK_FIRST
'''),
    (LNX,
     '''    if ($g -eq 'apt' -and -not (Ask "Update the package list and install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: sudo apt-get update; $txt" }
    elseif ($g -ne 'apt' -and -not (Ask "Install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: $txt" }
''',
     '''    if ($script:SudoAsked) { }   # AP_ASK_FIRST: already answered (and sudo -v done) right after step 0
    elseif ($g -eq 'apt' -and -not (Ask "Update the package list and install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: sudo apt-get update; $txt" }
    elseif ($g -ne 'apt' -and -not (Ask "Install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: $txt" }
'''),
    (SH,
     '''# The only thing that may ask for sudo is step 1 (system programs), and it asks first. You accept the Microsoft license
''',
     '''# AP_ASK_FIRST: both questions (sudo for the system programs of step 1, and the Microsoft license of the Windows
# CRT/SDK) are asked right after step 0, before anything is installed; -AcceptLicense accepts the license up front.
# The only thing that may ask for sudo is step 1 (system programs), and it asks first. You accept the Microsoft license
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)