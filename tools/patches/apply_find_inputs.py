"""apply_find_inputs.py - recomp_tsukihime.ps1 with two or more dumps in the root (AP_FIND_INPUTS).

FindInputs returned its list with "return , $ok" and every caller wraps it again in @(...): the result was ONE item
holding the whole list, so with two dumps in the root (tsukihime.xci + tsukihimeGlobal.nsp) step 0 asked .NET to open
the path "<root>\\tsukihime.xci <root>\\tsukihimeGlobal.nsp" ("The given path's format is not supported" / "No se admite
el formato proporcionado de la ruta de acceso"). With a single dump it worked by chance (an array of one item turned into
a string is that item). It only showed when the input has to be read (step 0-1 without a finished step 2): before
AP_ROMFS_DIRECT both editions were already extracted, so that branch never ran. Now FindInputs returns its items and
the @(...) of the callers makes the list.

Touches ../tools/recomp_tsukihime.ps1 only (and its $Markers). Idempotent (marker AP_FIND_INPUTS), all or nothing,
keeps CRLF and BOM, --remove leaves the file byte for byte as it was. Independent of apply_romfs_direct.py (either
order). Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_find_inputs.py
"""
from _patch import run

MARK = "AP_FIND_INPUTS"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECO,
     '''        return , $e
''',
     '''        return $e   # AP_FIND_INPUTS: was "return , $e" (the callers wrap it in @(...))
'''),
    (RECO,
     '''    return , $ok
''',
     '''    return $ok   # AP_FIND_INPUTS: was "return , $ok": @(FindInputs) got one item with every dump joined in one path
'''),
    (RECO,
     '''    @('japan', 'framework\\games\\tsukihime\\game.cmake', 'tsukihime_common/tsukihime_common.cmake',''',
     '''    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_FIND_INPUTS', 'apply_find_inputs.py', $false),
    @('japan', 'framework\\games\\tsukihime\\game.cmake', 'tsukihime_common/tsukihime_common.cmake','''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)