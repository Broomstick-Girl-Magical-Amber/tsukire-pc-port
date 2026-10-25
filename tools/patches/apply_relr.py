"""apply_relr.py - recomp: RELR relocations (DT_RELR) in the NSO reader (sdk/main of SDK 17.5.4, Tsukihime global).

Symptom (log 17:02): crash at startup, nn::lm::CreateLogServiceByHipc -> nn::sf::hipc::ConnectToHipcService with
x0 = 0x8a1ec6: a pointer of the module NOT relocated (it should be 0x4010000000 + 0x8a1ec6, a string of the rodata).
recomp/nso.py only reads DT_RELA and DT_JMPREL; binaries linked with RELR (DT_RELR = 36, compact "relative
relocations") keep most of the internal pointers there, and nobody relocates them.

Fix (only with NX_RECOMP_RELR=1 when running the recompiler): if .dynamic has DT_RELR/DT_RELRSZ, each address
it encodes is added as Reloc(R_AARCH64_RELATIVE) with addend = the value stored at that address (RELR uses an
implicit addend). The runtime already applies RELATIVE (base + addend) from table.cpp, and the analysis already uses
those addends as function seeds. Without the variable, or in a module without DT_RELR, the output is the usual one.

RELR format: each 64-bit word is an address (bit 0 = 0: relocate there and continue at +8) or a bitmap
(bit 0 = 1: bits 1..63 mark the words where + 8*(i-1); then where += 63*8).

It also adds NX_RECOMP_RELR=1 (and the check for the patch) to <root>\\tools\\internal\\regen_tsuki_global.ps1
(independent of apply_jt_outside.py: they can be applied and removed in any order). The japan one is not touched.

Usage:  python apply_relr.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_RELR), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found.
After: regen_tsuki_global.ps1 main, sdk  and  cmake --build build\\tsukihime_global
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_RELR"
NSO = "recomp/nso.py"
REGEN = "../tools/internal/regen_tsuki_global.ps1"

EDITS = [
    (NSO,
     "DT_GNU_HASH = 0x6FFFFEF5\n",
     "DT_GNU_HASH = 0x6FFFFEF5\n"
     "DT_RELRSZ, DT_RELR, DT_RELRENT = 35, 36, 37  # AP_RELR\n"
     "\n"
     "import os as _os  # noqa: E402\n"
     "\n"
     "# AP_RELR: off unless NX_RECOMP_RELR=1 when the recompiler runs (per game: Wonder's output stays the same).\n"
     "# Modules linked with RELR (Tsukihime global, SDK 17.5.4) keep most internal pointers there.\n"
     "_RELR = _os.environ.get(\"NX_RECOMP_RELR\") == \"1\"\n"),
    (NSO,
     "    if DT_JMPREL in dyn:\n"
     "        relocs(dyn[DT_JMPREL], dyn[DT_PLTRELSZ])\n",
     "    if DT_JMPREL in dyn:\n"
     "        relocs(dyn[DT_JMPREL], dyn[DT_PLTRELSZ])\n"
     "    if _RELR and DT_RELR in dyn:  # AP_RELR: implicit addends -> RELATIVE relocs with the stored value\n"
     "        where = 0\n"
     "        for a in range(dyn[DT_RELR], dyn[DT_RELR] + dyn.get(DT_RELRSZ, 0), 8):\n"
     "            e = m.u64(a)\n"
     "            if e & 1 == 0:\n"
     "                m.relocs.append(Reloc(e, R_AARCH64_RELATIVE, 0, m.u64(e)))\n"
     "                where = e + 8\n"
     "                continue\n"
     "            bits, k = e >> 1, 0\n"
     "            while bits:\n"
     "                if bits & 1:\n"
     "                    p = where + 8 * k\n"
     "                    m.relocs.append(Reloc(p, R_AARCH64_RELATIVE, 0, m.u64(p)))\n"
     "                bits >>= 1\n"
     "                k += 1\n"
     "            where += 63 * 8\n"),
    (REGEN,
     "$prev = $env:NX_RECOMP_JT_LSR\n",
     "# AP_RELR: RELR relocations of SDK 17.5.4\n"
     "if (-not (Select-String -Path (Join-Path $Framework 'recomp\\nso.py') -Pattern '_RELR = ' -Quiet)) {\n"
     "    throw 'recomp\\nso.py without apply_relr.py: apply it first'\n"
     "}\n"
     "$prev = $env:NX_RECOMP_JT_LSR\n"),
    (REGEN,
     "Push-Location $Framework\n",
     "$prevRelr = $env:NX_RECOMP_RELR  # AP_RELR\n"
     "$env:NX_RECOMP_RELR = '1'\n"
     "Push-Location $Framework\n"),
    (REGEN,
     "    Pop-Location\n",
     "    Pop-Location\n"
     "    if ($null -eq $prevRelr) { Remove-Item Env:NX_RECOMP_RELR -ErrorAction SilentlyContinue } else { $env:NX_RECOMP_RELR = $prevRelr }  # AP_RELR\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)