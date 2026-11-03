r"""apply_xwin_runtime.py - Visual C++ runtime next to the exe in the xwin flow. Marker AP_XWIN_RUNTIME.

Measured 2026-11-02 (first xwin run, clean Windows 10 VM): step 1 failed in the crt chain, "xwin (CRT/SDK) failed (code
-1073741515)" = 0xC0000135, DLL not found: xwin.exe (Rust, msvc target) imports VCRUNTIME140.dll and a Windows without
Visual C++ does not have it (VS Build Tools used to install it). Checked the imports of every exe of the flow: LLVM 23.1.3
(clang-cl, lld-link, llvm-rc, llvm-mt, llvm-lib), CMake 4.4.4 and Ninja 1.13.2 do not use it; xwin does, and so do the
game itself and the vcpkg DLLs (/MD: msvcp140.dll, vcruntime140.dll, vcruntime140_1.dll). Python and its wheels bring
their own (numpy bundles msvcp140).
- tools\internal\install_requirements_xwin.ps1 (new version, delivered with this patch): xwin.exe gets the
  vcruntime140.dll of the Python of the flow; step 5 says whether the pack brings the runtime.
- tools\dev\build_prebuilt.ps1 (new version): the deps pack brings redist\ (the Visual C++ runtime of VS\VC\Redist, or of
  System32) and a copy for spirv-val; MANIFEST without the PC name.
- This patch, recomp_tsukihime.ps1 step 9 (xwin flow): copies <pack>\redist\*.dll next to the exe after the build (only
  what changed); without them in the pack and without msvcp140.dll in System32, a warning with what to do.
Not tested on Windows by Claude: parser, ASCII, apply/remove, the copy with stubs in pwsh 7 on Linux.

Run: cd <root>\framework; private\venv\Scripts\python ..\tools\patches\apply_xwin_runtime.py [--remove]
Needs AP_TOOLCHAIN_XWIN (apply_toolchain_xwin.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_XWIN_RUNTIME"
RECOMP = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECOMP,
     r"""function ToolCmd([string]$p) { if ($p -like '*.exe') { return , @($p) } else { return , @('dotnet', $p) } }
""",
     r"""function ToolCmd([string]$p) { if ($p -like '*.exe') { return , @($p) } else { return , @('dotnet', $p) } }
# AP_XWIN_RUNTIME: the game and the vcpkg DLLs use the Visual C++ runtime (/MD). VS Build Tools installs it in Windows; the
# xwin flow does not, so the copy of the prebuilt pack (redist\) goes next to the exe (app-local, as Microsoft allows).
function CopyVcRuntime([string]$dir) {
    $rd = if ($Toolchain.PSObject.Properties['redist']) { "$($Toolchain.redist)" } else { '' }
    if ($rd -and (Test-Path -LiteralPath (Join-Path $rd 'msvcp140.dll'))) {
        $n = 0
        foreach ($f in @(Get-ChildItem -LiteralPath $rd -Filter '*.dll' -File)) {
            $d = Join-Path $dir $f.Name
            $same = $false
            if (Test-Path -LiteralPath $d) { $g = Get-Item -LiteralPath $d; $same = ($g.Length -eq $f.Length -and $g.LastWriteTimeUtc -eq $f.LastWriteTimeUtc) }
            if (-not $same) { Copy-Item -LiteralPath $f.FullName -Destination $d -Force; $n++ }
        }
        Info "Visual C++ runtime next to the exe ($rd): $n DLL(s) copied"
    } elseif (-not (Test-Path -LiteralPath (Join-Path $env:SystemRoot 'System32\msvcp140.dll'))) {
        Warn 'the game needs the Visual C++ runtime (msvcp140.dll, vcruntime140.dll) and neither Windows nor the prebuilt pack has it: install vc_redist.x64.exe from Microsoft, or run tools\install_requirements.ps1 -From 1 with a pack made by the current tools\dev\build_prebuilt.ps1'
    }
}
"""),
    (RECOMP,
     r"""        BuildTimes $nlog $nOff
""",
     r"""        BuildTimes $nlog $nOff
        if ($Toolchain) { CopyVcRuntime $Build }   # AP_XWIN_RUNTIME
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
