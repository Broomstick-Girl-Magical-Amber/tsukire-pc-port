"""apply_linux_pack.py - the release zip carries the prebuilt Linux launcher (AP_LINUX_PACK).

tools\\dev\\pack_test_zip.ps1 zips what git would publish plus tsukihime_launcher.exe. This adds tools/linux/bin/ (the
binary and THIRD_PARTY/ licenses that tools/linux/package_gui.sh leaves there; git ignores them) when the folder exists, so
the Linux user gets a launcher that needs no compiler. Without the folder it only warns and the zip is as before.
The binary needs no hash or tag written anywhere: it only has to exist before pack_test_zip.ps1 runs.

Touches only ../tools/dev/pack_test_zip.ps1. Idempotent (marker AP_LINUX_PACK), all or nothing, keeps CRLF and BOM,
--remove leaves the file byte for byte as it was. Independent of the other patchers.
New files (copy them before applying): tools/linux/package_gui.sh.
Apply: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_linux_pack.py
"""
import os
import sys

from _patch import run

MARK = "AP_LINUX_PACK"
PACK = "../tools/dev/pack_test_zip.ps1"

EDITS = [
    (PACK,
     '''    foreach ($f in $files + @('tsukihime_launcher.exe')) {
''',
     '''    foreach ($f in $files + $extra) {
'''),
    (PACK,
     '''Push-Location $Root
try {
    [Console]::OutputEncoding''',
     '''# AP_LINUX_PACK (apply_linux_pack.py): the prebuilt Linux launcher of tools/linux/package_gui.sh travels too.
$extra = @('tsukihime_launcher.exe')
$linuxBin = Join-Path $Root 'tools\\linux\\bin'
if (Test-Path -LiteralPath (Join-Path $linuxBin 'tsukihime_launcher')) {
    $extra += @(Get-ChildItem -LiteralPath $linuxBin -Recurse -File | ForEach-Object { $_.FullName.Substring($Root.Length + 1) -replace '\\\\', '/' })
} else { Write-Warning 'tools\\linux\\bin\\tsukihime_launcher not found: the zip carries no prebuilt Linux launcher (run tools/linux/package_gui.sh in the Ubuntu 22.04 container).' }

Push-Location $Root
try {
    [Console]::OutputEncoding'''),
]

if __name__ == "__main__":
    if "--remove" not in sys.argv:
        g = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "linux", "package_gui.sh")
        if not os.path.isfile(g):
            sys.exit("ERROR: tools/linux/package_gui.sh is missing (copy it with this patcher). Nothing touched.")
    run(MARK, EDITS, __doc__)
