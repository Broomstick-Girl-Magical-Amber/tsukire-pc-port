#!/usr/bin/env python3
"""apply_origin_default.py - lower-left origin by default for Tsukihime, without an environment variable.

apply_origin_lower_left.py left it behind NX_ORIGIN_LOWER_LEFT=1: in a terminal without the variable (cmd) the
menu comes out mirrored again. This patch adds a per-game default:
  - nvn_draw.cpp and nvn_vk.cpp: if the game is built with NX_DEFAULT_ORIGIN_LOWER_LEFT and the variable is not set, the
    origin is lower-left. NX_ORIGIN_LOWER_LEFT=1 / 0 still wins when it is set.
  - games/tsukihime/game.cmake: target_compile_definitions(... NX_DEFAULT_ORIGIN_LOWER_LEFT).
Wonder does not define the macro: it stays the same.

Requires apply_origin_lower_left.py applied first.
Usage: python apply_origin_default.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent; all or nothing; keeps CRLF. After: cmake -B build\\tsukihime (reconfigure) and build.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
NVN = os.path.join(ROOT, "runtime", "src", "nvn")
CMAKE = os.path.join(ROOT, "games", "tsukihime", "game.cmake")
MARK = "NX_DEFAULT_ORIGIN_LOWER_LEFT"

OLD = ('        const char* e = std::getenv("NX_ORIGIN_LOWER_LEFT");\n'
       "        return e && e[0] == '1';\n")
NEW = ('        const char* e = std::getenv("NX_ORIGIN_LOWER_LEFT");  // 1 / 0; sin ella, el default del juego\n'
       "#ifdef NX_DEFAULT_ORIGIN_LOWER_LEFT  // games/<juego>/game.cmake (apply_origin_default.py)\n"
       "        if (!e) return true;\n"
       "#endif\n"
       "        return e && e[0] == '1';\n")
CMAKE_LINES = ("\n# Origen de ventana NVN abajo-izquierda por defecto (apply_origin_default.py); NX_ORIGIN_LOWER_LEFT=0 lo apaga\n"
               "target_compile_definitions(${NX_GAME} PRIVATE NX_DEFAULT_ORIGIN_LOWER_LEFT)\n")


def load(path):
    raw = open(path, "rb").read().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def save(path, text, crlf):
    with open(path, "wb") as f:
        f.write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main():
    plans = []
    for name in ("nvn_draw.cpp", "nvn_vk.cpp"):
        path = os.path.join(NVN, name)
        text, crlf = load(path)
        if MARK in text:
            print(f"{name}: already applied")
            continue
        if text.count(OLD) != 1:
            sys.exit(f"{name}: cannot find the origin_lower_left() of apply_origin_lower_left.py "
                     "(is it applied?). Nothing was changed.")
        plans.append((path, name, text.replace(OLD, NEW), crlf))
    text, crlf = load(CMAKE) if os.path.exists(CMAKE) else ("", False)
    if MARK in text:
        print("game.cmake: already applied")
    else:
        plans.append((CMAKE, "game.cmake", text.rstrip("\n") + "\n" + CMAKE_LINES, crlf))
    for path, name, text, crlf in plans:
        save(path, text, crlf)
        print(f"{name}: updated")
    if plans:
        print("Done. Reconfigure and build: cmake -B build\\tsukihime ; cmake --build build\\tsukihime")


if __name__ == "__main__":
    main()