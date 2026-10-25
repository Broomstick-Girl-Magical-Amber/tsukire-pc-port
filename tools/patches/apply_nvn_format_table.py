#!/usr/bin/env python3
"""apply_nvn_format_table.py - Applies to the framework the change "per-game NVN format table".

Same change as tsuki_nvn_format_table.patch, but without depending on git apply (which fails if the .patch
ended up with CRLF line endings). Respects the line endings of each file and is idempotent.

Usage (from <root>\\framework):  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_nvn_format_table.py
"""
import sys
from pathlib import Path

GAME_H = Path("runtime/include/nx/game.h")
LAYOUT = Path("runtime/src/nvn/nvn_texture_layout.cpp")

GAME_H_OLD = "    uint64_t system_resource_size = 0;\n};"
GAME_H_NEW = """    uint64_t system_resource_size = 0;
    // NVN driver's format table in sdk (nvn_texture_layout.cpp): its offset and entry count depend on the SDK
    // version. 0 = the Super Mario Bros. Wonder values (SDK 16.2: 0xd34198, 0xd0 entries).
    uint64_t nvn_format_table = 0;
    uint32_t nvn_format_count = 0;
};"""

INCLUDE_OLD = '#include <algorithm>\n\n#include "nvn.h"'
INCLUDE_NEW = '#include <algorithm>\n\n#include <nx/game.h>\n\n#include "nvn.h"'

FN_START = "FormatInfo format_info(uint32_t format) {"
FN_END = "uint32_t div_up("
FN_NEW = """FormatInfo format_info(uint32_t format) {
    struct Table {
        uint64_t base = 0;  // 0: no usable table (the defaults below are used)
        uint32_t count = 0;
    };
    static Table table = [] {
        Table t;
        uint64_t loader = find_symbol("nvnBootstrapLoader");
        const LoadedModule* m = module_at(loader);
        if (!m) return t;
        uint64_t off = game::kInfo.nvn_format_table ? game::kInfo.nvn_format_table : kFormatTable;
        uint32_t count = game::kInfo.nvn_format_count ? game::kInfo.nvn_format_count : kFormatCount;
        // The table must lie inside the module: a wrong offset (another SDK version) must not read unmapped memory.
        if (off + uint64_t(count) * 0x30 > m->size) {
            log("nvn: format table sdk+%#llx (%u entries) is outside sdk (size %#llx): set nvn_format_table in "
                "games/<game>/game.cpp; using 1x1 blocks of 4 bytes for every format",
                (unsigned long long)off, count, (unsigned long long)m->size);
            return t;
        }
        t.base = m->base + off;
        t.count = count;
        return t;
    }();
    FormatInfo f;
    if (!table.base || format >= table.count) return f;
    const uint32_t* e = reinterpret_cast<const uint32_t*>(table.base + uint64_t(format) * 0x30);
    uint32_t bw = e[2] & 0xFF, bh = (e[2] >> 8) & 0xFF, bytes = e[4] & 0xFF;
    if (bw && bh && bytes) f = {bw, bh, bytes};
    return f;
}

"""


def load(path):
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    return raw.replace("\r\n", "\n"), crlf


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main():
    if not GAME_H.exists() or not LAYOUT.exists():
        sys.exit("Run this from <root>\\framework")

    text, crlf = load(GAME_H)
    if "nvn_format_table" in text:
        print("game.h: it already had the change")
    elif GAME_H_OLD in text:
        save(GAME_H, text.replace(GAME_H_OLD, GAME_H_NEW), crlf)
        print("game.h: applied")
    else:
        sys.exit("game.h: the expected text was not found (another framework commit?).")

    text, crlf = load(LAYOUT)
    if "game::kInfo.nvn_format_table" in text:
        print("nvn_texture_layout.cpp: it already had the change")
        return
    if INCLUDE_OLD not in text or FN_START not in text or FN_END not in text:
        sys.exit("nvn_texture_layout.cpp: the expected text was not found.")
    text = text.replace(INCLUDE_OLD, INCLUDE_NEW)
    a, b = text.index(FN_START), text.index(FN_END)
    text = text[:a] + FN_NEW + text[b:]
    save(LAYOUT, text, crlf)
    print("nvn_texture_layout.cpp: applied")


if __name__ == "__main__":
    main()