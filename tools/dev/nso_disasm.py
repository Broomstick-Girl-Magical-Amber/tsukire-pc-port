#!/usr/bin/env python3
"""nso_disasm.py - Disassembles functions of an NSO, by name (regex) or by address range (v2).

Calls (bl) are annotated with the name of the target function: exported from the same module, or imported
(the PLT stub "adrp x16 / ldr x17, [x16, #off] / br x17" is followed to its relocation).

Usage (with the framework venv, which has capstone):
  python nso_disasm.py exefs/sdk "AudioRenderSystem.*GetExecutionMode" [--max 40]
  python nso_disasm.py exefs/main --range 0x9fa00 0x9fb00

Needs nso_recon.py in the same folder.
"""
import argparse
import bisect
import re
import shutil
import struct
import subprocess

import capstone

from nso_recon import load_nso, parse_dynamic, symbol_count, DT_STRTAB, DT_SYMTAB, cstr

DT_RELA, DT_RELASZ, DT_JMPREL, DT_PLTRELSZ = 7, 8, 23, 2


def demangle(names):
    for tool in ("c++filt", "llvm-cxxfilt"):
        if shutil.which(tool):
            r = subprocess.run([tool], input="\n".join(names), capture_output=True, text=True)
            out = r.stdout.splitlines()
            if len(out) == len(names):
                return out
    return names


def load_symbols(img, tags):
    symtab, strtab = tags[DT_SYMTAB][0], tags[DT_STRTAB][0]
    syms = []
    for i in range(symbol_count(img, tags)):
        st_name, st_info, _, shndx, value, size = struct.unpack_from("<IBBHQQ", img, symtab + 24 * i)
        name = cstr(img, strtab + st_name) if st_name else ""
        syms.append((name, st_info, shndx, value, size))
    return syms


def got_names(img, tags, syms):
    """GOT address -> name of the imported symbol (RELA and JMPREL relocations)."""
    out = {}
    for base_tag, size_tag in ((DT_RELA, DT_RELASZ), (DT_JMPREL, DT_PLTRELSZ)):
        if base_tag not in tags or size_tag not in tags:
            continue
        base, size = tags[base_tag][0], tags[size_tag][0]
        for off in range(base, base + size, 24):
            r_off, r_info, _ = struct.unpack_from("<QQq", img, off)
            sym = r_info >> 32
            if sym and sym < len(syms) and syms[sym][0]:
                out[r_off] = syms[sym][0]
    return out


def plt_target(md, img, addr, got):
    """If addr is a PLT stub, the name of the import it jumps to."""
    try:
        ins = list(md.disasm(img[addr:addr + 12], addr))
    except Exception:  # noqa: BLE001
        return None
    if len(ins) < 2 or ins[0].mnemonic != "adrp" or ins[1].mnemonic != "ldr":
        return None
    page = int(ins[0].op_str.split("#")[-1], 16)
    m = re.search(r"#(0x[0-9a-f]+|\d+)\]", ins[1].op_str)
    if not m:
        return None
    return got.get(page + int(m.group(1), 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nso")
    ap.add_argument("regex", nargs="?")
    ap.add_argument("--range", nargs=2, metavar=("START", "END"), help="disassemble [START, END) (hex)")
    ap.add_argument("--max", type=int, default=40, help="maximum instructions per function (regex mode)")
    a = ap.parse_args()

    img, info = load_nso(a.nso)
    tags = parse_dynamic(img)
    syms = load_symbols(img, tags)
    got = got_names(img, tags, syms)
    funcs = sorted((v, s, n) for n, inf, sh, v, s in syms if n and sh and (inf & 0xF) == 2)
    starts = [f[0] for f in funcs]
    text_end = info["text"][0] + info["text"][1]
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

    def name_of(addr):
        k = bisect.bisect_right(starts, addr) - 1
        if k >= 0 and funcs[k][0] == addr:
            return funcs[k][2]
        return plt_target(md, img, addr, got)

    def dump(start, end):
        lines, names = [], []
        for ins in md.disasm(img[start:min(end, text_end)], start):
            note = ""
            if ins.mnemonic in ("bl", "b") and ins.op_str.startswith("#"):
                n = name_of(int(ins.op_str[1:], 16))
                if n:
                    note = n
            lines.append((ins, note))
            if note:
                names.append(note)
        pretty = dict(zip(names, demangle(names))) if names else {}
        for ins, note in lines:
            tail = f"   ; {pretty.get(note, note)}" if note else ""
            print(f"   {ins.address:8x}:  {ins.mnemonic:8} {ins.op_str}{tail}")

    if a.range:
        start, end = int(a.range[0], 16), int(a.range[1], 16)
        k = bisect.bisect_right(starts, start) - 1
        where = f" (inside {funcs[k][2]}+{start - funcs[k][0]:#x})" if k >= 0 and start < funcs[k][0] + max(funcs[k][1], 1) else ""
        print(f"== {a.nso} [{start:#x}, {end:#x}){where}")
        dump(start, end)
        return

    if not a.regex:
        raise SystemExit("give a regex or --range")
    rx = re.compile(a.regex)
    sel = [f for f in funcs if rx.search(f[2])]
    if not sel:
        raise SystemExit("no function symbol matches the regex")
    for (value, size, name), nice in zip(sel, demangle([f[2] for f in sel])):
        n = min(size // 4 if size else a.max, a.max)
        print(f"\n== {nice}\n   {name}\n   +{value:#x}, {size:#x} bytes")
        dump(value, value + n * 4)


if __name__ == "__main__":
    main()