#!/usr/bin/env python3
"""
nso_recon.py - Phase 0 of a static recompilation of a Switch game.

Analyzes the NSOs of an already extracted ExeFS (main, sdk, subsdk*) and optionally the
RomFS, and generates a Markdown report with:
  - NEEDED modules and segment sizes
  - imports grouped by API (NVN, Vulkan, OpenGL, nn::*, libc, ...)
  - middleware/engine signatures in the strings (CRI, Unity, Unreal, codecs...)
  - file extensions of the RomFS (hints of the video/audio format)

Usage:
  pip install lz4
  python nso_recon.py --exefs private/tsuki/exefs [--romfs private/tsuki/romfs] [-o report.md]

Only reads your local files. It contains and downloads nothing of the game.
"""
import argparse
import collections
import os
import re
import struct
import sys

try:
    import lz4.block
except ImportError:
    sys.exit("lz4 is missing: pip install lz4")

# ---------------------------------------------------------------- NSO -> image

def load_nso(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"NSO0":
        return None
    flags = struct.unpack_from("<I", raw, 0x0C)[0]
    segs = []
    for i, hdr in enumerate((0x10, 0x20, 0x30)):
        foff, moff, size = struct.unpack_from("<III", raw, hdr)
        fsize = struct.unpack_from("<I", raw, 0x60 + 4 * i)[0]
        segs.append((foff, moff, size, fsize, bool(flags & (1 << i))))
    bss = struct.unpack_from("<I", raw, 0x3C)[0]
    total = max(m + s for _, m, s, _, _ in segs) + bss
    img = bytearray(total)
    for foff, moff, size, fsize, comp in segs:
        blob = raw[foff:foff + fsize]
        data = lz4.block.decompress(blob, uncompressed_size=size) if comp else blob[:size]
        img[moff:moff + len(data)] = data
    names = ("text", "rodata", "data")
    info = {n: (s[1], s[2]) for n, s in zip(names, segs)}
    info["bss"] = bss
    return bytes(img), info


# ------------------------------------------------------------- MOD0 / dynamic

DT_NULL, DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_STRSZ = 0, 1, 4, 5, 6, 10
DT_GNU_HASH = 0x6FFFFEF5


def cstr(img, off):
    end = img.find(b"\0", off)
    return img[off:end].decode("utf-8", "replace")


def parse_dynamic(img):
    mod0 = struct.unpack_from("<I", img, 4)[0]
    if img[mod0:mod0 + 4] != b"MOD0":
        raise ValueError("MOD0 not found")
    dyn = mod0 + struct.unpack_from("<i", img, mod0 + 4)[0]
    tags = collections.defaultdict(list)
    off = dyn
    while off + 16 <= len(img):
        tag, val = struct.unpack_from("<qQ", img, off)
        if tag == DT_NULL:
            break
        tags[tag].append(val)
        off += 16
    return tags


def symbol_count(img, tags):
    if DT_HASH in tags:
        return struct.unpack_from("<I", img, tags[DT_HASH][0] + 4)[0]  # nchain
    symtab, strtab = tags[DT_SYMTAB][0], tags[DT_STRTAB][0]
    if strtab > symtab:
        return (strtab - symtab) // 24
    raise ValueError("could not determine the number of symbols")


def parse_symbols(img, tags):
    symtab, strtab = tags[DT_SYMTAB][0], tags[DT_STRTAB][0]
    imports, exports = [], []
    for i in range(symbol_count(img, tags)):
        st_name, st_info, _, shndx, value, _ = struct.unpack_from("<IBBHQQ", img, symtab + 24 * i)
        if not st_name:
            continue
        name = cstr(img, strtab + st_name)
        (imports if shndx == 0 else exports).append(name)
    needed = [cstr(img, strtab + v) for v in tags.get(DT_NEEDED, [])]
    return imports, exports, needed


# ------------------------------------------------------------- classification

def nested_ns(mangled, depth=2):
    """_ZN2nn2os15CreateThread... -> 'nn::os' (minimal parser, no dependencies)."""
    m = re.match(r"_ZN(?:[rVK]*)(.*)", mangled)
    if not m:
        return None
    s, parts = m.group(1), []
    while s and s[0].isdigit() and len(parts) < depth:
        n = re.match(r"(\d+)", s)
        ln = int(n.group(1))
        s = s[len(n.group(1)):]
        parts.append(s[:ln])
        s = s[ln:]
    return "::".join(parts) if parts else None


def classify(sym):
    if sym.startswith("nvn"):
        return "NVN (Nintendo's own graphics API)"
    if sym.startswith("glslc"):
        return "glslc (Nintendo's GLSL->NVN compiler)"
    if sym.startswith("vk"):
        return "Vulkan"
    if sym.startswith(("gl", "egl")):
        return "OpenGL / EGL"
    if sym.startswith("_ZN2nn"):
        ns = nested_ns(sym)
        return ns or "nn::?"
    if sym.startswith("_Z"):
        ns = nested_ns(sym, 1)
        return f"C++ {ns}" if ns else "C++ (others)"
    return "C / libc / others"


SIGNATURES = {
    "CRIWARE (general)": [b"CRIWARE", b"CRI Middleware", b"criAtom", b"criMana"],
    "CRI Sofdec2 (video .usm)": [b"Sofdec", b"criMana", b"CRI Mana"],
    "CRI ADX2 (audio .acb/.awb)": [b"criAtom", b"ADX2", b"CRI Atom"],
    "Unity": [b"UnityEngine", b"il2cpp", b"UnityPlayer"],
    "Unreal Engine": [b"UnrealEngine", b"FEngineLoop", b"UE4"],
    "Wwise": [b"AkSoundEngine", b"Wwise"],
    "FMOD": [b"FMOD"],
    "Bink": [b"Bink", b"RAD Game Tools"],
    "libvpx / WebM": [b"libvpx", b"webm", b"VP9"],
    "FFmpeg / libav": [b"libavcodec", b"FFmpeg", b"Lavc"],
    "Theora / Ogg": [b"theora", b"OggS", b"libvorbis", b"Xiph"],
    "Hardware decoder (nn::mm / nvdec)": [b"nvdec", b"nn::mm", b"/dev/nvhost-nvdec"],
    "Lua": [b"lua_pushstring", b"Lua 5."],
    "Known VN engines": [b"KiriKiri", b"krkr", b"Siglus", b"Ren'Py", b"Artemis", b"NScripter", b"onscripter"],
}


def scan_signatures(img):
    hits = {}
    for label, needles in SIGNATURES.items():
        found = sorted({n.decode() for n in needles if n in img})
        if found:
            hits[label] = found
    return hits


# ------------------------------------------------------------------- report

def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def romfs_summary(root):
    exts, sizes, top = collections.Counter(), collections.Counter(), collections.Counter()
    for dp, _, files in os.walk(root):
        rel = os.path.relpath(dp, root)
        first = rel.split(os.sep)[0] if rel != "." else "(root)"
        for fn in files:
            ext = os.path.splitext(fn)[1].lower() or "(no ext)"
            sz = os.path.getsize(os.path.join(dp, fn))
            exts[ext] += 1
            sizes[ext] += sz
            top[first] += sz
    return exts, sizes, top


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exefs", required=True)
    ap.add_argument("--romfs")
    ap.add_argument("-o", "--out", default="recon_report.md")
    ap.add_argument("--dump-imports", action="store_true", help="include the full list of imports")
    ap.add_argument("--exports-filter", help="regex: list the exports that match (e.g. '5movie')")
    a = ap.parse_args()

    out = ["# Reconnaissance report (phase 0)\n"]
    all_imports = collections.Counter()

    for fn in sorted(os.listdir(a.exefs)):
        p = os.path.join(a.exefs, fn)
        if not os.path.isfile(p):
            continue
        res = load_nso(p)
        if res is None:
            continue
        img, info = res
        out.append(f"## Module `{fn}`\n")
        out.append("| Segment | Offset | Size |\n|---|---|---|")
        for seg in ("text", "rodata", "data"):
            off, sz = info[seg]
            out.append(f"| {seg} | 0x{off:x} | {human(sz)} |")
        out.append(f"| bss | - | {human(info['bss'])} |\n")
        try:
            tags = parse_dynamic(img)
            imports, exports, needed = parse_symbols(img, tags)
        except Exception as e:  # noqa: BLE001
            out.append(f"Could not read the dynamic table: {e}\n")
            continue
        out.append(f"- NEEDED: {', '.join(needed) or '(none)'}")
        out.append(f"- Imports: {len(imports)}  |  Exports: {len(exports)}\n")
        groups = collections.Counter(classify(s) for s in imports)
        if groups:
            out.append("| Import group | Count |\n|---|---|")
            for g, c in groups.most_common():
                out.append(f"| {g} | {c} |")
            out.append("")
        sig = scan_signatures(img)
        if sig:
            out.append("**Signatures found in the binary:**\n")
            for k, v in sig.items():
                out.append(f"- {k}: {', '.join(v)}")
            out.append("")
        for s in imports:
            all_imports[s] += 1
        if a.dump_imports and imports:
            out.append("<details><summary>Full imports</summary>\n\n```")
            out.extend(sorted(imports))
            out.append("```\n</details>\n")
        if a.exports_filter:
            rx = re.compile(a.exports_filter)
            sel = sorted(e for e in exports if rx.search(e))
            if sel:
                out.append(f"<details><summary>Exports matching /{a.exports_filter}/ ({len(sel)})</summary>\n\n```")
                out.extend(sel)
                out.append("```\n</details>\n")
        if a.dump_imports and exports and len(exports) <= 200:
            out.append("<details><summary>Exports</summary>\n\n```")
            out.extend(sorted(exports))
            out.append("```\n</details>\n")

    # graphics verdict
    names = list(all_imports)
    names = [n for n in names if not n.startswith("glslc")]
    has = lambda pref: any(n.startswith(pref) for n in names)  # noqa: E731
    out.append("## Likely graphics path\n")
    if has("vk"):
        out.append("- **Vulkan** imported: Hades-like path (passthrough to the PC driver). The easiest one.")
    if has("nvn"):
        out.append("- **NVN** imported: Wonder-like path (NVN over Vulkan + Maxwell shader translation).")
    if has("gl") or has("egl"):
        out.append("- **OpenGL/EGL** imported: the framework does not have this path yet; it would have to be written.")
    if not (has("vk") or has("nvn") or has("gl") or has("egl")):
        out.append("- No graphics imports in ExeFS: check whether NVN is statically linked (look for 'nvn' strings).")

    if a.romfs:
        exts, sizes, top = romfs_summary(a.romfs)
        out.append("\n## RomFS\n")
        out.append("| Extension | Files | Size |\n|---|---|---|")
        for ext, _ in sizes.most_common(25):
            out.append(f"| {ext} | {exts[ext]} | {human(sizes[ext])} |")
        out.append("\n| Top-level folder | Size |\n|---|---|")
        for d, sz in top.most_common(15):
            out.append(f"| {d} | {human(sz)} |")

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Report written to {a.out}")


if __name__ == "__main__":
    main()