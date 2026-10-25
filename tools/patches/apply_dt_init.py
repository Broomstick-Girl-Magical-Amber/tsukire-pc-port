"""apply_dt_init.py - runtime: DT_INIT of the modules at another offset (SDK 17.5.4, Tsukihime global).

Symptom (log 17:08): "FATAL: call into the middle of a function: sdk+30 (_init+20)" when entering nnMain.
Cause (confirmed with nso_disasm and runtime/src/main.cpp): main.cpp calls the DT_INIT of each module at the fixed
offset kDtInit = 0x30 (so in SDK 9 and 16: Wonder and the japan edition, _init at sdk+0x30). In SDK 17.5.4 _init is at
+0x10, both in the sdk and in the global main; +0x30 falls in the middle of the function. The body of _init is the same
(adrp x19 / ldr w8, [x19, #..] with the state of __nnmusl_init_dso, then the GOT slots with ldr), so
init_status_addr() and tdata_range() work the same starting at +0x10.

Fix: kDtInit comes from NX_DT_INIT if the game defines it in its game.cmake; otherwise 0x30 as always (Wonder and the
japan edition do not define it: same binary). games/tsukihime_global/game.cmake defines NX_DT_INIT=0x10.

Usage:  python apply_dt_init.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_DT_INIT), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found.
After: cmake --build build\\tsukihime_global (no need to regenerate)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_DT_INIT"

EDITS = [
    ("runtime/src/main.cpp",
     "constexpr uint64_t kDtInit = 0x30;\n",
     "// AP_DT_INIT: a game can move it from its game.cmake (SDK 17.5.4, Tsukihime global: _init at +0x10 in main and\n"
     "// sdk). Without NX_DT_INIT it stays at +0x30.\n"
     "#ifdef NX_DT_INIT\n"
     "constexpr uint64_t kDtInit = NX_DT_INIT;\n"
     "#else\n"
     "constexpr uint64_t kDtInit = 0x30;\n"
     "#endif\n"),
    ("games/tsukihime_global/game.cmake",
     "target_sources(${NX_GAME} PRIVATE \"${NX_TSUKI_COMMON}/tsukihime.rc\")\n",
     "target_sources(${NX_GAME} PRIVATE \"${NX_TSUKI_COMMON}/tsukihime.rc\")\n"
     "\n"
     "# AP_DT_INIT: DT_INIT (_init) de main y sdk en +0x10 en el SDK 17.5.4 (el runtime asume +0x30; apply_dt_init.py)\n"
     "target_compile_definitions(${NX_GAME} PRIVATE NX_DT_INIT=0x10)\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)