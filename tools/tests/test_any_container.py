#!/usr/bin/env python3
"""Synthetic test of AP_ANY_CONTAINER (apply_any_container.py): the .xci and the .nsp of the same game are the same dump.

No game data, made-up keys (the builders of test_romfs_direct.py). The same program NCA is wrapped as an XCI (padded,
update/normal/secure partitions), as an NSP (with another file before the NCA, so the offsets differ), as a trimmed
XCI and as an XCI with the 0x1000-byte key area in front. Checks:
  - romfs_source.py locate: a table made from the XCI finds the NSP, and one made from the NSP finds the XCI (trimmed
    and with the key area too); the dump it was made from is still found as before.
  - not accepted: another NCA of the same size (other first 0x100 bytes), an NSP with the NCA cut short, a file that
    is not a container.
  - romfs_direct.cpp (--cxx, g++ or clang++): with the table of the XCI and only the NSP in the root (and the other way
    round), every file is read whole and at random ranges, equal to the original; the log says it is another container.
  - Home.java isTheDump (when javac is there): the same accept / reject cases, compiled out of the real Home.java.

Run from anywhere: python tools/tests/test_any_container.py [--cxx]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_romfs_direct as t  # noqa: E402  (builders, harness, FILES, keys)

ROOT = t.ROOT
RS = ROOT / "tools" / "internal" / "romfs_source.py"
HOME = ROOT / "android" / "java" / "io" / "github" / "tsukire" / "launcher" / "Home.java"


def java_checker(work):
    """A tiny Java program with the dump check of the real Home.java (Source, readSource, isTheDump and helpers)."""
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("skip Home.java part: no javac/java")
        return None
    src = HOME.read_text(encoding="utf-8")
    a, z = src.find("    static class Source {"), src.find("    static boolean isDumpName(")
    if a < 0 or z < a:
        print("skip Home.java part: Source / isDumpName not found")
        return None
    d = work / "java"
    d.mkdir()
    (d / "DumpCheck.java").write_text(
        "import java.io.File;\nimport java.io.RandomAccessFile;\nimport java.nio.ByteBuffer;\nimport java.nio.ByteOrder;\n"
        "import java.nio.charset.StandardCharsets;\nimport java.util.ArrayList;\nimport java.util.Arrays;\nimport java.util.List;\n"
        "public class DumpCheck {\n" + src[a:z] +
        "    public static void main(String[] a) {\n"
        "        Source s = readSource(new File(a[0]));\n"
        "        for (int i = 1; i < a.length; i++) System.out.println((isTheDump(new File(a[i]), s) ? \"YES \" : \"NO \") + a[i]);\n"
        "    }\n}\n", encoding="utf-8")
    r = subprocess.run([javac, "-d", str(d), str(d / "DumpCheck.java")], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("FAIL: the dump check of Home.java does not compile")
    return lambda table, *files: subprocess.run([java, "-cp", str(d), "DumpCheck", str(table), *map(str, files)],
                                                capture_output=True, text=True).stdout


def main():
    check = t.Checker()
    want_cxx = "--cxx" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "port"
        data = root / "data" / "tsuki"
        data.mkdir(parents=True)
        keys = root / "prod.keys"
        keys.write_text(f"header_key = {t.HEADER_KEY.hex()}\nkey_area_key_application_10 = {t.KAK.hex()}\n")
        nca, _ = t.build_nca(t.build_romfs(t.FILES, t.EMPTY_DIRS), t.EXEFS, t.KEY, t.CTR, 7)
        other, _ = t.build_nca(t.build_romfs(t.FILES, t.EMPTY_DIRS), t.EXEFS, t.KEY, t.CTR, 8)  # other first 0x100
        for rel, b in t.FILES.items():
            p = work / "expect" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)

        xci = t.build_xci("05e6.nca", nca, 1024 * 1024)
        nsp = t.build_pfs0([("0aaa.cnmt.nca", os.urandom(0x1234)), ("05e6.nca", nca), ("05e6.tik", os.urandom(0x2c0))])
        store = work / "store"
        store.mkdir()
        (store / "game.xci").write_bytes(xci)
        (store / "game.nsp").write_bytes(nsp)
        (store / "trimmed.xci").write_bytes(xci[:len(xci) - 1024 * 1024])
        (store / "keyarea.xci").write_bytes(os.urandom(0x1000) + xci)
        (store / "other.nsp").write_bytes(t.build_pfs0([("0aaa.cnmt.nca", os.urandom(0x1234)), ("05e6.nca", other)]))
        (store / "short.nsp").write_bytes(nsp[:len(nsp) - 0x2c0 - 0x400])
        (store / "junk.nsp").write_bytes(os.urandom(len(nsp)))
        check(xci.find(nca[:0x100]) != nsp.find(nca[:0x100]), "the NCA is at different offsets in the XCI and the NSP")

        def put(name, as_name):
            for p in root.iterdir():
                if p.is_file() and p.suffix.lower() in (".xci", ".nsp"):
                    p.unlink()
            shutil.copyfile(store / name, root / as_name)

        def prepare(name):
            put(name, name)
            for p in ("romfs_source.bin",):
                if (data / p).exists():
                    (data / p).unlink()
            shutil.rmtree(data / "romfs_build", ignore_errors=True)
            shutil.rmtree(data / "exefs", ignore_errors=True)
            r = t.py(RS, "prepare", "--keys", keys, "--input", root / name, "--program-id", f"{t.PID:016X}",
                     "--source", data / "romfs_source.bin", "--build-romfs", data / "romfs_build", "--exefs", data / "exefs")
            check("PREPARE " in r.stdout, f"prepare from {name}", r.stdout)

        def locate():
            return t.py(RS, "locate", "--source", data / "romfs_source.bin", "--root", root, check=False)

        jcheck = java_checker(work)

        # ---- table made from the XCI
        prepare("game.xci")
        r = locate()
        check(r.returncode == 0 and r.stdout.strip().endswith("game.xci") and "another container" not in r.stdout,
              "locate (table of the XCI): the XCI, as before", r.stdout)
        put("game.nsp", "tsukihime.nsp")
        r = locate()
        check(r.returncode == 0 and r.stdout.strip().endswith("tsukihime.nsp") and "another container" in r.stdout,
              "locate (table of the XCI): the NSP of the same game", r.stdout + r.stderr)
        for bad in ("other.nsp", "short.nsp", "junk.nsp"):
            put(bad, bad)
            r = locate()
            check(r.returncode == 4 and "NOTFOUND" in r.stdout, f"locate (table of the XCI): {bad} is not accepted", r.stdout)
        if jcheck:
            out = jcheck(data / "romfs_source.bin", *(store / n for n in
                         ("game.xci", "game.nsp", "trimmed.xci", "keyarea.xci", "other.nsp", "short.nsp", "junk.nsp")))
            want = {"game.xci": "YES", "game.nsp": "YES", "trimmed.xci": "YES", "keyarea.xci": "YES",
                    "other.nsp": "NO", "short.nsp": "NO", "junk.nsp": "NO"}
            got = {Path(l.split(" ", 1)[1]).name: l.split(" ", 1)[0] for l in out.splitlines() if " " in l}
            check(got == want, "Home.java isTheDump (table of the XCI): XCI, NSP, trimmed, key area yes; other/short/junk no",
                  out)

        exe = None
        if want_cxx:
            put("game.nsp", "renamed.nsp")
            res = t.cxx_test(work, data, check)  # every file through romfs_direct.cpp, with only the NSP in the root
            if res:
                exe, lst = res
                env = dict(os.environ, TEST_DATA_ROOT=str(data))
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env)
                check(r.returncode == 0 and "another container of the same game" in r.stdout and "renamed.nsp" in r.stdout,
                      "C++ (table of the XCI, only the NSP): read from the NSP, logged as another container", r.stdout[-1500:])
                put("other.nsp", "other.nsp")
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env)
                check(r.returncode == 7, "C++: another NCA of the same size is not the dump (exit code 7)", r.stdout[-800:])
                env2 = dict(env, NX_ROMFS_DUMP=str(store / "keyarea.xci"))
                r = subprocess.run([str(exe), str(lst), "4"], capture_output=True, text=True, env=env2)
                check(r.returncode == 0 and f"CHECKED {len(t.FILES)} BAD 0" in r.stdout,
                      "C++: NX_ROMFS_DUMP = an XCI with the key area in front", r.stdout[-1500:])

        # ---- table made from the NSP
        prepare("game.nsp")
        for name in ("game.xci", "trimmed.xci", "keyarea.xci"):
            put(name, "tsukihime.xci")
            r = locate()
            check(r.returncode == 0 and r.stdout.strip().endswith("tsukihime.xci") and "another container" in r.stdout,
                  f"locate (table of the NSP): {name}", r.stdout + r.stderr)
            if exe:
                env = dict(os.environ, TEST_DATA_ROOT=str(data))
                r = subprocess.run([str(exe), str(lst), "5"], capture_output=True, text=True, env=env)
                check(r.returncode == 0 and f"CHECKED {len(t.FILES)} BAD 0" in r.stdout,
                      f"C++ (table of the NSP, only {name}): every file whole and at 200 random ranges", r.stdout[-1500:])
        put("game.nsp", "game.nsp")
        r = locate()
        check(r.returncode == 0 and r.stdout.strip().endswith("game.nsp") and "another container" not in r.stdout,
              "locate (table of the NSP): the NSP, as before", r.stdout)
        if jcheck:
            out = jcheck(data / "romfs_source.bin", store / "game.xci", store / "trimmed.xci", store / "other.nsp")
            check(out.split() == ["YES", str(store / "game.xci"), "YES", str(store / "trimmed.xci"), "NO", str(store / "other.nsp")],
                  "Home.java isTheDump (table of the NSP): the XCIs yes, another NCA no", out)

    print("all passed" if not t.Checker.fails else f"{t.Checker.fails} failed")
    return 1 if t.Checker.fails else 0


if __name__ == "__main__":
    sys.exit(main())
