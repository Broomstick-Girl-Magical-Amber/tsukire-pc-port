"""_patch.py - shared engine of the apply_*.py patchers (exact text replacements).

By default it works on <root>/framework (two folders above this script); --root for another one.
Each patcher gives: MARK (marker), EDITS [(relative file or glob pattern, anchor, replacement)], and calls run().
- Idempotent: if the marker is in every file, it does nothing.
- All or nothing: checks every anchor before writing; if one is missing or repeated, it touches nothing.
- Keeps CRLF (the EDITS text uses \\n and is converted if the file is CRLF) and the BOM if there is one.
- --remove does the reverse replacement: leaves the files byte for byte as they were.
"""
import argparse
import glob
import os
import sys

# Relative to where this script is: <root>/tools/patches/_patch.py -> <root>/framework
FRAMEWORK_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "framework"))


def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]
    return raw.decode("utf-8"), b"\r\n" in raw, bom


def conv(s, crlf):
    return s.replace("\n", "\r\n") if crlf else s


def resolve(root, rel):
    if any(ch in rel for ch in "*?["):
        hits = sorted(glob.glob(os.path.join(root, rel), recursive=True))
        if len(hits) != 1:
            sys.exit(f"ERROR: {rel}: {len(hits)} files (1 expected): {hits}. Nothing touched.")
        return hits[0]
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        sys.exit(f"ERROR: {path} does not exist")
    return path


def run(mark, edits, doc):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=FRAMEWORK_ROOT, help="framework folder (default <root>/framework, relative to this script)")
    ap.add_argument("--remove", action="store_true", help="undoes the patch (leaves the files as they were)")
    a = ap.parse_args()

    paths, files = {}, {}
    for rel, _, _ in edits:
        if rel not in paths:
            paths[rel] = resolve(a.root, rel)
            files[rel] = load(paths[rel])

    marked = {rel: mark in txt for rel, (txt, _, _) in files.items()}
    if not a.remove and all(marked.values()):
        print(f"Already applied (marker {mark} in every file). Nothing to do.")
        return
    if a.remove and not any(marked.values()):
        print("It was not applied. Nothing to do.")
        return
    if any(marked.values()) and not all(marked.values()):
        sys.exit(f"ERROR: the marker {mark} is only in " + ", ".join(r for r, m in marked.items() if m) +
                 ". Mixed state: nothing touched.")

    new = {rel: txt for rel, (txt, _, _) in files.items()}
    for rel, old, rep in edits:
        crlf = files[rel][1]
        src, dst = (conv(rep, crlf), conv(old, crlf)) if a.remove else (conv(old, crlf), conv(rep, crlf))
        n = new[rel].count(src)
        if n != 1:
            sys.exit(f"ERROR: anchor found {n} times in {rel} (1 expected):\n{src[:300]}\nNothing touched.")
        new[rel] = new[rel].replace(src, dst)

    if a.remove and any(mark in t for t in new.values()):
        sys.exit(f"ERROR: a {mark} is left after removing (file edited by hand?). Nothing touched.")

    for rel, txt in new.items():
        bom = files[rel][2]
        with open(paths[rel], "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + txt.encode("utf-8"))
        print(("removed: " if a.remove else "patched: ") + os.path.relpath(paths[rel], a.root))