r"""apply_linux_vulkan.py - native Linux: asks to install the Vulkan driver when there is none (AP_LINUX_VULKAN).

On a fresh Arch (virtual machine) the install ended with the warning that no GPU is usable: Mesa does not bring its CPU
Vulkan driver (lavapipe) by default, and nothing says what to install. install_requirements_linux.sh now looks for the
Vulkan loader and a driver before it starts (tools/linux/vulkan_check.sh: vulkan_check); if they are missing it shows the
exact command for the distribution (pacman / apt / dnf; the driver of the graphics card found: NVIDIA, AMD, Intel, or
the CPU driver in a VM), and asks [y/N] before running it with sudo. Without a terminal it only shows the command.
Building does not need Vulkan, so answering no (or a failing command) does not stop the install.

tools/linux/vulkan_check.sh is a new file of the repo (copy it with this patcher). Windows and Android do not change.
"""
import os
import sys

from _patch import run

MARK = "AP_LINUX_VULKAN"
INST = "../tools/linux/install_requirements_linux.sh"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (INST,
     '''check=0; for a in "$@"; do case "${a,,}" in -check) check=1 ;; esac; done
''',
     '''check=0; for a in "$@"; do case "${a,,}" in -check) check=1 ;; esac; done
# AP_LINUX_VULKAN (apply_linux_vulkan.py): no Vulkan loader or driver -> shows the command and asks before running it
# (tools/linux/vulkan_check.sh).
. "$HERE/vulkan_check.sh"
vulkan_check "$@"
'''),
    (RECO,
     '''    @('both', 'framework\\CMakeLists.txt', 'AP_LINUX_PREFLIGHT', 'apply_linux_preflight.py', $false),
''',
     '''    @('both', 'framework\\CMakeLists.txt', 'AP_LINUX_PREFLIGHT', 'apply_linux_preflight.py', $false),
    @('both', 'tools\\linux\\install_requirements_linux.sh', 'AP_LINUX_VULKAN', 'apply_linux_vulkan.py', $false),
'''),
]

if __name__ == "__main__":
    if "--remove" not in sys.argv:
        vc = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "linux", "vulkan_check.sh")
        if not os.path.isfile(vc):
            sys.exit("ERROR: tools/linux/vulkan_check.sh is missing (copy it with this patcher). Nothing touched.")
    run(MARK, EDITS, __doc__)
