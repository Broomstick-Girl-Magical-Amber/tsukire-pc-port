#!/usr/bin/env python3
"""find_constant.py - Locates in a "new" NSO the equivalent of a data address of an "old" NSO.

Meant to port game.cpp constants between SDK versions (e.g. the NVN format table).
Two independent methods:
  1. Code references: looks in the old NSO for the functions that point to [ADDR, ADDR+SIZE) with
     adrp + add/ldr/str, and lists which data addresses those same functions (by name) point to
     in the new NSO.
  2. Bytes: looks for the content of the old table (several windows) inside the new NSO.

Usage (framework venv; needs nso_recon.py and nso_disasm.py in the same folder):
  python find_constant.py OLD NEW ADDR [--size 0x200] [-o output.md]
"""
import argparse
import bisect
import struct

from nso_recon import load_nso, parse_dynamic
from nso_disasm import load_symbols


def funcs_of(img):
    tags = parse_dynamic(img)
    syms = load_symbols(img, tags)
    fs = sorted((v, s, n) for n, inf, sh, v, s in syms if n and sh and (inf & 0xF) == 2)
    return fs


def owner(funcs, starts, addr):
    k = bisect.bisect_right(starts, addr) - 1
    if k >= 0 and addr < funcs[k][0] + max(funcs[k][1], 4):
        return funcs[k]
    return None


def data_refs(img, start, end):
    """(pc, target) for each adrp followed (up to 8 instr.) by add/ldr/str based on the same register."""
    out = []
    n = (end - start) // 4
    words = struct.unpack_from(f"<{n}I", img, start)
    for i, w in enumerate(words):
        if (w & 0x9F000000) != 0x90000000:
            continue
        pc = start + 4 * i
        imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (pc & ~0xFFF) + (imm << 12)
        rd = w & 31
        for j in range(i + 1, min(i + 9, n)):
            v = words[j]
            rn = (v >> 5) & 31
            if (v & 0xFF800000) == 0x91000000 and rn == rd:  # add xD, xN, #imm{, lsl 12}
                off = ((v >> 10) & 0xFFF) << (12 if (v >> 22) & 1 else 0)
                out.append((pc, page + off))
                break
            if (v & 0x3B000000) == 0x39000000 and rn == rd:  # ldr/str (unsigned offset)
                size = v >> 30
                if (v >> 26) & 1 and ((v >> 23) & 1):  # SIMD q
                    size = 4
                out.append((pc, page + (((v >> 10) & 0xFFF) << size)))
                break
            if (v & 0x1F) == rd and (v & 0x9F000000) == 0x90000000:  # another adrp overwrites the register
                break
    return out


def hexdump(img, addr, n):
    rows = []
    for o in range(0, n, 16):
        b = img[addr + o:addr + o + 16]
        rows.append(f"  {addr + o:8x}: " + " ".join(f"{x:02x}" for x in b))
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("dir")
    ap.add_argument("--size", default="0x200", help="size of the area to consider (hex)")
    ap.add_argument("-o", "--out", default="find_constant.md")
    ap.add_argument("--entry", type=lambda x: int(x, 0), default=0x30, help="entry size to locate differences")
    a = ap.parse_args()
    addr, size = int(a.dir, 16), int(a.size, 16)

    img_v, info_v = load_nso(a.old)
    img_n, info_n = load_nso(a.new)
    fv, fn = funcs_of(img_v), funcs_of(img_n)
    sv, sn = [f[0] for f in fv], [f[0] for f in fn]
    out = [f"# {a.old} 0x{addr:x} (+0x{size:x}) -> {a.new}\n"]

    # 1. code references
    tv = info_v["text"]
    refs = [(pc, d) for pc, d in data_refs(img_v, tv[0], tv[0] + tv[1]) if addr - 0x10 <= d < addr + size]
    out.append(f"## 1. Code of the old one that points to the area ({len(refs)} references)\n")
    by_name = {}
    for pc, d in refs:
        f = owner(fv, sv, pc)
        name = f[2] if f else "(no symbol)"
        out.append(f"- {pc:#x} -> {d:#x} (area+{d - addr:#x})  in `{name}`" + (f"+{pc - f[0]:#x}" if f else ""))
        if f:
            by_name.setdefault(name, []).append(d - addr)
    tn = info_n["text"]
    nrefs = data_refs(img_n, tn[0], tn[0] + tn[1])
    fn_by_name = {f[2]: f for f in fn}
    out.append("\n## 1b. Same functions in the new one: data targets\n")
    cand = {}
    for name, deltas in by_name.items():
        f = fn_by_name.get(name)
        if not f:
            out.append(f"- `{name}`: does not exist in the new one")
            continue
        ds = sorted({d for pc, d in nrefs if f[0] <= pc < f[0] + max(f[1], 4) and d >= tn[0] + tn[1]})
        out.append(f"- `{name}` (+{f[0]:#x}): " + ", ".join(f"{d:#x}" for d in ds))
        for d in ds:
            for dl in deltas:
                cand[d - dl] = cand.get(d - dl, 0) + 1
    if cand:
        out.append("\nCandidates for the area start in the new one (votes):")
        for c, v in sorted(cand.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"- {c:#x}: {v}")

    # 2. bytes
    out.append("\n## 2. Content of the old area looked up in the new one\n")
    hits = {}
    for win in range(0, size, 0x40):
        pat = bytes(img_v[addr + win:addr + win + 0x20])
        if len(pat) < 0x20 or pat.count(0) > 24:
            continue
        p = img_n.find(pat)
        found = []
        while p != -1 and len(found) < 5:
            found.append(p)
            p = img_n.find(pat, p + 1)
        out.append(f"- window +{win:#x}: " + (", ".join(f"{x:#x} (start {x - win:#x})" for x in found) or "no matches"))
        for x in found:
            hits[x - win] = hits.get(x - win, 0) + 1
    best = sorted(hits.items(), key=lambda kv: -kv[1])[:5]
    for c, v in best:
        diff = [i for i in range(size) if c + i >= len(img_n) or img_n[c + i] != img_v[addr + i]]
        out.append(f"\nStart {c:#x}: {v} windows, {size - len(diff)}/{size} bytes equal")
        if diff and c == best[0][0]:
            out.append("Different bytes (offset: old -> new):")
            for i in diff[:200]:
                nv = f"{img_n[c + i]:02x}" if c + i < len(img_n) else "--"
                out.append(f"- +{i:#x} (entry {i // a.entry}, byte {i % a.entry:#x}): {img_v[addr + i]:02x} -> {nv}")
    out.append("\n## Dump (old)\n```\n" + hexdump(img_v, addr, min(size, 0x80)) + "\n```")
    for c, _ in best[:1] or sorted(cand.items(), key=lambda kv: -kv[1])[:1]:
        out.append(f"\n## Dump (new, {c:#x})\n```\n" + hexdump(img_n, c, min(size, 0x80)) + "\n```")

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Written {a.out}")


if __name__ == "__main__":
    main()