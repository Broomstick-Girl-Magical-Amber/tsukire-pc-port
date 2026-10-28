"""tsuki_shaders_offline.py - Tsukihime shaders without running the game (used by recomp_tsukihime.ps1).

Gathers every Maxwell code block the game can pass to nvnProgramSetShaders:
  - romfs\\shader\\*.bnsh: with tools\\maxwell\\containers.py of the framework (BNSH -> variations -> stages); --romfs-shader
    can be given several times (the base romfs and the layers of mods\\, recomp_tsukihime.ps1): blocks are joined by hash
  - the programs embedded in main: with tools\\maxwell\\exefs_shaders.find over analysis\\main.bin
and names them like the runtime does (nvn_shaders.cpp): sha1(code[0:ctrl+0x704])[:16].

Measured 2026-10-18 against the 69 dumped with NX_DUMP_SHADER_MISSES from the japan edition: 69/69 found, code
identical byte for byte; control identical in 65; in 4 from main exefs_shaders pairs another control section with
the same fields the translator reads (+0x6f8..+0x70c, +0x748..+0x758), so the .spv is the same.

Usage:
  python tsuki_shaders_offline.py --maxwell <framework\\tools\\maxwell> --romfs-shader <dir> [--romfs-shader <dir> ...] --main-bin <main.bin>
         --spirv <data\\spirv> [--out <dir>]
Without --out it only counts. With --out it writes into <out>\\romfs and <out>\\main the blocks WITHOUT a .spv in --spirv
(code_<hash>.bin, ctrl_<hash>.bin, 13-column manifest.tsv like containers.py) for spirv_harness --batch.
It writes nothing outside --out (it does not use containers.extract, which requires private\\). Output read by
recomp_tsukihime.ps1 (CountShaders; change both together): one "without .spv: <hash> (<group>, <stage>)" line per block
without a .spv and, last, "SUMMARY total=N romfs=N main=N with_spv=N without_spv=N".
"""
import argparse
import glob
import hashlib
import os
import sys

HEADER = ("id\tstage\tcode_off\tcode_size\tctrl_off\tctrl_size\tprogram_start\tprogram_bytes\tconst_off\t"
          "const_size\tcode_sha1_16\tcode_file\tctrl_file\n")


def u32(d, o):
    return int.from_bytes(d[o:o + 4], "little")


def blocks(maxwell, romfs_shaders, main_bin):
    sys.path.insert(0, maxwell)
    import containers as C        # noqa: E402  (from the framework, read only)
    import exefs_shaders as E     # noqa: E402

    out = {}  # hash -> (origin, id, stage, code, control)
    files = [f for d in romfs_shaders for f in sorted(glob.glob(os.path.join(d, "*"))) if os.path.isfile(f)]
    if not files:
        sys.exit(f"ERROR: there are no files in {', '.join(romfs_shaders)}")
    for f in files:
        d = C.read_file(f)
        c = C.open_blob(d, 0, len(d), os.path.basename(f))
        if not c:
            continue
        for s in C.all_stages(c):
            if s.problems:
                sys.exit(f"ERROR: {s.id}: unexpected layout ({', '.join(s.problems)}). Stopping.")
            code = bytes(d[s.code_off:s.code_off + s.code_size])
            ctrl = bytes(d[s.ctrl_off:s.ctrl_off + s.ctrl_size])
            if len(ctrl) < 0x880 or u32(ctrl, 0x704) != len(code):
                sys.exit(f"ERROR: {s.id}: short control section or different size. Stopping.")
            h = hashlib.sha1(code).hexdigest()[:16]
            out.setdefault(h, ("romfs", s.id, s.kind, code, ctrl[:0x880]))
    n_romfs = len(out)

    d = open(main_bin, "rb").read()
    found, _ = E.find(d)
    n_main = 0
    for p, c in found:
        size = u32(d, c + 0x704)
        code, ctrl = d[p:p + size], d[c:c + 0x880]
        h = hashlib.sha1(code).hexdigest()[:16]
        if h in out:
            continue
        stage = "compute" if u32(d, p) == 0x12345679 else {1: "vertex", 5: "pixel"}[(u32(d, p + 0x30) >> 10) & 0xF]
        out[h] = ("main", f"main/{p:#x}/{stage}", stage, code, ctrl)
        n_main += 1
    return out, n_romfs, n_main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maxwell", required=True)
    ap.add_argument("--romfs-shader", required=True, action="append")
    ap.add_argument("--main-bin", required=True)
    ap.add_argument("--spirv", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()

    all_blocks, n_romfs, n_main = blocks(a.maxwell, a.romfs_shader, a.main_bin)
    with_spv = {h for h in all_blocks if os.path.isfile(os.path.join(a.spirv, h + ".spv"))}
    without_spv = sorted(set(all_blocks) - with_spv)
    if a.out:
        if os.path.exists(a.out) and os.listdir(a.out):
            sys.exit(f"ERROR: {a.out} already exists and is not empty. Nothing touched.")
        for group in ("romfs", "main"):
            hs = [h for h in without_spv if all_blocks[h][0] == group]
            if not hs:
                continue
            g = os.path.join(a.out, group)
            os.makedirs(g)
            with open(os.path.join(g, "manifest.tsv"), "w", newline="\n") as m:
                m.write(HEADER)
                for h in hs:
                    _, sid, stage, code, ctrl = all_blocks[h]
                    open(os.path.join(g, f"code_{h}.bin"), "wb").write(code)
                    open(os.path.join(g, f"ctrl_{h}.bin"), "wb").write(ctrl)
                    m.write(f"{sid}\t{stage}\t0\t{len(code):#x}\t0\t{len(ctrl):#x}\t{u32(ctrl, 0x708):#x}\t"
                            f"{u32(ctrl, 0x6f8):#x}\t{u32(ctrl, 0x700):#x}\t{u32(ctrl, 0x6fc):#x}\t{h}\t"
                            f"code_{h}.bin\tctrl_{h}.bin\n")
    for h in without_spv:
        print(f"without .spv: {h} ({all_blocks[h][0]}, {all_blocks[h][2]})")
    print(f"SUMMARY total={len(all_blocks)} romfs={n_romfs} main={n_main} with_spv={len(with_spv)} without_spv={len(without_spv)}")


if __name__ == "__main__":
    main()