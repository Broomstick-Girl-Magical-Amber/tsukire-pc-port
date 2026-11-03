"""apply_clean_downloads.py - delete the installers kept in <root>\\_prepare once they did their job. Marker AP_CLEAN_DOWNLOADS.

install_requirements.ps1 keeps in <root>\\_prepare what it downloads, only to reuse it if a step is repeated:
- downloads\\vulkan_sdk-<version>.exe       the Vulkan SDK installer          (step 1)
- winget\\                                  App Installer + dependencies      (steps 1/2, only when winget was missing)
- downloads\\ryubing-a82350bb77.tar.gz      the Ryubing source tarball        (step 8)
None is needed after its step succeeded (the FFmpeg zip was already deleted by apply_ffmpeg_prebuilt.py). Now each one is
deleted when its step ends well, which frees a few hundred MB (estimated: the sizes were not measured one by one).
- Only paths inside <root>\\_prepare are ever deleted; any error while deleting is ignored (never fails a step).
- If a step is repeated later (for example the tarball is needed again), the file is simply downloaded again, with its hash checked.
- NX_KEEP_DOWNLOADS=1 keeps everything as before.
- Not touched: _prepare\\ffmpeg (the build uses it), _prepare\\triplets (vcpkg overlay), the logs.

Edits ../tools/install_requirements.ps1 only. Independent from the other patches (any order). Idempotent, all or nothing,
keeps CRLF/LF and BOM.
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_clean_downloads.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_CLEAN_DOWNLOADS"
INST = "../tools/install_requirements.ps1"

FUNC_OLD = "function IsAdmin {\n"
FUNC_NEW = r"""# AP_CLEAN_DOWNLOADS (apply_clean_downloads.py): an installer or archive kept in <root>\_prepare only to be reused is deleted once its
# step succeeded. Only inside $WorkDir; never fails the step; NX_KEEP_DOWNLOADS=1 keeps everything.
function CleanDownload([string]$p) {
    try {
        if ($env:NX_KEEP_DOWNLOADS -or -not $p -or -not (Test-Path -LiteralPath $p)) { return }
        $full = [IO.Path]::GetFullPath($p)
        $base = [IO.Path]::GetFullPath($WorkDir).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if (-not $full.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { return }
        $mb = [double](@(Get-ChildItem -LiteralPath $full -Recurse -File -Force -ErrorAction SilentlyContinue) | Measure-Object Length -Sum).Sum / 1MB
        Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
        Info ('removed {0} ({1:N0} MB)' -f (Split-Path $full -Leaf), $mb)
    } catch { }
}
function IsAdmin {
"""

VULKAN_OLD = """    if ($p.ExitCode -ne 0) { Fail "the Vulkan SDK installer finished with code $($p.ExitCode)" }\n"""
VULKAN_NEW = VULKAN_OLD + "    CleanDownload $exe   # AP_CLEAN_DOWNLOADS\n"

WINGET_OLD = """    Info "winget $((RunCapture 'winget' @('--version')).Output -join ' ') installed"\n"""
WINGET_NEW = WINGET_OLD + "    CleanDownload (PathOf $WorkDir 'winget')   # AP_CLEAN_DOWNLOADS: the package is installed, its files are not needed\n"

RYU_OLD = """            Info "ryubing extracted into $ry ($RyubingFiles files, verified tree)"\n"""
RYU_NEW = RYU_OLD + "            CleanDownload $tgz   # AP_CLEAN_DOWNLOADS\n"

EDITS = [
    (INST, FUNC_OLD, FUNC_NEW),
    (INST, VULKAN_OLD, VULKAN_NEW),
    (INST, WINGET_OLD, WINGET_NEW),
    (INST, RYU_OLD, RYU_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)