"""bc7.py - small BC7 encoder (mode 6 only) for the HUD icon textures, numpy only.

Mode 6: one subset, RGBA endpoints (7 bits + 1 shared p-bit each), 4-bit indices. Fine for flat icons and gradients.
Public: encode_bc7(rgba: HxWx4 uint8) -> bytes of 16-byte blocks, row-major (W and H padded to a multiple of 4 by
edge replication). decode with any BC7 decoder.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_W = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], np.int32)
_CW = np.array([1.0, 1.0, 1.0, 8.0], np.float32)  # channel weights: alpha errors show as halos/ghost quads


def _blocks(img):
    h, w = img.shape[:2]
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="edge")
    h, w = img.shape[:2]
    b = img.reshape(h // 4, 4, w // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    return b.astype(np.float32), (h // 4, w // 4)


def _quant(e):
    """e: (n,4) float endpoint -> (q7 (n,4) int, p (n,) int, rec (n,4) float), best p-bit by error."""
    t = np.clip(e, 0, 255)
    best = None
    for p in (0, 1):
        q = np.clip(np.rint((t - p) / 2.0), 0, 127)
        rec = q * 2 + p
        err = (((rec - t) ** 2) * _CW).sum(axis=1)
        if best is None:
            best = [q, np.full(len(t), p), rec, err]
        else:
            m = err < best[3]
            best[0] = np.where(m[:, None], q, best[0])
            best[1] = np.where(m, p, best[1])
            best[2] = np.where(m[:, None], rec, best[2])
            best[3] = np.where(m, err, best[3])
    return best[0].astype(np.int32), best[1].astype(np.int32), best[2]


def _indices(px, e0, e1, wt):
    """px (n,16,4); e0,e1 (n,4) 8-bit endpoints; wt (n,16,4) error weights -> idx (n,16), weighted error (n,)."""
    pal = ((64 - _W)[None, :, None] * e0[:, None, :] + _W[None, :, None] * e1[:, None, :] + 32) // 64  # (n,16,4)
    pal = pal.astype(np.float32)
    d = (((px[:, :, None, :] - pal[:, None, :, :]) ** 2) * wt[:, :, None, :]).sum(axis=3)  # (n,16px,16idx)
    return d.argmin(axis=2).astype(np.int32), d.min(axis=2).sum(axis=1)


def bleed_rgb(rgba, min_alpha=1):
    """Colour of nearby opaque pixels into the transparent ones (imgops.bleed_rgb). Alpha is unchanged."""
    import imgops
    return imgops.bleed_rgb(rgba, min_alpha)


def encode_bc7(rgba):
    px, (bh, bw) = _blocks(np.asarray(rgba, np.uint8))
    n = len(px)
    # Error weights per pixel and channel: colour counts as much as the pixel is visible (premultiplied error, so
    # invisible pixels do not steer the endpoints), alpha always and more (alpha errors show as halos and ghost quads).
    wt = np.empty_like(px)
    wt[..., :3] = np.maximum(px[..., 3:4] / 255.0, 0.02)
    wt[..., 3] = _CW[3]
    sw_ = np.sqrt(wt)
    mean = (px * wt).sum(axis=1, keepdims=True) / wt.sum(axis=1, keepdims=True)
    c = (px - mean) * sw_
    # principal axis per block (power iteration on the 4x4 covariance)
    cov = np.einsum("npi,npj->nij", c, c)
    v = np.ones((n, 4), np.float32)
    for _ in range(6):
        v = np.einsum("nij,nj->ni", cov, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
    proj = np.einsum("npi,ni->np", c, v)
    lo = px[np.arange(n), proj.argmin(axis=1)]
    hi = px[np.arange(n), proj.argmax(axis=1)]
    e0, e1 = lo.copy(), hi.copy()
    amin, amax = px[:, :, 3].min(axis=1), px[:, :, 3].max(axis=1)
    const_a = amin == amax  # fully transparent / fully opaque blocks keep their exact alpha

    def pin(a0, a1):
        a0[const_a, 3] = amin[const_a]
        a1[const_a, 3] = amin[const_a]
        return a0, a1

    e0, e1 = pin(e0, e1)
    best_err = np.full(n, np.inf)
    best = [None] * 5
    for it in range(4):
        q0, p0, r0 = _quant(e0)
        q1, p1, r1 = _quant(e1)
        idx, err = _indices(px, r0.astype(np.int32), r1.astype(np.int32), wt)
        better = err < best_err
        best_err = np.where(better, err, best_err)
        cur = [q0, p0, q1, p1, idx]
        for k in range(5):
            best[k] = cur[k] if best[k] is None else np.where(better.reshape((-1,) + (1,) * (cur[k].ndim - 1)), cur[k], best[k])
        # least-squares refinement of the endpoints from the indices
        w = (_W[idx] / 64.0).astype(np.float32)[..., None]  # (n,16,1)
        a = (wt * (1 - w) ** 2).sum(1); b = (wt * (1 - w) * w).sum(1); d = (wt * w ** 2).sum(1)  # (n,4) per channel
        det = a * d - b * b
        ok = np.abs(det) > 1e-6
        sx = (wt * (1 - w) * px).sum(1)
        sy = (wt * w * px).sum(1)
        safe = np.where(ok, det, 1)
        ne0 = np.where(ok, (d * sx - b * sy) / safe, e0)
        ne1 = np.where(ok, (a * sy - b * sx) / safe, e1)
        e0, e1 = pin(np.clip(ne0, 0, 255), np.clip(ne1, 0, 255))
    q0, p0, q1, p1, idx = best
    # pixel 0 index must have its MSB clear: swap endpoints and invert indices
    sw = idx[:, 0] >= 8
    q0s = np.where(sw[:, None], q1, q0); q1s = np.where(sw[:, None], q0, q1)
    p0s = np.where(sw, p1, p0); p1s = np.where(sw, p0, p1)
    idxs = np.where(sw[:, None], 15 - idx, idx)
    out = np.zeros((n, 16), np.uint8)
    # pack as one 128-bit little-endian integer per block, using two uint64
    lo64 = np.zeros(n, np.uint64); hi64 = np.zeros(n, np.uint64)
    pos = 0
    def put(val, bits):
        nonlocal pos
        val = val.astype(np.uint64)
        for k in range(bits):
            bit = (val >> np.uint64(k)) & np.uint64(1)
            gpos = pos + k
            if gpos < 64: lo64[:] |= bit << np.uint64(gpos)
            else: hi64[:] |= bit << np.uint64(gpos - 64)
        pos += bits
    put(np.full(n, 0x40), 7)  # mode 6
    for ch in range(4):
        put(q0s[:, ch], 7); put(q1s[:, ch], 7)
    put(p0s, 1); put(p1s, 1)
    put(idxs[:, 0], 3)
    for k in range(1, 16): put(idxs[:, k], 4)
    assert pos == 128
    out[:, :8] = lo64.view(np.uint8).reshape(n, 8)
    out[:, 8:] = hi64.view(np.uint8).reshape(n, 8)
    return out.tobytes()


if __name__ == "__main__":
    import argparse
    from PIL import Image

    ap = argparse.ArgumentParser(description="Encode a PNG to raw BC7 blocks (mode 6, row-major, no header).")
    ap.add_argument("png")
    ap.add_argument("out")
    ap.add_argument("--no-bleed", action="store_true")
    args = ap.parse_args()
    img = np.array(Image.open(args.png).convert("RGBA"))
    if not args.no_bleed:
        img = bleed_rgb(img)
    with open(args.out, "wb") as f:
        f.write(encode_bc7(img))
