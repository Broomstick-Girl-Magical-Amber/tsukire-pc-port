"""apply_no_format_pref.py - recomp_tsukihime.ps1 no longer links a dump format to an edition (AP_NO_FORMAT_PREF).

FindInputs sorted the .xci/.nsp of the root by "the usual extension of the edition" (global: .nsp, japan: .xci) before
trying them. Both formats work for both editions (the edition is decided by the program ID inside the dump, step 1), so
the format says nothing and the preference is gone: the dumps of the root are tried in name order and the one of the
requested edition is kept, as before. Nothing else changes: the same dumps are found, the same one is used.

Touches ../tools/recomp_tsukihime.ps1 (the comment and the sort of FindInputs, and one $Markers row, anchored after the
row of apply_venv_auto.py, which no other patcher uses as an anchor). Idempotent (marker AP_NO_FORMAT_PREF), all or
nothing, keeps CRLF and BOM, --remove leaves the file byte for byte as it was.
Needs apply_venv_auto.py applied first (the $Markers row is anchored on its row).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_no_format_pref.py
"""
from _patch import run

MARK = "AP_NO_FORMAT_PREF"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECO,
     '''# order and the one of the requested edition is kept (step 1 compares the program ID); first the usual extension of
# the edition (global: .nsp, japan: .xci) to avoid reading too much, but any of them works for either.
''',
     '''# name order and the one of the requested edition is kept (step 1 compares the program ID). The format says nothing
# about the edition (AP_NO_FORMAT_PREF): an .xci or an .nsp works for either one.
'''),
    (RECO,
     '''    $pref = if ($Edition -eq 'global') { '.nsp' } else { '.xci' }
    $all = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } |
             Sort-Object @{ Expression = { if ($_.Extension -eq $pref) { 0 } else { 1 } } }, Name)
''',
     '''    $all = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } | Sort-Object Name)
'''),
    (RECO,
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_VENV_AUTO', 'apply_venv_auto.py', $false),
''',
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_VENV_AUTO', 'apply_venv_auto.py', $false),
    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_NO_FORMAT_PREF', 'apply_no_format_pref.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
