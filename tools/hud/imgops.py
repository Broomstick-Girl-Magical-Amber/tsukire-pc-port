"""imgops.py - the few image operations the HUD asset scripts need, with numpy and Pillow only (no scipy),
so they run with the project's venv (tools/requirements.txt)."""
from collections import deque

import numpy as np


def fill_holes(mask):
    """Boolean mask with its enclosed holes filled: the background not 4-connected to the border becomes True."""
    m = np.asarray(mask, bool)
    reach = np.zeros_like(m)
    reach[0, :], reach[-1, :], reach[:, 0], reach[:, -1] = ~m[0, :], ~m[-1, :], ~m[:, 0], ~m[:, -1]
    while True:
        grown = reach.copy()
        grown[1:, :] |= reach[:-1, :]
        grown[:-1, :] |= reach[1:, :]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= ~m
        if (grown == reach).all():
            return ~reach
        reach = grown


def largest_component(mask):
    """Boolean mask of the largest 4-connected component of `mask` (for small icons: plain BFS)."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    seen = np.zeros_like(m)
    best = []
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        comp, q = [], deque([(y0, x0)])
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            comp.append((y, x))
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        if len(comp) > len(best):
            best = comp
    out = np.zeros_like(m)
    if best:
        ys, xs = zip(*best)
        out[list(ys), list(xs)] = True
    return out


def bleed_rgb(rgba, min_alpha=1, rounds=64):
    """Spread the colour of opaque pixels into the (semi)transparent ones around them, one ring per round; what is
    still unreached gets the mean opaque colour. Alpha is unchanged. Keeps filtering and BC7 from pulling dark fringes
    out of invisible pixels."""
    a = np.array(rgba, np.uint8, copy=True)
    known = a[..., 3] >= min_alpha
    if not known.any():
        return a
    rgb = a[..., :3].astype(np.float32)
    rgb[~known] = 0
    for _ in range(rounds):
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
        grow = todo & (cnt > 0)
        if not grow.any():
            break
        rgb[grow] = acc[grow] / cnt[grow][:, None]
        known = known | grow
    if (~known).any():
        rgb[~known] = rgb[a[..., 3] >= min_alpha].mean(axis=0)
    out = a.copy()
    m = a[..., 3] < min_alpha
    out[m, :3] = np.clip(rgb[m] + 0.5, 0, 255).astype(np.uint8)
    return out
