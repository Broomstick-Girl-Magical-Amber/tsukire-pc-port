"""apply_title_live_opus.py - F5 / F6 change the title screen music at once (AP_TITLE_LIVE_OPUS).
Apply apply_title_live.py first (with the new title_bg.cpp that goes with this patch). If you applied
apply_title_live_music.py (the one that waited for the loop), undo it first: python apply_title_live_music.py --remove
(it never had an effect: the game loops the title track by jumping to byte 222388, not to 0).

The game streams the title track through its own handle and decodes it packet by packet with nn::codec (hle_codec.cpp). The
host cannot swap the file under a stream that is playing, but it does decode each packet. So:
  - games/tsukihime_common/hle_codec.cpp, DecodeOpusInterleavedWithHardware: asks tsuki_title::music_packet(in, size).
    If the title screen is up, F5 / F6 chose a track other than the one the game is playing (title_bg.cpp knows both files
    and recognises the game's packet by its size and final range) and music = follow, the packet that is decoded is the next
    one of the chosen track, from its start, looping at its loop point. The game does not notice: it still reads its own
    file and counts its own packets. Any other packet (voices, effects, the story) is decoded exactly as before. Under
    NX_TITLE_LIVE only (Wonder unchanged);
  - a row in $Markers of ../tools/recomp_tsukihime.ps1 for hle_codec.cpp and one for title_bg.cpp.
The loop point of MOON01_BGM59 is known (packet 892, 17.84 s: the jump the game makes). The one of MOON01_BGM02 is not: by
default it loops from the start; title.ini  loop_bgm02 = <seconds>  sets it.

Idempotent (marker AP_TITLE_LIVE_OPUS), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_TITLE_LIVE_OPUS"

INC_ANCHOR = "#include <unordered_map>\n"
INC_NEW = INC_ANCHOR + "#include <vector>\n"

DECL_ANCHOR = "namespace nx {\n\nnamespace {\n"
DECL_NEW = ("#ifdef NX_TITLE_LIVE  // AP_TITLE_LIVE_OPUS (apply_title_live_opus.py): the title music chosen with F5 / F6 (title_bg.cpp)\n"
            "namespace tsuki_title {\n"
            "bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out);\n"
            "}\n#endif\n\n" + DECL_ANCHOR)

DEC_OLD = ("    int max_frames = int(out_size / (2 * size_t(d->channels)));\n"
           "    int n;\n"
           "    {\n"
           "        std::lock_guard lk(d->lock);\n"
           "        n = opus_decode(d->dec, in + 8, int(len), out, max_frames, 0);\n"
           "    }\n")
DEC_NEW = ("    int max_frames = int(out_size / (2 * size_t(d->channels)));\n"
           "    const uint8_t* pkt = in + 8;  // AP_TITLE_LIVE_OPUS: the packet to decode\n"
           "    uint32_t pkt_len = len;\n"
           "#ifdef NX_TITLE_LIVE\n"
           "    std::vector<uint8_t> chosen;\n"
           "    if (tsuki_title::music_packet(in, len, &chosen)) pkt = chosen.data(), pkt_len = uint32_t(chosen.size());\n"
           "#endif\n"
           "    int n;\n"
           "    {\n"
           "        std::lock_guard lk(d->lock);\n"
           "        n = opus_decode(d->dec, pkt, int(pkt_len), out, max_frames, 0);\n"
           "    }\n")

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_codec.cpp', 'AP_TITLE_LIVE_OPUS', 'apply_title_live_opus.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE_OPUS', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("games/tsukihime_common/hle_codec.cpp", INC_ANCHOR, INC_NEW),
    ("games/tsukihime_common/hle_codec.cpp", DECL_ANCHOR, DECL_NEW),
    ("games/tsukihime_common/hle_codec.cpp", DEC_OLD, DEC_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
