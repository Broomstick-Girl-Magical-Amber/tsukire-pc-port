"""apply_jt_outside.py - recomp: jump table cases that fall outside the function (Tsukihime global sdk).

Cause (confirmed with the disassembly of sdk 17.5.4 and reading recomp/analysis.py and recomp/emit.py):
  sdk+0x5c514c..0x5c5164 is a clang switch with byte anchors (no "lsl #2" in the add):
      adrp x12, #0x9a0000 / add x12, x12, #0x71c
      adr  x10, #0x5c5158
      ldrsw x11, [x12, x9, lsl #2]
      add  x10, x10, x11
      br   x10
  1. analysis._looks_like_code_pointer() only discards the adr of a switch if it is followed by an "add ..., lsl #2";
     here the add has no shift, so 0x5c5158 stays as a function entry (sdk_005C5158, a "shared tail" up to the end
     of the FDE).
  2. an.jump_tables is a dict by address of the br, shared by all functions: the list computed by the big function
     that contains the switch stays (sdk_005C5158 does not find the table from its own start and does not overwrite
     it). That list includes 0x5c5114, which is BEFORE 0x5c5158.
  3. emit.i_br() emits "goto L_<t>" for each case without checking whether t is inside the function -> in sdk_005C5158
     there is no L_5C5114 -> "use of undeclared label 'L_5C5114'" when compiling code_124.cpp.

Fix (only with NX_RECOMP_JT_FUERA=1 when running the recompiler): in i_br, each case goes through goto_or_tail(),
the same that b / b.cond / cbz / tbz already use: goto if the target is in the function; otherwise, a tail call to the
function that starts there, or nx::call. For the cases inside the function the emitted text is identical to
before; only the outside cases change, which today produce code that does not compile. Without the variable, the
emitter is the usual one (Wonder and the japan edition generate exactly the same).

It also adds NX_RECOMP_JT_FUERA=1 (and the check that the patch is there) to <root>\\tools\\internal\\regen_tsuki_global.ps1.
The japan one (regen_tsuki.ps1) is not touched.

Usage:  python apply_jt_outside.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_JT_FUERA), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found.
After: regen_tsuki_global.ps1 sdk  and  cmake --build build\\tsukihime_global
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_JT_FUERA"
EMIT = "recomp/emit.py"
REGEN = "../tools/internal/regen_tsuki_global.ps1"

EDITS = [
    (EMIT,
     "SYSREG_NZCV = 0xDA10\n",
     "SYSREG_NZCV = 0xDA10\n"
     "\n"
     "import os as _os  # noqa: E402\n"
     "\n"
     "# AP_JT_FUERA: off unless NX_RECOMP_JT_FUERA=1 when the recompiler runs (per game: Wonder's output stays the\n"
     "# same). Jump-table cases outside the function become tail calls (goto_or_tail) instead of gotos to labels\n"
     "# that do not exist (Tsukihime global sdk+0x5c5164: an adr anchor became an entry, a case lands before it).\n"
     "_JT_FUERA = _os.environ.get(\"NX_RECOMP_JT_FUERA\") == \"1\"\n"),
    (EMIT,
     "        if i.address in jt:\n"
     "            cases = \" \".join(f\"case {B} + {t:#x}ull: goto L_{t:X};\" for t in jt[i.address])\n"
     "            for t in jt[i.address]:\n"
     "                self.labels.add(t)\n",
     "        if i.address in jt and _JT_FUERA:  # AP_JT_FUERA\n"
     "            cases = \" \".join(f\"case {B} + {t:#x}ull: {self.goto_or_tail(f, t)}\" for t in jt[i.address])\n"
     "            return f\"switch ({r.r()}) {{ {cases} default: nx::call(c, {r.r()}); return; }}\"\n"
     "        if i.address in jt:\n"
     "            cases = \" \".join(f\"case {B} + {t:#x}ull: goto L_{t:X};\" for t in jt[i.address])\n"
     "            for t in jt[i.address]:\n"
     "                self.labels.add(t)\n"),
    (REGEN,
     "    throw 'recomp\\analysis.py without apply_jt_lsr.py version 2: apply it first'\n"
     "}\n",
     "    throw 'recomp\\analysis.py without apply_jt_lsr.py version 2: apply it first'\n"
     "}\n"
     "# AP_JT_FUERA: jump table cases outside the function (sdk+0x5c5164 of SDK 17.5.4)\n"
     "if (-not (Select-String -Path (Join-Path $Framework 'recomp\\emit.py') -Pattern '_JT_FUERA = ' -Quiet)) {\n"
     "    throw 'recomp\\emit.py without apply_jt_outside.py: apply it first'\n"
     "}\n"),
    (REGEN,
     "$env:NX_RECOMP_JT_LSR = '1'\n",
     "$env:NX_RECOMP_JT_LSR = '1'\n"
     "$prevFuera = $env:NX_RECOMP_JT_FUERA  # AP_JT_FUERA\n"
     "$env:NX_RECOMP_JT_FUERA = '1'\n"),
    (REGEN,
     "    if ($null -eq $prev) { Remove-Item Env:NX_RECOMP_JT_LSR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_LSR = $prev }\n",
     "    if ($null -eq $prev) { Remove-Item Env:NX_RECOMP_JT_LSR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_LSR = $prev }\n"
     "    if ($null -eq $prevFuera) { Remove-Item Env:NX_RECOMP_JT_FUERA -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_FUERA = $prevFuera }  # AP_JT_FUERA\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)