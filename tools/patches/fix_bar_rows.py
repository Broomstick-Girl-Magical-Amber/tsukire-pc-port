"""fix_bar_rows.py - one-off: puts back the two $Markers rows of apply_bar_images.py version 2 in tools/recomp_tsukihime.ps1.

Use it when recomp_tsukihime.ps1 was replaced while version 2 was still applied to nvn_present.cpp and hle_carga.cpp
(apply_bar_images.py then says "Mixed state"). Run from the root of the port:
    python tools/patches/fix_bar_rows.py
then: python tools/patches/apply_bar_images_v2.py --remove, and apply the version 3 as usual.
"""
import os
import sys

root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
path = os.path.join(root, "tools", "recomp_tsukihime.ps1")
anchor = "    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),"
rows = [
    "    @('both', 'framework\\runtime\\src\\nvn\\nvn_present.cpp', 'AP_BAR_IMAGES', 'apply_bar_images.py', $false),",
    "    @('both', 'framework\\games\\tsukihime_common\\hle_carga.cpp', 'AP_BAR_IMAGES', 'apply_bar_images.py', $false),",
]
with open(path, "rb") as f:
    raw = f.read()
text = raw.decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"
if "AP_BAR_IMAGES" in text:
    sys.exit("recomp_tsukihime.ps1 already has AP_BAR_IMAGES rows: nothing to do.")
if text.count(anchor + nl) != 1:
    sys.exit("ERROR: the nvn_vk.cpp / AP_GPU_HANG row was not found exactly once. Nothing touched.")
text = text.replace(anchor + nl, anchor + nl + nl.join(rows) + nl)
with open(path, "wb") as f:
    f.write(text.encode("utf-8"))
print("rows added to tools/recomp_tsukihime.ps1")
