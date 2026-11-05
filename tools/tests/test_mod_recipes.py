#!/usr/bin/env python3
"""Synthetic test of AP_MOD_RECIPES (no game data, made-up keys). Needs apply_romfs_direct.py and apply_mod_recipes.py.

Builds the base dump of tools/tests/test_romfs_direct.py plus a 3 MiB allpac.mrg, and an update file made of: ranges of
the base at their place, ranges of the base moved, and new bytes. With a stand-in for the BKTR of the update:
  - mods_nsp.recipe_plan / write_recipe: the .nxrcp read back (RecipeReader, `mods_nsp.py unrecipe`) equals the file;
    the MD5 of --verify-csv is the one of the whole file; shader/, *allui.*, small files and mostly-new files stay whole;
    a damaged recipe is refused when it is written (nothing kept).
  - with --cxx (g++ or clang++): hle_fs.cpp + romfs_direct.cpp read the recipe of a mods layer through rom: (whole,
    200 random ranges, size, listing under its real name, read_game_file); a later layer's whole file wins over it;
    a recipe made against another dump is refused.

Run from anywhere: python tools/tests/test_mod_recipes.py [--cxx]
"""
import argparse
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import test_romfs_direct as t  # noqa: E402

m = t.m
PID = t.PID
RNG = random.Random(77)
SIZE = 3 << 20


class FakePatch:
    """What mods_nsp.Patched gives for one file: pieces (0 = base section offset, 1 = patch) and the patched bytes."""

    def __init__(self, voff, pieces, data):
        self.voff, self._pieces, self.data = voff, pieces, data

    def pieces(self, off, n):
        pos = self.voff
        for s, p, size in self._pieces:
            a, b = max(pos, off), min(pos + size, off + n)
            if a < b:
                yield s, p + (a - pos), b - a
            pos += size

    def read(self, off, n):
        return self.data[off - self.voff:off - self.voff + n]


def make_update(d, base_off, other_off):
    """The patched allpac.mrg: base at its place, new bytes, a range moved from another base file, base again, new tail."""
    base = d.read_section(d.l6_off + base_off, SIZE)
    other = d.read_section(d.l6_off + other_off, 5000)
    new1, new2 = os.urandom(100), os.urandom(777)
    a = 1 << 20
    data = base[:a] + new1 + base[a + 100:2 << 20] + other + base[(2 << 20) + 5000:SIZE - 777] + new2
    l6 = d.l6_off
    pieces = [(0, l6 + base_off, a), (1, 0x1000, 100), (0, l6 + base_off + a + 100, (2 << 20) - a - 100),
              (0, l6 + other_off, 5000), (0, l6 + base_off + (2 << 20) + 5000, SIZE - 777 - (2 << 20) - 5000), (1, 0x9000, 777)]
    return data, pieces


CXX_MAIN = r"""
int main(int argc, char** argv) {
    std::string want = slurp(argv[1]);
    const char* mode = argv[2];
    expect(call("_ZN2nn2fs8MountRomEPKcPvm", {P("rom"), 0, 0}) == 0, "MountRom");
    if (std::string(mode) == "refused") {
        expect(read_rom("rom:/allpac.mrg", 0, 16) == "<open failed>", "a recipe of another dump is refused");
        std::printf("HLE BAD %d\n", bad);
        return bad ? 1 : 0;
    }
    uint64_t sz = 0;
    expect(read_rom("rom:/allpac.mrg", 0, want.size() + 10, &sz) == want && sz == want.size(), "recipe: whole file and its size");
    std::srand(5);
    int okr = 0;
    for (int i = 0; i < 200; i++) {
        int64_t o = std::rand() % int64_t(want.size());
        uint64_t n = 1 + uint64_t(std::rand()) % 300000;
        if (read_rom("rom:/allpac.mrg", o, n) == want.substr(size_t(o), size_t(n))) okr++;
    }
    expect(okr == 200, "recipe: 200 random ranges");
    uint32_t t = 9;
    expect(entry_type("rom:/allpac.mrg", &t) == 0 && t == 1, "recipe: GetEntryType file");
    int64_t cnt = 0;
    auto r = list_rom("rom:/", 2, &cnt);
    int named = 0, raw = 0;
    for (auto& e : r) { if (e.name == "allpac.mrg") named++; if (e.name.find(".nxrcp") != std::string::npos) raw++; }
    expect(named == 1 && raw == 0 && has(r, "allpac.mrg", 1, (int64_t)want.size()) && cnt == (int64_t)r.size(),
           "recipe: listed once, under its name, with its size");
    expect(nx::read_game_file("allpac.mrg") == want, "recipe: read_game_file");
    std::printf("HLE BAD %d\n", bad);
    return bad ? 1 : 0;
}
"""


def main():
    check = t.Checker()
    want_cxx = "--cxx" in sys.argv
    if "AP_MOD_RECIPES" not in (ROOT / "tools" / "internal" / "mods_nsp.py").read_text(encoding="utf-8"):
        print("apply_mod_recipes.py is not applied")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "port"
        data = root / "data" / "tsuki"
        data.mkdir(parents=True)
        keys = root / "data" / "prod.keys"
        keys.write_text(f"header_key = {t.HEADER_KEY.hex()}\nkey_area_key_application_10 = {t.KAK.hex()}\n")
        files = dict(t.FILES)
        files["allpac.mrg"] = os.urandom(SIZE)
        nca, _ = t.build_nca(t.build_romfs(files, t.EMPTY_DIRS), t.EXEFS, t.KEY, t.CTR, 7)
        (root / "tsukihime.xci").write_bytes(t.build_xci("05e6.nca", nca, 4096))
        rs = ROOT / "tools" / "internal" / "romfs_source.py"
        t.py(rs, "prepare", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}",
             "--source", data / "romfs_source.bin", "--build-romfs", data / "romfs_build", "--exefs", data / "exefs")

        d = m.DumpBase(str(root / "tsukihime.xci"), m.load_keys(str(keys)), PID)
        offs = {r_: o for o, s, r_ in d.index["files"]}
        want, pieces = make_update(d, offs["allpac.mrg"], offs["movie/prologue.mp4"])
        fp = FakePatch(0x123450, pieces, want)
        a = argparse.Namespace(recipe_min_mb=1)

        plan = m.recipe_plan(fp, fp.voff, SIZE, d.index, "allpac.mrg", a)
        check(plan is not None and len(plan[0]) == 6 and plan[1] == SIZE - 877,
              "recipe_plan: 6 ranges, base bytes counted", str(plan and plan[0]))
        layer = root / "mods" / "japan" / "layer1" / "romfs"
        layer.mkdir(parents=True)
        rcp = layer / ("allpac.mrg" + m.RECIPE_EXT)
        md5 = hashlib.md5()
        saved = m.write_recipe(str(rcp), plan, SIZE, PID, d.index, fp.read, fp.voff, d, md5)
        check(saved == SIZE - 877 and rcp.stat().st_size < 10000, f"write_recipe: {rcp.stat().st_size} bytes for a {SIZE} byte file")
        check(md5.hexdigest() == hashlib.md5(want).hexdigest(), "write_recipe: MD5 of the whole file (for --verify-csv)")
        rr = m.RecipeReader(str(rcp), d, d.index)
        check(rr.read(0, SIZE) == want, "RecipeReader: whole file")
        okr = all(rr.read(o, n) == want[o:o + n] for o, n in ((RNG.randrange(SIZE), RNG.randrange(1, 400000)) for _ in range(300)))
        check(okr, "RecipeReader: 300 random ranges")
        t.py(ROOT / "tools" / "internal" / "mods_nsp.py", "unrecipe", "--keys", keys, "--base-dump", root / "tsukihime.xci",
             "--program-id", f"{PID:016X}", "--recipe", rcp, "--out", work / "rebuilt.mrg")
        check((work / "rebuilt.mrg").read_bytes() == want, "mods_nsp.py unrecipe: the whole file")

        check(m.recipe_plan(fp, fp.voff, SIZE, d.index, "shader/x.bnsh", a) is None, "recipe_plan: shader/ stays whole")
        check(m.recipe_plan(fp, fp.voff, SIZE, d.index, "w_allui.mrg", a) is None, "recipe_plan: *allui.* stays whole")
        check(m.recipe_plan(fp, fp.voff, SIZE, d.index, "allpac.mrg", argparse.Namespace(recipe_min_mb=4)) is None,
              "recipe_plan: smaller than --recipe-min-mb stays whole")
        check(m.recipe_plan(fp, fp.voff, SIZE, d.index, "allpac.mrg", argparse.Namespace(recipe_min_mb=0)) is None,
              "recipe_plan: --recipe-min-mb 0 = always whole")
        mostly_new = FakePatch(0, [(0, d.l6_off + offs["allpac.mrg"], 1 << 20), (1, 0, SIZE - (1 << 20))], want)
        check(m.recipe_plan(mostly_new, 0, SIZE, d.index, "allpac.mrg", a) is None, "recipe_plan: mostly new bytes stays whole")
        check(m.recipe_plan(None, 0, SIZE, d.index, "allpac.mrg", a) is None, "recipe_plan: no BKTR (whole RomFS patch) stays whole")
        liar = FakePatch(fp.voff, pieces, want[:1000] + b"X" + want[1001:])  # the patch says one thing, the pieces another
        bad = work / "bad.nxrcp"
        try:
            m.write_recipe(str(bad), plan, SIZE, PID, d.index, liar.read, liar.voff, d, None)
            check(False, "write_recipe: a recipe that does not rebuild the file is refused")
        except SystemExit:
            check(not bad.exists(), "write_recipe: a recipe that does not rebuild the file is refused (nothing kept)")

        if want_cxx:
            cxx = shutil.which("g++") or shutil.which("clang++")
            if not cxx:
                print("skip C++: no g++ / clang++")
            else:
                fw = ROOT / "framework"
                src = t.HLE_HARNESS.split("int main(")[0] + CXX_MAIN
                (work / "h.cpp").write_text(src)
                exe = work / "h"
                defs = ["-DNX_ROMFS_MODS", '-DNX_ROMFS_MODS_EDITION="japan"', "-DNX_TITLE_MUSIC", "-DNX_TITLE_LIVE", "-DNX_ROMFS_DIRECT"]
                r = subprocess.run([cxx, "-std=c++20", "-O1", "-w", *defs, f"-I{fw / 'runtime' / 'include'}", f"-I{fw / 'runtime' / 'src'}",
                                    str(fw / "runtime" / "src" / "hle_fs.cpp"), str(fw / "games" / "tsukihime_common" / "romfs_direct.cpp"),
                                    str(work / "h.cpp"), "-o", str(exe)], capture_output=True, text=True)
                check(r.returncode == 0, f"hle_fs.cpp + romfs_direct.cpp with recipes link ({Path(cxx).name})", r.stderr[-3000:])
                if r.returncode == 0:
                    (work / "want.bin").write_bytes(want)
                    env = dict(os.environ, TEST_DATA_ROOT=str(data))
                    r = subprocess.run([str(exe), str(work / "want.bin"), "read"], capture_output=True, text=True, env=env)
                    check(r.returncode == 0 and "HLE BAD 0" in r.stdout, "hle_fs: a layer's recipe through rom:", r.stdout[-3000:])
                    whole = root / "mods" / "japan" / "layer2" / "romfs"
                    whole.mkdir(parents=True)
                    other = os.urandom(4321)
                    (whole / "allpac.mrg").write_bytes(other)
                    (work / "other.bin").write_bytes(other)
                    r = subprocess.run([str(exe), str(work / "other.bin"), "read"], capture_output=True, text=True, env=env)
                    check(r.returncode == 0 and "HLE BAD 0" in r.stdout, "hle_fs: a later layer's whole file wins over a recipe", r.stdout[-3000:])
                    shutil.rmtree(whole.parent)
                    raw = bytearray(rcp.read_bytes())
                    raw[24] ^= 1  # program ID of the base
                    rcp.write_bytes(bytes(raw))
                    r = subprocess.run([str(exe), str(work / "want.bin"), "refused"], capture_output=True, text=True, env=env)
                    check(r.returncode == 0 and "HLE BAD 0" in r.stdout and "another dump" in r.stdout,
                          "hle_fs: a recipe made against another dump is refused", r.stdout[-3000:])

    print("all passed" if not t.Checker.fails else f"{t.Checker.fails} failed")
    return 1 if t.Checker.fails else 0


if __name__ == "__main__":
    sys.exit(main())
