#!/usr/bin/env python3
"""Build replacement help_key atlases (384x256 RGBA) from sliced controller icons.

The cell rectangles are the ones of the original atlas (same positions, so the game's UVs still work).
Switch positions are kept: the Switch "A" cell (right button) gets the Xbox B / PlayStation circle, etc.

Usage:
  python tools/hud/build_help_key.py --sliced utils/hud_sliced --out out_dir [--preview]
Writes <out_dir>/help_key_xbox.png and help_key_ps.png (and _preview_*.png on a grey background with --preview).
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imgops import fill_holes, largest_component  # noqa: E402

ATLAS = (384, 256)
# cell index -> (x, y, w, h) in the original atlas
CELLS = {
    0: (12, 12, 33, 33), 1: (59, 13, 35, 33), 2: (109, 11, 33, 35), 3: (157, 11, 33, 34), 4: (205, 12, 33, 34),
    5: (252, 13, 34, 33), 6: (299, 13, 34, 33),
    7: (14, 61, 30, 31), 8: (62, 61, 30, 31), 9: (110, 61, 30, 31), 10: (158, 61, 30, 31),
    11: (204, 61, 33, 33), 12: (252, 61, 33, 33), 13: (306, 60, 21, 19),
    14: (18, 108, 21, 19), 15: (66, 106, 21, 36), 16: (110, 105, 30, 38), 17: (161, 112, 25, 25), 18: (209, 119, 25, 10),
    19: (14, 159, 33, 29), 20: (66, 159, 33, 29), 21: (118, 156, 33, 33), 22: (170, 156, 32, 32),
}

# Sources are (row, col) of the sliced sheet, or a tuple op: ("union", a, b), ("outline", a, b, c, d), ("stack", a, b)
XBOX = {
    0: (2, 0),
    1: ("union", (2, 1), (2, 3)), 2: ("union", (2, 2), (2, 4)),
    3: (2, 2), 4: (2, 4), 5: (2, 3), 6: (2, 1),                      # up, down, right, left
    7: (0, 5), 8: (0, 6), 9: (0, 7), 10: (0, 4),                     # Switch A,Y,X,B = Xbox B,X,Y,A (by position)
    11: (3, 2), 12: (3, 3), 13: (1, 0), 14: (1, 1), 15: ("stack", (1, 0), (1, 1)),
    16: (4, 2), 17: (5, 0), 18: ("view_pill",),
    19: (1, 0), 20: (1, 1), 21: (1, 2), 22: (1, 3),                  # L, R, ZL, ZR = LB, RB, LT, RT
}
PS = {
    0: ("outline", (2, 4), (2, 5), (2, 6), (2, 7)),
    1: ("union", (2, 5), (2, 7)), 2: ("union", (2, 4), (2, 6)),
    3: (2, 4), 4: (2, 6), 5: (2, 5), 6: (2, 7),
    7: (1, 1), 8: (1, 3), 9: (1, 0), 10: (1, 2),                     # circle, square, triangle, cross
    11: (3, 0), 12: (3, 5), 13: (4, 3), 14: (4, 8), 15: ("stack", (4, 3), (4, 8)),
    16: (3, 1), 17: ("largest", (1, 10)), 18: ("largest_rot", (2, 10)),  # Options / Create pills
    19: (4, 3), 20: (4, 8), 21: (3, 4), 22: (3, 9),                  # L, R, ZL, ZR = L1, R1, L2, R2
}


def load(sliced, dev, rc):
    return Image.open(os.path.join(sliced, dev, f"{dev}_r{rc[0]:02d}_c{rc[1]:02d}.png")).convert("RGBA")


def trim(im):
    bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return im.crop(bb) if bb else im


def view_pill():
    """Wide Xbox 'View' button (two overlapping squares in a pill) for the 25x10 minus cell; drawn at 16x."""
    W, H, ink = 400, 160, (45, 55, 75, 255)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=(255, 255, 255, 255))
    d.rectangle([135, 24, 235, 90], outline=ink, width=18)
    d.rectangle([170, 66, 270, 136], fill=(255, 255, 255, 255), outline=ink, width=18)
    return im


def compose(sliced, dev, spec):
    if spec == ("view_pill",):
        return view_pill()
    if isinstance(spec[0], int):
        return trim(load(sliced, dev, spec))
    op, args = spec[0], [load(sliced, dev, a) for a in spec[1:]]
    w = max(a.width for a in args)
    h = max(a.height for a in args)
    arrs = []
    for a in args:  # same canvas, centred
        c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        c.paste(a, ((w - a.width) // 2, (h - a.height) // 2))
        arrs.append(np.array(c).astype(np.float64))
    if op in ("largest", "largest_rot"):  # biggest blob only (drops a small text label); optionally rotated 90 deg
        a = args[0]
        big = largest_component(np.array(a.getchannel("A")) > 128)
        arr = np.array(a)
        keep = fill_holes(big)  # filled, so thin outlines stay visible when small
        arr[~keep, 3] = 0
        arr[keep] = arr[keep] * 0 + np.array([*arr[big][:, :3].max(axis=0), 255], np.uint8)
        res = trim(Image.fromarray(arr))
        return res.transpose(Image.Transpose.ROTATE_90) if op == "largest_rot" else res
    if op == "union":  # both lit arms on the same cross
        out = arrs[0].copy()
        for a in arrs[1:]:
            take = a[..., 3] > out[..., 3]
            out[take] = a[take]
        return trim(Image.fromarray(out.astype(np.uint8)))
    if op == "outline":  # what all variants share = the cross outline, no lit arm
        out = arrs[0].copy()
        for a in arrs[1:]:
            take = a[..., 3] < out[..., 3]
            out[take] = a[take]
        return trim(Image.fromarray(out.astype(np.uint8)))
    if op == "stack":  # one above the other with a 2 px gap
        parts = [trim(a) for a in args]
        sw = max(p.width for p in parts)
        c = Image.new("RGBA", (sw, sum(p.height for p in parts) + 2 * (len(parts) - 1)), (0, 0, 0, 0))
        y = 0
        for p in parts:
            c.paste(p, ((sw - p.width) // 2, y))
            y += p.height + 2
        return c
    raise ValueError(op)


def fit(im, w, h, margin=0):
    """Scale keeping the aspect ratio so the icon fits (w,h); upscaling keeps edges crisp (alpha contrast)."""
    s = min((w - margin) / im.width, (h - margin) / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    a = np.array(im).astype(np.float64)
    pm = a[..., :3] * (a[..., 3:4] / 255.0)  # premultiplied resampling: no dark/light halos
    chans = [Image.fromarray(np.clip(pm[..., i], 0, 255).astype(np.uint8)).resize((nw, nh), Image.Resampling.LANCZOS) for i in range(3)]
    a0 = np.array(Image.fromarray(a[..., 3].astype(np.uint8)).resize((nw, nh), Image.Resampling.LANCZOS)).astype(np.float64) / 255.0
    pmr = np.stack([np.array(c).astype(np.float64) for c in chans], axis=2)
    rgb = np.where(a0[..., None] > 0.02, pmr / np.maximum(a0[..., None], 0.02), 0)  # un-premultiply
    al = np.clip((a0 - 0.5) * 1.7 + 0.5, 0, 1) if s > 1.15 else a0  # sharpen enlarged flat icons
    out = np.dstack([np.clip(rgb, 0, 255), al * 255])
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


def to_white(im):
    """The packs use a pale blue/grey ink; scale it to pure white (dark glyph details stay dark)."""
    a = np.array(im).astype(np.float64)
    op = a[..., 3] > 200
    ref = np.percentile(a[op][:, :3], 95, axis=0) if op.any() else np.full(3, 255.0)
    a[..., :3] = np.clip(a[..., :3] * (255.0 / np.maximum(ref, 1)), 0, 255)
    return Image.fromarray(a.astype(np.uint8))


def build(sliced, dev, table):
    atlas = Image.new("RGBA", ATLAS, (0, 0, 0, 0))
    for idx, (x, y, w, h) in CELLS.items():
        ic = fit(to_white(compose(sliced, dev, table[idx])), w, h)
        atlas.paste(ic, (x + (w - ic.width) // 2, y + (h - ic.height) // 2), ic)
    return atlas


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sliced", required=True, help="folder with xbox/ and ps/ (output of slice_sheet.py)")
    p.add_argument("--out", required=True)
    p.add_argument("--preview", action="store_true")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for dev, table in (("xbox", XBOX), ("ps", PS)):
        atlas = build(args.sliced, dev, table)
        atlas.save(os.path.join(args.out, f"help_key_{dev}.png"))
        if args.preview:
            bg = Image.new("RGBA", ATLAS, (70, 70, 80, 255))
            bg.alpha_composite(atlas)
            bg.resize((ATLAS[0] * 2, ATLAS[1] * 2), Image.Resampling.NEAREST).save(os.path.join(args.out, f"_preview_{dev}.png"))


if __name__ == "__main__":
    main()
