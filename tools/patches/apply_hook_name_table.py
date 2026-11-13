"""apply_hook_name_table.py - native hook for main+0x56720 (Tsukihime, japan edition): name lookup in a table.

Why: after apply_recomp_hooks.py the title screen stall was 689 ms, and most of it was the sdk memcmp called about
2.4 million times through the PLT (main -> nx::call -> stub -> hook) by one function, main+0x56720, which compares a
file name against every 32-byte entry of a table. The hook does that loop natively
(games/tsukihime_common/hle_hooks_nombres_tsuki.cpp, new file of the repo; NX_TSUKI_NAMES = 0 or check at run time).

Needs apply_recomp_hooks.py (checked). Idempotent (marker AP_HOOK_NAME_TABLE), all or nothing, keeps CRLF and BOM,
--remove leaves the files byte for byte; it can be removed before or after apply_recomp_hooks.py.
After (from <root>): .\\tools\\recomp_tsukihime.ps1 -Edition japan -From 6 -Replace

Usage (in <root>\\framework):
  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_hook_name_table.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import FRAMEWORK_ROOT, run  # noqa: E402

MARK = "AP_HOOK_NAME_TABLE"
REGEN = "../tools/internal/regen_tsuki.ps1"
RECOMP_PS = "../tools/recomp_tsukihime.ps1"
HOOK = "main:tsuki_name_table:0x56720,0xa88,0xd10143ff"

REGEN_OLD = ("        if (-not $Bases.ContainsKey($m)) { throw \"unknown module: $m (known: $($Bases.Keys -join ', '))\" }\n")
REGEN_NEW = REGEN_OLD + (
    "        if (\"$env:NX_RECOMP_HOOKS\" -notlike '*:tsuki_name_table:*') "
    "{ $env:NX_RECOMP_HOOKS = \"$env:NX_RECOMP_HOOKS;%s\" }  # AP_HOOK_NAME_TABLE\n" % HOOK)

PS_OLD = ("    @('both', 'framework\\runtime\\src\\kernel.cpp', 'CancelSynchronization that arrived before', "
          "'apply_cancel_sync.py', $false),\n")
PS_NEW = PS_OLD + (
    "    @('japan', 'tools\\internal\\regen_tsuki.ps1', 'AP_HOOK_NAME_TABLE', 'apply_hook_name_table.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_hooks_nombres_tsuki.cpp', 'AP_HOOK_NAME_TABLE', "
    "'hle_hooks_nombres_tsuki.cpp (in the repo)', $false),\n")

EDITS = [
    (REGEN, REGEN_OLD, REGEN_NEW),
    (RECOMP_PS, PS_OLD, PS_NEW),
]


def check_base():
    """Refuses to apply without apply_recomp_hooks.py (same root resolution as _patch.py, including --root)."""
    if "--remove" in sys.argv[1:]:
        return
    root = FRAMEWORK_ROOT
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]
        elif a.startswith("--root="):
            root = a.split("=", 1)[1]
    regen = os.path.normpath(os.path.join(root, REGEN))
    try:
        text = open(regen, "rb").read().decode("utf-8-sig", "replace")
    except OSError:
        return  # run() reports the missing file
    if "AP_RECOMP_HOOKS" not in text and MARK not in text:
        sys.exit("regen_tsuki.ps1 without apply_recomp_hooks.py: apply it first")


if __name__ == "__main__":
    check_base()
    run(MARK, EDITS, __doc__)