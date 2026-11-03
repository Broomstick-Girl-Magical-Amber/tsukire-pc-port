#!/usr/bin/env python3
"""ninja_times.py - where the time of a build went, from the .ninja_log of a build folder.

  python tools/dev/ninja_times.py framework/build/tsukihime_global/.ninja_log            # every entry in the log
  python tools/dev/ninja_times.py <.ninja_log> --offset <bytes>                           # only what one build appended
  python tools/dev/ninja_times.py <.ninja_log> --top 15                                   # the 15 slowest outputs

recomp_tsukihime.ps1 (step 9, AP_RECOMP_PARALLEL) calls it with --offset = the size of the log before `cmake --build`,
so only that build is summarized. Groups: the recompiled code of main and sdk (game_main / game_sdk), the runtime and
game code, and the link. "CPU" is the sum of the compile times; "wall" is the time of the whole build; CPU / wall is
how many compiles ran at once on average. Read only; never fails the build (exit code 0 with nothing to show).
"""
import argparse
import os
import sys


def group(path):
    p = path.replace("\\", "/").lower()
    if "/game_main.dir/" in p:
        return "recompiled main"
    if "/game_sdk.dir/" in p:
        return "recompiled sdk"
    if p.endswith((".exe", ".dll", ".lib", ".pdb", ".map")) and ".dir/" not in p:
        return "link / archive"
    return "runtime + game"


def read(log, offset):
    with open(log, "rb") as f:
        f.seek(offset)
        text = f.read().decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        rows.append((start, end, parts[3]))
    # ninja writes one line per output; a command with several outputs (an .obj and its .pdb, say) repeats the times
    # (counted once: same start, end and group)
    seen, out = set(), []
    for start, end, path in rows:
        key = (start, end, group(path))
        if key in seen:
            continue
        seen.add(key)
        out.append((start, end, path))
    return out


def fmt(ms):
    s = ms / 1000.0
    return f"{int(s // 60)}:{s % 60:04.1f}" if s >= 60 else f"{s:.1f} s"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--offset", type=int, default=0, help="bytes of the log to skip (its size before the build)")
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args()
    if not os.path.isfile(a.log):
        print(f"no {a.log}")
        return 0
    offset = max(0, a.offset)
    if offset > os.path.getsize(a.log):  # ninja recompacted the log at the start of the build: every entry left
        print("(.ninja_log was recompacted: the summary covers every output in it)")
        offset = 0
    rows = read(a.log, offset)
    if not rows:
        print("nothing was rebuilt")
        return 0
    wall = max(e for _, e, _ in rows) - min(s for s, _, _ in rows)
    cpu = sum(e - s for s, e, _ in rows)
    print(f"build: {len(rows)} outputs, wall {fmt(wall)}, CPU {fmt(cpu)} (on average {cpu / max(wall, 1):.1f} at once)")
    groups = {}
    for s, e, p in rows:
        g = groups.setdefault(group(p), [0, 0, 0])
        g[0] += 1
        g[1] += e - s
        g[2] = max(g[2], e - s)
    for name in ("recompiled main", "recompiled sdk", "runtime + game", "link / archive"):
        if name in groups:
            n, t, mx = groups[name]
            print(f"  {name:<16} {n:4} outputs  CPU {fmt(t):>8}  slowest {fmt(mx):>8}  ({100.0 * t / max(cpu, 1):.0f} % of CPU)")
    for s, e, p in sorted(rows, key=lambda r: r[0] - r[1])[:max(0, a.top)]:
        print(f"  {fmt(e - s):>8}  {p.replace(chr(92), '/').split('/')[-1]}  [{group(p)}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
