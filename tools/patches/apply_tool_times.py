"""apply_tool_times.py - time of each tool installed by steps 1 and 2 of install_requirements.ps1. Marker AP_TOOL_TIMES.

Step 1 installs VS Build Tools, LLVM, CMake, Git, .NET SDK and the Vulkan SDK one after the other in the elevated
window and prints one time for all of them. This patch measures each tool (it only measures: it changes nothing else):
- the elevated window prints "(took hh:mm:ss.f)" after every tool and saves the seconds in the result JSON;
- the original window repeats it as "<tool>: installed (took hh:mm:ss.f)";
- when no elevation is needed (already administrator, or step 2) every tool prints its time too.
Use it to decide what is worth optimizing in step 1 before changing how things are installed.

Edits ../tools/install_requirements.ps1 only. Idempotent, all or nothing, keeps CRLF/LF and BOM.
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_tool_times.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_TOOL_TIMES"
INST = "../tools/install_requirements.ps1"

ADMIN_OLD = """        try { InstallTool $c; $r[$c] = 'ok' } catch { $r[$c] = "error: $($_.Exception.Message)"; Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red }
"""
ADMIN_NEW = """        $sw = [Diagnostics.Stopwatch]::StartNew()   # AP_TOOL_TIMES
        try { InstallTool $c; $r[$c] = 'ok' } catch { $r[$c] = "error: $($_.Exception.Message)"; Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red }
        $sw.Stop()
        $r["${c}_seconds"] = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
        Write-Host ('   (took {0})' -f $sw.Elapsed.ToString('hh\\:mm\\:ss\\.f'))
"""

ELEV_OLD = """        if ($v -eq 'ok') { Info "$($ToolSpecs[$c].Name): installed" } else { Warn "$($ToolSpecs[$c].Name): $v" }
"""
ELEV_NEW = """        $tp = $j.PSObject.Properties["${c}_seconds"]   # AP_TOOL_TIMES
        $took = if ($tp) { ' (took ' + ([TimeSpan]::FromSeconds([double]$tp.Value)).ToString('hh\\:mm\\:ss\\.f') + ')' } else { '' }
        if ($v -eq 'ok') { Info "$($ToolSpecs[$c].Name): installed$took" } else { Warn "$($ToolSpecs[$c].Name): $v$took" }
"""

LOOP_OLD = """    else { foreach ($c in $install) { Write-Host "   -- $($ToolSpecs[$c].Name)"; InstallTool $c } }
"""
LOOP_NEW = """    else {
        foreach ($c in $install) {
            Write-Host "   -- $($ToolSpecs[$c].Name)"
            $sw = [Diagnostics.Stopwatch]::StartNew()   # AP_TOOL_TIMES
            try { InstallTool $c } finally { $sw.Stop(); Info ('(took {0})' -f $sw.Elapsed.ToString('hh\\:mm\\:ss\\.f')) }
        }
    }
"""

EDITS = [
    (INST, ADMIN_OLD, ADMIN_NEW),
    (INST, ELEV_OLD, ELEV_NEW),
    (INST, LOOP_OLD, LOOP_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)