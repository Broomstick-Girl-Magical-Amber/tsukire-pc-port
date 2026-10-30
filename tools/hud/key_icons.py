#!/usr/bin/env python3
"""Map input.ini key / mouse names to keyboard icon images.

Names are the ones accepted by input_tsuki.cpp (upper-cased). Keys that have no
icon in the pack (F13-F24, Num0-Num9, ...) get a generic key cap with the name
written on it. All keyboard icons use the FILLED variants of the pack.

Usage (self-test, writes a contact sheet):
  python tools/hud/key_icons.py --icons utils/KeyboardButtons/keyboard-input-icons \
      --mouse utils/KeyboardButtons/mouse-input-icons --out out_dir
"""
import argparse
import functools
import os

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imgops import fill_holes  # noqa: E402

ALIASES = {
    "SPACE": "space", "ESPACIO": "space", "ENTER": "enter", "RETURN": "enter", "INTRO": "enter",
    "ESC": "esc", "ESCAPE": "esc", "TAB": "tab", "BACKSPACE": "backspace",
    "SHIFT": "shift", "LSHIFT": "shift", "RSHIFT": "shift",
    "CTRL": "ctrl", "CONTROL": "ctrl", "LCTRL": "ctrl", "RCTRL": "ctrl",
    "ALT": "alt", "LALT": "alt", "RALT": "alt",
    "UP": "arrow-up", "ARRIBA": "arrow-up", "DOWN": "arrow-down", "ABAJO": "arrow-down",
    "LEFT": "arrow-left", "IZQUIERDA": "arrow-left", "RIGHT": "arrow-right", "DERECHA": "arrow-right",
    "PAGEUP": "pgup", "PAGEDOWN": "pgdn", "HOME": "home", "INICIO": "home", "END": "end", "FIN": "end",
    "INSERT": "ins", "DELETE": "del", "SUPR": "del", "COMMA": "comma", "PERIOD": "dot",
}
MOUSE = {"DERECHO": "right", "RIGHT": "right", "MEDIO": "middle", "MIDDLE": "middle",
         "RUEDAARRIBA": "wheel-up", "WHEELUP": "wheel-up", "RUEDAABAJO": "wheel-down", "WHEELDOWN": "wheel-down"}
LABELS = {"INSERT": "Ins", "DELETE": "Del", "SUPR": "Del"}
# The repo's own font (Noto Sans JP, OFL: resources/fonts), so every PC draws the same labels; it is regular weight,
# so the label gets a stroke to look bold like the pack's.
FONT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "resources",
                                     "fonts", "shared_0.ttf"))


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        sys.exit(f"ERROR: {FONT} is missing (it comes with the repo)")


@functools.lru_cache(maxsize=4)
def _blank_key(icons_dir):
    """Key cap without a label: the 'a' key with its letter painted over."""
    im = Image.open(os.path.join(icons_dir, "key-a.png")).convert("RGBA")
    a = np.array(im)
    body = fill_holes(a[..., 3] > 0)  # the letter is a cut-out of the key: fill it back
    a[body] = (255, 255, 255, 255)
    a[~body & (a[..., 3] > 0), :3] = 255
    return Image.fromarray(a)


def blank_key(icons_dir):
    return _blank_key(icons_dir).copy()


def labelled_key(icons_dir, text):
    im = blank_key(icons_dir)
    d = ImageDraw.Draw(im)
    size = 150
    while size > 24:
        f = _font(size)
        stroke = max(1, size // 32)
        l, t, r, b = d.textbbox((0, 0), text, font=f, stroke_width=stroke)
        if r - l <= 250:
            break
        size -= 6
    cx, cy = 256, 248
    # the pack's labels are cut-outs (transparent), so the label is punched out of the key the same way
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=f, fill=255, stroke_width=stroke,
                              stroke_fill=255)
    a = np.array(im)
    a[..., 3] = (a[..., 3].astype(np.int32) * (255 - np.array(mask, np.int32)) // 255).astype(np.uint8)
    return Image.fromarray(a)


def wheel_icon(mouse_dir, up):
    """mouse-move-vert with the arrow of the other direction dimmed."""
    a = np.array(Image.open(os.path.join(mouse_dir, "mouse-move-vert.png")).convert("RGBA"))
    dim = (slice(274, 364), slice(401, 497)) if up else (slice(136, 226), slice(401, 497))
    a[dim[0], dim[1], 3] = (a[dim[0], dim[1], 3] * 0.3).astype(np.uint8)
    return Image.fromarray(a)


def key_icon(name, icons_dir, mouse_dir, is_mouse=False):
    """512x512 RGBA icon for an input.ini name. is_mouse = the name comes from the [mouse] section
    (Right is a key in [teclado] but the right button in [mouse])."""
    n = name.strip().upper()
    if is_mouse:
        if n not in MOUSE:
            raise KeyError(name)
        m = MOUSE[n]
        if m.startswith("wheel"):
            return wheel_icon(mouse_dir, m == "wheel-up")
        return Image.open(os.path.join(mouse_dir, f"mouse-{m}.png")).convert("RGBA")
    base = ALIASES.get(n)
    if base is None and len(n) == 1 and n.isalnum():
        base = n.lower()
    if base is None and n[0] == "F" and n[1:].isdigit() and 1 <= int(n[1:]) <= 12:
        base = n.lower()
    if base is not None:
        path = os.path.join(icons_dir, f"key-{base}.png")
        if os.path.exists(path):
            return Image.open(path).convert("RGBA")
    return labelled_key(icons_dir, LABELS.get(n, n.title() if n.startswith("NUM") else n))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--icons", required=True)
    p.add_argument("--mouse", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    names = ["A", "S", "L", "C", "F", "SPACE", "ENTER", "ESCAPE", "TAB", "BACKSPACE", "CTRL", "UP", "DOWN", "LEFT",
             "RIGHT", "PAGEUP", "PAGEDOWN", "F5", "F13", "F24", "NUM5", "NUM0", "INSERT", "COMMA", "DERECHO", "MEDIO",
             "RUEDAARRIBA", "RUEDAABAJO"]
    cols = 7
    sheet = Image.new("RGB", (cols * 130, ((len(names) + cols - 1) // cols) * 130), (60, 60, 70))
    for i, n in enumerate(names):
        im = key_icon(n, args.icons, args.mouse, is_mouse=n in ("DERECHO", "MEDIO", "RUEDAARRIBA", "RUEDAABAJO")).resize((120, 120), Image.Resampling.LANCZOS)
        sheet.paste(im, ((i % cols) * 130 + 5, (i // cols) * 130 + 5), im)
    sheet.save(os.path.join(args.out, "_keys_test.png"))


if __name__ == "__main__":
    main()
