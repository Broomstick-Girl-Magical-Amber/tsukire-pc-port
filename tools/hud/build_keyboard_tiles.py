#!/usr/bin/env python3
"""Build the keyboard tile library used to compose the help_key atlas at game start.

The keyboard atlas depends on input.ini, which the port reads at start-up, so the game composes it
itself from pre-encoded BC7 tiles: one tile per (slot, key). A slot is a 4-pixel-aligned rectangle
inside one cell of the atlas (a cell can be split in halves or quarters to show 2 or 4 keys).

Library format (little-endian), written to --out (e.g. <data root>/hud/keyboard_tiles.bin):
  char[4] "NXHK", u32 version (1), u16 atlas_w, u16 atlas_h, u16 n_cells, u16 n_slots, u32 n_tiles
  n_cells x { u8 kind (0 one key, 1 two side by side, 2 two stacked, 3 d-pad), u8 count, u16 full_slot,
              u16 part_slot[4] (0xFFFF unused), char button[4][8] (Npad names as input.ini writes them, NUL padded) }
  n_slots x { u16 x, u16 y, u16 w, u16 h }                     aligned rectangles, index = slot id
  n_tiles x { u16 slot, u16 key, u32 offset, u32 size }        key = Windows VK code, or a special id
  BC7 blocks (row-major inside each tile; offsets are from the start of the file)
  Special keys: 0xF001 arrow cluster, 0xF002 WASD cluster, 0xF000 unassigned (a key cap with a dash),
  0xF0FF a fully transparent tile (clears a whole cell before its parts are written).
  The game (hud_glyphs.cpp) composes the atlas from this: for each cell, the first key input.ini gives each button.

Usage:
  python tools/hud/build_keyboard_tiles.py --icons utils/KeyboardButtons/keyboard-input-icons \
      --out utils/hud_out/keyboard_tiles.bin [--preview-ini path/to/input.ini] [--preview-dir utils/hud_out]
"""
import argparse
import os
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bc7  # noqa: E402
from build_help_key import ATLAS, CELLS, fit  # noqa: E402
from key_icons import key_icon, labelled_key  # noqa: E402

K_UNASSIGNED, K_ARROWS, K_WASD, K_CLEAR = 0xF000, 0xF001, 0xF002, 0xF0FF

# Windows VK codes accepted by input_tsuki.cpp (key_code) and the name key_icons.py understands.
VK = {}
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[ord(c)] = c
for i in range(1, 25):
    VK[0x70 + i - 1] = f"F{i}"
for i in range(10):
    VK[0x60 + i] = f"NUM{i}"
VK.update({0x20: "SPACE", 0x0D: "ENTER", 0x1B: "ESC", 0x09: "TAB", 0x08: "BACKSPACE",
           0x10: "SHIFT", 0xA0: "LSHIFT", 0xA1: "RSHIFT", 0x11: "CTRL", 0xA2: "LCTRL", 0xA3: "RCTRL",
           0x12: "ALT", 0xA4: "LALT", 0xA5: "RALT", 0x26: "UP", 0x28: "DOWN", 0x25: "LEFT", 0x27: "RIGHT",
           0x21: "PAGEUP", 0x22: "PAGEDOWN", 0x24: "HOME", 0x23: "END", 0x2D: "INSERT", 0x2E: "DELETE",
           0xBC: "COMMA", 0xBE: "PERIOD"})
NAME_TO_VK = {v: k for k, v in VK.items()}
NAME_TO_VK.update({"ESPACIO": 0x20, "RETURN": 0x0D, "INTRO": 0x0D, "ESCAPE": 0x1B, "CONTROL": 0x11,
                   "ARRIBA": 0x26, "ABAJO": 0x28, "IZQUIERDA": 0x25, "DERECHA": 0x27, "INICIO": 0x24,
                   "FIN": 0x23, "SUPR": 0x2E})


def align(cell):
    x, y, w, h = cell
    x0, y0 = x // 4 * 4, y // 4 * 4
    return x0, y0, -(-(x + w) // 4) * 4 - x0, -(-(y + h) // 4) * 4 - y0


def split(rect, parts, axis):
    """Split an aligned rect into `parts` along axis 0 (x) or 1 (y); cuts on multiples of 4."""
    x, y, w, h = rect
    size = w if axis == 0 else h
    cuts = [0] + [round(size * k / parts / 4) * 4 for k in range(1, parts)] + [size]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        out.append((x + a, y, b - a, h) if axis == 0 else (x, y + a, w, b - a))
    return out


# Layout of each cell: which Npad buttons it shows and how the cell is divided.
#   "one": a single key; "h2"/"v2": two keys side by side / stacked; "quad": 2x2; "cluster": arrows/WASD image.
CELL_BUTTONS = {
    0: ("dpad", ["UP", "RIGHT", "LEFT", "DOWN"]),  # in the order of the 2x2 parts (top left, top right, ...)
    1: ("h2", ["LEFT", "RIGHT"]), 2: ("v2", ["UP", "DOWN"]),
    3: ("one", ["UP"]), 4: ("one", ["DOWN"]), 5: ("one", ["RIGHT"]), 6: ("one", ["LEFT"]),
    7: ("one", ["A"]), 8: ("one", ["Y"]), 9: ("one", ["X"]), 10: ("one", ["B"]),
    11: ("v2", ["SUP", "SDOWN"]), 12: ("v2", ["RSUP", "RSDOWN"]), 13: ("one", ["L"]), 14: ("one", ["R"]),
    15: ("v2", ["L", "R"]), 16: ("one", ["STICKL"]), 17: ("one", ["PLUS"]), 18: ("one", ["MINUS"]),
    19: ("one", ["L"]), 20: ("one", ["R"]), 21: ("one", ["ZL"]), 22: ("one", ["ZR"]),
}


def build_slots():
    """slots: list of (aligned rect, cell rect); layout: cell -> {"full": id, "h2": [ids], "v2": [...], "quad": [...]}"""
    slots, layout = [], {}
    for idx, cell in CELLS.items():
        a = align(cell)
        kind = CELL_BUTTONS[idx][0]
        lay = {"full": len(slots)}
        slots.append((a, cell))
        subs = {"h2": [("h2", split(a, 2, 0))], "v2": [("v2", split(a, 2, 1))],
                "dpad": [("quad", [r2 for r1 in split(a, 2, 1) for r2 in split(r1, 2, 0)])]}.get(kind, [])
        for name, rects in subs:
            lay[name] = []
            for r in rects:
                lay[name].append(len(slots))
                slots.append((r, cell))
        layout[idx] = lay
    # aligned rects of different cells must not overlap (they are written independently)
    fulls = [slots[layout[i]["full"]][0] for i in layout]
    for i, p in enumerate(fulls):
        for q in fulls[i + 1:]:
            assert p[0] + p[2] <= q[0] or q[0] + q[2] <= p[0] or p[1] + p[3] <= q[1] or q[1] + q[3] <= p[1], (p, q)
    return slots, layout


def render_tile(icon, rect, cell, inset=1):
    """Fit the icon inside (slot rect ∩ cell rect), centred; returns an RGBA image of the slot size."""
    x, y, w, h = rect
    cx, cy, cw, ch = cell
    ix0, iy0 = max(x, cx), max(y, cy)
    ix1, iy1 = min(x + w, cx + cw), min(y + h, cy + ch)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if icon is None or ix1 - ix0 < 4 or iy1 - iy0 < 4:
        return canvas
    bb = icon.getchannel("A").getbbox()
    ic = fit(icon.crop(bb) if bb else icon, ix1 - ix0 - inset, iy1 - iy0 - inset)
    canvas.paste(ic, (ix0 - x + (ix1 - ix0 - ic.width) // 2, iy0 - y + (iy1 - iy0 - ic.height) // 2), ic)
    return canvas


def special_icon(key, icons):
    if key == K_UNASSIGNED:
        return labelled_key(icons, "–")
    if key == K_ARROWS:
        return Image.open(os.path.join(icons, "key-keyboard-arrows.png")).convert("RGBA")
    if key == K_WASD:
        return Image.open(os.path.join(icons, "key-keyboard-wasd.png")).convert("RGBA")
    return None


def build_library(icons, out_path):
    slots, layout = build_slots()
    full_ids = {lay["full"] for lay in layout.values()}
    dpad_full = layout[0]["full"]
    cache = {}

    def icon_for(key):
        if key not in cache:
            cache[key] = special_icon(key, icons) if key >= 0xF000 else key_icon(VK[key], icons, icons)
        return cache[key]

    tiles = []  # (slot, key, bytes)
    keys = sorted(VK) + [K_UNASSIGNED]
    for sid, (rect, cell) in enumerate(slots):
        todo = list(keys) + ([K_ARROWS, K_WASD] if sid == dpad_full else [])
        if sid in full_ids:
            todo.append(K_CLEAR)
        for key in todo:
            img = render_tile(None if key == K_CLEAR else icon_for(key), rect, cell)
            tiles.append((sid, key, bc7.encode_bc7(bc7.bleed_rgb(np.array(img)))))

    kinds = {"one": 0, "h2": 1, "v2": 2, "dpad": 3}
    cell_tab = b""
    for idx in sorted(CELL_BUTTONS):
        kind, btns = CELL_BUTTONS[idx]
        lay = layout[idx]
        parts = lay.get({"h2": "h2", "v2": "v2", "dpad": "quad"}.get(kind, ""), [])
        parts = list(parts) + [0xFFFF] * (4 - len(parts))
        names = b"".join(b.encode("ascii").ljust(8, b"\0") for b in btns) + b"\0" * 8 * (4 - len(btns))
        cell_tab += struct.pack("<BBH4H", kinds[kind], len(btns), lay["full"], *parts) + names
    head = struct.pack("<4sIHHHHI", b"NXHK", 1, ATLAS[0], ATLAS[1], len(CELL_BUTTONS), len(slots), len(tiles))
    slot_tab = b"".join(struct.pack("<HHHH", *rect) for rect, _ in slots)
    off = len(head) + len(cell_tab) + len(slot_tab) + 12 * len(tiles)
    tab, blob = [], []
    for sid, key, data in tiles:
        tab.append(struct.pack("<HHII", sid, key, off, len(data)))
        blob.append(data)
        off += len(data)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(head + cell_tab + slot_tab + b"".join(tab) + b"".join(blob))
    return slots, layout, {(s, k): d for s, k, d in tiles}


# ---------------------------------------------------------------- reference composition (what the game does)

DEFAULT_KEYS = {"A": [0x20, 0x0D], "UP": [0x26], "DOWN": [0x28], "LEFT": [0x25], "RIGHT": [0x27], "L": [0x41],
                "ZL": [0x52], "R": [0x46], "ZR": [0x11], "Y": [0x1B, 0x53, 0x4C, 0x43], "X": [0x09], "B": [0x08],
                "SUP": [0x21], "SDOWN": [0x22]}


def read_ini_keys(path):
    """[teclado] of an input.ini -> {npad button: [vk, ...]}; None if the file has no [teclado] section."""
    binds, sec, seen = {}, None, False
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.split(";")[0].strip()
            if not line:
                continue
            if line.startswith("["):
                sec = line.strip("[]").strip().lower()
                seen |= sec == "teclado"
                continue
            if sec != "teclado" or "=" not in line:
                continue
            btn, keys = (s.strip() for s in line.split("=", 1))
            for k in keys.split(","):
                vk = NAME_TO_VK.get(k.strip().upper())
                if vk:
                    binds.setdefault(btn.upper(), []).append(vk)
    return binds if seen else None


def plan(binds):
    """List of (slot id, key) writes for the given bindings; first key of each button wins."""
    _, layout = build_slots()
    first = {b: v[0] for b, v in binds.items() if v}
    get = lambda b: first.get(b, K_UNASSIGNED)  # noqa: E731
    writes = []
    for idx, (kind, btns) in CELL_BUTTONS.items():
        lay = layout[idx]
        writes.append((lay["full"], K_CLEAR))
        if kind == "one":
            writes[-1] = (lay["full"], get(btns[0]))
        elif kind in ("h2", "v2"):
            writes += list(zip(lay[kind], [get(b) for b in btns]))
        else:  # d-pad: a cluster image when the four directions are the arrows or WASD, else 2x2 keys
            ks = [get(b) for b in btns]
            if ks == [0x26, 0x27, 0x25, 0x28]:
                writes[-1] = (lay["full"], K_ARROWS)
            elif ks == [0x57, 0x44, 0x41, 0x53]:
                writes[-1] = (lay["full"], K_WASD)
            else:
                writes += list(zip(lay["quad"], ks))  # up, right / left, down
    return writes


def compose(slots, tiles, writes):
    """Decode-free composition: returns the atlas as BC7 bytes (row-major blocks), like the runtime."""
    bw, bh = ATLAS[0] // 4, ATLAS[1] // 4
    clear = bytes([0x40] + [0] * 15)  # BC7 mode 6, every endpoint 0: transparent black (as hud_glyphs.cpp)
    atlas = bytearray(clear * (bw * bh))
    for sid, key in writes:
        x, y, w, h = slots[sid][0]
        data = tiles[(sid, key)]
        tw = w // 4
        for r in range(h // 4):
            dst = ((y // 4 + r) * bw + x // 4) * 16
            atlas[dst:dst + tw * 16] = data[r * tw * 16:(r + 1) * tw * 16]
    return bytes(atlas)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--icons", required=True, help="keyboard-input-icons folder of the pack")
    p.add_argument("--out", required=True)
    p.add_argument("--preview-ini", help="input.ini to preview (default bindings if omitted or without [teclado])")
    p.add_argument("--preview-dir", help="write _preview_keyboard.png here")
    args = p.parse_args()
    slots, layout, tiles = build_library(args.icons, args.out)
    print(f"{len(slots)} slots, {len(tiles)} tiles, {os.path.getsize(args.out)} bytes -> {args.out}")
    if args.preview_dir:
        binds = (read_ini_keys(args.preview_ini) if args.preview_ini else None) or DEFAULT_KEYS
        data = compose(slots, tiles, plan(binds))
        try:
            import texture2ddecoder as t2d
        except ImportError:
            print("pip install texture2ddecoder to write the preview")
            return
        dec = np.frombuffer(t2d.decode_bc7(data, *ATLAS), np.uint8).reshape(ATLAS[1], ATLAS[0], 4)[..., [2, 1, 0, 3]]
        bg = Image.new("RGBA", ATLAS, (70, 70, 80, 255))
        bg.alpha_composite(Image.fromarray(np.ascontiguousarray(dec)))
        os.makedirs(args.preview_dir, exist_ok=True)
        bg.resize((ATLAS[0] * 2, ATLAS[1] * 2), Image.Resampling.NEAREST).save(os.path.join(args.preview_dir, "_preview_keyboard.png"))


if __name__ == "__main__":
    main()
