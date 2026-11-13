"""apply_title_music_diag.py - TEMPORARY: where the title music loops, the game's and the port's (AP_TITLE_MUSIC_DIAG).

Why: after F5 / F6 (or a click on the logo) back to video 4, the port plays MOON01_BGM59.opus from its start and, at its
last packet, jumps to packet 892. About 1:40 later the music "does not finish and starts again near the beginning", and
the game's own loop of the same track does not sound like that. This shows where each one jumps.

What it adds to games/tsukihime_common/title_bg.cpp (nothing else changes; not in $Markers, remove it when done):
  - when the title tracks are loaded: file size, packet count, where the last packet ends, the packet that starts at
    byte 222388, and the first 0x28 bytes of each file (its header, in hex);
  - for every title packet the game decodes: its index in the game's own track; when that index goes back (the game's
    loop) nx.log gets "music-diag: game loop ... packet A -> B";
  - when the port's substitute track wraps: "music-diag: port loop ... packet A -> B";
  - every 500 packets (10 s) while substituting: the game's index and the port's index.
Usage (in <root>\\framework):
  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_title_music_diag.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_TITLE_MUSIC_DIAG"
T = "games/tsukihime_common/title_bg.cpp"

INC_OLD = "#include <unordered_set>\n"
INC_NEW = INC_OLD + "#include <cstdio>  // AP_TITLE_MUSIC_DIAG\n#include <unordered_map>  // AP_TITLE_MUSIC_DIAG\n"

LOAD_OLD = "    return t->offs.size() > 100;\n}\n"
LOAD_NEW = r'''    {  // AP_TITLE_MUSIC_DIAG
        size_t at222388 = SIZE_MAX;
        for (size_t i = 0; i < t->offs.size(); i++)
            if (t->offs[i] == 222388u) at222388 = i;
        const size_t last = t->offs.empty() ? 0 : t->offs.back();
        const size_t last_end = t->offs.empty() ? 0 : last + 8 + be(last);
        char hex[0x28 * 3 + 1] = {};
        for (size_t i = 0; i < 0x28 && i < d.size(); i++) std::snprintf(hex + i * 3, 4, "%02x ", uint8_t(d[i]));
        nx::log("music-diag: %s: %zu bytes, %zu packets, last packet at %zu ends at %zu, packet at byte 222388: %lld",
                name, d.size(), t->offs.size(), last, last_end, at222388 == SIZE_MAX ? -1LL : (long long)at222388);
        nx::log("music-diag: %s header: %s", name, hex);
    }
''' + LOAD_OLD

GUEST_OLD = "    const int guest = in0 && in1 ? -1 : in1 ? 1 : 0;\n"
GUEST_NEW = GUEST_OLD + r'''    {  // AP_TITLE_MUSIC_DIAG: the game's position in its own track
        static std::unordered_map<uint64_t, size_t> index[2];
        static long long last_game = -1;
        static int last_track = -1;
        static uint64_t n_packets = 0;
        for (int k = 0; k < 2; k++)
            if (index[k].empty())
                for (size_t i = 0; i < st.tracks[k].offs.size(); i++) {
                    const uint32_t o = st.tracks[k].offs[i];
                    const std::string& dd = st.tracks[k].data;
                    const uint32_t sz2 = (uint32_t(uint8_t(dd[o])) << 24) | (uint32_t(uint8_t(dd[o + 1])) << 16) |
                                         (uint32_t(uint8_t(dd[o + 2])) << 8) | uint8_t(dd[o + 3]);
                    index[k].emplace(packet_hash(reinterpret_cast<const uint8_t*>(dd.data()) + o, 8 + size_t(sz2)), i);
                }
        if (guest >= 0) {
            auto it = index[guest].find(key);
            if (it != index[guest].end()) {
                const long long gi = (long long)it->second;
                if (guest == last_track && last_game >= 0 && gi < last_game)
                    nx::log("music-diag: game loop in %s: packet %lld -> %lld (port: %s, packet %zu)",
                            guest ? kMusicB : kMusicA, last_game, gi, st.sub_active ? "substituting" : "off", st.sub_idx);
                last_game = gi, last_track = guest;
            }
        }
        if (st.sub_active && ++n_packets % 500 == 0)
            nx::log("music-diag: game %s packet %lld, port %s packet %zu", last_track == 1 ? kMusicB : kMusicA,
                    last_game, st.sub_target ? kMusicB : kMusicA, st.sub_idx);
    }
'''

WRAP_OLD = "    if (++st.sub_idx >= t.offs.size()) st.sub_idx = std::min(loop_index(target, c), t.offs.size() - 1);\n"
WRAP_NEW = (
    "    if (st.sub_idx + 1 >= t.offs.size())  // AP_TITLE_MUSIC_DIAG\n"
    "        nx::log(\"music-diag: port loop in %s: packet %zu -> %zu\", target ? kMusicB : kMusicA, st.sub_idx,\n"
    "                std::min(loop_index(target, c), t.offs.size() - 1));\n"
    + WRAP_OLD)

EDITS = [
    (T, INC_OLD, INC_NEW),
    (T, LOAD_OLD, LOAD_NEW),
    (T, GUEST_OLD, GUEST_NEW),
    (T, WRAP_OLD, WRAP_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)