"""apply_jt_lsr.py - recomp: jump table bound with an "lsr + cmp" check (crash main+9eba4 of Tsukihime).

Cause (confirmed disassembling main and reading recomp/analysis.py):
  main+9ea90 (called from main+9cc80) is a big clang switch:
      add  w9, w8, #-0x2758
      lsr  w10, w9, #4
      cmp  w10, #0x370          ; index <= 0x370F  ->  0x3710 entries
      b.hi ...
      adrp x8, ... / add x8, x8, #0xbbc
      adr  x10, #0x9ead4
      ldrh w11, [x8, x9, lsl #1]
      add  x10, x10, x11, lsl #2
      br   x10
  _bound() only recognizes "cmp <index>, #N"; here the cmp is on w10 = w9 >> 4, so the bound stays None and
  entries() reads at most 256 entries. The crash had index 0x349e (13470) -> target main+9eba4, which did not
  end up as a "case" of the emitted switch -> default: nx::call -> "call into the middle of a function".

Active only with NX_RECOMP_JT_LSR=1 when running the recompiler (per game: without the variable the output is the
usual one, so Wonder does not change even if it cannot be measured).

Fix: in _bound(), a "cmp wT, #N" whose wT comes from "lsr wT, <index>, #s" (a few instructions before) gives the
bound (N + 1) << s with b.hi, N << s with b.hs. It only changes tables that today have no bound; the rest of the
recompiler output is identical (verifiable with medir_jt_lsr.py on any .nso, e.g. Wonder's).

Usage:  python apply_jt_lsr.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_JT_LSR), all or nothing, keeps CRLF, refuses if the anchor is not found.
After that the code of main has to be regenerated (python -m recomp ...) and rebuilt.
"""
import argparse
import os
import sys

MARK = "AP_JT_LSR"
REL = "recomp/analysis.py"

OLD = (
    "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) in regs and ops[1].startswith(\"#\"):\n"
    "            n = _imm(ops[1][1:])\n"
)
NEW = (
    "        # AP_JT_LSR: clang's range check for big sparse switches compares the index shifted right:\n"
    "        # lsr wT, wIdx, #s / cmp wT, #N / b.hi -> idx <= (N << s) | (2^s - 1), (N + 1) << s entries (Tsukihime's\n"
    "        # main+9ead0: lsr w10, w9, #4 / cmp w10, #0x370, 0x3710 entries; without this the table was cut at 256).\n"
    "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) not in regs and ops[1].startswith(\"#\"):\n"
    "            s = _lsr_index(an, f, a, _regno(ops[0]), regs)\n"
    "            if s is not None:\n"
    "                n = _imm(ops[1][1:])\n"
    "                for k in range(1, 6):\n"
    "                    bm = an.insns.get(a + 4 * k, (\"\", \"\"))[0]\n"
    "                    if bm == \"b.hi\":\n"
    "                        return (n + 1) << s\n"
    "                    if bm == \"b.hs\":\n"
    "                        return n << s\n"
    "                return None\n"
    "        if mn == \"cmp\" and len(ops) == 2 and _regno(ops[0]) in regs and ops[1].startswith(\"#\"):\n"
    "            n = _imm(ops[1][1:])\n"
)

OLD2 = "def _bound(an, f, before, idx):\n"
NEW2 = (
    "import os as _os  # noqa: E402\n"
    "\n"
    "# AP_JT_LSR: off unless NX_RECOMP_JT_LSR=1 when the recompiler runs (per game: Wonder's output stays the same).\n"
    "_JT_LSR = _os.environ.get(\"NX_RECOMP_JT_LSR\") == \"1\"\n"
    "\n"
    "\n"
    "def _lsr_index(an, f, cmp_at, tmp, regs):\n"
    "    \"\"\"AP_JT_LSR: s if `tmp` was set by `lsr tmp, <one of regs>, #s` just before cmp_at (nothing else writing\n"
    "    tmp or the index in between), else None.\"\"\"\n"
    "    if not _JT_LSR:\n"
    "        return None\n"
    "    a = cmp_at - 4\n"
    "    while a >= max(f.start, cmp_at - 4 * 4):\n"
    "        mn, op = an.insns.get(a, (\"\", \"\"))\n"
    "        ops = _ops(op)\n"
    "        if mn and mn not in NO_DEST and ops:\n"
    "            dst = _regno(ops[0])\n"
    "            if dst == tmp:\n"
    "                if mn == \"lsr\" and len(ops) == 3 and _regno(ops[1]) in regs and ops[2].startswith(\"#\"):\n"
    "                    return _imm(ops[2][1:])\n"
    "                return None\n"
    "            if dst in regs:\n"
    "                return None\n"
    "        a -= 4\n"
    "    return None\n"
    "\n"
    "\n"
    "def _bound(an, f, before, idx):\n"
)

# Version 1 (without the NX_RECOMP_JT_LSR variable): the bound always ran. It is recognized to update or remove it.
NEW2_V1 = NEW2.replace(
    "import os as _os  # noqa: E402\n"
    "\n"
    "# AP_JT_LSR: off unless NX_RECOMP_JT_LSR=1 when the recompiler runs (per game: Wonder's output stays the same).\n"
    "_JT_LSR = _os.environ.get(\"NX_RECOMP_JT_LSR\") == \"1\"\n"
    "\n"
    "\n", "").replace(
    "    if not _JT_LSR:\n"
    "        return None\n", "")

EDITS = [(OLD, NEW), (OLD2, NEW2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework"))
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    path = os.path.join(a.root, REL)
    if not os.path.isfile(path):
        sys.exit(f"ERROR: {path} does not exist")
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    txt = raw.decode("utf-8")
    conv = (lambda s: s.replace("\n", "\r\n")) if crlf else (lambda s: s)
    v2, v1 = conv(NEW2) in txt, conv(NEW2_V1) in txt
    if MARK in txt and not (v1 or v2):
        sys.exit("ERROR: there is an AP_JT_LSR that is not any known version (edited by hand?). Nothing touched.")
    if not a.remove:
        if v2:
            print("Already applied (version 2, with NX_RECOMP_JT_LSR). Nothing to do.")
            return
        if v1:
            if txt.count(conv(NEW2_V1)) != 1:
                sys.exit("ERROR: version 1 found more than once. Nothing touched.")
            txt = txt.replace(conv(NEW2_V1), conv(NEW2))
            open(path, "wb").write(txt.encode("utf-8"))
            print("updated to version 2 (the bound only with NX_RECOMP_JT_LSR=1): " + REL)
            return
    elif not (v1 or v2):
        print("It was not applied. Nothing to do.")
        return
    edits = [(OLD, NEW), (OLD2, NEW2_V1 if v1 else NEW2)]
    for old, new in edits:
        src, dst = (conv(new), conv(old)) if a.remove else (conv(old), conv(new))
        n = txt.count(src)
        if n != 1:
            sys.exit(f"ERROR: anchor found {n} times in {REL} (1 expected):\n{src[:160]}\nNothing touched.")
        txt = txt.replace(src, dst)
    if a.remove and MARK in txt:
        sys.exit("ERROR: an AP_JT_LSR is left after removing (edited by hand?). Nothing touched.")
    open(path, "wb").write(txt.encode("utf-8"))
    print(("removed: " if a.remove else "patched: ") + REL)

if __name__ == "__main__":
    main()