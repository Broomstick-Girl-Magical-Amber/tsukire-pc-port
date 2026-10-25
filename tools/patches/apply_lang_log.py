"""apply_lang_log.py - "am: desired language = ja" once per value, not on every call.

Tsukihime calls GetDesiredLanguage all the time: that line was 98 % of the log (427k of 433k lines, 22 MB).
Change (framework, runtime/src/svc_am.cpp): it is logged the first time and every time the value changes.
The answer to the game does not change (nor for Wonder); there are just fewer lines in the log.

Usage:  python apply_lang_log.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_LANG_LOG), all or nothing, keeps CRLF, refuses if an anchor is not found.
"""
from _patch import run

MARK = "AP_LANG_LOG"

EDITS = [
    ("runtime/src/svc_am.cpp",
     "#include <cstdlib>\n"
     "#include <deque>\n",
     "#include <atomic>  // AP_LANG_LOG\n"
     "#include <cstdlib>\n"
     "#include <deque>\n"),

    ("runtime/src/svc_am.cpp",
     "            log(\"am: desired language = %s\", tag);\n",
     "            static std::atomic<uint64_t> logged{~0ull};  // AP_LANG_LOG: once per value (some games ask all the time)\n"
     "            if (logged.exchange(code) != code) log(\"am: desired language = %s\", tag);\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
