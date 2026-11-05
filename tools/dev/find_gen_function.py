#!/usr/bin/env python3
"""find_gen_function.py - Finds a recompiled function in the generated C++ (data/<edition>/gen) and summarizes it.

The profiler (apply_diag_profile.py) and the crash log name hot code as the linker map does: main_000A90C0 = the function
of the main module that starts at guest offset 0xA90C0, sdk_00520C40 = the one of the sdk at 0x520C40. This finds its
definition, tells where it is, how long it is and what kind of statements it has (loops, jumps, calls), and prints it.

Usage (any Python 3):
  python tools\\dev\\find_gen_function.py data\\tsuki\\gen main_000A90C0
  python tools\\dev\\find_gen_function.py data\\tsuki\\gen main_000A90C0 --lines 400 > main_A90C0_gen.txt
  python tools\\dev\\find_gen_function.py data\\tsuki\\gen main_000A90C0 --out main_A90C0_gen.txt   (everything, no limit)
Only reads. Nothing of the game is copied into the repo: send the output to whoever is analysing, do not commit it.
"""
import argparse
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen_dir", help="the gen folder of the edition (data\\tsuki\\gen)")
    ap.add_argument("name", help="function name, e.g. main_000A90C0")
    ap.add_argument("--lines", type=int, default=250, help="lines of the body to print (default 250; 0 = none)")
    ap.add_argument("--out", help="write the whole function to this file instead (no line limit)")
    a = ap.parse_args()

    start_rx = re.compile(r"^\s*(?:static\s+|inline\s+)*(?:void|[A-Za-z_][\w:<>]*)\s+" + re.escape(a.name) + r"\s*\(")
    hits = []
    for root, _, files in os.walk(a.gen_dir):
        for fn in sorted(files):
            if not fn.endswith((".cpp", ".cc", ".h", ".inc")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.read().split("\n")
            except OSError:
                continue
            for i, ln in enumerate(lines):
                if start_rx.match(ln) and not ln.rstrip().endswith(";"):
                    j = i
                    depth = 0
                    seen = False
                    while j < len(lines):
                        depth += lines[j].count("{") - lines[j].count("}")
                        seen = seen or "{" in lines[j]
                        if seen and depth <= 0:
                            break
                        j += 1
                    hits.append((path, i + 1, lines[i:j + 1]))
    if not hits:
        sys.exit("%s not found under %s" % (a.name, a.gen_dir))
    for path, line, body in hits:
        text = "\n".join(body)
        kinds = {
            "lines": len(body),
            "bytes": len(text),
            "labels": len(re.findall(r"^\s*\w+:\s*(?://.*)?$", text, re.M)),
            "goto": len(re.findall(r"\bgoto\b", text)),
            "switch": len(re.findall(r"\bswitch\b", text)),
            "while/for": len(re.findall(r"\b(?:while|for)\b", text)),
            "calls (call/bl)": len(re.findall(r"\b(?:call|nx::call|bl_)\w*\(", text)),
            "svc": len(re.findall(r"\bsvc\w*\(", text)),
        }
        print("== %s:%d" % (os.path.relpath(path), line))
        print("   " + ", ".join("%s %s" % (k, v) for k, v in kinds.items()))
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print("   written to " + a.out)
        elif a.lines > 0:
            print("\n".join(body[:a.lines]))
            if len(body) > a.lines:
                print("... (%d more lines; use --lines or --out)" % (len(body) - a.lines))


if __name__ == "__main__":
    main()