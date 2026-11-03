"""apply_quickedit_off.py - a click in the console no longer pauses install_requirements.ps1. Marker AP_QUICKEDIT.

Problem: in a Windows console with "QuickEdit mode" on (the default of conhost), a click on the window starts a text
selection and FREEZES every program that writes to that console (winget, vcpkg, git, dotnet...) until Esc or Enter is
pressed. The step looks stuck and its time includes the wait (a .NET SDK install that takes 1 minute was measured as 6:49).

Fix: while install_requirements.ps1 runs (also the elevated window of step 1, and the recomp_tsukihime.ps1 that
`-Recomp` starts: it shares the console), QuickEdit is switched off for the console input buffer (SetConsoleMode, no
administrator needed); the original mode is restored when the script ends, also if it fails. Text can still be copied
after the run, or with Alt+Space > Edit > Mark. If the input is not a console (redirected, ISE, Linux) nothing changes.
recomp_tsukihime.ps1 run on its own is NOT changed (use install_requirements.ps1 -Recomp, or avoid clicking).

Edits ../tools/install_requirements.ps1 only (the end of the file, so it does not clash with apply_install_flow.py,
apply_ffmpeg_prebuilt.py or apply_tool_times.py, in any order). Idempotent, all or nothing, keeps CRLF/LF and BOM.
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_quickedit_off.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_QUICKEDIT"
INST = "../tools/install_requirements.ps1"

TAIL_OLD = """if ($FunctionsOnly) { return }
$c = Main
exit $c"""   # the file ends here, without a final line break
TAIL_NEW = r"""# AP_QUICKEDIT (apply_quickedit_off.py): QuickEdit off while the script runs, so a click in the console does not pause it.
# DisableQuickEdit returns the previous console input mode (or $null if there is no console / not Windows).
function DisableQuickEdit {
    if (-not $OnWindows) { return $null }
    try {
        if (-not ('NxConsoleMode' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NxConsoleMode {
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll")] static extern bool GetConsoleMode(IntPtr h, out uint m);
    [DllImport("kernel32.dll")] static extern bool SetConsoleMode(IntPtr h, uint m);
    public static long Get() { uint m; return GetConsoleMode(GetStdHandle(-10), out m) ? (long)m : -1; }
    public static bool Set(long m) { return m >= 0 && SetConsoleMode(GetStdHandle(-10), (uint)m); }
}
'@
        }
        $old = [NxConsoleMode]::Get()
        if ($old -lt 0) { return $null }
        if (($old -band 0x40) -eq 0) { return $null }   # QuickEdit was already off: nothing to change or restore
        # ENABLE_QUICK_EDIT_MODE = 0x40; the change only applies together with ENABLE_EXTENDED_FLAGS = 0x80
        if (-not [NxConsoleMode]::Set((($old -band (-bnot 0x40)) -bor 0x80))) { return $null }
        Write-Host '   (console QuickEdit is off while this runs: a click no longer pauses the output)'
        return $old
    } catch { return $null }
}
function RestoreConsoleMode($old) {
    if ($null -eq $old) { return }
    try { [void][NxConsoleMode]::Set(([long]$old -bor 0x80)) } catch { }
}

if ($FunctionsOnly) { return }
$nxConsoleMode = DisableQuickEdit
try { $c = Main } finally { RestoreConsoleMode $nxConsoleMode }
exit $c"""

EDITS = [
    (INST, TAIL_OLD, TAIL_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
