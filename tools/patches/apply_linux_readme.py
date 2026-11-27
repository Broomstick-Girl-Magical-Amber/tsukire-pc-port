"""apply_linux_readme.py - readme.md talks about the native Linux build (AP_LINUX_README).

The new page readme_linux.md (a plain file, not touched by this patcher) is the guide of the native build per distribution.
This patcher does two things:
1. readme.md: intro, status, known limitations, the Proton section and the to-do item now mention the native build and link
   to readme_linux.md. A hidden comment next to the intro carries the marker.
2. .gitignore: allows /readme_linux.md. The root ignores everything by default, so without this line the page would stay out
   of git and out of the release zip.

Idempotent (marker AP_LINUX_README in both files), all or nothing, keeps CRLF and BOM, --remove leaves both files byte for
byte as they were.
Apply: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_linux_readme.py
"""
from _patch import run

MARK = "AP_LINUX_README"
README = "../readme.md"
GITIGNORE = "../.gitignore"

EDITS = [
    (GITIGNORE,
     "!/README.md\n",
     "!/README.md\n# AP_LINUX_README: the native Linux guide\n!/readme_linux.md\n"),
    (README,
     "on Linux the same `.exe` is cross-compiled and played with Proton. There is a launcher for Windows to play.",
     "on Linux there is a native build (ELF, Vulkan and SDL3), or the same `.exe` can be cross-compiled and played with Proton. "
     "There are launchers for Windows and for Linux to play.\n\n<!-- AP_LINUX_README -->"),
    (README,
     "On Linux (tested with Proton), the recompilation is still done from the console.",
     "On Linux there is a launcher too (`bash tools/linux/launcher.sh gui`), see [readme_linux.md](readme_linux.md)."),
    (README,
     "- On Linux the build is the same Windows `.exe`, played with Proton. Plain Wine is not supported.",
     "- On Linux, the native build was tested in virtual machines without a GPU (Ubuntu, Fedora and Arch), not yet on a real GPU nor with Nintendo controllers. "
     "The Proton route plays the same Windows `.exe`; plain Wine is not supported."),
    (README,
     "- Native Linux build.",
     "- Test the native Linux build on real GPUs and controllers. The build itself is done: [readme_linux.md](readme_linux.md)."),
    (README,
     "## Rebuild on Linux and play with Proton\n\n",
     "## Rebuild on Linux and play with Proton\n\nThis section is the Proton route. For the native Linux build see [readme_linux.md](readme_linux.md).\n\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
