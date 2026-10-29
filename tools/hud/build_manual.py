#!/usr/bin/env python3
"""build_manual.py - data for the controls table (Options > 操作説明, texture conf_manual*) by device.

The table is one texture with the action names and a column of Switch button icons baked in. From the user's own
texture (each variant found in the romfs and its mod layers, e.g. a translation) this writes <data root>/hud/
manual_<hash>.bin: the table with its icon column erased (BC7, so it stays the user's own text in their language), where
each row's icon goes, and the icons to stamp there (RGBA): Xbox and PlayStation for each row, and every keyboard key
input.ini accepts. The game (hud_glyphs.cpp) composes the table for the device in use when its cached copy is drawn.
The Switch table is the game's own texture, untouched. Nothing from the game is written anywhere else.

Format (little-endian):
  char[4] "NXHM", u32 version (1), u16 width, u16 height, u16 rows, u16 keys
  rows x { u16 x, u16 y, u16 w, u16 h (where the icon is centred and fitted), char button[8] (Npad name) }
  u32 size + BC7 blocks of the erased table (width x height, row-major)
  rows x Xbox icon, rows x PlayStation icon, keys x { u16 vk } + keyboard icon, then the "unassigned" key icon;
  each icon: u16 w, u16 h, RGBA8 rows (straight alpha, sRGB bytes like the texture)

Usage (build_hud_assets.py calls it): build(linear_bc7_level0, width, height, root, out_path)
"""
import os
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bc7  # noqa: E402
from build_help_key import compose, fit, to_white  # noqa: E402
from build_keyboard_tiles import K_UNASSIGNED, VK  # noqa: E402
from key_icons import key_icon, labelled_key  # noqa: E402

# The rows of the table, top to bottom (Japanese edition), and the Npad button each one shows.
BUTTONS = ["A", "B", "Y", "X", "UP", "DOWN", "R", "L", "ZR", "ZL"]
# Same positions as the help bar atlas (build_help_key.py): Switch A (right) = Xbox B = PlayStation circle, etc.
XBOX = {"A": (0, 5), "B": (0, 4), "Y": (0, 6), "X": (0, 7), "UP": (2, 2), "DOWN": (2, 4), "R": (1, 1), "L": (1, 0),
        "ZR": (1, 3), "ZL": (1, 2)}
PS = {"A": (1, 1), "B": (1, 2), "Y": (1, 3), "X": (1, 0), "UP": (2, 4), "DOWN": (2, 6), "R": (4, 8), "L": (4, 3),
      "ZR": (3, 9), "ZL": (3, 4)}
COLUMN = (930, 1060)  # x range searched for the icon column (Japanese table: icons at x 976..1016)
ICON = 40             # pad icons are fitted into ICON x ICON; keys may be up to KEY_W wide
KEY_W = 64


def decode_bc7(blocks, w, h):
    import texture2ddecoder  # tools/requirements.txt
    return np.frombuffer(texture2ddecoder.decode_bc7(blocks, w, h), np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]]


def find_icons(a):
    """Icon boxes (x0, y0, x1, y1) of the column, top to bottom: pixels that differ from the straight line between the
    two edges of the column search range, row by row."""
    x0, x1 = COLUMN
    f = a.astype(np.float64)
    t = (np.arange(x0, x1) - x0) / (x1 - x0)
    base = f[:, x0:x0 + 1] * (1 - t)[None, :, None] + f[:, x1:x1 + 1] * t[None, :, None]
    m = np.abs(f[:, x0:x1] - base).sum(axis=2) > 40
    rows = np.where(m.any(axis=1))[0]
    boxes = []
    if rows.size == 0:
        return boxes
    start = prev = rows[0]
    for y in list(rows[1:]) + [None]:
        if y is None or y > prev + 1:
            sub = m[start:prev + 1]
            xs = np.where(sub.any(axis=0))[0]
            if prev - start >= 20:  # an icon, not a stray line
                boxes.append((x0 + int(xs.min()), int(start), x0 + int(xs.max()) + 1, int(prev) + 1))
            if y is not None:
                start = y
        if y is not None:
            prev = y
    return boxes


def erase(a, boxes, pad=3):
    """The icons replaced, row by row, by the straight line between the colours just left and right of them."""
    out = a.astype(np.float64).copy()
    for x0, y0, x1, y1 in boxes:
        l, r = x0 - pad - 1, x1 + pad
        t = (np.arange(l + 1, r) - l) / (r - l)
        for y in range(max(0, y0 - pad), min(a.shape[0], y1 + pad)):
            out[y, l + 1:r] = out[y, l][None, :] * (1 - t)[:, None] + out[y, r][None, :] * t[:, None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def icon_bytes(im):
    a = np.array(im.convert("RGBA"))
    return struct.pack("<HH", a.shape[1], a.shape[0]) + a.tobytes()


def build(linear, w, h, root, out_path):
    a = decode_bc7(linear, w, h)
    boxes = find_icons(a)
    if len(boxes) != len(BUTTONS):
        print(f"WARNING: {out_path}: {len(boxes)} icons found in the table ({len(BUTTONS)} expected): not built")
        return False
    erased = erase(a, boxes)
    blocks = bc7.encode_bc7(bc7.bleed_rgb(erased))
    sliced = os.path.join(root, "utils", "hud_sliced")
    icons_dir = os.path.join(root, "utils", "KeyboardButtons", "keyboard-input-icons")
    head = struct.pack("<4sIHHHH", b"NXHM", 1, w, h, len(BUTTONS), len(VK))
    rows = b""
    for (x0, y0, x1, y1), b in zip(boxes, BUTTONS):
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        rows += struct.pack("<HHHH", cx - KEY_W // 2, cy - ICON // 2, KEY_W, ICON) + b.encode().ljust(8, b"\0")
    body = struct.pack("<I", len(blocks)) + blocks
    for dev, table in (("xbox", XBOX), ("ps", PS)):
        for b in BUTTONS:
            body += icon_bytes(fit(to_white(compose(sliced, dev, table[b])), ICON, ICON))
    for vk in sorted(VK):
        ic = key_icon(VK[vk], icons_dir, icons_dir)
        bb = ic.getchannel("A").getbbox()
        body += struct.pack("<H", vk) + icon_bytes(fit(ic.crop(bb) if bb else ic, KEY_W, ICON))
    ic = labelled_key(icons_dir, "–")
    bb = ic.getchannel("A").getbbox()
    body += struct.pack("<H", K_UNASSIGNED) + icon_bytes(fit(ic.crop(bb) if bb else ic, KEY_W, ICON))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(head + rows + body)
    return True
