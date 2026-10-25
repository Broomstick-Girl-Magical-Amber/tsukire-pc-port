#!/usr/bin/env python3
"""find_nvn_format_table.py - Looks for the NVN driver format table inside sdk.bin (version 2).

The runtime (nvn_texture_layout.cpp) reads from that table the block size and the bytes per block of each NVNformat.
In Wonder (SDK 16.2) it is at sdk+0xd34198, with 0x30-byte entries (word 2: block w/h, word 4: bytes).
In another SDK it may be somewhere else and with another entry size, so neither is assumed here.

It looks for the bytes-per-pixel sequence of the first formats, whose order is fixed in NVN:
  1-4  R8   (Unorm, Snorm, Uint, Sint)         1 byte
  5-9  R16  (Float, Unorm, Snorm, Uint, Sint)  2 bytes
  10-12 R32 (Float, Uint, Sint)                4 bytes
  13-16 RG8                                    2 bytes
  17-21 RG16                                   4 bytes
  22-24 RG32                                   8 bytes
with any spacing between entries (4..0x80) and values in bytes or in bits.

Usage: python find_nvn_format_table.py <root>\\data\\tsuki\\analysis\\sdk.bin
"""
import re
import struct
import sys

BPP = [1] * 4 + [2] * 5 + [4] * 3 + [2] * 4 + [4] * 5 + [8] * 3  # formats 1..24
SHORT = 12  # with only R8/R16/R32 it is already very unlikely by chance


def pattern(seq, stride, scale):
    gap = b".{%d}" % (stride - 1)
    return re.compile(gap.join(re.escape(bytes([v * scale])) for v in seq), re.DOTALL)


def main():
    img = open(sys.argv[1], "rb").read()
    print(f"sdk.bin: {len(img):#x} bytes")
    hits = []
    for scale, unit in ((1, "bytes"), (8, "bits")):
        for stride in range(4, 0x84, 4):
            full = pattern(BPP, stride, scale)
            short = pattern(BPP[:SHORT], stride, scale)
            for m in short.finditer(img):
                q = m.start()  # "bytes" field of format 1
                n = len(BPP) if full.match(img, q) else SHORT
                hits.append((n, stride, scale, unit, q))
    if not hits:
        sys.exit("No matches: SDK 11 probably does not have this table as data.")

    hits.sort(key=lambda h: (-h[0], h[1]))
    print("\nMatches (best first):")
    for n, stride, scale, unit, q in hits[:10]:
        # q is the bytes field of format 1; entry 1 starts somewhere in [q-stride+1, q].
        print(f"  stride {stride:#x}, {unit}: bytes field of format 1 at sdk+{q:#x}  ({n}/{len(BPP)} formats match)")

    n, stride, scale, unit, q = hits[0]
    print()
    if stride == 0x30 and scale == 1:
        base = q - 0x30 - 0x10  # same layout as Wonder: bytes in word 4 (+0x10)
        w2 = struct.unpack_from("<I", img, base + 0x30 + 8)[0] if base >= 0 else 0
        if (w2 & 0xFFFF) == 0x0101:
            # How many consecutive entries look like valid formats (SDK 11 may have fewer than 16).
            ok_dim = (1, 4, 5, 6, 8, 10, 12)
            count = 1
            while base + (count + 1) * 0x30 <= len(img):
                o = base + count * 0x30
                w2 = struct.unpack_from("<I", img, o + 8)[0]
                w4 = struct.unpack_from("<I", img, o + 16)[0]
                if not ((w2 & 0xFF) in ok_dim and ((w2 >> 8) & 0xFF) in ok_dim and (w4 & 0xFF) in (1, 2, 3, 4, 6, 8, 12, 16)):
                    break
                count += 1
            print(f"Same layout as in Wonder. TABLE: sdk+{base:#x}, {count:#x} entries")
            print(f"In games\\tsukihime\\game.cpp, at the end of kInfo:  {base:#x}, {count:#x}")
            return
        print(f"stride 0x30 but the 1x1 block is not in word 2 (word2 = {w2:#x}): different layout.")
    print("The layout is not Wonder's: the runtime reader has to be adapted to it.")


if __name__ == "__main__":
    main()