"""apply_linux_gui.py - the graphical launcher of native Linux in git (AP_LINUX_GUI): .gitignore lets its sources through.

tools/linux/gui/ (CMakeLists.txt, launcher_gui.cpp) is the Dear ImGui + SDL3 front end of tools/linux/launcher.sh; it is
built by tools/linux/build_gui.sh into framework/build/launcher/ (ignored like all of framework/build). The root
.gitignore is an allowlist and tools/linux/ only lets scripts through (.sh .cmake .ps1 .py), so the sources of the folder
need their own lines. Dear ImGui is not in the repository: build_gui.sh downloads it, pinned, into _prepare/.

Touches only ../.gitignore (a block after the line of tools/linux/*.ps1). Idempotent (marker AP_LINUX_GUI), all or
nothing, keeps CRLF and BOM, --remove leaves the file byte for byte as it was. Independent of the other patchers.
New files (copy them before applying): tools/linux/gui/*, tools/linux/build_gui.sh.
Apply: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_linux_gui.py
"""
import os
import sys

from _patch import run

MARK = "AP_LINUX_GUI"
GITIGNORE = "../.gitignore"

EDITS = [
    (GITIGNORE,
     '''!/tools/linux/*.ps1
''',
     '''!/tools/linux/*.ps1
# AP_LINUX_GUI (apply_linux_gui.py): the graphical launcher of native Linux, sources only.
!/tools/linux/gui/
/tools/linux/gui/*
!/tools/linux/gui/CMakeLists.txt
!/tools/linux/gui/*.cpp
'''),
]

if __name__ == "__main__":
    if "--remove" not in sys.argv:
        g = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "linux", "gui", "launcher_gui.cpp")
        if not os.path.isfile(g):
            sys.exit("ERROR: tools/linux/gui/launcher_gui.cpp is missing (copy the folder with this patcher). Nothing touched.")
    run(MARK, EDITS, __doc__)
