#!/usr/bin/env python3
"""build_hud_assets.py - HUD button icons by device (NX_HUD_GLYPHS): the files the game reads from <data root>/hud.

  manifest.txt         hash of every help_key (button icon atlas) found in the edition's romfs and in its mod layers
                       (<root>/mods/<edition>/*/romfs, as the exe stacks them): the game only replaces a texture whose
                       untiled contents hash to one of these, so nothing else of the same size is touched
  help_key_xbox.bc7    Xbox atlas, raw BC7 blocks (384x256, row-major)  from utils/hud_sliced/xbox (slice_sheet.py)
  help_key_ps.bc7      PlayStation atlas, same                         from utils/hud_sliced/ps
  keyboard_tiles.bin   keyboard tiles (build_keyboard_tiles.py)         from utils/KeyboardButtons/keyboard-input-icons
  manual_<hash>.bin    the controls table (conf_manual*) of each variant found, with its icon column erased, and the
                       icons to stamp on it (build_manual.py). Made from the user's own romfs; it stays in the data root.
The game's own help bar icons (Switch) are not copied anywhere: they stay in the romfs; for them only hashes are written.
Each output is rebuilt only when it is missing or older than its inputs (--force: always).

Usage (recomp_tsukihime.ps1 runs it after the build; also by hand):
  python tools/hud/build_hud_assets.py --edition japan [--root <port root>] [--force]
Exit code 0 unless something is really broken; a missing input only skips that output, with a warning (the game then
keeps the original icons for the device it cannot show).
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA_DIRS = {"japan": os.path.join("data", "tsuki"), "global": os.path.join("data", "tsuki_global")}
ATLAS_NAME = "help_key"
MANUAL_PREFIX = "conf_manual"  # conf_manual0_ja (the controls table); other languages keep the prefix


def newer(out, inputs):
    if not os.path.exists(out):
        return False
    t = os.path.getmtime(out)
    return all(os.path.getmtime(p) <= t for p in inputs if os.path.exists(p))


def scripts(*names):
    return [os.path.join(HERE, n) for n in names]


def layers(root, edition):
    """Romfs folders, lowest first: the base romfs, then <root>/mods/<edition>/<name>/romfs in name order (a name
    starting with _ is disabled), like the exe."""
    out = [os.path.join(root, DATA_DIRS[edition], "extract", "romfs")]
    mods = os.path.join(root, "mods", edition)
    if os.path.isdir(mods):
        for name in sorted(os.listdir(mods)):
            r = os.path.join(mods, name, "romfs")
            if not name.startswith("_") and os.path.isdir(r):
                out.append(r)
    return [p for p in out if os.path.isdir(p)]


def resolve(stack, rel):
    """Top-most layer that has `rel` (relative path inside the romfs), or None."""
    for base in reversed(stack):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return None


def wanted(name):
    return name == ATLAS_NAME or name.startswith(MANUAL_PREFIX)


def build_manifest(root, edition, out, force):
    import ui_archive
    stack = layers(root, edition)
    if not stack:
        print(f"WARNING: no romfs for {edition} ({os.path.join(root, DATA_DIRS[edition], 'extract', 'romfs')}): "
              "no manifest, the game keeps its icons")
        return False
    rels = set()
    for base in stack:
        for p in glob.glob(os.path.join(base, "**", "*.hed"), recursive=True):
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            if stem.endswith("allui"):
                rels.add(os.path.relpath(p, base))
    hashes = []
    inputs = []
    for rel in sorted(rels):
        stem = rel[:-4]
        # every layer view: the base alone and with the mods on top (a layer may replace only some of the 3 files)
        for top in range(1, len(stack) + 1):
            files = [resolve(stack[:top], stem + ext) for ext in (".hed", ".nam", ".mrg")]
            if not all(files):
                continue
            inputs += files
            try:
                found = ui_archive.find_textures(*files, wanted)
            except (OSError, ValueError, KeyError, IndexError) as e:
                print(f"WARNING: {files[2]}: {e}")
                continue
            for name, variants in sorted(found.items()):
                for fmt, w, h, lin, entry in variants:
                    digest = ui_archive.fnv1a64(lin)
                    line = f"{name} {w} {h} {fmt:#06x} {digest:016x}"
                    if line in hashes:
                        continue
                    hashes.append(line)
                    print(f"  {line}  ({os.path.relpath(files[2], root)}, {entry})")
                    if name.startswith(MANUAL_PREFIX):
                        build_manual_variant(root, lin, w, h, digest, os.path.dirname(out), inputs, force)
    if not hashes:
        print(f"WARNING: no {ATLAS_NAME} texture found in the {edition} romfs: no manifest, the game keeps its icons")
        return False
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="ascii", newline="\n") as f:
        f.write("# build_hud_assets.py: textures the game may replace (name width height format fnv1a64 of level 0)\n")
        f.write("\n".join(hashes) + "\n")
    return True


def build_manual_variant(root, lin, w, h, digest, out_dir, archives, force):
    import build_manual
    out = os.path.join(out_dir, f"manual_{digest:016x}.bin")
    icons = glob.glob(os.path.join(root, "utils", "hud_sliced", "*", "*.png"))
    inputs = archives + icons + scripts("build_manual.py", "build_help_key.py", "key_icons.py", "bc7.py", "imgops.py",
                                        "build_keyboard_tiles.py")
    if not force and newer(out, inputs):
        print(f"    {os.path.basename(out)}: up to date")
        return
    print(f"    {os.path.basename(out)}: building (about half a minute)...")
    if build_manual.build(lin, w, h, root, out):
        print(f"    {os.path.basename(out)}: {os.path.getsize(out)} bytes")


def build_pad_atlas(root, dev, out, force):
    import bc7
    import build_help_key
    import numpy as np
    sliced = os.path.join(root, "utils", "hud_sliced")
    src_dir = os.path.join(sliced, dev)
    inputs = glob.glob(os.path.join(src_dir, "*.png")) + scripts("build_help_key.py", "bc7.py", "imgops.py",
                                                                  "build_hud_assets.py")
    if not os.path.isdir(src_dir):
        print(f"WARNING: {src_dir} is missing (slice_sheet.py output): no {dev} atlas")
        return
    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    table = {"xbox": build_help_key.XBOX, "ps": build_help_key.PS}[dev]
    atlas = build_help_key.build(sliced, dev, table)
    data = bc7.encode_bc7(bc7.bleed_rgb(np.array(atlas)))
    with open(out, "wb") as f:
        f.write(data)
    print(f"  {os.path.basename(out)}: {len(data)} bytes")


def build_keyboard(root, out, force):
    import build_keyboard_tiles
    icons = os.path.join(root, "utils", "KeyboardButtons", "keyboard-input-icons")
    if not os.path.isdir(icons):
        print(f"WARNING: {icons} is missing: no keyboard tiles")
        return
    inputs = glob.glob(os.path.join(icons, "*.png")) + scripts("build_keyboard_tiles.py", "key_icons.py",
                                                              "build_help_key.py", "bc7.py", "imgops.py")
    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    print("  keyboard_tiles.bin: encoding (about a minute)...")
    slots, _, tiles = build_keyboard_tiles.build_library(icons, out)
    print(f"  keyboard_tiles.bin: {len(slots)} slots, {len(tiles)} tiles, {os.path.getsize(out)} bytes")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition", required=True, choices=sorted(DATA_DIRS))
    ap.add_argument("--root", default=os.path.normpath(os.path.join(HERE, os.pardir, os.pardir)),
                    help="port root (default: two folders above this script)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import texture2ddecoder  # noqa: F401
    except ImportError as e:
        print(f"WARNING: {e.name} is missing in this python (tools\\install_requirements.ps1 -From 6): HUD icons skipped")
        return 0
    root = os.path.abspath(a.root)
    out_dir = os.path.join(root, DATA_DIRS[a.edition], "hud")
    print(f"HUD icons ({a.edition}) -> {os.path.relpath(out_dir, root)}")
    build_manifest(root, a.edition, os.path.join(out_dir, "manifest.txt"), a.force)
    for dev in ("xbox", "ps"):
        build_pad_atlas(root, dev, os.path.join(out_dir, f"help_key_{dev}.bc7"), a.force)
    build_keyboard(root, os.path.join(out_dir, "keyboard_tiles.bin"), a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
