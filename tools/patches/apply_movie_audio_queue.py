"""apply_movie_audio_queue.py - audio decoders accept many more queued samples than video ones (AP_MOVIE_AUDIO_QUEUE).

Why: the prologue (and any movie) could stop for ~5 s right at its start, then play with audio and video out of
step (frames dropped every second). The NX_MOVIE_TRACE state at that moment, seen on three phones (two MediaTek, one
Snapdragon), always the same:
    estado audio started 1, entrada libre 8/8, pendientes 68, salida: ... en el juego 4, libres 0
    estado video started 1, entrada libre 0/8, pendientes 0, ...
    hilo 3: ... AcquireInputBufferIndex(audio) -> TryAgain
The game does not start playing (its AudioOut) until the video gives its first picture. The mp4 interleaves audio
and video in blocks, so the game's feeder thread must push the leading audio block before it reaches the first
video sample. Audio cannot be consumed yet (the game holds all 4 audio outputs until playback starts), so its queue
fills up to pending_limit() (64 samples, ~1.4 s of AAC) and the audio decoder answers TryAgain; the feeder waits for
the audio, the video gets no sample, nothing moves until the game's own ~5 s timeout restarts the audio. Whether the
first video sample slips in before the audio queue is full is a matter of timing, so it failed on some runs and some
phones only (it looked tied to MediaCodec, but a run with the same build could go either way).
The 64 limit is there for video (see the comment on pending_limit()); compressed audio samples are a few hundred
bytes each, so audio can queue far more at no cost.

What it does (games/tsukihime_common/movie_core.cpp, every platform):
  * Audio decoders use their own limit: NX_MOVIE_AUDIO_PENDING (default 1024, about 20 s of AAC; 64..8192).
    Video decoders keep pending_limit() (NX_MOVIE_PENDING, 64).
  * acquire_input_index(), has_free_input() and free_input_count() use the limit of their decoder's kind.
Plus one $Markers row in ../tools/recomp_tsukihime.ps1 (after the row of AP_MOVIE_HW_SOLO).

Idempotent (marker AP_MOVIE_AUDIO_QUEUE), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_MOVIE_AUDIO_QUEUE"
C = "games/tsukihime_common/movie_core.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# 1. per-kind limit, right before the AP_TITLE_SMOOTH block that follows pending_limit()
A1 = "// AP_TITLE_SMOOTH (apply_title_smooth.py): one closed H.264 decoder kept (flushed) for the next start() with the same\n"
N1 = ("// AP_MOVIE_AUDIO_QUEUE: audio decoders queue up to NX_MOVIE_AUDIO_PENDING samples (default 1024, 64..8192). At the\n"
      "// start of a movie the game holds every audio output until the video gives its first picture, and the leading audio\n"
      "// block of the mp4 has to be queued before the feeder reaches the first video sample; with the video limit (64) the\n"
      "// audio filled up first and the movie stood still until the game's ~5 s timeout.\n"
      "size_t pending_limit_for(DecoderKind kind) {\n"
      "    if (kind != DecoderKind::Audio) return pending_limit();\n"
      "    static const size_t n = [] {\n"
      "        const char* e = std::getenv(\"NX_MOVIE_AUDIO_PENDING\");\n"
      "        long v = e && *e ? std::strtol(e, nullptr, 10) : 1024;\n"
      "        if (v < 64 || v > 8192) v = 1024;\n"
      "        if (e && *e) std::fprintf(stderr, \"movie: NX_MOVIE_AUDIO_PENDING = %ld\\n\", v);\n"
      "        return size_t(v);\n"
      "    }();\n"
      "    return n;\n"
      "}\n"
      "\n"
      + A1)

# 2-4. the three checks
A2 = "    if (p_->eos_in || p_->pending.size() >= pending_limit()) return kTryAgain;\n"
N2 = "    if (p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return kTryAgain;  // AP_MOVIE_AUDIO_QUEUE\n"
A3 = "    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit()) return false;\n"
N3 = "    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return false;  // AP_MOVIE_AUDIO_QUEUE\n"
A4 = "    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit()) return 0;\n"
N4 = "    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return 0;  // AP_MOVIE_AUDIO_QUEUE\n"

# 5. $Markers row
A5 = "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_HW_SOLO', 'apply_movie_hw_solo.py', $false),\n"
N5 = (A5 + "    @('both', 'framework\\games\\tsukihime_common\\movie_core.cpp', 'AP_MOVIE_AUDIO_QUEUE', 'apply_movie_audio_queue.py', $false),\n")

EDITS = [(C, A1, N1), (C, A2, N2), (C, A3, N3), (C, A4, N4), (RECO, A5, N5)]

run(MARK, EDITS, __doc__)
