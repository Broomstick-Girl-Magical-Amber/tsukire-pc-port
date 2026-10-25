"""apply_game_language.py - runtime: default language of each game (Tsukihime: japan ja, global en-US).

Today (runtime/src/language.h) desired_language() takes NX_LANGUAGE, otherwise <data root>/language.txt, and if there
is neither (or it does not recognize it) it answers en-US. Used by svc_am (GetDesiredLanguage) and svc_set (GetLanguageCode).

Fix: that last value comes from NX_DEFAULT_LANGUAGE if the game defines it in its game.cmake; otherwise en-US as
always (Wonder does not define it: same binary). NX_LANGUAGE and language.txt still take priority.
  games/tsukihime/game.cmake         -> NX_DEFAULT_LANGUAGE="ja"
  games/tsukihime_global/game.cmake  -> NX_DEFAULT_LANGUAGE="en-US"
Valid codes: those of the table in language.h (ja, en-US, en-GB, zh-Hans, zh-Hant, es, ...).

Usage:  python apply_game_language.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_LANG_DEFAULT), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found.
Independent of apply_dt_init.py. After: cmake --build in both build\\ (no need to regenerate).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_LANG_DEFAULT"
ANCHOR_CMAKE = "# Titulo e icono de la ventana (apply_game_window.py): kWindowTitle en game.cpp, icono 1 en tsukihime.rc\n"


def block_text(code_text, who):
    return ("# AP_LANG_DEFAULT: idioma si no hay NX_LANGUAGE ni <data root>\\language.txt (apply_game_language.py); " + who + "\n"
            "target_compile_definitions(${NX_GAME} PRIVATE NX_DEFAULT_LANGUAGE=\"" + code_text + "\")\n"
            "\n" + ANCHOR_CMAKE)


EDITS = [
    ("runtime/src/language.h",
     "    if (value.size() == 2) return pack_language_code(value.c_str());\n"
     "    return pack_language_code(\"en-US\");\n",
     "    if (value.size() == 2) return pack_language_code(value.c_str());\n"
     "    // AP_LANG_DEFAULT: a game can set its own default from its game.cmake (NX_DEFAULT_LANGUAGE=\"ja\").\n"
     "#ifdef NX_DEFAULT_LANGUAGE\n"
     "    return pack_language_code(NX_DEFAULT_LANGUAGE);\n"
     "#else\n"
     "    return pack_language_code(\"en-US\");\n"
     "#endif\n"),
    ("games/tsukihime/game.cmake", ANCHOR_CMAKE, block_text("ja", "edicion japonesa")),
    ("games/tsukihime_global/game.cmake", ANCHOR_CMAKE, block_text("en-US", "edicion global (tambien zh-Hans, zh-Hant, ja)")),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)