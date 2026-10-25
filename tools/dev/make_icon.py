"""make_icon.py - converts an image (PNG/JPG, ideally square and 256 px or more) into a Windows .ico
with the sizes 16, 24, 32, 48, 64, 128 and 256 px.

Usage:  python make_icon.py image.png [output.ico]   (default games\\tsukihime_common\\tsukihime.ico)
        --crop   crops to the central square instead of padding with transparency
Requires Pillow (pip install pillow).
"""
import argparse
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is missing: python -m pip install pillow")

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("output", nargs="?", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework" / "games" / "tsukihime_common" / "tsukihime.ico"))
    ap.add_argument("--crop", action="store_true")
    a = ap.parse_args()
    img = Image.open(a.image).convert("RGBA")
    w, h = img.size
    if w != h:
        if a.crop:
            s = min(w, h)
            img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
        else:
            s = max(w, h)
            canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            canvas.paste(img, ((s - w) // 2, (s - h) // 2))
            img = canvas
    if img.size[0] < 256:
        print(f"Warning: the image is {img.size[0]} px; the big icon will look blurry.")
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(a.output, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"{a.output}: {', '.join(str(s) for s in SIZES)} px")


if __name__ == "__main__":
    main()
