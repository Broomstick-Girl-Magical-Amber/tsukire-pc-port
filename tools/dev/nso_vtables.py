#!/usr/bin/env python3
"""
nso_vtables.py - Rebuilds the vtable layout of an NSO using its relocations.

For each requested vtable (by class name), reads the slots and resolves which
function each one points to, using the R_AARCH64_RELATIVE / ABS64 /
GLOB_DAT relocations and the table of exported symbols.

Usage:
  python nso_vtables.py exefs/subsdk1 movie::Extractor movie::Decoder
  python nso_vtables.py exefs/subsdk1 --all-prefix movie::      (all the vtables of movie::)

Needs nso_recon.py in the same folder (reuses its NSO reader).
"""
import argparse
import shutil
import struct
import subprocess
import sys

from nso_recon import load_nso, parse_dynamic, symbol_count, DT_STRTAB, DT_SYMTAB, cstr

DT_RELA, DT_RELASZ = 7, 8
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027


def mangle_vtable(cls):
    parts = cls.split("::")
    if len(parts) == 1:
        return f"_ZTV{len(cls)}{cls}"
    return "_ZTVN" + "".join(f"{len(p)}{p}" for p in parts) + "E"


def demangler():
    for tool in ("c++filt", "llvm-cxxfilt"):
        if shutil.which(tool):
            def run(names, tool=tool):
                r = subprocess.run([tool], input="\n".join(names), capture_output=True, text=True)
                return r.stdout.splitlines()
            return run
    try:
        import cxxfilt  # pip install cxxfilt (only works where there is libc/libstdc++)
        cxxfilt.demangle("_ZN5movie9Extractor7AdvanceEv")  # try before trusting it
        return lambda names: [cxxfilt.demangle(n) if n.startswith("_Z") else n for n in names]
    except Exception:  # noqa: BLE001
        print("Warning: no demangler available; the names stay mangled.", file=sys.stderr)
        return lambda names: names


def load_symbols(img, tags):
    symtab, strtab = tags[DT_SYMTAB][0], tags[DT_STRTAB][0]
    syms = []
    for i in range(symbol_count(img, tags)):
        st_name, _, _, shndx, value, size = struct.unpack_from("<IBBHQQ", img, symtab + 24 * i)
        name = cstr(img, strtab + st_name) if st_name else ""
        syms.append((name, shndx, value, size))
    return syms


def load_relocs(img, tags, syms):
    """offset -> (description of the target, target address or None)"""
    rel = {}
    if DT_RELA not in tags:
        return rel
    base, size = tags[DT_RELA][0], tags[DT_RELASZ][0]
    for off in range(base, base + size, 24):
        r_off, r_info, addend = struct.unpack_from("<QQq", img, off)
        rtype, rsym = r_info & 0xFFFFFFFF, r_info >> 32
        if rtype == R_RELATIVE:
            rel[r_off] = ("addr", addend)
        elif rtype in (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT):
            name, shndx, value, _ = syms[rsym]
            if shndx == 0:
                rel[r_off] = ("import", name)
            else:
                rel[r_off] = ("addr", value + addend)
    return rel


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nso")
    ap.add_argument("classes", nargs="*", help="e.g. movie::Extractor")
    ap.add_argument("--all-prefix", help="dump all the vtables whose name starts like this (e.g. movie::)")
    ap.add_argument("-o", "--out", default="vtables.md")
    a = ap.parse_args()

    img, _ = load_nso(a.nso)
    tags = parse_dynamic(img)
    syms = load_symbols(img, tags)
    relocs = load_relocs(img, tags, syms)
    dem = demangler()

    by_addr = {}
    for name, shndx, value, _ in syms:
        if shndx and name and value:
            by_addr.setdefault(value, name)
    vtables = {n: (v, s) for n, sh, v, s in syms if sh and n.startswith("_ZTV")}

    wanted = [mangle_vtable(c) for c in a.classes]
    if a.all_prefix:
        pref = mangle_vtable(a.all_prefix.rstrip(":") + "::X")[:-3]  # _ZTVN5movie
        wanted += sorted(n for n in vtables if n.startswith(pref))
    if not wanted:
        sys.exit("Give classes or --all-prefix")

    out = [f"# Vtables of `{a.nso}`\n",
           "Slot 0 of the table = offset-to-top, slot 1 = typeinfo; the object pointer points to slot 2 "
           "(virtual method index 0).\n"]
    for vt in dict.fromkeys(wanted):
        if vt not in vtables:
            out.append(f"## {dem([vt])[0]}\n\nNot exported in this module.\n")
            continue
        addr, size = vtables[vt]
        rows = []
        for i in range(size // 8):
            off = addr + 8 * i
            raw = struct.unpack_from("<Q", img, off)[0]
            kind, val = relocs.get(off, ("raw", raw))
            if kind == "import":
                target = f"[import] {val}"
            elif kind == "addr":
                target = by_addr.get(val, f"sub_{val:x}  (not exported)")
            else:
                target = f"0x{raw:x}" if raw else "0 / nullptr"
            rows.append((i, off, target))
        names = dem([t for _, _, t in rows])
        out.append(f"## {dem([vt])[0]}  ({size // 8} slots, at 0x{addr:x})\n")
        out.append("| Slot | Virtual index | Vtable offset | Function |\n|---|---|---|---|")
        for (i, off, _), n in zip(rows, names):
            vi = i - 2 if i >= 2 else "-"
            out.append(f"| {i} | {vi} | +0x{8 * i:x} | `{n}` |")
        out.append("")

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Written {a.out}")


if __name__ == "__main__":
    main()