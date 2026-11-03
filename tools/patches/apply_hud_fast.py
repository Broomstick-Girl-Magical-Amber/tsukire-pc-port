"""apply_hud_fast.py - faster HUD icon build (tools/hud), same output bytes. Marker AP_HUD_FAST.

Measured on the repo's own icon packs (Linux, 2 vCPU; the Windows VM measured ~1 min for the keyboard tiles and ~30 s
per controls table):
- keyboard_tiles.bin: ~50 s -> a few seconds. 3614 tiles were rendered and BC7-encoded one by one, but a tile depends
  only on the key and on the slot geometry, and many slots share it: each distinct tile is now rendered once and all
  of them are encoded in ONE BC7 pass (the per-call overhead of the encoder was most of the time).
- bc7._indices: one palette entry at a time instead of one (blocks x 16 x 16 x 4) float array (about 1 GB for a
  63720-block controls table, so ~4 GB with four tables built in parallel). Same arithmetic per element, so the same
  indices and the same bytes, with much less memory traffic.
- bc7.encode_bc7_many(images): several images in one pass (every block is encoded on its own: same bytes as
  encode_bc7 image by image).
- build_hud_assets.py: keyboard_tiles.bin, help_key_xbox.bc7 / help_key_ps.bc7 and manual_<hash>.bin do not depend on
  the edition (only on utils/ and on the table's own pixels, whose hash is in the name): when the other edition already
  has an up-to-date copy it is copied instead of built again. The "about half a minute" message is gone.
Checked: keyboard_tiles.bin, both pad atlases, a 1416x720 synthetic controls table and random images give the same
bytes as before.

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_hud_fast.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_HUD_FAST"
BC7 = "../tools/hud/bc7.py"
KBD = "../tools/hud/build_keyboard_tiles.py"
ASSETS = "../tools/hud/build_hud_assets.py"
HELP = "../tools/hud/build_help_key.py"
IMGOPS = "../tools/hud/imgops.py"

EDITS = [
    # ------------------------------------------------------------------------------------------ bc7.py
    (BC7,
     """    pal = pal.astype(np.float32)
    d = (((px[:, :, None, :] - pal[:, None, :, :]) ** 2) * wt[:, :, None, :]).sum(axis=3)  # (n,16px,16idx)
    return d.argmin(axis=2).astype(np.int32), d.min(axis=2).sum(axis=1)
""",
     """    pal = pal.astype(np.float32)
    # AP_HUD_FAST: one palette entry at a time instead of an (n,16px,16idx,4) array (about 1 GB for a 63720-block
    # table). Same arithmetic per element, the 4 channels added in the order numpy's sum used (checked equal with the
    # pinned numpy), and the first minimum wins, as argmin: same indices and errors.
    best_d, best_i = None, None
    for k in range(16):
        t = px - pal[:, None, k, :]
        np.multiply(t, t, out=t)
        t *= wt
        dk = t[..., 0] + t[..., 1] + t[..., 2] + t[..., 3]  # (n,16px)
        if best_d is None:
            best_d, best_i = dk, np.zeros(dk.shape, np.int32)
        else:
            m = dk < best_d
            best_d = np.where(m, dk, best_d)
            best_i[m] = k
    return best_i, best_d.sum(axis=1)
"""),
    (BC7,
     """def encode_bc7(rgba):
    px, (bh, bw) = _blocks(np.asarray(rgba, np.uint8))
    n = len(px)
""",
     """def encode_bc7_many(images):
    \"\"\"AP_HUD_FAST: several images in one pass; every block is encoded on its own, so each item is the same bytes
    encode_bc7 gives for that image alone. Returns a list of bytes.\"\"\"
    parts = [_blocks(np.asarray(im, np.uint8))[0] for im in images]
    if not parts:
        return []
    data = _encode_px(np.concatenate(parts))
    out, pos = [], 0
    for p in parts:
        out.append(data[pos * 16:(pos + len(p)) * 16])
        pos += len(p)
    return out


def encode_bc7(rgba):
    return _encode_px(_blocks(np.asarray(rgba, np.uint8))[0])


def _encode_px(px):
    \"\"\"(n,16,4) float32 pixel blocks -> bytes of n BC7 blocks (AP_HUD_FAST: the body of the former encode_bc7).\"\"\"
    n = len(px)
"""),
    # ------------------------------------------------------------------------------------------ build_keyboard_tiles.py
    (KBD,
     """    tiles = []  # (slot, key, bytes)
    keys = sorted(VK) + [K_UNASSIGNED]
    for sid, (rect, cell) in enumerate(slots):
        todo = list(keys) + ([K_ARROWS, K_WASD] if sid == dpad_full else [])
        if sid in full_ids:
            todo.append(K_CLEAR)
        for key in todo:
            img = render_tile(None if key == K_CLEAR else icon_for(key), rect, cell)
            tiles.append((sid, key, bc7.encode_bc7(bc7.bleed_rgb(np.array(img)))))
""",
     """    tiles = []  # (slot, key, bytes)
    keys = sorted(VK) + [K_UNASSIGNED]
    # AP_HUD_FAST: a tile depends only on the key and on the slot geometry (what render_tile reads of rect and cell),
    # and many slots share it: each distinct tile is rendered once, and all are encoded in ONE BC7 pass (same bytes).
    memo, uniq = {}, []
    for sid, (rect, cell) in enumerate(slots):
        todo = list(keys) + ([K_ARROWS, K_WASD] if sid == dpad_full else [])
        if sid in full_ids:
            todo.append(K_CLEAR)
        for key in todo:
            gk = (key,) + tile_geometry(rect, cell)
            if gk not in memo:
                memo[gk] = len(uniq)
                img = render_tile(None if key == K_CLEAR else icon_for(key), rect, cell)
                uniq.append(bc7.bleed_rgb(np.array(img)))
            tiles.append((sid, key, memo[gk]))
    encoded = bc7.encode_bc7_many(uniq)
    tiles = [(sid, key, encoded[i]) for sid, key, i in tiles]
"""),
    (KBD,
     """    bb = icon.getchannel("A").getbbox()
    ic = fit(icon.crop(bb) if bb else icon, ix1 - ix0 - inset, iy1 - iy0 - inset)
""",
     """    ic = fit(_cropped(icon), ix1 - ix0 - inset, iy1 - iy0 - inset)  # AP_HUD_FAST: the same crop object per icon
"""),
    (KBD,
     """def special_icon(key, icons):
""",
     """_CROPS = {}  # AP_HUD_FAST: id(icon) -> (icon, icon cropped to its alpha box): fit() reuses its work per source image


def _cropped(icon):
    hit = _CROPS.get(id(icon))
    if hit is None or hit[0] is not icon:
        bb = icon.getchannel("A").getbbox()
        hit = _CROPS[id(icon)] = (icon, icon.crop(bb) if bb else icon)
    return hit[1]


def tile_geometry(rect, cell):
    \"\"\"AP_HUD_FAST: everything render_tile uses of (rect, cell): slot size and the icon box inside it.\"\"\"
    x, y, w, h = rect
    cx, cy, cw, ch = cell
    ix0, iy0 = max(x, cx), max(y, cy)
    ix1, iy1 = min(x + w, cx + cw), min(y + h, cy + ch)
    return (w, h, ix0 - x, iy0 - y, ix1 - ix0, iy1 - iy0)


def special_icon(key, icons):
"""),
    # ------------------------------------------------------------------------------------------ build_help_key.py
    (HELP,
     """    s = min((w - margin) / im.width, (h - margin) / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    a = np.array(im).astype(np.float64)
    pm = a[..., :3] * (a[..., 3:4] / 255.0)  # premultiplied resampling: no dark/light halos
    chans = [Image.fromarray(np.clip(pm[..., i], 0, 255).astype(np.uint8)).resize((nw, nh), Image.Resampling.LANCZOS) for i in range(3)]
    a0 = np.array(Image.fromarray(a[..., 3].astype(np.uint8)).resize((nw, nh), Image.Resampling.LANCZOS)).astype(np.float64) / 255.0
    pmr = np.stack([np.array(c).astype(np.float64) for c in chans], axis=2)
""",
     """    s = min((w - margin) / im.width, (h - margin) / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    # AP_HUD_FAST: the 3 premultiplied channels and alpha resampled in ONE call: RGBX = 4 independent bands that
    # Pillow does not premultiply, byte for byte the same as one call per channel (checked), 4x fewer resizes; and
    # the premultiplied source is made once per source image (the keyboard tiles fit each key to ~40 sizes).
    r4 = np.array(_fit_source(im).resize((nw, nh), Image.Resampling.LANCZOS))
    a0 = r4[..., 3].astype(np.float64) / 255.0
    pmr = r4[..., :3].astype(np.float64)
"""),
    (HELP,
     """def fit(im, w, h, margin=0):
""",
     """_FIT_SOURCES = {}  # AP_HUD_FAST: id(image) -> (image, premultiplied RGBX source); the image is kept so its id stays its own


def _fit_source(im):
    hit = _FIT_SOURCES.get(id(im))
    if hit is not None and hit[0] is im:
        return hit[1]
    a = np.array(im).astype(np.float64)
    pm = a[..., :3] * (a[..., 3:4] / 255.0)  # premultiplied resampling: no dark/light halos
    four = np.ascontiguousarray(np.dstack([np.clip(pm, 0, 255).astype(np.uint8), a[..., 3].astype(np.uint8)]))
    src = Image.frombytes("RGBX", (four.shape[1], four.shape[0]), four.tobytes())
    if len(_FIT_SOURCES) >= 256:
        _FIT_SOURCES.clear()
    _FIT_SOURCES[id(im)] = (im, src)
    return src


def fit(im, w, h, margin=0):
"""),
    # ------------------------------------------------------------------------------------------ imgops.py
    (IMGOPS,
     """    for _ in range(rounds):
        todo = ~known
        if not todo.any():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape, np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                k = np.roll(np.roll(known, dy, 0), dx, 1)
                c = np.roll(np.roll(rgb, dy, 0), dx, 1)
                # no wrap-around at the edges
                if dy == 1: k[0, :] = False
                if dy == -1: k[-1, :] = False
                if dx == 1: k[:, 0] = False
                if dx == -1: k[:, -1] = False
                acc += c * k[..., None]
                cnt += k
""",
     """    h, w = known.shape
    for _ in range(rounds):
        todo = ~known
        if not todo.any():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape, np.float32)
        # AP_HUD_FAST: neighbours as views of a copy padded with "unknown" instead of two np.roll copies per
        # neighbour (no wrap-around at the edges either): same values added in the same order.
        kp = np.pad(known, 1)
        cp = np.pad(rgb, ((1, 1), (1, 1), (0, 0)))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                k = kp[1 - dy:1 - dy + h, 1 - dx:1 - dx + w]
                c = cp[1 - dy:1 - dy + h, 1 - dx:1 - dx + w]
                acc += c * k[..., None]
                cnt += k
"""),
    # ------------------------------------------------------------------------------------------ build_hud_assets.py
    (ASSETS,
     """def scripts(*names):
    return [os.path.join(HERE, n) for n in names]
""",
     """def scripts(*names):
    return [os.path.join(HERE, n) for n in names]


def from_other_edition(root, out, inputs, indent="  "):
    \"\"\"AP_HUD_FAST: keyboard_tiles.bin, help_key_<pad>.bc7 and manual_<hash>.bin do not depend on the edition: if the
    other edition has an up-to-date copy, it is copied (the date is kept, so `newer` stays true). True if copied.\"\"\"
    import shutil
    for d in DATA_DIRS.values():
        other = os.path.join(root, d, "hud", os.path.basename(out))
        if os.path.normcase(os.path.abspath(other)) == os.path.normcase(os.path.abspath(out)) or not newer(other, inputs):
            continue
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(other, out + ".tmp")
            os.replace(out + ".tmp", out)
        except OSError as e:
            print(f"{indent}{os.path.basename(out)}: could not copy it from {d} ({e}): building it")
            return False
        print(f"{indent}{os.path.basename(out)}: copied from {d} (same inputs)")
        return True
    return False
"""),
    (ASSETS,
     """    if not force and newer(out, inputs):
        print(f"    {os.path.basename(out)}: up to date")
        return
    print(f"    {os.path.basename(out)}: building (about half a minute)...")
""",
     """    if not force and newer(out, inputs):
        print(f"    {os.path.basename(out)}: up to date")
        return
    if not force and from_other_edition(root, out, inputs, "    "):  # AP_HUD_FAST
        return
    print(f"    {os.path.basename(out)}: building...")
"""),
    (ASSETS,
     """    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    table = {"xbox": build_help_key.XBOX, "ps": build_help_key.PS}[dev]
""",
     """    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    if not force and from_other_edition(root, out, inputs):  # AP_HUD_FAST
        return
    table = {"xbox": build_help_key.XBOX, "ps": build_help_key.PS}[dev]
"""),
    (ASSETS,
     """    data = bc7.encode_bc7(bc7.bleed_rgb(np.array(atlas)))
    with open(out, "wb") as f:
""",
     """    data = bc7.encode_bc7(bc7.bleed_rgb(np.array(atlas)))
    os.makedirs(os.path.dirname(out), exist_ok=True)  # AP_HUD_FAST: the folder may not exist yet (no manifest written)
    with open(out, "wb") as f:
"""),
    (ASSETS,
     """    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    print("  keyboard_tiles.bin: encoding (about a minute)...")
""",
     """    if not force and newer(out, inputs):
        print(f"  {os.path.basename(out)}: up to date")
        return
    if not force and from_other_edition(root, out, inputs):  # AP_HUD_FAST
        return
    print("  keyboard_tiles.bin: encoding...")
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
