"""xwin_vfs.py - Upper/lower case of the Windows headers on Linux (the xwin SDK lives on a case-sensitive disk).

Windows does not distinguish case in paths and third-party code writes the #includes any way ("Winver.h",
"WinSock2.h"...). xwin only fixes the cases that appear inside the SDK itself. This script generates, next to the xwin
folder (it does not modify it):

  <xwin>-vfs.yaml   clang overlay (-vfsoverlay) without case distinction with all the headers of the CRT and the SDK.
                    Used by clang-cl (C/C++) via VCPKG_C_FLAGS / CMAKE_C_FLAGS_INIT (clangcl-xwin.cmake).
  <xwin>-rc/        links with the name variants the .rc files use (Winver.h, WinVer.h, ...). The .rc files are
                    preprocessed with clang-cl but llvm-rc gets the same flags and does not know -vfsoverlay: hence links + /I.

Usage: python3 xwin_vfs.py <xwin splat folder>
Idempotent: rewrites both (they are derived from xwin) and touches nothing else.
"""
import json
import os
import sys

# Variants used by the known .rc files (libjpeg-turbo: "Winver.h"). Real name (lowercase in the SDK) -> variants.
RC_VARIANTS = {
    "winver.h": ["Winver.h", "WinVer.h", "WINVER.H"],
    "winres.h": ["Winres.h", "WinRes.h"],
    "windows.h": ["Windows.h", "WINDOWS.H"],
    "winresrc.h": ["WinResRc.h", "Winresrc.h"],
    "verrsrc.h": ["VerRsrc.h"],
}


def tree(d):
    """Overlay entries for folder d (recursive): files and subfolders."""
    out = []
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isdir(p):
            out.append({"name": n, "type": "directory", "contents": tree(p)})
        elif os.path.isfile(p):
            out.append({"name": n, "type": "file", "external-contents": os.path.realpath(p)})
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    xwin = os.path.abspath(sys.argv[1].rstrip("/"))
    msvc = os.path.join(xwin, "VC", "Tools", "MSVC")
    kits = os.path.join(xwin, "Windows Kits", "10", "Include")
    if not os.path.isdir(msvc) or not os.path.isdir(kits):
        sys.exit(f"ERROR: {xwin} is not an output of xwin splat --use-winsysroot-style (VC/Tools/MSVC or Windows Kits/10/Include is missing)")

    include_dirs = [os.path.join(msvc, v, "include") for v in sorted(os.listdir(msvc))]
    for v in sorted(os.listdir(kits)):
        include_dirs += [os.path.join(kits, v, s) for s in sorted(os.listdir(os.path.join(kits, v)))]
    include_dirs = [r for r in include_dirs if os.path.isdir(r)]

    roots, n = [], 0
    for r in include_dirs:
        c = tree(r)
        roots.append({"name": r, "type": "directory", "contents": c})
        n += sum(1 for _ in os.walk(r) for _ in _[2])
    ov = {"version": 0, "case-sensitive": "false", "roots": roots}
    yaml = xwin + "-vfs.yaml"
    with open(yaml + ".tmp", "w") as f:
        json.dump(ov, f)  # JSON is valid YAML for clang
    os.replace(yaml + ".tmp", yaml)

    rc = xwin + "-rc"
    os.makedirs(rc, exist_ok=True)
    done = 0
    for real, variants in RC_VARIANTS.items():
        target = next((os.path.join(r, real) for r in include_dirs if os.path.isfile(os.path.join(r, real))), None)
        if not target:
            continue
        for v in variants:
            if os.path.isfile(os.path.join(os.path.dirname(target), v)):
                continue  # the SDK already has it with that name
            link = os.path.join(rc, v)
            if os.path.islink(link) or os.path.exists(link):
                if os.path.realpath(link) == os.path.realpath(target):
                    done += 1
                    continue
                sys.exit(f"ERROR: {link} exists and does not point to {target}; set it aside by hand")
            os.symlink(target, link)
            done += 1
    print(f"overlay {yaml}: {len(include_dirs)} folders, {n} files; {rc}: {done} links")


if __name__ == "__main__":
    main()
