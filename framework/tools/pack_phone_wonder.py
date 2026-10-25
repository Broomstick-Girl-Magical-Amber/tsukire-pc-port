#!/usr/bin/env python3
"""Pack the Wonder data a phone needs into one zip (docs/nvn/ANDROID-PORT.md, 2.5).

The zip holds the folder "Super Mario Bros. Wonder" and is extracted into Internal storage (or the app's own external
files folder): the data root the Android runtime looks in. Everything in it comes from the player's own dump and tools:

    analysis/main.bin, analysis/sdk.bin    the modules (tools/nso2bin.py)
    extract/romfs/...                      the RomFS (3.5 GB)
    spirv/<hash>.spv                       the offline shader translation (tools/maxwell/spirv_harness)
    fonts/shared_5.ttf                     the button glyphs (tools/fonts/make_button_font.py), if built
    config.txt                             NX_* switches for the phone (written here if missing)

Files are stored, not compressed: RomFS files are compressed already, and a stored zip extracts fast on a phone.
Never share the result: it is the game.

Usage: pack_phone_wonder.py [--root private/wonder] [--out private/wonder/Wonder-data.zip] [--list]
"""
import argparse
import os
import sys
import zipfile

# The zip holds the folder itself: extracted into Internal storage it gives the data root the runtime looks in
# ("/storage/emulated/0/" + the game's title, platform_android.cpp).
TOP = "Super Mario Bros. Wonder/"

CONFIG = """# Super Mario Bros. Wonder (Switch recomp) on Android. NX_* lines become environment variables.
NX_HANDHELD=1
"""


def entries(root):
    """(path on disk, path in the zip) for everything the phone needs."""
    for name in ("main.bin", "sdk.bin"):
        p = os.path.join(root, "analysis", name)
        if not os.path.isfile(p):
            sys.exit(f"missing {p} (tools/nso2bin.py)")
        yield p, "analysis/" + name
    romfs = os.path.realpath(os.path.join(root, "extract", "romfs"))
    if not os.path.isdir(romfs):
        sys.exit(f"missing {romfs}")
    for d, _, files in os.walk(romfs):
        for f in sorted(files):
            p = os.path.join(d, f)
            yield p, "extract/romfs/" + os.path.relpath(p, romfs).replace(os.sep, "/")
    spirv = os.path.join(root, "spirv")
    n = 0
    for f in sorted(os.listdir(spirv)) if os.path.isdir(spirv) else []:
        if f.endswith(".spv"):
            n += 1
            yield os.path.join(spirv, f), "spirv/" + f
    if not n:
        sys.exit(f"no shaders in {spirv} (docs/nvn/TRANSLATOR.md)")
    # The harness's records: the c[0] offset of every storage buffer, which the SPIR-V does not carry. Without it
    # the runtime binds no storage buffer of any translated shader (nvn_shaders.cpp, shader_for).
    results = os.path.join(spirv, "results.tsv")
    if not os.path.isfile(results):
        sys.exit(f"missing {results} (docs/nvn/TRANSLATOR.md)")
    yield results, "spirv/results.tsv"
    font = os.path.join(root, "fonts", "shared_5.ttf")
    if os.path.isfile(font):
        yield font, "fonts/shared_5.ttf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="private/wonder")
    ap.add_argument("--out", default="private/wonder/Wonder-data.zip")
    ap.add_argument("--list", action="store_true", help="only count what would go in")
    a = ap.parse_args()
    files = list(entries(a.root))
    total = sum(os.path.getsize(p) for p, _ in files)
    print(f"{len(files)} files, {total / 2**30:.2f} GB")
    if a.list:
        return
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for k, (p, name) in enumerate(files):
            z.write(p, TOP + name)
            if k % 2000 == 0:
                print(f"  {k}/{len(files)}", flush=True)
        if not any(name == "config.txt" for _, name in files):
            z.writestr(TOP + "config.txt", CONFIG)
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 2**30:.2f} GB)")


if __name__ == "__main__":
    main()
