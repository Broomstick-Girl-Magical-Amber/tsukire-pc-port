"""apply_ffmpeg_prebuilt_fix.py - completes AP_FFMPEG_PREBUILT when only install_requirements.ps1 has it.

Found in the repo snapshot of 2026-11-02: tools/install_requirements.ps1 has AP_FFMPEG_PREBUILT (step 5 no longer asks
vcpkg for ffmpeg; it downloads the BtbN build to _prepare\\ffmpeg), but games/tsukihime_common/tsukihime_common.cmake
and tools/recomp_tsukihime.ps1 do not (find_package(FFMPEG) of vcpkg and no -DNX_FFMPEG_ROOT). On a new PC step 9 of
recomp then fails with "CMake Error ... tsukihime_common.cmake:24 (find_package) ... FFMPEG" (the same failure as in
the third VM on 2026-10-31). apply_ffmpeg_prebuilt.py refuses that mixed state (all or nothing), so this applies ITS
edits for the two files that lack them (the same text, imported from it: nothing is copied by hand).

After this, apply_ffmpeg_prebuilt.py sees the marker in its three files ("Already applied"), and
apply_ffmpeg_prebuilt.py --remove removes it from all three as usual. This file's --remove undoes only these two.

Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_ffmpeg_prebuilt_fix.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_ffmpeg_prebuilt as base  # noqa: E402
from _patch import run  # noqa: E402

EDITS = [e for e in base.EDITS if e[0] != base.INSTALL]

if __name__ == "__main__":
    run(base.MARK, EDITS, __doc__)
