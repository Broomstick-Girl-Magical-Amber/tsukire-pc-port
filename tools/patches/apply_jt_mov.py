"""apply_jt_mov.py - recomp: jump table bound kept in a register (crash main+9708c of the global).

Cause (confirmed with diag_ciel.py and reading recomp/analysis.py):
  main+97000 (called from main+95248) is a big clang switch:
      mov  w9, #-0x2758
      mov  w10, #0x3725        ; the bound goes in a register
      add  w9, w8, w9          ; index
      cmp  w9, w10             ; register-register cmp
      b.hi ...
      adrp x8, #0x248000 / add x8, x8, #0x798
      adr  x10, #0x97044
      ldrh w11, [x8, x9, lsl #1]
      add  x10, x10, x11, lsl #2
      br   x10
  _bound() only recognizes "cmp <index>, #N" (and, with AP_JT_LSR, "lsr" + "cmp #N"). With "cmp w9, w10" the bound
  stays None and entries() reads at most 256 entries of 0x3726. The crash (Ciel route, day 15, after
  phasetitle_15_01a_en.mp4) had index 0x2ec8, entry 0x12 -> main+9708c, which did not end up as a "case" of the
  emitted switch -> default: nx::call -> "call into the middle of a function".

Active only with NX_RECOMP_JT_MOV=1 when running the recompiler (per game: without the variable the output is the
usual one; Wonder and the japan edition do not change). It also adds that variable (and the check that the patch is
there) to <root>\\tools\\internal\\regen_tsuki_global.ps1. The japan one (regen_tsuki.ps1) is not touched.

Fix: in _bound(), a "cmp <index>, wT" whose wT is the result of "mov wT, #N" (the last write of wT,
at most 6 instructions before the cmp) gives the bound N + 1 with b.hi, N with b.hs. It only changes tables that
today have no bound (measure with medir_jt_mov.py).

Usage:  python apply_jt_mov.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_JT_MOV), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found.
After: regen_tsuki_global.ps1 main  and  cmake --build build\\tsukihime_global
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_JT_MOV"
ANALYSIS = "recomp/analysis.py"
REGEN = "../tools/internal/regen_tsuki_global.ps1"

EDITS = [
    (ANALYSIS,
     "def _bound(an, f, before, idx):\n",
     "import os as _os_jt_mov  # noqa: E402\n"
     "\n"
     "# AP_JT_MOV: off unless NX_RECOMP_JT_MOV=1 when the recompiler runs (per game: Wonder's output stays the same).\n"
     "_JT_MOV = _os_jt_mov.environ.get(\"NX_RECOMP_JT_MOV\") == \"1\"\n"
     "\n"
     "\n"
     "def _mov_bound(an, f, cmp_at, reg):\n"
     "    \"\"\"AP_JT_MOV: N if the last write of `reg` before cmp_at is `mov reg, #N` at most 6 instructions earlier,\n"
     "    else None.\"\"\"\n"
     "    if not _JT_MOV or not isinstance(reg, int):\n"
     "        return None\n"
     "    d = _find_def(an, f, cmp_at, reg)\n"
     "    if not d or d[0] < cmp_at - 4 * 6:\n"
     "        return None\n"
     "    _, mn, ops = d\n"
     "    if mn == \"mov\" and len(ops) == 2 and ops[1].startswith(\"#\"):\n"
     "        n = _imm(ops[1][1:])\n"
     "        return n if n >= 0 else None\n"
     "    return None\n"
     "\n"
     "\n"
     "def _bound(an, f, before, idx):\n"),
    (ANALYSIS,
     "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) in regs and ops[1].startswith(\"#\"):\n",
     "        # AP_JT_MOV: the bound in a register: mov wT, #N / cmp wIdx, wT / b.hi -> N + 1 entries (Tsukihime\n"
     "        # global main+97024: mov w10, #0x3725 / cmp w9, w10, 0x3726 entries; without this the table was cut at 256).\n"
     "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) in regs and not ops[1].startswith(\"#\"):\n"
     "            n = _mov_bound(an, f, a, _regno(ops[1]))\n"
     "            if n is not None:\n"
     "                for k in range(1, 6):\n"
     "                    bm = an.insns.get(a + 4 * k, (\"\", \"\"))[0]\n"
     "                    if bm == \"b.hi\":\n"
     "                        return n + 1\n"
     "                    if bm == \"b.hs\":\n"
     "                        return n\n"
     "                return None\n"
     "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) in regs and ops[1].startswith(\"#\"):\n"),
    (REGEN,
     "#   NX_RECOMP_JT_LSR=1  jump tables with an \"lsr + cmp\" check (apply_jt_lsr.py; crash main+9eba4)\n",
     "#   NX_RECOMP_JT_LSR=1  jump tables with an \"lsr + cmp\" check (apply_jt_lsr.py; crash main+9eba4)\n"
     "#   NX_RECOMP_JT_MOV=1  jump tables with the bound in a register (apply_jt_mov.py; crash main+9708c)\n"),
    (REGEN,
     "    throw 'recomp\\nso.py without apply_relr.py: apply it first'\n"
     "}\n",
     "    throw 'recomp\\nso.py without apply_relr.py: apply it first'\n"
     "}\n"
     "# AP_JT_MOV: jump tables with the bound in a register (main+97000 of the global)\n"
     "if (-not (Select-String -Path (Join-Path $Framework 'recomp\\analysis.py') -Pattern '_JT_MOV = ' -Quiet)) {\n"
     "    throw 'recomp\\analysis.py without apply_jt_mov.py: apply it first'\n"
     "}\n"),
    (REGEN,
     "$env:NX_RECOMP_RELR = '1'\n",
     "$env:NX_RECOMP_RELR = '1'\n"
     "$prevMov = $env:NX_RECOMP_JT_MOV  # AP_JT_MOV\n"
     "$env:NX_RECOMP_JT_MOV = '1'\n"),
    (REGEN,
     "    if ($null -eq $prevFuera) { Remove-Item Env:NX_RECOMP_JT_FUERA -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_FUERA = $prevFuera }  # AP_JT_FUERA\n",
     "    if ($null -eq $prevFuera) { Remove-Item Env:NX_RECOMP_JT_FUERA -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_FUERA = $prevFuera }  # AP_JT_FUERA\n"
     "    if ($null -eq $prevMov) { Remove-Item Env:NX_RECOMP_JT_MOV -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_JT_MOV = $prevMov }  # AP_JT_MOV\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)