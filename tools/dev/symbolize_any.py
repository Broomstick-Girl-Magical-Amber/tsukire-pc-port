#!/usr/bin/env python3
"""symbolize_any.py - Translates exe addresses (as they come out in the crash log) into functions.

Understands the .map of link.exe (MSVC) and that of lld-link (clang-cl). Accepts several addresses at once,
with or without the "tsukihime.exe+" prefix.

Usage:
  python symbolize_any.py build\\tsukihime\\tsukihime.map 140061585 140061937 ...
  python symbolize_any.py build\\tsukihime\\tsukihime.map --log <root>\\data\\tsuki\\nx.log   (takes the host #N of the log)
"""
import bisect
import re
import sys

MSVC = re.compile(r"\s*[0-9a-f]{4}:[0-9a-f]{8}\s+(\S+)\s+([0-9a-f]{16})\s", re.I)
LLD = re.compile(r"^\s*([0-9a-f]{9,16})\s+([0-9a-f]+)\s+\d+\s+(.+?)\s*$", re.I)


def load_map(path):
    syms = []
    for line in open(path, errors="replace"):
        m = MSVC.match(line)
        if m:
            syms.append((int(m.group(2), 16), m.group(1)))
            continue
        m = LLD.match(line)
        if m:
            name = m.group(3).strip()
            # Section lines (".text") and object lines ("x.obj:(.text)") are not symbols.
            if name.startswith(".") or ":(" in name or name.endswith(")"):
                continue
            syms.append((int(m.group(1), 16), name))
    syms.sort()
    return syms


def lookup(syms, addrs_only, va):
    k = bisect.bisect_right(addrs_only, va) - 1
    if k < 0:
        return "?"
    addr, name = syms[k]
    out = f"{name} + {va - addr:#x}"
    fn = re.search(r"(main|sdk)_([0-9A-F]{8})", name)
    if fn:
        out += f"   [recompiled code: {fn.group(1)}+{int(fn.group(2), 16):#x}]"
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    syms = load_map(sys.argv[1])
    if not syms:
        sys.exit("no symbol recognized in the .map")
    addrs_only = [s[0] for s in syms]
    vas = []
    if sys.argv[2] == "--log":
        for line in open(sys.argv[3], errors="replace"):
            m = re.search(r"host #(\d+)\s+\S+\.exe\+([0-9a-f]+)", line, re.I)
            if m:
                vas.append((f"#{m.group(1)}", int(m.group(2), 16)))
    else:
        for i, a in enumerate(sys.argv[2:]):
            vas.append((f"{i}", int(a.split("+")[-1], 16)))
    for tag, va in vas:
        print(f"{tag:>4}  {va:#x}  {lookup(syms, addrs_only, va)}")


if __name__ == "__main__":
    main()