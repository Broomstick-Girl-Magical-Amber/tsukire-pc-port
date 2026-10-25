#!/usr/bin/env python3
"""apply_audio_sdk.py - Makes the framework audio HLE work with other SDKs (not only Wonder's 16.2).

hle_audio.cpp had three things of SDK 16.2 hard-coded:
  - the vtable of the renderer sf object (sdk+0xd04910)  -> now looked up by symbol
  - AudioRendererImpl::ExecuteAudioRendererRendering (sdk+0x20cac8) -> now looked up by symbol
  - the execution mode / render device fields in the render system (+0x2d0 / +0x2d4)
    -> now they come from game::kInfo.audio_execution_mode_offset (0 = 0x2d0, Wonder); the device goes 4 bytes after
If a symbol is not exported, Wonder's address is used, so Wonder and Hades do not change.

Requires apply_nvn_format_table.py applied first. Keeps CRLF and is idempotent.
Usage (from <root>\\framework):  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_audio_sdk.py
"""
import sys
from pathlib import Path

GAME_H = Path("runtime/include/nx/game.h")
AUDIO = Path("runtime/src/hle_audio.cpp")

GAME_H_OLD = "    uint32_t nvn_format_count = 0;\n};"
GAME_H_NEW = """    uint32_t nvn_format_count = 0;
    // AudioRenderSystem's execution mode field (hle_audio.cpp); the rendering device is the next word.
    // 0 = the Super Mario Bros. Wonder value (SDK 16.2: 0x2d0). SDK 11.4: 0x2b8.
    uint32_t audio_execution_mode_offset = 0;
};"""

INCLUDE_OLD = "#include <thread>\n"
INCLUDE_NEW = "#include <thread>\n\n#include <nx/game.h>\n"

HELPERS_ANCHOR = "// The AudioRendererImpl behind an AudioRendererHandle"
HELPERS = """// What moves between SDK versions: found by symbol when the sdk exports it, else the SDK 16.2 (Wonder) values above.
uint64_t renderer_vtable() {
    static uint64_t v = [] {
        uint64_t s = find_symbol(
            "_ZTVN2nn2sf6detail38ObjectImplFactoryWithStatefulAllocatorINS0_13InterfaceInfoINS_5audio6detail14IAudio"
            "RendererEE4_tABINS7_5_tO2NINS4_6server24AudioRendererManagerImpl17AudioRendererImplEE4typeES6_E4typeENS0_"
            "24StatefulAllocationPolicyINS0_16ExpHeapAllocatorEEEE6ObjectE");
        if (s) log("audio: renderer vtable by symbol at %s", describe(s).c_str());
        return s ? s : sdk_base() + kRendererVtable;
    }();
    return v;
}
uint64_t execute_rendering() {
    static uint64_t f = [] {
        uint64_t s = find_symbol(
            "_ZN2nn5audio6server24AudioRendererManagerImpl17AudioRendererImpl29ExecuteAudioRendererRenderingEv");
        if (s) log("audio: ExecuteAudioRendererRendering by symbol at %s", describe(s).c_str());
        return s ? s : sdk_base() + kExecuteRendering;
    }();
    return f;
}
uint64_t execution_mode_offset() {
    return game::kInfo.audio_execution_mode_offset ? game::kInfo.audio_execution_mode_offset : 0x2d0;
}

"""

REPLACEMENTS = [
    ("    uint64_t vptr = sdk_base() + kRendererVtable + 0x10;",
     "    uint64_t vptr = renderer_vtable() + 0x10;"),
    ("            call(c, sdk_base() + kExecuteRendering);",
     "            call(c, execute_rendering());"),
    ('    log("audio: execution mode %u, rendering device %u -> 1, 1", ld<uint32_t>(system + 0x2d0), ld<uint32_t>(system + 0x2d4));\n'
     "    st<uint32_t>(system + 0x2d0, 1);\n"
     "    st<uint32_t>(system + 0x2d4, 1);",
     "    uint64_t mode = system + execution_mode_offset();  // rendering device: the next word\n"
     '    log("audio: execution mode %u, rendering device %u -> 1, 1", ld<uint32_t>(mode), ld<uint32_t>(mode + 4));\n'
     "    st<uint32_t>(mode, 1);\n"
     "    st<uint32_t>(mode + 4, 1);"),
]


def load(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main():
    if not GAME_H.exists() or not AUDIO.exists():
        sys.exit("Run this from <root>\\framework")

    text, crlf = load(GAME_H)
    if "audio_execution_mode_offset" in text:
        print("game.h: it already had the change")
    elif GAME_H_OLD in text:
        save(GAME_H, text.replace(GAME_H_OLD, GAME_H_NEW), crlf)
        print("game.h: applied")
    else:
        sys.exit("game.h: the previous change is missing. Run apply_nvn_format_table.py first.")

    text, crlf = load(AUDIO)
    if "execution_mode_offset()" in text:
        print("hle_audio.cpp: it already had the change")
        return
    missing = [old for old, _ in REPLACEMENTS if old not in text]
    if missing or HELPERS_ANCHOR not in text or INCLUDE_OLD not in text:
        sys.exit("hle_audio.cpp: the expected text was not found (another framework commit?).")
    text = text.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    save(AUDIO, text, crlf)
    print("hle_audio.cpp: applied")


if __name__ == "__main__":
    main()