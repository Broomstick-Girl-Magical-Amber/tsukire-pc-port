"""apply_linux_audio_opt.py - native Linux: no audio device is not fatal (AP_LINUX_AUDIO_OPT).

platform_linux.cpp started SDL with video, gamepad and audio in one SDL_Init: on a PC or VM without an audio device
(no sound card, PipeWire/PulseAudio not running) it failed ("SDL_Init: No available audio device") and the game did not
start. Now video and gamepad are required and audio is optional: without it audio_open fails, the game says so in the
log and runs silent, pacing itself as with NX_MUTE=1. Only platform_linux.cpp (native Linux) changes.
"""
from _patch import run

MARK = "AP_LINUX_AUDIO_OPT"
PLAT = "runtime/src/platform_linux.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (PLAT,
     '''    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD | SDL_INIT_AUDIO)) {
        std::fprintf(stderr, "SDL_Init: %s\\n", SDL_GetError());
        return 1;
    }
''',
     '''    // AP_LINUX_AUDIO_OPT (apply_linux_audio_opt.py): audio is optional; without a device the game runs silent
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        std::fprintf(stderr, "SDL_Init: %s\\n", SDL_GetError());
        return 1;
    }
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO))
        std::fprintf(stderr, "SDL audio: %s (the game runs without sound)\\n", SDL_GetError());
'''),
    (RECO,
     '''    @('global', 'tools\\internal\\regen_tsuki_global.ps1', 'AP_RELR', 'apply_relr.py', $true),
''',
     '''    @('both', 'framework\\runtime\\src\\platform_linux.cpp', 'AP_LINUX_AUDIO_OPT', 'apply_linux_audio_opt.py', $false),
    @('global', 'tools\\internal\\regen_tsuki_global.ps1', 'AP_RELR', 'apply_relr.py', $true),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
